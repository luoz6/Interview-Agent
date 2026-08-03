from dataclasses import dataclass
import os
import re

from app.services.postgres_identifiers import (
    validate_postgres_identifier,
    validate_runtime_table_prefix,
)


DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/interview"
DEFAULT_RUNTIME_STORE = "postgres"
DEFAULT_RUNTIME_TABLE_PREFIX = "interview"
DEFAULT_PGVECTOR_TABLE = "knowledge_chunks"
DEFAULT_RUNTIME_EVENT_BACKEND = "local"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"

_PG_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EmbeddingSettings:
    provider_name: str
    api_base: str
    model_name: str
    model_revision: str
    dimension: int
    batch_size: int
    connect_timeout_seconds: float
    read_timeout_seconds: float


@dataclass(frozen=True)
class ReportRuntimeProfile:
    name: str
    runtime_store: str
    report_job_store: str
    report_worker: str
    knowledge_store: str
    embedding_provider: str
    preview: bool
    configuration_valid: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class PostgresPoolSettings:
    business_min_size: int
    business_max_size: int
    business_acquire_timeout_seconds: float
    telemetry_min_size: int
    telemetry_max_size: int
    telemetry_acquire_timeout_seconds: float
    lock_min_size: int
    lock_max_size: int
    lock_acquire_timeout_seconds: float
    checkpointer_min_size: int
    checkpointer_max_size: int
    checkpointer_acquire_timeout_seconds: float
    checkpointer_overhead: int
    connect_timeout_seconds: int
    drain_timeout_seconds: float
    max_lifetime_seconds: float
    max_idle_seconds: float


@dataclass(frozen=True)
class PostgresCapacitySettings:
    expected_api_processes: int
    expected_celery_processes: int
    expected_outbox_processes: int
    external_connection_reserve: int
    max_utilization: float


def get_postgres_dsn() -> str:
    return os.getenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN).strip() or DEFAULT_POSTGRES_DSN


def get_runtime_store() -> str:
    return os.getenv("INTERVIEW_RUNTIME_STORE", DEFAULT_RUNTIME_STORE).strip().lower() or DEFAULT_RUNTIME_STORE


def get_report_runtime_profile() -> ReportRuntimeProfile:
    runtime_store = get_runtime_store()
    explicit_name = os.getenv("REPORT_RUNTIME_PROFILE", "").strip().lower()
    name = explicit_name or ("preview" if runtime_store == "memory" else "durable")
    if name not in {"preview", "durable"}:
        raise ValueError("REPORT_RUNTIME_PROFILE must be preview or durable")

    preview = name == "preview"
    report_job_store = (
        os.getenv("REPORT_JOB_STORE", "memory" if preview else "postgres")
        .strip()
        .lower()
    )
    report_worker = (
        os.getenv("REPORT_WORKER", "in_process" if preview else "external_process")
        .strip()
        .lower()
    )
    knowledge_store = (
        os.getenv("KNOWLEDGE_STORE", "static" if preview else "pgvector")
        .strip()
        .lower()
    )
    embedding_provider = get_embedding_settings().provider_name

    errors: list[str] = []
    if runtime_store not in {"memory", "postgres"}:
        errors.append("unsupported_runtime_store")
    if report_job_store not in {"memory", "postgres"}:
        errors.append("unsupported_report_job_store")
    if report_worker not in {"in_process", "external_process"}:
        errors.append("unsupported_report_worker")
    if knowledge_store not in {"static", "pgvector"}:
        errors.append("unsupported_knowledge_store")

    if preview:
        if runtime_store != "memory":
            errors.append("preview_requires_memory_session_store")
        if report_job_store != "memory":
            errors.append("preview_requires_memory_report_jobs")
        if report_worker != "in_process":
            errors.append("preview_requires_in_process_worker")
        if knowledge_store != "static":
            errors.append("preview_requires_static_knowledge")
    else:
        if runtime_store != "postgres":
            errors.append("durable_requires_postgres_session_store")
        if report_job_store != "postgres":
            errors.append("durable_requires_postgres_report_jobs")
        if report_worker != "external_process":
            errors.append("durable_requires_external_worker")
        if knowledge_store != "pgvector":
            errors.append("durable_requires_pgvector")
        if embedding_provider == "disabled":
            errors.append("pgvector_requires_embedding_provider")

    return ReportRuntimeProfile(
        name=name,
        runtime_store=runtime_store,
        report_job_store=report_job_store,
        report_worker=report_worker,
        knowledge_store=knowledge_store,
        embedding_provider=embedding_provider,
        preview=preview,
        configuration_valid=not errors,
        errors=tuple(dict.fromkeys(errors)),
    )


def get_runtime_table_prefix() -> str:
    prefix = os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX") or os.getenv("INTERVIEW_TABLE_PREFIX")
    resolved = prefix.strip() if prefix and prefix.strip() else DEFAULT_RUNTIME_TABLE_PREFIX
    return validate_runtime_table_prefix(resolved)


def get_pgvector_table() -> str:
    base = os.getenv("PGVECTOR_TABLE", DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
    derive_pgvector_table_names(base)
    return base


def derive_pgvector_table_names(base: str) -> tuple[str, str]:
    versions = f"{base}_versions"
    releases = f"{base}_releases"
    try:
        validate_postgres_identifier(base)
        validate_postgres_identifier(versions)
        validate_postgres_identifier(releases)
    except ValueError as exc:
        raise ValueError(
            "PGVECTOR_TABLE must produce safe identifiers within 63 bytes"
        ) from exc
    return versions, releases


def get_embedding_settings() -> EmbeddingSettings:
    provider = os.getenv("EMBEDDING_PROVIDER", "disabled").strip().lower() or "disabled"
    if provider not in {"disabled", "siliconflow"}:
        raise ValueError("EMBEDDING_PROVIDER must be disabled or siliconflow")
    return EmbeddingSettings(
        provider_name=provider,
        api_base=os.getenv(
            "EMBEDDING_API_BASE", "https://api.siliconflow.cn/v1"
        ).strip().rstrip("/"),
        model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3").strip(),
        model_revision=os.getenv(
            "EMBEDDING_MODEL_REVISION", "siliconflow-current"
        ).strip(),
        dimension=_positive_int("EMBEDDING_DIMENSION", 1024),
        batch_size=_positive_int("EMBEDDING_BATCH_SIZE", 32),
        connect_timeout_seconds=_positive_float(
            "EMBEDDING_CONNECT_TIMEOUT_SECONDS", 5.0
        ),
        read_timeout_seconds=_positive_float(
            "EMBEDDING_READ_TIMEOUT_SECONDS", 30.0
        ),
    )


def get_postgres_pool_settings() -> PostgresPoolSettings:
    settings = PostgresPoolSettings(
        business_min_size=_non_negative_int("POSTGRES_BUSINESS_POOL_MIN_SIZE", 1),
        business_max_size=_positive_int("POSTGRES_BUSINESS_POOL_MAX_SIZE", 12),
        business_acquire_timeout_seconds=_positive_float(
            "POSTGRES_BUSINESS_POOL_ACQUIRE_TIMEOUT_SECONDS", 2.0
        ),
        telemetry_min_size=_non_negative_int("POSTGRES_TELEMETRY_POOL_MIN_SIZE", 1),
        telemetry_max_size=_positive_int("POSTGRES_TELEMETRY_POOL_MAX_SIZE", 4),
        telemetry_acquire_timeout_seconds=_positive_float(
            "POSTGRES_TELEMETRY_POOL_ACQUIRE_TIMEOUT_SECONDS", 1.0
        ),
        lock_min_size=_non_negative_int("POSTGRES_LOCK_POOL_MIN_SIZE", 1),
        lock_max_size=_positive_int("POSTGRES_LOCK_POOL_MAX_SIZE", 4),
        lock_acquire_timeout_seconds=_positive_float(
            "POSTGRES_LOCK_POOL_ACQUIRE_TIMEOUT_SECONDS", 2.0
        ),
        checkpointer_min_size=_non_negative_int(
            "POSTGRES_CHECKPOINTER_POOL_MIN_SIZE", 1
        ),
        checkpointer_max_size=_positive_int(
            "POSTGRES_CHECKPOINTER_POOL_MAX_SIZE", 2
        ),
        checkpointer_acquire_timeout_seconds=_positive_float(
            "POSTGRES_CHECKPOINTER_POOL_ACQUIRE_TIMEOUT_SECONDS", 2.0
        ),
        checkpointer_overhead=_non_negative_int(
            "POSTGRES_CHECKPOINTER_POOL_OVERHEAD", 1
        ),
        connect_timeout_seconds=_positive_int("POSTGRES_CONNECT_TIMEOUT_SECONDS", 3),
        drain_timeout_seconds=_positive_float(
            "POSTGRES_POOL_DRAIN_TIMEOUT_SECONDS", 10.0
        ),
        max_lifetime_seconds=_positive_float(
            "POSTGRES_POOL_MAX_LIFETIME_SECONDS", 1800.0
        ),
        max_idle_seconds=_positive_float("POSTGRES_POOL_MAX_IDLE_SECONDS", 300.0),
    )
    for domain, minimum, maximum in (
        ("business", settings.business_min_size, settings.business_max_size),
        ("telemetry", settings.telemetry_min_size, settings.telemetry_max_size),
        ("lock", settings.lock_min_size, settings.lock_max_size),
        (
            "checkpointer",
            settings.checkpointer_min_size,
            settings.checkpointer_max_size,
        ),
    ):
        if minimum > maximum:
            raise ValueError(f"POSTGRES_{domain.upper()} pool min exceeds max")
    return settings


def get_postgres_capacity_settings() -> PostgresCapacitySettings:
    maximum = float(os.getenv("POSTGRES_CAPACITY_MAX_UTILIZATION", "0.80"))
    if not 0 < maximum <= 1:
        raise ValueError("POSTGRES_CAPACITY_MAX_UTILIZATION must be in (0, 1]")
    return PostgresCapacitySettings(
        expected_api_processes=_non_negative_int(
            "POSTGRES_EXPECTED_API_PROCESSES", 1
        ),
        expected_celery_processes=_non_negative_int(
            "POSTGRES_EXPECTED_CELERY_PROCESSES", 1
        ),
        expected_outbox_processes=_non_negative_int(
            "POSTGRES_EXPECTED_OUTBOX_PROCESSES", 1
        ),
        external_connection_reserve=_non_negative_int(
            "POSTGRES_EXTERNAL_CONNECTION_RESERVE", 10
        ),
        max_utilization=maximum,
    )


def get_postgres_runtime_auto_migrate() -> bool:
    value = os.getenv("POSTGRES_RUNTIME_AUTO_MIGRATE", "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError("POSTGRES_RUNTIME_AUTO_MIGRATE must be true or false")
    return value == "true"


def get_runtime_event_backend() -> str:
    raw = os.getenv("INTERVIEW_EVENT_BACKEND", DEFAULT_RUNTIME_EVENT_BACKEND)
    return raw.strip().lower() or DEFAULT_RUNTIME_EVENT_BACKEND


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL).strip() or DEFAULT_REDIS_URL


def get_runtime_outbox_batch_size() -> int:
    return _positive_int("RUNTIME_OUTBOX_BATCH_SIZE", 20)


def get_runtime_outbox_lease_seconds() -> int:
    return _positive_int("RUNTIME_OUTBOX_LEASE_SECONDS", 60)


def get_runtime_outbox_poll_seconds() -> float:
    return _positive_float("RUNTIME_OUTBOX_POLL_SECONDS", 0.5)


def get_runtime_receipt_lease_seconds() -> int:
    return _positive_int("RUNTIME_RECEIPT_LEASE_SECONDS", 300)


def get_interview_chunk_retention_hours() -> int:
    return _positive_int("INTERVIEW_CHUNK_RETENTION_HOURS", 24)


def get_durable_workflow_maintenance_seconds() -> int:
    return _positive_int("DURABLE_WORKFLOW_MAINTENANCE_SECONDS", 3600)


def get_langgraph_canary_signal_retention_hours() -> int:
    return _positive_int("LANGGRAPH_CANARY_SIGNAL_RETENTION_HOURS", 168)


def get_interview_langgraph_rollout_percent() -> int:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().interview_graph.rollout_percent


def get_interview_langgraph_version() -> str:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().interview_graph.version


def get_interview_langgraph_runtime_enabled() -> bool:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().interview_graph.runtime_enabled


def get_report_langgraph_rollout_percent() -> int:
    raw = os.getenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", "0")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "REPORT_LANGGRAPH_ROLLOUT_PERCENT must be between 0 and 100"
        ) from exc
    if not 0 <= value <= 100:
        raise ValueError(
            "REPORT_LANGGRAPH_ROLLOUT_PERCENT must be between 0 and 100"
        )
    return value


def get_report_langgraph_version() -> str:
    value = os.getenv(
        "REPORT_LANGGRAPH_VERSION", "langgraph-review-v1"
    ).strip()
    if value != "langgraph-review-v1":
        raise ValueError("unsupported REPORT_LANGGRAPH_VERSION")
    return value


def get_report_langgraph_runtime_enabled() -> bool:
    value = os.getenv(
        "REPORT_LANGGRAPH_RUNTIME_ENABLED", "true"
    ).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(
            "REPORT_LANGGRAPH_RUNTIME_ENABLED must be true or false"
        )
    return value == "true"


def get_report_langgraph_max_parallel_question_reviews() -> int:
    return _positive_int("REPORT_LANGGRAPH_MAX_PARALLEL_QUESTION_REVIEWS", 3)


def get_report_langgraph_max_provider_attempts() -> int:
    return _positive_int("REPORT_LANGGRAPH_MAX_PROVIDER_ATTEMPTS", 3)


def get_report_langgraph_max_quality_repairs() -> int:
    return _positive_int("REPORT_LANGGRAPH_MAX_QUALITY_REPAIRS", 2)


def get_context_compression_shadow_enabled() -> bool:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().compression.mode == "shadow"


def get_context_compression_prep_enabled() -> bool:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().compression.prep


def get_context_compression_interview_enabled() -> bool:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().compression.interview_question_memory


def get_context_compression_evidence_enabled() -> bool:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().compression.evidence


def get_context_compression_review_enabled() -> bool:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().compression.review


def get_context_artifact_lease_seconds() -> int:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().artifact.lease_seconds


def get_context_artifact_unreferenced_retention_hours() -> int:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().retention.artifact_unreferenced_hours


def get_context_artifact_failed_retention_hours() -> int:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().retention.artifact_failed_hours


def get_context_artifact_prep_ref_retention_hours() -> int:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().retention.prep_ref_hours


def get_context_artifact_cleanup_batch_size() -> int:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().retention.cleanup_batch_size


def get_context_artifact_deployment_scope() -> str:
    from app.services.memory_config import load_effective_memory_config

    return load_effective_memory_config().privacy.deployment_id


def _strict_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return raw == "true"


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
