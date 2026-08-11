from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
PRODUCT_PAGES = (
    "StartPage.jsx",
    "InterviewPage.jsx",
    "ReportProcessingPage.jsx",
    "ReportDetailPage.jsx",
    "ReportsPage.jsx",
    "HelpPage.jsx",
    "MemoryCenterPage.jsx",
)
RETIRED_HTML = (
    "test0.html",
    "test1.html",
    "test2.html",
    "test3.html",
    "test4.html",
    "test-help.html",
)


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


def test_frontend_is_an_independent_vite_react_runtime():
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    root_package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert {"react", "react-dom", "vite"}.issubset(package["dependencies"])
    assert {"dev", "build", "check"}.issubset(package["scripts"])
    assert root_package["scripts"]["dev:frontend"].startswith("npm --prefix frontend")
    assert root_package["scripts"]["build:frontend"].startswith(
        "npm --prefix frontend"
    )
    assert "build:prototype-css" not in root_package["scripts"]
    assert (FRONTEND / "index.html").is_file()
    assert (FRONTEND / "vite.config.js").is_file()


def test_product_pages_are_owned_by_react_not_fastapi_static_mounts():
    pages = FRONTEND / "src" / "pages"
    assert all((pages / name).is_file() for name in PRODUCT_PAGES)

    main_imports = _imported_modules(ROOT / "app" / "main.py")
    assert "fastapi.staticfiles" not in main_imports
    assert "starlette.staticfiles" not in main_imports


def test_retired_static_frontend_cannot_reappear_as_a_second_runtime():
    app_dir = ROOT / "app"

    assert not (app_dir / "static").exists()
    assert all(not (app_dir / name).exists() for name in RETIRED_HTML)
    assert all(
        not (FRONTEND / "public" / name).exists()
        for name in ("memory-center.html", "memory-center.css", "memory-center.js")
    )
