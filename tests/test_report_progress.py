from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import ReportProgress
from app.services.session import InterviewSessionStore


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="Backend interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain Redis cache invalidation.",
                    focus="Redis reliability",
                )
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please continue."

    def generate_report(self, plan, evaluation_items, session_id):
        raise AssertionError


def test_update_report_progress_updates_processing_record():
    store = InterviewSessionStore(llm=FakeLLM())
    session = store.start(
        FakeLLM().generate_plan("Backend role", "Backend resume"),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )
    state = store.get(session.session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)
    store.mark_report_processing(session.session_id)

    store.update_report_progress(
        session.session_id,
        ReportProgress(
            stage="analyzing",
            percent=60,
            message="Analyzing Redis depth.",
            current_question_id="q1",
        ),
    )

    record = store.get_report_record(session.session_id)
    assert record.progress.stage == "analyzing"
    assert record.progress.percent == 60
