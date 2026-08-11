from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion


class FakeReviewWorkflowStore:
    def __init__(self):
        self.initialized = []
        self.failed = []
        self.retries = []

    def initialize_run(self, **kwargs):
        self.initialized.append(kwargs)

    def reusable_question_ids(self, *_):
        return []

    def fail_review(self, *args):
        self.failed.append(args)

    def schedule_retry(self, **kwargs):
        self.retries.append(kwargs)


def round_review_plan():
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


def round_review_state():
    state = build_initial_state(
        session_id="s1",
        plan=round_review_plan(),
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
