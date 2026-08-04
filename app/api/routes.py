import logging
import os
from ipaddress import ip_address
from collections.abc import Iterator
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.services.job_tags import extract_job_tags
from app.services.agent_runtime import correlation_id_from_plan
from app.services.prep import (
    prepare_interview,
    public_interview_plan_payload,
    validate_launchable_interview_plan,
)
from app.services.config import (
    get_report_runtime_profile,
    get_interview_langgraph_rollout_percent,
    get_runtime_event_backend,
    get_runtime_store,
)
from app.services.interview_rounds import round_closed_event_from_transition
from app.services.report_enqueue import enqueue_report_if_needed
from app.services.report_pdf import build_report_pdf
from app.services.runtime_events import (
    InterviewStreamChunkEvent,
    InterviewStreamDoneEvent,
    InterviewStreamErrorEvent,
)
from app.services.runtime import (
    get_agent_execution_runner,
    get_draft_store,
    get_event_publisher,
    get_report_job_store,
    get_runtime_control_store,
    get_session_store,
    get_interview_workflow_service,
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
from app.services.session_errors import SessionVersionConflict
from app.services.session import InterviewSessionStore
from app.graphs.interview_state import is_durable_interview_version
from app.services.memory_config import (
    load_effective_memory_config,
    memory_readiness_payload,
)


router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


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


class PrepRequest(BaseModel):
    job_description: str
    resume_text: str


class AnswerRequest(BaseModel):
    answer: str
    expected_version: int | None = None
    command_id: str | None = None


class SessionCommandRequest(BaseModel):
    expected_version: int | None = None
    command_id: str | None = None


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

    @field_validator("job_description", "resume_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


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
def prep_interview(payload: PrepRequest):
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
            execution_runner=get_agent_execution_runner(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    response = public_interview_plan_payload(plan)
    response["job_tags"] = extract_job_tags(payload.job_description)
    return response


@router.post("/interview-drafts")
def save_interview_draft(payload: DraftRequest, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.save(
            draft_id=payload.draft_id,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            title=payload.title,
            job_tags=payload.job_tags
            if payload.job_tags is not None
            else extract_job_tags(payload.job_description),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/interview-drafts/{draft_id}")
def get_interview_draft(draft_id: str, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.get(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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
    payload: PrepRequest,
    store: InterviewSessionStore = Depends(get_session_store),
):
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
        raise HTTPException(status_code=500, detail=record.error)
    return record.report.model_dump()


@router.get("/interviews/{session_id}/report.pdf")
def download_interview_report_pdf(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=409, detail="interview is not finished")

    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        raise HTTPException(status_code=409, detail="report is not ready")
    if record.status == "failed":
        raise HTTPException(status_code=409, detail=record.error)

    pdf_bytes = build_report_pdf(record.report)
    filename = f'interview-report-{session_id}.pdf'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        "error": record.error,
        "job_title": session_summary.get("job_title"),
        "job_tags": list(session_summary.get("job_tags") or []),
        "question_count": session_summary.get("question_count"),
        "started_at": session_summary.get("started_at"),
        "duration_seconds": _duration_seconds(session_summary),
        "report_path": _public_report_path(record),
        "report_url": f"/api/interviews/{session_id}/report",
        "report_pdf_url": f"/api/interviews/{session_id}/report.pdf"
        if record.status == "completed"
        else None,
    }


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
        metadata = record.progress.metadata if record.progress is not None else {}
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
        message = record.error or "Report generation failed."
        metadata = record.progress.metadata if record.progress is not None else {}
        error_code = (
            job.get("last_error_code") if job else None
        ) or _public_report_error_code(message)
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
    metadata = progress.metadata if progress is not None else {}

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
                },
                exc_info=exc,
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
