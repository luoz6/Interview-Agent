from app.ports.runtime import (
    InterviewSessionRepository,
    KnowledgeRepository,
    QuestionEvaluationRepository,
    ReportJobQueue,
    ReportRepository,
    RuntimeEventPublisher,
    RuntimeLLMProvider,
    SessionCommandRepository,
)
from app.services.event_publisher import NoopRuntimeEventPublisher
from app.services.llm import OpenAIInterviewLLM
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.session import InterviewSessionStore
from app.services.vector_store import PgVectorKnowledgeStore
from app.services.vector_store import KnowledgeSearchStore


def test_runtime_protocols_are_runtime_checkable():
    for protocol in (
        SessionCommandRepository,
        ReportRepository,
        QuestionEvaluationRepository,
        InterviewSessionRepository,
        ReportJobQueue,
        KnowledgeRepository,
        RuntimeLLMProvider,
        RuntimeEventPublisher,
    ):
        assert getattr(protocol, "_is_runtime_protocol", False)


def test_memory_session_store_matches_split_repository_protocols():
    store = InterviewSessionStore()

    assert isinstance(store, SessionCommandRepository)
    assert isinstance(store, ReportRepository)
    assert isinstance(store, QuestionEvaluationRepository)
    assert isinstance(store, InterviewSessionRepository)


def test_postgres_session_store_matches_split_repository_protocols_without_connecting():
    store = object.__new__(PostgresInterviewSessionStore)
    store._llm = None

    assert isinstance(store, SessionCommandRepository)
    assert isinstance(store, ReportRepository)
    assert isinstance(store, QuestionEvaluationRepository)
    assert isinstance(store, InterviewSessionRepository)


def test_postgres_job_store_matches_report_queue_protocol_without_connecting():
    queue = object.__new__(PostgresReportJobStore)

    assert isinstance(queue, ReportJobQueue)


def test_noop_event_publisher_makes_local_v1_publisher_boundary_explicit():
    publisher = NoopRuntimeEventPublisher()

    assert isinstance(publisher, RuntimeEventPublisher)
    assert publisher.publish({"event": "ignored"}) is None


def test_vector_store_and_llm_expose_runtime_contracts_without_network_calls():
    vector_store = object.__new__(PgVectorKnowledgeStore)
    llm = object.__new__(OpenAIInterviewLLM)

    assert isinstance(vector_store, KnowledgeRepository)
    assert isinstance(llm, RuntimeLLMProvider)


def test_legacy_knowledge_search_protocol_is_canonical_repository_alias():
    assert KnowledgeSearchStore is KnowledgeRepository


def test_search_only_fake_does_not_claim_v2_repository_contract():
    class SearchOnlyFake:
        def search(
            self,
            query_text: str,
            *,
            job_tags: list[str],
            source_types=None,
            limit=5,
        ):
            return []

    assert not isinstance(SearchOnlyFake(), KnowledgeRepository)
