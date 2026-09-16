from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import scan_duplicate_implementations as scanner  # noqa: E402


def test_duplicate_scan_is_parse_clean_and_covers_every_category():
    result = scanner.scan()

    assert result["metadata"]["parse_error_count"] == 0
    assert result["metadata"]["files_scanned"] > 0
    assert set(result["categories"]) == {
        "exact_implementations",
        "repository_families",
        "dto_shapes",
        "evaluator_names",
        "runtime_wiring_fingerprints",
        "version_modules",
        "version_symbols",
    }


def test_duplicate_scan_artifact_matches_current_tree():
    current = scanner.scan()
    frozen = json.loads(scanner.JSON_PATH.read_text(encoding="utf-8"))

    assert frozen == current


def test_non_exact_groups_never_claim_confirmed_duplicates():
    result = scanner.scan()
    for category, groups in result["categories"].items():
        expected = "confirmed_exact" if category == "exact_implementations" else "candidate"
        assert {group["status"] for group in groups}.issubset({expected})


def test_every_duplicate_group_has_review_disposition():
    result = scanner.scan()

    for groups in result["categories"].values():
        for group in groups:
            assert group["disposition"]
            assert group["review_priority"] in {"high", "medium", "low"}
            assert group["review_note"]


def test_reviewed_findings_distinguish_variants_from_consolidation_candidates():
    result = scanner.scan()
    dispositions = {
        finding["disposition"] for finding in result["reviewed_findings"]
    }

    assert "overlapping_port_contracts" in dispositions
    assert "canonical_owner_candidate" in dispositions
    assert "expected_polymorphism" in dispositions
    assert "composition_wrapper" in dispositions
    assert "migration_dependency_present" in dispositions
