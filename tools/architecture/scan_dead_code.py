#!/usr/bin/env python3
"""P9-T02: conservative static/runtime/test dead-code evidence scan.

The scanner never deletes code. A result is a deletion-review candidate only
when no static production/script reference, runtime-wiring reference, or test
reference is found. Dynamic Python remains a manual-review boundary.
"""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
TESTS_ROOT = REPO_ROOT / "tests"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"
JSON_PATH = ARTIFACTS_DIR / "dead-code-scan.json"
MARKDOWN_PATH = ARTIFACTS_DIR / "dead-code-scan.md"

RUNTIME_WIRING_PREFIXES = (
    "app.a2a",
    "app.agents",
    "app.api",
    "app.graphs",
    "app.runtime",
)
RUNTIME_WIRING_MODULES = {"app.main"}
EXTERNAL_ENTRYPOINT_MODULES = {"app.main"}
REGISTRATION_DECORATOR_TOKENS = {
    "command",
    "delete",
    "get",
    "handler",
    "exception_handler",
    "patch",
    "post",
    "put",
    "route",
    "shared_task",
    "task",
    "websocket",
}


@dataclass(frozen=True)
class Source:
    path: Path
    relative: str
    module: str
    is_package: bool
    scope: str
    tree: ast.Module


def _module_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = relative.parts[:-1] if path.name == "__init__.py" else relative.parts
    return ".".join(parts)


def _scope(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).as_posix()
    if relative.startswith("tests/"):
        return "test"
    if relative.startswith("scripts/"):
        return "script"
    return "production"


def _is_runtime_wiring(module: str) -> bool:
    return module in RUNTIME_WIRING_MODULES or any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in RUNTIME_WIRING_PREFIXES
    )


def _load_sources() -> tuple[list[Source], list[dict[str, str]]]:
    paths: list[Path] = []
    for root in (APP_ROOT, SCRIPTS_ROOT, TESTS_ROOT):
        if root.exists():
            paths.extend(root.rglob("*.py"))
    sources: list[Source] = []
    errors: list[dict[str, str]] = []
    for path in sorted(paths):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append({"file": relative, "error": type(exc).__name__})
            continue
        sources.append(
            Source(
                path=path,
                relative=relative,
                module=_module_name(path),
                is_package=path.name == "__init__.py",
                scope=_scope(path),
                tree=tree,
            )
        )
    return sources, errors


def _resolve_from(source: Source, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = source.module if source.is_package else source.module.rsplit(".", 1)[0]
    parts = package.split(".") if package else []
    ascend = max(0, node.level - 1)
    if ascend:
        parts = parts[:-ascend]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _definition_nodes(tree: ast.Module) -> dict[str, ast.AST]:
    result: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            result.setdefault(node.name, node)
    return result


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _has_registration_decorator(node: ast.AST) -> bool:
    decorators = getattr(node, "decorator_list", ())
    return any(
        _decorator_name(decorator).casefold() in REGISTRATION_DECORATOR_TOKENS
        for decorator in decorators
    )


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        try:
            expression = ast.unparse(node.test)
        except Exception:
            continue
        if "__name__" in expression and "__main__" in expression:
            return True
    return False


def _attribute_chain(node: ast.Attribute) -> tuple[str, list[str]] | None:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.reverse()
    return current.id, parts


def _iter_string_constants(tree: ast.AST) -> Iterable[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def _public_exports(tree: ast.Module) -> set[str]:
    exports: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            continue
        exports.update(
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
    return exports


def _evidence_kind(source: Source) -> str:
    if source.scope == "test":
        return "test"
    return "static"


def scan() -> dict[str, Any]:
    sources, parse_errors = _load_sources()
    app_sources = [source for source in sources if source.scope == "production"]
    modules = {source.module for source in app_sources}
    definitions = {
        source.module: _definition_nodes(source.tree) for source in app_sources
    }
    source_by_module = {source.module: source for source in app_sources}

    module_evidence: dict[str, dict[str, set[tuple[str, int, str]]]] = {
        module: {"static": set(), "runtime": set(), "test": set()}
        for module in modules
    }
    symbol_evidence: dict[
        tuple[str, str], dict[str, set[tuple[str, int, str]]]
    ] = {
        (module, name): {"static": set(), "runtime": set(), "test": set()}
        for module, names in definitions.items()
        for name in names
    }

    def add_module(target: str, source: Source, line: int, reason: str) -> None:
        if target not in module_evidence or target == source.module:
            return
        kind = _evidence_kind(source)
        item = (source.relative, line, reason)
        module_evidence[target][kind].add(item)
        if source.scope == "production" and _is_runtime_wiring(source.module):
            module_evidence[target]["runtime"].add(item)

    def add_symbol(
        target_module: str,
        name: str,
        source: Source,
        line: int,
        reason: str,
        *,
        allow_self: bool = True,
    ) -> None:
        key = (target_module, name)
        if key not in symbol_evidence:
            return
        if not allow_self and target_module == source.module:
            return
        kind = _evidence_kind(source)
        item = (source.relative, line, reason)
        symbol_evidence[key][kind].add(item)
        if source.scope == "production" and _is_runtime_wiring(source.module):
            symbol_evidence[key]["runtime"].add(item)

    dynamic_import_sites: list[dict[str, Any]] = []
    for source in sources:
        aliases: dict[str, tuple[str, str | None]] = {}
        own_definitions = definitions.get(source.module, {})

        for node in ast.walk(source.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name
                    add_module(target, source, node.lineno, "import")
                    local = alias.asname or target.split(".", 1)[0]
                    aliases[local] = (target if alias.asname else local, None)
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_from(source, node)
                if base in modules:
                    add_module(base, source, node.lineno, "from_import")
                for alias in node.names:
                    if alias.name == "*":
                        for name in definitions.get(base, {}):
                            add_symbol(base, name, source, node.lineno, "star_import")
                        continue
                    possible_module = f"{base}.{alias.name}" if base else alias.name
                    local = alias.asname or alias.name
                    if possible_module in modules:
                        add_module(possible_module, source, node.lineno, "from_import_module")
                        aliases[local] = (possible_module, None)
                    else:
                        add_symbol(base, alias.name, source, node.lineno, "from_import_symbol")
                        aliases[local] = (base, alias.name)
            elif isinstance(node, ast.Call):
                function = ast.unparse(node.func)
                if function in {"__import__", "importlib.import_module"}:
                    target = None
                    if node.args and isinstance(node.args[0], ast.Constant):
                        if isinstance(node.args[0].value, str):
                            target = node.args[0].value
                    dynamic_import_sites.append(
                        {
                            "file": source.relative,
                            "line": node.lineno,
                            "expression": ast.unparse(node),
                            "resolved_target": target,
                        }
                    )
                    if target:
                        add_module(target, source, node.lineno, "dynamic_import")

        # Qualified references through imported module aliases.
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Attribute):
                continue
            chain = _attribute_chain(node)
            if chain is None:
                continue
            root, attributes = chain
            binding = aliases.get(root)
            if binding is None or binding[1] is not None:
                continue
            base = binding[0]
            for index in range(len(attributes)):
                possible_module = ".".join([base, *attributes[: index + 1]])
                add_module(possible_module, source, node.lineno, "qualified_reference")
            if attributes:
                target_module = ".".join([base, *attributes[:-1]]) or base
                add_symbol(
                    target_module,
                    attributes[-1],
                    source,
                    node.lineno,
                    "qualified_reference",
                )

        # Direct imported symbol uses. The import itself is already evidence;
        # this records actual load sites for review detail.
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            binding = aliases.get(node.id)
            if binding is not None and binding[1] is not None:
                add_symbol(
                    binding[0], binding[1], source, node.lineno, "imported_symbol_use"
                )

        # References between top-level definitions in the same module. A
        # recursive self-reference is deliberately not counted as reachability.
        for owner_name, owner in own_definitions.items():
            for node in ast.walk(owner):
                if (
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id in own_definitions
                    and node.id != owner_name
                ):
                    add_symbol(
                        source.module,
                        node.id,
                        source,
                        node.lineno,
                        f"local_reference_from:{owner_name}",
                    )

        # Module-level executable statements may reference local definitions.
        for statement in source.tree.body:
            if isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(statement):
                if (
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Load)
                    and node.id in own_definitions
                ):
                    add_symbol(
                        source.module,
                        node.id,
                        source,
                        node.lineno,
                        "module_level_reference",
                    )

        if source.scope == "production":
            for name in _public_exports(source.tree):
                add_symbol(
                    source.module,
                    name,
                    source,
                    1,
                    "public_export",
                )
            if _is_runtime_wiring(source.module):
                for name, node in own_definitions.items():
                    if _has_registration_decorator(node):
                        item = (source.relative, node.lineno, "framework_registration")
                        symbol_evidence[(source.module, name)]["runtime"].add(item)
            if source.is_package and "__getattr__" in own_definitions:
                add_symbol(
                    source.module,
                    "__getattr__",
                    source,
                    own_definitions["__getattr__"].lineno,
                    "dynamic_module_hook",
                )

        # Lazy-export maps commonly pair a fully qualified module string with
        # an attribute name. Model that pair so package __getattr__ shims do not
        # make their canonical target look unreferenced.
        for node in ast.walk(source.tree):
            if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) != 2:
                continue
            module_node, symbol_node = node.elts
            if not (
                isinstance(module_node, ast.Constant)
                and isinstance(module_node.value, str)
                and isinstance(symbol_node, ast.Constant)
                and isinstance(symbol_node.value, str)
                and module_node.value in modules
            ):
                continue
            add_module(
                module_node.value,
                source,
                node.lineno,
                "dynamic_export_map",
            )
            add_symbol(
                module_node.value,
                symbol_node.value,
                source,
                node.lineno,
                "dynamic_export_map",
            )

        # Exact qualified string references cover Celery/import configuration
        # without treating arbitrary same-name prose as code reachability.
        for line, value in _iter_string_constants(source.tree):
            normalized = value.replace(":", ".")
            if normalized in modules:
                add_module(normalized, source, line, "qualified_string")
            head, separator, tail = normalized.rpartition(".")
            if separator:
                add_symbol(head, tail, source, line, "qualified_string")

    def evidence_rows(values: set[tuple[str, int, str]]) -> list[dict[str, Any]]:
        return [
            {"file": file, "line": line, "reason": reason}
            for file, line, reason in sorted(values)
        ]

    module_candidates: list[dict[str, Any]] = []
    test_only_modules: list[dict[str, Any]] = []
    for module in sorted(modules):
        source = source_by_module[module]
        evidence = module_evidence[module]
        is_package = source.is_package
        is_entrypoint = module in EXTERNAL_ENTRYPOINT_MODULES or _has_main_guard(source.tree)
        row = {
            "module": module,
            "file": source.relative,
            "static_references": len(evidence["static"]),
            "runtime_wiring_references": len(evidence["runtime"]),
            "test_references": len(evidence["test"]),
        }
        if (
            not is_package
            and not is_entrypoint
            and not evidence["static"]
            and not evidence["runtime"]
            and not evidence["test"]
        ):
            row.update(
                {
                    "confidence": "review_required",
                    "decision": "triple_zero_deletion_review_candidate",
                }
            )
            module_candidates.append(row)
        elif (
            not is_package
            and not is_entrypoint
            and not evidence["static"]
            and not evidence["runtime"]
            and evidence["test"]
        ):
            test_only_modules.append(row)

    candidate_modules = {row["module"] for row in module_candidates}
    symbol_candidates: list[dict[str, Any]] = []
    symbols_in_candidate_modules: list[dict[str, Any]] = []
    test_only_symbols: list[dict[str, Any]] = []
    for (module, name), evidence in sorted(symbol_evidence.items()):
        node = definitions[module][name]
        source = source_by_module[module]
        row = {
            "module": module,
            "name": name,
            "kind": "class" if isinstance(node, ast.ClassDef) else "function",
            "file": source.relative,
            "line": node.lineno,
            "visibility": "private" if name.startswith("_") else "public",
            "static_references": len(evidence["static"]),
            "runtime_wiring_references": len(evidence["runtime"]),
            "test_references": len(evidence["test"]),
        }
        if not any(evidence.values()):
            row.update(
                {
                    "confidence": (
                        "higher_private_symbol" if name.startswith("_") else "review_public_contract"
                    ),
                    "decision": "triple_zero_deletion_review_candidate",
                }
            )
            if module in candidate_modules:
                row["decision"] = "covered_by_triple_zero_module_candidate"
                symbols_in_candidate_modules.append(row)
            else:
                symbol_candidates.append(row)
        elif not evidence["static"] and not evidence["runtime"] and evidence["test"]:
            test_only_symbols.append(row)

    unresolved_dynamic_import_sites = [
        site for site in dynamic_import_sites if site["resolved_target"] is None
    ]
    reviewed_findings = [
        {
            "category": "triple_zero_modules",
            "count": len(module_candidates),
            "disposition": "dedicated_cleanup_candidate",
            "finding": (
                "The module candidates are legacy T63/T65 evaluation implementations. "
                "Their former one-off scripts/tests are absent and no current Python "
                "consumer or runtime wiring remains."
            ),
        },
        {
            "category": "private_symbols",
            "count": sum(row["visibility"] == "private" for row in symbol_candidates),
            "disposition": "higher_confidence_cleanup_candidate",
            "finding": (
                "Private triple-zero helpers have no detected production, runtime, or "
                "test reachability; remove only in a dedicated change with regression tests."
            ),
        },
        {
            "category": "public_symbols",
            "count": sum(row["visibility"] == "public" for row in symbol_candidates),
            "disposition": "external_contract_review_required",
            "finding": (
                "Public triple-zero symbols may still have external import consumers; "
                "zero repository reachability alone is insufficient for deletion."
            ),
        },
        {
            "category": "test_only",
            "count": len(test_only_modules) + len(test_only_symbols),
            "disposition": "retain",
            "finding": (
                "Test-only modules and symbols have explicit test reachability and do "
                "not satisfy the triple-zero rule."
            ),
        },
        {
            "category": "unresolved_dynamic_imports",
            "count": len(unresolved_dynamic_import_sites),
            "disposition": "reviewed_no_candidate_target",
            "finding": (
                "Unresolved calls are test helpers whose call sites supply literal Runtime "
                "module names; exact string scanning records those targets separately."
            ),
        },
    ]

    return {
        "metadata": {
            "generated_by": "tools/architecture/scan_dead_code.py",
            "app_files_scanned": len(app_sources),
            "script_files_scanned": sum(source.scope == "script" for source in sources),
            "test_files_scanned": sum(source.scope == "test" for source in sources),
            "top_level_symbols_scanned": len(symbol_evidence),
            "parse_error_count": len(parse_errors),
            "dynamic_import_site_count": len(dynamic_import_sites),
            "unresolved_dynamic_import_site_count": len(
                unresolved_dynamic_import_sites
            ),
            "decision_rule": (
                "candidate only when static references = 0, runtime wiring references = 0, "
                "and test references = 0; package initializers and explicit entrypoints excluded"
            ),
        },
        "parse_errors": parse_errors,
        "dynamic_import_sites": dynamic_import_sites,
        "reviewed_findings": reviewed_findings,
        "triple_zero_module_candidates": module_candidates,
        "triple_zero_symbol_candidates": symbol_candidates,
        "symbols_in_triple_zero_modules": symbols_in_candidate_modules,
        "test_only_modules": test_only_modules,
        "test_only_symbols": test_only_symbols,
        "limitations": [
            "Static evidence cannot prove absence of reflection or external consumers.",
            "Public triple-zero symbols require contract review before deletion.",
            "Top-level constants and nested definitions are not deletion candidates in this scan.",
            "Test-only results are retained and are not triple-zero deletion candidates.",
        ],
    }


def _candidate_table(rows: list[dict[str, Any]], *, symbols: bool) -> list[str]:
    if not rows:
        return ["No candidates detected."]
    if symbols:
        lines = [
            "| symbol | kind | visibility | static | runtime | tests | confidence |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
        for row in rows:
            lines.append(
                f"| `{row['module']}:{row['name']}` | {row['kind']} | "
                f"{row['visibility']} | {row['static_references']} | "
                f"{row['runtime_wiring_references']} | {row['test_references']} | "
                f"`{row['confidence']}` |"
            )
        return lines
    lines = [
        "| module | static | runtime | tests | decision |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['module']}` | {row['static_references']} | "
            f"{row['runtime_wiring_references']} | {row['test_references']} | "
            f"`{row.get('decision', 'test_only')}` |"
        )
    return lines


def render_markdown(result: dict[str, Any]) -> str:
    metadata = result["metadata"]
    modules = result["triple_zero_module_candidates"]
    symbols = result["triple_zero_symbol_candidates"]
    covered_symbols = result["symbols_in_triple_zero_modules"]
    test_modules = result["test_only_modules"]
    test_symbols = result["test_only_symbols"]
    private_symbols = sum(row["visibility"] == "private" for row in symbols)
    lines = [
        "# P9 Dead Code Scan",
        "",
        "Status: P9-T02 COMPLETE - evidence scan only; no deletion performed",
        "",
        "## Evidence Model",
        "",
        "- Static: references from `app/**/*.py` and `scripts/**/*.py`.",
        "- Runtime wiring: the subset originating in main, Runtime, API, Agents, Graphs, or A2A modules, including framework decorators and qualified strings.",
        "- Tests: references from `tests/**/*.py`.",
        "- A deletion-review candidate must be zero in all three columns.",
        "- Package initializers and explicit external entrypoints are excluded.",
        "",
        "## Summary",
        "",
        f"- App files scanned: {metadata['app_files_scanned']}",
        f"- Script files scanned: {metadata['script_files_scanned']}",
        f"- Test files scanned: {metadata['test_files_scanned']}",
        f"- Top-level symbols scanned: {metadata['top_level_symbols_scanned']}",
        f"- Parse errors: {metadata['parse_error_count']}",
        f"- Dynamic import sites reviewed: {metadata['dynamic_import_site_count']}",
        f"- Unresolved dynamic import calls: {metadata['unresolved_dynamic_import_site_count']}",
        f"- Triple-zero modules: {len(modules)}",
        f"- Triple-zero symbols: {len(symbols)} ({private_symbols} private)",
        f"- Additional symbols covered by triple-zero modules: {len(covered_symbols)}",
        f"- Test-only modules: {len(test_modules)}",
        f"- Test-only symbols: {len(test_symbols)}",
        "",
        "## Reviewed Disposition",
        "",
        "| category | count | disposition | finding |",
        "| --- | ---: | --- | --- |",
        *[
            f"| {finding['category']} | {finding['count']} | "
            f"`{finding['disposition']}` | {finding['finding']} |"
            for finding in result["reviewed_findings"]
        ],
        "",
        "## Triple-Zero Module Candidates",
        "",
        *_candidate_table(modules, symbols=False),
        "",
        "## Triple-Zero Symbol Candidates",
        "",
        *_candidate_table(symbols, symbols=True),
        "",
        "## Test-Only Modules",
        "",
        "These are retained: test reachability means they do not satisfy the deletion rule.",
        "",
        *_candidate_table(test_modules, symbols=False),
        "",
        "## Review Boundary",
        "",
        "Triple-zero means eligible for deletion review, not proven safe to delete. Reflection, external consumers, serialized import paths, and public API compatibility still require human or integration evidence. P9-T03 has the stricter version-removal gate.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    result = scan()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    MARKDOWN_PATH.write_text(render_markdown(result), encoding="utf-8")
    print(f"wrote {JSON_PATH.relative_to(REPO_ROOT).as_posix()}")
    print(f"wrote {MARKDOWN_PATH.relative_to(REPO_ROOT).as_posix()}")
    for key, value in result["metadata"].items():
        if key not in {"generated_by", "decision_rule"}:
            print(f"{key}={value}")
    print(f"triple_zero_modules={len(result['triple_zero_module_candidates'])}")
    print(f"triple_zero_symbols={len(result['triple_zero_symbol_candidates'])}")
    print(f"test_only_modules={len(result['test_only_modules'])}")
    print(f"test_only_symbols={len(result['test_only_symbols'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
