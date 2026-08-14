from __future__ import annotations

import math
from collections.abc import Mapping

from app.runtime.config.environment import process_environment, use_environment
from app.runtime.config.models import (
    ApiRuntimeSettings,
    CoreRuntimeSettings,
    EffectiveRuntimeConfig,
    KnowledgeRuntimeSettings,
    RagConsoleRuntimeSettings,
    KnowledgeProfileBudget,
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


def load_rag_console_runtime_settings(
    environ: Mapping[str, str] | None = None,
) -> RagConsoleRuntimeSettings:
    if environ is not None:
        with use_environment(environ):
            return _load_rag_console_runtime_settings()
    return _load_rag_console_runtime_settings()


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
    env = process_environment()
    engine = env.get("KNOWLEDGE_ENGINE", "legacy").strip().lower()
    if engine not in {"legacy", "hybrid-v2"}:
        raise ValueError("KNOWLEDGE_ENGINE must be legacy or hybrid-v2")
    remote_reranker_enabled = _strict_bool(
        env, "KNOWLEDGE_REMOTE_RERANKER_ENABLED", False
    )
    if remote_reranker_enabled:
        raise ValueError(
            "knowledge remote reranker is blocked until the ranking-gap evidence gate passes"
        )
    component_versions = {
        "retrieval_engine_version": env.get(
            "KNOWLEDGE_RETRIEVAL_ENGINE_VERSION", "hybrid-v2"
        ).strip(),
        "fusion_version": env.get(
            "KNOWLEDGE_FUSION_VERSION", "weighted-rrf-v1"
        ).strip(),
        "reranker_version": env.get(
            "KNOWLEDGE_RERANKER_VERSION", "deterministic-v1"
        ).strip(),
        "evidence_gate_version": env.get(
            "KNOWLEDGE_EVIDENCE_GATE_VERSION", "retrieval-gate-v1"
        ).strip(),
        "taxonomy_version": env.get(
            "KNOWLEDGE_TAXONOMY_VERSION", "pilot-v1"
        ).strip(),
    }
    if any(not value for value in component_versions.values()):
        raise ValueError("knowledge component versions must not be blank")
    profile_values = {
        "profile_prep": env.get("KNOWLEDGE_PROFILE_PREP", "prep@hybrid-v1").strip(),
        "profile_followup": env.get(
            "KNOWLEDGE_PROFILE_FOLLOWUP", "followup@hybrid-v1"
        ).strip(),
        "profile_question_review": env.get(
            "KNOWLEDGE_PROFILE_QUESTION_REVIEW", "question-review@hybrid-v1"
        ).strip(),
        "profile_report_repair": env.get(
            "KNOWLEDGE_PROFILE_REPORT_REPAIR", "report-repair@hybrid-v1"
        ).strip(),
    }
    if any(
        "@" not in value
        or not value.rpartition("@")[0]
        or not value.rpartition("@")[2]
        for value in profile_values.values()
    ):
        raise ValueError("knowledge profiles must use <profile-id>@<version>")

    def profile_budget(prefix: str, defaults: tuple[int, int, int, int, int]):
        semantic, lexical, rerank, total, p95 = defaults
        budget = KnowledgeProfileBudget(
            semantic_timeout_ms=_positive_int(
                env, f"KNOWLEDGE_{prefix}_SEMANTIC_TIMEOUT_MS", semantic
            ),
            lexical_timeout_ms=_positive_int(
                env, f"KNOWLEDGE_{prefix}_LEXICAL_TIMEOUT_MS", lexical
            ),
            rerank_timeout_ms=_positive_int(
                env, f"KNOWLEDGE_{prefix}_RERANK_TIMEOUT_MS", rerank
            ),
            total_timeout_ms=_positive_int(
                env, f"KNOWLEDGE_{prefix}_TOTAL_TIMEOUT_MS", total
            ),
            absolute_p95_budget_ms=_positive_int(
                env, f"KNOWLEDGE_{prefix}_ABSOLUTE_P95_BUDGET_MS", p95
            ),
            max_relative_p95_multiplier=_positive_float(
                env, f"KNOWLEDGE_{prefix}_MAX_RELATIVE_P95_MULTIPLIER", 1.25
            ),
        )
        if max(
            budget.semantic_timeout_ms,
            budget.lexical_timeout_ms,
            budget.rerank_timeout_ms,
        ) > budget.total_timeout_ms:
            raise ValueError(
                f"KNOWLEDGE_{prefix} channel timeouts must not exceed total timeout"
            )
        if budget.total_timeout_ms > budget.absolute_p95_budget_ms:
            raise ValueError(
                f"KNOWLEDGE_{prefix} total timeout must not exceed absolute P95 budget"
            )
        if budget.max_relative_p95_multiplier < 1:
            raise ValueError(
                f"KNOWLEDGE_{prefix} relative P95 multiplier must be at least 1"
            )
        return budget

    return KnowledgeRuntimeSettings(
        minimum_score=_finite_float(
            env,
            "KNOWLEDGE_MIN_SCORE",
            0.45,
        ),
        engine=engine,
        semantic_enabled=_strict_bool(env, "KNOWLEDGE_SEMANTIC_ENABLED", True),
        lexical_enabled=_strict_bool(env, "KNOWLEDGE_LEXICAL_ENABLED", True),
        remote_reranker_enabled=remote_reranker_enabled,
        evidence_gate_enabled=_strict_bool(
            env, "KNOWLEDGE_EVIDENCE_GATE_ENABLED", True
        ),
        rrf_k=_positive_int(env, "KNOWLEDGE_RRF_K", 60),
        semantic_weight=_positive_float(env, "KNOWLEDGE_SEMANTIC_WEIGHT", 1.0),
        lexical_weight=_positive_float(env, "KNOWLEDGE_LEXICAL_WEIGHT", 1.0),
        prep_budget=profile_budget("PREP", (1200, 400, 300, 1500, 1500)),
        followup_budget=profile_budget("FOLLOWUP", (600, 250, 200, 800, 800)),
        question_review_budget=profile_budget(
            "QUESTION_REVIEW", (900, 350, 250, 1200, 1200)
        ),
        report_repair_budget=profile_budget(
            "REPORT_REPAIR", (900, 350, 250, 1200, 1200)
        ),
        **profile_values,
        **component_versions,
    )


def _load_rag_console_runtime_settings() -> RagConsoleRuntimeSettings:
    env = process_environment()
    access_mode = env.get("RAG_DIAGNOSTIC_ACCESS_MODE", "loopback").strip().lower()
    if access_mode != "loopback":
        raise ValueError(
            "RAG_DIAGNOSTIC_ACCESS_MODE must be loopback until authenticated "
            "diagnostic principals are implemented"
        )
    return RagConsoleRuntimeSettings(
        diagnostic_ui_enabled=_strict_bool(
            env, "RAG_DIAGNOSTIC_UI_ENABLED", False
        ),
        live_inspector_enabled=_strict_bool(
            env, "RAG_LIVE_INSPECTOR_ENABLED", False
        ),
        eval_artifact_access_enabled=_strict_bool(
            env, "RAG_EVAL_ARTIFACT_ACCESS_ENABLED", False
        ),
        authored_eval_query_access_enabled=_strict_bool(
            env, "RAG_EVAL_AUTHORED_QUERY_ACCESS_ENABLED", False
        ),
        corpus_write_enabled=_strict_bool(
            env, "RAG_CORPUS_WRITE_ENABLED", False
        ),
        access_mode=access_mode,
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


def _percent(env: Mapping[str, str], name: str, default: int) -> int:
    value = _integer(env, name, default)
    if value < 0 or value > 100:
        raise ValueError(f"{name} must be between 0 and 100")
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
