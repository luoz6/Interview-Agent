from pydantic import BaseModel

from app.graphs.interview_state import InterviewState
from app.services.llm import InterviewLLM
from app.services.prep import InterviewQuestion
from app.services.report import (
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
)


class EvaluationChunk(BaseModel):
    question_id: str
    question_text: str
    focus: str
    messages: list[dict[str, str]]


class ShadowEvaluator:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._llm = llm

    def evaluate(self, state: InterviewState) -> InterviewReport:
        chunks = build_evaluation_chunks(state)
        try:
            if self._llm is None:
                raise ReportGenerationFailed("report llm is not configured")
            return self._llm.generate_report(
                plan=state["plan"],
                chunks=[chunk.model_dump() for chunk in chunks],
                session_id=state["session_id"],
            )
        except ReportGenerationTimeout:
            raise
        except ReportGenerationFailed:
            raise
        except (TypeError, ValueError):
            return build_fallback_report(state, chunks)


def build_evaluation_chunks(state: InterviewState) -> list[EvaluationChunk]:
    return [
        EvaluationChunk(
            question_id=question.id,
            question_text=question.prompt,
            focus=question.focus,
            messages=_messages_for_question(state, question),
        )
        for question in state["plan"].questions
    ]


def build_fallback_report(
    state: InterviewState,
    chunks: list[EvaluationChunk] | None = None,
) -> InterviewReport:
    chunks = chunks if chunks is not None else build_evaluation_chunks(state)
    return InterviewReport(
        session_id=state["session_id"],
        overall_score=60,
        summary=(
            "AI evaluation could not generate a complete report. "
            "Review the original answers manually."
        ),
        highlights=["Completed the mock interview"],
        is_fallback=True,
        feedbacks=[
            InterviewFeedback(
                question_id=chunk.question_id,
                question_text=chunk.question_text,
                user_answer=_summarize_candidate_answers(chunk),
                score=60,
                critique="AI evaluation could not parse stable feedback for this question.",
                better_answer=(
                    "Rebuild the answer around context, task, action, and result, "
                    "then add concrete technical tradeoffs."
                ),
            )
            for chunk in chunks
        ],
    )


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
    answers = [
        message["content"].strip()
        for message in chunk.messages
        if message["role"] == "candidate" and message["content"].strip()
    ]
    if not answers:
        return "No candidate answer was recorded for this question."
    return " ".join(answers)
