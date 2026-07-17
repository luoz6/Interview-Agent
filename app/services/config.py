import os


DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/interview"
DEFAULT_RUNTIME_STORE = "postgres"
DEFAULT_RUNTIME_TABLE_PREFIX = "interview"
DEFAULT_PGVECTOR_TABLE = "knowledge_chunks"
DEFAULT_RUNTIME_EVENT_BACKEND = "local"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


def get_postgres_dsn() -> str:
    return os.getenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN).strip() or DEFAULT_POSTGRES_DSN


def get_runtime_store() -> str:
    return os.getenv("INTERVIEW_RUNTIME_STORE", DEFAULT_RUNTIME_STORE).strip().lower() or DEFAULT_RUNTIME_STORE


def get_runtime_table_prefix() -> str:
    prefix = os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX") or os.getenv("INTERVIEW_TABLE_PREFIX")
    return prefix.strip() if prefix and prefix.strip() else DEFAULT_RUNTIME_TABLE_PREFIX


def get_pgvector_table() -> str:
    return os.getenv("PGVECTOR_TABLE", DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE


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
