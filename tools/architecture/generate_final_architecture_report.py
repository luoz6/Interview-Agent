#!/usr/bin/env python3
"""P9-T04: compare the frozen P0 baseline with the current P9 Python tree."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import subprocess
import tarfile
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"
JSON_PATH = ARTIFACTS_DIR / "P9-final-python-loc-report.json"
MARKDOWN_PATH = ARTIFACTS_DIR / "P9-final-python-loc-report.md"
P0_BASELINE_COMMIT = "0af2a8b"
P0_ARCHITECTURE_BASELINE = (
    REPO_ROOT / "docs" / "architecture" / "current-architecture-baseline.md"
)
P0_GOD_MODULE_BASELINE = ARTIFACTS_DIR / "god-module-baseline.md"

LAYER_PREFIXES = (
    ("app.adapters", "adapters"),
    ("app.agents", "agents"),
    ("app.api", "api"),
    ("app.application", "application"),
    ("app.domain", "domain"),
    ("app.evals", "evals"),
    ("app.graphs", "graphs"),
    ("app.ports", "ports"),
    ("app.runtime", "runtime"),
    ("app.services", "services"),
)
VIOLATION_RULES = (
    ("DOMAIN_MUST_NOT_IMPORT_APPLICATION", "domain", "application"),
    ("DOMAIN_MUST_NOT_IMPORT_ADAPTERS", "domain", "adapters"),
    ("DOMAIN_MUST_NOT_IMPORT_RUNTIME", "domain", "runtime"),
    ("DOMAIN_MUST_NOT_IMPORT_SERVICES", "domain", "services"),
    ("APPLICATION_MUST_NOT_IMPORT_ADAPTERS", "application", "adapters"),
    ("APPLICATION_MUST_NOT_IMPORT_RUNTIME", "application", "runtime"),
    ("APPLICATION_MUST_NOT_IMPORT_SERVICES", "application", "services"),
    ("PORTS_MUST_NOT_IMPORT_ADAPTERS", "ports", "adapters"),
    ("PORTS_MUST_NOT_IMPORT_RUNTIME", "ports", "runtime"),
    ("PORTS_MUST_NOT_IMPORT_SERVICES", "ports", "services"),
)
GOD_OWNER_MAPPINGS = (
    ("Runtime composition", "app.services.runtime", "app.runtime.composition"),
    ("Durable interview graph", "app.graphs.durable_interview_graph", "app.graphs.durable_interview_graph"),
    ("LLM provider", "app.services.llm", "app.adapters.providers.llm"),
)


def _git_archive_python_files(commit: str) -> dict[str, str]:
    completed = subprocess.run(
        ["git", "archive", "--format=tar", commit, "app"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            files[member.name] = extracted.read().decode("utf-8-sig")
    return files


def _current_python_files() -> dict[str, str]:
    return {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8-sig")
        for path in sorted(APP_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _module_name(path: str) -> str:
    parts = Path(path).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _layer(module: str) -> str:
    for prefix, layer in LAYER_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return layer
    return "other"


def _resolve_from(source_module: str, source_path: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    is_package = source_path.endswith("/__init__.py") or source_path == "app/__init__.py"
    package = source_module if is_package else source_module.rsplit(".", 1)[0]
    parts = package.split(".") if package else []
    ascend = max(0, node.level - 1)
    if ascend:
        parts = parts[:-ascend]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _dependency_scan(files: dict[str, str]) -> dict[str, Any]:
    edges: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    modules = {_module_name(path) for path in files}
    for path, source in sorted(files.items()):
        source_module = _module_name(path)
        try:
            tree = ast.parse(source, filename=path)
        except (SyntaxError, UnicodeError) as exc:
            parse_errors.append(f"{path}:{type(exc).__name__}")
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_from(source_module, path, node)
                if base == "app":
                    targets.extend(
                        f"app.{alias.name}" if alias.name != "*" else "app"
                        for alias in node.names
                    )
                else:
                    targets.extend(base for _ in node.names)
            for target in targets:
                if target == "app" or target.startswith("app."):
                    edges.append(
                        {
                            "source": source_module,
                            "source_layer": _layer(source_module),
                            "target": target,
                            "target_layer": _layer(target),
                        }
                    )
    return {"edges": edges, "modules": modules, "parse_errors": parse_errors}


def _violation_summary(edges: list[dict[str, Any]]) -> dict[str, Any]:
    keys: set[tuple[str, str, str]] = set()
    by_rule: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for rule, source_layer, target_layer in VIOLATION_RULES:
        for edge in edges:
            if (
                edge["source_layer"] == source_layer
                and edge["target_layer"] == target_layer
            ):
                key = (rule, edge["source"], edge["target"])
                keys.add(key)
                by_rule[rule].add((edge["source"], edge["target"]))
    return {
        "unique_pair_count": len(keys),
        "by_rule": {rule: len(by_rule[rule]) for rule, _, _ in VIOLATION_RULES},
        "pairs": [
            {"rule": rule, "source": source, "target": target}
            for rule, source, target in sorted(keys)
        ],
    }


def _strongly_connected_components(
    modules: set[str], edges: list[dict[str, Any]]
) -> list[list[str]]:
    adjacency: dict[str, set[str]] = {module: set() for module in modules}
    for edge in edges:
        source = edge["source"]
        target = edge["target"]
        if source in modules and target in modules and source != target:
            adjacency[source].add(target)

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(module: str) -> None:
        nonlocal index
        indices[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)
        for target in sorted(adjacency[module]):
            if target not in indices:
                visit(target)
                lowlinks[module] = min(lowlinks[module], lowlinks[target])
            elif target in on_stack:
                lowlinks[module] = min(lowlinks[module], indices[target])
        if lowlinks[module] != indices[module]:
            return
        component: list[str] = []
        while stack:
            target = stack.pop()
            on_stack.remove(target)
            component.append(target)
            if target == module:
                break
        if len(component) > 1:
            components.append(sorted(component))

    for module in sorted(modules):
        if module not in indices:
            visit(module)
    return sorted(components, key=lambda item: (item[0], len(item)))


def _implementation_hash(node: ast.AST) -> str | None:
    body = list(getattr(node, "body", ()))
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    if sum(1 for item in body for _ in ast.walk(item)) < 12:
        return None
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        payload = {
            "kind": type(node).__name__,
            "arguments": ast.dump(node.args, include_attributes=False),
            "body": [ast.dump(item, include_attributes=False) for item in body],
        }
    elif isinstance(node, ast.ClassDef):
        payload = {
            "kind": "ClassDef",
            "bases": sorted(ast.unparse(base).rsplit(".", 1)[-1] for base in node.bases),
            "body": [ast.dump(item, include_attributes=False) for item in body],
        }
    else:
        return None
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _duplicate_groups(files: dict[str, str]) -> int:
    grouped: dict[str, set[str]] = defaultdict(set)
    for path, source in sorted(files.items()):
        tree = ast.parse(source, filename=path)
        module = _module_name(path)
        for node in tree.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            digest = _implementation_hash(node)
            if digest:
                grouped[digest].add(module)
    return sum(len(modules) >= 2 for modules in grouped.values())


def _loc_summary(files: dict[str, str]) -> dict[str, Any]:
    rows = [
        {
            "file": path,
            "module": _module_name(path),
            "physical_loc": len(source.splitlines()),
            "nonempty_loc": sum(bool(line.strip()) for line in source.splitlines()),
        }
        for path, source in sorted(files.items())
    ]
    services = [row for row in rows if row["file"].startswith("app/services/")]
    largest = max(rows, key=lambda row: (row["physical_loc"], row["module"]))
    return {
        "python_files": len(rows),
        "physical_loc": sum(row["physical_loc"] for row in rows),
        "nonempty_loc": sum(row["nonempty_loc"] for row in rows),
        "services_python_files": len(services),
        "services_physical_loc": sum(row["physical_loc"] for row in services),
        "services_nonempty_loc": sum(row["nonempty_loc"] for row in services),
        "largest_module": largest,
        "by_module": {row["module"]: row for row in rows},
    }


def _official_p0_services_loc() -> int:
    source = P0_ARCHITECTURE_BASELINE.read_text(encoding="utf-8")
    match = re.search(r"\| `app/services` \| [^|]+ \| \d+ \| ([\d,]+) \|", source)
    if match is None:
        raise RuntimeError("P0 services LOC baseline is missing")
    return int(match.group(1).replace(",", ""))


def _official_p0_god_locs() -> dict[str, int]:
    source = P0_GOD_MODULE_BASELINE.read_text(encoding="utf-8")
    result: dict[str, int] = {}
    for match in re.finditer(r"^\| (app\.[^| ]+) \| (\d+) \|", source, re.MULTILINE):
        result.setdefault(match.group(1), int(match.group(2)))
    if not result:
        raise RuntimeError("P0 God-module baseline is missing")
    return result


def _raw_edge_count(
    edges: list[dict[str, Any]], source_layer: str, target_layers: set[str]
) -> int:
    return sum(
        edge["source_layer"] == source_layer
        and edge["target_layer"] in target_layers
        for edge in edges
    )


def scan() -> dict[str, Any]:
    p0_files = _git_archive_python_files(P0_BASELINE_COMMIT)
    p9_files = _current_python_files()
    p0_dependencies = _dependency_scan(p0_files)
    p9_dependencies = _dependency_scan(p9_files)
    p0_loc = _loc_summary(p0_files)
    p9_loc = _loc_summary(p9_files)
    p0_violations = _violation_summary(p0_dependencies["edges"])
    p9_violations = _violation_summary(p9_dependencies["edges"])
    p0_cycles = _strongly_connected_components(
        p0_dependencies["modules"], p0_dependencies["edges"]
    )
    p9_cycles = _strongly_connected_components(
        p9_dependencies["modules"], p9_dependencies["edges"]
    )
    p0_god_locs = _official_p0_god_locs()
    god_comparison = []
    for label, p0_module, p9_module in GOD_OWNER_MAPPINGS:
        p0_value = p0_god_locs[p0_module]
        p9_value = p9_loc["by_module"][p9_module]["physical_loc"]
        god_comparison.append(
            {
                "owner": label,
                "p0_module": p0_module,
                "p0_physical_loc": p0_value,
                "p9_module": p9_module,
                "p9_physical_loc": p9_value,
                "delta": p9_value - p0_value,
            }
        )

    p0_services_nonempty = _official_p0_services_loc()
    final_signals = {
        "domain_to_infrastructure_edges": _raw_edge_count(
            p9_dependencies["edges"],
            "domain",
            {"adapters", "application", "graphs", "runtime", "services"},
        ),
        "application_to_adapters_edges": _raw_edge_count(
            p9_dependencies["edges"], "application", {"adapters"}
        ),
        "application_to_services_edges": _raw_edge_count(
            p9_dependencies["edges"], "application", {"services"}
        ),
        "application_to_runtime_edges": _raw_edge_count(
            p9_dependencies["edges"], "application", {"runtime"}
        ),
        "application_to_langgraph_edges": _raw_edge_count(
            p9_dependencies["edges"], "application", {"graphs"}
        ),
        "services_directory_exists": (APP_ROOT / "services").exists(),
        "ratchet_legacy_violation_pairs": p9_violations["unique_pair_count"],
    }
    measured_boundaries_satisfied = all(
        value == 0
        for key, value in final_signals.items()
        if key != "services_directory_exists"
    ) and not final_signals["services_directory_exists"]

    def public_loc(loc: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in loc.items() if key != "by_module"}

    return {
        "metadata": {
            "generated_by": "tools/architecture/generate_final_architecture_report.py",
            "p0_baseline_commit": P0_BASELINE_COMMIT,
            "p0_services_loc_source": "docs/architecture/current-architecture-baseline.md",
            "p0_god_module_source": "artifacts/architecture/god-module-baseline.md",
            "retrospective_metrics": [
                "total Python LOC",
                "cross-layer violation pairs",
                "dependency cycle SCCs",
                "exact normalized AST duplicate groups",
            ],
            "p0_parse_errors": len(p0_dependencies["parse_errors"]),
            "p9_parse_errors": len(p9_dependencies["parse_errors"]),
        },
        "loc": {
            "p0": {
                **public_loc(p0_loc),
                "official_services_nonempty_loc": p0_services_nonempty,
            },
            "p9": {
                **public_loc(p9_loc),
                "official_services_nonempty_loc": p9_loc["services_nonempty_loc"],
            },
        },
        "god_module_owner_comparison": god_comparison,
        "architecture_metrics": {
            "cross_layer_violation_pairs": {
                "p0": p0_violations,
                "p9": p9_violations,
                "delta": p9_violations["unique_pair_count"]
                - p0_violations["unique_pair_count"],
            },
            "dependency_cycles": {
                "p0_group_count": len(p0_cycles),
                "p0_module_count": sum(map(len, p0_cycles)),
                "p0_groups": p0_cycles,
                "p9_group_count": len(p9_cycles),
                "p9_module_count": sum(map(len, p9_cycles)),
                "p9_groups": p9_cycles,
            },
            "exact_duplicate_groups": {
                "p0": _duplicate_groups(p0_files),
                "p9": _duplicate_groups(p9_files),
            },
        },
        "final_gate_signals": final_signals,
        "measured_boundary_signals_satisfied": measured_boundaries_satisfied,
        "architecture_refactor_status": (
            "COMPLETE" if measured_boundaries_satisfied else "INCOMPLETE"
        ),
        "architecture_refactor_gate": (
            "PASS" if measured_boundaries_satisfied else "FAIL"
        ),
        "architecture_boundary_gate": (
            "PASS" if measured_boundaries_satisfied else "FAIL"
        ),
        "postgres_reliability_verification": "DEFERRED",
        "postgres_reliability_deferred_reason": (
            "BLOCKED_ENVIRONMENT / EXTERNAL_APPROVAL_UNAVAILABLE"
        ),
        "full_production_reliability_gate": "NOT_VERIFIED",
        "final_architecture_definition_satisfied": measured_boundaries_satisfied,
        "final_gate_scope_note": (
            "P9-T04 supplies measurement evidence for the P0-P9 architecture "
            "refactor closure. Protected PostgreSQL reliability is deferred and "
            "the full production reliability gate remains NOT_VERIFIED."
        ),
        "decision": (
            "The P9 measurement task is complete, its measured boundary signals "
            "are satisfied, and it supports P0-P9 architecture refactor closure."
            if measured_boundaries_satisfied
            else "The P9 measurement task is complete, but remaining boundary "
            "violations must not be hidden by LOC reduction."
        ),
    }


def _delta(p0: int, p9: int) -> str:
    value = p9 - p0
    return f"{value:+,}"


def render_markdown(result: dict[str, Any]) -> str:
    p0 = result["loc"]["p0"]
    p9 = result["loc"]["p9"]
    architecture = result["architecture_metrics"]
    violations = architecture["cross_layer_violation_pairs"]
    cycles = architecture["dependency_cycles"]
    duplicates = architecture["exact_duplicate_groups"]
    signals = result["final_gate_signals"]
    lines = [
        "# P9 Final Python LOC and Architecture Report",
        "",
        "Status: P9-T04 COMPLETE - architecture closure measurement evidence",
        "",
        "## Counting Rules",
        "",
        "- P0 is pinned to commit `0af2a8b`.",
        "- Services LOC uses the official P0 non-empty-line baseline; P9 uses the same definition.",
        "- God-module and largest-module LOC are physical lines including blanks and comments.",
        "- Cross-layer violations are unique `(rule, source module, target module)` ratchet pairs.",
        "- Dependency cycles are strongly connected components containing at least two modules.",
        "- Duplicate implementations are exact normalized top-level AST-body groups.",
        "- LOC is an outcome metric, not proof of architecture quality.",
        "",
        "## P0 vs P9",
        "",
        "| metric | P0 | P9 | delta |",
        "| --- | ---: | ---: | ---: |",
        f"| App Python files | {p0['python_files']:,} | {p9['python_files']:,} | {_delta(p0['python_files'], p9['python_files'])} |",
        f"| App physical LOC | {p0['physical_loc']:,} | {p9['physical_loc']:,} | {_delta(p0['physical_loc'], p9['physical_loc'])} |",
        f"| App non-empty LOC | {p0['nonempty_loc']:,} | {p9['nonempty_loc']:,} | {_delta(p0['nonempty_loc'], p9['nonempty_loc'])} |",
        f"| Services Python files | {p0['services_python_files']:,} | {p9['services_python_files']:,} | {_delta(p0['services_python_files'], p9['services_python_files'])} |",
        f"| Services non-empty LOC | {p0['official_services_nonempty_loc']:,} | {p9['official_services_nonempty_loc']:,} | {_delta(p0['official_services_nonempty_loc'], p9['official_services_nonempty_loc'])} |",
        f"| Cross-layer violation pairs | {violations['p0']['unique_pair_count']:,} | {violations['p9']['unique_pair_count']:,} | {violations['delta']:+,} |",
        f"| Dependency cycle groups | {cycles['p0_group_count']:,} | {cycles['p9_group_count']:,} | {_delta(cycles['p0_group_count'], cycles['p9_group_count'])} |",
        f"| Modules in dependency cycles | {cycles['p0_module_count']:,} | {cycles['p9_module_count']:,} | {_delta(cycles['p0_module_count'], cycles['p9_module_count'])} |",
        f"| Exact duplicate implementation groups | {duplicates['p0']:,} | {duplicates['p9']:,} | {_delta(duplicates['p0'], duplicates['p9'])} |",
        f"| Largest module physical LOC | {p0['largest_module']['physical_loc']:,} | {p9['largest_module']['physical_loc']:,} | {_delta(p0['largest_module']['physical_loc'], p9['largest_module']['physical_loc'])} |",
        "",
        "Largest modules:",
        "",
        f"- P0: `{p0['largest_module']['module']}` ({p0['largest_module']['physical_loc']:,} LOC from pinned commit).",
        f"- P9: `{p9['largest_module']['module']}` ({p9['largest_module']['physical_loc']:,} LOC).",
        "",
        "## God-Module Owners",
        "",
        "| responsibility | P0 owner | P0 LOC | P9 owner | P9 LOC | delta |",
        "| --- | --- | ---: | --- | ---: | ---: |",
    ]
    for row in result["god_module_owner_comparison"]:
        lines.append(
            f"| {row['owner']} | `{row['p0_module']}` | {row['p0_physical_loc']:,} | "
            f"`{row['p9_module']}` | {row['p9_physical_loc']:,} | {row['delta']:+,} |"
        )
    lines.extend(
        [
            "",
            "## Remaining Boundary Signals",
            "",
            "| signal | final value | required |",
            "| --- | ---: | ---: |",
            f"| Domain to infrastructure edges | {signals['domain_to_infrastructure_edges']} | 0 |",
            f"| Application to adapters edges | {signals['application_to_adapters_edges']} | 0 |",
            f"| Application to services edges | {signals['application_to_services_edges']} | 0 |",
            f"| Application to Runtime edges | {signals['application_to_runtime_edges']} | 0 |",
            f"| Application to LangGraph edges | {signals['application_to_langgraph_edges']} | 0 |",
            f"| Ratchet legacy violation pairs | {signals['ratchet_legacy_violation_pairs']} | 0 |",
            f"| `app/services` exists | {'yes' if signals['services_directory_exists'] else 'no'} | no |",
            "",
            "## Decision",
            "",
            result["decision"],
            "",
            result["final_gate_scope_note"],
            "",
            "Large composition/graph modules, remaining cycles, and duplicate groups are Future Hardening items rather than P0-P9 closure blockers.",
            "",
        ]
    )
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
    print(f"p0_app_files={result['loc']['p0']['python_files']}")
    print(f"p9_app_files={result['loc']['p9']['python_files']}")
    print(
        "p0_cross_layer_violations="
        f"{result['architecture_metrics']['cross_layer_violation_pairs']['p0']['unique_pair_count']}"
    )
    print(
        "p9_cross_layer_violations="
        f"{result['architecture_metrics']['cross_layer_violation_pairs']['p9']['unique_pair_count']}"
    )
    print(
        "final_architecture_definition_satisfied="
        f"{result['final_architecture_definition_satisfied']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
