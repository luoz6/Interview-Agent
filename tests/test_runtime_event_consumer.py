from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
)
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.runtime_event_consumer import consume_round_review_event
from tests.test_round_review import make_state


def make_event() -> RoundClosedEvent:
    return RoundClosedEvent(
        event_id="event-1",
        session_id="s1",
        correlation_id="prep-1",
        causation_id="cmd-1",
        state_version=3,
        question_id="q1",
        answer_state="answered",
        job_tags=["python", "redis"],
    )


class FakeControl:
    def __init__(self, claim_status: str):
        self.claim_status = claim_status
        self.atomic_completions = []
        self.retries = []
        self.dead_letters = []

    def claim_receipt(self, event, **kwargs):
        payload = {
            "claim_status": self.claim_status,
            "attempt_count": 1,
            "max_attempts": 5,
        }
        if self.claim_status in {"active", "retry_wait"}:
            payload["countdown_seconds"] = 12
        return payload

    def complete_round_review(
        self,
        event_id,
        consumer_name,
        worker_id,
        record,
    ):
        self.atomic_completions.append(
            (event_id, consumer_name, record.question_id)
        )

    def mark_receipt_retrying(self, *args, **kwargs):
        self.retries.append((args, kwargs))

    def fail_round_review(self, *args, **kwargs):
        self.dead_letters.append((args, kwargs))


class FakeStore:
    def __init__(self):
        self.get_calls = 0

    def get(self, session_id):
        self.get_calls += 1
        return make_state()


class CountingReviewerFactory:
    def __init__(self):
        self.calls = 0

    def __call__(self, *, llm, vector_store):
        self.calls += 1
        return SuccessfulReviewer()


class SuccessfulReviewer:
    last_retrieval_by_question = {}

    def evaluate(self, state, on_progress=None):
        scores = DimensionScores(
            breadth=80,
            depth=80,
            architecture=80,
            engineering=80,
            communication=80,
        )
        return InterviewReport(
            session_id="s1",
            overall_score=80,
            overall_dimension_scores=scores,
            summary="Completed review.",
            highlights=["Durable delivery"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Explain Redis cache invalidation.",
                    user_answer="I delete cache after the database update.",
                    answer_state="answered",
                    score=80,
                    dimension_scores=scores,
                    rationale="Explained the sequence.",
                    critique="Add race handling.",
                    better_answer="Use versioning and delayed deletion.",
                    references=[],
                )
            ],
        )


def test_completed_receipt_skips_reviewer():
    control = FakeControl("completed")
    reviewer = CountingReviewerFactory()
    store = FakeStore()

    outcome = consume_round_review_event(
        make_event(),
        control_store=control,
        worker_id="consumer-1",
        store=store,
        reviewer_factory=reviewer,
    )

    assert outcome.status == "duplicate_completed"
    assert reviewer.calls == 0
    assert store.get_calls == 0


def test_active_lease_reschedules():
    outcome = consume_round_review_event(
        make_event(),
        control_store=FakeControl("active"),
        worker_id="consumer-2",
    )

    assert outcome.status == "reschedule"
    assert outcome.countdown_seconds == 12


def test_result_and_receipt_complete_atomically():
    control = FakeControl("claimed")

    outcome = consume_round_review_event(
        make_event(),
        control_store=control,
        worker_id="consumer-3",
        store=FakeStore(),
        llm=object(),
        vector_store=object(),
        reviewer_factory=CountingReviewerFactory(),
    )

    assert outcome.status == "completed"
    assert control.atomic_completions == [
        ("event-1", "round_review", "q1")
    ]
