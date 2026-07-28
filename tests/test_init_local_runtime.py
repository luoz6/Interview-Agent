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

    def get_active_corpus_version(self):
        return getattr(self, "active_version", None)


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
    assert result["knowledge_corpus_version"] is None
    assert result["seeded"] is False


def test_initialize_runtime_seeds_knowledge_idempotently():
    knowledge = FakeKnowledgeStore()
    received_versions = []

    def seed(*, store, corpus_version):
        received_versions.append(corpus_version)
        store.count = 10
        store.active_version = corpus_version

    first = initialize_runtime(
        session_store=FakeSessionStore(),
        job_store=FakeJobStore(),
        knowledge_store=knowledge,
        seed_knowledge=True,
        seed_loader=seed,
        corpus_version="stage44a-bge-m3-v1",
    )
    second = initialize_runtime(
        session_store=FakeSessionStore(),
        job_store=FakeJobStore(),
        knowledge_store=knowledge,
        seed_knowledge=True,
        seed_loader=seed,
        corpus_version="stage44a-bge-m3-v1",
    )

    assert first["knowledge_chunks"] == 10
    assert second["knowledge_chunks"] == 10
    assert first["seeded"] is True
    assert first["knowledge_corpus_version"] == "stage44a-bge-m3-v1"
    assert received_versions == ["stage44a-bge-m3-v1", "stage44a-bge-m3-v1"]


def test_initialize_runtime_requires_version_when_seeding():
    try:
        initialize_runtime(
            session_store=FakeSessionStore(),
            job_store=FakeJobStore(),
            knowledge_store=FakeKnowledgeStore(),
            seed_knowledge=True,
        )
    except ValueError as exc:
        assert "corpus_version" in str(exc)
    else:
        raise AssertionError("expected corpus_version requirement")


class VersionedReadOnlyConnection:
    existing_tables = {
        "stage44_sessions",
        "stage44_messages",
        "stage44_reports",
        "stage44_question_evaluations",
        "stage44_report_jobs",
        "knowledge_stage44_versions",
        "knowledge_stage44_releases",
    }
    active_release = ("stage44a-bge-m3-v1", 25)
    vector_extension = True
    statements = []

    def __enter__(self):
        type(self).statements = []
        return self

    def __exit__(self, *_args):
        return None

    def set_session(self, **kwargs):
        assert kwargs == {"readonly": True, "autocommit": True}

    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, statement, params=None):
                connection.statements.append((statement, params))

            def fetchone(self):
                statement = connection.statements[-1][0]
                if "pg_extension" in statement:
                    return (connection.vector_extension,)
                if "status = 'active'" in statement:
                    return connection.active_release
                raise AssertionError(f"unexpected fetchone query: {statement}")

            def fetchall(self):
                requested = connection.statements[-1][1][0]
                return [(name,) for name in requested if name in connection.existing_tables]

        return Cursor()


def connect_versioned(dsn):
    assert dsn == "postgresql://example"
    return VersionedReadOnlyConnection()


def test_check_runtime_uses_versioned_tables_and_active_release_only():
    result = check_runtime(
        dsn="postgresql://example",
        table_prefix="stage44",
        knowledge_table="knowledge_stage44",
        connect=connect_versioned,
    )

    assert result["initialized"] is True
    assert result["knowledge_table"] == "knowledge_stage44"
    assert result["knowledge_corpus_version"] == "stage44a-bge-m3-v1"
    assert result["knowledge_chunks"] == 25
    assert result["required_knowledge_tables"] == [
        "knowledge_stage44_versions",
        "knowledge_stage44_releases",
    ]
    assert result["runtime_tables"] == [
        "stage44_sessions",
        "stage44_messages",
        "stage44_reports",
        "stage44_question_evaluations",
        "stage44_report_jobs",
    ]
    assert all(
        token not in str(statement).upper()
        for statement, _ in VersionedReadOnlyConnection.statements
        for token in ("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE ")
    )


def test_check_runtime_allows_no_active_release():
    original = VersionedReadOnlyConnection.active_release
    VersionedReadOnlyConnection.active_release = None
    try:
        result = check_runtime(
            dsn="postgresql://example",
            table_prefix="stage44",
            knowledge_table="knowledge_stage44",
            connect=connect_versioned,
        )
    finally:
        VersionedReadOnlyConnection.active_release = original

    assert result["initialized"] is True
    assert result["knowledge_corpus_version"] is None
    assert result["knowledge_chunks"] == 0


def test_check_runtime_requires_both_derived_tables():
    original = VersionedReadOnlyConnection.existing_tables
    VersionedReadOnlyConnection.existing_tables = original - {
        "knowledge_stage44_releases"
    }
    try:
        result = check_runtime(
            dsn="postgresql://example",
            table_prefix="stage44",
            knowledge_table="knowledge_stage44",
            connect=connect_versioned,
        )
    finally:
        VersionedReadOnlyConnection.existing_tables = original

    assert result["initialized"] is False


def test_check_runtime_rejects_overlong_knowledge_base():
    try:
        check_runtime(
            dsn="postgresql://example",
            table_prefix="stage44",
            knowledge_table="x" * 55,
            connect=connect_versioned,
        )
    except ValueError as exc:
        assert "PGVECTOR_TABLE" in str(exc)
    else:
        raise AssertionError("expected overlong knowledge table to fail")


def test_check_runtime_does_not_require_legacy_base_table():
    assert "knowledge_stage44" not in VersionedReadOnlyConnection.existing_tables

    result = check_runtime(
        dsn="postgresql://example",
        table_prefix="stage44",
        knowledge_table="knowledge_stage44",
        connect=connect_versioned,
    )

    assert result["initialized"] is True
