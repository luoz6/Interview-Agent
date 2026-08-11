from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetShadowPreflight:
    target_environment: str
    observation_hours: int
    durable_metrics_available: bool
    postgres_validation_passed: bool
    knowledge_p1_ready: bool
    long_context_gate_passed: bool
    python_baseline_passed: bool
    browser_baseline_passed: bool
    staging_preflight_passed: bool
    principal_memory_disabled: bool
    operation_window_approved: bool
    stop_owner_role: str
    question_memory_consumption_enabled: bool = False
    long_term_consumption_available: bool = False
    budget_shadow_currently_enabled: bool = False


def evaluate_preflight(value: BudgetShadowPreflight) -> dict:
    failures = []
    if not value.target_environment.strip():
        failures.append("target_environment_missing")
    if not 1 <= value.observation_hours <= 168:
        failures.append("observation_window_invalid")
    checks = {
        "durable_metrics_unavailable": value.durable_metrics_available,
        "postgres_validation_not_passed": value.postgres_validation_passed,
        "knowledge_p1_not_ready": value.knowledge_p1_ready,
        "long_context_gate_not_passed": value.long_context_gate_passed,
        "python_baseline_not_passed": value.python_baseline_passed,
        "browser_baseline_not_passed": value.browser_baseline_passed,
        "staging_preflight_not_passed": value.staging_preflight_passed,
        "principal_memory_must_remain_disabled": value.principal_memory_disabled,
        "operation_window_not_approved": value.operation_window_approved,
    }
    failures.extend(code for code, passed in checks.items() if not passed)
    if not value.stop_owner_role.strip():
        failures.append("stop_owner_role_missing")
    if value.question_memory_consumption_enabled:
        failures.append("question_memory_consumption_must_remain_disabled")
    if value.long_term_consumption_available:
        failures.append("long_term_consumption_must_not_exist")
    if value.budget_shadow_currently_enabled:
        failures.append("validate_only_requires_shadow_disabled")
    config_payload = {
        "target_environment": value.target_environment.strip(),
        "observation_hours": value.observation_hours,
        "question_memory_consumption_enabled": (
            value.question_memory_consumption_enabled
        ),
        "long_term_consumption_available": value.long_term_consumption_available,
        "stop_owner_role": value.stop_owner_role.strip(),
    }
    canonical = json.dumps(config_payload, sort_keys=True, separators=(",", ":"))
    config_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    observation_id = "budget-shadow-" + config_digest[:16]
    return {
        "schema_version": "memory-budget-shadow-preflight-v1",
        "mode": "VALIDATE_ONLY",
        "observation_id": observation_id,
        "config_digest": config_digest,
        "target_environment": value.target_environment.strip(),
        "observation_hours": value.observation_hours,
        "ready": not failures,
        "failures": sorted(failures),
        "configuration_changed": False,
    }

def evaluate_stop_gates(observation: dict) -> dict:
    stop_reasons = []
    direct = {
        "known_over_budget_provider_call": observation.get(
            "known_over_budget_provider_calls", 0
        )
        > 0,
        "mandatory_current_content_loss": observation.get(
            "mandatory_current_content_losses", 0
        )
        > 0,
        "privacy_audit_hit": observation.get("privacy_audit_hits", 0) > 0,
        "budget_config_conflict": bool(observation.get("budget_config_conflict")),
        "metric_data_incomplete": not bool(observation.get("data_complete", False)),
        "metric_store_unavailable": observation.get(
            "unavailable_bucket_count", 0
        )
        > 1,
    }
    stop_reasons.extend(reason for reason, triggered in direct.items() if triggered)
    sample_count = int(observation.get("followup_sample_count", 0))
    if sample_count >= 200:
        baseline_errors = float(observation.get("baseline_error_rate", 0.0))
        observed_errors = float(observation.get("followup_error_rate", 0.0))
        if observed_errors - baseline_errors > 0.005:
            stop_reasons.append("followup_error_rate_regression")
        baseline_p95 = float(observation.get("baseline_p95_latency_ms", 0.0))
        observed_p95 = float(observation.get("followup_p95_latency_ms", 0.0))
        if baseline_p95 > 0 and observed_p95 > baseline_p95 * 1.20:
            stop_reasons.append("followup_p95_latency_regression")
    return {
        "stop": bool(stop_reasons),
        "stop_reasons": sorted(stop_reasons),
        "sample_status": "sufficient" if sample_count >= 200 else "insufficient_sample",
        "may_expand": sample_count >= 200 and not stop_reasons,
    }


def build_observation_record(*, preflight: dict, aggregate: dict) -> dict:
    gates = evaluate_stop_gates(aggregate)
    return {
        "schema_version": "memory-budget-shadow-observation-v1",
        "observation_id": preflight["observation_id"],
        "config_digest": preflight["config_digest"],
        "target_environment": preflight["target_environment"],
        "observation_hours": preflight["observation_hours"],
        "language_sample_status": dict(aggregate.get("language_sample_status", {})),
        "estimator_error_direction": dict(
            aggregate.get("estimator_error_direction", {})
        ),
        "route_counts": dict(aggregate.get("route_counts", {})),
        "fallback_count": int(aggregate.get("fallback_count", 0)),
        "latency_aggregates": dict(aggregate.get("latency_aggregates", {})),
        "cost_aggregates": dict(aggregate.get("cost_aggregates", {})),
        "stop_gate": gates,
    }
