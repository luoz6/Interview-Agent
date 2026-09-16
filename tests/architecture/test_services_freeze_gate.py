from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

from app.runtime.celery_app import celery_app


ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = ROOT / "app" / "services"
DEPENDENCY_BASELINE = (
    ROOT / "tests" / "architecture" / "dependency_violation_baseline.json"
)

CANONICAL_TASK_MODULES = {
    "app.runtime.interview_workflow_tasks",
    "app.runtime.principal_memory_tasks",
    "app.runtime.review_workflow_tasks",
    "app.runtime.round_review_tasks",
}
STABLE_TASK_NAMES = {
    "app.services.interview_workflow_tasks.run_interview_workflow_event",
    "app.services.principal_memory_tasks.run_principal_memory_proposal_event",
    "app.services.review_workflow_tasks.run_review_workflow_event",
    "app.services.round_review_tasks.run_closed_round_review",
}


def _services_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if alias.name == "app.services"
                or alias.name.startswith("app.services.")
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "app.services" or module.startswith("app.services."):
                imports.append((node.lineno, module))
    return imports


def test_services_directory_is_retired():
    assert not SERVICES_DIR.exists()


def test_python_sources_do_not_import_services():
    offenders: list[str] = []
    for top_level in ("app", "tests", "scripts"):
        for path in sorted((ROOT / top_level).rglob("*.py")):
            for line, module in _services_imports(path):
                offenders.append(
                    f"{path.relative_to(ROOT).as_posix()}:{line} -> {module}"
                )
    assert offenders == []


def test_dependency_baseline_has_no_services_exceptions():
    baseline = json.loads(DEPENDENCY_BASELINE.read_text(encoding="utf-8"))
    offenders = [
        violation
        for violation in baseline.get("violations", [])
        if str(violation.get("source", "")).startswith("app.services")
        or str(violation.get("target", "")).startswith("app.services")
    ]
    assert offenders == []


def test_celery_loads_canonical_modules_with_stable_task_names():
    assert set(celery_app.conf.include) == CANONICAL_TASK_MODULES
    for module in sorted(CANONICAL_TASK_MODULES):
        importlib.import_module(module)
    assert STABLE_TASK_NAMES.issubset(celery_app.tasks)
