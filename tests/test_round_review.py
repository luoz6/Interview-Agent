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
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
from app.services.round_review import build_single_question_review_state
from app.services.round_review_tasks import run_closed_round_review


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
        "app.services.round_review_tasks.get_session_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "app.services.round_review_tasks.get_knowledge_store",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.services.round_review_tasks.resolve_runtime_llm",
        lambda store: store.llm,
    )
    monkeypatch.setattr("app.services.round_review_tasks.ShadowReviewerAgent", FakeAgent)

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
        "app.services.round_review_tasks.get_session_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "app.services.round_review_tasks.get_knowledge_store",
        lambda: object(),
    )
    monkeypatch.setattr(
        "app.services.round_review_tasks.resolve_runtime_llm",
        lambda store: store.llm,
    )
    monkeypatch.setattr("app.services.round_review_tasks.ShadowReviewerAgent", FakeAgent)

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
