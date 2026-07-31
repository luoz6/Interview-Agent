from __future__ import annotations

import json
from pathlib import Path

from app.services.memory_config import load_effective_memory_config
from scripts.memory_budget_shadow_observe import (
    SCENARIOS,
    build_profile_b_cases,
    run_profile_b,
    shadow_environment,
    validate_observation_artifact,
    validate_single_shadow_axis,
)


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "memory-budget-shadow-runbook.md"


class CompleteMetricStore:
    store_kind = "postgres_aggregate"

    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def diagnostics(self):
        return {
            "store_kind": self.store_kind,
            "data_complete": True,
            "latest_bucket_at": "aggregate-only",
        }

    def aggregate(self, *, window_minutes):
        assert window_minutes == 1440
        return {
            "store_kind": self.store_kind,
            "data_complete": True,
            "items": [{"event_count": len(self.events)}],
        }


def test_profile_b_matrix_has_300_balanced_sessions_and_required_scenarios():
    cases = build_profile_b_cases()

    assert len(cases) == 300
    assert {case.language_bucket for case in cases} == {"zh_hans", "en", "mixed"}
    assert {case.scenario for case in cases} == set(SCENARIOS)
    for language in ("zh_hans", "en", "mixed"):
        assert sum(case.language_bucket == language for case in cases) == 100


def test_single_axis_configuration_rejects_enforcement_or_memory_consumption():
    ready = load_effective_memory_config(shadow_environment())
    enforced = load_effective_memory_config(
        {
            **shadow_environment(),
            "MEMORY_BUDGET_MODE": "enforce",
            "MEMORY_BUDGET_ENFORCEMENT_INTERVIEW": "true",
        }
    )

    assert validate_single_shadow_axis(ready) == []
    assert "BUDGET_SHADOW_NOT_ENABLED" in validate_single_shadow_axis(enforced)
    assert "BUDGET_ENFORCEMENT_MUST_REMAIN_DISABLED" in validate_single_shadow_axis(
        enforced
    )


def test_profile_b_observation_is_aggregate_only_and_never_changes_provider_input():
    store = CompleteMetricStore()

    record = run_profile_b(
        metric_store=store,
        validated_rc_revision="a982b1f",
        staging_preflight_revision="5280c9d",
    )
    validate_observation_artifact(record)

    assert record["session_count"] == 300
    assert record["language_sample_counts"] == {
        "en": 100,
        "mixed": 100,
        "zh_hans": 100,
    }
    assert record["scenario_counts"] == {name: 30 for name in sorted(SCENARIOS)}
    assert record["mandatory_current_content_losses"] == 0
    assert record["provider_calls"] == 0
    assert record["provider_input_change_count"] == 0
    assert record["data_complete"] is True
    assert record["followup_p95_latency_ms"] < 600
    assert record["baseline_p95_latency_ms"] == 500.0
    assert record["budget_mode_during_observation"] == "shadow"
    assert record["budget_mode_after_observation"] == "disabled"
    assert record["budget_enforcement"] == "disabled"
    assert record["principal_memory"] == "disabled"
    assert len(store.events) == 300

    rendered = json.dumps(record, sort_keys=True).casefold()
    for forbidden in (
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "prompt",
        "answer",
        "postgresql://",
    ):
        assert forbidden not in rendered


def test_artifact_audit_rejects_subject_or_prompt_fields():
    for unsafe in (
        {"session_id": "private"},
        {"prompt": "private"},
        {"database_fingerprint": "private"},
    ):
        try:
            validate_observation_artifact(unsafe)
        except RuntimeError:
            pass
        else:
            raise AssertionError("unsafe observation was accepted")


def test_runbook_keeps_budget_shadow_hypothetical_and_single_axis():
    text = RUNBOOK.read_text(encoding="utf-8")

    for required in (
        "--staging-preflight-passed",
        "--principal-memory-disabled",
        "--operation-window-approved",
        "--sessions 300",
        "provider_calls=0",
        "provider_input_change_count=0",
        "budget_enforcement=disabled",
        "principal_memory=disabled",
        "BUDGET_SHADOW_OBSERVATION=RECORDED",
        "BUDGET_ENFORCEMENT=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ):
        assert required in text
    assert "PASS_FOR_PRODUCTION" not in text
    assert "postgresql://" not in text
