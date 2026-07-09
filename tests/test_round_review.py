import sys
import types

try:
    import celery  # noqa: F401
except ModuleNotFoundError:
    fake_celery = types.ModuleType("celery")

    class Celery:
        def __init__(self, *args, **kwargs):
            self.conf = {}

        def task(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    fake_celery.Celery = Celery
    sys.modules["celery"] = fake_celery

from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
from app.services.round_review import build_single_question_review_state
from app.services.round_review_runner import run_round_review_event
from app.services.round_review_tasks import run_closed_round_review
from app.services.runtime_domain_events import RoundClosedEvent


def make_plan():
    return InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            ),
            InterviewQuestion(
                id="q2",
                kind="system-design",
                prompt="Design the service.",
                focus="system design",
            ),
        ],
    )


def make_state():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )
    state["messages"].append(
        {
            "role": "candidate",
            "content": "I delete cache after the database update.",
            "question_id": "q1",
        }
    )
    state["messages"].append(
        {
            "role": "interviewer",
            "content": "Explain Redis.",
            "question_id": "q2",
        }
    )
    state["current_index"] = 1
    return state


def test_build_single_question_review_state_filters_other_questions():
    review_state = build_single_question_review_state(make_state(), "q1")

    assert review_state["status"] == "finished"
    assert review_state["current_index"] == 1
    assert len(review_state["plan"].questions) == 1
    assert review_state["plan"].questions[0].id == "q1"
    assert all(message["question_id"] == "q1" for message in review_state["messages"])


def test_build_single_question_review_state_restores_prompt_as_first_message():
    state = make_state()
    state["messages"] = [
        {
            "role": "candidate",
            "content": "I delete cache after the database update.",
            "question_id": "q1",
        },
        {
            "role": "interviewer",
            "content": "How do you handle race conditions?",
            "question_id": "q1",
        },
        {
            "role": "interviewer",
            "content": "Design the service.",
            "question_id": "q2",
        },
    ]

    review_state = build_single_question_review_state(state, "q1")

    assert review_state["messages"][0] == {
        "role": "interviewer",
        "content": "Explain Redis cache invalidation.",
        "question_id": "q1",
    }
    assert all(message["question_id"] == "q1" for message in review_state["messages"])


def make_round_closed_event(answer_state: str = "answered") -> RoundClosedEvent:
    return RoundClosedEvent(
        session_id="s1",
        question_id="q1",
        answer_state=answer_state,
        job_tags=["python", "redis"],
    )


def test_run_round_review_event_saves_completed_question_evaluation(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.llm = object()
            self.saved = []

        def get(self, session_id: str):
            assert session_id == "s1"
            return make_state()

        def upsert_question_evaluation(self, session_id: str, record):
            self.saved.append((session_id, record))

    class FakeAgent:
        def __init__(self, *, llm, vector_store):
            self.llm = llm
            self.vector_store = vector_store

        def evaluate(self, state, on_progress=None):
            return InterviewReport(
                session_id="s1",
                overall_score=90,
                overall_dimension_scores=make_dimension_scores(90),
                summary="ignored",
                highlights=["ignored"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Explain Redis cache invalidation.",
                        user_answer="I delete cache after the database update.",
                        answer_state="answered",
                        score=90,
                        dimension_scores=make_dimension_scores(90),
                        rationale="Good invalidation sequence.",
                        critique="Needs race-condition handling.",
                        better_answer="Mention delayed double delete and retry.",
                        references=[],
                    )
                ],
            )

    store = FakeStore()

    record = run_round_review_event(
        make_round_closed_event(),
        store=store,
        llm=store.llm,
        vector_store=object(),
        reviewer_factory=FakeAgent,
    )

    assert record.status == "completed"
    assert record.question_id == "q1"
    assert record.feedback.score == 90
    assert store.saved == [("s1", record)]


def test_run_round_review_event_saves_failed_record_when_reviewer_raises():
    class FakeStore:
        def __init__(self):
            self.llm = object()
            self.saved = []

        def get(self, session_id: str):
            return make_state()

        def upsert_question_evaluation(self, session_id: str, record):
            self.saved.append((session_id, record))

    class FailingAgent:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            raise RuntimeError("review model unavailable")

    store = FakeStore()

    record = run_round_review_event(
        make_round_closed_event(answer_state="skipped"),
        store=store,
        llm=store.llm,
        vector_store=object(),
        reviewer_factory=FailingAgent,
    )

    assert record == store.saved[0][1]
    assert record.status == "failed"
    assert record.session_id == "s1"
    assert record.question_id == "q1"
    assert record.answer_state == "skipped"
    assert record.feedback is None
    assert "review model unavailable" in record.error


def test_run_round_review_event_selects_matching_feedback_when_report_has_extra_feedback():
    class FakeStore:
        def __init__(self):
            self.llm = object()
            self.saved = []

        def get(self, session_id: str):
            return make_state()

        def upsert_question_evaluation(self, session_id: str, record):
            self.saved.append((session_id, record))

    class FakeAgent:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            return InterviewReport(
                session_id="s1",
                overall_score=82,
                overall_dimension_scores=make_dimension_scores(82),
                summary="ignored",
                highlights=["ignored"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q2",
                        question_text="Design the service.",
                        user_answer="Wrong feedback first.",
                        answer_state="answered",
                        score=10,
                        dimension_scores=make_dimension_scores(10),
                        rationale="Wrong question.",
                        critique="Wrong question.",
                        better_answer="Wrong question.",
                        references=[],
                    ),
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Explain Redis cache invalidation.",
                        user_answer="I delete cache after the database update.",
                        answer_state="answered",
                        score=82,
                        dimension_scores=make_dimension_scores(82),
                        rationale="Matched the closed round.",
                        critique="Needs race-condition detail.",
                        better_answer="Add delayed double delete and retry details.",
                        references=[],
                    ),
                ],
            )

    store = FakeStore()

    record = run_round_review_event(
        make_round_closed_event(),
        store=store,
        llm=store.llm,
        vector_store=object(),
        reviewer_factory=FakeAgent,
    )

    assert record.question_id == "q1"
    assert record.feedback.score == 82
    assert record.feedback.rationale == "Matched the closed round."


def test_run_closed_round_review_delegates_to_runner(monkeypatch):
    calls = []

    def fake_runner(payload):
        calls.append(payload)
        return QuestionEvaluationRecord(
            session_id=payload["session_id"],
            question_id=payload["question_id"],
            answer_state=payload["answer_state"],
            status="failed",
            error="delegated",
        )

    monkeypatch.setattr(
        "app.services.round_review_tasks.run_round_review_event_payload",
        fake_runner,
    )

    run_closed_round_review(
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "answered",
            "job_tags": ["python", "redis"],
            "emitted_at": "2026-07-09T00:00:00Z",
        }
    )

    assert calls == [
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "answered",
            "job_tags": ["python", "redis"],
            "emitted_at": "2026-07-09T00:00:00Z",
        }
    ]


def test_run_closed_round_review_saves_one_question_evaluation(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.llm = object()
            self.saved = []

        def get(self, session_id: str):
            assert session_id == "s1"
            return make_state()

        def upsert_question_evaluation(self, session_id: str, record):
            self.saved.append((session_id, record))

    class FakeAgent:
        def __init__(self, *, llm, vector_store):
            self.llm = llm
            self.vector_store = vector_store

        def evaluate(self, state, on_progress=None):
            return InterviewReport(
                session_id="s1",
                overall_score=88,
                overall_dimension_scores=make_dimension_scores(88),
                summary="ignored",
                highlights=["ignored"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Explain Redis cache invalidation.",
                        user_answer="I delete cache after the database update.",
                        answer_state="answered",
                        score=88,
                        dimension_scores=make_dimension_scores(88),
                        rationale="Answered with cache invalidation timing.",
                        critique="Needs race-condition detail.",
                        better_answer="Add delayed double delete and retry details.",
                        references=[],
                    )
                ],
            )

    store = FakeStore()
    monkeypatch.setattr(
        "app.services.round_review_runner.get_session_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "app.services.round_review_runner.get_knowledge_store",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.services.round_review_runner.resolve_runtime_llm",
        lambda store: store.llm,
    )
    monkeypatch.setattr("app.services.round_review_runner.ShadowReviewerAgent", FakeAgent)

    run_closed_round_review(
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "answered",
            "job_tags": ["python", "redis"],
            "emitted_at": "2026-07-08T00:00:00Z",
        }
    )

    assert len(store.saved) == 1
    assert store.saved[0][0] == "s1"
    assert store.saved[0][1].question_id == "q1"
    assert store.saved[0][1].answer_state == "answered"
    assert store.saved[0][1].feedback.score == 88


def test_run_closed_round_review_uses_event_answer_state(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.llm = object()
            self.saved = []

        def get(self, session_id: str):
            assert session_id == "s1"
            return make_state()

        def upsert_question_evaluation(self, session_id: str, record):
            self.saved.append(record)

    class FakeAgent:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            return InterviewReport(
                session_id="s1",
                overall_score=72,
                overall_dimension_scores=make_dimension_scores(72),
                summary="ignored",
                highlights=["ignored"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Explain Redis cache invalidation.",
                        user_answer="I answered before skipping.",
                        answer_state="answered",
                        score=72,
                        dimension_scores=make_dimension_scores(72),
                        rationale="LLM saw an answer.",
                        critique="Skipped state should come from the event.",
                        better_answer="Handle skipped-state metadata explicitly.",
                        references=[],
                    )
                ],
            )

    store = FakeStore()
    monkeypatch.setattr(
        "app.services.round_review_runner.get_session_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "app.services.round_review_runner.get_knowledge_store",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.services.round_review_runner.resolve_runtime_llm",
        lambda store: store.llm,
    )
    monkeypatch.setattr("app.services.round_review_runner.ShadowReviewerAgent", FakeAgent)

    run_closed_round_review(
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "skipped",
            "job_tags": ["python", "redis"],
            "emitted_at": "2026-07-08T00:00:00Z",
        }
    )

    assert store.saved[0].answer_state == "skipped"
    assert store.saved[0].feedback.answer_state == "answered"


def make_dimension_scores(score: int) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )
