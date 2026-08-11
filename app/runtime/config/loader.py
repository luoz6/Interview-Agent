from __future__ import annotations

import math
from collections.abc import Mapping

from app.runtime.config.environment import process_environment, use_environment
from app.runtime.config.models import (
    ApiRuntimeSettings,
    CoreRuntimeSettings,
    EffectiveRuntimeConfig,
    KnowledgeRuntimeSettings,
    LLMRuntimeSettings,
    ProviderCredentialSettings,
    ReportGraphRuntimeSettings,
    TraceRuntimeSettings,
    WorkerRuntimeSettings,
)


DEFAULT_FRONTEND_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:4173",
    "http://localhost:4173",
)
DEFAULT_FRONTEND_URL = "http://127.0.0.1:5173"


def load_effective_runtime_config(
    environ: Mapping[str, str] | None = None,
) -> EffectiveRuntimeConfig:
    """Parse and validate the complete runtime configuration in one pass."""

    if environ is not None:
        with use_environment(environ):
            return _load_active_environment()
    return _load_active_environment()


def load_api_runtime_settings(
    environ: Mapping[str, str] | None = None,
) -> ApiRuntimeSettings:
    if environ is not None:
        with use_environment(environ):
            return _load_api_runtime_settings()
    return _load_api_runtime_settings()


def load_trace_runtime_settings(
    environ: Mapping[str, str] | None = None,
) -> TraceRuntimeSettings:
    if environ is not None:
        with use_environment(environ):
            return _load_trace_runtime_settings()
    return _load_trace_runtime_settings()


def load_provider_credentials(
    environ: Mapping[str, str] | None = None,
) -> ProviderCredentialSettings:
    if environ is not None:
        with use_environment(environ):
            return _load_provider_credentials()
    return _load_provider_credentials()


def load_llm_runtime_settings(
    environ: Mapping[str, str] | None = None,
) -> LLMRuntimeSettings:
    if environ is not None:
        with use_environment(environ):
            return _load_llm_runtime_settings()
    return _load_llm_runtime_settings()


def load_worker_runtime_settings(
    environ: Mapping[str, str] | None = None,
) -> WorkerRuntimeSettings:
    if environ is not None:
        with use_environment(environ):
            return _load_worker_runtime_settings()
    return _load_worker_runtime_settings()


def load_knowledge_runtime_settings(
    environ: Mapping[str, str] | None = None,
) -> KnowledgeRuntimeSettings:
    if environ is not None:
        with use_environment(environ):
            return _load_knowledge_runtime_settings()
    return _load_knowledge_runtime_settings()


def load_langgraph_strict_msgpack(
    environ: Mapping[str, str] | None = None,
) -> bool:
    if environ is not None:
        with use_environment(environ):
            return _load_langgraph_strict_msgpack()
    return _load_langgraph_strict_msgpack()


def _load_active_environment() -> EffectiveRuntimeConfig:
    from app.runtime.config.compatibility import (
        get_durable_workflow_maintenance_seconds,
        get_embedding_settings,
        get_interview_chunk_retention_hours,
        get_interview_draft_ttl_seconds,
        get_langgraph_canary_signal_retention_hours,
        get_pgvector_table,
        get_postgres_capacity_settings,
        get_postgres_dsn,
        get_postgres_pool_settings,
        get_postgres_runtime_auto_migrate,
        get_prep_plan_consumed_retention_seconds,
        get_prep_plan_expired_grace_seconds,
        get_prep_plan_ttl_seconds,
        get_redis_url,
        get_report_langgraph_max_parallel_question_reviews,
        get_report_langgraph_max_provider_attempts,
        get_report_langgraph_max_quality_repairs,
        get_report_langgraph_rollout_percent,
        get_report_langgraph_runtime_enabled,
        get_report_langgraph_version,
        get_report_runtime_profile,
        get_runtime_event_backend,
        get_runtime_outbox_batch_size,
        get_runtime_outbox_lease_seconds,
        get_runtime_outbox_poll_seconds,
        get_runtime_receipt_lease_seconds,
        get_runtime_store,
        get_runtime_table_prefix,
    )
    from app.runtime.config.memory import load_effective_memory_config

    env = process_environment()
    runtime_store = get_runtime_store()

    return EffectiveRuntimeConfig(
        schema_version="runtime-config-v1",
        core=CoreRuntimeSettings(
            postgres_dsn=get_postgres_dsn(required=runtime_store == "postgres"),
            runtime_store=runtime_store,
            runtime_table_prefix=get_runtime_table_prefix(),
            pgvector_table=get_pgvector_table(),
            runtime_event_backend=get_runtime_event_backend(),
            redis_url=get_redis_url(),
            postgres_runtime_auto_migrate=get_postgres_runtime_auto_migrate(),
            interview_draft_ttl_seconds=get_interview_draft_ttl_seconds(),
            prep_plan_ttl_seconds=get_prep_plan_ttl_seconds(),
            prep_plan_expired_grace_seconds=get_prep_plan_expired_grace_seconds(),
            prep_plan_consumed_retention_seconds=(
                get_prep_plan_consumed_retention_seconds()
            ),
            runtime_outbox_batch_size=get_runtime_outbox_batch_size(),
            runtime_outbox_lease_seconds=get_runtime_outbox_lease_seconds(),
            runtime_outbox_poll_seconds=get_runtime_outbox_poll_seconds(),
            runtime_receipt_lease_seconds=get_runtime_receipt_lease_seconds(),
            interview_chunk_retention_hours=get_interview_chunk_retention_hours(),
            durable_workflow_maintenance_seconds=(
                get_durable_workflow_maintenance_seconds()
            ),
            langgraph_canary_signal_retention_hours=(
                get_langgraph_canary_signal_retention_hours()
            ),
        ),
        embedding=get_embedding_settings(),
        postgres_pools=get_postgres_pool_settings(),
        postgres_capacity=get_postgres_capacity_settings(),
        report_profile=get_report_runtime_profile(),
        report_graph=ReportGraphRuntimeSettings(
            rollout_percent=get_report_langgraph_rollout_percent(),
            version=get_report_langgraph_version(),
            runtime_enabled=get_report_langgraph_runtime_enabled(),
            max_parallel_question_reviews=(
                get_report_langgraph_max_parallel_question_reviews()
            ),
            max_provider_attempts=get_report_langgraph_max_provider_attempts(),
            max_quality_repairs=get_report_langgraph_max_quality_repairs(),
        ),
        memory=load_effective_memory_config(env),
        api=_load_api_runtime_settings(),
        traces=_load_trace_runtime_settings(),
        credentials=_load_provider_credentials(),
        llm=_load_llm_runtime_settings(),
        worker=_load_worker_runtime_settings(),
        knowledge=_load_knowledge_runtime_settings(),
        langgraph_strict_msgpack=_load_langgraph_strict_msgpack(),
    )


def _load_api_runtime_settings() -> ApiRuntimeSettings:
    env = process_environment()
    origins = tuple(
        origin.strip()
        for origin in env.get(
            "FRONTEND_ORIGINS",
            ",".join(DEFAULT_FRONTEND_ORIGINS),
        ).split(",")
        if origin.strip()
    )
    if not origins:
        raise ValueError("FRONTEND_ORIGINS must contain at least one origin")
    return ApiRuntimeSettings(
        frontend_origins=origins,
        frontend_url=env.get("FRONTEND_URL", DEFAULT_FRONTEND_URL).strip()
        or DEFAULT_FRONTEND_URL,
        report_job_stall_seconds=_positive_int(
            env,
            "REPORT_JOB_STALL_SECONDS",
            90,
        ),
    )


def _load_trace_runtime_settings() -> TraceRuntimeSettings:
    env = process_environment()
    return TraceRuntimeSettings(
        agent_directory=_optional_text(env, "AGENT_TRACE_DIR"),
        knowledge_directory=_optional_text(env, "KNOWLEDGE_TRACE_DIR"),
        report_directory=_optional_text(env, "REPORT_TRACE_DIR"),
    )


def _load_provider_credentials() -> ProviderCredentialSettings:
    env = process_environment()
    return ProviderCredentialSettings(
        openai_api_key=_optional_text(env, "OPENAI_API_KEY"),
        siliconflow_api_key=_optional_text(env, "SILICONFLOW_API_KEY"),
    )


def _load_llm_runtime_settings() -> LLMRuntimeSettings:
    env = process_environment()
    output_mode = env.get("OPENAI_REPORT_OUTPUT_MODE", "structured_first").strip()
    if output_mode not in {"structured_first", "raw_only"}:
        raise ValueError(f"unsupported OPENAI_REPORT_OUTPUT_MODE: {output_mode}")
    return LLMRuntimeSettings(
        base_url=_optional_text(env, "OPENAI_BASE_URL"),
        temperature=_finite_float(env, "OPENAI_TEMPERATURE", 0.2),
        request_timeout_seconds=_positive_float(
            env,
            "OPENAI_REQUEST_TIMEOUT_SECONDS",
            120.0,
        ),
        max_retries=_non_negative_int(env, "OPENAI_MAX_RETRIES", 1),
        report_output_mode=output_mode,
    )


def _load_worker_runtime_settings() -> WorkerRuntimeSettings:
    env = process_environment()
    return WorkerRuntimeSettings(
        report_worker_id=_optional_text(env, "REPORT_WORKER_ID"),
        report_job_lease_seconds=_positive_int(
            env,
            "REPORT_JOB_LEASE_SECONDS",
            45,
        ),
        workflow_thread_lock_timeout_seconds=_positive_float(
            env,
            "WORKFLOW_THREAD_LOCK_TIMEOUT_SECONDS",
            1.0,
        ),
    )


def _load_knowledge_runtime_settings() -> KnowledgeRuntimeSettings:
    return KnowledgeRuntimeSettings(
        minimum_score=_finite_float(
            process_environment(),
            "KNOWLEDGE_MIN_SCORE",
            0.45,
        )
    )


def _load_langgraph_strict_msgpack() -> bool:
    return _strict_bool(
        process_environment(),
        "LANGGRAPH_STRICT_MSGPACK",
        True,
    )


def _optional_text(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _strict_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "true" if default else "false").strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return raw == "true"


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = _integer(env, name, default)
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_int(env: Mapping[str, str], name: str, default: int) -> int:
    value = _integer(env, name, default)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    value = _finite_float(env, name, default)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _finite_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(env.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value
