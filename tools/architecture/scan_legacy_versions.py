#!/usr/bin/env python3
"""P9-T03: prove legacy-version removal gates before any deletion."""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
TESTS_ROOT = REPO_ROOT / "tests"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"
JSON_PATH = ARTIFACTS_DIR / "legacy-version-removal.json"
MARKDOWN_PATH = ARTIFACTS_DIR / "legacy-version-removal.md"

VERSIONED_MODULE_RE = re.compile(r"^(?P<base>.+)_v(?P<version>\d+)$")
ANY_VERSION_RE = re.compile(r"(?:^|[._-])v(?P<version>\d+)(?:$|[._-])")
RUNTIME_WIRING_PREFIXES = (
    "app.a2a",
    "app.agents",
    "app.api",
    "app.graphs",
    "app.runtime",
)
RUNTIME_WIRING_MODULES = {"app.main"}
AUDIT_HARNESS_FILES = {
    "tests/architecture/test_legacy_version_removal_gate.py",
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


def _discover_families(app_sources: list[Source]) -> list[dict[str, Any]]:
    modules = {source.module for source in app_sources}
    grouped: dict[str, dict[int, str]] = defaultdict(dict)
    for module in sorted(modules):
        match = VERSIONED_MODULE_RE.match(module)
        if match is None:
            continue
        grouped[match.group("base")][int(match.group("version"))] = module
    families: list[dict[str, Any]] = []
    for base, members in sorted(grouped.items()):
        if base in modules:
            members.setdefault(1, base)
        if len(members) < 2:
            continue
        latest_version = max(members)
        families.append(
            {
                "family": base,
                "latest_version": latest_version,
                "latest_module": members[latest_version],
                "members": [
                    {"version": version, "module": module}
                    for version, module in sorted(members.items())
                ],
            }
        )
    return families


def _source_version(module: str) -> int | None:
    matches = list(ANY_VERSION_RE.finditer(module + "."))
    if not matches:
        return None
    return int(matches[-1].group("version"))


def _references(source: Source, targets: set[str]) -> list[dict[str, Any]]:
    found: set[tuple[str, int]] = set()
    for node in ast.walk(source.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in targets:
                    found.add(("import", node.lineno))
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(source, node)
            if base in targets:
                found.add(("from_import", node.lineno))
            for alias in node.names:
                possible = f"{base}.{alias.name}" if base else alias.name
                if possible in targets:
                    found.add(("from_import_module", node.lineno))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            normalized = node.value.replace(":", ".")
            for target in targets:
                if normalized == target or normalized.startswith(target + "."):
                    found.add(("qualified_string", node.lineno))
    return [
        {
            "file": source.relative,
            "module": source.module,
            "line": line,
            "reason": reason,
        }
        for reason, line in sorted(found, key=lambda item: (item[1], item[0]))
    ]


def scan() -> dict[str, Any]:
    sources, parse_errors = _load_sources()
    app_sources = [source for source in sources if source.scope == "production"]
    families = _discover_families(app_sources)
    source_by_module = {source.module: source for source in app_sources}
    audits: list[dict[str, Any]] = []

    for family in families:
        latest_version = family["latest_version"]
        latest_module = family["latest_module"]
        for member in family["members"]:
            version = member["version"]
            legacy_module = member["module"]
            if version == latest_version:
                continue
            production: list[dict[str, Any]] = []
            runtime: list[dict[str, Any]] = []
            integration: list[dict[str, Any]] = []
            migration: list[dict[str, Any]] = []
            for source in sources:
                if source.module == legacy_module:
                    continue
                references = _references(source, {legacy_module})
                if not references:
                    continue
                if source.scope == "production":
                    production.extend(references)
                    if _is_runtime_wiring(source.module):
                        runtime.extend(references)
                if (
                    source.scope == "test"
                    and source.relative not in AUDIT_HARNESS_FILES
                ):
                    integration.extend(references)
                source_version = _source_version(source.module)
                is_newer_version_bridge = (
                    source.scope == "production"
                    and source_version is not None
                    and source_version > version
                )
                is_migration_tool = source.scope == "script" or any(
                    token in source.relative.casefold()
                    for token in ("migration", "migrate")
                )
                if is_newer_version_bridge or is_migration_tool:
                    migration.extend(references)

            gates = {
                "production_import": len(production),
                "runtime_wiring": len(runtime),
                "integration_dependency": len(integration),
                "migration_dependency": len(migration),
            }
            authorized = all(count == 0 for count in gates.values())
            source = source_by_module[legacy_module]
            audits.append(
                {
                    "family": family["family"],
                    "legacy_version": version,
                    "legacy_module": legacy_module,
                    "legacy_file": source.relative,
                    "replacement_version": latest_version,
                    "replacement_module": latest_module,
                    "gates": gates,
                    "removal_authorized": authorized,
                    "action": "remove" if authorized else "retain_blocked",
                    "blockers": [name for name, count in gates.items() if count],
                    "evidence": {
                        "production_import": production,
                        "runtime_wiring": runtime,
                        "integration_dependency": integration,
                        "migration_dependency": migration,
                    },
                }
            )

    authorized = [audit for audit in audits if audit["removal_authorized"]]
    blocked = [audit for audit in audits if not audit["removal_authorized"]]
    return {
        "metadata": {
            "generated_by": "tools/architecture/scan_legacy_versions.py",
            "app_files_scanned": len(app_sources),
            "script_files_scanned": sum(source.scope == "script" for source in sources),
            "test_files_scanned": sum(source.scope == "test" for source in sources),
            "parse_error_count": len(parse_errors),
            "version_family_count": len(families),
            "legacy_module_count": len(audits),
            "removal_authorized_count": len(authorized),
            "retained_blocked_count": len(blocked),
            "gate_rule": (
                "removal is authorized only when production_import, runtime_wiring, "
                "integration_dependency, and migration_dependency are all zero"
            ),
        },
        "parse_errors": parse_errors,
        "families": families,
        "legacy_module_audits": audits,
        "removal_actions": {
            "removed": [],
            "retained": [audit["legacy_module"] for audit in blocked],
        },
        "decision": (
            "No legacy version is removable under the four-gate rule. Existing "
            "version contracts remain active and were not copied or deleted merely "
            "to force dependency counts to zero."
            if not authorized
            else "Only modules with all four gates at zero may be removed."
        ),
    }


def _evidence_lines(audit: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for gate, evidence in audit["evidence"].items():
        lines.extend(
            f"- `{gate}`: `{item['file']}:{item['line']}` ({item['reason']})"
            for item in evidence
        )
    return lines or ["- No blocking evidence."]


def render_markdown(result: dict[str, Any]) -> str:
    metadata = result["metadata"]
    audits = result["legacy_module_audits"]
    lines = [
        "# P9 Legacy Version Removal Gate",
        "",
        (
            "Status: P9-T03 COMPLETE - no removal authorized"
            if metadata["removal_authorized_count"] == 0
            else "Status: P9-T03 REVIEW REQUIRED - removal candidates detected"
        ),
        "",
        "## Rule",
        "",
        "A legacy module may be deleted only when all four values are zero:",
        "",
        "1. production import",
        "2. Runtime wiring",
        "3. integration dependency",
        "4. migration dependency",
        "",
        "Unversioned modules that share a base name with `_v2`/`_v3` are audited as v1.",
        "",
        "## Summary",
        "",
        f"- Version families: {metadata['version_family_count']}",
        f"- Legacy modules audited: {metadata['legacy_module_count']}",
        f"- Removal authorized: {metadata['removal_authorized_count']}",
        f"- Retained due to blockers: {metadata['retained_blocked_count']}",
        f"- Parse errors: {metadata['parse_error_count']}",
        "",
        "| legacy module | replacement | production | runtime | integration | migration | action |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for audit in audits:
        gates = audit["gates"]
        lines.append(
            f"| `{audit['legacy_module']}` | `{audit['replacement_module']}` | "
            f"{gates['production_import']} | {gates['runtime_wiring']} | "
            f"{gates['integration_dependency']} | {gates['migration_dependency']} | "
            f"`{audit['action']}` |"
        )
    lines.extend(["", "## Blocking Evidence", ""])
    for audit in audits:
        lines.extend(
            [
                f"### {audit['legacy_module']}",
                "",
                f"Blocked by: {', '.join(audit['blockers'])}.",
                "",
                *_evidence_lines(audit),
                "",
            ]
        )
    lines.extend(
        [
            "## Decision",
            "",
            result["decision"],
            "",
            "No production module, test, script, dataset, or persisted-state compatibility path was deleted in P9-T03.",
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
    for key, value in result["metadata"].items():
        if key not in {"generated_by", "gate_rule"}:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
