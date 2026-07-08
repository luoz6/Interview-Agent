from copy import deepcopy

from app.graphs.interview_graph import InterviewGraphRunner
from app.services.interview_rounds import round_closed_event_from_transition
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport
from app.services.session import finish_interview_state, skip_interview_question_state


def make_plan():
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce the project.",
                focus="project",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis.",
                focus="Redis",
            ),
        ],
    )


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the cache invalidation strategy."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please explain the cache invalidation strategy."

    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError


def make_start_kwargs():
    return {
        "session_id": "s1",
        "plan": make_plan(),
        "job_description": "Backend role using Python and Redis.",
        "resume_text": "Built a Python API with Redis.",
        "job_tags": ["python", "redis"],
    }


def test_round_closed_event_is_none_for_first_answer_followup_transition():
    runner = InterviewGraphRunner(llm=FakeLLM())
    before = runner.start(**make_start_kwargs())
    after = runner.submit_answer(before, "I improved cache consistency.")

    assert round_closed_event_from_transition(before, after) is None


def test_round_closed_event_is_emitted_when_question_advances():
    runner = InterviewGraphRunner(llm=FakeLLM())
    before = runner.start(**make_start_kwargs())
    followup_state = runner.submit_answer(before, "I improved cache consistency.")
    after = runner.submit_answer(followup_state, "I added delayed double delete.")

    event = round_closed_event_from_transition(followup_state, after)

    assert event is not None
    assert event.session_id == "s1"
    assert event.question_id == "q1"
    assert event.answer_state == "answered"
    assert event.job_tags == ["python", "redis"]


def test_round_closed_event_is_emitted_for_skip():
    runner = InterviewGraphRunner(llm=FakeLLM())
    before = runner.start(**make_start_kwargs())
    after = skip_interview_question_state(deepcopy(before))

    event = round_closed_event_from_transition(before, after)

    assert event is not None
    assert event.question_id == "q1"
    assert event.answer_state == "skipped"


def test_round_closed_event_is_emitted_for_finish_without_answer():
    runner = InterviewGraphRunner(llm=FakeLLM())
    before = runner.start(**make_start_kwargs())
    after = finish_interview_state(deepcopy(before))

    event = round_closed_event_from_transition(before, after)

    assert event is not None
    assert event.question_id == "q1"
    assert event.answer_state == "unanswered"
