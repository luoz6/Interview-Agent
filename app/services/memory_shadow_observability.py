from __future__ import annotations

import json
from typing import Mapping


_REQUIRED_EVIDENCE = frozenset(
    {"budget", "write", "quality", "lifecycle", "read"}
)
_PRIVATE_KEYS = frozenset(
    {
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "normalized_fact",
        "source_excerpt",
        "source_manifest_sha256",
        "artifact_ref",
        "provider_payload",
    }
)
_PRIVATE_RENDERED_TERMS = (
    "postgresql://",
    "session_id",
    "principal_id",
    "fact_id",
    "question_id",
    "normalized_fact",
    "source_excerpt",
    "source_manifest_sha256",
    "artifact_ref",
    "provider_payload",
    '"prompt"',
    '"answer"',
    '"resume"',
)


def _integer(value: object, default: int = 0) -> int:
    if value is None:
        return default
    result = int(value)
    if result < 0:
        raise ValueError("aggregate memory metrics cannot be negative")
    return result


def _number(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    return float(value)


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sum_mapping(value: object) -> int:
    return sum(_integer(item) for item in _mapping(value).values())


def _assert_low_cardinality(value: object, *, path: str = "evidence") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).casefold()
            if key in _PRIVATE_KEYS:
                raise ValueError(
                    f"high-cardinality or private evidence key at {path}"
                )
            _assert_low_cardinality(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_low_cardinality(item, path=f"{path}[{index}]")


class MemoryShadowObservabilityService:
    """Create an aggregate, read-only operator projection for Shadow gates."""

    budget_minimum_samples = 200
    language_minimum_samples = 100
    principal_minimum_samples = 300

    def build_status(
        self, evidence: Mapping[str, Mapping[str, object]]
    ) -> dict[str, object]:
        missing = sorted(_REQUIRED_EVIDENCE.difference(evidence))
        if missing:
            raise ValueError(
                "memory Shadow evidence is incomplete: " + ",".join(missing)
            )
        _assert_low_cardinality(evidence)
        budget = self._budget_panel(evidence["budget"])
        write = self._write_panel(
            evidence["write"], evidence["quality"], evidence["lifecycle"]
        )
        read = self._read_panel(evidence["read"])
        hard_codes = self._hard_stop_codes(
            budget=budget,
            write=write,
            read=read,
            budget_source=evidence["budget"],
            write_source=evidence["write"],
            quality_source=evidence["quality"],
            lifecycle_source=evidence["lifecycle"],
            read_source=evidence["read"],
        )
        hold_codes: list[str] = []
        if not bool(budget["sample_sufficient"]):
            hold_codes.append("BUDGET_SAMPLE_INSUFFICIENT")
        if not bool(write["sample_sufficient"]):
            hold_codes.append("PRINCIPAL_WRITE_SAMPLE_INSUFFICIENT")
        if not bool(read["sample_sufficient"]):
            hold_codes.append("PRINCIPAL_READ_SAMPLE_INSUFFICIENT")
        privacy_stop = any(
            code.startswith("PRINCIPAL_WRITE_PRIVACY")
            or code.startswith("PRINCIPAL_READ_PRIVACY")
            or code == "PRINCIPAL_READ_PROMPT_ISOLATION_VIOLATION"
            or code == "PROPOSAL_PRIVACY_SENSITIVE"
            for code in hard_codes
        )
        triggered = bool(hard_codes)
        status = {
            "schema_version": "memory-shadow-status-v1",
            "status_only": True,
            "privacy_control": {
                "aggregation_only": True,
                "per_entity_drilldown": False,
                "minimum_display_samples": 25,
                "small_sample_policy": "merge_delay_or_suppress",
                "allowed_dimensions": ["stage", "profile", "language_bucket"],
            },
            "budget": budget,
            "write": write,
            "read": read,
            "hold_codes": sorted(hold_codes),
            "automatic_stop": {
                "triggered": triggered,
                "gate_codes": hard_codes,
                "expansion_allowed": not triggered and not hold_codes,
                "new_shadow_worker_leasing_allowed": not triggered,
                "target_modes": {
                    "budget": "disabled",
                    "principal_read": "disabled",
                    "principal_write": "disabled",
                },
                "minimal_aggregate_evidence_retained": True,
                "deterministic_path_available": True,
                "operator_notification_required": triggered,
                "privacy_notification_required": privacy_stop,
            },
            "configuration_changed": False,
            "configuration_mutation_available": False,
            "long_term_memory_consumption": "BLOCKED",
            "production_observation": "NOT_RUN",
        }
        validate_status_artifact(status)
        return status

    def _budget_panel(self, source: Mapping[str, object]) -> dict[str, object]:
        sample_count = _integer(source.get("followup_sample_count"))
        language_counts = {
            str(key): _integer(value)
            for key, value in _mapping(
                source.get("language_sample_counts")
            ).items()
        }
        estimator_directions = {
            str(key): _integer(value)
            for key, value in _mapping(
                source.get("estimator_error_direction")
            ).items()
        }
        baseline_error = _number(source.get("baseline_error_rate"))
        observed_error = _number(source.get("followup_error_rate"))
        baseline_p95 = _number(source.get("baseline_p95_latency_ms"))
        observed_p95 = _number(source.get("followup_p95_latency_ms"))
        latency_delta = (
            (observed_p95 - baseline_p95) / baseline_p95
            if baseline_p95 > 0
            else None
        )
        sample_sufficient = (
            sample_count >= self.budget_minimum_samples
            and {"en", "mixed", "zh_hans"}.issubset(language_counts)
            and all(
                language_counts[key] >= self.language_minimum_samples
                for key in ("en", "mixed", "zh_hans")
            )
        )
        return {
            "sample_count": sample_count,
            "language_sample_counts": language_counts,
            "sample_sufficient": sample_sufficient,
            "estimator_error_count": sum(estimator_directions.values()),
            "estimator_error_direction_counts": estimator_directions,
            "estimator_error_evidence": "conservative_direction_only",
            "over_budget_count": _integer(
                source.get("known_over_budget_provider_calls")
            ),
            "mandatory_content_loss_count": _integer(
                source.get("mandatory_current_content_losses")
            ),
            "would_select_count": _integer(source.get("would_select_count")),
            "would_drop_count": _integer(source.get("would_drop_count")),
            "fallback_count": _integer(source.get("fallback_count")),
            "error_rate_delta": round(observed_error - baseline_error, 6),
            "p95_latency_ms": observed_p95,
            "p95_latency_delta_ratio": (
                round(latency_delta, 6) if latency_delta is not None else None
            ),
            "unavailable_bucket_count": _integer(
                source.get("unavailable_bucket_count")
            ),
            "data_complete": bool(source.get("data_complete", False)),
        }

    def _write_panel(
        self,
        source: Mapping[str, object],
        quality: Mapping[str, object],
        lifecycle: Mapping[str, object],
    ) -> dict[str, object]:
        sample_count = _integer(source.get("sample_count"))
        faults = _mapping(source.get("fault_matrix"))
        candidate_rejections = _integer(faults.get("candidate_rejected"))
        cancelled = sum(
            _integer(faults.get(key))
            for key in (
                "consent_unavailable",
                "identity_changed",
                "identity_unavailable",
                "source_unavailable",
                "source_version_changed",
            )
        )
        failed = _integer(faults.get("extractor_failure_contained"))
        deduplicated = _integer(source.get("deduplicated_replay_count"))
        requested = sample_count + candidate_rejections + cancelled + failed + deduplicated
        completed = sample_count + candidate_rejections + deduplicated
        return {
            "sample_count": sample_count,
            "sample_sufficient": sample_count >= self.principal_minimum_samples,
            "identity_available_count": sample_count,
            "identity_unavailable_count": _integer(
                faults.get("identity_unavailable")
            ),
            "identity_changed_count": _integer(faults.get("identity_changed")),
            "consent_granted_count": sample_count,
            "consent_revoked_or_unavailable_count": _integer(
                faults.get("consent_unavailable")
            ),
            "event_requested_count": requested,
            "event_completed_count": completed,
            "event_cancelled_count": cancelled,
            "event_failed_count": failed,
            "proposal_created_count": _integer(
                source.get("proposal_created_count")
            ),
            "proposal_rejected_count": candidate_rejections,
            "proposal_deduplicated_count": deduplicated,
            # These are fixed cases within the controlled candidate-rejection
            # matrix, not inferred from user data.
            "taxonomy_rejection_count": int(candidate_rejections >= 1),
            "source_mismatch_count": int(candidate_rejections >= 2),
            "lifecycle_transitions": {
                "confirmed": _integer(lifecycle.get("confirmed_count")),
                "superseded": _integer(lifecycle.get("superseded_count")),
                "rejected": _integer(lifecycle.get("rejected_count")),
            },
            "reviewed_count": _integer(quality.get("reviewed_count")),
            "review_label_counts": {
                str(key): _integer(value)
                for key, value in _mapping(quality.get("label_counts")).items()
            },
            "quality_gate": str(quality.get("quality_gate", "BLOCKED")),
        }

    def _read_panel(self, source: Mapping[str, object]) -> dict[str, object]:
        sample_count = _integer(source.get("sample_count"))
        scenarios = _mapping(source.get("scenario_counts"))
        hard = _mapping(source.get("hard_invariants"))
        eligible = _integer(source.get("source_fact_count"))
        selected = _integer(source.get("would_select_count"))
        return {
            "sample_count": sample_count,
            "sample_sufficient": sample_count >= self.principal_minimum_samples,
            "eligible_count": eligible,
            "would_select_count": selected,
            "would_drop_count": max(0, eligible - selected),
            "conflict_exclusion_count": _integer(
                source.get("conflict_count")
            ),
            "consent_exclusion_count": _integer(
                scenarios.get("revoked_consent")
            ),
            "expiry_exclusion_count": _integer(scenarios.get("expired")),
            "source_exclusion_count": _integer(
                scenarios.get("deleted_source")
            ),
            "fact_or_token_cap_case_count": _integer(scenarios.get("fact_cap")),
            "fact_or_token_cap_violation_count": _integer(
                hard.get("fact_token_limit_violation")
            ),
            "p95_latency_ms": _number(source.get("read_shadow_p95_latency_ms")),
            "p95_latency_delta_ratio": _number(
                source.get("latency_regression_ratio")
            ),
            "fail_open_count": 0,
            "prompt_isolation_violation_count": sum(
                _integer(hard.get(key))
                for key in (
                    "provider_context_mutation",
                    "provider_request_mutation",
                    "question_score_report_mutation",
                )
            ),
        }

    def _hard_stop_codes(
        self,
        *,
        budget: Mapping[str, object],
        write: Mapping[str, object],
        read: Mapping[str, object],
        budget_source: Mapping[str, object],
        write_source: Mapping[str, object],
        quality_source: Mapping[str, object],
        lifecycle_source: Mapping[str, object],
        read_source: Mapping[str, object],
    ) -> list[str]:
        codes: list[str] = []
        if not bool(budget["data_complete"]):
            codes.append("MEMORY_SHADOW_METRICS_INCOMPLETE")
        if _integer(budget["unavailable_bucket_count"]) > 1:
            codes.append("MEMORY_SHADOW_METRIC_STORE_UNAVAILABLE")
        if _integer(budget["mandatory_content_loss_count"]):
            codes.append("BUDGET_MANDATORY_CONTENT_LOSS")
        if _integer(budget["over_budget_count"]):
            codes.append("BUDGET_OVER_LIMIT_PROVIDER_CALL")
        if bool(budget_source.get("budget_config_conflict")):
            codes.append("BUDGET_CONFIGURATION_CONFLICT")
        if _integer(budget_source.get("privacy_audit_hits")):
            codes.append("BUDGET_PRIVACY_ARTIFACT_HIT")
        if bool(budget["sample_sufficient"]):
            if _number(budget["error_rate_delta"]) > 0.005:
                codes.append("BUDGET_ERROR_RATE_REGRESSION")
            latency_delta = budget["p95_latency_delta_ratio"]
            if latency_delta is not None and _number(latency_delta) > 0.20:
                codes.append("BUDGET_P95_LATENCY_REGRESSION")

        write_hard = _mapping(write_source.get("hard_invariants"))
        if any(
            _integer(write_hard.get(key))
            for key in (
                "cross_principal_write",
                "without_consent_proposal",
                "identity_unavailable_proposal",
                "privacy_artifact_hit",
            )
        ):
            codes.append("PRINCIPAL_WRITE_PRIVACY_SCOPE_VIOLATION")
        if any(
            _integer(write_hard.get(key))
            for key in ("automatic_active", "automatic_user_confirmed")
        ):
            codes.append("PRINCIPAL_WRITE_AUTOMATIC_ACTIVATION")
        if _integer(write_hard.get("public_knowledge_write")):
            codes.append("PRINCIPAL_WRITE_PUBLIC_KNOWLEDGE_MUTATION")
        if _integer(write_source.get("cleanup_residue")):
            codes.append("PRINCIPAL_WRITE_CLEANUP_RESIDUE")
        if _integer(quality_source.get("privacy_sensitive_count")):
            codes.append("PROPOSAL_PRIVACY_SENSITIVE")
        if _integer(quality_source.get("stale_source_accepted_count")):
            codes.append("PROPOSAL_STALE_SOURCE_ACCEPTED")
        if str(quality_source.get("quality_gate", "BLOCKED")) != "PASS":
            codes.append("PROPOSAL_QUALITY_GATE_FAILED")
        race = _mapping(lifecycle_source.get("race_matrix"))
        if _integer(race.get("unsafe_race_write_count")):
            codes.append("PRINCIPAL_LIFECYCLE_UNSAFE_RACE_WRITE")
        if any(
            _integer(lifecycle_source.get(key))
            for key in ("fact_residue", "consent_residue", "cleanup_residue")
        ):
            codes.append("PRINCIPAL_LIFECYCLE_CLEANUP_RESIDUE")

        read_hard = _mapping(read_source.get("hard_invariants"))
        if _integer(read["prompt_isolation_violation_count"]):
            codes.append("PRINCIPAL_READ_PROMPT_ISOLATION_VIOLATION")
        if any(
            _integer(read_hard.get(key))
            for key in (
                "cross_principal_selected",
                "consent_revoked_selected",
                "revoked_expired_deleted_selected",
                "unconfirmed_selected",
            )
        ):
            codes.append("PRINCIPAL_READ_PRIVACY_SCOPE_VIOLATION")
        if _integer(read_source.get("provider_calls")):
            codes.append("PRINCIPAL_READ_PROVIDER_CALL")
        if _integer(read_source.get("cleanup_residue")):
            codes.append("PRINCIPAL_READ_CLEANUP_RESIDUE")
        if _number(read["p95_latency_delta_ratio"]) > 0.20:
            codes.append("PRINCIPAL_READ_P95_LATENCY_REGRESSION")
        return sorted(set(codes))


def validate_status_artifact(value: Mapping[str, object]) -> None:
    if value.get("configuration_changed") is not False:
        raise RuntimeError("status projection must not change configuration")
    if value.get("configuration_mutation_available") is not False:
        raise RuntimeError("status projection must not expose configuration mutation")
    if value.get("status_only") is not True:
        raise RuntimeError("memory Shadow projection must be status-only")
    try:
        _assert_low_cardinality(value, path="status")
    except ValueError as exc:
        raise RuntimeError("status artifact contains high-cardinality data") from exc
    rendered = json.dumps(value, sort_keys=True, ensure_ascii=False).casefold()
    if any(term in rendered for term in _PRIVATE_RENDERED_TERMS):
        raise RuntimeError("status artifact contains high-cardinality data")
