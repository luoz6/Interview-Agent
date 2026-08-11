from __future__ import annotations

from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport
from app.services.session import InterviewSessionStore


class FakeInterviewLLM:
    def __init__(self) -> None:
        self.last_context = None
        self.should_fail_followup = False

    def generate_plan(
        self,
        job_description: str,
        resume_text: str,
    ) -> InterviewPlan:
        return InterviewPlan(
            title="LLM generated backend interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Introduce one project.",
                    focus="project",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis.",
                    focus="Redis",
                ),
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="Design a backend service.",
                    focus="system design",
                ),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        self.last_context = context
        if self.should_fail_followup:
            raise RuntimeError("llm failed")
        return (
            "You mentioned caching. Please explain how you protect the database "
            "when the cache becomes invalid."
        )

    def stream_followup(self, context: list[dict[str, str]]):
        self.last_context = context
        if self.should_fail_followup:
            raise RuntimeError("llm failed")
        yield "You mentioned caching. "
        yield "Please explain how you protect the database "
        yield "when the cache becomes invalid."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Session store tests do not generate reports")


class NoopInterviewLLM:
    def generate_followup(self, context):
        return "follow-up"


def make_interview_plan() -> InterviewPlan:
    return FakeInterviewLLM().generate_plan("Backend role", "Backend resume")


def make_deletion_session_store(
    *,
    session_id: str | None = None,
) -> tuple[InterviewSessionStore, str]:
    store = InterviewSessionStore(llm=NoopInterviewLLM())
    turn = store.start(
        InterviewPlan(
            title="Deletion test",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain retries.",
                    focus="reliability",
                )
            ],
        ),
        job_description="Backend role",
        resume_text="Built services",
        job_tags=["reliability"],
        session_id=session_id,
    )
    return store, turn.session_id
