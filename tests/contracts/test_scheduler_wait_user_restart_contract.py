from __future__ import annotations

from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

from app.application.scheduling import (
    InMemoryExecutionStateStore,
    InMemoryUserCommandStore,
    SchedulerApplicationCapability,
)
from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionDependencyDefinition,
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    TaskRuntimeState,
    UserCommand,
)
from app.graphs.scheduler_graph import (
    build_scheduler_graph,
    deserialize_execution_state,
    scheduler_application_dependencies,
    scheduler_graph_input,
)


class QuestionArtifact(DomainArtifact):
    question_id: str = Field(min_length=1)


class Catalog:
    def __init__(self, capabilities):
        self.capabilities = {
            (item.agent_id, item.skill): item for item in capabilities
        }

    def resolve(self, *, agent_id, skill, capability_version=None):
        item = self.capabilities.get((agent_id, skill))
        if item is None:
            return None
        if capability_version and item.capability_version != capability_version:
            return None
        return item

    def validate_compatibility(self, **kwargs):
        return self.resolve(
            agent_id=kwargs["agent_id"],
            skill=kwargs["skill"],
            capability_version=kwargs.get("capability_version"),
        ) is not None


class Invoker:
    def invoke(self, *, agent_id, skill, request, execution_context=None):
        if skill == "generate-main-question":
            return QuestionArtifact(
                artifact_type="main-question-artifact",
                question_id="q-restart",
            )
        return DomainArtifact(artifact_type="evaluation-artifact")


class DurableCommands:
    def __init__(self):
        self.records = {}

    def enqueue(self, **values):
        self.records.setdefault(values["command_id"], values)
        return self.records[values["command_id"]]

    def get(self, *, execution_id, command_id):
        record = self.records.get(command_id)
        if record is None:
            return None
        return {
            "command_id": command_id,
            "command_type": record["command_type"],
            "expected_version": record["expected_version"],
            "answer_text": record["payload"].get("answer_text"),
        }


def _runtime(*, command_port, state=None):
    question = ExecutionTaskDefinition(
        task_id="question",
        capability="interview.question",
        agent_id="interview-agent",
        skill="generate-main-question",
        output_contract="main-question-artifact",
        parameters={"intent": {"topic": "reliability"}},
    )
    evaluation = ExecutionTaskDefinition(
        task_id="evaluation",
        capability="interview.evaluation",
        agent_id="interview-agent",
        skill="evaluate-answer",
        output_contract="evaluation-artifact",
        parameters={"state": {"answer": "checkpointed"}},
    )
    plan = ExecutionPlan(
        execution_id="exec-restart",
        interview_plan_ref="plan-restart",
        task_definitions=(question, evaluation),
        dependency_definitions=(
            ExecutionDependencyDefinition(
                predecessor_task_id="question",
                successor_task_id="evaluation",
            ),
        ),
    )
    capabilities = (
        CapabilityDescriptor(
            agent_id="interview-agent",
            skill="generate-main-question",
            description="question",
            request_contract_id="generate-main-question-request",
            request_contract_version="v1",
            output_artifact_type="main-question-artifact",
            output_artifact_version="1.0",
            capability_version="v1",
        ),
        CapabilityDescriptor(
            agent_id="interview-agent",
            skill="evaluate-answer",
            description="evaluation",
            request_contract_id="evaluate-answer-request",
            request_contract_version="v1",
            output_artifact_type="evaluation-artifact",
            output_artifact_version="1.0",
            capability_version="v1",
        ),
    )
    state = state or ExecutionState(
        execution_id="exec-restart",
        task_states=(
            TaskRuntimeState(task_id="question"),
            TaskRuntimeState(task_id="evaluation"),
        ),
    )
    scheduler = SchedulerApplicationCapability(
        state_store=InMemoryExecutionStateStore(state),
        plan=plan,
        capability_port=Catalog(capabilities),
        invocation_port=Invoker(),
        command_store=InMemoryUserCommandStore(),
        durable_command_port=command_port,
    )
    return scheduler, plan, state


def _checkpoint_at_wait():
    saver = InMemorySaver()
    commands = DurableCommands()
    scheduler, plan, initial = _runtime(command_port=commands)
    graph = build_scheduler_graph(
        scheduler_application_dependencies(scheduler, plan),
        checkpointer=saver,
    )
    config = {"configurable": {"thread_id": initial.execution_id}}
    graph.invoke(scheduler_graph_input(initial), config)
    snapshot = graph.get_state(config)
    state = deserialize_execution_state(snapshot.values["execution_state"])
    assert snapshot.next == ("WAIT_USER",)
    assert state.current_wait_handle is not None
    assert state.current_wait_handle.question_id == "q-restart"
    assert state.artifact_refs[0].artifact_type == "main-question-artifact"
    return saver, commands, config, state, plan


def _command(wait, **changes):
    values = {
        "command_id": "cmd-restart",
        "execution_id": wait.execution_id,
        "wait_id": wait.wait_id,
        "task_id": wait.task_id,
        "question_id": wait.question_id,
        "expected_revision": wait.issued_revision,
        "payload": {"answer_text": "use a fenced lease"},
    }
    values.update(changes)
    return UserCommand(**values)


def test_wait_handle_survives_restart_and_valid_command_resumes_execution():
    saver, commands, config, waiting, plan = _checkpoint_at_wait()
    restarted, _, _ = _runtime(command_port=commands, state=waiting)
    graph = build_scheduler_graph(
        scheduler_application_dependencies(restarted, plan),
        checkpointer=saver,
    )
    command = _command(waiting.current_wait_handle)

    result = graph.invoke(Command(resume=command.model_dump(mode="json")), config)
    final = deserialize_execution_state(result["execution_state"])

    assert final.current_wait_handle is None
    assert final.execution_status == "COMPLETED"
    assert final.task_state("evaluation").status == "COMPLETED"
    assert commands.records[command.command_id]["expected_version"] == waiting.revision

    # A second process has no in-memory command ledger and still recognizes
    # the durable command as an idempotent replay.
    replayed, _, _ = _runtime(command_port=commands, state=final)
    replay = replayed.apply_user_command(final, command)
    assert replay.outcome.disposition == "REPLAY"
    assert replay.state == final


@pytest.mark.parametrize(
    ("changes", "disposition", "reason_code"),
    (
        ({"task_id": "old-question-task"}, "REJECT", "late_task"),
        ({"expected_revision": 0}, "STALE", "stale_revision"),
        ({"question_id": "wrong-question"}, "REJECT", "wrong_question"),
        ({"wait_id": "wrong-wait"}, "REJECT", "wrong_wait"),
        ({"expected_revision": 999}, "STALE", "stale_revision"),
    ),
    ids=("late", "stale", "wrong-question", "wrong-wait", "wrong-revision"),
)
def test_invalid_resumes_remain_waiting_after_restart(
    changes,
    disposition,
    reason_code,
):
    saver, commands, config, waiting, plan = _checkpoint_at_wait()
    restarted, _, _ = _runtime(command_port=commands, state=waiting)
    outcomes = []
    base = scheduler_application_dependencies(restarted, plan)

    def record_resume(state, payload):
        result = restarted.apply_user_command(
            state,
            UserCommand.model_validate(payload),
        )
        outcomes.append(result.outcome)
        return result.state

    graph = build_scheduler_graph(
        replace(base, resume_wait=record_resume),
        checkpointer=saver,
    )
    invalid = _command(waiting.current_wait_handle, **changes)

    graph.invoke(Command(resume=invalid.model_dump(mode="json")), config)
    snapshot = graph.get_state(config)
    persisted = deserialize_execution_state(snapshot.values["execution_state"])

    assert outcomes[-1].disposition == disposition
    assert outcomes[-1].reason_code == reason_code
    assert snapshot.next == ("WAIT_USER",)
    assert persisted == waiting
    assert commands.records == {}

