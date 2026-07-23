from importlib.metadata import version
import os

import pytest

from app.services.config import (
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


def test_checkpointer_starts_once_and_closes():
    context = FakeSaverContext()
    runtime = PostgresCheckpointerRuntime(
        "postgresql://postgres:postgres@127.0.0.1:5432/interview",
        saver_factory=lambda dsn: context,
    )

    assert runtime.start() is context.saver
    assert runtime.start() is context.saver
    assert context.saver.setup_calls == 1

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
    assert runtime.saver.setup_calls == 1
    assert runtime.saver is not None


def test_graph_registry_never_falls_back_across_versions():
    registry = VersionedInterviewGraphRegistry()
    graph = object()
    registry.register("langgraph-v1", graph)

    assert registry.get("langgraph-v1") is graph
    with pytest.raises(ValueError, match="unsupported graph version"):
        registry.get("langgraph-v2")
