import logging

import pytest

from app.services.llm import OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
)
from app.services.report_tasks import generate_report_for_session, run_report_generation
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
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        if self.should_timeout:
            raise ReportGenerationTimeout("report generation timed out")
        return InterviewReport(
            session_id=session_id,
            overall_score=self.report_score,
            overall_dimension_scores=make_dimension_scores(self.report_score),
            summary="Strong backend fundamentals.",
            highlights=["Explained tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a project.",
                    user_answer="I built a cache service.",
                    score=self.report_score,
                    dimension_scores=make_dimension_scores(self.report_score),
                    rationale="The answer showed practical implementation detail.",
                    critique="Needs sharper metrics.",
                    better_answer="I reduced p95 latency with Redis and fallback.",
                    references=[],
                )
            ],
        )


class FallbackReportLLM(ReportLLM):
    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        return InterviewReport(
            session_id=session_id,
            overall_score=60,
            overall_dimension_scores=make_dimension_scores(60),
            summary="Evidence was insufficient for a grounded expert report.",
            highlights=["Completed the mock interview"],
            is_fallback=True,
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a project.",
                    user_answer="I built a cache service.",
                    score=60,
                    dimension_scores=make_dimension_scores(60),
                    rationale="Fallback report generated because grounded evidence was insufficient.",
                    critique="Needs sharper metrics.",
                    better_answer="I reduced p95 latency with Redis and fallback.",
                    references=[],
                )
            ],
        )


class FailingStructuredModel:
    def invoke(self, prompt: str):
        raise RuntimeError(
            "Error code: 400 - {'error': {'message': 'This response_format type is unavailable now'}}"
        )


class FakeJsonMessage:
    def __init__(self, content: str):
        self.content = content


class WrappedJsonFallbackChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            Final answer:
            {
              "session_id": "s1",
              "overall_score": 88,
              "overall_dimension_scores": {
                "breadth": 88,
                "depth": 88,
                "architecture": 88,
                "engineering": 88,
                "communication": 88
              },
              "summary": "Clear backend tradeoff explanation.",
              "highlights": ["Explained Redis consistency"],
              "feedbacks": [
                {
                  "question_id": "q1",
                  "question_text": "Introduce a project.",
                  "user_answer": "I built a cache service.",
                  "score": 88,
                  "dimension_scores": {
                    "breadth": 88,
                    "depth": 88,
                    "architecture": 88,
                    "engineering": 88,
                    "communication": 88
                  },
                  "rationale": "The answer showed practical implementation detail.",
                  "critique": "Needs sharper metrics.",
                  "better_answer": "I reduced p95 latency with Redis and fallback.",
                  "references": []
                }
              ],
              "status": "completed",
              "is_fallback": false
            }
            """
        )


class InvalidJsonFallbackChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage('{"session_id":"s1","overall_score":"bad"}')


def make_dimension_scores(score: int = 81) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
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


def start_session(store: InterviewSessionStore):
    return store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
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


class FakeStore:
    def __init__(self, state: dict):
        self._state = state
        self.progress_updates: list[object] = []
        self.saved_report = None
        self.failed_error = None

    def get(self, session_id: str):
        assert session_id == self._state["session_id"]
        return self._state

    def update_report_progress(self, session_id: str, progress) -> None:
        assert session_id == self._state["session_id"]
        self.progress_updates.append(progress)

    def save_report(self, session_id: str, report) -> None:
        assert session_id == self._state["session_id"]
        self.saved_report = report

    def fail_report(self, session_id: str, error: str) -> None:
        assert session_id == self._state["session_id"]
        self.failed_error = error


def make_finished_state(session_id: str = "s1") -> dict:
    return {
        "session_id": session_id,
        "status": "finished",
        "plan": make_plan(),
        "messages": [
            {
                "role": "candidate",
                "content": "I built a cache service.",
                "question_id": "q1",
            }
        ],
        "job_description": "Backend role using Python and Redis.",
        "resume_text": "Built a Python API with Redis.",
        "job_tags": ["python", "redis"],
    }


def test_generate_report_for_session_saves_completed_report():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=ReportLLM(report_score=81))
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report.overall_score == 81
    assert record.report.feedbacks[0].question_id == "q1"


def test_run_report_generation_returns_report_and_persists_side_effects():
    class FakeVectorStore:
        def __init__(self) -> None:
            self.search_calls = []

        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            self.search_calls.append(
                {
                    "query_text": query_text,
                    "job_tags": job_tags,
                    "source_types": source_types,
                    "limit": limit,
                }
            )
            return []

    store = FakeStore(make_finished_state())
    llm = ReportLLM(report_score=88)
    vector_store = FakeVectorStore()

    report = run_report_generation(
        session_id="s1",
        store=store,
        llm=llm,
        vector_store=vector_store,
    )

    assert report.session_id == "s1"
    assert report.overall_score == 88
    assert store.saved_report is report
    assert store.failed_error is None
    assert store.progress_updates
    assert vector_store.search_calls


def test_run_report_generation_marks_failed_status_when_execution_raises():
    class ExplodingLLM(ReportLLM):
        def generate_report(
            self,
            plan: InterviewPlan,
            evaluation_items: list[dict],
            session_id: str,
        ) -> InterviewReport:
            raise ReportGenerationFailed("llm exploded")

    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = FakeStore(make_finished_state())

    report = run_report_generation(
        session_id="s1",
        store=store,
        llm=ExplodingLLM(),
        vector_store=FakeVectorStore(),
    )

    assert report is None
    assert store.saved_report is None
    assert store.failed_error == "llm exploded"


def test_generate_report_for_session_saves_failed_record_on_timeout():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=ReportLLM(should_timeout=True))
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert record.error == "report generation timed out"
    assert record.report is None


def test_generate_report_for_session_saves_failed_record_when_retrieval_is_unavailable():
    class FailingVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            raise RuntimeError("db down")

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FailingVectorStore()
    store = InterviewSessionStore(llm=ReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert record.error == "pgvector knowledge store is unavailable"
    assert record.report is None


def test_generate_report_for_session_saves_completed_fallback_when_evidence_is_insufficient():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=FallbackReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report is not None
    assert record.report.is_fallback is True
    assert record.report.summary == "Evidence was insufficient for a grounded expert report."
    assert record.report.feedbacks[0].references == []


def test_generate_report_for_session_returns_when_session_is_missing():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=ReportLLM())

    generate_report_for_session("missing", store)


def test_run_report_generation_saves_grounded_report_when_raw_json_path_is_valid():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore(
        llm=OpenAIInterviewLLM(chat_model=WrappedJsonFallbackChatModel())
    )
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = run_report_generation(
        session_id=session.session_id,
        store=store,
        llm=store.llm,
        vector_store=FakeVectorStore(),
    )

    assert report is not None
    assert report.is_fallback is False
    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report is report


def test_run_report_generation_saves_fallback_completed_report_when_raw_json_is_invalid(
    caplog: pytest.LogCaptureFixture,
):
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore(
        llm=OpenAIInterviewLLM(chat_model=InvalidJsonFallbackChatModel())
    )
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    with caplog.at_level(logging.WARNING):
        report = run_report_generation(
            session_id=session.session_id,
            store=store,
            llm=store.llm,
            vector_store=FakeVectorStore(),
        )

    record = store.get_report_record(session.session_id)
    assert report is not None
    assert record.status == "completed"
    assert record.report is not None
    assert record.report.is_fallback is True
    assert any(
        record.levelno == logging.WARNING
        and record.message == "Falling back to heuristic interview report"
        and getattr(record, "session_id", None) == session.session_id
        and getattr(record, "question_count", None) == 1
        for record in caplog.records
    )
