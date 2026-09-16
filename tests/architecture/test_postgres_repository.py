import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPOSITORIES = {
    "PostgresMessageRepository": (
        ROOT / "app" / "adapters" / "postgres" / "message_repository.py"
    ),
    "PostgresQuestionEvaluationRepository": (
        ROOT
        / "app"
        / "adapters"
        / "postgres"
        / "question_evaluation_repository.py"
    ),
    "PostgresReportRepository": (
        ROOT / "app" / "adapters" / "postgres" / "report_repository.py"
    ),
    "PostgresSessionRepository": (
        ROOT / "app" / "adapters" / "postgres" / "session_repository.py"
    ),
}
COMPATIBILITY_EXPORTS = (
    ROOT / "app" / "adapters" / "postgres" / "session_repositories.py"
)
FACADE = (
    ROOT
    / "app"
    / "adapters"
    / "persistence"
    / "postgres"
    / "session_store.py"
)
CORE_PRODUCTION_DIRECTORIES = (
    ROOT / "app" / "agents",
    ROOT / "app" / "api",
    ROOT / "app" / "application",
    ROOT / "app" / "domain",
    ROOT / "app" / "graphs",
    ROOT / "app" / "ports",
    ROOT / "app" / "runtime",
)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _called_attributes(node: ast.AST) -> set[str]:
    return {
        child.func.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
    }


def test_postgres_read_repositories_are_named_boundaries_without_transactions():
    for class_name, path in REPOSITORIES.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert _class(tree, class_name).name == class_name
        assert _called_attributes(tree).isdisjoint({"commit", "rollback"})


def test_postgres_repository_mutations_use_caller_owned_cursor():
    mutations = (
        ("PostgresMessageRepository", "replace_messages"),
        ("PostgresSessionRepository", "mark_deleting"),
        ("PostgresSessionRepository", "delete_session"),
        ("PostgresSessionRepository", "insert_state"),
        ("PostgresSessionRepository", "replace_state"),
        ("PostgresReportRepository", "upsert_report_record"),
        ("PostgresQuestionEvaluationRepository", "upsert_question_evaluation"),
    )

    for class_name, method_name in mutations:
        path = REPOSITORIES[class_name]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        method = _method(_class(tree, class_name), method_name)
        assert method.args.args[1].arg == "cursor"
        called = _called_attributes(method)
        assert "connection" not in called
        assert "commit" not in called
        assert "rollback" not in called


def test_session_repository_compatibility_module_is_removed():
    assert not COMPATIBILITY_EXPORTS.exists()


def test_postgres_session_facade_delegates_extracted_read_methods():
    tree = ast.parse(FACADE.read_text(encoding="utf-8"), filename=str(FACADE))
    facade = _class(tree, "PostgresInterviewSessionStore")
    extracted = {
        "list_messages",
        "get_report_record",
        "list_reports",
        "count_reports",
        "report_status_totals",
        "list_question_evaluations",
    }

    for name in extracted:
        called = _called_attributes(_method(facade, name))
        assert "connection" not in called, name
        assert "cursor" not in called, name
        assert "execute" not in called, name


def test_session_store_delegates_schema_mutation_to_postgres_adapter():
    source = FACADE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(FACADE))
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "PostgresSessionSchemaAdapter" in imports
    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "CREATE INDEX" not in source
    assert "_ensure_schema" not in source


def test_repository_split_does_not_create_parallel_port_tree_or_legacy_monolith():
    assert not (ROOT / "app" / "ports" / "repositories").exists()
    assert not (ROOT / "app" / "services" / "legacy_postgres_session.py").exists()


def test_core_production_layers_do_not_execute_sql_directly():
    offenders: list[str] = []
    for directory in CORE_PRODUCTION_DIRECTORIES:
        for path in sorted(directory.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"execute", "executemany"}
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "cursor"
                ):
                    continue
                offenders.append(
                    f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                )
    assert offenders == []


def test_runtime_postgres_probes_are_adapter_canonical_aliases():
    from app.adapters.postgres.principal_memory_migration import (
        PostgresPrincipalMemoryMigrationProbe as AdapterMigrationProbe,
    )
    from app.adapters.workflows.checkpointer_schema import (
        validate_checkpointer_schema as adapter_schema_validator,
    )
    from app.runtime.langgraph_runtime import validate_checkpointer_schema
    from app.runtime.principal_memory_operations import (
        PostgresPrincipalMemoryMigrationProbe,
    )

    assert validate_checkpointer_schema is adapter_schema_validator
    assert PostgresPrincipalMemoryMigrationProbe is AdapterMigrationProbe
