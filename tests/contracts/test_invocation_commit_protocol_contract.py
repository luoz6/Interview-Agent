import ast
from pathlib import Path

import pytest

from app.domain.execution_lease import LeaseLost
from app.domain.interview.scheduling import (
    LogicalEffectConflict,
    InvocationCommitReceipt,
    InvocationIdentity,
    InvocationLedgerEntry,
    accept_commit,
    recovery_action,
)
from app.ports.agent_invocation_ledger import AgentInvocationLedgerPort


ROOT = Path(__file__).resolve().parents[2]


def _identity() -> InvocationIdentity:
    return InvocationIdentity(
        execution_id="exec-1",
        task_id="task-1",
        logical_attempt=1,
    )


def _entry(status: str) -> InvocationLedgerEntry:
    values = {
        "identity": _identity(),
        "status": status,
        "agent_id": "reviewer",
        "skill": "evaluate-answer",
        "request_digest": "sha256:req",
    }
    if status == "RUNNING":
        values.update(lease_owner="worker-1", fencing_version=2)
    elif status == "COMPLETED":
        values.update(artifact_ref="artifact:evaluation:1", fencing_version=2)
    elif status == "FAILED":
        values.update(error_result={"code": "timeout"})
    return InvocationLedgerEntry(**values)


def test_commit_receipt_binds_identity_artifact_and_fence():
    receipt = InvocationCommitReceipt(
        identity=_identity(),
        artifact_ref="artifact:evaluation:1",
        fencing_version=2,
    )
    assert receipt.identity_key == ("exec-1", "task-1", 1)
    assert receipt.status == "COMMITTED"
    with pytest.raises(ValueError):
        InvocationCommitReceipt(
            identity=_identity(),
            artifact_ref="artifact:evaluation:1",
            fencing_version=0,
        )


def test_recovery_actions_define_each_crash_window():
    assert recovery_action(_entry("PREPARED")) == "REDISPATCH"
    assert recovery_action(_entry("RUNNING")) == "RECLAIM_IF_EXPIRED"
    assert recovery_action(_entry("COMPLETED")) == "OBSERVE_COMMITTED"
    assert recovery_action(_entry("FAILED")) == "RETRY_OR_TERMINAL"


def test_commit_port_requires_fenced_idempotent_commit_boundary():
    methods = {
        name
        for name in dir(AgentInvocationLedgerPort)
        if not name.startswith("_")
    }
    assert {"commit", "mark_completed", "assert_lease"}.issubset(methods)
    assert "artifact_ref" in AgentInvocationLedgerPort.commit.__annotations__


def test_commit_protocol_contract_is_transport_neutral():
    for relative_path in (
        ("app", "domain", "interview", "scheduling", "ledger.py"),
        ("app", "ports", "agent_invocation_ledger.py"),
    ):
        tree = ast.parse((ROOT.joinpath(*relative_path)).read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert all(not module.startswith("app.a2a") for module in imported_modules)


def test_stale_worker_is_a_lease_failure_not_a_second_commit():
    assert LeaseLost.code == "lease_lost"


def test_single_logical_attempt_accepts_one_effect_and_replays_same_artifact():
    first = InvocationCommitReceipt(
        identity=_identity(),
        artifact_ref="artifact:evaluation:1",
        fencing_version=2,
    )
    replay = InvocationCommitReceipt(
        identity=_identity(),
        artifact_ref="artifact:evaluation:1",
        fencing_version=3,
    )
    accepted = accept_commit(first, replay)
    assert accepted.status == "ALREADY_COMMITTED"
    assert accepted.artifact_ref == first.artifact_ref
    assert accepted.fencing_version == first.fencing_version

    with pytest.raises(LogicalEffectConflict, match="different artifact"):
        accept_commit(
            first,
            InvocationCommitReceipt(
                identity=_identity(),
                artifact_ref="artifact:evaluation:2",
                fencing_version=3,
            ),
        )

    with pytest.raises(LeaseLost, match="stale worker"):
        accept_commit(
            first,
            InvocationCommitReceipt(
                identity=_identity(),
                artifact_ref=first.artifact_ref,
                fencing_version=1,
            ),
        )
