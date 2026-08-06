from pydantic import BaseModel

from app.graphs.interview_state import InterviewState
from app.services.llm import InterviewLLM
from app.services.prep import InterviewQuestion
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    REPORT_PRESENTATION_VERSION_V2,
    REPORT_SCHEMA_VERSION_V2,
    ReportCoverageV2,
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
    ReportTechnicalAppendixV2,
)
from app.services.report_coverage import (
    aggregate_report_coverage,
    apply_report_coverage,
    dimension_evaluations,
    populate_feedback_dimension_evaluations,
    question_evaluations,
)
from app.services.report_contract import build_report_evidence_refs
from app.services.report_observations import aggregate_report_observations
from app.services.report_summary import (
    REPORT_SUMMARY_PROMPT_SHA256,
    REPORT_SUMMARY_PROMPT_VERSION,
    build_cross_question_summary,
)
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_SHA256,
    REPORT_SCORING_RUBRIC_VERSION,
)


class EvaluationChunk(BaseModel):
    question_id: str
    question_text: str
    question_kind: str
    focus: str
    answer_state: str
    messages: list[dict[str, str]]


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


def build_evaluation_chunks(state: InterviewState) -> list[EvaluationChunk]:
    return [
        EvaluationChunk(
            question_id=question.id,
            question_text=question.prompt,
            question_kind=question.kind,
            focus=question.focus,
            answer_state=_answer_state_for_question(state, question),
            messages=_messages_for_question(state, question),
        )
        for question in state["plan"].questions
    ]


def _null_dimension_scores() -> DimensionScores:
    return DimensionScores()


def build_fallback_report(
    state: InterviewState,
    chunks: list[EvaluationChunk] | None = None,
) -> InterviewReport:
    chunks = chunks if chunks is not None else build_evaluation_chunks(state)
    feedbacks = [
        InterviewFeedback(
            question_id=chunk.question_id,
            question_text=chunk.question_text,
            user_answer=_summarize_candidate_answers(chunk),
            answer_state=chunk.answer_state,
            score=None,
            dimension_scores=_null_dimension_scores(),
            evaluation_status=(
                "insufficient_evidence"
                if chunk.answer_state == "answered"
                else "not_evaluated"
            ),
            evaluation_reason_code=(
                "scoring_generation_failed"
                if chunk.answer_state == "answered"
                else chunk.answer_state
            ),
            rationale=(
                "兜底报告：本题未能生成稳定的结构化专家评估。"
            ),
            critique="AI 评估未能解析出稳定的逐题反馈。",
            better_answer=(
                "请按背景、动作、取舍、结果四段式重构回答，并补充可量化指标。"
            ),
            references=[],
        )
        for chunk in chunks
    ]
    feedbacks = populate_feedback_dimension_evaluations(feedbacks)
    coverage = aggregate_report_coverage(feedbacks)
    report_dimension_evaluations = dimension_evaluations(coverage)
    report_evidence_refs = build_report_evidence_refs(feedbacks)
    observations = aggregate_report_observations(
        feedbacks=feedbacks,
        dimension_evaluations=report_dimension_evaluations,
        evidence_refs=report_evidence_refs,
    )
    report_coverage = ReportCoverageV2(
        status=coverage.coverage_status,
        evaluated_count=coverage.evaluated_count,
        total_eligible_count=coverage.total_eligible_count,
        evidence_count=coverage.evidence_count,
        per_dimension=report_dimension_evaluations,
    )
    summary_result = build_cross_question_summary(
        observations=observations,
        coverage=report_coverage,
        evidence_refs=report_evidence_refs,
    )
    return InterviewReport(
        session_id=state["session_id"],
        report_schema_version=REPORT_SCHEMA_VERSION_V2,
        presentation_version=REPORT_PRESENTATION_VERSION_V2,
        overall_score=None,
        overall_dimension_scores=_null_dimension_scores(),
        generation_status="degraded",
        generation_reason_code="invalid_provider_output",
        score_status="unscored",
        score_reason_code="scoring_generation_failed",
        coverage_status="none",
        evaluated_count=0,
        total_eligible_count=sum(chunk.answer_state == "answered" for chunk in chunks),
        evidence_count=0,
        dimension_evaluations=report_dimension_evaluations,
        question_evaluations=question_evaluations(feedbacks),
        coverage=report_coverage,
        summary_observations=summary_result.summary_observations,
        strengths=summary_result.strengths,
        limitations=summary_result.limitations,
        evidence_refs=report_evidence_refs,
        technical_appendix=ReportTechnicalAppendixV2(
            reason_codes=[
                "invalid_provider_output",
                coverage.score_reason_code,
            ],
            report_path="heuristic",
            observations=observations,
            summary_prompt_version=REPORT_SUMMARY_PROMPT_VERSION,
            summary_prompt_sha256=REPORT_SUMMARY_PROMPT_SHA256,
            summary_generation_mode="deterministic_fallback",
            metadata={"coverage_status": coverage.coverage_status},
        ),
        report_path="heuristic",
        scoring_rubric_version=REPORT_SCORING_RUBRIC_VERSION,
        scoring_rubric_sha256=REPORT_SCORING_RUBRIC_SHA256,
        summary=summary_result.summary,
        highlights=["已完成本次模拟面试"],
        is_fallback=True,
        feedbacks=feedbacks,
    )


def _answer_state_for_question(
    state: InterviewState,
    question: InterviewQuestion,
) -> str:
    if question.id in state.get("skipped_question_ids", []):
        return "skipped"
    has_answer = any(
        message["role"] == "candidate"
        and message["question_id"] == question.id
        and message["content"].strip()
        for message in state["messages"]
    )
    if has_answer:
        return "answered"
    return "unanswered"


def _apply_answer_state_overrides(
    report: InterviewReport,
    chunks: list[EvaluationChunk],
) -> InterviewReport:
    chunk_by_id = {chunk.question_id: chunk for chunk in chunks}
    feedbacks = []
    for feedback in report.feedbacks:
        chunk = chunk_by_id.get(feedback.question_id)
        if chunk is None or chunk.answer_state == "answered":
            feedbacks.append(feedback)
            continue
        feedbacks.append(
            build_empty_answer_feedback(
                chunk,
                references=feedback.references,
            )
        )
    return apply_report_coverage(report, feedbacks=feedbacks)


def build_empty_answer_feedback(
    chunk: EvaluationChunk,
    *,
    references=None,
) -> InterviewFeedback:
    skipped = chunk.answer_state == "skipped"
    return InterviewFeedback(
        question_id=chunk.question_id,
        question_text=chunk.question_text,
        user_answer=(
            "候选人跳过了这道题。"
            if skipped
            else "候选人未作答这道题。"
        ),
        answer_state=chunk.answer_state,
        score=None,
        dimension_scores=_null_dimension_scores(),
        evaluation_status="not_evaluated",
        evaluation_reason_code=chunk.answer_state,
        rationale=(
            "候选人跳过了这道题。"
            if skipped
            else "候选人未作答这道题。"
        ),
        critique="当前没有可评估的候选人回答。",
        better_answer="请补充题目背景、关键动作、技术取舍和量化结果。",
        references=list(references or []),
    )


def _average_score(feedbacks: list[InterviewFeedback]) -> int | None:
    return aggregate_report_coverage(feedbacks).overall_score


def _average_dimension_scores(feedbacks: list[InterviewFeedback]) -> DimensionScores:
    return aggregate_report_coverage(feedbacks).overall_dimension_scores


def _messages_for_question(
    state: InterviewState,
    question: InterviewQuestion,
) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"]
        if message["question_id"] == question.id
    ]


def _summarize_candidate_answers(chunk: EvaluationChunk) -> str:
    if chunk.answer_state == "skipped":
        return "候选人跳过了这道题。"
    answers = [
        message["content"].strip()
        for message in chunk.messages
        if message["role"] == "candidate" and message["content"].strip()
    ]
    if not answers:
        return "候选人未作答这道题。"
    return " ".join(answers)
