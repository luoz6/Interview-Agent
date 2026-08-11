from fastapi import APIRouter, HTTPException, Query

from app.api.shared import dependencies
from app.runtime.config import load_trace_runtime_settings
from app.runtime.config.compatibility import (
    get_report_runtime_profile,
    get_runtime_event_backend,
    get_runtime_store,
)
from app.runtime.config.memory import (
    load_effective_memory_config,
    memory_readiness_payload,
)


router = APIRouter()


def _require_trusted_local_metrics() -> None:
    if not load_effective_memory_config().privacy.trusted_local_metrics_enabled:
        raise HTTPException(status_code=404, detail="not found")


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
            "trace_enabled": bool(load_trace_runtime_settings().agent_directory),
            "outbox_enabled": runtime_store == "postgres",
            "agent_ledger_enabled": runtime_store == "postgres",
        },
        "memory_runtime": {
            **memory_readiness_payload(memory_config),
            "durable_metrics_available": bool(
                dependencies.get_memory_metric_store()
                .diagnostics()
                .get("data_complete")
            ),
        },
    }


@router.get("/runtime/memory-metrics")
def memory_metrics_boundary(
    window_minutes: int = Query(default=60),
):
    _require_trusted_local_metrics()
    try:
        return dependencies.get_memory_metric_store().aggregate(
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
            dependencies.get_memory_metric_store()
            .diagnostics()
            .get("data_complete")
        ),
        "question_memory_consumption_enabled": bool(
            config.compression.mode == "consume"
            and config.compression.interview_question_memory
        ),
        "long_term_consumption_available": False,
    }


__all__ = [
    "health",
    "memory_budget_shadow_boundary",
    "memory_metrics_boundary",
    "router",
    "runtime_boundary",
]
