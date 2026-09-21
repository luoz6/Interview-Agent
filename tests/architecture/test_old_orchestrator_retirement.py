from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_old_orchestrator_agent_and_graph_are_removed():
    assert not (APP / "agents" / "orchestrator.py").exists()
    assert not (APP / "graphs" / "orchestrator_graph.py").exists()
    assert "OrchestratorAgent" not in (APP / "agents" / "__init__.py").read_text(
        encoding="utf-8"
    )


def test_session_stores_do_not_recreate_generic_command_routing():
    for relative_path in (
        "adapters/memory/session_store.py",
        "adapters/persistence/postgres/session_store.py",
    ):
        path = APP / relative_path
        source = path.read_text(encoding="utf-8")
        imports = _imported_modules(path)

        assert "app.agents.orchestrator" not in imports
        assert "app.graphs.orchestrator_graph" not in imports
        assert "_orchestrator" not in source
        assert ".apply_command(" not in source


def test_legacy_drain_runner_exposes_only_explicit_session_operations():
    source = (APP / "graphs" / "interview_graph.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    runner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "InterviewGraphRunner"
    )
    methods = {
        node.name for node in runner.body if isinstance(node, ast.FunctionDef)
    }

    assert {"submit_answer", "prepare_answer", "finalize_prepared_answer"} <= methods
    assert {"skip", "finish"} <= methods
    assert "apply_command" not in methods
