from app.domain.interview.state import InterviewState
from app.ports.llm import InterviewLLM
from app.domain.report.evaluation_chunks import (
    EvaluationChunk,
    build_evaluation_chunks,
)
from app.domain.report.fallback import (
    _apply_answer_state_overrides,
    _null_dimension_scores,
    _summarize_candidate_answers,
    build_empty_answer_feedback,
    build_fallback_report,
)
from app.domain.report.models import (
    DimensionScores,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
)
from app.domain.report.coverage import (
    aggregate_report_coverage,
)


class ShadowEvaluator:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._llm = llm

    def evaluate(self, state: InterviewState) -> InterviewReport:
        chunks = build_evaluation_chunks(state)
        try:
            if self._llm is None:
                raise ReportGenerationFailed("report llm is not configured")
            report = self._llm.generate_report(
                plan=state["plan"],
                evaluation_items=[chunk.model_dump() for chunk in chunks],
                session_id=state["session_id"],
            )
            return _apply_answer_state_overrides(report, chunks)
        except ReportGenerationTimeout:
            raise
        except ReportGenerationFailed:
            raise
        except ReportOutputFormatError:
            fallback = build_fallback_report(state, chunks)
            return _apply_answer_state_overrides(fallback, chunks)


def _average_score(feedbacks: list) -> int | None:
    return aggregate_report_coverage(feedbacks).overall_score


def _average_dimension_scores(feedbacks: list) -> DimensionScores:
    return aggregate_report_coverage(feedbacks).overall_dimension_scores
