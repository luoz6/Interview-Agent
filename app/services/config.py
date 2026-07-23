from dataclasses import dataclass
import os
import re


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


def get_postgres_dsn() -> str:
    return os.getenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN).strip() or DEFAULT_POSTGRES_DSN


def get_runtime_store() -> str:
    return os.getenv("INTERVIEW_RUNTIME_STORE", DEFAULT_RUNTIME_STORE).strip().lower() or DEFAULT_RUNTIME_STORE


def get_runtime_table_prefix() -> str:
    prefix = os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX") or os.getenv("INTERVIEW_TABLE_PREFIX")
    return prefix.strip() if prefix and prefix.strip() else DEFAULT_RUNTIME_TABLE_PREFIX


def get_pgvector_table() -> str:
    base = os.getenv("PGVECTOR_TABLE", DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
    derive_pgvector_table_names(base)
    return base


def derive_pgvector_table_names(base: str) -> tuple[str, str]:
    versions = f"{base}_versions"
    releases = f"{base}_releases"
    if not _PG_IDENTIFIER.fullmatch(base):
        raise ValueError("PGVECTOR_TABLE must be a valid PostgreSQL identifier")
    if max(len(versions.encode("ascii")), len(releases.encode("ascii"))) > 63:
        raise ValueError("PGVECTOR_TABLE is too long for derived tables")
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


def get_interview_langgraph_rollout_percent() -> int:
    raw = os.getenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", "0")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT must be between 0 and 100"
        ) from exc
    if not 0 <= value <= 100:
        raise ValueError(
            "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT must be between 0 and 100"
        )
    return value


def get_interview_langgraph_version() -> str:
    value = os.getenv(
        "INTERVIEW_LANGGRAPH_VERSION", "langgraph-v1"
    ).strip()
    if value != "langgraph-v1":
        raise ValueError("unsupported INTERVIEW_LANGGRAPH_VERSION")
    return value


def get_interview_langgraph_runtime_enabled() -> bool:
    value = os.getenv(
        "INTERVIEW_LANGGRAPH_RUNTIME_ENABLED", "true"
    ).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(
            "INTERVIEW_LANGGRAPH_RUNTIME_ENABLED must be true or false"
        )
    return value == "true"


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value
