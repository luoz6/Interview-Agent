import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "app" / "api"
DOMAIN_ROUTE_MODULES = (
    API / "deletion" / "routes.py",
    API / "interview" / "routes.py",
    API / "memory" / "routes.py",
    API / "prep" / "routes.py",
    API / "reports" / "routes.py",
    API / "runtime" / "routes.py",
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

    assert len(schema["paths"]) == 39
    assert len(operations) == 45
    assert len(operations) == len(set(operations))
