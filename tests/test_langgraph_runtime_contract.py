from importlib.metadata import version

import pytest

from app.services.config import (
    get_interview_langgraph_rollout_percent,
    get_interview_langgraph_runtime_enabled,
    get_interview_langgraph_version,
)


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
