import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTBOX_REPOSITORY = (
    ROOT / "app" / "adapters" / "postgres" / "runtime_outbox_repository.py"
)
RECEIPT_REPOSITORY = (
    ROOT / "app" / "adapters" / "postgres" / "runtime_receipt_repository.py"
)
COMPATIBILITY_EXPORTS = (
    ROOT / "app" / "adapters" / "postgres" / "runtime_repositories.py"
)
FACADE = ROOT / "app" / "services" / "postgres_runtime_control.py"
RUNTIME_COMPOSITION = ROOT / "app" / "services" / "runtime.py"
LANGGRAPH_RUNTIME = ROOT / "app" / "services" / "langgraph_runtime.py"


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


def _attributes(node: ast.AST) -> set[str]:
    return {
        child.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute)
    }


def test_runtime_outbox_repository_is_a_real_sql_boundary():
    tree = ast.parse(
        OUTBOX_REPOSITORY.read_text(encoding="utf-8"),
        filename=str(OUTBOX_REPOSITORY),
    )
    repository = _class(tree, "PostgresRuntimeOutboxRepository")

    mutations = {
        "enqueue_event",
        "claim_batch",
        "mark_published",
        "mark_retrying",
        "extend_outbox_lease",
        "extend_outbox_leases",
        "mark_dead_letter",
        "release_expired_leases",
        "replay_dead_letter",
    }
    for name in mutations:
        method = _method(repository, name)
        assert method.args.args[1].arg == "cursor", name
        assert _called_attributes(method).isdisjoint(
            {"connection", "commit", "rollback"}
        ), name

    assert "execute" in _called_attributes(_method(repository, "enqueue_event"))
    assert _called_attributes(repository).isdisjoint({"commit", "rollback"})

    receipt_tree = ast.parse(
        RECEIPT_REPOSITORY.read_text(encoding="utf-8"),
        filename=str(RECEIPT_REPOSITORY),
    )
    receipt = _class(receipt_tree, "PostgresRuntimeReceiptRepository")
    receipt_mutations = {
        "reset_dead_letter",
        "claim_receipt",
        "mark_receipt_retrying",
        "complete_round_review",
        "fail_round_review",
    }
    for name in receipt_mutations:
        method = _method(receipt, name)
        assert method.args.args[1].arg == "cursor", name
        assert _called_attributes(method).isdisjoint(
            {"connection", "commit", "rollback"}
        ), name

    reset = _method(receipt, "reset_dead_letter")
    assert "execute" in _called_attributes(reset)
    assert _called_attributes(receipt).isdisjoint({"commit", "rollback"})


def test_runtime_repository_compatibility_module_is_removed():
    assert not COMPATIBILITY_EXPORTS.exists()


def test_runtime_control_facade_delegates_extracted_outbox_methods():
    tree = ast.parse(FACADE.read_text(encoding="utf-8"), filename=str(FACADE))
    facade = _class(tree, "PostgresRuntimeControlStore")
    extracted = {
        "enqueue_event",
        "count_outbox",
        "list_outbox",
        "list_runtime_events",
        "list_recovery_events",
        "claim_batch",
        "mark_published",
        "mark_retrying",
        "extend_outbox_lease",
        "extend_outbox_leases",
        "mark_dead_letter",
        "release_expired_leases",
        "replay_dead_letter",
        "claim_receipt",
        "get_receipt",
        "mark_receipt_retrying",
        "complete_round_review",
        "fail_round_review",
    }

    for name in extracted:
        called = _called_attributes(_method(facade, name))
        assert "execute" not in called, name
        assert "cursor" not in called, name

    mutation_runner = _method(facade, "_run_mutation")
    called = _called_attributes(mutation_runner)
    assert "commit" in called
    assert "cursor" in _attributes(mutation_runner)
    assert "rollback" not in called

    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "PostgresUnitOfWork" in imports


def test_runtime_control_store_delegates_schema_mutation_to_postgres_adapter():
    source = FACADE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(FACADE))
    imports = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "PostgresRuntimeControlSchemaAdapter" in imports
    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "CREATE INDEX" not in source
    assert "_ensure_schema" not in source


def test_runtime_repository_split_does_not_add_parallel_ports():
    assert not (ROOT / "app" / "ports" / "repositories").exists()


def test_runtime_composition_never_owns_schema_migration():
    runtime_tree = ast.parse(
        RUNTIME_COMPOSITION.read_text(encoding="utf-8"),
        filename=str(RUNTIME_COMPOSITION),
    )
    imported_modules = {
        node.module
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    called_names = {
        node.func.id
        for node in ast.walk(runtime_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "app.services.postgres_runtime_migrations" not in imported_modules
    assert "migrate_postgres_runtime" not in called_names

    langgraph_tree = ast.parse(
        LANGGRAPH_RUNTIME.read_text(encoding="utf-8"),
        filename=str(LANGGRAPH_RUNTIME),
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setup"
        for node in ast.walk(langgraph_tree)
    )
