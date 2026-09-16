from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_DIRECT_DEPENDENCIES = frozenset(
    {
        "principal_memory_consume",
        "principal_memory_retrieval",
        "PostgresPrincipalMemoryFactStore",
        "get_principal_memory_fact_store",
        "get_principal_memory_consume_service",
        "ASSISTANCE_CONTEXT_KIND",
        "principal_memory_assistance_v1",
    }
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _symbols(tree: ast.AST) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            values.add(node.id)
        elif isinstance(node, ast.Attribute):
            values.add(node.attr)
        elif isinstance(node, ast.alias):
            values.add(node.name)
            if node.asname:
                values.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


def _protected_sink_paths() -> tuple[Path, ...]:
    paths = {
        ROOT / "app" / "graphs" / "durable_review_graph.py",
        ROOT / "app" / "adapters" / "pgvector" / "repository.py",
        ROOT / "app" / "adapters" / "knowledge" / "static_store.py",
        ROOT / "app" / "application" / "knowledge" / "grounding.py",
    }
    patterns = (
        "app/agents/*.py",
        "app/application/report/evaluator.py",
        "app/evals/*.py",
        "app/adapters/providers/*embedding*.py",
        "app/domain/knowledge/*.py",
        "app/domain/report/*.py",
        "scripts/load_knowledge*.py",
        "scripts/build_knowledge_manifest*.py",
        "scripts/evaluate_knowledge*.py",
        "scripts/evaluate_report*.py",
    )
    for pattern in patterns:
        paths.update(ROOT.glob(pattern))
    paths.discard(ROOT / "app" / "agents" / "examiner.py")
    return tuple(sorted(path for path in paths if path.exists()))


def test_principal_memory_consumer_is_absent_from_protected_sinks():
    paths = _protected_sink_paths()
    required = {
        ROOT / "app" / "agents" / "report_coach.py",
        ROOT / "app" / "agents" / "shadow_reviewer.py",
        ROOT / "app" / "graphs" / "durable_review_graph.py",
        ROOT / "app" / "application" / "report" / "evaluator.py",
        ROOT / "app" / "domain" / "report" / "models.py",
        ROOT / "app" / "domain" / "report" / "review_execution.py",
        ROOT / "app" / "domain" / "report" / "answer_guidance.py",
        ROOT / "app" / "application" / "knowledge" / "grounding.py",
        ROOT / "app" / "adapters" / "pgvector" / "repository.py",
        ROOT / "app" / "adapters" / "knowledge" / "static_store.py",
        ROOT / "app" / "adapters" / "providers" / "embedding_providers.py",
        ROOT / "scripts" / "load_knowledge_v2.py",
        ROOT / "scripts" / "build_knowledge_manifest_v2.py",
        ROOT / "scripts" / "evaluate_knowledge_retrieval_v2.py",
    }
    assert required.issubset(paths)

    offenders = []
    for path in paths:
        symbols = _symbols(_tree(path))
        matches = sorted(
            token
            for token in FORBIDDEN_DIRECT_DEPENDENCIES
            if any(token == value or token in value.split(".") for value in symbols)
        )
        if matches:
            offenders.append((path.relative_to(ROOT).as_posix(), matches))
    assert offenders == []


def test_public_knowledge_paths_do_not_accept_principal_scope():
    paths = (
        ROOT / "app" / "domain" / "knowledge" / "query.py",
        ROOT / "app" / "application" / "knowledge" / "grounding.py",
        ROOT / "app" / "adapters" / "pgvector" / "repository.py",
        ROOT / "app" / "domain" / "report" / "models.py",
        ROOT / "app" / "adapters" / "knowledge" / "trace.py",
    )
    forbidden = {"principal_memory", "normalized_fact"}
    for path in paths:
        symbols = _symbols(_tree(path))
        assert not any(
            token == value or token in value.casefold().split(".")
            for token in forbidden
            for value in symbols
        ), path


def test_principal_deletion_has_no_public_knowledge_dependency():
    path = ROOT / "app" / "application" / "memory" / "deletion.py"
    tree = _tree(path)
    imported = {
        node.module.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported.update(
        alias.name.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        token in module
        for token in ("vector", "knowledge", "embedding")
        for module in imported
    )


def test_consumer_is_wired_only_before_durable_interview_followup():
    graph = _tree(ROOT / "app" / "graphs" / "durable_interview_graph.py")
    calls = [
        (node.lineno, ast.unparse(node.func))
        for node in ast.walk(graph)
        if isinstance(node, ast.Call)
    ]

    prepare = next(line for line, name in calls if name.endswith(".prepare"))
    finalize = next(line for line, name in calls if name.endswith(".finalize"))
    followup = next(
        line for line, name in calls if name.endswith(".stream_followup_attempt")
    )
    assert prepare < finalize < followup

    runtime = _tree(ROOT / "app" / "runtime" / "composition.py")
    assert any(
        keyword.arg == "principal_memory_consumer"
        and isinstance(keyword.value, ast.Call)
        and isinstance(keyword.value.func, ast.Name)
        and keyword.value.func.id == "get_principal_memory_consume_service"
        for node in ast.walk(runtime)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
    )
