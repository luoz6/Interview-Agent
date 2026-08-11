from __future__ import annotations

from importlib.metadata import version
import os

import pytest

from app.runtime.config.compatibility import (
    get_interview_langgraph_rollout_percent,
    get_interview_langgraph_runtime_enabled,
    get_interview_langgraph_version,
)
from app.services.langgraph_runtime import (
    PostgresCheckpointerRuntime,
    VersionedInterviewGraphRegistry,
)


class FakeSaver:
    def __init__(self):
        self.setup_calls = 0
        self.deleted = []

    def setup(self):
        self.setup_calls += 1

    def delete_thread(self, thread_id):
        self.deleted.append(thread_id)


class FakeSaverContext:
    def __init__(self):
        self.saver = FakeSaver()
        self.exits = 0

    def __enter__(self):
        return self.saver

    def __exit__(self, exc_type, exc, traceback):
        self.exits += 1


def test_supported_langgraph_packages_are_installed():
    assert version("langgraph").startswith("1.2.")
    assert version("langgraph-checkpoint-postgres").startswith("3.1.")


def test_rollout_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", raising=False)
    monkeypatch.delenv("INTERVIEW_LANGGRAPH_RUNTIME_ENABLED", raising=False)
    monkeypatch.delenv("INTERVIEW_LANGGRAPH_VERSION", raising=False)

    assert get_interview_langgraph_rollout_percent() == 0
    assert get_interview_langgraph_runtime_enabled() is True
    assert get_interview_langgraph_version() == "langgraph-v1"


@pytest.mark.parametrize("value", ["-1", "101", "abc"])
def test_rollout_rejects_invalid_percentage(monkeypatch, value):
    monkeypatch.setenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", value)

    with pytest.raises(ValueError, match="between 0 and 100"):
        get_interview_langgraph_rollout_percent()


def test_interview_graph_v2_is_an_explicit_supported_version(monkeypatch):
    monkeypatch.setenv("INTERVIEW_LANGGRAPH_VERSION", "langgraph-v2")

    assert get_interview_langgraph_version() == "langgraph-v2"


def test_checkpointer_starts_once_and_closes():
    context = FakeSaverContext()
    runtime = PostgresCheckpointerRuntime(
        "postgresql://postgres:postgres@127.0.0.1:5432/interview",
        saver_factory=lambda dsn: context,
    )

    assert runtime.start() is context.saver
    assert runtime.start() is context.saver
    assert context.saver.setup_calls == 0

    runtime.shutdown()

    assert context.exits == 1


def test_strict_msgpack_is_enabled_before_saver_creation(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_STRICT_MSGPACK", raising=False)
    runtime = PostgresCheckpointerRuntime(
        "dsn",
        saver_factory=lambda dsn: FakeSaverContext(),
    )

    runtime.start()

    assert os.environ["LANGGRAPH_STRICT_MSGPACK"] == "true"
    assert runtime.saver.setup_calls == 0
    assert runtime.saver is not None


class FakePool:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.open_calls = []
        self.close_calls = []

    def open(self, *, wait, timeout):
        self.open_calls.append((wait, timeout))

    def close(self, *, timeout):
        self.close_calls.append(timeout)


class RetryableClosePool(FakePool):
    def close(self, *, timeout):
        self.close_calls.append(timeout)
        if len(self.close_calls) == 1:
            raise TimeoutError("pool drain timed out")


def test_checkpointer_constructs_explicit_pool_and_never_runs_setup(monkeypatch):
    pools = []

    def pool_factory(**kwargs):
        pool = FakePool(**kwargs)
        pools.append(pool)
        return pool

    saver_pool = []

    class Saver:
        def __init__(self, pool):
            saver_pool.append(pool)

    monkeypatch.setattr(
        "langgraph.checkpoint.postgres.PostgresSaver",
        Saver,
    )
    runtime = PostgresCheckpointerRuntime(
        "safe-dsn",
        min_size=1,
        max_size=3,
        acquire_timeout=0.25,
        shutdown_timeout=0.5,
        connect_timeout=3,
        max_lifetime=900,
        max_idle=120,
        pool_factory=pool_factory,
        schema_validator=lambda pool: None,
    )

    saver = runtime.start()

    assert isinstance(saver, Saver)
    assert pools[0].kwargs["open"] is False
    assert pools[0].kwargs["min_size"] == 1
    assert pools[0].kwargs["max_size"] == 3
    assert pools[0].kwargs["max_lifetime"] == 900
    assert pools[0].kwargs["max_idle"] == 120
    assert pools[0].kwargs["kwargs"]["connect_timeout"] == 3
    assert pools[0].open_calls == [(True, 0.25)]
    assert saver_pool == pools
    assert not hasattr(saver, "setup_calls")

    runtime.shutdown()
    assert pools[0].close_calls == [0.5]
    assert runtime.state == "closed"


def test_checkpointer_shutdown_can_retry_after_pool_close_failure(monkeypatch):
    pools = []

    def pool_factory(**kwargs):
        pool = RetryableClosePool(**kwargs)
        pools.append(pool)
        return pool

    monkeypatch.setattr(
        "langgraph.checkpoint.postgres.PostgresSaver",
        lambda pool: object(),
    )
    runtime = PostgresCheckpointerRuntime(
        "safe-dsn",
        pool_factory=pool_factory,
        schema_validator=lambda pool: None,
    )
    runtime.start()

    with pytest.raises(TimeoutError, match="drain"):
        runtime.shutdown()

    assert runtime.state == "closing"
    assert pools[0].close_calls == [5.0]

    runtime.shutdown()

    assert runtime.state == "closed"
    assert pools[0].close_calls == [5.0, 5.0]


def test_checkpointer_pool_and_legacy_factory_are_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        PostgresCheckpointerRuntime(
            "dsn",
            pool_factory=lambda **kwargs: object(),
            saver_factory=lambda dsn: FakeSaverContext(),
        )


def test_graph_registry_never_falls_back_across_versions():
    registry = VersionedInterviewGraphRegistry()
    graph = object()
    registry.register("langgraph-v1", graph)

    assert registry.get("langgraph-v1") is graph
    with pytest.raises(ValueError, match="unsupported graph version"):
        registry.get("langgraph-v2")
