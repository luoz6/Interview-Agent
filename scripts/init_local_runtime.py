import argparse
import json
import re

from app.services.config import get_pgvector_table, get_postgres_dsn, get_runtime_table_prefix
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.vector_store import PgVectorKnowledgeStore
from scripts.load_knowledge import load_knowledge


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_RUNTIME_TABLE_SUFFIXES = (
    "sessions",
    "messages",
    "reports",
    "question_evaluations",
    "report_jobs",
)


def check_runtime(*, dsn: str, table_prefix: str, knowledge_table: str, connect=None) -> dict:
    if not _IDENTIFIER_PATTERN.fullmatch(table_prefix):
        raise ValueError("runtime table prefix must be a valid PostgreSQL identifier")
    if not _IDENTIFIER_PATTERN.fullmatch(knowledge_table):
        raise ValueError("knowledge table must be a valid PostgreSQL identifier")
    if connect is None:
        import psycopg2

        connect = psycopg2.connect

    runtime_tables = [f"{table_prefix}_{suffix}" for suffix in _RUNTIME_TABLE_SUFFIXES]
    expected_tables = [*runtime_tables, knowledge_table]
    with connect(dsn) as connection:
        connection.set_session(readonly=True, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            vector_extension = bool(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(%s)
                """,
                (expected_tables,),
            )
            existing_tables = {row[0] for row in cursor.fetchall()}
            knowledge_chunks = 0
            if knowledge_table in existing_tables:
                cursor.execute(f'SELECT COUNT(*) FROM "{knowledge_table}"')
                row = cursor.fetchone()
                knowledge_chunks = int(row[0]) if row is not None else 0

    present_runtime_tables = [name for name in runtime_tables if name in existing_tables]
    return {
        "initialized": vector_extension and set(expected_tables) <= existing_tables,
        "schema_version": "local-v1",
        "vector_extension": vector_extension,
        "runtime_tables": present_runtime_tables,
        "knowledge_table": knowledge_table,
        "knowledge_chunks": knowledge_chunks,
        "table_prefix": table_prefix,
    }


def ensure_knowledge_schema(store) -> None:
    public_method = getattr(store, 'ensure_schema', None)
    if public_method is not None:
        public_method()
        return
    psycopg2, _ = store._import_psycopg2()
    with psycopg2.connect(store.dsn) as connection:
        store._ensure_schema(connection)


def count_knowledge_chunks(store) -> int:
    public_method = getattr(store, 'count_chunks', None)
    if public_method is not None:
        return int(public_method())
    psycopg2, sql = store._import_psycopg2()
    with psycopg2.connect(store.dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL('SELECT COUNT(*) FROM {table}').format(
                    table=sql.Identifier(store.table_name)
                )
            )
            row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def initialize_runtime(
    *,
    session_store,
    job_store,
    knowledge_store,
    seed_knowledge: bool,
    seed_loader=load_knowledge,
) -> dict:
    knowledge_store.ensure_schema()
    if seed_knowledge:
        seed_loader(knowledge_store)
    runtime_tables = list(session_store.list_runtime_tables())
    if job_store.jobs_table not in runtime_tables:
        runtime_tables.append(job_store.jobs_table)
    return {
        "runtime_tables": runtime_tables,
        "knowledge_table": knowledge_store.table_name,
        "knowledge_chunks": knowledge_store.count_chunks(),
        "seeded": seed_knowledge,
    }


def build_runtime_components():
    dsn = get_postgres_dsn()
    prefix = get_runtime_table_prefix()
    return (
        PostgresInterviewSessionStore(dsn=dsn, table_prefix=prefix),
        PostgresReportJobStore(dsn=dsn, table_prefix=prefix),
        PgVectorKnowledgeStore.from_env(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Local V1 PostgreSQL runtime")
    parser.add_argument("--seed-knowledge", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            if args.seed_knowledge:
                parser.error("--check cannot be combined with --seed-knowledge")
            result = check_runtime(
                dsn=get_postgres_dsn(),
                table_prefix=get_runtime_table_prefix(),
                knowledge_table=get_pgvector_table(),
            )
        else:
            session_store, job_store, knowledge_store = build_runtime_components()
            result = initialize_runtime(
                session_store=session_store,
                job_store=job_store,
                knowledge_store=knowledge_store,
                seed_knowledge=args.seed_knowledge,
            )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
