from importlib.metadata import version

import pytest

from app.services.config import (
    get_report_langgraph_max_parallel_question_reviews,
    get_report_langgraph_max_provider_attempts,
    get_report_langgraph_max_quality_repairs,
    get_report_langgraph_rollout_percent,
    get_report_langgraph_runtime_enabled,
    get_report_langgraph_version,
)
from app.services.langgraph_runtime import VersionedGraphRegistry
from app.services.report_jobs import choose_report_workflow_engine


def test_review_langgraph_packages_are_available():
    assert version("langgraph").startswith("1.2.")
    assert version("langgraph-checkpoint-postgres").startswith("3.1.")


def test_review_rollout_defaults_to_disabled(monkeypatch):
    for name in (
        "REPORT_LANGGRAPH_ROLLOUT_PERCENT",
        "REPORT_LANGGRAPH_RUNTIME_ENABLED",
        "REPORT_LANGGRAPH_VERSION",
        "REPORT_LANGGRAPH_MAX_PARALLEL_QUESTION_REVIEWS",
        "REPORT_LANGGRAPH_MAX_PROVIDER_ATTEMPTS",
        "REPORT_LANGGRAPH_MAX_QUALITY_REPAIRS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert get_report_langgraph_rollout_percent() == 0
    assert get_report_langgraph_runtime_enabled() is True
    assert get_report_langgraph_version() == "langgraph-review-v1"
    assert get_report_langgraph_max_parallel_question_reviews() == 3
    assert get_report_langgraph_max_provider_attempts() == 3
    assert get_report_langgraph_max_quality_repairs() == 2


@pytest.mark.parametrize("value", ["-1", "101", "invalid"])
def test_review_rollout_rejects_invalid_percentage(monkeypatch, value):
    monkeypatch.setenv("REPORT_LANGGRAPH_ROLLOUT_PERCENT", value)

    with pytest.raises(ValueError, match="between 0 and 100"):
        get_report_langgraph_rollout_percent()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("REPORT_LANGGRAPH_MAX_PARALLEL_QUESTION_REVIEWS", "0"),
        ("REPORT_LANGGRAPH_MAX_PROVIDER_ATTEMPTS", "0"),
        ("REPORT_LANGGRAPH_MAX_QUALITY_REPAIRS", "0"),
    ],
)
def test_review_bounds_must_be_positive(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="positive"):
        if name.endswith("QUESTION_REVIEWS"):
            get_report_langgraph_max_parallel_question_reviews()
        elif name.endswith("ATTEMPTS"):
            get_report_langgraph_max_provider_attempts()
        else:
            get_report_langgraph_max_quality_repairs()


def test_review_engine_assignment_is_stable():
    values = {
        choose_report_workflow_engine(
            "job-fixed",
            runtime_store="postgres",
            runtime_enabled=True,
            rollout_percent=25,
        )
        for _ in range(10)
    }

    assert len(values) == 1
    assert (
        choose_report_workflow_engine(
            "job-fixed",
            runtime_store="memory",
            runtime_enabled=True,
            rollout_percent=100,
        )
        == "legacy"
    )
    assert (
        choose_report_workflow_engine(
            "job-fixed",
            runtime_store="postgres",
            runtime_enabled=False,
            rollout_percent=100,
        )
        == "legacy"
    )


def test_generalized_registry_preserves_exact_versions():
    registry = VersionedGraphRegistry()
    interview_graph = object()
    review_graph = object()
    registry.register("langgraph-v1", interview_graph)
    registry.register("langgraph-review-v1", review_graph)

    assert registry.get("langgraph-v1") is interview_graph
    assert registry.get("langgraph-review-v1") is review_graph
    with pytest.raises(ValueError, match="unsupported graph version"):
        registry.get("langgraph-review-v2")
