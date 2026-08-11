from types import SimpleNamespace

import pytest

import app.services.runtime as runtime_module
from app.services.runtime import (
    DEFAULT_POSTGRES_DSN,
    build_report_executor,
    build_runtime_followup_decision_provider,
    build_event_publisher,
    build_report_job_store,
    build_session_store,
    get_draft_store,
    get_event_publisher,
    get_report_executor,
    get_report_job_store,
    reset_runtime_for_tests,
    shutdown_runtime,
)


@pytest.fixture(autouse=True)
def isolated_connection_domains(monkeypatch):
    domains = SimpleNamespace(business=object(), telemetry=object())
    monkeypatch.setattr(
        runtime_module,
        "get_postgres_connection_domains",
        lambda: domains,
    )
    return domains


def test_build_session_store_defaults_to_local_postgres(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.delenv("INTERVIEW_RUNTIME_STORE", raising=False)

    created = {}

    class FakePostgresStore:
        def __init__(
            self,
            *,
            dsn,
            connection_provider=None,
            agent_run_connection_provider=None,
            table_prefix="interview",
            llm=None,
            execution_runner=None,
            schema_mode="migrate",
        ):
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
        def __init__(
            self,
            *,
            dsn,
            connection_provider=None,
            agent_run_connection_provider=None,
            table_prefix="interview",
            llm=None,
            execution_runner=None,
            schema_mode="migrate",
        ):
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
        def __init__(
            self,
            *,
            dsn,
            connection_provider=None,
            agent_run_connection_provider=None,
            table_prefix="interview",
            llm=None,
            execution_runner=None,
            schema_mode="migrate",
        ):
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
        def __init__(self, *, dsn, connection_provider=None, table_prefix="interview", lease_seconds=300, schema_mode="migrate"):
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
        def __init__(self, *, dsn, connection_provider=None, table_prefix="interview", lease_seconds=300, schema_mode="migrate"):
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
    assert created["lease_seconds"] == 45


def test_build_report_job_store_uses_memory_queue_for_preview(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "memory")
    monkeypatch.delenv("REPORT_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("REPORT_JOB_STORE", raising=False)

    store = build_report_job_store()

    from app.services.memory_report_jobs import InMemoryReportJobStore

    assert isinstance(store, InMemoryReportJobStore)


def test_context_compression_failure_containment_is_interview_only(monkeypatch):
    from app.services.memory_config import load_effective_memory_config

    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "memory")
    reset_runtime_for_tests()
    try:
        interview = runtime_module.get_context_compression_runner(
            workflow="interview",
            lease_seconds=30,
        )
        review = runtime_module.get_context_compression_runner(
            workflow="review",
            lease_seconds=30,
        )
        prep = runtime_module.get_context_compression_runner(
            workflow="prep",
            lease_seconds=30,
        )
    finally:
        reset_runtime_for_tests()

    effective = load_effective_memory_config({}).compression
    assert interview is not review
    assert interview is not prep
    assert interview.failure_containment is not None
    assert review.failure_containment is None
    assert prep.failure_containment is None
    actual = interview.failure_containment.config
    assert actual.provider_circuit_threshold == (
        effective.provider_circuit_threshold
    )
    assert actual.provider_circuit_cooldown_seconds == (
        effective.provider_circuit_cooldown_seconds
    )
    assert actual.validation_quarantine_threshold == (
        effective.validation_quarantine_threshold
    )
    assert actual.validation_quarantine_cooldown_seconds == (
        effective.validation_quarantine_cooldown_seconds
    )
    assert actual.failure_state_lease_seconds == (
        effective.failure_state_lease_seconds
    )


def test_runtime_session_deletion_worker_receives_authoritative_failure_store(
    monkeypatch,
):
    captured = {}
    failure_store = object()
    service = SimpleNamespace(
        job_store=object(),
        tombstone_store=object(),
    )

    class FakeWorker:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    reset_runtime_for_tests()
    monkeypatch.setattr(runtime_module, "get_runtime_store", lambda: "memory")
    monkeypatch.setattr(
        runtime_module,
        "get_session_deletion_service",
        lambda: service,
    )
    monkeypatch.setattr(runtime_module, "get_session_store", object)
    monkeypatch.setattr(runtime_module, "get_question_memory_index_store", object)
    monkeypatch.setattr(runtime_module, "get_context_artifact_store", object)
    monkeypatch.setattr(runtime_module, "get_report_job_store", object)
    monkeypatch.setattr(runtime_module, "get_report_artifact_store", object)
    monkeypatch.setattr(runtime_module, "get_principal_memory_fact_store", object)
    monkeypatch.setattr(runtime_module, "get_principal_memory_control_store", object)
    monkeypatch.setattr(
        runtime_module,
        "get_context_compression_failure_store",
        lambda: failure_store,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.session_deletion_worker.SessionDeletionWorker",
        FakeWorker,
    )

    runtime_module.get_session_deletion_worker()

    assert captured["failure_state_store"] is failure_store
    reset_runtime_for_tests()


def test_runtime_maintenance_receives_authoritative_failure_store(monkeypatch):
    captured = {}
    failure_store = object()

    class FakeMaintenance:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(runtime_module, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(runtime_module, "get_postgres_dsn", lambda: "fake-dsn")
    monkeypatch.setattr(runtime_module, "get_runtime_table_prefix", lambda: "fake")
    monkeypatch.setattr(runtime_module, "get_runtime_signal_store", object)
    monkeypatch.setattr(runtime_module, "get_context_artifact_store", object)
    monkeypatch.setattr(
        runtime_module,
        "get_context_compression_failure_store",
        lambda: failure_store,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.durable_workflow_maintenance.DurableWorkflowMaintenanceService",
        FakeMaintenance,
    )
    monkeypatch.setattr(
        "app.services.interview_generation_store.PostgresInterviewGenerationStore",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.services.interview_workflow_store.PostgresInterviewWorkflowStore",
        lambda **kwargs: object(),
    )

    runtime_module.build_durable_workflow_maintenance_service()

    assert captured["failure_state_store"] is failure_store
    assert captured["failure_state_cleanup_batch_size"] > 0


def test_config_exposes_event_backend_and_redis_defaults(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from app.services.config import (
        DEFAULT_REDIS_URL,
        DEFAULT_RUNTIME_EVENT_BACKEND,
        get_redis_url,
        get_runtime_event_backend,
    )

    assert DEFAULT_RUNTIME_EVENT_BACKEND == "local"
    assert DEFAULT_REDIS_URL == "redis://127.0.0.1:6379/0"
    assert get_runtime_event_backend() == "local"
    assert get_redis_url() == "redis://127.0.0.1:6379/0"


def test_build_event_publisher_defaults_to_local_round_review(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)

    from app.services.event_publisher import LocalRoundReviewEventPublisher

    publisher = build_event_publisher()

    assert isinstance(publisher, LocalRoundReviewEventPublisher)


def test_build_event_publisher_supports_explicit_noop(monkeypatch):
    monkeypatch.setenv("INTERVIEW_EVENT_BACKEND", "noop")

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
    monkeypatch.setattr("app.services.runtime.get_knowledge_store", lambda **kwargs: fake_vector_store)
    monkeypatch.setattr("app.services.runtime.OpenAIInterviewLLM", FakeOpenAIInterviewLLM)

    executor = build_report_executor()

    assert executor.store is fake_store
    assert executor.llm is fake_llm
    assert executor.vector_store is fake_vector_store
    assert created["llm_factory_called"] is False


def test_runtime_followup_decision_provider_uses_exact_llm_config():
    store = SimpleNamespace(
        llm=SimpleNamespace(
            chat_model=object(),
            config=SimpleNamespace(model="deepseek-v4-pro"),
        )
    )

    provider = build_runtime_followup_decision_provider(store)

    assert provider.output_mode == "raw_only"
    assert provider.expected_model == "deepseek-v4-pro"

    missing_model = SimpleNamespace(
        llm=SimpleNamespace(chat_model=object())
    )
    with pytest.raises(ValueError, match="requires an exact configured model"):
        build_runtime_followup_decision_provider(missing_model)


def test_build_report_executor_creates_llm_when_store_has_none(monkeypatch):
    fake_store = type("FakeStore", (), {"llm": None})()
    fake_vector_store = object()
    fake_llm = object()

    monkeypatch.setattr("app.services.runtime.get_session_store", lambda: fake_store)
    monkeypatch.setattr(
        "app.services.runtime.get_knowledge_store",
        lambda **kwargs: fake_vector_store,
    )
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


def test_shutdown_runtime_drains_cached_event_publisher(monkeypatch):
    closed = []

    class FakePublisher:
        def shutdown(self, *, wait=True):
            closed.append(wait)

    reset_runtime_for_tests()
    monkeypatch.setattr("app.services.runtime.build_event_publisher", lambda: FakePublisher())

    get_event_publisher()
    shutdown_runtime(wait=True)

    assert closed == [True]


def test_reset_runtime_for_tests_shuts_down_cached_event_publisher(monkeypatch):
    closed = []

    class FakePublisher:
        def shutdown(self, *, wait=True):
            closed.append(wait)

    reset_runtime_for_tests()
    monkeypatch.setattr("app.services.runtime.build_event_publisher", lambda: FakePublisher())

    get_event_publisher()
    reset_runtime_for_tests()

    assert closed == [False]


def test_get_draft_store_caches_until_reset(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "memory")
    reset_runtime_for_tests()
    first = get_draft_store()
    second = get_draft_store()

    assert first is second

    reset_runtime_for_tests()
    third = get_draft_store()

    assert third is not first
