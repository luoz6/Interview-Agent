"""Stable FastAPI dependency entry points.

Routers import dependencies from this module instead of binding runtime stores or
workers at import time.  The exported callables remain stable FastAPI override
keys while the runtime container resolves the concrete instances on each call.
"""

from fastapi import Depends, HTTPException, Request

from app.application.interview.session_commands import (
    InterviewApplicationService,
    StreamingTurnService,
)
from app.application.interview.interview_start import InterviewStartService
from app.api.shared.models import StartInterviewRequest
from app.services.interview_launch import InterviewLaunchCoordinator
from app.runtime.config.compatibility import (
    get_interview_langgraph_rollout_percent,
    get_runtime_store,
)
from app.services.prep_question_regeneration import PrepQuestionRegenerator
from app.services.runtime import (
    get_agent_execution_runner,
    get_draft_store,
    get_event_publisher,
    get_interview_launch_coordinator,
    get_interview_launch_repository,
    get_interview_workflow_service,
    get_memory_metric_store,
    get_plan_revision_store,
    get_prep_plan_store,
    get_principal_identity_resolver,
    get_principal_memory_consent_store,
    get_principal_memory_control_store,
    get_principal_memory_deletion_tombstone_store,
    get_principal_memory_export_store,
    get_principal_memory_fact_store,
    get_principal_memory_safe_ref_store,
    get_question_memory_index_store,
    get_report_artifact_store,
    get_report_job_store,
    get_runtime_control_store,
    get_session_deletion_service,
    get_session_deletion_worker,
    get_session_store,
)


def get_report_job_queue():
    try:
        return get_report_job_store()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="report queue is unavailable",
        ) from exc


def get_prep_question_regenerator() -> PrepQuestionRegenerator:
    return PrepQuestionRegenerator()


def get_plan_regenerator():
    from app.services.interview_plan_regenerator import ProviderPlanRegenerator
    from app.services.prep import prepare_interview

    return ProviderPlanRegenerator(
        lambda job_description, resume_text, configuration: prepare_interview(
            job_description,
            resume_text,
            execution_runner=get_agent_execution_runner(),
            configuration=configuration,
            allow_fallback=False,
        )
    )


def get_interview_application_service(
    store=Depends(get_session_store),
    publisher=Depends(get_event_publisher),
) -> InterviewApplicationService:
    return InterviewApplicationService(
        store=store,
        workflow_service_factory=get_interview_workflow_service,
        publisher=publisher,
        report_job_store_factory=get_report_job_store,
    )


def get_streaming_turn_service(
    application=Depends(get_interview_application_service),
) -> StreamingTurnService:
    return StreamingTurnService(application)


def get_legacy_launch_session_store(
    request: Request,
    payload: StartInterviewRequest,
):
    if payload.plan_id is not None:
        return None
    override = request.app.dependency_overrides.get(get_session_store)
    return override() if override is not None else get_session_store()


def get_legacy_interview_start_service(
    store=Depends(get_legacy_launch_session_store),
) -> InterviewStartService | None:
    if store is None:
        return None
    return InterviewStartService(
        store=store,
        workflow_service_factory=get_interview_workflow_service,
        execution_runner_factory=get_agent_execution_runner,
        runtime_store_factory=get_runtime_store,
        rollout_percent_factory=get_interview_langgraph_rollout_percent,
    )


def get_request_interview_launch_coordinator(
    request: Request,
    payload: StartInterviewRequest,
):
    """Resolve a coordinator that honors request-app store overrides."""

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


def get_request_plan_revision_store(
    request: Request,
    payload: StartInterviewRequest,
):
    """Resolve revision storage only for revision-bound launch requests."""

    if payload.plan_revision_id is None:
        return None
    override = request.app.dependency_overrides.get(get_plan_revision_store)
    return override() if override is not None else get_plan_revision_store()


__all__ = [
    "get_agent_execution_runner",
    "get_draft_store",
    "get_event_publisher",
    "get_interview_launch_coordinator",
    "get_interview_launch_repository",
    "get_interview_workflow_service",
    "get_interview_application_service",
    "get_legacy_launch_session_store",
    "get_legacy_interview_start_service",
    "get_memory_metric_store",
    "get_plan_regenerator",
    "get_plan_revision_store",
    "get_prep_plan_store",
    "get_prep_question_regenerator",
    "get_principal_identity_resolver",
    "get_principal_memory_consent_store",
    "get_principal_memory_control_store",
    "get_principal_memory_deletion_tombstone_store",
    "get_principal_memory_export_store",
    "get_principal_memory_fact_store",
    "get_principal_memory_safe_ref_store",
    "get_question_memory_index_store",
    "get_report_artifact_store",
    "get_report_job_queue",
    "get_report_job_store",
    "get_request_interview_launch_coordinator",
    "get_request_plan_revision_store",
    "get_runtime_control_store",
    "get_session_deletion_service",
    "get_session_deletion_worker",
    "get_session_store",
    "get_streaming_turn_service",
]
