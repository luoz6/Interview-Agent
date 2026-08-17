import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "app" / "api"
DOMAIN_ROUTE_MODULES = (
    API / "deletion" / "routes.py",
    API / "interview" / "routes.py",
    API / "memory" / "routes.py",
    API / "materials" / "routes.py",
    API / "plans" / "routes.py",
    API / "prep" / "routes.py",
    API / "reports" / "routes.py",
    API / "runtime" / "routes.py",
    API / "rag" / "routes.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_legacy_routes_compatibility_facade_is_removed():
    assert not (API / "routes.py").exists()


def test_router_composition_and_main_do_not_import_legacy_monolith():
    for path in (API / "router.py", ROOT / "app" / "main.py"):
        tree = _tree(path)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "app.api.routes" not in imported


def test_domain_routers_do_not_depend_on_legacy_facade():
    for path in DOMAIN_ROUTE_MODULES:
        tree = _tree(path)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "app.api.routes" not in imported, path


def test_composed_openapi_has_expected_unique_operation_inventory():
    from app.main import app

    schema = app.openapi()
    methods = {"get", "post", "put", "patch", "delete"}
    operations = [
        (path, method)
        for path, item in schema["paths"].items()
        for method in item
        if method in methods
    ]

    # RAG Corpus exposes separate preview and create-version commands.
    assert len(schema["paths"]) == 66
    assert len(operations) == 74
    assert len(operations) == len(set(operations))


def test_application_layer_does_not_import_rag_api_adapter():
    application = ROOT / "app" / "application"
    for path in application.rglob("*.py"):
        imported = {
            node.module
            for node in ast.walk(_tree(path))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any(module.startswith("app.api.rag") for module in imported), path
