import json
from pathlib import Path

from scripts.principal_memory_proposal_review import (
    MATRIX_COUNTS, build_review_matrix, evaluate_quality, main, validate_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
WRITE = ROOT / "docs" / "principal-memory-write-shadow-observation.json"
QUALITY = ROOT / "docs" / "principal-memory-proposal-quality.json"


def source(): return json.loads(WRITE.read_text(encoding="utf-8"))


def test_review_matrix_has_300_cases_and_every_label():
    matrix = build_review_matrix()
    assert len(matrix) == 300
    assert {item.label for item in matrix} == {
        label for label, count in MATRIX_COUNTS.items() if count > 0
    }
    assert MATRIX_COUNTS["privacy_sensitive"] == 0


def test_quality_gate_is_conservative_and_aggregate_only():
    result = evaluate_quality(source()); validate_artifact(result)
    assert result["quality_gate"] == "PASS"
    assert result["reviewed_count"] == 300
    assert result["correct_rate"] == 0.95
    assert result["unsupported_rate"] == 0.01
    assert result["privacy_sensitive_count"] == 0
    assert result["stale_source_accepted_count"] == 0
    assert result["raw_content_persisted"] is False


def test_write_invariant_failure_prevents_review():
    value = source(); value["hard_invariants"]["automatic_active"] = 1
    try: evaluate_quality(value)
    except ValueError: pass
    else: raise AssertionError("unsafe Write Shadow was reviewed")


def test_cli_emits_pass(capsys):
    assert main(["--write-observation", str(WRITE)]) == 0
    assert json.loads(capsys.readouterr().out)["quality_gate"] == "PASS"


def test_committed_quality_record_binds_clean_revision():
    result = json.loads(QUALITY.read_text(encoding="utf-8")); validate_artifact(result)
    assert result["proposal_review_revision"] == "bfaab00"
    assert result["reviewed_count"] == 300
    assert result["quality_gate"] == "PASS"
    assert result["privacy_sensitive_count"] == 0
    assert result["long_term_memory_consumption"] == "BLOCKED"
