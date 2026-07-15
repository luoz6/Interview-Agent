from scripts.init_local_runtime import (
    check_runtime,
    count_knowledge_chunks,
    ensure_knowledge_schema,
    initialize_runtime,
)


class FakeSessionStore:
    def list_runtime_tables(self):
        return [
            "stage41_sessions",
            "stage41_messages",
            "stage41_reports",
            "stage41_question_evaluations",
        ]


class FakeJobStore:
    jobs_table = "stage41_report_jobs"


class FakeKnowledgeStore:
    table_name = "knowledge_stage41"

    def ensure_schema(self):
        self.schema_ensured = True

    def count_chunks(self):
        return getattr(self, "count", 0)


def test_knowledge_helpers_support_current_pgvector_private_schema_boundary():
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement): self.statement = statement
        def fetchone(self): return (7,)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    class Psycopg:
        @staticmethod
        def connect(_dsn): return Connection()

    class Sql:
        @staticmethod
        def SQL(value): return value
        @staticmethod
        def Identifier(value): return value

    class CurrentKnowledgeStore:
        dsn = bytes([100, 115, 110]).decode()
        table_name = bytes([107, 110, 111, 119, 108, 101, 100, 103, 101]).decode()
        def _import_psycopg2(self): return Psycopg, Sql
        def _ensure_schema(self, connection): self.connection = connection

    store = CurrentKnowledgeStore()
    ensure_knowledge_schema(store)
    assert store.connection is not None
    assert count_knowledge_chunks(store) == 7


def test_initialize_runtime_reports_all_tables_without_seeding():
    knowledge = FakeKnowledgeStore()

    result = initialize_runtime(
        session_store=FakeSessionStore(),
        job_store=FakeJobStore(),
        knowledge_store=knowledge,
        seed_knowledge=False,
    )

    assert knowledge.schema_ensured is True
    assert result["runtime_tables"] == [
        "stage41_sessions",
        "stage41_messages",
        "stage41_reports",
        "stage41_question_evaluations",
        "stage41_report_jobs",
    ]
    assert result["knowledge_table"] == "knowledge_stage41"
    assert result["knowledge_chunks"] == 0
    assert result["seeded"] is False


def test_initialize_runtime_seeds_knowledge_idempotently():
    knowledge = FakeKnowledgeStore()

    def seed(store):
        store.count = 10
        return {"discovered": 10, "upserted": 10}

    first = initialize_runtime(
        session_store=FakeSessionStore(),
        job_store=FakeJobStore(),
        knowledge_store=knowledge,
        seed_knowledge=True,
        seed_loader=seed,
    )
    second = initialize_runtime(
        session_store=FakeSessionStore(),
        job_store=FakeJobStore(),
        knowledge_store=knowledge,
        seed_knowledge=True,
        seed_loader=seed,
    )

    assert first["knowledge_chunks"] == 10
    assert second["knowledge_chunks"] == 10
    assert first["seeded"] is True


def test_check_runtime_only_reads_existing_schema():
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, params=None):
            statements.append((statement, params))

        def fetchone(self):
            if "pg_extension" in statements[-1][0]:
                return (True,)
            return (12,)

        def fetchall(self):
            return [(name,) for name in statements[-1][1][0]]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def set_session(self, **kwargs):
            assert kwargs == {"readonly": True, "autocommit": True}

        def cursor(self):
            return Cursor()

    def connect(dsn):
        assert dsn == "postgresql://example"
        return Connection()

    result = check_runtime(
        dsn="postgresql://example",
        table_prefix="stage41",
        knowledge_table="knowledge_stage41",
        connect=connect,
    )

    assert result["initialized"] is True
    assert result["vector_extension"] is True
    assert result["knowledge_chunks"] == 12
    assert result["runtime_tables"] == [
        "stage41_sessions",
        "stage41_messages",
        "stage41_reports",
        "stage41_question_evaluations",
        "stage41_report_jobs",
    ]
    assert all(
        token not in statement.upper()
        for statement, _ in statements
        for token in ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")
    )
