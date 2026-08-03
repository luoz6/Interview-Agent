from pathlib import Path
import re


PLAN = Path(
    "docs/superpowers/plans/"
    "2026-08-03-memory-production-budget-shadow-execution-and-evidence.md"
)


def plan_text() -> str:
    return PLAN.read_text(encoding="utf-8")


def test_plan_has_a_complete_contiguous_task_sequence():
    tasks = [
        int(value)
        for value in re.findall(r"^## Task (\d+)：", plan_text(), re.MULTILINE)
    ]

    assert tasks == list(range(14))


def test_plan_pins_external_approval_and_single_axis_boundaries():
    text = plan_text()

    for role in (
        "change_owner",
        "operations",
        "privacy",
        "security",
        "fairness",
    ):
        assert role in text
    for contract in (
        "BUDGET_SHADOW_ONLY",
        "MEMORY_BUDGET_MODE=shadow",
        "MEMORY_BUDGET_SHADOW_ENABLED=true",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "EXTERNAL_APPROVAL_RECORD=ABSENT",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ):
        assert contract in text


def test_plan_requires_three_state_closure_and_a_new_window_for_more_data():
    text = plan_text()

    for status in (
        "PRODUCTION_BUDGET_SHADOW=PASS",
        "PRODUCTION_BUDGET_SHADOW=BLOCKED",
        "PRODUCTION_BUDGET_SHADOW=CONTINUE_OBSERVATION",
        "NEW_APPROVAL_WINDOW_REQUIRED=true",
        "CONFIGURATION_RESTORED=disabled",
    ):
        assert status in text


def test_plan_records_remote_clone_and_observation_thresholds():
    text = plan_text()

    for requirement in (
        "minimum_clone_depth=2",
        "0.1% warm-up",
        "follow-up sample count >= 200",
        "window duration >= 24 hours",
        "observed_error_rate - baseline_error_rate > 0.005",
        "observed_p95_latency_ms > baseline_p95_latency_ms * 1.20",
    ):
        assert requirement in text


def test_plan_does_not_invent_normative_requirement_ids():
    text = plan_text()

    assert not re.search(r"MEM-[A-Z]+-\d+", text)
    assert "不得创建未经 Spec 定义的新 `MEM-*` requirement ID" in text
