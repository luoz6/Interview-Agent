from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import scan_dependencies as scanner  # noqa: E402


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_layer_mapping_covers_required_layers():
    assert scanner.map_module_to_layer("app.domain.models") == "domain"
    assert scanner.map_module_to_layer("app.application.interview") == "application"
    assert scanner.map_module_to_layer("app.ports.runtime") == "ports"
    assert scanner.map_module_to_layer("app.adapters.postgres") == "adapters"
    assert scanner.map_module_to_layer("app.api.routes") == "api"
    assert scanner.map_module_to_layer("app.runtime.container") == "runtime"
    assert scanner.map_module_to_layer("app.graphs.interview_graph") == "graphs"
    assert scanner.map_module_to_layer("app.runtime.composition") == "runtime"
    assert scanner.map_module_to_layer("app.agents.examiner") == "agents"
    assert scanner.map_module_to_layer("app.main") == "other"
    assert scanner.map_module_to_layer("os") == "external"


def test_relative_import_resolution():
    assert (
        scanner.resolve_relative_module(
            "app.services.foo",
            level=1,
            module="bar",
            alias_name="Baz",
        )
        == "app.services.bar"
    )
    assert (
        scanner.resolve_relative_module(
            "app.services.foo",
            level=2,
            module="adapters",
            alias_name="X",
        )
        == "app.adapters"
    )


def test_extract_file_edges_separates_internal_and_external(tmp_path):
    app_root = tmp_path / "app"
    path = _write(
        app_root / "application" / "demo.py",
        "\n".join(
            [
                "import os",
                "import app.services.foo",
                "import app.services.foo as foo",
                "from app.services.foo import Bar",
                "from app.application.interview import something",
                "from app import services",
            ]
        ),
    )

    internal, external, unresolved, dynamic_sites, issue = scanner.extract_file_edges(
        path,
        app_root=app_root,
    )

    assert issue is None
    assert unresolved == []
    assert dynamic_sites == []

    internal_modules = [edge.target_module for edge in internal]
    assert "app.services.foo" in internal_modules
    assert "app.application.interview" in internal_modules
    assert "app.services" in internal_modules
    assert {edge.import_type for edge in internal} == {"import", "from"}
    assert all(edge.source_layer == "application" for edge in internal)
    assert all(edge.line for edge in internal)

    external_modules = [edge.target_module for edge in external]
    assert "os" in external_modules
    assert all(edge.target_layer == "external" for edge in external)


def test_relative_import_resolves_to_app_layer(tmp_path):
    app_root = tmp_path / "app"
    path = _write(
        app_root / "services" / "demo.py",
        "from ..adapters import X\n",
    )

    internal, external, unresolved, _, _ = scanner.extract_file_edges(
        path,
        app_root=app_root,
    )

    assert external == []
    assert unresolved == []
    assert internal[0].target_module == "app.adapters"
    assert internal[0].target_layer == "adapters"


def test_dynamic_import_is_recorded_as_limitation(tmp_path):
    app_root = tmp_path / "app"
    path = _write(
        app_root / "services" / "demo.py",
        "import importlib\nimportlib.import_module('app.services.other')\n",
    )

    _, _, _, dynamic_sites, _ = scanner.extract_file_edges(
        path,
        app_root=app_root,
    )

    assert len(dynamic_sites) == 1
    assert dynamic_sites[0].kind == "importlib.import_module"
