from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVALUATION_RUNNER = ROOT / "scripts" / "evaluate_knowledge_retrieval.py"
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


def _is_forbidden_runtime_dependency(module: str) -> bool:
    return (
        module == "app.agents"
        or module.startswith("app.agents.")
        or module == "app.services.llm"
        or module.startswith("app.services.llm.")
        or module.startswith("app.services.report")
    )


def test_knowledge_retrieval_evaluation_has_no_llm_or_report_dependency():
    imported_modules = _imported_modules(EVALUATION_RUNNER)
    findings = {
        module
        for module in imported_modules
        if _is_forbidden_runtime_dependency(module)
    }

    assert findings == set()
