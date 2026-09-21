#!/usr/bin/env python3
"""Detect a second owner for each canonical multi-agent orchestration concern."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "app"

CANONICAL = {
    "scheduler": {
        "app.application.scheduling.scheduler.SchedulerApplicationCapability",
    },
    "workflow": {
        "app.application.interview.scheduler_production_entry.SchedulerProductionEntry",
    },
    "execution_state": {
        "app.domain.interview.scheduling.state.ExecutionState",
    },
    "invocation_port": {
        "app.ports.agent_invocation.AgentInvocationPort",
    },
    "agent_registry": {
        "app.a2a.registry.AgentRegistry",
    },
    "memory_subsystem": {
        "app.adapters.memory.agent_memory.InMemoryAgentMemoryStore",
    },
    "retry_system": {
        "app.domain.interview.scheduling.decisions.validate_scheduling_decision",
        "app.domain.interview.scheduling.state.transition_task_state",
    },
    "task_lifecycle": {
        "app.domain.interview.scheduling.state.ExecutionState.transition_task",
        "app.domain.interview.scheduling.state.TaskRuntimeState",
        "app.domain.interview.scheduling.state.transition_task_state",
    },
}

_SCHEDULER_SCOPE = (
    "app.application.scheduling",
    "app.application.interview.scheduler_production_entry",
    "app.domain.interview.scheduling",
    "app.graphs.scheduler_graph",
    "app.runtime.scheduler_composition",
)


def _module_name(app_root: Path, path: Path) -> str:
    relative = path.relative_to(app_root).with_suffix("")
    parts = relative.parts[:-1] if path.name == "__init__.py" else relative.parts
    return ".".join(("app", *parts))


def _base_names(node: ast.ClassDef) -> set[str]:
    return {ast.unparse(base).rsplit(".", 1)[-1] for base in node.bases}


def _class_fields(node: ast.ClassDef) -> set[str]:
    fields: set[str] = set()
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            fields.add(item.target.id)
        elif isinstance(item, ast.Assign):
            fields.update(
                target.id for target in item.targets if isinstance(target, ast.Name)
            )
    return fields


def _method_names(node: ast.ClassDef) -> set[str]:
    return {
        item.name
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _function_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {
        argument.arg
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    }


def _record(
    *, module: str, path: Path, app_root: Path, qualname: str, node: ast.AST
) -> dict[str, Any]:
    return {
        "symbol": f"{module}.{qualname}",
        "module": module,
        "file": path.relative_to(app_root.parent).as_posix(),
        "line": getattr(node, "lineno", 1),
    }


def _violation(category: str, reason: str, record: dict[str, Any]) -> dict[str, Any]:
    return {"category": category, "reason": reason, **record}


def _in_scope(module: str, prefixes: Iterable[str]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def scan(app_root: Path = APP_ROOT) -> dict[str, Any]:
    app_root = Path(app_root)
    files = sorted(app_root.rglob("*.py"))
    parse_errors: list[dict[str, str]] = []
    classes: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []

    for path in files:
        module = _module_name(app_root, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            parse_errors.append(
                {
                    "file": path.relative_to(app_root.parent).as_posix(),
                    "error": type(exc).__name__,
                }
            )
            continue

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_record = _record(
                    module=module,
                    path=path,
                    app_root=app_root,
                    qualname=node.name,
                    node=node,
                )
                class_record.update(
                    {
                        "name": node.name,
                        "bases": _base_names(node),
                        "fields": _class_fields(node),
                        "methods": _method_names(node),
                    }
                )
                classes.append(class_record)
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_record = _record(
                            module=module,
                            path=path,
                            app_root=app_root,
                            qualname=f"{node.name}.{item.name}",
                            node=item,
                        )
                        method_record.update(
                            {
                                "name": item.name,
                                "args": _function_args(item),
                                "parent": node.name,
                            }
                        )
                        functions.append(method_record)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_record = _record(
                    module=module,
                    path=path,
                    app_root=app_root,
                    qualname=node.name,
                    node=node,
                )
                function_record.update(
                    {
                        "name": node.name,
                        "args": _function_args(node),
                        "parent": None,
                    }
                )
                functions.append(function_record)

    categories: dict[str, dict[str, Any]] = {
        category: {
            "canonical": sorted(symbols),
            "candidates": [],
            "violations": [],
        }
        for category, symbols in CANONICAL.items()
    }

    def add_candidate(category: str, record: dict[str, Any]) -> None:
        summary = {
            key: record[key] for key in ("symbol", "module", "file", "line")
        }
        categories[category]["candidates"].append(summary)

    # A Scheduler engine owns both public one-step entry points. Composition,
    # production-entry, and graph classes do not satisfy this implementation shape.
    for item in classes:
        if {"step", "run_once"}.issubset(item["methods"]):
            add_candidate("scheduler", item)
            if item["symbol"] not in CANONICAL["scheduler"]:
                categories["scheduler"]["violations"].append(
                    _violation("scheduler", "second_scheduler_engine", item)
                )

    # A broad command-owning workflow is the retired duplication shape. The
    # remaining InterviewWorkflowService is an explicit OLD-execution drain.
    for item in classes:
        command_methods = {"start", "submit_command", "resume_command"}
        is_command_workflow = len(command_methods & item["methods"]) >= 2
        is_retired_orchestrator = item["name"] in {
            "OrchestratorAgent",
            "OrchestratorGraph",
        }
        is_production_entry = item["symbol"] in CANONICAL["workflow"]
        if is_command_workflow or is_retired_orchestrator or is_production_entry:
            add_candidate("workflow", item)
            allowed_drain = (
                item["symbol"] == "app.runtime.interview_workflow.InterviewWorkflowService"
            )
            if not allowed_drain and not is_production_entry:
                categories["workflow"]["violations"].append(
                    _violation("workflow", "second_command_workflow", item)
                )

    state_fields = {"execution_id", "revision", "task_states", "execution_status"}
    for item in classes:
        if state_fields.issubset(item["fields"]):
            add_candidate("execution_state", item)
            if item["symbol"] not in CANONICAL["execution_state"]:
                categories["execution_state"]["violations"].append(
                    _violation("execution_state", "second_runtime_truth", item)
                )

    for item in classes:
        is_invocation_protocol = (
            "Protocol" in item["bases"]
            and "invoke" in item["methods"]
            and ("Invocation" in item["name"] or item["module"].startswith("app.ports"))
        )
        if is_invocation_protocol:
            add_candidate("invocation_port", item)
            if item["symbol"] not in CANONICAL["invocation_port"]:
                categories["invocation_port"]["violations"].append(
                    _violation("invocation_port", "second_invocation_port", item)
                )

    for item in classes:
        registry_methods = item["methods"]
        is_agent_registry = (
            {"list_capabilities", "resolve"}.issubset(registry_methods)
            and bool({"register", "register_card", "register_capability"} & registry_methods)
        )
        if is_agent_registry:
            add_candidate("agent_registry", item)
            if item["symbol"] not in CANONICAL["agent_registry"]:
                categories["agent_registry"]["violations"].append(
                    _violation("agent_registry", "second_agent_registry", item)
                )

    for item in classes:
        normalized_methods = {name.lstrip("_") for name in item["methods"]}
        is_physical_agent_memory = (
            "Memory" in item["name"]
            and "bind" in normalized_methods
            and {"recall", "remember", "delete_scope"}.issubset(normalized_methods)
        )
        if is_physical_agent_memory:
            add_candidate("memory_subsystem", item)
            if item["symbol"] not in CANONICAL["memory_subsystem"]:
                categories["memory_subsystem"]["violations"].append(
                    _violation("memory_subsystem", "second_agent_memory_store", item)
                )

    # Scheduler retry is intentionally split between decision validation and
    # the canonical task transition. A standalone Retry class/function inside
    # the Scheduler slice would create another retry authority.
    for item in classes:
        if _in_scope(item["module"], _SCHEDULER_SCOPE) and "retry" in item["name"].casefold():
            add_candidate("retry_system", item)
            categories["retry_system"]["violations"].append(
                _violation("retry_system", "standalone_scheduler_retry_owner", item)
            )
    for item in functions:
        if item["symbol"] in CANONICAL["retry_system"]:
            add_candidate("retry_system", item)
        elif (
            item["parent"] is None
            and _in_scope(item["module"], _SCHEDULER_SCOPE)
            and "retry" in item["name"].casefold()
        ):
            add_candidate("retry_system", item)
            categories["retry_system"]["violations"].append(
                _violation("retry_system", "standalone_scheduler_retry_owner", item)
            )

    lifecycle_fields = {"task_id", "status", "attempt", "max_attempts"}
    for item in classes:
        if lifecycle_fields.issubset(item["fields"]):
            add_candidate("task_lifecycle", item)
            if item["symbol"] not in CANONICAL["task_lifecycle"]:
                categories["task_lifecycle"]["violations"].append(
                    _violation("task_lifecycle", "second_task_lifecycle_model", item)
                )
    for item in functions:
        if item["name"] in {"transition_task", "transition_task_state"}:
            add_candidate("task_lifecycle", item)
            if item["symbol"] not in CANONICAL["task_lifecycle"]:
                categories["task_lifecycle"]["violations"].append(
                    _violation("task_lifecycle", "second_task_transition_owner", item)
                )

    all_symbols = {item["symbol"] for item in (*classes, *functions)}
    for category, expected in CANONICAL.items():
        for missing in sorted(expected - all_symbols):
            categories[category]["violations"].append(
                {
                    "category": category,
                    "reason": "canonical_owner_missing",
                    "symbol": missing,
                    "module": missing.rsplit(".", 1)[0],
                    "file": "",
                    "line": 0,
                }
            )
        categories[category]["candidates"].sort(key=lambda item: item["symbol"])
        categories[category]["violations"].sort(
            key=lambda item: (item["reason"], item["symbol"])
        )

    return {
        "metadata": {
            "files_scanned": len(files),
            "parse_error_count": len(parse_errors),
            "rule": "one canonical responsibility owner; explicit adapters and distinct workflows are allowed",
        },
        "parse_errors": parse_errors,
        "categories": categories,
    }


def main() -> int:
    result = scan()
    print(f"files_scanned={result['metadata']['files_scanned']}")
    print(f"parse_error_count={result['metadata']['parse_error_count']}")
    for category, detail in result["categories"].items():
        print(
            f"{category}: candidates={len(detail['candidates'])} "
            f"violations={len(detail['violations'])}"
        )
        for violation in detail["violations"]:
            print(
                f"  {violation['reason']}: {violation['symbol']} "
                f"({violation['file']}:{violation['line']})"
            )
    return int(bool(result["parse_errors"] or any(
        detail["violations"] for detail in result["categories"].values()
    )))


if __name__ == "__main__":
    raise SystemExit(main())
