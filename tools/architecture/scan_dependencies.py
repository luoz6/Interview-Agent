#!/usr/bin/env python3
"""P0A-T02: deterministic AST import-dependency scanner for ``app/**/*.py``.

This tool is intentionally read-only.  It never imports application modules,
so scanning cannot execute app code or trigger runtime side effects.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"

LAYER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("app.domain", "domain"),
    ("app.application", "application"),
    ("app.ports", "ports"),
    ("app.adapters", "adapters"),
    ("app.api", "api"),
    ("app.runtime", "runtime"),
    ("app.evals", "evals"),
    ("app.graphs", "graphs"),
    ("app.services", "services"),
    ("app.agents", "agents"),
)

KNOWN_LAYERS = tuple(layer for _, layer in LAYER_PREFIXES) + ("other",)


def map_module_to_layer(module: str) -> str:
    """Deterministically map an absolute Python module name to a layer."""
    if not module.startswith("app.") and module != "app":
        return "external"
    for prefix, layer in LAYER_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return layer
    return "other"


def module_name_for_file(path: Path, app_root: Path = APP_ROOT) -> str:
    rel = path.resolve().relative_to(app_root.resolve())
    stem = rel.with_suffix("")
    parts = [part for part in stem.parts if part not in ("", ".")]
    if path.name == "__init__.py":
        if len(parts) <= 1:
            return "app"
        parts = parts[:-1]
    return ".".join(["app", *parts])


def resolve_relative_module(
    source_module: str,
    *,
    level: int,
    module: str | None,
    alias_name: str | None,
) -> str:
    """Resolve a relative import to an absolute module when possible.

    Python semantics for a module whose ``__package__`` is the source module's
    parent package:

    * level == 1 means the current package.
    * level == n means ``n - 1`` packages above the current package.
    """
    if source_module == "app":
        package_parts: list[str] = ["app"]
    else:
        package_parts = source_module.split(".")
        # ``source_module`` is a file module, so its package is the module
        # without the final component.
        package_parts = package_parts[:-1]

    remove_count = max(0, level - 1)
    base_parts = package_parts[: max(1, len(package_parts) - remove_count)]
    if not base_parts:
        base_parts = ["app"]

    if module:
        return ".".join([*base_parts, *module.split(".")])

    if alias_name and alias_name != "*":
        return ".".join([*base_parts, alias_name])

    return ".".join(base_parts)


@dataclass(frozen=True)
class ImportEdge:
    source_file: str
    source_module: str
    source_layer: str
    target_module: str
    target_layer: str
    import_type: str
    line: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_module": self.source_module,
            "source_layer": self.source_layer,
            "target_module": self.target_module,
            "target_layer": self.target_layer,
            "import_type": self.import_type,
            "line": self.line,
        }


@dataclass(frozen=True)
class DynamicImportSite:
    source_file: str
    source_module: str
    line: int
    kind: str
    target_expression: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_module": self.source_module,
            "line": self.line,
            "kind": self.kind,
            "target_expression": self.target_expression,
        }


@dataclass(frozen=True)
class ParseIssue:
    source_file: str
    error: str

    def as_dict(self) -> dict[str, Any]:
        return {"source_file": self.source_file, "error": self.error}


def _expression_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def _is_internal_module(module: str) -> bool:
    return module == "app" or module.startswith("app.")


def _edges_from_import(
    *,
    node: ast.Import,
    source_file: str,
    source_module: str,
    source_layer: str,
) -> Iterable[ImportEdge]:
    for alias in node.names:
        target_module = alias.name
        yield ImportEdge(
            source_file=source_file,
            source_module=source_module,
            source_layer=source_layer,
            target_module=target_module,
            target_layer=map_module_to_layer(target_module),
            import_type="import",
            line=node.lineno,
        )


def _edges_from_importfrom(
    *,
    node: ast.ImportFrom,
    source_file: str,
    source_module: str,
    source_layer: str,
) -> tuple[list[ImportEdge], list[str]]:
    edges: list[ImportEdge] = []
    unresolved: list[str] = []

    if node.level and not _is_internal_module(node.module or ""):
        # The absolute import below resolves below; this branch is kept to make
        # the relative-vs-absolute decision explicit for future reviewers.
        pass

    if node.level == 0:
        base_module = node.module or ""
        if base_module == "app":
            target_modules = [
                f"app.{alias.name}" if alias.name != "*" else "app"
                for alias in node.names
            ]
        else:
            target_modules = [base_module for _ in node.names]
        for alias, target_module in zip(node.names, target_modules):
            edges.append(
                ImportEdge(
                    source_file=source_file,
                    source_module=source_module,
                    source_layer=source_layer,
                    target_module=target_module,
                    target_layer=map_module_to_layer(target_module),
                    import_type="from",
                    line=node.lineno,
                )
            )
        return edges, unresolved

    # Relative import.
    for alias in node.names:
        target_module = resolve_relative_module(
            source_module,
            level=node.level,
            module=node.module,
            alias_name=alias.name,
        )
        if not _is_internal_module(target_module):
            unresolved.append(
                f"{source_module}:{node.lineno}: from {'.' * node.level}"
                f"{node.module or ''} import {alias.name}"
            )
            continue
        edges.append(
            ImportEdge(
                source_file=source_file,
                source_module=source_module,
                source_layer=source_layer,
                target_module=target_module,
                target_layer=map_module_to_layer(target_module),
                import_type="from",
                line=node.lineno,
            )
        )
    return edges, unresolved


def _dynamic_import_sites(
    tree: ast.AST,
    *,
    source_file: str,
    source_module: str,
) -> list[DynamicImportSite]:
    sites: list[DynamicImportSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            sites.append(
                DynamicImportSite(
                    source_file=source_file,
                    source_module=source_module,
                    line=node.lineno,
                    kind="__import__",
                    target_expression=_expression_text(node),
                )
            )
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            sites.append(
                DynamicImportSite(
                    source_file=source_file,
                    source_module=source_module,
                    line=node.lineno,
                    kind="importlib.import_module",
                    target_expression=_expression_text(node),
                )
            )
    return sites


def extract_file_edges(
    path: Path,
    *,
    app_root: Path = APP_ROOT,
) -> tuple[list[ImportEdge], list[ImportEdge], list[str], list[DynamicImportSite], ParseIssue | None]:
    relative_file = path.resolve().relative_to(app_root.resolve()).as_posix()
    source_file = f"app/{relative_file}"
    source_module = module_name_for_file(path, app_root)
    source_layer = map_module_to_layer(source_module)

    try:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
        return [], [], [], [], ParseIssue(source_file=source_file, error=str(exc))

    internal: list[ImportEdge] = []
    external: list[ImportEdge] = []
    unresolved: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for edge in _edges_from_import(
                node=node,
                source_file=source_file,
                source_module=source_module,
                source_layer=source_layer,
            ):
                if _is_internal_module(edge.target_module):
                    internal.append(edge)
                else:
                    external.append(edge)
        elif isinstance(node, ast.ImportFrom):
            file_internal, file_unresolved = _edges_from_importfrom(
                node=node,
                source_file=source_file,
                source_module=source_module,
                source_layer=source_layer,
            )
            for edge in file_internal:
                if _is_internal_module(edge.target_module):
                    internal.append(edge)
                else:
                    external.append(edge)
            unresolved.extend(file_unresolved)

    dynamic_sites = _dynamic_import_sites(
        tree,
        source_file=source_file,
        source_module=source_module,
    )
    return internal, external, unresolved, dynamic_sites, None


def scan_app(app_root: Path = APP_ROOT) -> dict[str, Any]:
    internal_edges: list[ImportEdge] = []
    external_edges: list[ImportEdge] = []
    unresolved: list[str] = []
    dynamic_sites: list[DynamicImportSite] = []
    parse_issues: list[ParseIssue] = []
    files_scanned = 0

    for path in sorted(app_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        files_scanned += 1
        file_internal, file_external, file_unresolved, file_dynamic, issue = (
            extract_file_edges(path, app_root=app_root)
        )
        internal_edges.extend(file_internal)
        external_edges.extend(file_external)
        unresolved.extend(file_unresolved)
        dynamic_sites.extend(file_dynamic)
        if issue is not None:
            parse_issues.append(issue)

    return {
        "metadata": {
            "repo_root": str(REPO_ROOT.resolve()),
            "app_root": str(app_root.resolve()),
            "files_scanned": files_scanned,
            "internal_edge_count": len(internal_edges),
            "external_edge_count": len(external_edges),
            "unresolved_relative_import_count": len(unresolved),
            "dynamic_import_site_count": len(dynamic_sites),
            "parse_error_count": len(parse_issues),
        },
        "layer_mapping": {prefix: layer for prefix, layer in LAYER_PREFIXES},
        "edges": [edge.as_dict() for edge in internal_edges],
        "external_edges": [edge.as_dict() for edge in external_edges],
        "unresolved_relative_imports": unresolved,
        "dynamic_imports": [site.as_dict() for site in dynamic_sites],
        "parse_errors": [issue.as_dict() for issue in parse_issues],
    }


def _source_layer_target_layer_pairs(
    edges: list[ImportEdge],
) -> list[tuple[str, str, int, int]]:
    row: dict[tuple[str, str], dict[str, int | set[str]]] = defaultdict(
        lambda: {"edges": 0, "files": set()}
    )
    for edge in edges:
        key = (edge.source_layer, edge.target_layer)
        row[key]["edges"] += 1
        assert isinstance(row[key]["files"], set)
        row[key]["files"].add(edge.source_file)
    result = [
        (source, target, int(data["edges"]), len(data["files"]))
        for (source, target), data in sorted(row.items())
    ]
    return result


def _top_cross_layer_sources(edges: list[ImportEdge], limit: int = 20) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for edge in edges:
        if edge.source_layer != edge.target_layer:
            counter[edge.source_file] += 1
    return [
        {"source_file": source_file, "cross_layer_edges": count}
        for source_file, count in counter.most_common(limit)
    ]


def _top_cross_layer_targets(edges: list[ImportEdge], limit: int = 20) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for edge in edges:
        if edge.source_layer != edge.target_layer:
            counter[edge.target_module] += 1
    return [
        {"target_module": target, "cross_layer_edges": count}
        for target, count in counter.most_common(limit)
    ]


def _services_overview(edges: list[ImportEdge]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, int | set[str]]] = defaultdict(
        lambda: {"edges": 0, "files": set()}
    )
    for edge in edges:
        if edge.target_layer != "services":
            continue
        grouped[edge.source_layer]["edges"] += 1
        assert isinstance(grouped[edge.source_layer]["files"], set)
        grouped[edge.source_layer]["files"].add(edge.source_file)
    return [
        {
            "source_layer": source_layer,
            "edges": int(data["edges"]),
            "source_files": len(data["files"]),
        }
        for source_layer, data in sorted(grouped.items())
    ]


def _graph_overview(edges: list[ImportEdge]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, int | set[str]]] = defaultdict(
        lambda: {"edges": 0, "files": set()}
    )
    for edge in edges:
        if edge.source_layer != "graphs":
            continue
        grouped[edge.target_layer]["edges"] += 1
        assert isinstance(grouped[edge.target_layer]["files"], set)
        grouped[edge.target_layer]["files"].add(edge.source_file)
    return [
        {
            "target_layer": target_layer,
            "edges": int(data["edges"]),
            "source_files": len(data["files"]),
        }
        for target_layer, data in sorted(grouped.items())
    ]


def _pair_metrics(
    edges: list[ImportEdge],
    source_layer: str,
    target_layer: str,
) -> tuple[int, int]:
    sources: set[str] = set()
    edge_count = 0
    for edge in edges:
        if edge.source_layer == source_layer and edge.target_layer == target_layer:
            edge_count += 1
            sources.add(edge.source_file)
    return edge_count, len(sources)


def build_summary(scan_result: dict[str, Any]) -> str:
    edges = [
        ImportEdge(
            source_file=edge["source_file"],
            source_module=edge["source_module"],
            source_layer=edge["source_layer"],
            target_module=edge["target_module"],
            target_layer=edge["target_layer"],
            import_type=edge["import_type"],
            line=edge["line"],
        )
        for edge in scan_result["edges"]
    ]
    metadata = scan_result["metadata"]
    lines: list[str] = []
    lines.append("# Dependency Summary")
    lines.append("")
    lines.append("## Counting Rule")
    lines.append("")
    lines.append(
        "- `edges` 是 raw AST import edge 数：每条 `import` alias 或 "
        "`from ... import ...` alias 计为 1。"
    )
    lines.append(
        "- `source_files` 是对应 edge 的去重源文件数，因此两者必须分别报告。"
    )
    lines.append(
        "- 主指标只统计 `app.* -> app.*` 内部依赖；external import 单列。"
    )
    lines.append("")
    lines.append("## 1. Layer-to-Layer Matrix")
    lines.append("")
    lines.append("| source | target | edges | source_files |")
    lines.append("| --- | --- | ---: | ---: |")
    for source, target, edge_count, file_count in _source_layer_target_layer_pairs(edges):
        lines.append(
            f"| {source} | {target} | {edge_count} | {file_count} |"
        )
    lines.append("")
    lines.append("## 1b. Required Cross-Layer Counts")
    lines.append("")
    lines.append("| source | target | edges | source_files |")
    lines.append("| --- | --- | ---: | ---: |")
    required_pairs = (
        ("application", "services"),
        ("application", "adapters"),
        ("domain", "services"),
        ("graphs", "services"),
        ("adapters", "services"),
        ("ports", "services"),
        ("api", "services"),
    )
    for source, target in required_pairs:
        edge_count, file_count = _pair_metrics(edges, source, target)
        lines.append(f"| {source} | {target} | {edge_count} | {file_count} |")
    lines.append("")
    lines.append("## 2. Top Cross-Layer Source Files")
    lines.append("")
    lines.append("| source_file | cross_layer_edges |")
    lines.append("| --- | ---: |")
    for row in _top_cross_layer_sources(edges):
        lines.append(f"| {row['source_file']} | {row['cross_layer_edges']} |")
    lines.append("")
    lines.append("## 3. Top Cross-Layer Target Modules")
    lines.append("")
    lines.append("| target_module | cross_layer_edges |")
    lines.append("| --- | ---: |")
    for row in _top_cross_layer_targets(edges):
        lines.append(
            f"| {row['target_module']} | {row['cross_layer_edges']} |"
        )
    lines.append("")
    lines.append("## 4. Services Dependency Overview")
    lines.append("")
    lines.append("| source_layer | edges | source_files |")
    lines.append("| --- | ---: | ---: |")
    for row in _services_overview(edges):
        lines.append(
            f"| {row['source_layer']} | {row['edges']} | "
            f"{row['source_files']} |"
        )
    lines.append("")
    lines.append("## 5. Graph Dependency Overview")
    lines.append("")
    lines.append("| target_layer | edges | source_files |")
    lines.append("| --- | ---: | ---: |")
    for row in _graph_overview(edges):
        lines.append(
            f"| {row['target_layer']} | {row['edges']} | "
            f"{row['source_files']} |"
        )
    lines.append("")
    lines.append("## Scan Metadata")
    lines.append("")
    lines.append(f"- files_scanned: {metadata['files_scanned']}")
    lines.append(f"- internal_edge_count: {metadata['internal_edge_count']}")
    lines.append(f"- external_edge_count: {metadata['external_edge_count']}")
    lines.append(
        "- unresolved_relative_import_count: "
        f"{metadata['unresolved_relative_import_count']}"
    )
    lines.append(
        f"- dynamic_import_site_count: {metadata['dynamic_import_site_count']}"
    )
    lines.append(f"- parse_error_count: {metadata['parse_error_count']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan app/**/*.py import dependencies with AST."
    )
    parser.add_argument(
        "--app-root",
        type=Path,
        default=APP_ROOT,
        help="Application root. Defaults to repo app/.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ARTIFACTS_DIR,
        help="Artifact output directory.",
    )
    args = parser.parse_args()

    scan_result = scan_app(args.app_root)
    summary = build_summary(scan_result)

    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    edges_path = args.artifacts_dir / "dependency-edges.json"
    summary_path = args.artifacts_dir / "dependency-summary.md"
    edges_path.write_text(
        json.dumps(scan_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_path.write_text(summary, encoding="utf-8")

    print(f"wrote {edges_path.relative_to(REPO_ROOT).as_posix()}")
    print(f"wrote {summary_path.relative_to(REPO_ROOT).as_posix()}")
    print(f"files_scanned={scan_result['metadata']['files_scanned']}")
    print(f"internal_edge_count={scan_result['metadata']['internal_edge_count']}")
    print(f"external_edge_count={scan_result['metadata']['external_edge_count']}")
    print(
        "unresolved_relative_import_count="
        f"{scan_result['metadata']['unresolved_relative_import_count']}"
    )
    print(
        f"dynamic_import_site_count="
        f"{scan_result['metadata']['dynamic_import_site_count']}"
    )
    print(f"parse_error_count={scan_result['metadata']['parse_error_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
