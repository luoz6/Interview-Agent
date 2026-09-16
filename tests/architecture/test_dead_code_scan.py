from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import scan_dead_code as scanner  # noqa: E402


def test_dead_code_scan_is_parse_clean_and_covers_three_evidence_sources():
    result = scanner.scan()

    assert result["metadata"]["parse_error_count"] == 0
    assert result["metadata"]["app_files_scanned"] > 0
    assert result["metadata"]["script_files_scanned"] > 0
    assert result["metadata"]["test_files_scanned"] > 0


def test_dead_code_artifact_matches_current_tree():
    current = scanner.scan()
    frozen = json.loads(scanner.JSON_PATH.read_text(encoding="utf-8"))

    assert frozen == current


def test_deletion_review_candidates_are_strictly_triple_zero():
    result = scanner.scan()

    for category in (
        "triple_zero_module_candidates",
        "triple_zero_symbol_candidates",
    ):
        for candidate in result[category]:
            assert candidate["static_references"] == 0
            assert candidate["runtime_wiring_references"] == 0
            assert candidate["test_references"] == 0
            assert candidate["decision"] == "triple_zero_deletion_review_candidate"


def test_package_initializers_and_main_entrypoint_are_not_deletion_candidates():
    result = scanner.scan()
    candidates = {
        row["module"] for row in result["triple_zero_module_candidates"]
    }

    assert "app.main" not in candidates
    assert all(not row["file"].endswith("/__init__.py") for row in result["triple_zero_module_candidates"])


def test_test_only_code_is_not_reported_as_triple_zero():
    result = scanner.scan()
    triple_zero_modules = {
        row["module"] for row in result["triple_zero_module_candidates"]
    }
    test_only_modules = {row["module"] for row in result["test_only_modules"]}
    triple_zero_symbols = {
        (row["module"], row["name"])
        for row in result["triple_zero_symbol_candidates"]
    }
    test_only_symbols = {
        (row["module"], row["name"]) for row in result["test_only_symbols"]
    }

    assert triple_zero_modules.isdisjoint(test_only_modules)
    assert triple_zero_symbols.isdisjoint(test_only_symbols)


def test_symbols_inside_candidate_modules_are_not_double_counted():
    result = scanner.scan()
    candidate_modules = {
        row["module"] for row in result["triple_zero_module_candidates"]
    }

    assert all(
        row["module"] not in candidate_modules
        for row in result["triple_zero_symbol_candidates"]
    )
    assert all(
        row["module"] in candidate_modules
        for row in result["symbols_in_triple_zero_modules"]
    )


def test_reviewed_dispositions_preserve_public_and_test_only_boundaries():
    result = scanner.scan()
    dispositions = {
        row["disposition"] for row in result["reviewed_findings"]
    }

    assert "dedicated_cleanup_candidate" in dispositions
    assert "higher_confidence_cleanup_candidate" in dispositions
    assert "external_contract_review_required" in dispositions
    assert "retain" in dispositions
    assert "reviewed_no_candidate_target" in dispositions
