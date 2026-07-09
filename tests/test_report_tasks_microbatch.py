from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import InterviewReport
from app.services.report_microbatch import MicrobatchReportUnavailable
from app.services.report_tasks import execute_report_generation
from app.services.session import InterviewSessionStore
from tests.report_microbatch_fixtures import (
    make_dimension_scores,
    make_feedback,
    make_single_question_plan,
)


def start_finished_session(store: InterviewSessionStore):
    turn = store.start(
        make_single_question_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a FastAPI Redis service.",
        job_tags=["python", "redis"],
    )
    state = store.get(turn.session_id)
    state["messages"].append(
        {
            "role": "candidate",
            "content": "I built a FastAPI Redis service.",
            "question_id": "q1",
        }
    )
    state["current_index"] = 1
    state["status"] = "finished"
    store.mark_report_processing(turn.session_id)
    return turn


class CapturingReportLLM:
    def __init__(self):
        self.evaluation_items = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("not used")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError("not used")

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str):
        self.evaluation_items = evaluation_items
        return InterviewReport(
            session_id=session_id,
            overall_score=99,
            overall_dimension_scores=make_dimension_scores(99),
            summary="\u6700\u7ec8\u62a5\u544a\u590d\u7528\u4e86\u9010\u9898\u5fae\u6279\u53cd\u9988\u3002",
            highlights=["\u9010\u9898\u5fae\u6279\u53cd\u9988\u5df2\u590d\u7528"],
            feedbacks=[make_feedback(question_id="q1", score=11)],
        )


class ExplodingFullSessionReviewer:
    def __init__(self, *, llm, vector_store):
        raise AssertionError("full-session ShadowReviewerAgent should not be used")


class FullSessionReviewer:
    def __init__(self, *, llm, vector_store):
        self.llm = llm
        self.vector_store = vector_store

    def evaluate(self, state, on_progress=None):
        return InterviewReport(
            session_id=state["session_id"],
            overall_score=71,
            overall_dimension_scores=make_dimension_scores(71),
            summary="\u56de\u9000\u5230\u6574\u573a Shadow Reviewer \u62a5\u544a\u3002",
            highlights=["\u6574\u573a\u8bc4\u4f30\u5668\u5df2\u4f7f\u7528"],
            feedbacks=[make_feedback(question_id="q1", score=71)],
        )


def test_execute_report_generation_reuses_completed_microbatch_without_full_session_reviewer(monkeypatch):
    import app.services.report_tasks as report_tasks

    store = InterviewSessionStore()
    turn = start_finished_session(store)
    store.upsert_question_evaluation(
        turn.session_id,
        question_evaluation_from_feedback(
            session_id=turn.session_id,
            feedback=make_feedback(question_id="q1", score=84),
        ),
    )
    llm = CapturingReportLLM()
    monkeypatch.setattr(report_tasks, "ShadowReviewerAgent", ExplodingFullSessionReviewer)

    report = execute_report_generation(
        session_id=turn.session_id,
        store=store,
        llm=llm,
        vector_store=object(),
    )

    assert report.overall_score == 84
    assert report.feedbacks[0].score == 84
    assert report.feedbacks[0].rationale == (
        "q1 \u7684\u56de\u7b54\u8986\u76d6\u4e86\u6838\u5fc3\u94fe\u8def\u548c\u4e3b\u8981\u53d6\u820d\u3002"
    )
    assert llm.evaluation_items[0]["source"] == "question_evaluation_record"
    assert llm.evaluation_items[0]["microbatch_score"] == 84
    record = store.get_report_record(turn.session_id)
    assert record.status == "completed"
    assert record.report is report
    saved = store.list_question_evaluations(turn.session_id)
    assert saved[0].feedback.score == 84
    assert saved[0].feedback.rationale == (
        "q1 \u7684\u56de\u7b54\u8986\u76d6\u4e86\u6838\u5fc3\u94fe\u8def\u548c\u4e3b\u8981\u53d6\u820d\u3002"
    )


def test_execute_report_generation_falls_back_to_full_session_when_microbatch_unavailable(monkeypatch):
    import app.services.report_tasks as report_tasks

    store = InterviewSessionStore()
    turn = start_finished_session(store)

    def raise_microbatch_unavailable(*args, **kwargs):
        raise MicrobatchReportUnavailable("missing q1")

    monkeypatch.setattr(
        report_tasks,
        "generate_microbatch_report",
        raise_microbatch_unavailable,
    )
    monkeypatch.setattr(report_tasks, "ShadowReviewerAgent", FullSessionReviewer)

    report = execute_report_generation(
        session_id=turn.session_id,
        store=store,
        llm=CapturingReportLLM(),
        vector_store=object(),
    )

    assert report.overall_score == 71
    assert report.summary == "\u56de\u9000\u5230\u6574\u573a Shadow Reviewer \u62a5\u544a\u3002"
    assert store.get_report_record(turn.session_id).status == "completed"
