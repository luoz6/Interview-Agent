from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.runtime.config.memory import load_effective_memory_config


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_IMPLEMENTATION_PATHS = (
    ROOT / "app" / "services" / "principal_memory_consumption.py",
    ROOT / "app" / "ports" / "principal_memory_consumption.py",
    ROOT / "app" / "api" / "principal_memory_consumption.py",
)


def test_principal_memory_consumption_remains_fail_closed_and_unrouted():
    assert all(not path.exists() for path in FORBIDDEN_IMPLEMENTATION_PATHS)

    route_literals: set[str] = set()
    imported_names: set[str] = set()
    for path in (ROOT / "app" / "api").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        route_literals.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        imported_names.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        )

    assert not any("/principal-memory/consume" in value for value in route_literals)
    assert "get_principal_memory_consumer" not in imported_names

    with pytest.raises(ValueError, match="consume is not supported"):
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})
