from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(tree: ast.AST) -> set[str]:
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


def _has_direct_environment_access(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "getenv":
                return True
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr == "getenv"
            ):
                return True
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
            and node.attr == "environ"
        ):
            return True
    return False


def test_process_environment_access_is_confined_to_runtime_config_boundary():
    allowed = APP / "runtime" / "config" / "environment.py"
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in sorted(APP.rglob("*.py"))
        if path != allowed and _has_direct_environment_access(_tree(path))
    ]
    assert offenders == []


def test_legacy_config_compatibility_exports_are_removed():
    for relative in (
        "app/services/config.py",
        "app/services/memory_config.py",
    ):
        assert not (ROOT / relative).exists()

    for path in sorted((*APP.rglob("*.py"), *(ROOT / "scripts").rglob("*.py"))):
        imported = _imports(_tree(path))
        assert "app.services.config" not in imported
        assert "app.services.memory_config" not in imported


def test_runtime_composition_keeps_only_the_root_container_as_mutable_state():
    tree = _tree(APP / "services" / "runtime.py")
    private_assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
        and target.id.startswith("_")
        and not target.id.isupper()
    }
    global_names = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Global)
        for name in node.names
    }

    assert private_assignments == {"_runtime_container"}
    assert global_names == {"_runtime_container"}


def test_runtime_has_no_local_embedding_dependency():
    vector_imports = _imports(
        _tree(APP / "adapters" / "pgvector" / "repository.py")
    )
    normalized_imports = {module.replace("-", "_").casefold() for module in vector_imports}
    requirements = {
        line.split("==", 1)[0].split(">=", 1)[0].strip().casefold()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "sentence_transformers" not in normalized_imports
    assert "sentence-transformers" not in requirements
    assert "langchain-huggingface" not in requirements


def test_runtime_failure_taxonomy_is_an_adapter_over_reliability_core():
    legacy = APP / "services" / "runtime_work.py"
    adapter = APP / "adapters" / "reliability" / "runtime_failure.py"

    assert not legacy.exists()
    adapter_imports = _imports(_tree(adapter))
    assert "app.runtime.reliability" in adapter_imports

    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in sorted(APP.rglob("*.py"))
        if "app.services.runtime_work" in _imports(_tree(path))
    ]
    assert offenders == []
