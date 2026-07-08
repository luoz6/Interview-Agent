from pydantic import BaseModel

from app.graphs.interview_state import InterviewState
from app.services.llm import InterviewLLM
from app.services.prep import InterviewQuestion
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
)


class EvaluationChunk(BaseModel):
    question_id: str
    question_text: str
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
            focus=question.focus,
            answer_state=_answer_state_for_question(state, question),
            messages=_messages_for_question(state, question),
        )
        for question in state["plan"].questions
    ]


def _default_dimension_scores(score: int = 60) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def build_fallback_report(
    state: InterviewState,
    chunks: list[EvaluationChunk] | None = None,
) -> InterviewReport:
    chunks = chunks if chunks is not None else build_evaluation_chunks(state)
    return InterviewReport(
        session_id=state["session_id"],
        overall_score=60,
        overall_dimension_scores=_default_dimension_scores(),
        summary=(
            "AI 评估未能生成完整报告，请结合原始回答继续复盘。"
        ),
        highlights=["已完成本次模拟面试"],
        is_fallback=True,
        feedbacks=[
            InterviewFeedback(
                question_id=chunk.question_id,
                question_text=chunk.question_text,
                user_answer=_summarize_candidate_answers(chunk),
                answer_state=chunk.answer_state,
                score=60,
                dimension_scores=_default_dimension_scores(),
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
        ],
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
        feedbacks.append(_empty_answer_feedback(chunk))
    return report.model_copy(
        update={
            "feedbacks": feedbacks,
            "overall_score": _average_score(feedbacks),
            "overall_dimension_scores": _average_dimension_scores(feedbacks),
        }
    )


def _empty_answer_feedback(chunk: EvaluationChunk) -> InterviewFeedback:
    skipped = chunk.answer_state == "skipped"
    return InterviewFeedback(
        question_id=chunk.question_id,
        question_text=chunk.question_text,
        user_answer=(
            "候选人跳过了这道题。"
            if skipped
            else "这道题没有记录到候选人的有效作答。"
        ),
        answer_state=chunk.answer_state,
        score=0,
        dimension_scores=_default_dimension_scores(0),
        rationale=(
            "候选人跳过了这道题。"
            if skipped
            else "这道题没有记录到候选人的有效作答。"
        ),
        critique="当前没有可评估的候选人回答。",
        better_answer="请补充题目背景、关键动作、技术取舍和量化结果。",
        references=[],
    )


def _average_score(feedbacks: list[InterviewFeedback]) -> int:
    if not feedbacks:
        return 0
    return round(sum(feedback.score for feedback in feedbacks) / len(feedbacks))


def _average_dimension_scores(feedbacks: list[InterviewFeedback]) -> DimensionScores:
    if not feedbacks:
        return _default_dimension_scores(0)
    fields = DimensionScores.model_fields.keys()
    values = {
        field: round(
            sum(getattr(feedback.dimension_scores, field) for feedback in feedbacks)
            / len(feedbacks)
        )
        for field in fields
    }
    return DimensionScores(**values)


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
        return "这道题没有记录到候选人的有效作答。"
    return " ".join(answers)
