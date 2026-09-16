from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
APPLICATION = APP / "application" / "interview" / "launch_prepared_interview.py"
PORT = APP / "ports" / "interview_entry.py"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return modules


def test_p1_canonical_application_has_no_infrastructure_imports():
    imported = _imported_modules(APPLICATION)
    assert not any(
        module == "app.services"
        or module.startswith("app.services.")
        or module == "app.adapters"
        or module.startswith("app.adapters.")
        or module == "app.runtime"
        or module.startswith("app.runtime.")
        or module.startswith("langgraph")
        or module.startswith("psycopg")
        for module in imported
    )


def test_p1_port_has_no_persistence_or_workflow_leakage():
    source = PORT.read_text(encoding="utf-8")
    forbidden = (
        "cursor",
        "psycopg",
        "durability",
        "postgres",
        "langgraph",
        "checkpoint",
        "redis",
    )
    for token in forbidden:
        assert token not in source
