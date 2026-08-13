from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.domain.interview.models import InterviewTurn, PreparedInterviewTurn
from app.graphs.interview_state import InterviewState
from app.services.prep import InterviewPlan
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport, ReportProgress, ReportRecord


@runtime_checkable
class RuntimeLLMProvider(Protocol):
    def stream_followup(self, context: list[dict[str, str]]) -> Iterator[str]:
        ...


@runtime_checkable
class EmbeddingPort(Protocol):
    provider_name: str
    model_name: str
    model_revision: str
    dimension: int

    def embed_query(self, text: str) -> list[float]:
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass
class KnowledgeLookupResult:
    found: list[Any] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    version_mismatch: list[str] = field(default_factory=list)


@runtime_checkable
class KnowledgeRepositoryPort(Protocol):
    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        domains: list[str] | None = None,
        limit: int = 5,
    ) -> list[Any]:
        ...

    def get_by_ids(
        self,
        ids: list[str],
        *,
        expected_hashes: dict[str, str] | None = None,
    ) -> KnowledgeLookupResult:
        ...


# Stable compatibility names. They point at the canonical Ports rather than
# defining a parallel protocol tree.
EmbeddingProvider = EmbeddingPort
KnowledgeRepository = KnowledgeRepositoryPort


@runtime_checkable
class SessionCommandRepository(Protocol):
    @property
    def llm(self) -> RuntimeLLMProvider | None:
        ...

    def start(
        self,
        plan: InterviewPlan,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
        session_id: str | None = None,
    ) -> InterviewTurn:
        ...

    def get(self, session_id: str) -> InterviewState:
        ...

    def snapshot(self, session_id: str) -> dict[str, Any]:
        ...

    def submit_answer(
        self,
        session_id: str,
        answer: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
        ...

    def prepare_streaming_answer(
        self,
        session_id: str,
        answer: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> PreparedInterviewTurn:
        ...

    def complete_streaming_answer(
        self,
        session_id: str,
        *,
        follow_up_text: str | None = None,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewState:
        ...

    def stream_followup(self, session_id: str) -> Iterator[str]:
        ...

    def skip(
        self,
        session_id: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
        ...

    def finish(
        self,
        session_id: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
        ...


@runtime_checkable
class ReportRepository(Protocol):
    def mark_report_processing(self, session_id: str) -> bool:
        ...

    def update_report_progress(self, session_id: str, progress: ReportProgress) -> None:
        ...

    def save_report(self, session_id: str, report: InterviewReport) -> None:
        ...

    def fail_report(self, session_id: str, error: str) -> None:
        ...

    def requeue_report(self, session_id: str) -> None:
        ...

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        ...

    def list_reports(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        days: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        ...

    def count_reports(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        days: int | None = None,
    ) -> int:
        ...

    def report_status_totals(
        self,
        *,
        query: str | None = None,
        days: int | None = None,
    ) -> dict[str, int]:
        ...


@runtime_checkable
class QuestionEvaluationRepository(Protocol):
    def upsert_question_evaluation(self, session_id: str, record: QuestionEvaluationRecord) -> None:
        ...

    def save_question_evaluations(self, session_id: str, records: list[QuestionEvaluationRecord]) -> None:
        ...

    def list_question_evaluations(self, session_id: str) -> list[QuestionEvaluationRecord]:
        ...


@runtime_checkable
class InterviewSessionRepository(
    SessionCommandRepository,
    ReportRepository,
    QuestionEvaluationRepository,
    Protocol,
):
    """Current Local V1 aggregate protocol over session, report, and evaluation storage."""


@runtime_checkable
class ReportJobRepository(Protocol):
    def enqueue_report_request(self, session_id: str) -> dict[str, Any]:
        ...

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        ...

    def get_job_by_session(self, session_id: str) -> dict[str, Any] | None:
        ...

    def mark_completed(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict[str, Any] | None:
        ...

    def mark_failed(
        self,
        job_id: str,
        error: str,
        *,
        error_code: str = "unexpected_error",
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict[str, Any] | None:
        ...

    def requeue_failed(self, session_id: str) -> dict[str, Any]:
        ...


@runtime_checkable
class ReportJobLeaseAdapter(Protocol):
    def claim_next(
        self,
        worker_id: str,
        lease_seconds: int | None = None,
    ) -> dict[str, Any] | None:
        ...

    def assert_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> bool:
        ...

    def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: int | None = None,
    ) -> bool:
        ...

    def release_claim_for_retry(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_token: str,
        delay_seconds: float = 0.25,
    ) -> bool:
        ...


@runtime_checkable
class ReportRetryAdapter(Protocol):
    def schedule_review_retry(
        self,
        job_id: str,
        *,
        next_attempt_number: int,
        delay_seconds: float = 0,
    ) -> str:
        ...

    def mark_retryable_failure(
        self,
        job_id: str,
        error: str,
        *,
        error_code: str = "unexpected_error",
        worker_id: str | None = None,
        lease_token: str | None = None,
    ) -> dict[str, Any] | None:
        ...


@runtime_checkable
class ReportOrphanRepair(Protocol):
    def repair_orphan_processing_reports(self) -> int:
        ...


@runtime_checkable
class ReportJobQueue(
    ReportJobRepository,
    ReportJobLeaseAdapter,
    ReportRetryAdapter,
    ReportOrphanRepair,
    Protocol,
):
    """Aggregate compatibility Port for current runtime wiring."""


class ReportWorker(Protocol):
    def run_one(self) -> bool:
        ...


@runtime_checkable
class RuntimeEventPublisher(Protocol):
    def publish(self, event: Any) -> None:
        ...
