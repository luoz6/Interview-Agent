from app.services.runtime import (
    DEFAULT_POSTGRES_DSN,
    build_report_executor,
    build_event_publisher,
    build_report_job_store,
    build_session_store,
    get_draft_store,
    get_event_publisher,
    get_report_executor,
    get_report_job_store,
    reset_runtime_for_tests,
)


def test_build_session_store_defaults_to_local_postgres(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.delenv("INTERVIEW_RUNTIME_STORE", raising=False)

    created = {}

    class FakePostgresStore:
        def __init__(self, *, dsn, table_prefix="interview", llm=None):
            created["dsn"] = dsn
            created["table_prefix"] = table_prefix
            created["llm"] = llm

    monkeypatch.setattr(
        "app.services.runtime.PostgresInterviewSessionStore",
        FakePostgresStore,
    )

    store = build_session_store()

    assert isinstance(store, FakePostgresStore)
    assert created["dsn"] == DEFAULT_POSTGRES_DSN
    assert created["table_prefix"] == "interview"


def test_build_session_store_uses_postgres_when_enabled(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "postgres")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@localhost/db")

    created = {}

    class FakePostgresStore:
        def __init__(self, *, dsn, table_prefix="interview", llm=None):
            created["dsn"] = dsn
            created["table_prefix"] = table_prefix
            created["llm"] = llm

    monkeypatch.setattr(
        "app.services.runtime.PostgresInterviewSessionStore",
        FakePostgresStore,
    )

    store = build_session_store()

    assert isinstance(store, FakePostgresStore)
    assert created["dsn"] == "postgresql://user:pass@localhost/db"
    assert created["table_prefix"] == "interview"


def test_build_session_store_uses_runtime_table_prefix_with_legacy_fallback(monkeypatch):
    monkeypatch.delenv("INTERVIEW_RUNTIME_STORE", raising=False)
    monkeypatch.setenv("INTERVIEW_TABLE_PREFIX", "legacy_prefix")
    monkeypatch.delenv("INTERVIEW_RUNTIME_TABLE_PREFIX", raising=False)

    created = {}

    class FakePostgresStore:
        def __init__(self, *, dsn, table_prefix="interview", llm=None):
            created["table_prefix"] = table_prefix

    monkeypatch.setattr(
        "app.services.runtime.PostgresInterviewSessionStore",
        FakePostgresStore,
    )

    build_session_store()
    assert created["table_prefix"] == "legacy_prefix"

    monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "runtime_prefix")
    build_session_store()
    assert created["table_prefix"] == "runtime_prefix"


def test_build_report_job_store_defaults_to_local_postgres_dsn(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    created = {}

    class FakeReportJobStore:
        def __init__(self, *, dsn, table_prefix="interview", lease_seconds=300):
            created["dsn"] = dsn
            created["table_prefix"] = table_prefix
            created["lease_seconds"] = lease_seconds

    monkeypatch.setattr(
        "app.services.runtime.PostgresReportJobStore",
        FakeReportJobStore,
    )

    store = build_report_job_store()

    assert isinstance(store, FakeReportJobStore)
    assert created["dsn"] == DEFAULT_POSTGRES_DSN
    assert created["table_prefix"] == "interview"


def test_build_report_job_store_uses_postgres_dsn_and_runtime_prefix(monkeypatch):
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@localhost/interview")
    monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "runtime_test")

    created = {}

    class FakeReportJobStore:
        def __init__(self, *, dsn, table_prefix="interview", lease_seconds=300):
            created["dsn"] = dsn
            created["table_prefix"] = table_prefix
            created["lease_seconds"] = lease_seconds

    monkeypatch.setattr(
        "app.services.runtime.PostgresReportJobStore",
        FakeReportJobStore,
    )

    store = build_report_job_store()

    assert isinstance(store, FakeReportJobStore)
    assert created["dsn"] == "postgresql://user:pass@localhost/interview"
    assert created["table_prefix"] == "runtime_test"
    assert created["lease_seconds"] == 300


def test_config_exposes_event_backend_and_redis_defaults(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from app.services.config import (
        DEFAULT_REDIS_URL,
        DEFAULT_RUNTIME_EVENT_BACKEND,
        get_redis_url,
        get_runtime_event_backend,
    )

    assert DEFAULT_RUNTIME_EVENT_BACKEND == "noop"
    assert DEFAULT_REDIS_URL == "redis://127.0.0.1:6379/0"
    assert get_runtime_event_backend() == "noop"
    assert get_redis_url() == "redis://127.0.0.1:6379/0"


def test_build_event_publisher_defaults_to_noop(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)

    from app.services.event_publisher import NoopRuntimeEventPublisher

    publisher = build_event_publisher()

    assert isinstance(publisher, NoopRuntimeEventPublisher)


def test_build_report_executor_reuses_session_store_llm_and_vector_store(monkeypatch):
    fake_llm = object()
    fake_store = type("FakeStore", (), {"llm": fake_llm})()
    fake_vector_store = object()
    created = {"llm_factory_called": False}

    class FakeOpenAIInterviewLLM:
        def __init__(self):
            created["llm_factory_called"] = True

    monkeypatch.setattr("app.services.runtime.get_session_store", lambda: fake_store)
    monkeypatch.setattr("app.services.runtime.get_knowledge_store", lambda: fake_vector_store)
    monkeypatch.setattr("app.services.runtime.OpenAIInterviewLLM", FakeOpenAIInterviewLLM)

    executor = build_report_executor()

    assert executor.store is fake_store
    assert executor.llm is fake_llm
    assert executor.vector_store is fake_vector_store
    assert created["llm_factory_called"] is False


def test_build_report_executor_creates_llm_when_store_has_none(monkeypatch):
    fake_store = type("FakeStore", (), {"llm": None})()
    fake_vector_store = object()
    fake_llm = object()

    monkeypatch.setattr("app.services.runtime.get_session_store", lambda: fake_store)
    monkeypatch.setattr("app.services.runtime.get_knowledge_store", lambda: fake_vector_store)
    monkeypatch.setattr("app.services.runtime.OpenAIInterviewLLM", lambda: fake_llm)

    executor = build_report_executor()

    assert executor.store is fake_store
    assert executor.llm is fake_llm
    assert executor.vector_store is fake_vector_store


def test_get_report_job_store_caches_until_reset(monkeypatch):
    created = []

    def fake_builder():
        value = object()
        created.append(value)
        return value

    reset_runtime_for_tests()
    monkeypatch.setattr("app.services.runtime.build_report_job_store", fake_builder)

    first = get_report_job_store()
    second = get_report_job_store()

    assert first is second
    assert len(created) == 1

    reset_runtime_for_tests()
    third = get_report_job_store()

    assert third is not first
    assert len(created) == 2


def test_get_report_executor_caches_until_reset(monkeypatch):
    created = []

    def fake_builder():
        value = object()
        created.append(value)
        return value

    reset_runtime_for_tests()
    monkeypatch.setattr("app.services.runtime.build_report_executor", fake_builder)

    first = get_report_executor()
    second = get_report_executor()

    assert first is second
    assert len(created) == 1

    reset_runtime_for_tests()
    third = get_report_executor()

    assert third is not first
    assert len(created) == 2


def test_get_event_publisher_caches_until_reset(monkeypatch):
    created = []

    def fake_builder():
        value = object()
        created.append(value)
        return value

    reset_runtime_for_tests()
    monkeypatch.setattr("app.services.runtime.build_event_publisher", fake_builder)

    first = get_event_publisher()
    second = get_event_publisher()

    assert first is second
    assert len(created) == 1

    reset_runtime_for_tests()
    third = get_event_publisher()

    assert third is not first
    assert len(created) == 2


def test_get_draft_store_caches_until_reset():
    reset_runtime_for_tests()
    first = get_draft_store()
    second = get_draft_store()

    assert first is second

    reset_runtime_for_tests()
    third = get_draft_store()

    assert third is not first
