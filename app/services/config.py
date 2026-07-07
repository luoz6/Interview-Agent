import os


DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/interview"
DEFAULT_RUNTIME_STORE = "postgres"
DEFAULT_RUNTIME_TABLE_PREFIX = "interview"
DEFAULT_PGVECTOR_TABLE = "knowledge_chunks"


def get_postgres_dsn() -> str:
    return os.getenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN).strip() or DEFAULT_POSTGRES_DSN


def get_runtime_store() -> str:
    return os.getenv("INTERVIEW_RUNTIME_STORE", DEFAULT_RUNTIME_STORE).strip().lower() or DEFAULT_RUNTIME_STORE


def get_runtime_table_prefix() -> str:
    prefix = os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX") or os.getenv("INTERVIEW_TABLE_PREFIX")
    return prefix.strip() if prefix and prefix.strip() else DEFAULT_RUNTIME_TABLE_PREFIX


def get_pgvector_table() -> str:
    return os.getenv("PGVECTOR_TABLE", DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
