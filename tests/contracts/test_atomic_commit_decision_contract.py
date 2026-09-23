import ast
from pathlib import Path

import pytest

from app.domain.interview.scheduling import (
    CURRENT_INVOCATION_COMMIT_DECISION,
    AtomicCommitDecision,
    choose_atomic_commit_strategy,
)


ROOT = Path(__file__).resolve().parents[2]


def test_current_decision_uses_existing_transactional_outbox_boundary():
    decision = CURRENT_INVOCATION_COMMIT_DECISION
    assert decision.strategy == "TRANSACTIONAL_OUTBOX"
    assert decision.state_and_ledger_same_store is True
    assert decision.artifact_metadata_same_store is True
    assert decision.outbox_name == "runtime_outbox"
    assert decision.logical_effect_guarantee is True
    assert decision.external_provider_exactly_once is False


def test_same_store_prefers_one_atomic_transaction():
    decision = choose_atomic_commit_strategy(same_transactional_store=True)
    assert decision.strategy == "SAME_TRANSACTION"
    assert decision.state_and_ledger_same_store is True
    assert decision.artifact_metadata_same_store is True
    assert decision.outbox_name is None


def test_cross_store_requires_outbox_and_rejects_best_effort_or_exactly_once_claims():
    with pytest.raises(ValueError, match="outbox name"):
        AtomicCommitDecision(
            strategy="TRANSACTIONAL_OUTBOX",
            state_and_ledger_same_store=False,
            artifact_metadata_same_store=False,
        )
    with pytest.raises(ValueError, match="exactly-once"):
        AtomicCommitDecision(
            strategy="TRANSACTIONAL_OUTBOX",
            state_and_ledger_same_store=False,
            artifact_metadata_same_store=False,
            outbox_name="runtime_outbox",
            external_provider_exactly_once=True,
        )
    with pytest.raises(ValueError, match="one transactional store"):
        AtomicCommitDecision(
            strategy="SAME_TRANSACTION",
            state_and_ledger_same_store=False,
            artifact_metadata_same_store=True,
        )


def test_commit_decision_contract_is_transport_neutral():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "commit.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(not module.startswith(("app.a2a", "app.adapters", "app.runtime")) for module in imported_modules)
