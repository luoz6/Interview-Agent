from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.runtime.composition as runtime
from app.adapters.memory.execution_path_binding import (
    InMemoryExecutionPathBindingStore,
)
from app.application.interview.interview_start import InterviewStartService
from app.application.interview.orchestration_cutover import ExecutionPathRouter
from app.domain.interview.orchestration_cutover import (
    ExecutionPathConflict,
    ExecutionPathUnbound,
)
from app.ports import ExecutionPathBindingPort
from app.runtime.interview_workflow import InterviewWorkflowService


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"
NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def setup_function():
    runtime.reset_runtime_for_tests()


def teardown_function():
    runtime.reset_runtime_for_tests()


def _store():
    return InMemoryExecutionPathBindingStore(clock=lambda: NOW)


@pytest.mark.parametrize("path", ["OLD", "NEW"])
def test_first_claim_binds_exactly_one_path(path):
    store = _store()

    binding = store.bind("execution-1", path)

    assert isinstance(store, ExecutionPathBindingPort)
    assert binding.execution_id == "execution-1"
    assert binding.path == path
    assert binding.bound_at == NOW
    assert binding.schema_version == "execution-path-binding-v1"


@pytest.mark.parametrize("path", ["OLD", "NEW"])
def test_same_path_replay_is_idempotent(path):
    store = _store()

    first = store.bind("execution-1", path)
    second = store.bind("execution-1", path)

    assert second is first
    assert second.bound_at == first.bound_at


@pytest.mark.parametrize(
    ("first_path", "second_path"),
    [("OLD", "NEW"), ("NEW", "OLD")],
)
def test_path_change_fails_closed(first_path, second_path):
    store = _store()
    store.bind("execution-1", first_path)

    with pytest.raises(ExecutionPathConflict) as error:
        store.bind("execution-1", second_path)

    assert error.value.existing_path == first_path
    assert error.value.requested_path == second_path
    assert store.get("execution-1").path == first_path


def test_unbound_execution_cannot_enter_either_path():
    router = ExecutionPathRouter(_store())

    with pytest.raises(ExecutionPathUnbound):
        router.require_execution_path("unbound", "OLD")
    with pytest.raises(ExecutionPathUnbound):
        router.require_execution_path("unbound", "NEW")


def test_binding_port_has_no_delete_or_rebind_escape_hatch():
    store = _store()
    store.bind("deleted-session", "OLD")

    assert not hasattr(store, "delete_execution")
    assert not hasattr(ExecutionPathBindingPort, "delete_execution")
    assert store.get("deleted-session").path == "OLD"


def test_competing_paths_can_only_claim_one_execution():
    store = _store()

    def claim(path):
        try:
            return store.bind("contended", path).path
        except ExecutionPathConflict:
            return "CONFLICT"

    paths = ("OLD", "NEW") * 32
    with ThreadPoolExecutor(max_workers=16) as executor:
        results = tuple(executor.map(claim, paths))

    winner = store.get("contended").path
    assert set(results) <= {winner, "CONFLICT"}
    assert winner in results
    assert "CONFLICT" in results


def test_runtime_composition_owns_one_binding_store(monkeypatch):
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "memory")

    first = runtime.get_execution_path_binding_store()
    second = runtime.get_execution_path_binding_store()
    router = runtime.get_execution_path_router()

    assert first is second
    assert router.binding_store is first
    assert runtime.get_runtime_container().require(
        "execution_path_binding_store"
    ) is first


def test_existing_old_execution_remains_drainable_and_cannot_move_to_new():
    store = _store()
    router = ExecutionPathRouter(store)

    class LegacyStore:
        def start(self, _plan, **kwargs):
            return kwargs["session_id"]

    service = InterviewWorkflowService(
        legacy_store=LegacyStore(),
        workflow_store=object(),
        generation_store=object(),
        graph_registry=object(),
        runtime_store="memory",
        runtime_enabled=False,
        rollout_percent=0,
        default_graph_version="langgraph-v1",
        execution_path_router=router,
    )

    assert service.start(
        object(),
        job_description="job",
        resume_text="resume",
        job_tags=[],
        session_id="legacy-execution",
    ) == "legacy-execution"
    assert store.get("legacy-execution").path == "OLD"
    with pytest.raises(ExecutionPathConflict):
        router.claim_execution("legacy-execution", "NEW")

    store.bind("scheduler-owned", "NEW")
    with pytest.raises(ExecutionPathConflict):
        service.start(
            object(),
            job_description="job",
            resume_text="resume",
            job_tags=[],
            session_id="scheduler-owned",
        )


def test_new_start_service_requires_scheduler_and_has_no_old_fallback():
    parameters = inspect.signature(InterviewStartService).parameters
    source = (
        APP / "application" / "interview" / "interview_start.py"
    ).read_text(encoding="utf-8")

    assert parameters["scheduler_entry_factory"].default is inspect.Parameter.empty
    assert "workflow_service_factory" not in parameters
    assert "runtime_store_factory" not in parameters
    assert "rollout_percent_factory" not in parameters
    assert "return self.store.start(" not in source
    assert "self.workflow_service_factory().start(" not in source
    assert "return self.scheduler_entry_factory().start(" in source


def test_temporary_path_selector_is_absent_from_production():
    production_source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(APP.rglob("*.py"))
    )
    environment_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "TemporaryCutoverSwitch" not in production_source
    assert "get_interview_orchestration_path" not in production_source
    assert "bind_new_execution" not in production_source
    assert "INTERVIEW_ORCHESTRATION_PATH" not in production_source
    assert "INTERVIEW_ORCHESTRATION_PATH" not in environment_example


def test_postgres_binding_is_runtime_owned_and_database_serialized():
    adapter = (
        APP
        / "adapters"
        / "persistence"
        / "postgres"
        / "execution_path_binding.py"
    ).read_text(encoding="utf-8")
    schema = (
        APP / "adapters" / "postgres" / "store_schema_adapter.py"
    ).read_text(encoding="utf-8")

    assert "ON CONFLICT (execution_id) DO NOTHING" in adapter
    assert "PRIMARY KEY" in schema
    assert "orchestration_path IN ('OLD', 'NEW')" in schema
    assert "execution-path-binding-v1" in schema
    assert "SELECT session_id, 'OLD'" in schema
