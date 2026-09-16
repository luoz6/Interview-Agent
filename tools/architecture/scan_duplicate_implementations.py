#!/usr/bin/env python3
"""P9-T01: deterministic duplicate-implementation candidate scanner."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "architecture"
JSON_PATH = ARTIFACTS_DIR / "duplicate-implementation-scan.json"
MARKDOWN_PATH = ARTIFACTS_DIR / "duplicate-implementation-scan.md"

VERSION_RE = re.compile(r"(?i)(?:_v\d+|v\d+|version[_-]?\d+)")
REPOSITORY_SUFFIXES = ("Repository", "Store")
REPOSITORY_PREFIXES = (
    "InMemory",
    "Postgres",
    "PgVector",
    "Static",
    "Local",
    "Durable",
    "Runtime",
    "Sql",
)
DTO_BASES = {"BaseModel", "TypedDict", "NamedTuple"}
DTO_SUFFIXES = (
    "Artifact",
    "Config",
    "DTO",
    "Event",
    "Model",
    "Payload",
    "Record",
    "Request",
    "Response",
    "Result",
    "Settings",
    "Snapshot",
    "State",
)
EVALUATOR_TOKENS = (
    "diagnostic",
    "eval",
    "evaluator",
    "quality",
    "review",
    "scor",
)
RUNTIME_WIRING_PREFIXES = (
    "build_",
    "create_",
    "get_",
    "load_",
    "reset_",
    "start_",
    "stop_",
)

REVIEWED_FINDINGS = (
    {
        "priority": "high",
        "disposition": "overlapping_port_contracts",
        "finding": (
            "InterviewLaunchRepository and InterviewSessionRepository each have "
            "multiple Protocol definitions under app.ports. Their method surfaces "
            "differ, so consolidation needs a contract migration rather than deletion."
        ),
        "next_task": "P9-T02 reachability proof, then a dedicated port consolidation",
    },
    {
        "priority": "high",
        "disposition": "canonical_owner_candidate",
        "finding": (
            "interview_plan_from_intent_draft and "
            "enforce_generated_interview_question_quality have exact copies in Domain "
            "and Runtime; Domain is the likely canonical owner."
        ),
        "next_task": "replace Runtime copies with canonical imports after compatibility tests",
    },
    {
        "priority": "high",
        "disposition": "shared_dto_candidate",
        "finding": (
            "InterviewArtifactContext and EvidenceArtifactContext are exact dataclass "
            "copies and are candidates for one stable shared contract."
        ),
        "next_task": "prove ownership and import direction before consolidation",
    },
    {
        "priority": "medium",
        "disposition": "shared_utility_candidates",
        "finding": (
            "UUID, owner, canonical JSON/hash, file hash, safe-ref, and UTC helpers "
            "have exact repeated bodies across modules."
        ),
        "next_task": "consolidate only where dependency direction remains valid",
    },
    {
        "priority": "low",
        "disposition": "expected_polymorphism",
        "finding": (
            "Most repository/store families are one port with in-memory and durable "
            "adapter implementations; family-name similarity is expected, not duplication."
        ),
        "next_task": "exclude from deletion unless reachability proves an adapter unused",
    },
    {
        "priority": "low",
        "disposition": "composition_wrapper",
        "finding": (
            "consume_round_review_event and consume_round_review_event_payload in "
            "app.runtime.composition inject dependencies and delegate to the consumer; "
            "their same-name matches are not duplicate evaluator implementations."
        ),
        "next_task": "retain as composition-root wiring",
    },
    {
        "priority": "high",
        "disposition": "migration_dependency_present",
        "finding": (
            "The v2 knowledge evaluation modules are imported by v3 and scripts, while "
            "durable_interview_state_v2 remains in production Runtime wiring."
        ),
        "next_task": "P9-T03 must reach zero migration and runtime dependency before removal",
    },
)


def module_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = relative.parts[:-1] if path.name == "__init__.py" else relative.parts
    return ".".join(parts)


def _annotation(node: ast.AST | None) -> str:
    return ast.unparse(node) if node is not None else "Any"


def _decorator_names(node: ast.ClassDef) -> set[str]:
    return {ast.unparse(decorator).split("(", 1)[0] for decorator in node.decorator_list}


def _class_bases(node: ast.ClassDef) -> list[str]:
    return [ast.unparse(base).rsplit(".", 1)[-1] for base in node.bases]


def _class_fields(node: ast.ClassDef) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            fields.append((item.target.id, _annotation(item.annotation)))
    return fields


def _node_count(nodes: Iterable[ast.AST]) -> int:
    return sum(1 for node in nodes for _ in ast.walk(node))


def _implementation_hash(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str | None:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    if _node_count(body) < 12:
        return None
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        payload = {
            "kind": type(node).__name__,
            "arguments": ast.dump(node.args, include_attributes=False),
            "body": [ast.dump(item, include_attributes=False) for item in body],
        }
    else:
        payload = {
            "kind": "ClassDef",
            "bases": sorted(_class_bases(node)),
            "body": [ast.dump(item, include_attributes=False) for item in body],
        }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _repository_family(name: str) -> str:
    result = name
    for prefix in REPOSITORY_PREFIXES:
        if result.startswith(prefix) and len(result) > len(prefix):
            result = result[len(prefix) :]
            break
    return result.casefold()


def _version_key(value: str) -> str | None:
    if VERSION_RE.search(value) is None:
        return None
    return VERSION_RE.sub("<version>", value).casefold()


def _call_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    calls = {
        ast.unparse(call.func)
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
    }
    return sorted(calls)


def _review_metadata(
    category: str,
    members: list[dict[str, Any]] | list[str],
) -> dict[str, str]:
    if category == "exact_implementations":
        return {
            "disposition": "consolidation_candidate",
            "review_priority": "medium",
            "review_note": "Exact body match; verify ownership and dependency direction.",
        }
    if category == "repository_families":
        protocol_count = sum(
            1
            for item in members
            if isinstance(item, dict) and item["module"].startswith("app.ports.")
        )
        if protocol_count > 1:
            return {
                "disposition": "overlapping_port_contracts",
                "review_priority": "high",
                "review_note": "Multiple port Protocols share this family name.",
            }
        return {
            "disposition": "expected_polymorphic_family",
            "review_priority": "low",
            "review_note": "Port and adapter variants are expected unless reachability says otherwise.",
        }
    if category == "dto_shapes":
        names = {
            item["name"] for item in members if isinstance(item, dict)
        }
        shared_context = {
            "InterviewArtifactContext",
            "EvidenceArtifactContext",
        }
        if shared_context.issubset(names):
            return {
                "disposition": "shared_contract_candidate",
                "review_priority": "high",
                "review_note": "Exact context shape and body; establish a canonical contract owner.",
            }
        return {
            "disposition": "compatible_shape_only",
            "review_priority": "low",
            "review_note": "Field-shape equality alone does not establish duplicate semantics.",
        }
    if category == "evaluator_names":
        names = {
            item["name"] for item in members if isinstance(item, dict)
        }
        if names.intersection(
            {"consume_round_review_event", "consume_round_review_event_payload"}
        ):
            return {
                "disposition": "composition_wrapper",
                "review_priority": "low",
                "review_note": "Composition injects dependencies and delegates; not an evaluator clone.",
            }
        return {
            "disposition": "evaluation_related_name_review",
            "review_priority": "medium",
            "review_note": "Broad token/name heuristic; compare behavior before consolidation.",
        }
    if category == "runtime_wiring_fingerprints":
        return {
            "disposition": "runtime_wiring_review",
            "review_priority": "high",
            "review_note": "Matching call sets require manual composition-root review.",
        }
    if category in {"version_modules", "version_symbols"}:
        return {
            "disposition": "migration_dependency_review",
            "review_priority": "high",
            "review_note": "Version removal requires zero production, wiring, integration, and migration dependency.",
        }
    raise ValueError(f"unsupported duplicate scan category: {category}")


def scan(app_root: Path = APP_ROOT) -> dict[str, Any]:
    definitions: list[dict[str, Any]] = []
    version_modules: dict[str, list[str]] = defaultdict(list)
    parse_errors: list[dict[str, str]] = []
    files = sorted(app_root.rglob("*.py"))

    for path in files:
        relative = path.relative_to(REPO_ROOT).as_posix()
        module = module_name(path)
        module_version_key = _version_key(module)
        if module_version_key is not None:
            version_modules[module_version_key].append(module)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            parse_errors.append({"file": relative, "error": type(exc).__name__})
            continue

        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            record: dict[str, Any] = {
                "module": module,
                "file": relative,
                "name": node.name,
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                "line": node.lineno,
                "implementation_hash": _implementation_hash(node),
                "version_key": _version_key(f"{module}.{node.name}"),
            }
            if isinstance(node, ast.ClassDef):
                bases = _class_bases(node)
                fields = _class_fields(node)
                decorators = _decorator_names(node)
                record.update(
                    {
                        "bases": bases,
                        "fields": fields,
                        "is_repository": node.name.endswith(REPOSITORY_SUFFIXES),
                        "repository_family": (
                            _repository_family(node.name)
                            if node.name.endswith(REPOSITORY_SUFFIXES)
                            else None
                        ),
                        "is_dto": bool(
                            DTO_BASES.intersection(bases)
                            or "dataclass" in decorators
                            or node.name.endswith(DTO_SUFFIXES)
                        ),
                    }
                )
            else:
                record.update(
                    {
                        "calls": _call_fingerprint(node),
                        "is_runtime_wiring": bool(
                            module.startswith("app.runtime")
                            and node.name.startswith(RUNTIME_WIRING_PREFIXES)
                        ),
                    }
                )
            record["is_evaluator"] = any(
                token in f"{module}.{node.name}".casefold()
                for token in EVALUATOR_TOKENS
            )
            definitions.append(record)

    exact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    repositories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dto_shapes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evaluators: dict[str, list[dict[str, Any]]] = defaultdict(list)
    runtime_wiring: dict[str, list[dict[str, Any]]] = defaultdict(list)
    version_symbols: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for definition in definitions:
        if definition["implementation_hash"]:
            exact[definition["implementation_hash"]].append(definition)
        if definition.get("is_repository"):
            repositories[definition["repository_family"]].append(definition)
        fields = definition.get("fields", [])
        if definition.get("is_dto") and len(fields) >= 2:
            shape = json.dumps(fields, sort_keys=True, separators=(",", ":"))
            dto_shapes[shape].append(definition)
        if definition["is_evaluator"]:
            evaluators[definition["name"].casefold()].append(definition)
        if definition.get("is_runtime_wiring"):
            calls = definition.get("calls", [])
            if calls:
                runtime_wiring["|".join(calls)].append(definition)
        if definition["version_key"]:
            version_symbols[definition["version_key"]].append(definition)

    def groups(
        mapping: dict[str, list[dict[str, Any]]],
        *,
        category: str,
        cross_module: bool = True,
    ):
        result = []
        for key, items in sorted(mapping.items()):
            modules = {item["module"] for item in items}
            if len(items) < 2 or (cross_module and len(modules) < 2):
                continue
            members = [
                {
                    "module": item["module"],
                    "name": item["name"],
                    "kind": item["kind"],
                    "file": item["file"],
                    "line": item["line"],
                }
                for item in sorted(items, key=lambda value: (value["module"], value["line"]))
            ]
            result.append(
                {
                    "key": key,
                    "status": "confirmed_exact" if mapping is exact else "candidate",
                    **_review_metadata(category, members),
                    "members": members,
                }
            )
        return result

    module_families = [
        {
            "key": key,
            "status": "candidate",
            **_review_metadata("version_modules", sorted(set(members))),
            "members": sorted(set(members)),
        }
        for key, members in sorted(version_modules.items())
        if len(set(members)) >= 2
    ]
    return {
        "metadata": {
            "generated_by": "tools/architecture/scan_duplicate_implementations.py",
            "files_scanned": len(files),
            "definitions_scanned": len(definitions),
            "parse_error_count": len(parse_errors),
            "classification_rule": (
                "Only normalized AST body matches are confirmed_exact; all family, "
                "shape, name, wiring, and version matches are review candidates."
            ),
        },
        "parse_errors": parse_errors,
        "reviewed_findings": list(REVIEWED_FINDINGS),
        "categories": {
            "exact_implementations": groups(exact, category="exact_implementations"),
            "repository_families": groups(repositories, category="repository_families"),
            "dto_shapes": groups(dto_shapes, category="dto_shapes"),
            "evaluator_names": groups(evaluators, category="evaluator_names"),
            "runtime_wiring_fingerprints": groups(
                runtime_wiring, category="runtime_wiring_fingerprints"
            ),
            "version_modules": module_families,
            "version_symbols": groups(version_symbols, category="version_symbols"),
        },
    }


def _members(group: dict[str, Any]) -> str:
    members = group["members"]
    if members and isinstance(members[0], str):
        return "<br>".join(members)
    return "<br>".join(f"{item['module']}:{item['name']}" for item in members)


def render_markdown(result: dict[str, Any]) -> str:
    metadata = result["metadata"]
    categories = result["categories"]
    labels = {
        "exact_implementations": "Exact normalized AST implementations",
        "repository_families": "Repository / store families",
        "dto_shapes": "DTO field-shape matches",
        "evaluator_names": "Evaluation-related name matches (broad heuristic)",
        "runtime_wiring_fingerprints": "Runtime wiring call fingerprints",
        "version_modules": "Version module families",
        "version_symbols": "Version symbol families",
    }
    lines = [
        "# P9 Duplicate Implementation Scan",
        "",
        "Status: P9-T01 COMPLETE - observational scan only",
        "",
        "## Method",
        "",
        f"- Python files scanned: {metadata['files_scanned']}",
        f"- Top-level definitions scanned: {metadata['definitions_scanned']}",
        f"- Parse errors: {metadata['parse_error_count']}",
        "- Exact matches use normalized AST bodies with docstrings removed.",
        "- Repository, DTO, evaluator, runtime-wiring, and version-family matches are candidates, not deletion decisions.",
        "- Evaluation-related names use a deliberately broad token heuristic and include reviewed false positives.",
        "",
        "## Summary",
        "",
        "| category | groups | disposition |",
        "| --- | ---: | --- |",
    ]
    for category, label in labels.items():
        disposition = "exact clone review" if category == "exact_implementations" else "manual review required"
        lines.append(f"| {label} | {len(categories[category])} | {disposition} |")

    lines.extend(
        [
            "",
            "## Reviewed Findings",
            "",
            "| priority | disposition | finding | next task |",
            "| --- | --- | --- | --- |",
        ]
    )
    for finding in result["reviewed_findings"]:
        lines.append(
            f"| {finding['priority']} | `{finding['disposition']}` | "
            f"{finding['finding']} | {finding['next_task']} |"
        )

    for category, label in labels.items():
        lines.extend(["", f"## {label}", ""])
        groups = categories[category]
        if not groups:
            lines.append("No cross-module groups detected.")
            continue
        lines.extend(
            [
                "| status | priority | disposition | key | members |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for group in groups:
            key = str(group["key"]).replace("|", "\\|")
            lines.append(
                f"| {group['status']} | {group['review_priority']} | "
                f"`{group['disposition']}` | `{key}` | {_members(group)} |"
            )

    lines.extend(
        [
            "",
            "## Decision Boundary",
            "",
            "This scan does not authorize deletion. P9-T02 must prove static, runtime-wiring, and test reachability before dead-code removal. P9-T03 must additionally prove migration dependency is zero before removing a version family.",
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
    print(f"files_scanned={result['metadata']['files_scanned']}")
    print(f"definitions_scanned={result['metadata']['definitions_scanned']}")
    print(f"parse_error_count={result['metadata']['parse_error_count']}")
    for category, groups in result["categories"].items():
        print(f"{category}={len(groups)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
