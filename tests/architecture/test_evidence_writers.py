from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROTECTED_EVIDENCE_RUNNERS = (
    ROOT / "scripts" / "memory_budget_shadow_observe.py",
    ROOT / "scripts" / "memory_operational_input_evidence.py",
    ROOT / "scripts" / "memory_operational_shadow_acceptance.py",
    ROOT / "scripts" / "memory_validation_foundation_acceptance.py",
    ROOT / "scripts" / "memory_production_budget_shadow_observation.py",
    ROOT / "scripts" / "memory_production_budget_shadow_readiness.py",
    ROOT / "scripts" / "memory_production_budget_shadow_window.py",
    ROOT / "scripts" / "memory_production_shadow_change_preflight.py",
    ROOT / "scripts" / "memory_production_shadow_evidence_manifest.py",
)
PROTECTED_LEGACY_PATH_CONSUMERS = (
    ROOT / "scripts" / "memory_budget_shadow_observe.py",
    ROOT / "scripts" / "memory_shadow_status.py",
)
LEGACY_OPERATIONAL_INPUT_PATHS = frozenset(
    {
        "docs/memory-validation-operational-evidence.json",
        "docs/memory-operational-regression-evidence.json",
        "docs/memory-shadow-staging-acceptance.md",
        "docs/memory-shadow-status.json",
        "docs/memory-shadow-security-review-evidence.json",
    }
)
LEGACY_SYMBOLS = frozenset(
    {
        "DEFAULT_CONTRACTS",
        "database_fingerprint",
        "build_blocked_evidence",
        "validate_preflight_evidence",
        "OBSERVATION_OUTPUT_NOT_EXTERNAL",
        "WINDOW_PATH_NOT_EXTERNAL",
    }
)
RETIRED_COMMITTED_MACHINE_RECORDS = frozenset(
    {
        "local-v1-hardening-acceptance.json",
        "memory-budget-shadow-observation.json",
        "memory-operational-regression-evidence.json",
        "memory-operational-shadow-evidence.json",
        "memory-production-budget-shadow-readiness-evidence.json",
        "memory-production-shadow-approval-evidence.json",
        "memory-production-shadow-change-preflight-evidence.json",
        "memory-production-shadow-evidence-manifest.json",
        "memory-shadow-restore-drill-evidence.json",
        "memory-shadow-security-review-evidence.json",
        "memory-shadow-staging-acceptance.md",
        "memory-shadow-status.json",
        "memory-validation-operational-evidence.json",
        "principal-memory-lifecycle-drill-evidence.json",
        "principal-memory-proposal-quality.json",
        "principal-memory-read-shadow-observation.json",
        "principal-memory-write-shadow-observation.json",
    }
)


def _is_historical_machine_evidence_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return (
        normalized.startswith("docs/memory-production")
        and normalized.endswith("-evidence.json")
    ) or normalized in LEGACY_OPERATIONAL_INPUT_PATHS


def test_protected_evidence_runners_use_shared_writer_without_legacy_paths():
    findings: list[str] = []
    for path in PROTECTED_EVIDENCE_RUNNERS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        symbols = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        symbols.update(
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        )
        string_literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        direct_writes = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
        ]

        if not any(module.startswith("contracts.evidence") for module in imported_modules):
            findings.append(f"{path.name}:missing-shared-evidence-import")
        if direct_writes:
            findings.append(f"{path.name}:direct-write-text:{direct_writes}")
        if LEGACY_SYMBOLS.intersection(symbols):
            findings.append(f"{path.name}:legacy-symbol")
        if any(
            _is_historical_machine_evidence_path(value)
            for value in string_literals
        ):
            findings.append(f"{path.name}:historical-docs-evidence-path")

    assert findings == []


def test_budget_and_status_consumers_do_not_restore_legacy_operational_inputs():
    findings: list[str] = []
    for path in PROTECTED_LEGACY_PATH_CONSUMERS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        string_literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if any(
            _is_historical_machine_evidence_path(value)
            for value in string_literals
        ):
            findings.append(f"{path.name}:historical-docs-evidence-path")

    assert findings == []


def test_retired_committed_machine_records_remain_deleted():
    restored = sorted(
        name
        for name in RETIRED_COMMITTED_MACHINE_RECORDS
        if (ROOT / "docs" / name).exists()
    )

    assert restored == []
