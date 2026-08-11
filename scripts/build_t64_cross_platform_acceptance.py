from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "interview-quality-v1-t64-cross-platform-acceptance-v1"
ACCEPTANCE_ID = "t64-cross-platform-acceptance-v1"
DEFAULT_OUTPUT = Path(
    "tests/golden/interview_quality_v1/t64-cross-platform-acceptance-v1.json"
)
T64_TEST = "tests/acceptance/test_t64_cross_platform_acceptance.py"
REQUIRED_PLATFORMS = ("windows-11-x64", "ubuntu-24.04-x64")
REQUIRED_COMMANDS = (
    "python_full_pytest",
    "postgres_marked_pytest",
    "migration_restore",
    "npm_ci_root",
    "npm_ci_frontend",
    "eslint",
    "vitest",
    "frontend_build",
    "playwright_preflight",
    "playwright_browser",
    "quality_replays",
)


def _requirement(
    identifier: str,
    requirement: str,
    evidence_codes: list[str],
    test_nodes: list[str],
) -> dict[str, Any]:
    return {
        "id": identifier,
        "requirement": requirement,
        "evidence_codes": sorted(evidence_codes),
        "test_nodes": sorted(test_nodes),
    }


REQUIREMENTS = (
    _requirement("T64-M01", "run Windows 11 x64", ["windows_11_x64"], [f"{T64_TEST}::test_t64_gate_accepts_complete_target_matrix"]),
    _requirement("T64-M02", "run Ubuntu 24.04 LTS x64", ["ubuntu_24_04_x64"], [f"{T64_TEST}::test_t64_gate_accepts_complete_target_matrix"]),
    _requirement("T64-M03", "run Python 3.11 from an absolute executable path", ["python_3_11", "python_absolute_path"], [f"{T64_TEST}::test_t64_gate_rejects_wrong_toolchain_or_relative_python"]),
    _requirement("T64-M04", "run Node 22 LTS", ["node_22"], [f"{T64_TEST}::test_t64_gate_rejects_wrong_toolchain_or_relative_python"]),
    _requirement("T64-M05", "run PostgreSQL 16", ["postgresql_16"], [f"{T64_TEST}::test_t64_gate_rejects_wrong_toolchain_or_relative_python"]),
    _requirement("T64-M06", "run project-locked Playwright Chromium", ["playwright_1_61_1", "chromium_149_0_7827_55"], [f"{T64_TEST}::test_t64_gate_rejects_wrong_toolchain_or_relative_python"]),
    _requirement("T64-C01", "run full Python pytest", ["python_full_pytest"], [f"{T64_TEST}::test_t64_gate_rejects_missing_or_failed_required_command", "tests/contracts/test_t64_platform_matrix.py::test_t64_platform_runner_parses_pytest_counts_and_skip_identity"]),
    _requirement("T64-C02", "run PostgreSQL-marked tests with a reachable DSN", ["postgres_marked_pytest", "postgres_dsn_configured"], [f"{T64_TEST}::test_t64_gate_rejects_postgres_skip_or_missing_dsn"]),
    _requirement("T64-C03", "run migration and backup restore", ["migration_restore"], [f"{T64_TEST}::test_t64_gate_rejects_missing_or_failed_required_command"]),
    _requirement("T64-C04", "run root and frontend npm ci", ["npm_ci_root", "npm_ci_frontend"], [f"{T64_TEST}::test_t64_gate_rejects_missing_or_failed_required_command"]),
    _requirement("T64-C05", "run ESLint", ["eslint"], [f"{T64_TEST}::test_t64_gate_rejects_missing_or_failed_required_command"]),
    _requirement("T64-C06", "run Vitest", ["vitest"], [f"{T64_TEST}::test_t64_gate_rejects_missing_or_failed_required_command"]),
    _requirement("T64-C07", "run frontend production build", ["frontend_build"], [f"{T64_TEST}::test_t64_gate_rejects_missing_or_failed_required_command"]),
    _requirement("T64-C08", "run Playwright preflight and browser tests", ["playwright_preflight", "playwright_browser"], [f"{T64_TEST}::test_t64_gate_rejects_missing_or_failed_required_command", "tests/contracts/test_t64_platform_matrix.py::test_t64_platform_runner_parses_playwright_skip_reason"]),
    _requirement("T64-C09", "replay all four frozen quality evaluations", ["initial_question_replay", "followup_decision_replay", "report_score_replay", "report_semantic_replay"], [f"{T64_TEST}::test_t64_gate_requires_all_four_zero_provider_replays"]),
    _requirement("T64-C10", "clean ports processes traces screenshots and temporary database objects", ["cleanup_zero_residue"], [f"{T64_TEST}::test_t64_gate_rejects_cleanup_residue"]),
    _requirement("T64-R01", "allow zero required failures and zero blocking skips", ["zero_failures", "zero_blocking_skips"], [f"{T64_TEST}::test_t64_gate_rejects_missing_or_failed_required_command"]),
    _requirement("T64-R02", "never accept PostgreSQL skips caused by a missing DSN", ["zero_missing_dsn_skips"], [f"{T64_TEST}::test_t64_gate_rejects_postgres_skip_or_missing_dsn"]),
    _requirement("T64-R03", "give every non-blocking skip a reason and owner", ["skip_inventory_owned"], [f"{T64_TEST}::test_t64_gate_rejects_unowned_or_blocking_skip", "tests/contracts/test_t64_platform_matrix.py::test_t64_platform_runner_unknown_skip_is_blocking"]),
    _requirement("T64-R04", "reject substitute Python Node OS database or browser versions", ["target_versions_exact"], [f"{T64_TEST}::test_t64_gate_rejects_wrong_toolchain_or_relative_python"]),
    _requirement("T64-R05", "freeze one clean provider candidate revision and tree", ["candidate_revision_frozen", "candidate_tree_frozen", "source_clean"], [f"{T64_TEST}::test_t64_gate_rejects_revision_tree_or_cleanliness_drift"]),
)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_acceptance() -> dict[str, Any]:
    requirements = [dict(item) for item in REQUIREMENTS]
    nodes = sorted({node for item in requirements for node in item["test_nodes"]})
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "acceptance_id": ACCEPTANCE_ID,
        "plan_task": "T64",
        "required_platforms": list(REQUIRED_PLATFORMS),
        "required_commands": list(REQUIRED_COMMANDS),
        "target_toolchain": {
            "python": "3.11.x",
            "node": "22.x",
            "postgresql": "16.x",
            "playwright": "1.61.1",
            "chromium": "149.0.7827.55",
        },
        "required_quality_replays": [
            "initial_question",
            "followup_decision",
            "report_score",
            "report_semantic",
        ],
        "skip_policy": "zero_blocking_and_every_nonblocking_owned",
        "postgres_missing_dsn_skips_allowed": 0,
        "provider_calls_expected": 0,
        "requirement_count": len(requirements),
        "unique_test_node_count": len(nodes),
        "requirements": requirements,
        "unique_test_nodes": nodes,
    }
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


def validate_acceptance(payload: dict[str, Any], *, root: Path) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("acceptance_id") != ACCEPTANCE_ID:
        raise ValueError("T64 acceptance identity drifted")
    if payload.get("required_platforms") != list(REQUIRED_PLATFORMS):
        raise ValueError("T64 platform matrix drifted")
    if payload.get("required_commands") != list(REQUIRED_COMMANDS):
        raise ValueError("T64 command matrix drifted")
    if payload.get("requirement_count") != len(REQUIREMENTS):
        raise ValueError("T64 requirement count drifted")
    requirements = payload.get("requirements", [])
    if [item.get("id") for item in requirements] != [item["id"] for item in REQUIREMENTS]:
        raise ValueError("T64 requirement IDs or order drifted")
    nodes = sorted({node for item in requirements for node in item.get("test_nodes", [])})
    if payload.get("unique_test_nodes") != nodes or payload.get("unique_test_node_count") != len(nodes):
        raise ValueError("T64 test-node projection drifted")
    for item in requirements:
        if not item.get("evidence_codes") or not item.get("test_nodes"):
            raise ValueError("T64 requirement evidence mapping is incomplete")
    for node in nodes:
        path, separator, test_name = node.partition("::")
        if not separator or not test_name or not (root / path).is_file():
            raise ValueError(f"T64 test node is missing: {node}")
    copy = dict(payload)
    digest = copy.pop("canonical_sha256", None)
    if digest != _canonical_sha256(copy):
        raise ValueError("T64 acceptance canonical hash drifted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    payload = build_acceptance()
    validate_acceptance(payload, root=root)
    output = args.output if args.output.is_absolute() else root / args.output
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("checked-in T64 acceptance differs from deterministic builder")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")
    print(payload["canonical_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
