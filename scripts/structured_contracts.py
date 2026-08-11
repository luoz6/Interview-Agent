from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATHS = {
    "requirements": ROOT / "contracts" / "requirements.yaml",
    "decisions": ROOT / "contracts" / "decisions.yaml",
    "tasks": ROOT / "contracts" / "tasks.yaml",
    "gates": ROOT / "contracts" / "gates.yaml",
    "runbooks": ROOT / "contracts" / "runbooks.yaml",
    "releases": ROOT / "contracts" / "releases.yaml",
}
GENERATED_PATHS = {
    "acceptance": ROOT / "docs" / "generated" / "refactoring-acceptance-reference.md",
    "traceability": ROOT / "docs" / "generated" / "refactoring-requirement-traceability.md",
    "execution": ROOT / "docs" / "generated" / "refactoring-execution-reference.md",
    "decisions": ROOT / "docs" / "generated" / "refactoring-decisions.md",
    "runbooks": ROOT / "docs" / "generated" / "refactoring-runbooks.md",
    "release": ROOT / "docs" / "generated" / "refactoring-release-contract.md",
}

ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
HEX_REVISION_PATTERN = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", re.IGNORECASE)
WINDOWS_PATH_PATTERN = re.compile(r"(?i)(?<![A-Z0-9])[A-Z]:[\\/]")
MACHINE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home|tmp|workspace)/")
TEST_RESULT_PATTERN = re.compile(r"\b\d+\s+(?:passed|failed|skipped)\b", re.IGNORECASE)
FORBIDDEN_KEYS = {
    "ahead",
    "behind",
    "commit",
    "commit_hash",
    "machine_path",
    "passed_count",
    "python_version",
    "revision",
    "run_id",
    "skipped_count",
    "test_count",
    "tree",
    "validated_revision",
}
TASK_STATUSES = {
    "pending",
    "in_progress",
    "implemented_pending_final_audit",
    "completed",
}
DECISION_STATUSES = {"proposed", "accepted", "superseded"}
GATE_MODES = {"automated", "approval_required", "procedural"}
RELEASE_STATES = {"not_ready", "ready", "released"}


class StructuredContractError(ValueError):
    pass


def load_documents() -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for kind, path in CONTRACT_PATHS.items():
        if not path.is_file():
            raise StructuredContractError(f"missing contract: {path.relative_to(ROOT)}")
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise StructuredContractError(f"{path.name}: root must be a mapping")
        documents[kind] = parsed
    return documents


def _require_keys(item: Mapping[str, Any], keys: Iterable[str], location: str) -> None:
    missing = [key for key in keys if key not in item]
    if missing:
        raise StructuredContractError(f"{location}: missing fields {', '.join(missing)}")


def _require_string_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise StructuredContractError(f"{location}: must be a list of non-empty strings")
    return value


def _walk(value: Any, location: str = "root") -> Iterable[tuple[str, Any]]:
    yield location, value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{location}[{index}]")


def _validate_no_ephemeral_facts(documents: Mapping[str, Mapping[str, Any]]) -> None:
    for kind, document in documents.items():
        for location, value in _walk(document, kind):
            if isinstance(value, Mapping):
                forbidden = FORBIDDEN_KEYS.intersection(str(key).lower() for key in value)
                if forbidden:
                    raise StructuredContractError(
                        f"{location}: forbidden ephemeral fields {', '.join(sorted(forbidden))}"
                    )
            if not isinstance(value, str):
                continue
            if HEX_REVISION_PATTERN.search(value):
                raise StructuredContractError(f"{location}: historical revision is forbidden")
            if WINDOWS_PATH_PATTERN.search(value) or MACHINE_PATH_PATTERN.search(value):
                raise StructuredContractError(f"{location}: machine-specific path is forbidden")
            if TEST_RESULT_PATTERN.search(value):
                raise StructuredContractError(f"{location}: fixed test result count is forbidden")


def _index_items(documents: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    all_ids: set[str] = set()
    for expected_kind, document in documents.items():
        _require_keys(document, ("schema_version", "kind", "authoritative_source", "items"), expected_kind)
        if document["schema_version"] != "1.0":
            raise StructuredContractError(f"{expected_kind}: unsupported schema_version")
        if document["kind"] != expected_kind:
            raise StructuredContractError(f"{expected_kind}: kind mismatch")
        source = document["authoritative_source"]
        if not isinstance(source, str) or Path(source).is_absolute():
            raise StructuredContractError(f"{expected_kind}: authoritative_source must be relative")
        items = document["items"]
        if not isinstance(items, list) or not items:
            raise StructuredContractError(f"{expected_kind}: items must be a non-empty list")
        index: dict[str, dict[str, Any]] = {}
        for position, item in enumerate(items):
            location = f"{expected_kind}.items[{position}]"
            if not isinstance(item, dict):
                raise StructuredContractError(f"{location}: item must be a mapping")
            _require_keys(item, ("id", "title"), location)
            item_id = item["id"]
            if not isinstance(item_id, str) or not ID_PATTERN.fullmatch(item_id):
                raise StructuredContractError(f"{location}: invalid id")
            if item_id in all_ids:
                raise StructuredContractError(f"{location}: duplicate id {item_id}")
            if not isinstance(item["title"], str) or not item["title"].strip():
                raise StructuredContractError(f"{location}: title must be non-empty")
            index[item_id] = item
            all_ids.add(item_id)
        indexes[expected_kind] = index
    return indexes


def _validate_references(
    indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    requirements = indexes["requirements"]
    decisions = indexes["decisions"]
    tasks = indexes["tasks"]
    gates = indexes["gates"]
    runbooks = indexes["runbooks"]

    def refs(item: Mapping[str, Any], field: str, target: Mapping[str, Any]) -> None:
        for reference in _require_string_list(item.get(field, []), f"{item['id']}.{field}"):
            if reference not in target:
                raise StructuredContractError(f"{item['id']}.{field}: unknown id {reference}")

    for requirement in requirements.values():
        _require_keys(requirement, ("category", "statement", "verification", "gate_ids"), requirement["id"])
        refs(requirement, "gate_ids", gates)
    for decision in decisions.values():
        _require_keys(decision, ("status", "rationale", "consequences", "requirement_ids"), decision["id"])
        if decision["status"] not in DECISION_STATUSES:
            raise StructuredContractError(f"{decision['id']}: invalid decision status")
        _require_string_list(decision["consequences"], f"{decision['id']}.consequences")
        refs(decision, "requirement_ids", requirements)
    for gate in gates.values():
        _require_keys(gate, ("mode", "proves"), gate["id"])
        if gate["mode"] not in GATE_MODES:
            raise StructuredContractError(f"{gate['id']}: invalid gate mode")
        if gate["mode"] in {"automated", "approval_required"}:
            commands = gate.get("commands")
            if not isinstance(commands, list) or not commands:
                raise StructuredContractError(f"{gate['id']}: commands are required")
            for index, command in enumerate(commands):
                _require_string_list(command, f"{gate['id']}.commands[{index}]")
        elif not _require_string_list(gate.get("checks", []), f"{gate['id']}.checks"):
            raise StructuredContractError(f"{gate['id']}: checks are required")
    for task in tasks.values():
        _require_keys(
            task,
            ("wave", "status", "depends_on", "deliverables", "requirement_ids", "gate_ids"),
            task["id"],
        )
        if task["status"] not in TASK_STATUSES:
            raise StructuredContractError(f"{task['id']}: invalid task status")
        refs(task, "depends_on", tasks)
        refs(task, "requirement_ids", requirements)
        refs(task, "gate_ids", gates)
        _require_string_list(task["deliverables"], f"{task['id']}.deliverables")
    for runbook in runbooks.values():
        _require_keys(
            runbook,
            ("audience", "preconditions", "steps", "failure_policy", "gate_ids", "requirement_ids"),
            runbook["id"],
        )
        for field in ("preconditions", "steps", "failure_policy"):
            _require_string_list(runbook[field], f"{runbook['id']}.{field}")
        refs(runbook, "gate_ids", gates)
        refs(runbook, "requirement_ids", requirements)
    for release in indexes["releases"].values():
        _require_keys(
            release,
            (
                "state",
                "required_task_ids",
                "required_gate_ids",
                "required_runbook_ids",
                "required_requirement_ids",
                "audit_report_path",
                "readiness_rule",
            ),
            release["id"],
        )
        if release["state"] not in RELEASE_STATES:
            raise StructuredContractError(f"{release['id']}: invalid release state")
        refs(release, "required_task_ids", tasks)
        refs(release, "required_gate_ids", gates)
        refs(release, "required_runbook_ids", runbooks)
        refs(release, "required_requirement_ids", requirements)
        audit_path = Path(release["audit_report_path"])
        if audit_path.is_absolute() or ".." in audit_path.parts:
            raise StructuredContractError(f"{release['id']}: audit_report_path must be relative")
        if release["state"] in {"ready", "released"}:
            unfinished = [
                task_id
                for task_id in release["required_task_ids"]
                if tasks[task_id]["status"] != "completed"
            ]
            if unfinished:
                raise StructuredContractError(
                    f"{release['id']}: ready release has unfinished tasks {', '.join(unfinished)}"
                )


def _validate_task_graph(tasks: Mapping[str, Mapping[str, Any]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise StructuredContractError(f"tasks: dependency cycle at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in tasks[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in tasks:
        visit(task_id)


def validate_documents(documents: Mapping[str, Mapping[str, Any]]) -> None:
    if set(documents) != set(CONTRACT_PATHS):
        raise StructuredContractError("contract kinds do not match the required manifest")
    _validate_no_ephemeral_facts(documents)
    indexes = _index_items(documents)
    _validate_references(indexes)
    _validate_task_graph(indexes["tasks"])


def _header(title: str) -> list[str]:
    return [
        f"# {title}",
        "",
        "> 由 `contracts/*.yaml` 确定性生成。请修改结构化 Contract，不要直接编辑本文件。",
        "",
    ]


def _table_row(values: Iterable[Any]) -> str:
    return "| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values) + " |"


def render_documents(documents: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    validate_documents(documents)
    requirements = documents["requirements"]["items"]
    decisions = documents["decisions"]["items"]
    tasks = documents["tasks"]["items"]
    gates = documents["gates"]["items"]
    runbooks = documents["runbooks"]["items"]
    releases = documents["releases"]["items"]

    acceptance = _header("重构验收参考")
    acceptance += [
        _table_row(("Requirement", "Category", "Statement", "Verification", "Gates")),
        _table_row(("---", "---", "---", "---", "---")),
    ]
    for item in requirements:
        acceptance.append(
            _table_row((item["id"], item["category"], item["statement"], item["verification"], ", ".join(item["gate_ids"])))
        )
    acceptance.append("")

    decision_by_requirement: dict[str, list[str]] = defaultdict(list)
    task_by_requirement: dict[str, list[str]] = defaultdict(list)
    for item in decisions:
        for requirement_id in item["requirement_ids"]:
            decision_by_requirement[requirement_id].append(item["id"])
    for item in tasks:
        for requirement_id in item["requirement_ids"]:
            task_by_requirement[requirement_id].append(item["id"])
    traceability = _header("重构 Requirement Traceability")
    traceability += [
        _table_row(("Requirement", "Decisions", "Tasks", "Gates")),
        _table_row(("---", "---", "---", "---")),
    ]
    for item in requirements:
        traceability.append(
            _table_row(
                (
                    item["id"],
                    ", ".join(decision_by_requirement[item["id"]]) or "None",
                    ", ".join(task_by_requirement[item["id"]]) or "None",
                    ", ".join(item["gate_ids"]),
                )
            )
        )
    traceability.append("")

    execution = _header("重构执行参考")
    execution += [
        _table_row(("Task", "Wave", "Status", "Dependencies", "Deliverables", "Gates")),
        _table_row(("---", "---", "---", "---", "---", "---")),
    ]
    for item in tasks:
        execution.append(
            _table_row(
                (
                    item["id"],
                    item["wave"],
                    item["status"],
                    ", ".join(item["depends_on"]) or "None",
                    "; ".join(item["deliverables"]),
                    ", ".join(item["gate_ids"]),
                )
            )
        )
    execution.append("")

    decision_lines = _header("重构架构决策参考")
    for item in decisions:
        decision_lines += [
            f"## {item['id']}：{item['title']}",
            "",
            f"- 状态：`{item['status']}`",
            f"- 关联 Requirement：{', '.join(item['requirement_ids'])}",
            f"- 理由：{item['rationale']}",
            "- 结果：",
            "",
        ]
        decision_lines.extend(f"  - {consequence}" for consequence in item["consequences"])
        decision_lines.append("")

    runbook_lines = _header("重构运行手册参考")
    for item in runbooks:
        runbook_lines += [
            f"## {item['id']}：{item['title']}",
            "",
            f"- 受众：`{item['audience']}`",
            f"- Gates：{', '.join(item['gate_ids'])}",
            "- 前置条件：",
            "",
        ]
        runbook_lines.extend(f"  - {entry}" for entry in item["preconditions"])
        runbook_lines += ["", "- 步骤：", ""]
        runbook_lines.extend(f"  {index}. {entry}" for index, entry in enumerate(item["steps"], 1))
        runbook_lines += ["", "- 失败策略：", ""]
        runbook_lines.extend(f"  - {entry}" for entry in item["failure_policy"])
        runbook_lines.append("")

    release_lines = _header("重构 Release Contract")
    for item in releases:
        release_lines += [
            f"## {item['id']}：{item['title']}",
            "",
            f"- 当前状态：`{item['state']}`",
            f"- 审查报告：`{item['audit_report_path']}`",
            f"- Readiness Rule：{item['readiness_rule']}",
            f"- Required Tasks：{', '.join(item['required_task_ids'])}",
            f"- Required Gates：{', '.join(item['required_gate_ids'])}",
            f"- Required Runbooks：{', '.join(item['required_runbook_ids'])}",
            f"- Required Requirements：{', '.join(item['required_requirement_ids'])}",
            "",
        ]

    gate_lines = _header("重构 Gate 参考")
    gate_lines += [
        _table_row(("Gate", "Mode", "Evidence")),
        _table_row(("---", "---", "---")),
    ]
    for item in gates:
        gate_lines.append(_table_row((item["id"], item["mode"], item["proves"])))
    gate_lines.append("")

    return {
        "acceptance": "\n".join(acceptance),
        "traceability": "\n".join(traceability),
        "execution": "\n".join(execution),
        "decisions": "\n".join(decision_lines),
        "runbooks": "\n".join(runbook_lines),
        "release": "\n".join(release_lines + gate_lines[4:]),
    }


def check_generated(rendered: Mapping[str, str]) -> None:
    mismatches: list[str] = []
    for name, expected in rendered.items():
        path = GENERATED_PATHS[name]
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            mismatches.append(str(path.relative_to(ROOT)))
    if mismatches:
        raise StructuredContractError(
            "generated documentation is stale: " + ", ".join(mismatches)
        )


def write_generated(rendered: Mapping[str, str]) -> None:
    for name, text in rendered.items():
        path = GENERATED_PATHS[name]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and render structured refactoring contracts")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate contracts and generated documents")
    group.add_argument("--write", action="store_true", help="write generated documents")
    group.add_argument("--render-json", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        documents = load_documents()
        validate_documents(documents)
        rendered = render_documents(documents)
        if args.check:
            check_generated(rendered)
        elif args.write:
            write_generated(rendered)
        else:
            print(json.dumps(rendered, ensure_ascii=False))
    except StructuredContractError as error:
        parser.exit(1, f"structured_contracts: FAIL: {error}\n")
    if not args.render_json:
        print("structured_contracts: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
