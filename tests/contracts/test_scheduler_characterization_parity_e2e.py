from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import Field

from app.application.scheduling import (
    InMemoryExecutionStateStore,
    SchedulerApplicationCapability,
)
from app.domain.agents.artifacts import DomainArtifact
from app.domain.execution_lease import LeaseToken
from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionDependencyDefinition,
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    InvocationCommitReceipt,
    TaskRuntimeState,
    UserCommand,
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
    def __init__(self):
        self.skills: list[str] = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.skills.append(skill)
        if skill == "generate-main-question":
            return QuestionArtifact(
                artifact_type="main-question-artifact",
                question_id="q-main",
            )
        if skill == "generate-followup":
            return QuestionArtifact(
                artifact_type="followup-artifact",
                question_id="q-followup",
            )
        return DomainArtifact(artifact_type={
            "evaluate-answer": "evaluation-artifact",
            "evaluate-interview": "interview-evaluation-artifact",
            "generate-report": "report-artifact",
        }[skill])


class DurableCommands:
    def __init__(self):
        self.records = {}

    def enqueue(self, **values):
        existing = self.records.get(values["command_id"])
        if existing is not None and existing != values:
            raise ValueError("command payload conflict")
        self.records[values["command_id"]] = values
        return values

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


class DurableLedger:
    def __init__(self):
        self.entries = {}

    def prepare(self, entry):
        self.entries.setdefault(entry.identity.key, entry)
        return self.entries[entry.identity.key]

    def acquire(self, identity, *, owner_id, lease_seconds, now=None):
        now = now or datetime.now(timezone.utc)
        lease = LeaseToken(
            resource_id=":".join(map(str, identity.key)),
            owner_id=owner_id,
            token=f"lease-{identity.logical_attempt}",
            fencing_version=1,
            expires_at=now + timedelta(seconds=lease_seconds),
        )
        current = self.entries[identity.key]
        self.entries[identity.key] = current.model_copy(
            update={
                "status": "RUNNING",
                "lease_owner": owner_id,
                "lease_token": lease.token,
                "lease_expires_at": lease.expires_at,
                "fencing_version": lease.fencing_version,
            }
        )
        return lease

    def mark_running(self, identity, *, lease_owner, fencing_version):
        return self.entries[identity.key]

    def commit(self, identity, *, artifact_ref, lease_owner, fencing_version):
        current = self.entries[identity.key]
        self.entries[identity.key] = current.model_copy(
            update={"status": "COMPLETED", "artifact_ref": artifact_ref}
        )
        return InvocationCommitReceipt(
            identity=identity,
            artifact_ref=artifact_ref,
            fencing_version=fencing_version,
        )

    def mark_failed(self, identity, **kwargs):
        raise AssertionError("the parity happy path must not fail")


def _task(task_id, skill, output_type, **parameters):
    return ExecutionTaskDefinition(
        task_id=task_id,
        capability=f"interview.{skill}",
        agent_id="interview-agent",
        skill=skill,
        output_contract=output_type,
        parameters=parameters,
    )


def _scheduler():
    specs = (
        ("main", "generate-main-question", "main-question-artifact", {"intent": {"topic": "systems"}}),
        ("evaluation", "evaluate-answer", "evaluation-artifact", {"state": {"answer": "first"}}),
        ("followup", "generate-followup", "followup-artifact", {"question_id": "q-followup"}),
        ("final", "evaluate-interview", "interview-evaluation-artifact", {"state": {"complete": True}}),
        ("report", "generate-report", "report-artifact", {"plan": {"id": "plan-1"}, "session_id": "exec-parity", "evaluation_items": []}),
    )
    tasks = tuple(_task(task_id, skill, output, **params) for task_id, skill, output, params in specs)
    plan = ExecutionPlan(
        execution_id="exec-parity",
        interview_plan_ref="plan-1",
        task_definitions=tasks,
        dependency_definitions=tuple(
            ExecutionDependencyDefinition(
                predecessor_task_id=tasks[index].task_id,
                successor_task_id=tasks[index + 1].task_id,
            )
            for index in range(len(tasks) - 1)
        ),
    )
    capabilities = tuple(
        CapabilityDescriptor(
            agent_id="interview-agent",
            skill=skill,
            description=skill,
            request_contract_id=f"{skill}-request",
            request_contract_version="v1",
            output_artifact_type=output,
            output_artifact_version="1.0",
            capability_version="v1",
        )
        for _, skill, output, _ in specs
    )
    state = ExecutionState(
        execution_id="exec-parity",
        task_states=tuple(TaskRuntimeState(task_id=item.task_id) for item in tasks),
    )
    invoker = Invoker()
    scheduler = SchedulerApplicationCapability(
        state_store=InMemoryExecutionStateStore(state),
        plan=plan,
        capability_port=Catalog(capabilities),
        invocation_port=invoker,
        durable_command_port=DurableCommands(),
        invocation_ledger=DurableLedger(),
    )
    return scheduler, invoker


def _answer(scheduler, command_id, answer):
    waiting = scheduler.step("exec-parity")
    assert waiting.action == "WAIT_USER"
    wait = waiting.state.current_wait_handle
    result = scheduler.accept_user_command(
        "exec-parity",
        UserCommand(
            command_id=command_id,
            execution_id="exec-parity",
            wait_id=wait.wait_id,
            task_id=wait.task_id,
            question_id=wait.question_id,
            expected_revision=wait.issued_revision,
            payload={"answer_text": answer},
        ),
    )
    assert result.outcome.disposition == "ACCEPT"


def test_scheduler_normal_path_matches_frozen_interview_phase_order():
    scheduler, invoker = _scheduler()

    assert scheduler.step("exec-parity").task.task_id == "main"
    _answer(scheduler, "answer-main", "bounded queues")
    assert scheduler.step("exec-parity").task.task_id == "evaluation"
    assert scheduler.step("exec-parity").task.task_id == "followup"
    _answer(scheduler, "answer-followup", "load shedding")
    assert scheduler.step("exec-parity").task.task_id == "final"
    assert scheduler.step("exec-parity").task.task_id == "report"

    completed = scheduler.step("exec-parity")
    assert completed.action == "COMPLETE"
    assert completed.state.execution_status == "COMPLETED"
    assert invoker.skills == [
        "generate-main-question",
        "evaluate-answer",
        "generate-followup",
        "evaluate-interview",
        "generate-report",
    ]

