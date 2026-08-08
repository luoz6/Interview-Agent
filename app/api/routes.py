import logging
import os
from ipaddress import ip_address
from collections.abc import Iterator
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4, uuid5

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.job_tags import extract_job_tags
from app.services.agent_runtime import correlation_id_from_plan
from app.services.prep import (
    PlanGenerationValidationError,
    prepare_interview,
    prepared_plan_revision,
    public_interview_plan_payload,
    public_interview_plan_v2_payload,
    validate_launchable_interview_plan,
)
from app.services.interview_plan_editor import (
    InterviewPlanEditor,
    PlanEditRequest,
    PlanOperation,
    PlanOperationValidationError,
)
from app.services.interview_plan_budget import assess_interview_plan_budget
from app.services.interview_plan_revision import (
    canonical_sha256,
    default_plan_configuration,
    PlanConfigurationSnapshot,
    v2_plan_to_legacy,
)
from app.services.interview_plan_regenerator import (
    PlanRegenerationFailed,
    ProviderPlanRegenerator,
)
from app.services.interview_plan_revision_store import (
    PlanRevisionConflict,
    PlanRevisionNotFound,
    PlanSourceUnavailable,
)
from app.services.session_plan_binding import session_plan_binding_from_revision
from app.services.session_plan_binding import session_plan_binding_from_state
from app.services.config import (
    get_report_artifact_read_mode,
    get_report_runtime_profile,
    get_interview_langgraph_rollout_percent,
    get_runtime_event_backend,
    get_runtime_store,
)
from app.services.interview_rounds import round_closed_event_from_transition
from app.services.report_enqueue import enqueue_report_if_needed
from app.services.report_pdf import build_report_pdf
from app.services.report_reliability import build_report_reliability
from app.services.runtime_events import (
    InterviewStreamChunkEvent,
    InterviewStreamDoneEvent,
    InterviewStreamErrorEvent,
)
from app.services.runtime import (
    get_agent_execution_runner,
    get_draft_store,
    get_plan_revision_store,
    get_event_publisher,
    get_report_job_store,
    get_report_artifact_store,
    get_runtime_control_store,
    get_session_store,
    get_interview_workflow_service,
    get_interview_launch_repository,
    get_prep_plan_store,
    get_interview_launch_coordinator,
    get_session_deletion_service,
    get_session_deletion_worker,
    get_question_memory_index_store,
    get_memory_metric_store,
    get_principal_identity_resolver,
    get_principal_memory_control_store,
    get_principal_memory_consent_store,
    get_principal_memory_deletion_tombstone_store,
    get_principal_memory_export_store,
    get_principal_memory_fact_store,
    get_principal_memory_safe_ref_store,
)
from app.services.prep_plans import PrepPlanError
from app.services.prep_question_regeneration import PrepQuestionRegenerator
from app.services.practice_plans import PracticePlanError, PracticePlanService
from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.report_artifact_store import ReportArtifactNotFound, ReportArtifactConflict
from app.services.report_view import compose_report_view
from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.session_errors import SessionVersionConflict
from app.services.session import InterviewSessionStore
from app.graphs.interview_state import is_durable_interview_version
from app.services.memory_config import (
    load_effective_memory_config,
    memory_readiness_payload,
)


router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

_SESSION_START_REQUEST_NAMESPACE = UUID("d27df012-60f1-4df7-b8a0-c44f5209b06b")
_SESSION_START_REQUEST_CONFLICT = "session_start_request_conflict"


def _raise_if_deleting(state: dict) -> None:
    if state.get("deletion_status") == "deleting":
        raise HTTPException(
            status_code=409,
            detail={"code": "session_deleting", "status": "deleting"},
        )


def _require_trusted_local_deletion() -> None:
    if not load_effective_memory_config().privacy.trusted_local_deletion_enabled:
        raise HTTPException(status_code=404, detail="not found")


def _require_trusted_local_metrics() -> None:
    if not load_effective_memory_config().privacy.trusted_local_metrics_enabled:
        raise HTTPException(status_code=404, detail="not found")


def _require_trusted_local_principal_memory(request: Request):
    config = load_effective_memory_config()
    if not (
        config.long_term.local_principal_enabled
        and config.long_term.trusted_local_api_enabled
    ):
        raise HTTPException(status_code=404, detail="not found")
    client = request.client
    try:
        is_loopback = bool(
            client
            and ip_address(client.host.split("%", 1)[0]).is_loopback
        )
    except ValueError:
        is_loopback = False
    if not is_loopback:
        # Forwarded headers are intentionally ignored. Proxy deployments must
        # keep this local-only API unavailable until authenticated proxy trust
        # is explicitly implemented.
        raise HTTPException(status_code=404, detail="not found")
    identity = get_principal_identity_resolver().resolve()
    if identity is None or identity.assurance != "trusted_local":
        raise HTTPException(status_code=404, detail="not found")
    return identity


def _require_local_memory_mutation(request: Request) -> None:
    if request.headers.get("x-local-memory-action") != "1":
        raise HTTPException(status_code=403, detail="local action header required")
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlparse

        if urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(status_code=403, detail="local origin required")


def _deletion_job_payload(job) -> dict:
    return {
        "deletion_job_id": job.job_id,
        "status": job.status,
        "attempt_count": job.attempt_count,
        "error_code": job.error_code,
        "safe_counts": dict(job.safe_counts),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at else None
        ),
    }


def get_report_job_queue():
    try:
        return get_report_job_store()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="report queue is unavailable",
        ) from exc


def get_plan_regenerator() -> ProviderPlanRegenerator:
    return ProviderPlanRegenerator(
        lambda job_description, resume_text, configuration: prepare_interview(
            job_description,
            resume_text,
            execution_runner=get_agent_execution_runner(),
            configuration=configuration,
            allow_fallback=False,
        )
    )


class PrepRequest(BaseModel):
    model_config = {"extra": "forbid"}

    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    draft_id: str | None = None
    configuration: PlanConfigurationSnapshot | None = None

    @field_validator("job_description", "resume_text")
    @classmethod
    def reject_blank_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class PrepPlanPatchRequest(BaseModel):
    expected_version: int = Field(ge=1)
    operations: list[dict] = Field(min_length=1, max_length=20)


class PrepQuestionRegenerateRequest(BaseModel):
    expected_version: int = Field(ge=1)


class PracticePlanRequest(BaseModel):
    focus_dimension: str
    session_question_ids: list[str]
    mode: Literal["targeted"] = "targeted"

    @field_validator("session_question_ids")
    @classmethod
    def validate_session_question_ids(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value]


def get_prep_question_regenerator() -> PrepQuestionRegenerator:
    return PrepQuestionRegenerator()


class StartInterviewRequest(BaseModel):
    model_config = {"extra": "forbid"}

    plan_id: str | None = None
    expected_plan_version: int | None = Field(default=None, ge=1)
    command_id: str | None = None
    # Temporary compatibility window for pre-V15 clients. New clients must
    # send the authoritative plan tuple above.
    job_description: str | None = None
    resume_text: str | None = None

    plan_revision_id: str | None = Field(default=None, min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)
    plan_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_id: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_launch_contract(self):
        revision_values = (
            self.plan_revision_id,
            self.expected_revision,
            self.plan_sha256,
            self.request_id,
        )
        if any(value is not None for value in revision_values):
            if any(value is None for value in revision_values):
                raise ValueError("plan revision launch tuple is incomplete")
            if self.plan_id is not None:
                raise ValueError("plan_id and plan_revision_id are mutually exclusive")
        return self


def get_legacy_launch_session_store(
    request: Request,
    payload: StartInterviewRequest,
):
    if payload.plan_id is not None:
        return None
    override = request.app.dependency_overrides.get(get_session_store)
    return override() if override is not None else get_session_store()


def get_request_interview_launch_coordinator(
    request: Request,
    payload: StartInterviewRequest,
):
    """Honor request-app store overrides without weakening launch ownership.

    Production requests reuse the runtime singleton. Test/support applications
    may override individual FastAPI store dependencies; in that case the
    coordinator must use those exact store instances so the session created by
    launch is visible to subsequent session endpoints.
    """
    if payload.plan_id is None:
        return None
    coordinator = get_interview_launch_coordinator()
    session_override = request.app.dependency_overrides.get(get_session_store)
    plan_override = request.app.dependency_overrides.get(get_prep_plan_store)
    if session_override is None and plan_override is None:
        return coordinator
    return InterviewLaunchCoordinator(
        prep_plan_store=(
            plan_override()
            if plan_override is not None
            else coordinator.prep_plan_store
        ),
        session_store=(
            session_override()
            if session_override is not None
            else coordinator.session_store
        ),
        launch_repository=coordinator.launch_repository,
        workflow_service=coordinator.workflow_service,
    )


class RegenerateQuestionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1)


class RegenerateAllRequest(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1)
    confirmed: Literal[True]
    configuration: PlanConfigurationSnapshot | None = None


class AnswerRequest(BaseModel):
    answer: str
    expected_version: int | None = None
    command_id: str | None = None


class SessionCommandRequest(BaseModel):
    expected_version: int | None = None
    command_id: str | None = None


class RescoreReportRequest(BaseModel):
    activate_on_success: bool = True
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class PrincipalConsentRequest(BaseModel):
    allowed_purposes: list[
        Literal[
            "proposal_write",
            "fact_storage",
            "read_shadow",
            "local_consume",
        ]
    ] = Field(min_length=1)


class PrincipalFactActionRequest(BaseModel):
    fact_type: Literal[
        "declared_preference",
        "confirmed_skill",
        "learning_goal",
        "accessibility_preference",
    ]
    normalized_value: dict[str, str]
    expected_version: int = Field(ge=1)


class PrincipalFactDeclareRequest(BaseModel):
    fact_type: Literal[
        "declared_preference",
        "confirmed_skill",
        "learning_goal",
        "accessibility_preference",
    ]
    normalized_value: dict[str, str]


class PrincipalFactRefActionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class PrincipalFactCorrectionRequest(PrincipalFactRefActionRequest):
    normalized_value: dict[str, str]


class DraftRequest(BaseModel):
    job_description: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)
    draft_id: str | None = None
    title: str | None = None
    job_tags: list[str] | None = None
    plan_family_id: str | None = None
    latest_plan_revision_id: str | None = None
    clear_plan: bool = False

    @field_validator("job_description", "resume_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def validate_plan_binding(self):
        if (self.plan_family_id is None) != (self.latest_plan_revision_id is None):
            raise ValueError(
                "plan_family_id and latest_plan_revision_id must be provided together"
            )
        if self.clear_plan and self.plan_family_id is not None:
            raise ValueError("clear_plan cannot be combined with a plan revision")
        return self


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/runtime")
def runtime_boundary():
    runtime_store = get_runtime_store()
    report_profile = get_report_runtime_profile()
    event_backend = get_runtime_event_backend()
    memory_config = load_effective_memory_config()
    runtime_enabled = memory_config.interview_graph.runtime_enabled
    rollout_percent = memory_config.interview_graph.rollout_percent
    session_store = (
        "PostgresInterviewSessionStore"
        if runtime_store == "postgres"
        else "InterviewSessionStore"
    )
    return {
        "runtime_store": runtime_store,
        "session_store": session_store,
        "report_runtime_profile": report_profile.name,
        "configuration_valid": report_profile.configuration_valid,
        "report_runtime_ready": report_profile.configuration_valid,
        "knowledge_runtime_ready": report_profile.configuration_valid,
        "report_job_store": (
            "InMemoryReportJobStore"
            if report_profile.report_job_store == "memory"
            else "PostgresReportJobStore"
        ),
        "report_worker": report_profile.report_worker,
        "knowledge_store": (
            "StaticKnowledgeStore"
            if report_profile.knowledge_store == "static"
            else "PgVectorKnowledgeStore"
        ),
        "embedding_provider": report_profile.embedding_provider,
        "preview": report_profile.preview,
        "configuration_warnings": list(report_profile.errors),
        "event_transport": {
            "interview": "sse",
            "report_progress": "polling",
        },
        "event_backend": event_backend,
        "capabilities": {
            "redis": event_backend == "celery",
            "celery": event_backend == "celery",
            "websocket": False,
            "langgraph": True,
        },
        "orchestration": {
            "engine": "versioned",
            "default_engine": "legacy",
            "langgraph_version": memory_config.interview_graph.version,
            "langgraph_runtime_enabled": runtime_enabled,
            "langgraph_rollout_percent": rollout_percent,
            "checkpoint_backend": (
                "postgres"
                if runtime_enabled and runtime_store == "postgres"
                else "disabled"
            ),
            "phase_aware": True,
            "resume_contract": "checkpointed_http_sse",
        },
        "agent_runtime": {
            "schema_version": "agent-runtime-v1",
            "event_schema_version": "runtime-event-v1",
            "trace_enabled": bool(os.getenv("AGENT_TRACE_DIR")),
            "outbox_enabled": runtime_store == "postgres",
            "agent_ledger_enabled": runtime_store == "postgres",
        },
        "memory_runtime": {
            **memory_readiness_payload(memory_config),
            "durable_metrics_available": bool(
                get_memory_metric_store().diagnostics().get("data_complete")
            ),
        },
    }


@router.get("/runtime/memory-metrics")
def memory_metrics_boundary(
    window_minutes: int = Query(default=60),
):
    _require_trusted_local_metrics()
    try:
        return get_memory_metric_store().aggregate(
            window_minutes=window_minutes
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/runtime/memory-budget-shadow")
def memory_budget_shadow_boundary():
    _require_trusted_local_metrics()
    config = load_effective_memory_config()
    return {
        "schema_version": "memory-budget-shadow-status-v1",
        "configured": bool(config.budget.shadow_enabled),
        "active": False,
        "configuration_changed_by_endpoint": False,
        "durable_metrics_available": bool(
            get_memory_metric_store().diagnostics().get("data_complete")
        ),
        "question_memory_consumption_enabled": bool(
            config.compression.mode == "consume"
            and config.compression.interview_question_memory
        ),
        "long_term_consumption_available": False,
    }


def _principal_memory_lifecycle(identity):
    from app.services.principal_memory_consent import PrincipalMemoryConsentService
    from app.services.principal_memory_control import PrincipalMemoryControlService
    from app.services.principal_memory_lifecycle import PrincipalMemoryLifecycleService

    config = load_effective_memory_config()
    resolver = get_principal_identity_resolver()
    deletion_fence = get_principal_memory_deletion_tombstone_store()
    return PrincipalMemoryLifecycleService(
        identity_resolver=resolver,
        consent_service=PrincipalMemoryConsentService(
            identity_resolver=resolver,
            store=get_principal_memory_consent_store(),
            policy_version=config.long_term.consent_policy_version,
            control_service=PrincipalMemoryControlService(
                identity_resolver=resolver,
                store=get_principal_memory_control_store(),
            ),
            deletion_fence=deletion_fence,
        ),
        fact_store=get_principal_memory_fact_store(),
        session_store=get_session_store(),
        config=config,
        clock=lambda: datetime.now(timezone.utc),
        deletion_fence=deletion_fence,
    )


def _principal_memory_control():
    from app.services.principal_memory_control import PrincipalMemoryControlService

    return PrincipalMemoryControlService(
        identity_resolver=get_principal_identity_resolver(),
        store=get_principal_memory_control_store(),
        clock=lambda: datetime.now(timezone.utc),
    )


def _principal_memory_writer_guard(identity):
    fence = get_principal_memory_deletion_tombstone_store()
    if not hasattr(fence, "writer_guard"):
        return nullcontext()
    return fence.writer_guard(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
    )


def _principal_memory_safe_items(*, request, limit):
    identity = _require_trusted_local_principal_memory(request)
    store = get_principal_memory_fact_store()
    refs = get_principal_memory_safe_ref_store()
    facts = store.list_by_principal(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
        limit=limit,
        include_terminal=True,
    )
    return [
        {
            **_principal_memory_lifecycle(identity).safe_payload(fact),
            "safe_ref": refs.issue(fact),
        }
        for fact in facts
    ]


def _resolve_principal_memory_safe_ref(request, safe_ref):
    identity = _require_trusted_local_principal_memory(request)
    try:
        fact = get_principal_memory_safe_ref_store().resolve(
            safe_ref,
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
            fact_store=get_principal_memory_fact_store(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return identity, fact


@router.get("/runtime/principal-memory/status")
def principal_memory_status(request: Request):
    identity = _require_trusted_local_principal_memory(request)
    config = load_effective_memory_config()
    consent = get_principal_memory_consent_store().get_current(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
    )
    facts = get_principal_memory_fact_store().list_all_by_principal(
        deployment_id=identity.deployment_id,
        principal_id=identity.principal_id,
        include_terminal=True,
    )
    fence = get_principal_memory_deletion_tombstone_store()
    deletion_blocked = bool(
        hasattr(fence, "is_write_blocked")
        and fence.is_write_blocked(
            deployment_id=identity.deployment_id,
            principal_id=identity.principal_id,
        )
    )
    policy_current = bool(
        consent
        and consent.policy_version == config.long_term.consent_policy_version
    )
    return {
        "schema_version": "principal-memory-local-status-v1",
        "mode": config.long_term.mode,
        "global_enabled": bool(
            config.long_term.mode != "disabled"
            and _principal_memory_control().snapshot()["global_enabled"]
            and not deletion_blocked
        ),
        "consent": {
            "granted": bool(
                consent
                and consent.revoked_at is None
                and policy_current
                and not deletion_blocked
            ),
            "allowed_purposes": list(consent.allowed_purposes) if consent else [],
            "version": consent.version if consent else 0,
        },
        "fact_count": len(facts),
        "local_consumption_enabled": config.long_term.local_consumption_enabled,
        "deletion_fence_active": deletion_blocked,
    }


@router.put("/runtime/principal-memory/consent")
def grant_principal_memory_consent(
    payload: PrincipalConsentRequest,
    request: Request,
):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    from app.services.principal_memory_consent import PrincipalMemoryConsent

    config = load_effective_memory_config()
    try:
        with _principal_memory_writer_guard(identity):
            consent = get_principal_memory_consent_store().grant(
                PrincipalMemoryConsent(
                    deployment_id=identity.deployment_id,
                    principal_id=identity.principal_id,
                    policy_version=config.long_term.consent_policy_version,
                    allowed_purposes=payload.allowed_purposes,
                    granted_at=datetime.now(timezone.utc),
                )
            )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "schema_version": consent.schema_version,
        "policy_version": consent.policy_version,
        "allowed_purposes": consent.allowed_purposes,
        "granted_at": consent.granted_at.isoformat(),
        "revoked": False,
        "version": consent.version,
    }


@router.delete("/runtime/principal-memory/consent")
def revoke_principal_memory_consent(request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    now = datetime.now(timezone.utc)
    try:
        with _principal_memory_writer_guard(identity):
            consent = get_principal_memory_consent_store().revoke(
                deployment_id=identity.deployment_id,
                principal_id=identity.principal_id,
                revoked_at=now,
            )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "revoked": consent is not None,
        "facts_retained": True,
    }


@router.get("/runtime/principal-memory/facts")
def list_principal_memory_facts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
):
    return {
        "schema_version": "principal-memory-safe-list-v1",
        "items": _principal_memory_safe_items(request=request, limit=limit),
    }


@router.post("/runtime/principal-memory/disable")
def disable_principal_memory(request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    with _principal_memory_writer_guard(identity):
        control = _principal_memory_control().set_global_enabled(False)
    return {"global_enabled": False, "version": control.version, "facts_retained": True}


@router.post("/runtime/principal-memory/enable")
def enable_principal_memory(request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    with _principal_memory_writer_guard(identity):
        control = _principal_memory_control().set_global_enabled(True)
    return {"global_enabled": True, "version": control.version, "facts_retained": True}


@router.post("/runtime/principal-memory/sessions/{session_id}/ignore")
def ignore_principal_memory_session(session_id: str, request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    try:
        get_session_store().get(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    with _principal_memory_writer_guard(identity):
        control = _principal_memory_control().set_session_ignored(session_id, True)
    return {"session_ignored": True, "version": control.version}


@router.delete("/runtime/principal-memory/sessions/{session_id}/ignore")
def allow_principal_memory_session(session_id: str, request: Request):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    try:
        get_session_store().get(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    with _principal_memory_writer_guard(identity):
        control = _principal_memory_control().set_session_ignored(session_id, False)
    return {"session_ignored": False, "version": control.version}


@router.post("/runtime/principal-memory/facts")
def declare_principal_memory_fact(
    payload: PrincipalFactDeclareRequest,
    request: Request,
):
    identity = _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    from app.services.principal_memory_contracts import canonical_principal_fact

    try:
        return _principal_memory_lifecycle(identity).declare(
            fact_type=payload.fact_type,
            normalized_fact=canonical_principal_fact(payload.normalized_value),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _principal_fact_ref_action(request, safe_ref, payload, action):
    identity, fact = _resolve_principal_memory_safe_ref(request, safe_ref)
    if fact.version != payload.expected_version:
        raise HTTPException(status_code=409, detail="principal memory version changed")
    try:
        return getattr(_principal_memory_lifecycle(identity), action)(
            fact_id=fact.fact_id,
            expected_version=payload.expected_version,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runtime/principal-memory/facts/{safe_ref}/confirm")
def confirm_principal_memory_fact(
    safe_ref: str,
    payload: PrincipalFactRefActionRequest,
    request: Request,
):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    return _principal_fact_ref_action(request, safe_ref, payload, "confirm")


@router.post("/runtime/principal-memory/facts/{safe_ref}/reject")
def reject_principal_memory_fact(
    safe_ref: str,
    payload: PrincipalFactRefActionRequest,
    request: Request,
):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    return _principal_fact_ref_action(request, safe_ref, payload, "reject")


@router.post("/runtime/principal-memory/facts/{safe_ref}/revoke")
def revoke_principal_memory_fact(
    safe_ref: str,
    payload: PrincipalFactRefActionRequest,
    request: Request,
):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    return _principal_fact_ref_action(request, safe_ref, payload, "revoke")


@router.put("/runtime/principal-memory/facts/{safe_ref}")
def correct_principal_memory_fact(
    safe_ref: str,
    payload: PrincipalFactCorrectionRequest,
    request: Request,
):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    identity, fact = _resolve_principal_memory_safe_ref(request, safe_ref)
    if fact.version != payload.expected_version or fact.status != "active":
        raise HTTPException(status_code=409, detail="principal memory version changed")
    from app.services.principal_memory_contracts import canonical_principal_fact
    import json

    normalized = canonical_principal_fact(payload.normalized_value)
    if next(iter(json.loads(normalized))) != next(iter(json.loads(fact.normalized_fact))):
        raise HTTPException(status_code=409, detail="principal memory taxonomy key changed")
    try:
        return _principal_memory_lifecycle(identity).declare(
            fact_type=fact.fact_type,
            normalized_fact=normalized,
            expected_predecessor_fact_id=fact.fact_id,
            expected_predecessor_version=payload.expected_version,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runtime/principal-memory/export")
def export_principal_memory(request: Request):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    from app.services.principal_memory_rights import PrincipalMemoryExportService

    try:
        return PrincipalMemoryExportService(
            identity_resolver=get_principal_identity_resolver(),
            lifecycle_service=_principal_memory_lifecycle(None),
            consent_store=get_principal_memory_consent_store(),
            control_service=_principal_memory_control(),
            export_store=get_principal_memory_export_store(),
        ).create()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="memory export unavailable") from exc


@router.delete("/runtime/principal-memory")
def delete_principal_memory(request: Request):
    _require_trusted_local_principal_memory(request)
    _require_local_memory_mutation(request)
    from app.services.principal_memory_deletion import (
        PrincipalMemoryDeletionIncomplete,
        PrincipalMemoryDeletionService,
    )
    from app.services.runtime import get_principal_memory_durable_ledger

    try:
        durable_ledger = get_principal_memory_durable_ledger()
        if durable_ledger is None:
            raise RuntimeError("TOMBSTONE_LEDGER_REQUIRED")
        durable_ledger.require_ready()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="memory deletion unavailable") from exc

    try:
        return PrincipalMemoryDeletionService(
            identity_resolver=get_principal_identity_resolver(),
            consent_store=get_principal_memory_consent_store(),
            fact_store=get_principal_memory_fact_store(),
            control_store=get_principal_memory_control_store(),
            export_store=get_principal_memory_export_store(),
            tombstone_store=get_principal_memory_deletion_tombstone_store(),
            cache_purge=get_principal_memory_safe_ref_store().purge,
            cache_count=get_principal_memory_safe_ref_store().count,
            ledger_writer=durable_ledger.append_completed,
            ledger_applied_writer=durable_ledger.mark_applied,
        ).purge_current_principal()
    except PrincipalMemoryDeletionIncomplete as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "memory_delete_retryable", "stage": exc.stage},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="memory deletion unavailable") from exc


@router.post("/prep")
def prep_interview(
    payload: PrepRequest,
    plan_store=Depends(get_prep_plan_store),
    revision_store=Depends(get_plan_revision_store),
):
    configuration = payload.configuration or default_plan_configuration()
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
            execution_runner=get_agent_execution_runner(),
            configuration=configuration,
        )
        revision_plan = prepared_plan_revision(plan, configuration)
    except PlanGenerationValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    job_tags = extract_job_tags(payload.job_description)
    try:
        response = plan_store.create(
            plan=plan,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            job_tags=job_tags,
            source_draft_id=payload.draft_id,
        )
    except PrepPlanError as exc:
        _raise_prep_plan_error(exc)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PREP_PLAN_STORE_UNAVAILABLE",
                "message": "计划暂时无法保存，请稍后重试。",
                "retryable": True,
            },
        ) from exc

    revision = revision_store.create_initial(
        source_payload={
            "job_description": payload.job_description,
            "resume_text": payload.resume_text,
            "job_tags": job_tags,
        },
        plan=revision_plan,
        retention_policy="local-v1",
        generator_version=configuration.generator_version,
    )
    response.update(_plan_revision_payload(revision))
    response["job_tags"] = job_tags
    return response


@router.get("/prep-plans/{plan_id}")
def get_prep_plan(plan_id: str, plan_store=Depends(get_prep_plan_store)):
    try:
        return plan_store.get(plan_id)
    except PrepPlanError as exc:
        _raise_prep_plan_error(exc)


@router.patch("/prep-plans/{plan_id}")
def patch_prep_plan(
    plan_id: str,
    payload: PrepPlanPatchRequest,
    plan_store=Depends(get_prep_plan_store),
):
    try:
        return plan_store.apply_operations(
            plan_id,
            expected_version=payload.expected_version,
            operations=payload.operations,
        )
    except PrepPlanError as exc:
        _raise_prep_plan_error(exc)


@router.post("/prep-plans/{plan_id}/questions/{question_id}/regenerate")
def regenerate_prep_question(
    plan_id: str,
    question_id: str,
    payload: PrepQuestionRegenerateRequest,
    plan_store=Depends(get_prep_plan_store),
    regenerator: PrepQuestionRegenerator = Depends(get_prep_question_regenerator),
):
    try:
        return regenerator.regenerate(
            plan_store,
            plan_id=plan_id,
            question_id=question_id,
            expected_version=payload.expected_version,
        )
    except PrepPlanError as exc:
        _raise_prep_plan_error(exc)


@router.patch("/interview-plans/{plan_family_id}")
def edit_interview_plan(
    plan_family_id: str,
    payload: PlanEditRequest,
    revision_store=Depends(get_plan_revision_store),
):
    provider_managed = {
        operation.op
        for operation in payload.operations
        if operation.op in {"regenerate_question", "regenerate_all"}
    }
    if provider_managed:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "provider_managed_operation",
                "message": "regeneration output must be created by the server Provider boundary",
                "operations": sorted(provider_managed),
            },
        )
    return _apply_plan_edit(plan_family_id, payload, revision_store)


@router.get("/interview-plans/{plan_family_id}/revisions")
def list_interview_plan_revisions(
    plan_family_id: str,
    revision_store=Depends(get_plan_revision_store),
):
    try:
        revisions = revision_store.list_revisions(plan_family_id)
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    latest_revision = revisions[-1].revision
    return {
        "plan_family_id": plan_family_id,
        "latest_revision": latest_revision,
        "revisions": [
            {
                "plan_revision_id": revision.plan_revision_id,
                "revision": revision.revision,
                "parent_revision_id": revision.parent_revision_id,
                "plan_sha256": revision.plan_sha256,
                "created_at": revision.created_at.isoformat(),
                "created_reason": revision.created_reason,
                "source_kind": revision.source_kind,
                "title": revision.plan.title,
                "question_count": len(revision.plan.questions),
                "is_latest": revision.revision == latest_revision,
            }
            for revision in reversed(revisions)
        ],
    }


@router.get("/interview-plans/{plan_family_id}/revisions/{plan_revision_id}")
def get_interview_plan_revision(
    plan_family_id: str,
    plan_revision_id: str,
    revision_store=Depends(get_plan_revision_store),
):
    try:
        revision = revision_store.get_by_id(plan_revision_id)
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if revision.plan_family_id != plan_family_id:
        raise HTTPException(status_code=404, detail="plan revision not found")
    return _plan_revision_payload(revision)


def _apply_plan_edit(
    plan_family_id: str,
    payload: PlanEditRequest,
    revision_store,
    *,
    request_sha256: str | None = None,
    allow_configuration_change: bool = False,
):
    try:
        revision = InterviewPlanEditor(revision_store).apply(
            plan_family_id,
            payload,
            request_sha256=request_sha256,
            allow_configuration_change=allow_configuration_change,
        )
    except PlanRevisionConflict as exc:
        current = None
        try:
            latest = revision_store.get_latest(plan_family_id)
            current = {
                "plan_revision_id": latest.plan_revision_id,
                "revision": latest.revision,
                "plan_sha256": latest.plan_sha256,
            }
        except PlanRevisionNotFound:
            pass
        return JSONResponse(
            status_code=409,
            content={"code": "plan_revision_conflict", "current_revision": current},
        )
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanSourceUnavailable as exc:
        raise HTTPException(status_code=422, detail={"code": "plan_source_unavailable"}) from exc
    except PlanOperationValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.detail()) from exc
    return _plan_revision_payload(revision)


@router.post("/interview-plans/{plan_family_id}/questions/{question_id}/regenerate")
def regenerate_interview_question(
    plan_family_id: str,
    question_id: str,
    payload: RegenerateQuestionRequest,
    revision_store=Depends(get_plan_revision_store),
    regenerator: ProviderPlanRegenerator = Depends(get_plan_regenerator),
):
    request_sha256 = canonical_sha256(
        {
            "operation": "regenerate_question",
            "plan_family_id": plan_family_id,
            "question_id": question_id,
            **payload.model_dump(mode="json"),
        }
    )
    try:
        current = revision_store.get_latest(plan_family_id)
        if current.revision != payload.expected_revision:
            raise PlanRevisionConflict(
                "expected revision does not match latest revision",
                current_revision=current.revision,
            )
        source = revision_store.get_source(current.source_id)
        if source.protected_payload is None:
            raise PlanSourceUnavailable("plan source payload is unavailable")
        generated = regenerator.regenerate_question(
            current=current,
            source=source.protected_payload,
            question_id=question_id,
        )
    except PlanRevisionConflict:
        return _apply_plan_edit(
            plan_family_id,
            PlanEditRequest(
                expected_revision=payload.expected_revision,
                request_id=payload.request_id,
                operations=[
                    PlanOperation(
                        op="edit_focus",
                        question_id=question_id,
                        focus="idempotency-replay-placeholder",
                    )
                ],
            ),
            revision_store,
            request_sha256=request_sha256,
        )
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanSourceUnavailable as exc:
        raise HTTPException(
            status_code=422, detail={"code": "plan_source_unavailable"}
        ) from exc
    except PlanRegenerationFailed as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    request = PlanEditRequest(
        expected_revision=payload.expected_revision,
        request_id=payload.request_id,
        operations=[
            PlanOperation(
                op="regenerate_question",
                question_id=question_id,
                question_text=generated.question_text,
                focus=generated.focus,
                question_type=generated.question_type,
                difficulty=generated.difficulty,
                expected_minutes=generated.expected_minutes,
                expected_followups=generated.expected_followups,
                knowledge_binding=generated.knowledge_binding,
            )
        ],
    )
    return _apply_plan_edit(
        plan_family_id,
        request,
        revision_store,
        request_sha256=request_sha256,
    )


@router.post("/interview-plans/{plan_family_id}/regenerate")
def regenerate_entire_interview_plan(
    plan_family_id: str,
    payload: RegenerateAllRequest,
    revision_store=Depends(get_plan_revision_store),
    regenerator: ProviderPlanRegenerator = Depends(get_plan_regenerator),
):
    try:
        current = revision_store.get_latest(plan_family_id)
        if current.revision != payload.expected_revision:
            return _apply_plan_edit(
                plan_family_id,
                PlanEditRequest(
                    expected_revision=payload.expected_revision,
                    request_id=payload.request_id,
                    operations=[
                        PlanOperation(
                            op="regenerate_all",
                            regenerated_plan=current.plan,
                        )
                    ],
                ),
                revision_store,
                request_sha256=canonical_sha256(
                    {
                        "operation": "regenerate_all",
                        "plan_family_id": plan_family_id,
                        **payload.model_dump(mode="json"),
                    }
                ),
            )
        source = revision_store.get_source(current.source_id)
        if source.protected_payload is None:
            raise PlanSourceUnavailable("plan source payload is unavailable")
        regeneration_arguments = {
            "current": current,
            "source": source.protected_payload,
        }
        if payload.configuration is not None:
            regeneration_arguments["configuration"] = payload.configuration
        regenerated_plan = regenerator.regenerate_all(**regeneration_arguments)
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanSourceUnavailable as exc:
        raise HTTPException(
            status_code=422, detail={"code": "plan_source_unavailable"}
        ) from exc
    except PlanRegenerationFailed as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    request_sha256 = canonical_sha256(
        {
            "operation": "regenerate_all",
            "plan_family_id": plan_family_id,
            **payload.model_dump(mode="json"),
        }
    )
    request = PlanEditRequest(
        expected_revision=payload.expected_revision,
        request_id=payload.request_id,
        operations=[
            PlanOperation(op="regenerate_all", regenerated_plan=regenerated_plan)
        ],
    )
    return _apply_plan_edit(
        plan_family_id,
        request,
        revision_store,
        request_sha256=request_sha256,
        allow_configuration_change=payload.configuration is not None,
    )


@router.post("/interview-drafts")
def save_interview_draft(
    payload: DraftRequest,
    draft_store=Depends(get_draft_store),
    revision_store=Depends(get_plan_revision_store),
):
    with revision_store.source_reference_recovery_lock():
        return _save_interview_draft_locked(payload, draft_store, revision_store)


def _save_interview_draft_locked(payload, draft_store, revision_store):
    try:
        if not hasattr(draft_store, "prepare_save"):
            if payload.plan_family_id is not None or payload.clear_plan:
                raise ValueError(
                    "revision-bound drafts require a revision-aware draft store"
                )
            return draft_store.save(
                draft_id=payload.draft_id,
                job_description=payload.job_description,
                resume_text=payload.resume_text,
                title=payload.title,
                job_tags=payload.job_tags
                if payload.job_tags is not None
                else extract_job_tags(payload.job_description),
            )
        _reconcile_draft_source_references(draft_store, revision_store)
        existing_draft = None
        previous_revision = None
        if payload.draft_id is not None:
            try:
                existing_draft = draft_store.get(payload.draft_id)
                previous_revision_id = existing_draft.get("latest_plan_revision_id")
                if previous_revision_id:
                    previous_revision = revision_store.get_by_id(previous_revision_id)
            except ValueError:
                pass
        plan_source_sha256 = None
        revision = None
        if payload.latest_plan_revision_id is not None:
            revision = revision_store.get_by_id(payload.latest_plan_revision_id)
            if revision.plan_family_id != payload.plan_family_id:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "draft_plan_family_mismatch"},
                )
            plan_source_sha256 = revision.source_sha256
        prepared = draft_store.prepare_save(
            draft_id=payload.draft_id,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            title=payload.title,
            job_tags=payload.job_tags
            if payload.job_tags is not None
            else extract_job_tags(payload.job_description),
            plan_family_id=payload.plan_family_id,
            latest_plan_revision_id=payload.latest_plan_revision_id,
            plan_source_sha256=plan_source_sha256,
            clear_plan=payload.clear_plan,
        )
        target_revision_id = prepared.get("latest_plan_revision_id")
        target_revision = (
            revision_store.get_by_id(target_revision_id)
            if target_revision_id is not None
            else None
        )
        old_source_id = previous_revision.source_id if previous_revision else None
        new_source_id = target_revision.source_id if target_revision else None
        revision_store.replace_source_reference(
            old_source_id=old_source_id,
            new_source_id=new_source_id,
            owner_type="draft",
            owner_id=prepared["draft_id"],
        )
        try:
            draft = draft_store.commit_save(prepared)
        except Exception:
            revision_store.replace_source_reference(
                old_source_id=new_source_id,
                new_source_id=old_source_id,
                owner_type="draft",
                owner_id=prepared["draft_id"],
            )
            raise
        return draft
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanSourceUnavailable as exc:
        raise HTTPException(
            status_code=422, detail={"code": "plan_source_unavailable"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/interview-drafts/{draft_id}")
def get_interview_draft(draft_id: str, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.get(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/interview-drafts/{draft_id}", status_code=204)
def delete_interview_draft(
    draft_id: str,
    draft_store=Depends(get_draft_store),
    plan_store=Depends(get_prep_plan_store),
    revision_store=Depends(get_plan_revision_store),
):
    with revision_store.source_reference_recovery_lock():
        return _delete_interview_draft_locked(
            draft_id,
            draft_store,
            plan_store,
            revision_store,
        )


def _delete_interview_draft_locked(
    draft_id,
    draft_store,
    plan_store,
    revision_store,
):
    try:
        _reconcile_draft_source_references(draft_store, revision_store)
        draft = draft_store.get(draft_id)
        revision_id = draft.get("latest_plan_revision_id")
        revision = revision_store.get_by_id(revision_id) if revision_id else None
        if revision is not None:
            revision_store.replace_source_reference(
                old_source_id=revision.source_id,
                new_source_id=None,
                owner_type="draft",
                owner_id=draft_id,
            )
        try:
            deleted = draft_store.delete(draft_id)
            if deleted is False:
                raise ValueError("draft not found")
            if getattr(draft_store, "durability", None) != "postgres":
                plan_store.delete_by_source_draft(draft_id)
        except Exception:
            if revision is not None:
                revision_store.replace_source_reference(
                    old_source_id=None,
                    new_source_id=revision.source_id,
                    owner_type="draft",
                    owner_id=draft_id,
                )
            raise
    except (ValueError, PlanRevisionNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/reports")
def list_reports(
    status: str | None = None,
    query: str | None = Query(default=None, max_length=200),
    days: int | None = Query(default=None, ge=1, le=3650),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    store: InterviewSessionStore = Depends(get_session_store),
):
    if status not in (None, "processing", "completed", "failed"):
        raise HTTPException(status_code=422, detail="invalid status")
    normalized_query = query.strip() if query and query.strip() else None
    reports = store.list_reports(
        status=status,
        query=normalized_query,
        days=days,
        limit=limit,
        offset=offset,
    )
    total = store.count_reports(
        status=status,
        query=normalized_query,
        days=days,
    )
    status_totals = store.report_status_totals(
        query=normalized_query,
        days=days,
    )
    items = [
        _report_summary_to_dict(
            item["session_id"],
            item["record"],
            session_summary=item["session_summary"],
        )
        for item in reports
    ]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "status_totals": status_totals,
    }


@router.post("/interviews")
def start_interview(
    payload: StartInterviewRequest,
    store: InterviewSessionStore | None = Depends(get_legacy_launch_session_store),
    launch_coordinator: InterviewLaunchCoordinator | None = Depends(
        get_request_interview_launch_coordinator
    ),
    revision_store=Depends(get_plan_revision_store),
):
    if payload.plan_revision_id is not None:
        if store is None:
            raise HTTPException(status_code=503, detail="session store is unavailable")
        with revision_store.source_reference_recovery_lock():
            return _start_interview_locked(payload, store, revision_store)

    if payload.plan_id is not None:
        if payload.expected_plan_version is None or not payload.command_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_LAUNCH_REQUEST",
                    "message": "缺少计划版本或安全启动标识。",
                    "retryable": False,
                },
            )
        if launch_coordinator is None:
            raise HTTPException(
                status_code=503,
                detail="launch coordinator is unavailable",
            )
        try:
            return launch_coordinator.launch(
                plan_id=payload.plan_id,
                expected_plan_version=payload.expected_plan_version,
                command_id=payload.command_id,
            )
        except PrepPlanError as exc:
            _raise_prep_plan_error(exc)

    if not payload.job_description or not payload.resume_text:
        raise HTTPException(status_code=422, detail="legacy launch input is incomplete")
    if store is None:
        raise HTTPException(status_code=503, detail="session store is unavailable")
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
            llm=store.llm,
            execution_runner=get_agent_execution_runner(),
        )
        validate_launchable_interview_plan(plan)
        job_tags = extract_job_tags(payload.job_description)
        if (
            get_runtime_store() == "postgres"
            and get_interview_langgraph_rollout_percent() > 0
        ):
            turn = get_interview_workflow_service().start(
                plan,
                job_description=payload.job_description,
                resume_text=payload.resume_text,
                job_tags=job_tags,
            )
        else:
            turn = store.start(
                plan,
                job_description=payload.job_description,
                resume_text=payload.resume_text,
                job_tags=job_tags,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)


def _start_interview_locked(payload, store, revision_store):
    try:
        # A previous process can have died after reserving a source but before
        # inserting its session, or after inserting the session but before the
        # reference became visible. Persistent stores repair both directions.
        revision_store.reconcile_session_source_references()
        revision = revision_store.get_by_id(payload.plan_revision_id)
        plan_binding = session_plan_binding_from_revision(revision)
        session_id = _session_id_for_start_request(
            revision.plan_family_id,
            payload.request_id,
        )
        turn = _load_session_start_replay(
            store,
            session_id,
            plan_binding,
            expected_revision=payload.expected_revision,
            plan_sha256=payload.plan_sha256,
        )
        if turn is not None:
            revision_store.add_source_reference(
                revision.source_id,
                owner_type="session",
                owner_id=turn.session_id,
            )
            return _turn_to_dict(turn)
        latest = revision_store.get_latest(revision.plan_family_id)
        if latest.plan_revision_id != revision.plan_revision_id:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "plan_revision_conflict",
                    "current_revision": {
                        "plan_revision_id": latest.plan_revision_id,
                        "revision": latest.revision,
                        "plan_sha256": latest.plan_sha256,
                    },
                },
            )
        if payload.expected_revision != revision.revision:
            raise HTTPException(status_code=409, detail="plan revision conflict")
        if payload.plan_sha256 != revision.plan_sha256:
            raise HTTPException(status_code=409, detail="plan hash conflict")
        source = revision_store.get_source(revision.source_id)
        if source.protected_payload is None:
            raise HTTPException(status_code=422, detail="plan source unavailable")
        plan = v2_plan_to_legacy(revision.plan)
        source_payload = source.protected_payload
        job_description = source_payload.job_description
        resume_text = source_payload.resume_text
        job_tags = list(source_payload.job_tags)
        revision_store.add_source_reference(
            revision.source_id,
            owner_type="session",
            owner_id=session_id,
        )
        try:
            if (
                get_runtime_store() == "postgres"
                and get_interview_langgraph_rollout_percent() > 0
            ):
                turn = get_interview_workflow_service().start(
                    plan,
                    job_description=job_description,
                    resume_text=resume_text,
                    job_tags=job_tags,
                    plan_binding=plan_binding,
                    session_id=session_id,
                )
            else:
                turn = store.start(
                    plan,
                    job_description=job_description,
                    resume_text=resume_text,
                    job_tags=job_tags,
                    plan_binding=plan_binding,
                    session_id=session_id,
                )
        except Exception:
            turn = _load_session_start_replay(
                store,
                session_id,
                plan_binding,
                expected_revision=payload.expected_revision,
                plan_sha256=payload.plan_sha256,
            )
            if turn is None:
                revision_store.remove_source_reference(
                    revision.source_id,
                    owner_type="session",
                    owner_id=session_id,
                )
                raise
    except SessionStartRequestConflict:
        return JSONResponse(
            status_code=409,
            content={"code": _SESSION_START_REQUEST_CONFLICT},
        )
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _turn_to_dict(turn)


class SessionStartRequestConflict(Exception):
    pass


def _session_id_for_start_request(plan_family_id: str, request_id: str) -> str:
    identity = f"{plan_family_id}:{request_id}"
    return str(uuid5(_SESSION_START_REQUEST_NAMESPACE, identity))


def _reconcile_draft_source_references(draft_store, revision_store) -> None:
    if not hasattr(draft_store, "plan_revision_bindings"):
        return
    expected = {}
    for draft_id, revision_id in draft_store.plan_revision_bindings().items():
        expected[draft_id] = revision_store.get_by_id(revision_id).source_id
    revision_store.reconcile_source_references(
        owner_type="draft",
        expected=expected,
    )


def _load_session_start_replay(
    store,
    session_id: str,
    plan_binding,
    *,
    expected_revision: int,
    plan_sha256: str,
):
    try:
        state = store.get(session_id)
    except ValueError as exc:
        if str(exc) == "session not found":
            return None
        raise
    existing_binding = session_plan_binding_from_state(state)
    if (
        existing_binding != plan_binding
        or existing_binding.revision != expected_revision
        or existing_binding.plan_sha256 != plan_sha256
    ):
        raise SessionStartRequestConflict()
    return store._to_turn(state, follow_up=None)


def _is_unique_constraint_violation(exc: Exception) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if getattr(current, "pgcode", None) == "23505":
            return True
        current = current.__cause__ or current.__context__
    return False


def _plan_revision_payload(revision) -> dict:
    legacy = public_interview_plan_payload(v2_plan_to_legacy(revision.plan))
    public_plan = public_interview_plan_v2_payload(revision.plan)
    if "prep_context" in legacy:
        public_plan["prep_context"] = legacy["prep_context"]
    else:
        public_plan.pop("prep_context", None)
    return {
        "plan_family_id": revision.plan_family_id,
        "plan_revision_id": revision.plan_revision_id,
        "revision": revision.revision,
        "plan_sha256": revision.plan_sha256,
        "audit": revision.audit.model_dump(mode="json"),
        "budget_assessment": assess_interview_plan_budget(
            revision.plan
        ).model_dump(mode="json"),
        "plan": public_plan,
        "legacy_plan": legacy,
    }


def _raise_prep_plan_error(exc: PrepPlanError) -> None:
    raise exc


@router.get("/interviews/{session_id}")
def get_interview_session(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
        snapshot = (
            get_interview_workflow_service().snapshot(session_id)
            if is_durable_interview_version(state.get("workflow_engine"))
            else store.snapshot(session_id)
        )
        public_plan = public_interview_plan_payload(state["plan"])
        snapshot["prep_context"] = public_plan.get("prep_context")
        return snapshot
    except ValueError as exc:
        _raise_value_error(exc)


@router.delete("/interviews/{session_id}", status_code=202)
def delete_interview_session(
    session_id: str,
    background_tasks: BackgroundTasks,
):
    _require_trusted_local_deletion()
    service = get_session_deletion_service()
    try:
        job = service.request(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    response = _deletion_job_payload(job)
    if job.status in {"queued", "running"}:
        background_tasks.add_task(get_session_deletion_worker().run_once)
    return response


@router.get("/interviews/{session_id}/deletion")
def get_interview_session_deletion(session_id: str):
    _require_trusted_local_deletion()
    try:
        job = get_session_deletion_service().get(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _deletion_job_payload(job)


@router.get("/interviews/{session_id}/agent-runs")
def list_agent_runs(
    session_id: str,
    agent: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    store: InterviewSessionStore = Depends(get_session_store),
    control=Depends(get_runtime_control_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    if control is None:
        return {"session_id": session_id, "items": []}
    correlation_id = correlation_id_from_plan(
        state["plan"],
        session_id=session_id,
    )
    return {
        "session_id": session_id,
        "items": control.list_agent_runs(
            session_id=session_id,
            correlation_id=correlation_id,
            agent=agent,
            status=status,
            limit=limit,
        ),
    }


@router.get("/interviews/{session_id}/runtime-events")
def list_runtime_events(
    session_id: str,
    status: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    store: InterviewSessionStore = Depends(get_session_store),
    control=Depends(get_runtime_control_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    if control is None:
        return {"session_id": session_id, "items": []}
    return {
        "session_id": session_id,
        "items": control.list_runtime_events(
            session_id=session_id,
            status=status,
            event_type=event_type,
            limit=limit,
        ),
    }


@router.post("/interviews/{session_id}/answer")
def submit_answer(
    session_id: str,
    payload: AnswerRequest,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
    publisher=Depends(get_event_publisher),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
        if is_durable_interview_version(state.get("workflow_engine")):
            accepted = get_interview_workflow_service().submit_command(
                session_id,
                command_type="answer",
                expected_version=payload.expected_version,
                command_id=payload.command_id,
                answer_text=payload.answer,
            )
            return JSONResponse(
                status_code=202,
                content=accepted.model_dump(mode="json"),
            )
        before_state = _snapshot_session_state(store, session_id)
        turn = store.submit_answer(
            session_id,
            payload.answer,
            expected_version=payload.expected_version,
            command_id=payload.command_id,
        )
        after_state = _snapshot_session_state(store, session_id)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)
    _publish_round_closed_event(
        publisher,
        store,
        before_state,
        after_state,
    )
    enqueue_report_if_needed(
        turn_status=turn.status,
        session_id=session_id,
        store=store,
        job_store_factory=get_report_job_store,
        background_tasks=background_tasks,
    )
    return _turn_to_dict(turn)


@router.post("/interviews/{session_id}/answer/stream")
def submit_answer_stream(
    session_id: str,
    payload: AnswerRequest,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
    publisher=Depends(get_event_publisher),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
        if is_durable_interview_version(state.get("workflow_engine")):
            accepted = get_interview_workflow_service().submit_command(
                session_id,
                command_type="answer",
                expected_version=payload.expected_version,
                command_id=payload.command_id,
                answer_text=payload.answer,
            )
            workflow = get_interview_workflow_service()
            return StreamingResponse(
                workflow.event_stream.iter_sse(
                    session_id, accepted.command_id
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        before_state = _snapshot_session_state(store, session_id)
        prepared = store.prepare_streaming_answer(
            session_id,
            payload.answer,
            expected_version=payload.expected_version,
            command_id=payload.command_id,
        )
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)

    def event_stream() -> Iterator[str]:
        try:
            if prepared.stream_follow_up:
                chunks: list[str] = []
                for chunk in store.stream_followup(session_id):
                    chunks.append(chunk)
                    yield InterviewStreamChunkEvent(delta=chunk).to_sse()
                follow_up_text = "".join(chunks).strip()
            else:
                decision = prepared.state["decision"]
                follow_up_text = decision.get("follow_up") if decision else None

            finalized_state = store.complete_streaming_answer(
                session_id,
                follow_up_text=follow_up_text,
                expected_version=prepared.state["state_version"],
                command_id=payload.command_id,
            )
            after_state = deepcopy(finalized_state)
            _publish_round_closed_event(
                publisher,
                store,
                before_state,
                after_state,
            )
            turn = store._to_turn(finalized_state, follow_up=_extract_follow_up(finalized_state))
            enqueue_report_if_needed(
                turn_status=turn.status,
                session_id=session_id,
                store=store,
                job_store_factory=get_report_job_store,
                background_tasks=background_tasks,
            )
            yield InterviewStreamDoneEvent(turn=_turn_to_dict(turn)).to_sse()
        except Exception as exc:  # pragma: no cover - defensive streaming boundary
            yield InterviewStreamErrorEvent(detail=str(exc)).to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/interviews/{session_id}/finish")
def finish_interview(
    session_id: str,
    background_tasks: BackgroundTasks,
    payload: SessionCommandRequest | None = None,
    store: InterviewSessionStore = Depends(get_session_store),
    publisher=Depends(get_event_publisher),
):
    payload = payload or SessionCommandRequest()
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
        if is_durable_interview_version(state.get("workflow_engine")):
            accepted = get_interview_workflow_service().submit_command(
                session_id,
                command_type="finish",
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
            return JSONResponse(
                status_code=202,
                content=accepted.model_dump(mode="json"),
            )
        before_state = _snapshot_session_state(store, session_id)
        turn = store.finish(
            session_id,
            expected_version=payload.expected_version,
            command_id=payload.command_id,
        )
        after_state = _snapshot_session_state(store, session_id)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)
    _publish_round_closed_event(
        publisher,
        store,
        before_state,
        after_state,
    )
    enqueue_report_if_needed(
        turn_status=turn.status,
        session_id=session_id,
        store=store,
        job_store_factory=get_report_job_store,
        background_tasks=background_tasks,
    )
    return _turn_to_dict(turn)


@router.post("/interviews/{session_id}/skip")
def skip_interview_question(
    session_id: str,
    background_tasks: BackgroundTasks,
    payload: SessionCommandRequest | None = None,
    store: InterviewSessionStore = Depends(get_session_store),
    publisher=Depends(get_event_publisher),
):
    payload = payload or SessionCommandRequest()
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
        if is_durable_interview_version(state.get("workflow_engine")):
            accepted = get_interview_workflow_service().submit_command(
                session_id,
                command_type="skip",
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
            return JSONResponse(
                status_code=202,
                content=accepted.model_dump(mode="json"),
            )
        before_state = _snapshot_session_state(store, session_id)
        turn = store.skip(
            session_id,
            expected_version=payload.expected_version,
            command_id=payload.command_id,
        )
        after_state = _snapshot_session_state(store, session_id)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)
    _publish_round_closed_event(
        publisher,
        store,
        before_state,
        after_state,
    )
    enqueue_report_if_needed(
        turn_status=turn.status,
        session_id=session_id,
        store=store,
        job_store_factory=get_report_job_store,
        background_tasks=background_tasks,
    )
    return _turn_to_dict(turn)


@router.get(
    "/interviews/{session_id}/commands/{command_id}/stream"
)
def stream_interview_command(
    session_id: str,
    command_id: str,
    request: Request,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    workflow = get_interview_workflow_service()
    return StreamingResponse(
        workflow.event_stream.iter_sse(
            session_id,
            command_id,
            after_event_id=request.headers.get("Last-Event-ID"),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/interviews/{session_id}/report")
def get_interview_report(
    session_id: str,
    request: Request,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=404, detail="interview is not finished")

    artifact_store = _optional_report_artifact_store(request)
    active_artifact, latest_job = (
        _active_report_view(artifact_store, session_id)
        if artifact_store is not None
        and get_report_artifact_read_mode() == "artifact_first"
        else (None, None)
    )
    if active_artifact is not None:
        return {
            "active_artifact": _report_artifact_to_dict(
                active_artifact,
                active=True,
                latest_job=latest_job,
            ),
            "latest_job": _report_job_v2_to_dict(latest_job),
        }
    if latest_job is not None:
        if latest_job.status in {"queued", "running", "retrying"}:
            return JSONResponse(
                status_code=202,
                content={
                    "active_artifact": None,
                    "latest_job": _report_job_v2_to_dict(latest_job),
                },
            )
        if latest_job.status == "failed":
            return JSONResponse(
                status_code=500,
                content={
                    "active_artifact": None,
                    "latest_job": _report_job_v2_to_dict(latest_job),
                },
            )

    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        return JSONResponse(
            status_code=202,
            content={
                "status": "processing",
                "progress": record.progress.model_dump()
                if record is not None and record.progress is not None
                else None,
            },
        )
    if record.status == "failed":
        raise HTTPException(
            status_code=500,
            detail=_public_report_failure_message(
                _public_report_error_code(record.error or "")
            ),
        )
    evaluations = (
        store.list_question_evaluations(session_id)
        if hasattr(store, "list_question_evaluations")
        else []
    )
    reliability = build_report_reliability(
        state,
        record.report,
        evaluations,
        report_path=_public_report_path(record),
    )
    return {
        **record.report.model_dump(),
        "reliability": reliability.model_dump(),
    }


@router.post("/interviews/{session_id}/practice-plan", status_code=201)
def create_practice_plan(
    session_id: str,
    payload: PracticePlanRequest,
    store: InterviewSessionStore = Depends(get_session_store),
    plan_store=Depends(get_prep_plan_store),
    launch_repository=Depends(get_interview_launch_repository),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="interview not found") from exc
    if state["status"] != "finished":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRACTICE_INTERVIEW_NOT_FINISHED",
                "message": "面试结束并生成报告后才能创建针对性练习。",
                "retryable": False,
            },
        )
    record = store.get_report_record(session_id)
    if record is None or record.status != "completed" or record.report is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRACTICE_REPORT_NOT_READY",
                "message": "报告尚未完成，暂时无法创建针对性练习。",
                "retryable": record is not None and record.status == "processing",
            },
        )
    evaluations = (
        store.list_question_evaluations(session_id)
        if hasattr(store, "list_question_evaluations")
        else []
    )
    reliability = build_report_reliability(
        state,
        record.report,
        evaluations,
        report_path=_public_report_path(record),
    )
    try:
        return PracticePlanService(
            prep_plan_store=plan_store,
            launch_repository=launch_repository,
        ).create(
            state=state,
            report=record.report,
            reliability=reliability,
            focus_dimension=payload.focus_dimension,
            session_question_ids=payload.session_question_ids,
        )
    except PracticePlanError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        ) from exc


def _active_report_view(artifact_store, session_id: str):
    try:
        head = artifact_store.get_head(session_id)
    except ReportArtifactNotFound:
        head = None
    if hasattr(artifact_store, "get_latest_job"):
        latest_job = artifact_store.get_latest_job(session_id)
    else:
        jobs = artifact_store.list_jobs(session_id)
        latest_job = jobs[-1] if jobs else None
    if head is None or head.active_report_id is None:
        return None, latest_job
    try:
        return artifact_store.get_artifact(head.active_report_id), latest_job
    except ReportArtifactNotFound as exc:
        raise HTTPException(
            status_code=500,
            detail="active report pointer is invalid",
        ) from exc


def _optional_report_artifact_store(request: Request):
    override = request.app.dependency_overrides.get(get_report_artifact_store)
    try:
        return override() if override is not None else get_report_artifact_store()
    except PostgresSchemaNotReady:
        # Legacy compatibility endpoints remain readable before the additive
        # artifact migration. Version-specific endpoints still fail closed.
        return None


def _report_artifact_to_dict(
    artifact,
    *,
    active: bool = False,
    latest_job=None,
) -> dict:
    result = compose_report_view(
        artifact,
        latest_job=latest_job,
        active=active,
    ).model_dump(mode="json")
    if result.get("latest_job") is not None:
        result["latest_job"]["error_code"] = _public_artifact_error_code(
            result["latest_job"].get("error_code")
        )
    return result


def _report_job_v2_to_dict(job) -> dict | None:
    if job is None:
        return None
    result = job.model_dump(mode="json")
    result["error_code"] = _public_artifact_error_code(job.error_code)
    result["internal_status"] = job.status
    result["status"] = "running" if job.status == "retrying" else job.status
    result["retrying"] = job.status == "retrying"
    return result


def _public_artifact_error_code(raw_code: object) -> str | None:
    code = str(raw_code or "").strip().casefold()
    if not code:
        return None
    return {
        "provider_timeout": "report_provider_timeout",
        "report_provider_timeout": "report_provider_timeout",
        "provider_unavailable": "provider_unavailable",
        "embedding_provider_timeout": "embedding_provider_timeout",
        "knowledge_store_unavailable": "knowledge_store_unavailable",
        "report_enqueue_unavailable": "report_enqueue_unavailable",
        "report_job_missing": "report_job_missing",
    }.get(code, "report_generation_failed")


@router.get("/interviews/{session_id}/report.pdf")
def download_interview_report_pdf(
    session_id: str,
    request: Request,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=409, detail="interview is not finished")

    artifact_store = _optional_report_artifact_store(request)
    if (
        artifact_store is not None
        and get_report_artifact_read_mode() == "artifact_first"
    ):
        active_artifact, _ = _active_report_view(artifact_store, session_id)
        if active_artifact is not None:
            return _report_artifact_pdf_response(active_artifact)

    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        raise HTTPException(status_code=409, detail="report is not ready")
    if record.status == "failed":
        raise HTTPException(
            status_code=409,
            detail=_public_report_failure_message(
                _public_report_error_code(record.error or "")
            ),
        )

    pdf_bytes = build_report_pdf(record.report)
    filename = f'interview-report-{session_id}.pdf'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/interviews/{session_id}/reports")
def list_interview_report_artifacts(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
    artifact_store=Depends(get_report_artifact_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    head = artifact_store.get_head(session_id)
    artifacts = artifact_store.list_artifacts(session_id)
    active_id = head.active_report_id
    return {
        "items": [
            _report_artifact_to_dict(item, active=item.report_id == active_id)
            for item in artifacts
        ],
        "active_report_id": active_id,
    }


@router.get("/interviews/{session_id}/report-jobs")
def list_interview_report_jobs(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
    artifact_store=Depends(get_report_artifact_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "items": [_report_job_v2_to_dict(item) for item in artifact_store.list_jobs(session_id)]
    }


@router.get("/reports/{report_id}.pdf")
def download_report_artifact_pdf(
    report_id: str,
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
    artifact_store=Depends(get_report_artifact_store),
):
    artifact = _session_bound_report_artifact(
        session_id=session_id,
        report_id=report_id,
        store=store,
        artifact_store=artifact_store,
    )
    return _report_artifact_pdf_response(artifact)


def _report_artifact_pdf_response(artifact):
    from app.services.report import InterviewReport

    try:
        report = InterviewReport.model_validate(artifact.payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="report artifact is not renderable as the legacy PDF schema",
        ) from exc
    pdf_bytes = build_report_pdf(
        report,
        report_id=artifact.report_id,
        revision=artifact.revision,
        created_at=(
            artifact.created_at.isoformat()
            if artifact.created_at is not None
            else None
        ),
    )
    filename = f"interview-report-r{artifact.revision}-{artifact.report_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/{report_id}")
def get_report_artifact(
    report_id: str,
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
    artifact_store=Depends(get_report_artifact_store),
):
    artifact = _session_bound_report_artifact(
        session_id=session_id,
        report_id=report_id,
        store=store,
        artifact_store=artifact_store,
    )
    return _report_artifact_to_dict(artifact)


def _session_bound_report_artifact(
    *,
    session_id: str,
    report_id: str,
    store,
    artifact_store,
):
    try:
        state = store.get(session_id)
        if state.get("deletion_status") == "deleting":
            raise ValueError("session is deleting")
        artifact = artifact_store.get_artifact(report_id)
    except (ValueError, ReportArtifactNotFound) as exc:
        raise HTTPException(
            status_code=404,
            detail="report artifact not found for session",
        ) from exc
    if artifact.session_id != session_id:
        raise HTTPException(
            status_code=404,
            detail="report artifact not found for session",
        )
    return artifact


@router.post("/interviews/{session_id}/report/rescore", status_code=202)
def rescore_interview_report(
    session_id: str,
    body: RescoreReportRequest | None = None,
    store: InterviewSessionStore = Depends(get_session_store),
    artifact_store=Depends(get_report_artifact_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="interview session not found") from exc
    request_body = body or RescoreReportRequest()
    key = request_body.idempotency_key or f"rescore:{uuid4()}"
    if request_body.idempotency_key is not None:
        existing = artifact_store.get_job_by_idempotency_key(session_id, key)
        if existing is not None:
            if (
                existing.job_kind != "rescore"
                or existing.activate_on_success
                is not request_body.activate_on_success
            ):
                raise HTTPException(
                    status_code=409,
                    detail="idempotency key payload conflicts",
                )
            return {
                "session_id": session_id,
                "report_job_id": existing.job_id,
                "status": existing.status,
                "job_kind": existing.job_kind,
                "source_report_id": existing.source_report_id,
                "active_report_id": existing.source_report_id,
            }
    active_artifact, _ = _active_report_view(artifact_store, session_id)
    if active_artifact is None:
        raise HTTPException(status_code=409, detail="an active report is required before rescoring")
    try:
        job = artifact_store.enqueue_job(
            session_id=session_id,
            job_kind="rescore",
            source_report_id=active_artifact.report_id,
            activate_on_success=request_body.activate_on_success,
            idempotency_key=key,
        )
    except ReportArtifactConflict as exc:
        existing = (
            artifact_store.get_job_by_idempotency_key(session_id, key)
            if request_body.idempotency_key is not None
            else None
        )
        if (
            existing is None
            or existing.job_kind != "rescore"
            or existing.activate_on_success
            is not request_body.activate_on_success
        ):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        job = existing
    return {
        "session_id": session_id,
        "report_job_id": job.job_id,
        "status": job.status,
        "job_kind": job.job_kind,
        "source_report_id": job.source_report_id,
        "active_report_id": job.source_report_id,
    }


@router.get("/interviews/{session_id}/report/progress")
def get_interview_report_progress(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=404, detail="interview is not finished")

    record = store.get_report_record(session_id)
    job = _report_job_for_session(session_id)
    detail = _report_progress_detail(
        session_id,
        record,
        job=job,
    )
    if job and job.get("review_engine") == "langgraph-review-v1":
        completed = len(
            [
                item
                for item in store.list_question_evaluations(session_id)
                if item.status == "completed"
            ]
        )
        detail.update(
            {
                "workflow_engine": "langgraph-review-v1",
                "workflow_status": {
                    "queued": "queued",
                    "running": "running",
                    "retrying": "waiting_for_retry",
                    "completed": "completed",
                    "failed": "failed",
                }.get(job.get("status"), "processing"),
                "completed_question_count": completed,
                "total_question_count": len(state["plan"].questions),
                "retrying": job.get("status") == "retrying",
            }
        )
    return detail


@router.post(
    "/interviews/{session_id}/report/requeue",
    status_code=202,
)
def requeue_failed_report(
    session_id: str,
    request: Request,
    store: InterviewSessionStore = Depends(get_session_store),
    queue=Depends(get_report_job_queue),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail="interview session not found",
        ) from exc

    artifact_store = _optional_report_artifact_store(request)
    artifact_jobs = artifact_store.list_jobs(session_id) if artifact_store is not None else []
    artifact_job = artifact_jobs[-1] if artifact_jobs else None
    if artifact_job is not None:
        if artifact_job.status in {"queued", "retrying", "running"}:
            raise HTTPException(
                status_code=409,
                detail="report job is already queued or processing",
            )
        if artifact_job.status == "completed":
            raise HTTPException(
                status_code=409,
                detail="completed report cannot be requeued",
            )
        try:
            requeued = artifact_store.requeue_failed(artifact_job.job_id)
        except ReportArtifactConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        head = artifact_store.get_head(session_id)
        return {
            "session_id": session_id,
            "report_job_id": requeued.job_id,
            "status": requeued.status,
            "job_kind": requeued.job_kind,
            "active_report_id": head.active_report_id,
            "recovered_from": "failed",
            "report_progress_url": f"/api/interviews/{session_id}/report/progress",
        }

    job = queue.get_job_by_session(session_id)
    if job is None:
        record = store.get_report_record(session_id)
        detail = _report_progress_detail(session_id, record, job=None)
        if detail["status"] != "orphaned":
            raise HTTPException(status_code=404, detail="report job not found")
        try:
            created = queue.enqueue_report_request(session_id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="report queue is unavailable",
            ) from exc
        return {
            "session_id": session_id,
            "report_job_id": str(created["job_id"]),
            "status": "queued",
            "attempt": int(created.get("attempt_count") or 0),
            "recovered_from": "orphaned",
            "report_progress_url": f"/api/interviews/{session_id}/report/progress",
        }

    status = job.get("status")
    if status in {"queued", "retrying", "running"}:
        raise HTTPException(
            status_code=409,
            detail="report job is already queued or processing",
        )
    if status == "completed":
        raise HTTPException(
            status_code=409,
            detail="completed report cannot be requeued",
        )
    if status != "failed":
        raise HTTPException(status_code=409, detail="report job is not failed")

    try:
        store.requeue_report(session_id)
        requeued = queue.requeue_failed(session_id)
    except ValueError as exc:
        store.fail_report(session_id, "report requeue failed")
        raise HTTPException(
            status_code=409,
            detail="report job is not failed",
        ) from exc

    return {
        "session_id": session_id,
        "report_job_id": str(requeued["job_id"]),
        "status": "queued",
        "attempt": int(requeued.get("attempt_count") or 0),
        "recovered_from": "failed",
        "report_progress_url": f"/api/interviews/{session_id}/report/progress",
    }


@router.get("/interviews/{session_id}/question-evaluations")
def get_interview_question_evaluations(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
        records = store.list_question_evaluations(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    return {
        "session_id": session_id,
        "items": [
            record.model_dump(
                exclude={
                    "evidence_content_sha256",
                    "review_input_sha256",
                    "question_input_sha256",
                    "review_engine",
                    "review_graph_schema_version",
                    "output_sha256",
                }
            )
            for record in records
        ],
        "total": len(records),
    }


def _turn_to_dict(turn):
    return {
        "session_id": turn.session_id,
        "current_question": turn.current_question.model_dump()
        if turn.current_question
        else None,
        "follow_up": turn.follow_up,
        "status": turn.status,
    }


def _report_job_id_for_session(session_id: str) -> str | None:
    job = _report_job_for_session(session_id)
    return job.get("job_id") if job else None


def _report_job_for_session(session_id: str) -> dict | None:
    try:
        job = get_report_job_store().get_job_by_session(session_id)
    except (AttributeError, RuntimeError):
        return None
    if not job:
        return None
    return job


_PUBLIC_REPORT_PATHS = {
    "microbatch",
    "full_session",
    "full_session_fallback",
}


def _public_report_path(record) -> str | None:
    metadata = record.progress.metadata if record.progress is not None else {}
    value = metadata.get("report_path")
    return value if value in _PUBLIC_REPORT_PATHS else None


def _duration_seconds(summary: dict) -> int | None:
    started_at = summary.get("started_at")
    finished_at = summary.get("finished_at")
    if not started_at or not finished_at:
        return None
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return max(0, int((finished - started).total_seconds()))


def _report_summary_to_dict(
    session_id: str,
    record,
    *,
    session_summary: dict,
) -> dict:
    report = record.report
    return {
        "session_id": session_id,
        "status": record.status,
        "created_at": record.created_at,
        "finished_at": record.finished_at,
        "overall_score": report.overall_score if report is not None else None,
        "summary": report.summary if report is not None else None,
        "is_fallback": report.is_fallback if report is not None else False,
        "error": (
            _public_report_failure_message(
                _public_report_error_code(record.error or "")
            )
            if record.status == "failed"
            else None
        ),
        "job_title": session_summary.get("job_title"),
        "job_tags": list(session_summary.get("job_tags") or []),
        "question_count": session_summary.get("question_count"),
        "answered_question_count": session_summary.get(
            "answered_question_count", 0
        ),
        "started_at": session_summary.get("started_at"),
        "duration_seconds": _duration_seconds(session_summary),
        "report_path": _public_report_path(record),
        "report_url": f"/api/interviews/{session_id}/report",
        "report_pdf_url": f"/api/interviews/{session_id}/report.pdf"
        if record.status == "completed"
        else None,
    }


_PUBLIC_REPORT_KNOWLEDGE_PATHS = frozenset(
    {
        "not_recorded",
        "bound_evidence_reuse",
        "degraded",
        "legacy_semantic_search",
        "mixed",
    }
)
_PUBLIC_REPORT_PROGRESS_COUNT_KEYS = frozenset(
    {
        "microbatch_total_questions",
        "microbatch_reused_questions",
        "microbatch_rerun_questions",
        "microbatch_failed_questions",
    }
)


def _public_report_progress_metadata(metadata: object) -> dict:
    if not isinstance(metadata, dict):
        return {}
    result = {}
    report_path = metadata.get("report_path")
    if report_path in _PUBLIC_REPORT_PATHS:
        result["report_path"] = report_path
    knowledge_path = metadata.get("knowledge_path")
    if knowledge_path in _PUBLIC_REPORT_KNOWLEDGE_PATHS:
        result["knowledge_path"] = knowledge_path
    for key in _PUBLIC_REPORT_PROGRESS_COUNT_KEYS:
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    return result


def _report_progress_detail(session_id: str, record, *, job: dict | None):
    report_job_id = job.get("job_id") if job else None
    job_health = _report_job_health(job, record)
    if record is None:
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "processing",
            "stage": "queued",
            "percent": 0,
            "message": "Waiting for report generation to start.",
            "events": [],
            "rag": _rag_progress_defaults(),
            "metadata": {},
            **job_health,
        }

    if record.status == "completed":
        metadata = _public_report_progress_metadata(
            record.progress.metadata if record.progress is not None else {}
        )
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "completed",
            "stage": "completed",
            "percent": 100,
            "message": "Report completed.",
            "events": [],
            "rag": _rag_progress_defaults(),
            "metadata": metadata,
            **job_health,
        }

    if record.status == "failed":
        internal_message = record.error or ""
        metadata = _public_report_progress_metadata(
            record.progress.metadata if record.progress is not None else {}
        )
        error_code = _coerce_public_report_error_code(
            job.get("last_error_code") if job else None,
            internal_message,
        )
        message = _public_report_failure_message(error_code)
        retryable = _report_error_retryable(error_code)
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "failed",
            "stage": "failed",
            "percent": 100,
            "message": message,
            "events": [],
            "rag": _rag_progress_defaults(),
            "metadata": metadata,
            **job_health,
            "retryable": retryable,
            "error": {
                "code": error_code,
                "message": message,
                "retryable": retryable,
            },
        }

    progress = record.progress
    if progress is None:
        stage = "retrieving"
        percent = 0
        message = "Report generation is processing."
        current_question_id = None
    else:
        stage = progress.stage
        percent = progress.percent
        message = progress.message
        current_question_id = progress.current_question_id
    metadata = _public_report_progress_metadata(
        progress.metadata if progress is not None else {}
    )

    if job_health["orphaned"]:
        return {
            "session_id": session_id,
            "report_job_id": None,
            "status": "orphaned",
            "stage": "orphaned",
            "percent": percent,
            "message": "Report task lost its execution owner.",
            "current_question_id": current_question_id,
            "events": [],
            "rag": _rag_progress_defaults(),
            "metadata": metadata,
            **job_health,
            "retryable": True,
            "error": {
                "code": "report_job_missing",
                "message": "Report task lost its execution owner.",
                "retryable": True,
            },
        }

    return {
        "session_id": session_id,
        "report_job_id": report_job_id,
        "status": "processing",
        "stage": stage,
        "percent": percent,
        "message": message,
        "current_question_id": current_question_id,
        "events": [],
        "rag": _rag_progress_defaults(),
        "metadata": metadata,
        "error": None,
        **job_health,
    }


def _report_job_health(job: dict | None, record) -> dict:
    threshold = max(1, int(os.getenv("REPORT_JOB_STALL_SECONDS", "90")))
    now = datetime.now(timezone.utc)
    heartbeat = job.get("heartbeat_at") if job else None
    updated = job.get("updated_at") if job else getattr(record, "created_at", None)
    started = job.get("started_at") if job else None
    lease_expires = job.get("lease_expires_at") if job else None
    activity = heartbeat or updated
    activity_at = _coerce_datetime(activity)
    lease_expires_at = _coerce_datetime(lease_expires)
    age_stalled = bool(
        activity_at is not None and (now - activity_at).total_seconds() > threshold
    )
    lease_stalled = bool(
        job
        and job.get("status") == "running"
        and lease_expires_at is not None
        and lease_expires_at <= now
    )
    orphaned = bool(
        job is None
        and record is not None
        and record.status == "processing"
        and age_stalled
    )
    stalled = orphaned or lease_stalled or bool(
        job and job.get("status") in {"queued", "running", "retrying"} and age_stalled
    )
    return {
        "attempt": int(job.get("attempt_count") or 0) if job else 0,
        "max_attempts": int(job.get("max_attempts") or 0) if job else 0,
        "started_at": _public_datetime(started),
        "last_updated_at": _public_datetime(updated),
        "heartbeat_at": _public_datetime(heartbeat),
        "stalled": stalled,
        "orphaned": orphaned,
        "retryable": bool(job and job.get("status") == "failed"),
    }


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _public_datetime(value) -> str | None:
    parsed = _coerce_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed is not None else None


def _public_report_error_code(message: str) -> str:
    normalized = " ".join(message.casefold().split())
    if normalized in {
        "report queue unavailable",
        "report enqueue unavailable",
    }:
        return "report_enqueue_unavailable"
    if "embedding provider is disabled" in normalized:
        return "embedding_provider_disabled"
    if "pgvector" in normalized or "knowledge store" in normalized:
        return "knowledge_store_unavailable"
    if "timeout" in normalized or "timed out" in normalized:
        return "report_provider_timeout"
    return "report_generation_failed"


_PUBLIC_REPORT_ERROR_CODES = frozenset(
    {
        "report_enqueue_unavailable",
        "embedding_provider_disabled",
        "embedding_provider_timeout",
        "knowledge_store_unavailable",
        "report_provider_timeout",
        "provider_unavailable",
        "report_job_missing",
        "report_generation_failed",
    }
)


def _coerce_public_report_error_code(
    raw_code: object,
    internal_message: str,
) -> str:
    code = str(raw_code or "").strip().casefold()
    if code in _PUBLIC_REPORT_ERROR_CODES:
        return code
    return _public_report_error_code(internal_message)


def _public_report_failure_message(error_code: str) -> str:
    return {
        "report_enqueue_unavailable": "Report queue is unavailable.",
        "embedding_provider_disabled": "Report knowledge retrieval is unavailable.",
        "embedding_provider_timeout": "Report knowledge retrieval timed out.",
        "knowledge_store_unavailable": "Report knowledge retrieval is unavailable.",
        "report_provider_timeout": "Report generation timed out.",
        "provider_unavailable": "Report provider is unavailable.",
        "report_job_missing": "Report task lost its execution owner.",
    }.get(error_code, "Report generation failed.")


def _report_error_retryable(error_code: str) -> bool:
    return error_code in {
        "report_enqueue_unavailable",
        "embedding_provider_timeout",
        "knowledge_store_unavailable",
        "report_provider_timeout",
        "provider_unavailable",
        "report_job_missing",
    }


def _rag_progress_defaults() -> dict:
    return {
        "top_k": 5,
        "source_types": ["theory", "expert_benchmark"],
        "matched_chunks": None,
    }


def _extract_follow_up(state) -> str | None:
    decision = state["decision"]
    if decision and decision["action"] == "follow_up":
        return state["pending_output"]
    if state["status"] == "finished":
        return state["pending_output"]
    return None


def _snapshot_session_state(
    store: InterviewSessionStore,
    session_id: str,
):
    return deepcopy(store.get(session_id))


def _publish_round_closed_event(
    publisher,
    store,
    before_state,
    after_state,
) -> None:
    if (
        getattr(store, "runtime_event_delivery", "direct")
        == "transactional_outbox"
    ):
        return
    event = round_closed_event_from_transition(before_state, after_state)
    if event is not None:
        try:
            publisher.publish(event)
        except Exception as exc:
            logger.warning(
                "round_closed event publish failed",
                extra={
                    "session_id": event.session_id,
                    "question_id": event.question_id,
                    "event_backend": get_runtime_event_backend(),
                    "error_code": type(exc).__name__,
                },
            )


def _raise_value_error(exc: ValueError) -> None:
    detail = str(exc)
    status_code = 404 if detail == "session not found" else 400
    raise HTTPException(status_code=status_code, detail=detail)


def _version_conflict_response(exc: SessionVersionConflict) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "session version conflict",
            "expected_version": exc.expected_version,
            "actual_version": exc.actual_version,
        },
    )
