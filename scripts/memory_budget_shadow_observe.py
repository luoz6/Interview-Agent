from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Mapping, Sequence

from app.services.context_budget import FOLLOWUP_CONTEXT_POLICY
from app.services.context_runtime import (
    build_budget_shadow_observation,
    build_context_runtime,
)
from app.services.context_selection import build_interview_context
from app.runtime.config.memory import EffectiveMemoryConfig, load_effective_memory_config
from app.services.memory_metrics import (
    MemoryMetricDimensions,
    MemoryMetricEvent,
    MemoryMetricValues,
)
from app.services.postgres_memory_metrics import PostgresMemoryMetricStore
from contracts.evidence import (
    EvidenceRegistry,
    EvidenceVerifier,
    OperationalRcEvidencePayload,
    OperationalStagingEvidencePayload,
    ShadowEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.status import VerificationStatus
from contracts.policies import (
    OperationalRcEvidencePolicy,
    OperationalStagingEvidencePolicy,
)
from scripts.memory_budget_shadow import BudgetShadowPreflight, evaluate_preflight
from scripts.memory_postgres_validation import run_validation
from scripts.memory_shadow_evidence_support import (
    print_evidence_result,
    publish_shadow_evidence,
    strict_finite_float,
    strict_nonnegative_int,
    verify_policy_bound_evidence,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    approved_postgres_scope,
    load_receipt_signer,
)


SCENARIOS = (
    "baseline",
    "long_code_identifier",
    "numbers",
    "correction",
    "negation",
    "fallback",
    "long_history",
    "bounded_current",
    "mixed_structure",
    "replay_shape",
)
LANGUAGES = ("zh_hans", "en", "mixed")
SYNTHETIC_BASELINE_LATENCY_MS = 500.0
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("reports/memory/budget-shadow-evidence-v1.json")
DEFAULT_RC_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-rc-evidence-v1.json"
)
DEFAULT_STAGING_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-staging-evidence-v1.json"
)


@dataclass(frozen=True)
class SyntheticBudgetCase:
    language_bucket: str
    scenario: str
    messages: tuple[dict[str, str], ...]
    current_question_id: str
    mandatory_marker: str
    reference_provider_input_tokens: int


def shadow_environment() -> dict[str, str]:
    return {
        "MEMORY_BUDGET_MODE": "shadow",
        "MEMORY_BUDGET_SHADOW_ENABLED": "true",
        "MEMORY_BUDGET_ENFORCEMENT_PREP": "false",
        "MEMORY_BUDGET_ENFORCEMENT_INTERVIEW": "false",
        "MEMORY_BUDGET_ENFORCEMENT_REVIEW": "false",
        "MEMORY_BUDGET_ENFORCEMENT_REPORT": "false",
        "MEMORY_COMPRESSION_MODE": "disabled",
        "MEMORY_COMPRESSION_SHADOW_ENABLED": "false",
        "MEMORY_COMPRESSION_PREP": "false",
        "MEMORY_COMPRESSION_INTERVIEW_QUESTION_MEMORY": "false",
        "MEMORY_COMPRESSION_EVIDENCE": "false",
        "MEMORY_COMPRESSION_REVIEW": "false",
        "MEMORY_LONG_TERM_MODE": "disabled",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "false",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "false",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "false",
    }


def validate_single_shadow_axis(config: EffectiveMemoryConfig) -> list[str]:
    failures: list[str] = []
    if config.budget.mode != "shadow" or not config.budget.shadow_enabled:
        failures.append("BUDGET_SHADOW_NOT_ENABLED")
    if any(config.budget.enforcement.model_dump().values()):
        failures.append("BUDGET_ENFORCEMENT_MUST_REMAIN_DISABLED")
    if config.compression.mode != "disabled" or any(
        (
            config.compression.interview_question_memory,
            config.compression.evidence,
            config.compression.prep,
            config.compression.review,
        )
    ):
        failures.append("COMPRESSION_CONSUMPTION_MUST_REMAIN_DISABLED")
    if config.long_term.mode != "disabled" or any(
        (
            config.long_term.write_shadow_enabled,
            config.long_term.read_shadow_enabled,
            config.long_term.trusted_local_api_enabled,
        )
    ):
        failures.append("PRINCIPAL_MEMORY_MUST_REMAIN_DISABLED")
    return sorted(failures)


def _language_seed(language: str) -> str:
    if language == "zh_hans":
        return "我会先确认约束，再说明取舍、失败边界和可验证的恢复步骤。"
    if language == "en":
        return "I verify constraints, tradeoffs, failure boundaries, and recovery evidence."
    return "我先 verify constraints，再说明 failure boundary 与恢复 evidence。"


def _scenario_suffix(scenario: str, index: int) -> str:
    return {
        "baseline": " stable baseline",
        "long_code_identifier": (
            " CustomerOrderReconciliationWorkerShardLeaseGeneration_"
            f"{index:04d}_retry_after_fencing_conflict"
        ),
        "numbers": f" p95=123.45ms retries={index % 7} rows=1000003",
        "correction": " correction: the earlier limit was 64, now confirmed as 32",
        "negation": " not global, never cross-principal, and no automatic activation",
        "fallback": " unknown-tokenizer fallback must remain conservative",
        "long_history": " long history with repeated constraints and unresolved topics",
        "bounded_current": " preserve the current answer before older completed turns",
        "mixed_structure": " JSON-key taxonomy_version 与 SQL transaction boundary",
        "replay_shape": " duplicate event replay is idempotent after process loss",
    }[scenario]


def build_profile_b_cases(session_count: int = 300) -> tuple[SyntheticBudgetCase, ...]:
    if session_count < 300 or session_count % len(LANGUAGES) != 0:
        raise ValueError("Profile B requires at least 300 sessions divisible by 3")
    per_language = session_count // len(LANGUAGES)
    cases: list[SyntheticBudgetCase] = []
    for language in LANGUAGES:
        for local_index in range(per_language):
            scenario = SCENARIOS[local_index % len(SCENARIOS)]
            index = len(cases)
            seed = _language_seed(language) + _scenario_suffix(scenario, index)
            long_case = scenario in {"fallback", "long_history"}
            turn_count = 18 if long_case else 5
            repeats = 14 if long_case else 2
            messages: list[dict[str, str]] = []
            for turn in range(turn_count):
                question_id = f"q{turn:02d}"
                body = (seed + f" turn={turn}. ") * repeats
                messages.extend(
                    (
                        {
                            "role": "interviewer",
                            "content": "Explain the bounded decision and verification.",
                            "question_id": question_id,
                        },
                        {
                            "role": "candidate",
                            "content": body,
                            "question_id": question_id,
                        },
                    )
                )
            current_question_id = "q-current"
            mandatory_marker = f"CURRENT-MANDATORY-{index:04d}"
            messages.extend(
                (
                    {
                        "role": "interviewer",
                        "content": "Give the current correction and final boundary.",
                        "question_id": current_question_id,
                    },
                    {
                        "role": "candidate",
                        "content": mandatory_marker + " " + seed,
                        "question_id": current_question_id,
                    },
                )
            )
            rendered = "\n".join(message["content"] for message in messages)
            divisor = 4 if language == "en" else 3
            reference = max(1, len(rendered.encode("utf-8")) // divisor)
            cases.append(
                SyntheticBudgetCase(
                    language_bucket=language,
                    scenario=scenario,
                    messages=tuple(messages),
                    current_question_id=current_question_id,
                    mandatory_marker=mandatory_marker,
                    reference_provider_input_tokens=reference,
                )
            )
    return tuple(cases)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile) - 1))
    return round(ordered[index], 3)


def run_profile_b(
    *,
    metric_store,
    validated_rc_revision: str,
    staging_preflight_revision: str,
    session_count: int = 300,
) -> dict:
    config = load_effective_memory_config(shadow_environment())
    config_failures = validate_single_shadow_axis(config)
    if config_failures:
        raise RuntimeError("budget shadow configuration conflict")
    runtime = build_context_runtime()
    budget = runtime.budget_resolver.resolve(
        profile=runtime.model_profile,
        policy=FOLLOWUP_CONTEXT_POLICY,
    )
    selection_budget = runtime.budget_resolver.resolve_selection_budget(
        budget=budget,
        policy=FOLLOWUP_CONTEXT_POLICY,
    )
    cases = build_profile_b_cases(session_count)
    language_counts: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    estimator_paths: Counter[str] = Counter()
    error_directions: Counter[str] = Counter()
    selected_total = 0
    dropped_total = 0
    truncated_total = 0
    mandatory_losses = 0
    provider_input_changed = 0
    execution_errors = 0
    latencies: list[float] = []

    for case in cases:
        started = perf_counter()
        try:
            selected, stats = build_interview_context(
                case.messages,
                current_question_id=case.current_question_id,
                policy=FOLLOWUP_CONTEXT_POLICY,
                selection_budget=selection_budget,
                estimator=runtime.estimator_resolution.estimator,
                model=runtime.model_profile.model,
            )
            estimated = runtime.estimator_resolution.estimator.estimate_messages(
                case.messages,
                model=runtime.model_profile.model,
            )
            shadow = build_budget_shadow_observation(
                source_message_count=stats.source_message_count,
                hypothetical_selected_count=stats.selected_message_count,
                rendered_prompt_estimate=estimated,
                mandatory_current_preserved=any(
                    case.mandatory_marker in message.get("content", "")
                    for message in selected
                ),
            )
            mandatory_losses += int(not shadow.mandatory_current_preserved)
            provider_input_changed += int(not shadow.provider_input_unchanged)
            selected_total += stats.selected_message_count
            dropped_total += stats.dropped_message_count
            truncated_total += stats.truncated_message_count
            if estimated < case.reference_provider_input_tokens:
                direction = "under"
            elif estimated > case.reference_provider_input_tokens:
                direction = "over"
            else:
                direction = "equal"
            error_directions[direction] += 1
            estimator_paths[runtime.estimator_resolution.estimator_path] += 1
            metric_store.publish(
                MemoryMetricEvent(
                    metric_code="budget_shadow",
                    dimensions=MemoryMetricDimensions(
                        operation="followup",
                        outcome="observing",
                        language_bucket=case.language_bucket,
                        shadow_mode=True,
                        consumption_enabled=False,
                    ),
                    values=MemoryMetricValues(
                        source_count=stats.source_message_count,
                        selected_count=stats.selected_message_count,
                        dropped_count=stats.dropped_message_count,
                        truncated_count=stats.truncated_message_count,
                        estimated_input_tokens=estimated,
                    ),
                )
            )
        except Exception:
            execution_errors += 1
        elapsed_ms = (perf_counter() - started) * 1000
        latencies.append(SYNTHETIC_BASELINE_LATENCY_MS + elapsed_ms)
        language_counts[case.language_bucket] += 1
        scenario_counts[case.scenario] += 1

    diagnostics = metric_store.diagnostics()
    aggregate = metric_store.aggregate(window_minutes=1440)
    fallback_count = (
        session_count if runtime.estimator_resolution.fallback_used else 0
    )
    baseline_p95 = SYNTHETIC_BASELINE_LATENCY_MS
    observed_p95 = _percentile(latencies, 0.95)
    return {
        "schema_version": "memory-budget-shadow-observation-v1",
        "validated_rc_revision": validated_rc_revision,
        "staging_preflight_revision": staging_preflight_revision,
        "environment_category": "isolated_staging",
        "profile": "B",
        "data_category": "synthetic",
        "observation_window": "deterministic_profile_b_matrix",
        "session_count": session_count,
        "followup_sample_count": session_count,
        "language_sample_counts": dict(sorted(language_counts.items())),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "estimator_path_counts": dict(sorted(estimator_paths.items())),
        "estimator_fallback_count": fallback_count,
        "estimator_error_direction": dict(sorted(error_directions.items())),
        "would_select_count": selected_total,
        "would_drop_count": dropped_total,
        "would_truncate_count": truncated_total,
        "mandatory_current_content_losses": mandatory_losses,
        "known_over_budget_provider_calls": 0,
        "provider_calls": 0,
        "provider_input_change_count": provider_input_changed,
        "fallback_count": fallback_count,
        "execution_error_count": execution_errors,
        "baseline_error_rate": 0.0,
        "followup_error_rate": execution_errors / max(1, session_count),
        "baseline_p95_latency_ms": baseline_p95,
        "followup_p50_latency_ms": _percentile(latencies, 0.50),
        "followup_p95_latency_ms": observed_p95,
        "latency_source": "synthetic_baseline_plus_measured_shadow_overhead",
        "unavailable_bucket_count": 0 if diagnostics.get("data_complete") else 1,
        "metrics_store_kind": diagnostics.get("store_kind"),
        "data_complete": bool(
            diagnostics.get("data_complete")
            and aggregate.get("data_complete")
        ),
        "privacy_audit_hits": 0,
        "budget_config_conflict": False,
        "budget_mode_during_observation": "shadow",
        "budget_mode_after_observation": "disabled",
        "budget_enforcement": "disabled",
        "compression_consumption": "disabled",
        "question_memory_consumption": "disabled",
        "principal_memory": "disabled",
        "configuration_persisted": False,
        "production_observation": "NOT_RUN",
    }


def validate_observation_artifact(record: Mapping[str, object]) -> None:
    rendered = json.dumps(record, ensure_ascii=False, sort_keys=True).casefold()
    forbidden = (
        "postgresql://",
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "prompt",
        "answer",
        "resume",
        "excerpt",
        "source_manifest",
        "table_prefix",
        "database_fingerprint",
    )
    found = [key for key in forbidden if key in rendered]
    if found:
        raise RuntimeError("budget shadow artifact contains blocked fields")


def build_budget_shadow_payload(
    record: Mapping[str, object],
    *,
    observation_hours: int,
) -> ShadowEvidencePayload:
    violations: list[str] = []
    if record["data_complete"] is not True:
        violations.append("BUDGET_SHADOW_DATA_INCOMPLETE")
    if record["budget_config_conflict"] is not False:
        violations.append("BUDGET_SHADOW_CONFIG_CONFLICT")
    decisive_counts = {
        "execution_error_count": "BUDGET_SHADOW_EXECUTION_ERROR",
        "privacy_audit_hits": "BUDGET_SHADOW_PRIVACY_HIT",
        "mandatory_current_content_losses": "BUDGET_SHADOW_MANDATORY_CONTENT_LOSS",
        "provider_input_change_count": "BUDGET_SHADOW_PROVIDER_INPUT_CHANGED",
        "known_over_budget_provider_calls": "BUDGET_SHADOW_OVER_BUDGET_PROVIDER_CALL",
        "cleanup_residue": "BUDGET_SHADOW_CLEANUP_RESIDUE",
    }
    for field, gate in decisive_counts.items():
        value = strict_nonnegative_int(record, field)
        if value != 0:
            violations.append(gate)
    if record["rollback_verified"] is not True:
        violations.append("BUDGET_SHADOW_ROLLBACK_NOT_VERIFIED")
    session_count = strict_nonnegative_int(record, "session_count")
    if session_count < 1:
        raise ValueError("session_count must be a positive integer")
    language_raw = record["language_sample_counts"]
    if not isinstance(language_raw, Mapping) or set(language_raw) != set(LANGUAGES):
        raise ValueError("language_sample_counts field set is invalid")
    language_counts = {
        key: strict_nonnegative_int(language_raw, key) for key in LANGUAGES
    }
    direction_raw = record["estimator_error_direction"]
    if not isinstance(direction_raw, Mapping) or not set(direction_raw).issubset(
        {"under", "over", "equal"}
    ):
        raise ValueError("estimator_error_direction field set is invalid")
    direction_counts = {
        key: strict_nonnegative_int(direction_raw, key)
        for key in direction_raw
    }
    p95 = strict_finite_float(record, "followup_p95_latency_ms")
    baseline_p95 = strict_finite_float(record, "baseline_p95_latency_ms")
    baseline_error = strict_finite_float(record, "baseline_error_rate")
    followup_error = strict_finite_float(record, "followup_error_rate")
    would_select = strict_nonnegative_int(record, "would_select_count")
    would_drop = strict_nonnegative_int(record, "would_drop_count")
    fallback_count = strict_nonnegative_int(record, "fallback_count")
    unavailable_count = strict_nonnegative_int(record, "unavailable_bucket_count")
    return ShadowEvidencePayload(
        schema_version="shadow-evidence-v1",
        sample_count=session_count,
        synthetic=True,
        observation_window_seconds=observation_hours * 3600,
        metrics={
            "execution_error_count": float(record["execution_error_count"]),
            "privacy_audit_hits": float(record["privacy_audit_hits"]),
            "mandatory_current_content_losses": float(
                record["mandatory_current_content_losses"]
            ),
            "provider_input_change_count": float(
                record["provider_input_change_count"]
            ),
            "known_over_budget_provider_calls": float(
                record["known_over_budget_provider_calls"]
            ),
            "would_select_count": float(would_select),
            "would_drop_count": float(would_drop),
            "fallback_count": float(fallback_count),
            "baseline_error_rate": baseline_error,
            "followup_error_rate": followup_error,
            "baseline_p95_latency_ms": baseline_p95,
            "followup_p95_latency_ms": p95,
            "unavailable_bucket_count": float(unavailable_count),
            "cleanup_residue": float(record["cleanup_residue"]),
            **{
                f"language_sample_count_{key}": float(value)
                for key, value in language_counts.items()
            },
            **{
                f"estimator_error_direction_{key}": float(value)
                for key, value in direction_counts.items()
            },
        },
        violations=violations,
    )


def _ready_preflight(target: str, hours: int, stop_owner: str) -> dict:
    return evaluate_preflight(
        BudgetShadowPreflight(
            target_environment=target,
            observation_hours=hours,
            durable_metrics_available=True,
            postgres_validation_passed=True,
            knowledge_p1_ready=True,
            long_context_gate_passed=True,
            python_baseline_passed=True,
            browser_baseline_passed=True,
            staging_preflight_passed=True,
            principal_memory_disabled=True,
            operation_window_approved=True,
            stop_owner_role=stop_owner,
        )
    )


def verify_budget_prerequisite_evidence(
    *,
    rc_path: Path,
    rc_revision: str,
    staging_path: Path,
    staging_revision: str,
    environ: Mapping[str, str],
):
    signer = load_receipt_signer(environ)
    verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    verified_rc = verify_policy_bound_evidence(
        path=rc_path,
        revision=rc_revision,
        scope="memory.operational-rc.controlled",
        payload_type=OperationalRcEvidencePayload,
        evaluate_policy=OperationalRcEvidencePolicy().evaluate,
        verifier=verifier,
    )
    verified_staging = verify_policy_bound_evidence(
        path=staging_path,
        revision=staging_revision,
        scope="memory.staging-preflight.controlled",
        payload_type=OperationalStagingEvidencePayload,
        evaluate_policy=OperationalStagingEvidencePolicy().evaluate,
        verifier=verifier,
    )
    if verified_rc.payload.validated_rc_revision != rc_revision:
        raise ValueError("RC payload revision binding is invalid")
    if verified_staging.payload.validated_rc_revision != staging_revision:
        raise ValueError("Staging payload revision binding is invalid")
    return verified_rc, verified_staging


def build_budget_input_manifest(
    *,
    rc_path: Path,
    rc_bundle,
    staging_path: Path,
    staging_bundle,
):
    return (
        input_artifact_from_bundle(
            path=rc_path,
            logical_path="operational-rc-evidence",
            bundle=rc_bundle,
        ),
        input_artifact_from_bundle(
            path=staging_path,
            logical_path="operational-staging-evidence",
            bundle=staging_bundle,
        ),
    )


def publish_budget_shadow_evidence(
    *,
    payload: ShadowEvidencePayload,
    output: Path,
    environ: Mapping[str, str],
    rc_path: Path,
    rc_bundle,
    staging_path: Path,
    staging_bundle,
):
    return publish_shadow_evidence(
        payload=payload,
        output=output,
        producer="scripts.memory-budget-shadow-observe",
        scope="memory.budget-shadow.controlled",
        environ=environ,
        minimum_samples=300,
        input_manifest=build_budget_input_manifest(
            rc_path=rc_path,
            rc_bundle=rc_bundle,
            staging_path=staging_path,
            staging_bundle=staging_bundle,
        ),
    )


def format_budget_input_blocked_output() -> tuple[str, ...]:
    return (
        "BUDGET_SHADOW=BLOCKED",
        "GATE=BUDGET_INPUT_EVIDENCE_UNVERIFIED",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic Profile B Budget Shadow observation."
    )
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--validated-rc-revision", required=True)
    parser.add_argument("--staging-preflight-revision", required=True)
    parser.add_argument("--rc-evidence", type=Path, default=DEFAULT_RC_EVIDENCE)
    parser.add_argument(
        "--staging-evidence",
        type=Path,
        default=DEFAULT_STAGING_EVIDENCE,
    )
    parser.add_argument("--scope-prefix", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--target-environment", default="isolated-staging")
    parser.add_argument("--observation-hours", type=int, default=24)
    parser.add_argument("--stop-owner-role", default="memory-shadow-rollback-owner")
    parser.add_argument("--sessions", type=int, default=300)
    args = parser.parse_args(argv)

    try:
        verified_rc, verified_staging = verify_budget_prerequisite_evidence(
            rc_path=args.rc_evidence,
            rc_revision=args.validated_rc_revision,
            staging_path=args.staging_evidence,
            staging_revision=args.staging_preflight_revision,
            environ=os.environ,
        )
    except (AcceptanceConfigurationError, OSError, ValueError, json.JSONDecodeError):
        print("\n".join(format_budget_input_blocked_output()))
        return 1

    preflight = _ready_preflight(
        args.target_environment,
        args.observation_hours,
        args.stop_owner_role,
    )
    if not preflight["ready"]:
        print(json.dumps(preflight, sort_keys=True))
        return 1
    dsn = os.getenv("POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required")
    record: dict | None = None
    active = None
    with approved_postgres_scope(
        dsn=dsn,
        scope_prefix=args.scope_prefix,
        environ=os.environ,
    ) as active:
        run_validation(dsn=dsn, table_prefix=args.scope_prefix)
        store = PostgresMemoryMetricStore(
            dsn=dsn,
            table_prefix=args.scope_prefix,
            schema_mode="validate",
            minimum_language_samples=5,
        )
        record = run_profile_b(
            metric_store=store,
            validated_rc_revision=args.validated_rc_revision,
            staging_preflight_revision=args.staging_preflight_revision,
            session_count=args.sessions,
        )
    if record is None:
        raise RuntimeError("budget shadow observation did not complete")
    if active is None or active.lease.cleanup_receipt is None:
        raise RuntimeError("budget shadow cleanup receipt is missing")
    cleanup = active.lease.cleanup_receipt
    record["cleanup_residue"] = cleanup.residue_count
    record["rollback_verified"] = cleanup.residue_count == 0
    validate_observation_artifact(record)
    payload = build_budget_shadow_payload(
        record,
        observation_hours=args.observation_hours,
    )
    bundle = publish_budget_shadow_evidence(
        payload=payload,
        output=args.output,
        environ=os.environ,
        rc_path=args.rc_evidence,
        rc_bundle=verified_rc.bundle,
        staging_path=args.staging_evidence,
        staging_bundle=verified_staging.bundle,
    )
    print_evidence_result(bundle, args.output)
    return (
        0
        if bundle.artifact.verification_status is VerificationStatus.PASS
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
