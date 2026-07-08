from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

from app.graphs.interview_state import InterviewState
from app.services.prep import InterviewPlan
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.session import InterviewTurn, PreparedInterviewTurn


@runtime_checkable
class RuntimeLLMProvider(Protocol):
    def stream_followup(self, context: list[dict[str, str]]) -> Iterator[str]:
        ...


@runtime_checkable
class KnowledgeRepository(Protocol):
    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[Any]:
        ...


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
    ) -> InterviewTurn:
        ...

    def get(self, session_id: str) -> InterviewState:
        ...

    def snapshot(self, session_id: str) -> dict[str, Any]:
        ...

    def submit_answer(self, session_id: str, answer: str) -> InterviewTurn:
        ...

    def prepare_streaming_answer(self, session_id: str, answer: str) -> PreparedInterviewTurn:
        ...

    def complete_streaming_answer(self, session_id: str, *, follow_up_text: str | None = None) -> InterviewState:
        ...

    def stream_followup(self, session_id: str) -> Iterator[str]:
        ...

    def skip(self, session_id: str) -> InterviewTurn:
        ...

    def finish(self, session_id: str) -> InterviewTurn:
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

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        ...

    def list_reports(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
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
class ReportJobQueue(Protocol):
    def enqueue_report_request(self, session_id: str) -> dict[str, Any]:
        ...

    def claim_next(self, worker_id: str, lease_seconds: int | None = None) -> dict[str, Any] | None:
        ...

    def mark_completed(self, job_id: str) -> dict[str, Any] | None:
        ...

    def mark_failed(self, job_id: str, error: str) -> dict[str, Any] | None:
        ...

    def mark_retryable_failure(self, job_id: str, error: str) -> dict[str, Any] | None:
        ...

    def repair_orphan_processing_reports(self) -> int:
        ...

    def get_job_by_session(self, session_id: str) -> dict[str, Any] | None:
        ...


@runtime_checkable
class RuntimeEventPublisher(Protocol):
    def publish(self, event: Any) -> None:
        ...
