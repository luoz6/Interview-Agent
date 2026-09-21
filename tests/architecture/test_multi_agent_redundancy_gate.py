from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCANNER_DIR = ROOT / "tools" / "architecture"
sys.path.insert(0, str(SCANNER_DIR))

import scan_multi_agent_redundancy as scanner  # noqa: E402


EXPECTED_CATEGORIES = {
    "scheduler",
    "workflow",
    "execution_state",
    "invocation_port",
    "agent_registry",
    "memory_subsystem",
    "retry_system",
    "task_lifecycle",
}


def _write(app_root: Path, relative: str, source: str) -> None:
    path = app_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_multi_agent_redundancy_gate_has_eight_zero_categories() -> None:
    result = scanner.scan()

    assert result["parse_errors"] == []
    assert set(result["categories"]) == EXPECTED_CATEGORIES
    assert all(
        detail["candidates"] for detail in result["categories"].values()
    )
    assert {
        category: detail["violations"]
        for category, detail in result["categories"].items()
    } == {category: [] for category in EXPECTED_CATEGORIES}


def test_redundancy_gate_detects_a_second_owner_in_every_category(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    _write(
        app_root,
        "application/scheduling/scheduler.py",
        "class SchedulerApplicationCapability:\n"
        "    def step(self): pass\n"
        "    def run_once(self): pass\n",
    )
    _write(
        app_root,
        "application/interview/scheduler_production_entry.py",
        "class SchedulerProductionEntry: pass\n",
    )
    _write(
        app_root,
        "domain/interview/scheduling/state.py",
        "class TaskRuntimeState:\n"
        "    task_id: str\n    status: str\n    attempt: int\n    max_attempts: int\n"
        "class ExecutionState:\n"
        "    execution_id: str\n    revision: int\n    task_states: tuple\n    execution_status: str\n"
        "    def transition_task(self): pass\n"
        "def transition_task_state(current, target_status): pass\n",
    )
    _write(
        app_root,
        "domain/interview/scheduling/decisions.py",
        "def validate_scheduling_decision(context, decision): pass\n",
    )
    _write(
        app_root,
        "ports/agent_invocation.py",
        "from typing import Protocol\n"
        "class AgentInvocationPort(Protocol):\n    def invoke(self): ...\n",
    )
    _write(
        app_root,
        "a2a/registry.py",
        "class AgentRegistry:\n"
        "    def register_capability(self): pass\n"
        "    def list_capabilities(self): pass\n"
        "    def resolve(self): pass\n",
    )
    _write(
        app_root,
        "adapters/memory/agent_memory.py",
        "class InMemoryAgentMemoryStore:\n"
        "    def bind(self): pass\n    def _recall(self): pass\n"
        "    def _remember(self): pass\n    def _delete_scope(self): pass\n",
    )

    _write(
        app_root,
        "application/scheduling/duplicate.py",
        "class AlternateScheduler:\n"
        "    def step(self): pass\n    def run_once(self): pass\n"
        "class SchedulerRetryPolicy:\n    pass\n",
    )
    _write(
        app_root,
        "graphs/duplicate_workflow.py",
        "class DuplicateWorkflow:\n"
        "    def start(self): pass\n"
        "    def submit_command(self): pass\n"
        "    def resume_command(self): pass\n",
    )
    _write(
        app_root,
        "runtime/duplicate_state.py",
        "class ShadowExecutionState:\n"
        "    execution_id: str\n    revision: int\n"
        "    task_states: tuple\n    execution_status: str\n",
    )
    _write(
        app_root,
        "ports/duplicate_invocation.py",
        "from typing import Protocol\n"
        "class AlternateInvocationPort(Protocol):\n    def invoke(self): ...\n",
    )
    _write(
        app_root,
        "runtime/duplicate_registry.py",
        "class AlternateAgentRegistry:\n"
        "    def register(self): pass\n"
        "    def list_capabilities(self): pass\n"
        "    def resolve(self): pass\n",
    )
    _write(
        app_root,
        "adapters/memory/duplicate_agent_memory.py",
        "class SecondAgentMemoryStore:\n"
        "    def bind(self): pass\n    def recall(self): pass\n"
        "    def remember(self): pass\n    def delete_scope(self): pass\n",
    )
    _write(
        app_root,
        "runtime/duplicate_lifecycle.py",
        "class ShadowTaskState:\n"
        "    task_id: str\n    status: str\n"
        "    attempt: int\n    max_attempts: int\n"
        "def transition_task_state(current, target_status): pass\n",
    )

    result = scanner.scan(app_root)

    assert result["parse_errors"] == []
    reasons = {
        category: {item["reason"] for item in detail["violations"]}
        for category, detail in result["categories"].items()
    }
    assert "second_scheduler_engine" in reasons["scheduler"]
    assert "second_command_workflow" in reasons["workflow"]
    assert "second_runtime_truth" in reasons["execution_state"]
    assert "second_invocation_port" in reasons["invocation_port"]
    assert "second_agent_registry" in reasons["agent_registry"]
    assert "second_agent_memory_store" in reasons["memory_subsystem"]
    assert "standalone_scheduler_retry_owner" in reasons["retry_system"]
    assert {
        "second_task_lifecycle_model",
        "second_task_transition_owner",
    } <= reasons["task_lifecycle"]
