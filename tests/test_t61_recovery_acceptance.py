import json
from pathlib import Path

import pytest

from scripts.build_t61_recovery_acceptance import (
    ACCEPTANCE_INVARIANTS,
    DEFAULT_OUTPUT,
    REQUIREMENTS,
    build_acceptance,
    validate_acceptance,
)
from scripts.run_t61_recovery_acceptance import _postgres_preflight


ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_t61_acceptance_matches_deterministic_builder():
    checked_in = json.loads((ROOT / DEFAULT_OUTPUT).read_text(encoding="utf-8"))
    generated = build_acceptance()

    assert checked_in == generated
    validate_acceptance(checked_in, root=ROOT)


def test_t61_acceptance_maps_all_seventeen_plan_requirements_once():
    payload = build_acceptance()

    assert payload["requirement_count"] == 17 == len(REQUIREMENTS)
    assert [item["id"] for item in payload["requirements"]] == [
        f"T61-R{index:02d}" for index in range(1, 18)
    ]
    assert payload["unique_test_node_count"] >= 30


def test_t61_acceptance_maps_all_four_acceptance_invariants():
    payload = build_acceptance()

    assert payload["acceptance_invariant_count"] == 4 == len(
        ACCEPTANCE_INVARIANTS
    )
    assert payload["acceptance_invariants"][-1]["requirement_ids"] == [
        f"T61-R{index:02d}" for index in range(1, 18)
    ]


def test_t61_transaction_windows_are_explicit_and_not_collapsed():
    payload = build_acceptance()
    by_id = {item["id"]: item for item in payload["requirements"]}

    assert "before_artifact" in " ".join(by_id["T61-R06"]["test_nodes"])
    assert "[artifact]" in " ".join(by_id["T61-R07"]["test_nodes"])
    assert "[head]" in " ".join(by_id["T61-R08"]["test_nodes"])
    assert all(
        marker in " ".join(by_id["T61-R09"]["test_nodes"])
        for marker in ("[job]", "[review_run]", "[session]", "outbox")
    )
    assert "response_loss" in " ".join(by_id["T61-R10"]["test_nodes"])


def test_t61_every_recovery_path_has_stable_coded_evidence():
    payload = build_acceptance()

    assert all(item["stable_evidence_codes"] for item in payload["requirements"])
    assert all(
        code == code.lower() and " " not in code
        for item in payload["requirements"]
        for code in item["stable_evidence_codes"]
    )


def test_t61_runner_fails_closed_before_pytest_without_postgres(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    with pytest.raises(RuntimeError, match="POSTGRES_DSN is required"):
        _postgres_preflight()
