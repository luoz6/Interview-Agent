"""Unit tests for runtime start, shutdown, and reset ordering."""

import app.services.runtime as runtime
import pytest


class FakeService:
    def __init__(self):
        self.starts = 0
        self.shutdowns = []

    def start(self):
        self.starts += 1

    def shutdown(self, *, wait=True):
        self.shutdowns.append(wait)


def test_start_runtime_starts_postgres_local_dispatcher(monkeypatch):
    service = FakeService()
    checkpointer = FakeService()
    maintenance = FakeService()
    runtime.reset_runtime_for_tests()
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(
        runtime,
        "get_runtime_event_backend",
        lambda: "local",
    )
    monkeypatch.setattr(
        runtime,
        "build_runtime_outbox_service",
        lambda: service,
    )
    monkeypatch.setattr(
        runtime,
        "get_langgraph_checkpointer_runtime",
        lambda: checkpointer,
    )
    monkeypatch.setattr(
        runtime,
        "get_durable_workflow_maintenance_service",
        lambda: maintenance,
    )

    runtime.start_runtime()
    runtime.start_runtime()
    runtime.shutdown_runtime()

    assert service.starts == 1
    assert service.shutdowns == [True]
    assert checkpointer.starts == 1
    assert checkpointer.shutdowns == [True]
    assert maintenance.starts == 1
    assert maintenance.shutdowns == [True]


def test_start_runtime_does_not_start_memory_dispatcher(monkeypatch):
    runtime.reset_runtime_for_tests()
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "memory")
    monkeypatch.setattr(
        runtime,
        "get_runtime_event_backend",
        lambda: "local",
    )
    monkeypatch.setattr(
        runtime,
        "build_runtime_outbox_service",
        lambda: (_ for _ in ()).throw(
            AssertionError("memory must not build an outbox service")
        ),
    )

    runtime.start_runtime()
    runtime.shutdown_runtime()


def test_shutdown_order_is_explicit_and_attempts_every_resource():
    events = []

    class WaitResource:
        def __init__(self, name, *, fail=False):
            self.name = name
            self.fail = fail

        def shutdown(self, *, wait=True):
            events.append((self.name, wait))
            if self.fail:
                raise RuntimeError(self.name)

    class NoWaitResource:
        def __init__(self, name):
            self.name = name

        def shutdown(self):
            events.append((self.name, None))

    class CloseResource:
        def __init__(self, name):
            self.name = name

        def close(self):
            events.append((self.name, None))

    runtime.reset_runtime_for_tests()
    container = runtime.get_runtime_container()
    container.set("runtime_outbox_service", WaitResource("outbox"))
    container.set(
        "durable_workflow_maintenance_service",
        WaitResource("maintenance", fail=True),
    )
    container.set("report_job_store", WaitResource("report"))
    container.set(
        "langgraph_checkpointer_runtime",
        NoWaitResource("checkpointer"),
    )
    container.set("workflow_thread_lock", CloseResource("thread_lock"))
    container.set(
        "postgres_connection_domains",
        CloseResource("postgres"),
    )
    container.set("event_publisher", WaitResource("publisher"))

    with pytest.raises(ExceptionGroup, match="runtime resource shutdown failed"):
        runtime.shutdown_runtime(wait=False)

    assert events == [
        ("outbox", False),
        ("maintenance", False),
        ("report", False),
        ("checkpointer", None),
        ("thread_lock", None),
        ("postgres", None),
        ("publisher", False),
    ]
    assert container.state == "closed"


def test_shutdown_is_idempotent_and_reset_replaces_closed_container(monkeypatch):
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "memory")
    runtime.reset_runtime_for_tests()
    first = runtime.get_runtime_container()

    runtime.start_runtime()
    runtime.shutdown_runtime()
    runtime.shutdown_runtime()

    assert first.state == "closed"
    with pytest.raises(RuntimeError, match="closed RuntimeContainer"):
        runtime.start_runtime()

    runtime.reset_runtime_for_tests()
    second = runtime.get_runtime_container()
    assert second is not first
    assert second.state == "new"
