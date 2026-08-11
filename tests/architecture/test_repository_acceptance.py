from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RETIRED_RUNNER_MODULES = {
    "scripts.memory_budget_shadow_acceptance",
    "scripts.agent_runtime_stage47_2_acceptance",
    "scripts.langgraph_dual_workflow_acceptance",
    "scripts.langgraph_recovery_acceptance",
    "scripts.langgraph_stage47_acceptance",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_stage47_legacy_acceptance_runners_cannot_reappear():
    for module in RETIRED_RUNNER_MODULES:
        assert not (ROOT / f"{module.replace('.', '/')}.py").exists()


def test_active_python_does_not_import_retired_stage47_runners():
    findings = []
    for directory in ("app", "scripts", "tests"):
        for path in (ROOT / directory).rglob("*.py"):
            retired = _imported_modules(path) & RETIRED_RUNNER_MODULES
            if retired:
                findings.append(
                    f"{path.relative_to(ROOT)}: {', '.join(sorted(retired))}"
                )

    assert findings == []
