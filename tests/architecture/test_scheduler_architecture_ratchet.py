from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import scan_dependencies as scanner  # noqa: E402


FORBIDDEN_DEPENDENCIES = (
    (
        "application.scheduling -> adapters",
        "app.application.scheduling",
        "app.adapters",
    ),
    (
        "application.scheduling -> runtime",
        "app.application.scheduling",
        "app.runtime",
    ),
    (
        "application.scheduling -> app.a2a",
        "app.application.scheduling",
        "app.a2a",
    ),
    ("ports -> app.a2a", "app.ports", "app.a2a"),
    ("domain -> app.a2a", "app.domain", "app.a2a"),
)


def _is_module_or_descendant(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _violations(scan_result: dict[str, Any]) -> list[tuple[str, str, str, int]]:
    violations: set[tuple[str, str, str, int]] = set()
    for edge in scan_result["edges"]:
        for rule, source_prefix, target_prefix in FORBIDDEN_DEPENDENCIES:
            if _is_module_or_descendant(
                edge["source_module"], source_prefix
            ) and _is_module_or_descendant(edge["target_module"], target_prefix):
                violations.add(
                    (
                        rule,
                        edge["source_module"],
                        edge["target_module"],
                        edge["line"],
                    )
                )
    return sorted(violations)


def _write(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_scheduler_architecture_has_no_forbidden_dependencies():
    result = scanner.scan_app()

    assert result["parse_errors"] == []
    assert _violations(result) == []


def test_scheduler_architecture_gate_detects_every_rule(tmp_path):
    app_root = tmp_path / "app"
    _write(
        app_root / "application" / "scheduling" / "illegal.py",
        "\n".join(
            (
                "import app.adapters.client",
                "from ...adapters import repository",
                "from app.runtime import container",
                "from app.a2a import protocol",
            )
        ),
    )
    _write(
        app_root / "ports" / "illegal.py",
        "from app.a2a import transport\n",
    )
    _write(
        app_root / "domain" / "illegal.py",
        "import app.a2a.messages\n",
    )

    result = scanner.scan_app(app_root)
    violations = _violations(result)

    assert result["parse_errors"] == []
    assert result["unresolved_relative_imports"] == []
    assert {violation[0] for violation in violations} == {
        rule for rule, _, _ in FORBIDDEN_DEPENDENCIES
    }
    assert len(violations) == 6
    assert (
        "application.scheduling -> adapters",
        "app.application.scheduling.illegal",
        "app.adapters",
        2,
    ) in violations
