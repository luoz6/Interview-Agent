from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest

from app.graphs.durable_interview_graph import validate_command
from app.graphs.interview_state import (
    choose_workflow_engine,
    is_durable_interview_version,
)
from app.graphs.durable_interview_state_v2 import make_durable_initial_state_v2
from app.services.interview_workflow import (
    InterviewWorkflowService,
    _followup_ui_state,
)
from app.services.langgraph_runtime import VersionedGraphRegistry
from app.services.report_jobs import choose_report_workflow_engine
from tests.test_postgres_session_store import make_plan


def _find_id(selector, expected: str, rollout_percent: int) -> str:
    for candidate in range(1, 1_001):
        value = str(UUID(int=candidate))
        if (
            selector(
                value,
                runtime_store="postgres",
                runtime_enabled=True,
                rollout_percent=rollout_percent,
            )
            == expected
        ):
            return value
    pytest.fail(
        f"no deterministic {expected} bucket found within 1,000 candidates"
    )


def find_session_id_for_interview_engine(
    engine: str, rollout_percent: int
) -> str:
    return _find_id(choose_workflow_engine, engine, rollout_percent)


def find_job_id_for_review_engine(engine: str, rollout_percent: int) -> str:
    return _find_id(
        choose_report_workflow_engine, engine, rollout_percent
    )


@pytest.mark.parametrize(
    ("selector", "durable_engine"),
    [
        (choose_workflow_engine, "langgraph-v1"),
        (choose_report_workflow_engine, "langgraph-review-v1"),
    ],
)
def test_assignment_fails_closed_outside_eligible_postgres_runtime(
    selector, durable_engine
):
    fixed_id = str(UUID(int=1))

    assert selector(
        fixed_id,
        runtime_store="memory",
        runtime_enabled=True,
        rollout_percent=100,
    ) == "legacy"
    assert selector(
        fixed_id,
        runtime_store="postgres",
        runtime_enabled=False,
        rollout_percent=100,
    ) == "legacy"
    assert selector(
        fixed_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=0,
    ) == "legacy"
    assert selector(
        fixed_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
    ) == durable_engine


def test_one_percent_bucket_search_uses_real_production_hashing():
    interview_id = find_session_id_for_interview_engine(
        "langgraph-v1", 1
    )
    legacy_interview_id = find_session_id_for_interview_engine("legacy", 1)
    review_id = find_job_id_for_review_engine("langgraph-review-v1", 1)
    legacy_review_id = find_job_id_for_review_engine("legacy", 1)

    assert choose_workflow_engine(
        interview_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=1,
    ) == "langgraph-v1"
    assert choose_workflow_engine(
        legacy_interview_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=1,
    ) == "legacy"
    assert choose_report_workflow_engine(
        review_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=1,
    ) == "langgraph-review-v1"
    assert choose_report_workflow_engine(
        legacy_review_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=1,
    ) == "legacy"


def test_interview_and_review_assignments_are_independent():
    session_id = find_session_id_for_interview_engine("langgraph-v1", 1)
    job_id = find_job_id_for_review_engine("legacy", 1)

    assert choose_workflow_engine(
        session_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=1,
    ) == "langgraph-v1"
    assert choose_report_workflow_engine(
        job_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=1,
    ) == "legacy"


def test_new_interview_assignment_uses_configured_v2_without_changing_v1_default():
    fixed_id = str(UUID(int=1))

    assert choose_workflow_engine(
        fixed_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
    ) == "langgraph-v1"
    assert choose_workflow_engine(
        fixed_id,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        durable_version="langgraph-v2",
    ) == "langgraph-v2"


@pytest.mark.parametrize("version", ["langgraph-v1", "langgraph-v2"])
def test_durable_interview_version_predicate_accepts_registered_versions(version):
    assert is_durable_interview_version(version) is True


@pytest.mark.parametrize("version", [None, "legacy", "langgraph-review-v1", "langgraph-v3"])
def test_durable_interview_version_predicate_rejects_other_versions(version):
    assert is_durable_interview_version(version) is False


def test_v2_initial_state_contains_only_bounded_artifact_references():
    state = make_durable_initial_state_v2(
        "session-1",
        make_plan(),
        memory_policy_version="question-memory-v1",
    )

    assert state["workflow_engine"] == "langgraph-v2"
    assert state["graph_schema_version"] == "langgraph-v2"
    assert state["active_context_artifact_ref"] is None
    assert state["active_context_artifact_sha256"] is None
    assert state["memory_policy_version"] == "question-memory-v1"
    assert "context_artifact_payload" not in state


def test_registry_never_falls_back_to_another_graph_version():
    registry = VersionedGraphRegistry()
    registry.register("langgraph-v1", object())
    registry.register("langgraph-review-v1", object())

    with pytest.raises(ValueError, match="unsupported graph version"):
        registry.get("langgraph-v2")


@dataclass
class FakeCommand:
    command_id: str
    command_type: str
    expected_version: int
    answer_text: str | None = None
    status: str = "pending"


class FakeWorkflowStore:
    def __init__(self, existing: FakeCommand | None = None):
        self.existing = existing
        self.enqueued = []
        self.conflicts = []

    def get_command_or_none(self, session_id, command_id):
        if self.existing and self.existing.command_id == command_id:
            return self.existing
        return None

    def enqueue_command(self, **kwargs):
        self.enqueued.append(kwargs)
        return self.existing or FakeCommand(
            command_id=kwargs["command_id"],
            command_type=kwargs["command_type"],
            expected_version=kwargs["expected_version"],
            answer_text=kwargs["answer_text"],
        )

    def get_command(self, session_id, command_id):
        return self.existing

    def mark_command_conflict(self, session_id, command_id, state_version):
        self.conflicts.append((session_id, command_id, state_version))


class FakeLegacyStore:
    def __init__(self, state):
        self.state = state

    def get(self, session_id):
        return dict(self.state)

    def snapshot(self, session_id):
        return {
            "session_id": session_id,
            "workflow_engine": self.state.get("workflow_engine", "legacy"),
            "followup_policy_version": self.state.get(
                "followup_policy_version", "fixed_v1"
            ),
            "current_followup_count": self.state.get(
                "current_followup_count", 0
            ),
        }


class FakeGraph:
    def __init__(self, values, next_node):
        self.values = values
        self.next_node = next_node

    def get_state(self, config):
        return type(
            "GraphState",
            (),
            {"values": self.values, "next": (self.next_node,)},
        )()


def _workflow(state, workflow_store):
    return InterviewWorkflowService(
        legacy_store=FakeLegacyStore(state),
        workflow_store=workflow_store,
        generation_store=object(),
        graph_registry=VersionedGraphRegistry(),
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        default_graph_version="langgraph-v1",
    )


@pytest.mark.parametrize(
    ("policy_version", "next_node", "expected"),
    [
        ("adaptive_v1", "execute_decision_attempt", "decision_pending"),
        ("fixed_v1", "execute_decision_attempt", "generation_pending"),
        ("adaptive_v1", "generate_followup", "generation_pending"),
    ],
)
def test_followup_ui_state_exposes_only_policy_safe_stage(
    policy_version, next_node, expected
):
    values = {
        "active_command_id": "command-1",
        "gap_summary": "internal gap detail",
        "decision_confidence": "low",
        "reason_code": "internal_reason",
    }

    result = _followup_ui_state(
        values,
        next_node=next_node,
        policy_version=policy_version,
    )

    assert result == expected
    assert "gap" not in result
    assert "confidence" not in result
    assert "reason" not in result


def test_followup_ui_state_prioritizes_safe_degraded_semantics():
    result = _followup_ui_state(
        {
            "active_command_id": "command-1",
            "termination_reason_code": "provider_unavailable",
            "gap_summary": "internal gap detail",
        },
        next_node="execute_decision_attempt",
        policy_version="adaptive_v1",
    )

    assert result == "degraded"


def test_decision_stage_snapshot_can_recover_before_generation_exists():
    state = {
        "workflow_engine": "langgraph-v1",
        "graph_schema_version": "langgraph-v1",
        "memory_policy_version": "question-memory-v1",
        "messages": [],
    }
    values = {
        "workflow_engine": "langgraph-v1",
        "active_command_id": "command-1",
        "generation_id": None,
        "followup_policy_version": "adaptive_v1",
        "current_followup_count": 1,
        "gap_summary": "internal gap detail",
        "decision_confidence": "low",
        "reason_code": "internal_reason",
    }
    registry = VersionedGraphRegistry()
    registry.register(
        "langgraph-v1",
        FakeGraph(values, "execute_decision_attempt"),
    )
    workflow = InterviewWorkflowService(
        legacy_store=FakeLegacyStore(state),
        workflow_store=FakeWorkflowStore(),
        generation_store=object(),
        graph_registry=registry,
        runtime_store="postgres",
        runtime_enabled=True,
        rollout_percent=100,
        default_graph_version="langgraph-v1",
    )

    snapshot = workflow.snapshot("s1")

    assert snapshot["followup_ui_state"] == "decision_pending"
    assert snapshot["active_command_id"] == "command-1"
    assert snapshot["active_generation_id"] is None
    assert snapshot["active_stream_url"] == (
        "/api/interviews/s1/commands/command-1/stream"
    )
    assert snapshot["current_followup_count"] == 1
    assert "gap_summary" not in snapshot
    assert "decision_confidence" not in snapshot
    assert "reason_code" not in snapshot


def test_new_answer_after_finish_is_rejected_before_inbox_write():
    store = FakeWorkflowStore()
    workflow = _workflow(
        {"workflow_engine": "langgraph-v1", "status": "finished"},
        store,
    )

    with pytest.raises(ValueError, match="already finished"):
        workflow.submit_command(
            "s1",
            command_type="answer",
            expected_version=3,
            command_id="late-answer",
            answer_text="late",
        )

    assert store.enqueued == []


def test_duplicate_applied_command_can_reconnect_after_finish():
    existing = FakeCommand(
        command_id="finish-1",
        command_type="finish",
        expected_version=2,
        status="applied",
    )
    store = FakeWorkflowStore(existing)
    workflow = _workflow(
        {"workflow_engine": "langgraph-v1", "status": "finished"},
        store,
    )

    accepted = workflow.submit_command(
        "s1",
        command_type="finish",
        expected_version=2,
        command_id="finish-1",
    )

    assert accepted.command_id == "finish-1"
    assert len(store.enqueued) == 1


def test_old_round_command_is_marked_conflict_without_generation():
    command = FakeCommand(
        command_id="old-answer",
        command_type="answer",
        expected_version=1,
        answer_text="stale",
    )
    store = FakeWorkflowStore(command)
    state = {
        "session_id": "s1",
        "active_command_id": "old-answer",
        "state_version": 2,
    }

    result = validate_command(state, type("Deps", (), {"workflow_store": store})())

    assert result == {
        "active_command_id": None,
        "command_outcome": "conflict",
    }
    assert store.conflicts == [("s1", "old-answer", 2)]
