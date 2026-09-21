from app.application.scheduling import (
    InMemoryExecutionStateStore,
    SchedulerApplicationCapability,
)
from app.a2a.contracts.followup import FollowupArtifactPayload
from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionDependencyDefinition,
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    TaskRuntimeState,
    UserCommand,
)


class Catalog:
    def __init__(self, capability):
        self.capability = capability

    def resolve(self, *, agent_id, skill, capability_version=None):
        if (agent_id, skill) != (
            self.capability.agent_id,
            self.capability.skill,
        ):
            return None
        return self.capability

    def validate_compatibility(self, **kwargs):
        return True


class Invoker:
    def invoke(self, *, agent_id, skill, request, execution_context=None):
        return FollowupArtifactPayload(
            question_id=request.question_id,
            followup_text="请补充关键取舍。",
            reason_code="gap",
            policy_version="adaptive_v1",
        )


def _scheduler():
    capability = CapabilityDescriptor(
        agent_id="interview-examiner",
        skill="generate-followup",
        description="Generate a follow-up.",
        request_contract_id="generate-followup-request",
        request_contract_version="v1",
        output_artifact_type="followup-artifact",
        output_artifact_version="1.0",
        capability_version="v1",
    )
    question = ExecutionTaskDefinition(
        task_id="followup-q1",
        capability="interview.followup",
        agent_id="interview-examiner",
        skill="generate-followup",
        parameters={"question_id": "q1"},
    )
    evaluation = ExecutionTaskDefinition(
        task_id="evaluate-q1",
        capability="interview.evaluation",
        agent_id="interview-examiner",
        skill="evaluate-answer",
        parameters={"state": {"answer": ""}},
    )
    plan = ExecutionPlan(
        execution_id="exec-wait",
        interview_plan_ref="plan-1",
        task_definitions=(question, evaluation),
        dependency_definitions=(
            ExecutionDependencyDefinition(
                predecessor_task_id="followup-q1",
                successor_task_id="evaluate-q1",
            ),
        ),
    )
    state = ExecutionState(
        execution_id="exec-wait",
        task_states=(
            TaskRuntimeState(task_id="followup-q1"),
            TaskRuntimeState(task_id="evaluate-q1"),
        ),
    )
    store = InMemoryExecutionStateStore(state)
    scheduler = SchedulerApplicationCapability(
        state_store=store,
        plan=plan,
        capability_port=Catalog(capability),
        invocation_port=Invoker(),
    )
    return scheduler, store


def test_scheduler_persists_wait_handle_and_accepts_only_fenced_command():
    scheduler, store = _scheduler()
    scheduler.step("exec-wait")
    waiting = scheduler.step("exec-wait")

    assert waiting.action == "WAIT_USER"
    state = waiting.state
    assert state.execution_status == "WAITING"
    assert state.current_wait_handle is not None
    wait = state.current_wait_handle
    assert wait.question_id == "q1"
    assert wait.issued_revision == state.revision

    command = UserCommand(
        command_id="cmd-1",
        execution_id="exec-wait",
        wait_id=wait.wait_id,
        task_id=wait.task_id,
        question_id=wait.question_id,
        expected_revision=wait.issued_revision,
        payload={"answer_text": "bounded queue"},
    )
    accepted = scheduler.accept_user_command("exec-wait", command)
    assert accepted.outcome.disposition == "ACCEPT"
    assert accepted.state.current_wait_handle is None
    assert accepted.state.execution_status == "RUNNING"
    assert accepted.state.latest_observation["status"] == "ANSWER_RECEIVED"
    assert store.load("exec-wait") == accepted.state


def test_wait_user_rejects_stale_wrong_and_replays_commands_deterministically():
    scheduler, _store = _scheduler()
    scheduler.step("exec-wait")
    waiting = scheduler.step("exec-wait")
    wait = waiting.state.current_wait_handle

    base = {
        "command_id": "cmd-1",
        "execution_id": "exec-wait",
        "wait_id": wait.wait_id,
        "task_id": wait.task_id,
        "question_id": wait.question_id,
        "expected_revision": wait.issued_revision,
        "payload": {"answer_text": "answer"},
    }
    stale = scheduler.accept_user_command(
        "exec-wait",
        UserCommand(**{**base, "expected_revision": wait.issued_revision - 1}),
    )
    assert stale.outcome.disposition == "STALE"

    wrong = scheduler.accept_user_command(
        "exec-wait",
        UserCommand(**{**base, "question_id": "old-question"}),
    )
    assert wrong.outcome.disposition == "REJECT"
    assert wrong.outcome.reason_code == "wrong_question"

    accepted = scheduler.accept_user_command("exec-wait", UserCommand(**base))
    assert accepted.outcome.disposition == "ACCEPT"
    replay = scheduler.accept_user_command("exec-wait", UserCommand(**base))
    assert replay.outcome.disposition == "REPLAY"

    conflict = scheduler.accept_user_command(
        "exec-wait",
        UserCommand(**{**base, "payload": {"answer_text": "changed"}}),
    )
    assert conflict.outcome.disposition == "CONFLICT"


def test_wait_integration_does_not_expose_unfenced_resume_answer_api():
    scheduler, _store = _scheduler()
    assert not hasattr(scheduler, "resume")

