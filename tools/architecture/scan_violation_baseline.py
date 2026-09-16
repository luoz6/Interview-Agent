#!/usr/bin/env python3
"""P0B-T01: generate the dependency violation baseline from P0A-T02 facts."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"
OUTPUT_PATH = REPO_ROOT / "tests" / "architecture" / "dependency_violation_baseline.json"

DEPENDENCY_EDGES_PATH = ARTIFACTS_DIR / "dependency-edges.json"
INFRASTRUCTURE_IMPORTS_PATH = ARTIFACTS_DIR / "infrastructure-imports.json"

RULES = (
    {
        "name": "DOMAIN_MUST_NOT_IMPORT_APPLICATION",
        "source_layer": "domain",
        "target_layer": "application",
    },
    {
        "name": "DOMAIN_MUST_NOT_IMPORT_ADAPTERS",
        "source_layer": "domain",
        "target_layer": "adapters",
    },
    {
        "name": "DOMAIN_MUST_NOT_IMPORT_RUNTIME",
        "source_layer": "domain",
        "target_layer": "runtime",
    },
    {
        "name": "DOMAIN_MUST_NOT_IMPORT_SERVICES",
        "source_layer": "domain",
        "target_layer": "services",
    },
    {
        "name": "APPLICATION_MUST_NOT_IMPORT_ADAPTERS",
        "source_layer": "application",
        "target_layer": "adapters",
    },
    {
        "name": "APPLICATION_MUST_NOT_IMPORT_RUNTIME",
        "source_layer": "application",
        "target_layer": "runtime",
    },
    {
        "name": "APPLICATION_MUST_NOT_IMPORT_SERVICES",
        "source_layer": "application",
        "target_layer": "services",
    },
    {
        "name": "PORTS_MUST_NOT_IMPORT_ADAPTERS",
        "source_layer": "ports",
        "target_layer": "adapters",
    },
    {
        "name": "PORTS_MUST_NOT_IMPORT_RUNTIME",
        "source_layer": "ports",
        "target_layer": "runtime",
    },
    {
        "name": "PORTS_MUST_NOT_IMPORT_SERVICES",
        "source_layer": "ports",
        "target_layer": "services",
    },
)


def _removal_phase(rule: str, source_module: str, target_module: str) -> str:
    if rule == "APPLICATION_MUST_NOT_IMPORT_RUNTIME":
        return "P7"
    if rule == "PORTS_MUST_NOT_IMPORT_SERVICES":
        if "context" in target_module:
            return "P3"
        if "report" in target_module or "question_evaluations" in target_module:
            return "P4"
        return "P2"
    if rule == "APPLICATION_MUST_NOT_IMPORT_SERVICES":
        if "knowledge" in source_module:
            return "P3"
        if "materials" in source_module or "embedding_providers" in target_module:
            return "P2"
        if "interview" in source_module:
            return "P1"
        return "P2"
    return "P2"


def _reason(rule: str, source_layer: str, target_layer: str) -> str:
    return (
        f"{source_layer} layer directly imports {target_layer} layer; "
        "grandfathered as a pre-refactor architecture fact."
    )


def main() -> int:
    dependency_data = json.loads(
        DEPENDENCY_EDGES_PATH.read_text(encoding="utf-8")
    )
    edges = dependency_data.get("edges", [])

    violations: list[dict[str, Any]] = []
    for rule in RULES:
        grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "source_files": set(),
                "edge_count": 0,
            }
        )
        for edge in edges:
            if (
                edge.get("source_layer") == rule["source_layer"]
                and edge.get("target_layer") == rule["target_layer"]
            ):
                source = edge["source_module"]
                target = edge["target_module"]
                key = (source, target)
                grouped[key]["source_files"].add(edge["source_file"])
                grouped[key]["edge_count"] += 1

        for (source, target), data in sorted(grouped.items()):
            violations.append(
                {
                    "source": source,
                    "target": target,
                    "rule": rule["name"],
                    "status": "grandfathered",
                    "removal_phase": _removal_phase(
                        rule["name"], source, target
                    ),
                    "reason": _reason(
                        rule["name"],
                        rule["source_layer"],
                        rule["target_layer"],
                    ),
                    "edge_count": int(data["edge_count"]),
                    "source_files": sorted(data["source_files"]),
                }
            )

    rule_summary = {}
    for rule in RULES:
        rule_summary[rule["name"]] = len(
            [
                violation
                for violation in violations
                if violation["rule"] == rule["name"]
            ]
        )

    infrastructure_reference = None
    if INFRASTRUCTURE_IMPORTS_PATH.exists():
        infrastructure_data = json.loads(
            INFRASTRUCTURE_IMPORTS_PATH.read_text(encoding="utf-8")
        )
        infrastructure_reference = {
            "infrastructure_match_count": infrastructure_data["metadata"].get(
                "infrastructure_match_count"
            ),
            "classification_counts": infrastructure_data.get(
                "classification_counts", {}
            ),
        }

    result = {
        "generated_by": "tools/architecture/scan_violation_baseline.py",
        "source_artifacts": [
            "artifacts/architecture/dependency-edges.json",
            "artifacts/architecture/infrastructure-imports.json",
        ],
        "counting_rule": (
            "violations are unique source_module -> target_module pairs; "
            "edge_count is the raw import edge count for that pair"
        ),
        "rules": rule_summary,
        "violations": violations,
        "infrastructure_import_reference": infrastructure_reference,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT).as_posix()}")
    print(f"violation_count={len(violations)}")
    for rule_name, count in sorted(rule_summary.items()):
        print(f"{rule_name}={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
