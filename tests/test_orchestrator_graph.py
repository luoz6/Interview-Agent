from app.agents.orchestrator import OrchestratorAgent
from app.graphs.interview_state import build_initial_state
from app.services.agent_runtime import AgentExecutionRunner
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    KnowledgeBindingSnapshot,
    PrepContext,
    PrepQuestionHint,
)
from app.services.report import InterviewReport


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
                focus="redis",
            ),
        ],
    )


def make_correlated_plan():
    plan = make_plan()
    plan.prep_context = PrepContext(
        schema_version="v2",
        summary="Grounded interview plan.",
        knowledge_status="completed",
        question_hints=[
            PrepQuestionHint(question_id="q1", evidence_ids=["evidence-redis"])
        ],
        binding_snapshot=KnowledgeBindingSnapshot(
            prep_run_id="prep-orchestrator-1",
            corpus_manifest_sha256="a" * 64,
            status="completed",
        ),
    )
    return plan


def make_state():
    return build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )


def make_correlated_state():
    return build_initial_state(
        session_id="s1",
        plan=make_correlated_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )


class CapturingRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("not used")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the cache invalidation strategy."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please explain the cache invalidation strategy."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("not used")


def test_orchestrator_answer_delegates_to_interview_phase():
    agent = OrchestratorAgent(llm=FakeLLM())

    updated = agent.apply_command(
        make_state(),
        {"kind": "answer", "answer": "I used Redis to cache hot records."},
    )

    assert updated["phase"] == "interview"
    assert updated["pending_output"] == "Please explain the cache invalidation strategy."
    assert updated["state_version"] == 1


def test_orchestrator_trace_records_current_command_and_plan_correlation():
    recorder = CapturingRecorder()
    agent = OrchestratorAgent(
        llm=FakeLLM(),
        execution_runner=AgentExecutionRunner(recorder=recorder),
    )
    state = make_correlated_state()
    state["last_command_id"] = "cmd-previous"

    agent.apply_command(
        state,
        {
            "kind": "answer",
            "answer": "I used Redis.",
            "command_id": "cmd-current",
        },
    )

    by_agent = {record.agent: record for record in recorder.records}
    assert set(by_agent) == {"examiner", "orchestrator"}
    for record in by_agent.values():
        assert record.correlation_id == "prep-orchestrator-1"
        assert record.command_id == "cmd-current"
        assert record.causation_id == "cmd-current"
        assert record.state_version == 1
    assert by_agent["orchestrator"].operation == "answer"
    assert by_agent["orchestrator"].evidence_ids == ["evidence-redis"]


def test_orchestrator_finish_promotes_review_phase():
    agent = OrchestratorAgent(llm=FakeLLM())

    finished = agent.apply_command(make_state(), {"kind": "finish"})

    assert finished["status"] == "finished"
    assert finished["phase"] == "review"
    assert finished["phase_status"] == "active"
    assert finished["review_status"] == "processing"


def test_orchestrator_sync_review_completed_marks_phase_complete():
    agent = OrchestratorAgent(llm=FakeLLM())
    finished = agent.apply_command(make_state(), {"kind": "finish"})

    completed = agent.apply_command(
        finished,
        {"kind": "sync_review", "report_status": "completed"},
    )

    assert completed["phase"] == "review"
    assert completed["phase_status"] == "completed"
    assert completed["review_status"] == "completed"
