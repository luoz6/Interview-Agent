import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.application.scheduling import (
    InMemoryExecutionStateStore,
    SchedulerApplicationCapability,
    SchedulerDispatchError,
)
from app.domain.agents.artifacts import DomainArtifact
from app.domain.interview.scheduling import (
    CapabilityDescriptor,
    ExecutionConstraints,
    ExecutionPlan,
    ExecutionState,
    ExecutionTaskDefinition,
    TaskRuntimeState,
)


ROOT = Path(__file__).resolve().parents[2]


class FakeCapabilityCatalog:
    def __init__(self, capability: CapabilityDescriptor) -> None:
        self.capability = capability

    def list_capabilities(self):
        return (self.capability,)

    def resolve(self, *, agent_id, skill, capability_version=None):
        if (agent_id, skill) != (self.capability.agent_id, self.capability.skill):
            return None
        if (
            capability_version is not None
            and capability_version != self.capability.capability_version
        ):
            return None
        return self.capability

    def validate_compatibility(
        self,
        *,
        agent_id,
        skill,
        request_contract_id,
        request_contract_version,
        input_artifact_types=(),
        capability_version=None,
    ):
        capability = self.resolve(
            agent_id=agent_id,
            skill=skill,
            capability_version=capability_version,
        )
        return capability is not None and capability.is_compatible(
            agent_id=agent_id,
            skill=skill,
            request_contract_id=request_contract_id,
            request_contract_version=request_contract_version,
            input_artifact_types=input_artifact_types,
        )


class FakeInvoker:
    def __init__(self) -> None:
        self.requests = []

    def invoke(self, *, agent_id, skill, request, execution_context=None):
        self.requests.append((agent_id, skill, request, execution_context))
        return DomainArtifact(artifact_type="followup-artifact")


def _fixture():
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
    task = ExecutionTaskDefinition(
        task_id="followup-1",
        capability="interview.followup",
        agent_id="interview-examiner",
        skill="generate-followup",
        input_contract="generate-followup-request",
        output_contract="followup-artifact",
        parameters={"question_id": "q1", "focus": "tradeoffs"},
    )
    plan = ExecutionPlan(
        execution_id="exec-1",
        interview_plan_ref="plan-1",
        task_definitions=(task,),
    )
    state = ExecutionState(
        execution_id="exec-1",
        task_states=(TaskRuntimeState(task_id="followup-1"),),
    )
    catalog = FakeCapabilityCatalog(capability)
    invoker = FakeInvoker()
    store = InMemoryExecutionStateStore(state)
    scheduler = SchedulerApplicationCapability(
        state_store=store,
        plan=plan,
        capability_port=catalog,
        invocation_port=invoker,
    )
    return scheduler, invoker, store


def test_scheduler_application_loads_resolves_assembles_dispatches_and_commits():
    scheduler, invoker, store = _fixture()

    result = scheduler.step("exec-1")

    assert result.action == "DISPATCH"
    assert result.task.task_id == "followup-1"
    assert result.request.question_id == "q1"
    assert result.artifact.artifact_type == "followup-artifact"
    assert result.state.task_state("followup-1").status == "COMPLETED"
    assert len(result.state.artifact_refs) == 1
    assert result.state.latest_observation["task_id"] == "followup-1"
    assert len(invoker.requests) == 1
    assert store.load("exec-1") == result.state


def test_scheduler_returns_complete_after_all_tasks_are_terminal():
    scheduler, _invoker, _store = _fixture()

    scheduler.step("exec-1")
    result = scheduler.step("exec-1")

    assert result.action == "COMPLETE"
    assert result.state.execution_id == "exec-1"


def test_scheduler_application_has_no_transport_dependency():
    path = ROOT / "app" / "application" / "scheduling" / "scheduler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(not module.startswith("app.a2a") for module in imported_modules)
    assert "app.ports.agent_capability" in imported_modules
    assert "app.ports.agent_invocation" in imported_modules


def test_agent_call_budget_exhaustion_blocks_invocation():
    scheduler, invoker, store = _fixture()
    scheduler.plan = scheduler.plan.model_copy(
        update={"execution_constraints": ExecutionConstraints(max_agent_calls=0)}
    )

    with pytest.raises(SchedulerDispatchError) as failure:
        scheduler.step("exec-1")

    assert failure.value.code == "AGENT_CALL_BUDGET_EXHAUSTED"
    assert invoker.requests == []
    assert store.load("exec-1").agent_calls_used == 0


def test_execution_timeout_blocks_invocation_from_durable_start_time():
    scheduler, invoker, store = _fixture()
    scheduler.plan = scheduler.plan.model_copy(
        update={
            "execution_constraints": ExecutionConstraints(
                execution_timeout_seconds=1,
            )
        }
    )
    expired = store.load("exec-1").model_copy(
        update={
            "execution_started_at": datetime.now(timezone.utc)
            - timedelta(seconds=2),
        }
    )
    store.save(expired)

    with pytest.raises(SchedulerDispatchError) as failure:
        scheduler.step("exec-1")

    assert failure.value.code == "EXECUTION_TIMEOUT"
    assert invoker.requests == []


def test_zero_retry_budget_allows_a_new_task():
    scheduler, invoker, _store = _fixture()
    scheduler.plan = scheduler.plan.model_copy(
        update={"execution_constraints": ExecutionConstraints(max_retries=0)}
    )

    result = scheduler.step("exec-1")

    assert result.action == "DISPATCH"
    assert len(invoker.requests) == 1


def test_zero_retry_budget_blocks_a_new_logical_attempt():
    scheduler, invoker, store = _fixture()
    scheduler.plan = scheduler.plan.model_copy(
        update={"execution_constraints": ExecutionConstraints(max_retries=0)}
    )
    retried = store.load("exec-1").model_copy(
        update={
            "task_states": (
                TaskRuntimeState(
                    task_id="followup-1",
                    status="READY",
                    attempt=1,
                    max_attempts=2,
                ),
            ),
        }
    )
    store.save(retried)

    with pytest.raises(SchedulerDispatchError) as failure:
        scheduler.step("exec-1")

    assert failure.value.code == "RETRY_BUDGET_EXHAUSTED"
    assert invoker.requests == []
