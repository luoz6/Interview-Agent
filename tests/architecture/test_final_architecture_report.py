from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import generate_final_architecture_report as report  # noqa: E402


def test_final_report_is_parse_clean_and_uses_pinned_p0_baseline():
    result = report.scan()

    assert result["metadata"]["p0_baseline_commit"] == "0af2a8b"
    assert result["metadata"]["p0_parse_errors"] == 0
    assert result["metadata"]["p9_parse_errors"] == 0


def test_final_report_artifact_matches_current_tree():
    current = report.scan()
    frozen = json.loads(report.JSON_PATH.read_text(encoding="utf-8"))

    assert frozen == current


def test_services_retirement_is_reflected_in_final_loc():
    result = report.scan()
    p0 = result["loc"]["p0"]
    p9 = result["loc"]["p9"]

    assert p0["services_python_files"] == 220
    assert p0["official_services_nonempty_loc"] == 75_970
    assert p9["services_python_files"] == 0
    assert p9["official_services_nonempty_loc"] == 0
    assert result["final_gate_signals"]["services_directory_exists"] is False


def test_required_architecture_outcome_metrics_are_present():
    result = report.scan()
    architecture = result["architecture_metrics"]

    assert "cross_layer_violation_pairs" in architecture
    assert "dependency_cycles" in architecture
    assert "exact_duplicate_groups" in architecture
    assert len(result["god_module_owner_comparison"]) == 3
    assert result["loc"]["p0"]["largest_module"]["physical_loc"] > 0
    assert result["loc"]["p9"]["largest_module"]["physical_loc"] > 0


def test_report_separates_architecture_closure_from_deferred_reliability():
    result = report.scan()
    signals = result["final_gate_signals"]

    remaining = {
        key: value
        for key, value in signals.items()
        if key != "services_directory_exists" and value != 0
    }
    assert result["measured_boundary_signals_satisfied"] is (not remaining)
    assert result["final_architecture_definition_satisfied"] is True
    assert result["architecture_refactor_status"] == "COMPLETE"
    assert result["architecture_refactor_gate"] == "PASS"
    assert result["architecture_boundary_gate"] == "PASS"
    assert result["postgres_reliability_verification"] == "DEFERRED"
    assert result["postgres_reliability_deferred_reason"] == (
        "BLOCKED_ENVIRONMENT / EXTERNAL_APPROVAL_UNAVAILABLE"
    )
    assert result["full_production_reliability_gate"] == "NOT_VERIFIED"
    assert "full production reliability gate remains NOT_VERIFIED" in result[
        "final_gate_scope_note"
    ]
