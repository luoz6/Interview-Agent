import json
from pathlib import Path

import pytest

from scripts.build_t60_combination_matrix import (
    DEFAULT_OUTPUT,
    P0_SCENARIOS,
    build_matrix,
    validate_matrix,
)
from scripts.run_t60_combination_matrix import _postgres_preflight


ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_t60_matrix_matches_deterministic_builder():
    checked_in = json.loads((ROOT / DEFAULT_OUTPUT).read_text(encoding="utf-8"))
    generated = build_matrix()

    assert checked_in == generated
    validate_matrix(checked_in, root=ROOT)


def test_t60_matrix_has_exactly_four_manual_p0_state_machine_crosses():
    matrix = build_matrix()
    p0_rows = [row for row in matrix["scenarios"] if row["priority"] == "P0"]

    assert [row["id"] for row in p0_rows] == [item["id"] for item in P0_SCENARIOS]
    assert all(row["selection_kind"] == "manual_state_machine_cross" for row in p0_rows)
    assert all(len(row["cross_state_test_nodes"]) == 4 for row in p0_rows)
    assert all(len(row["expected_invariants"]) == 4 for row in p0_rows)


def test_t60_matrix_covers_every_value_and_all_declared_risk_pairs():
    matrix = build_matrix()

    assert matrix["covered_risk_pair_count"] == matrix["required_risk_pair_count"]
    assert matrix["full_cartesian_product_required"] is False
    assert matrix["generation_is_deterministic"] is True
    assert matrix["scenario_count"] < 100
    assert matrix["unique_test_node_count"] >= 40


def test_t60_matrix_keeps_principal_memory_disabled_everywhere():
    matrix = build_matrix()

    assert {
        row["axes"]["memory_mode"] for row in matrix["scenarios"]
    } == {"disabled"}
    assert any(
        "test_disabled_runtime_does_not_construct_shadow_dependencies" in node
        for node in matrix["unique_test_nodes"]
    )


def test_t60_runner_fails_closed_before_pytest_without_postgres(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    with pytest.raises(RuntimeError, match="POSTGRES_DSN is required"):
        _postgres_preflight()
