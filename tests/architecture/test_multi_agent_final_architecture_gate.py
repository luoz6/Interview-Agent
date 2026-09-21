from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import scan_dependencies as dependency_scanner  # noqa: E402
import scan_infrastructure_imports as infrastructure_scanner  # noqa: E402


INTERNAL_RULES = (
    (
        "domain -> infrastructure",
        "app.domain",
        (
            "app.adapters",
            "app.application",
            "app.graphs",
            "app.runtime",
            "app.services",
            "app.a2a",
        ),
    ),
    (
        "application -> adapters",
        "app.application",
        ("app.adapters",),
    ),
    (
        "application -> runtime",
        "app.application",
        ("app.runtime",),
    ),
    (
        "application -> A2A implementation",
        "app.application",
        ("app.a2a",),
    ),
    (
        "ports -> A2A implementation",
        "app.ports",
        ("app.a2a",),
    ),
)


def _is_module_or_descendant(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _internal_violations(
    scan_result: dict[str, Any],
) -> list[tuple[str, str, str, int]]:
    violations: set[tuple[str, str, str, int]] = set()
    for edge in scan_result["edges"]:
        for rule, source_prefix, target_prefixes in INTERNAL_RULES:
            if not _is_module_or_descendant(
                edge["source_module"], source_prefix
            ):
                continue
            if any(
                _is_module_or_descendant(edge["target_module"], prefix)
                for prefix in target_prefixes
            ):
                violations.add(
                    (
                        rule,
                        edge["source_module"],
                        edge["target_module"],
                        edge["line"],
                    )
                )
    return sorted(violations)


def _domain_external_infrastructure_violations(
    scan_result: dict[str, Any],
) -> list[tuple[str, str, str, int]]:
    violations = []
    for edge in scan_result["external_edges"]:
        if edge["source_layer"] != "domain":
            continue
        package = infrastructure_scanner.package_for(edge["target_module"])
        if package is None:
            continue
        classification, _reason = infrastructure_scanner.classify(
            package,
            edge["source_layer"],
        )
        if classification == "明确违规":
            violations.append(
                (
                    "domain -> infrastructure",
                    edge["source_module"],
                    edge["target_module"],
                    edge["line"],
                )
            )
    return sorted(violations)


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_multi_agent_final_architecture_has_five_zero_boundaries() -> None:
    result = dependency_scanner.scan_app()

    assert result["parse_errors"] == []
    assert result["unresolved_relative_imports"] == []
    assert _internal_violations(result) == []
    assert _domain_external_infrastructure_violations(result) == []


def test_multi_agent_final_architecture_gate_detects_every_rule(tmp_path) -> None:
    app_root = tmp_path / "app"
    _write(
        app_root / "domain" / "illegal.py",
        "import app.adapters.database\nimport psycopg\n",
    )
    _write(
        app_root / "application" / "illegal.py",
        "\n".join(
            (
                "import app.adapters.repository",
                "from app.runtime import composition",
                "from app.a2a.invocation import local",
            )
        ),
    )
    _write(
        app_root / "ports" / "illegal.py",
        "from app.a2a import protocol\n",
    )

    result = dependency_scanner.scan_app(app_root)
    violations = (
        _internal_violations(result)
        + _domain_external_infrastructure_violations(result)
    )

    assert result["parse_errors"] == []
    assert result["unresolved_relative_imports"] == []
    assert {violation[0] for violation in violations} == {
        rule for rule, _source, _targets in INTERNAL_RULES
    }
    assert len(violations) == 6
