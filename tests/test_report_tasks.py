from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    InterviewFeedback,
    InterviewReport,
    ReportGenerationTimeout,
)
from app.services.report_tasks import generate_report_for_session
from app.services.session import InterviewSessionStore


class ReportLLM:
    def __init__(self, report_score: int = 81, should_timeout: bool = False) -> None:
        self.report_score = report_score
        self.should_timeout = should_timeout

    def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
        raise AssertionError("Report task tests do not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please continue."

    def generate_report(
        self,
        plan: InterviewPlan,
        chunks: list[dict],
        session_id: str,
    ) -> InterviewReport:
        if self.should_timeout:
            raise ReportGenerationTimeout("report generation timed out")
        return InterviewReport(
            session_id=session_id,
            overall_score=self.report_score,
            summary="Strong backend fundamentals.",
            highlights=["Explained tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a project.",
                    user_answer="I built a cache service.",
                    score=self.report_score,
                    critique="Needs sharper metrics.",
                    better_answer="I reduced p95 latency with Redis and fallback.",
                )
            ],
        )


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce a project.",
                focus="project depth",
            )
        ],
    )


def finish_session(store: InterviewSessionStore, session_id: str) -> None:
    state = store.get(session_id)
    state["messages"].append(
        {
            "role": "candidate",
            "content": "I built a cache service.",
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)


def test_generate_report_for_session_saves_completed_report():
    store = InterviewSessionStore(llm=ReportLLM(report_score=81))
    session = store.start(make_plan())
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report.overall_score == 81
    assert record.report.feedbacks[0].question_id == "q1"


def test_generate_report_for_session_saves_failed_record_on_timeout():
    store = InterviewSessionStore(llm=ReportLLM(should_timeout=True))
    session = store.start(make_plan())
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert record.error == "report generation timed out"
    assert record.report is None


def test_generate_report_for_session_returns_when_session_is_missing():
    store = InterviewSessionStore(llm=ReportLLM())

    generate_report_for_session("missing", store)
