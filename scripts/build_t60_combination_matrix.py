from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "interview-quality-v1-t60-combination-matrix-v1"
BUILDER_VERSION = "risk-pairwise-greedy-v1"
DEFAULT_OUTPUT = Path(
    "tests/golden/interview_quality_v1/t60-combination-matrix-v1.json"
)

AXES: dict[str, tuple[str, ...]] = {
    "interview_graph": ("legacy", "durable_v1", "durable_v2"),
    "followup_policy": ("fixed_v1", "adaptive_v1"),
    "plan_state": ("legacy_snapshot", "revision_v2", "edited", "stale", "conflict"),
    "report_job_state": ("queued", "running", "completed", "failed"),
    "report_artifact": ("scored", "partial", "unscored", "degraded", "legacy"),
    "report_version_state": (
        "single",
        "active_history",
        "rescore_success",
        "rescore_fail",
    ),
    "knowledge_retrieval": ("normal", "empty", "degraded"),
    "answer_type": ("strong", "partial", "incorrect", "off_topic", "empty", "skipped"),
    "provider_behavior": ("normal", "timeout", "invalid_json", "retry", "exhausted"),
    "runtime_exception": (
        "sse_interrupt",
        "checkpoint_recovery",
        "lease_expired",
        "stale_worker",
    ),
    "memory_mode": ("disabled",),
}

RISK_PAIR_FAMILIES: tuple[tuple[str, str], ...] = (
    ("interview_graph", "followup_policy"),
    ("plan_state", "report_job_state"),
    ("report_artifact", "report_version_state"),
    ("answer_type", "report_artifact"),
    ("provider_behavior", "runtime_exception"),
    ("knowledge_retrieval", "provider_behavior"),
)

TEST_NODE_BY_VALUE: dict[str, dict[str, str]] = {
    "interview_graph": {
        "legacy": "tests/unit/test_interview_graph.py::test_runner_finishes_after_last_question_followup_answer",
        "durable_v1": "tests/unit/test_durable_interview_graph.py::test_graph_initializes_then_waits_for_answer",
        "durable_v2": "tests/unit/test_dual_langgraph_rollout.py::test_v2_initial_state_contains_only_bounded_artifact_references",
    },
    "followup_policy": {
        "fixed_v1": "tests/unit/test_dual_langgraph_rollout.py::test_followup_ui_state_exposes_only_policy_safe_stage",
        "adaptive_v1": "tests/unit/test_durable_interview_graph.py::test_adaptive_graph_routes_only_from_persisted_decision_and_replays_after_crash",
    },
    "plan_state": {
        "legacy_snapshot": "tests/unit/test_interview_plan_budget.py::test_default_legacy_conversion_closes_its_30_minute_estimate",
        "revision_v2": "tests/unit/test_interview_plan_revision.py::test_saved_revision_cannot_be_mutated_in_place_or_through_a_returned_copy",
        "edited": "tests/unit/test_interview_plan_audit_and_knowledge.py::test_content_edit_invalidates_binding_and_audit_contains_only_hashes",
        "stale": "tests/unit/test_interview_plan_api.py::test_start_rejects_a_historical_revision_and_returns_latest_winner",
        "conflict": "tests/unit/test_interview_plan_api.py::test_revision_conflict_contains_current_metadata_and_does_not_overwrite",
    },
    "report_job_state": {
        "queued": "tests/integration/postgres/test_report_jobs.py::test_enqueue_report_request_creates_job_and_processing_report",
        "running": "tests/integration/postgres/test_report_jobs.py::test_claim_marks_job_running",
        "completed": "tests/unit/test_memory_report_jobs.py::test_preview_runtime_factory_completes_report_and_job",
        "failed": "tests/integration/postgres/test_report_jobs.py::test_report_failure_persists_stable_error_code",
    },
    "report_artifact": {
        "scored": "tests/unit/test_report_coverage.py::test_all_answered_with_sufficient_evidence_is_scored_and_complete",
        "partial": "tests/unit/test_report_degraded.py::test_partial_scores_keep_their_numerator_and_denominator_when_text_degrades",
        "unscored": "tests/unit/test_report_degraded.py::test_insufficient_evidence_publishes_unscored_without_any_numeric_score",
        "degraded": "tests/unit/test_report_degraded.py::test_summary_failure_publishes_degraded_report_without_erasing_valid_scores",
        "legacy": "tests/unit/test_report_view.py::test_legacy_payload_does_not_fabricate_coverage_for_missing_score",
    },
    "report_version_state": {
        "single": "tests/unit/test_report_artifact_store.py::test_artifact_publish_is_immutable_monotonic_and_replay_idempotent",
        "active_history": "tests/unit/test_report_artifact_api.py::test_historical_pdf_is_bound_to_requested_artifact_after_active_pointer_moves",
        "rescore_success": "tests/unit/test_report_artifact_store.py::test_rescore_creates_history_and_switches_active_only_on_success",
        "rescore_fail": "tests/unit/test_report_artifact_store.py::test_failed_job_keeps_old_active_and_requeue_reuses_job",
    },
    "knowledge_retrieval": {
        "normal": "tests/unit/test_knowledge_grounding.py::test_grounding_summaries_are_chinese",
        "empty": "tests/unit/test_knowledge_grounding.py::test_empty_grounding_summary_is_chinese",
        "degraded": "tests/unit/test_knowledge_grounding.py::test_degraded_grounding_context_summary_is_chinese",
    },
    "answer_type": {
        "strong": "tests/unit/test_report_rule_score.py::test_score_dimension_evidence_rewards_tradeoff_metrics_and_fallback",
        "partial": "tests/unit/test_report_rule_score.py::test_score_dimension_evidence_caps_concept_only_answer_below_pass_level",
        "incorrect": "tests/unit/test_report_rule_score.py::test_score_question_caps_nonsense_answer_even_when_provider_claims_evidence",
        "off_topic": "tests/unit/test_report_rule_score.py::test_explicit_off_topic_answer_is_capped",
        "empty": "tests/unit/test_round_review.py::test_run_closed_round_review_short_circuits_empty_answer_agents[unanswered]",
        "skipped": "tests/unit/test_round_review.py::test_run_closed_round_review_short_circuits_empty_answer_agents[skipped]",
    },
    "provider_behavior": {
        "normal": "tests/unit/test_llm_report_service.py::test_generate_report_uses_question_result_schema_and_assembles_report",
        "timeout": "tests/unit/test_report_evaluator.py::test_evaluator_propagates_timeout_for_background_failure_state",
        "invalid_json": "tests/unit/test_llm_report_service.py::test_generate_report_raises_typed_format_error_for_schema_invalid_json",
        "retry": "tests/unit/test_report_eval_runner.py::test_runner_retries_fallback_result",
        "exhausted": "tests/unit/test_report_eval_runner.py::test_runner_propagates_provider_budget_exhaustion",
    },
    "runtime_exception": {
        "sse_interrupt": "tests/unit/test_interview_event_stream.py::test_pending_sse_times_out_with_reconnect_cursor",
        "checkpoint_recovery": "tests/unit/test_durable_interview_graph.py::test_adaptive_graph_routes_only_from_persisted_decision_and_replays_after_crash",
        "lease_expired": "tests/integration/postgres/test_interview_generation_store.py::test_expired_attempt_is_replaced_with_reset_event",
        "stale_worker": "tests/integration/postgres/test_interview_generation_store.py::test_reclaimed_attempt_rejects_every_stale_mutation",
    },
    "memory_mode": {
        "disabled": "tests/unit/test_principal_memory_runtime_isolation.py::test_disabled_runtime_does_not_construct_shadow_dependencies"
    },
}

P0_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "p0-01-durable-v2-checkpoint-report-commit",
        "title": "durable v2 adaptive recovery commits one immutable scored report",
        "axes": {
            "interview_graph": "durable_v2",
            "followup_policy": "adaptive_v1",
            "plan_state": "revision_v2",
            "report_job_state": "completed",
            "report_artifact": "scored",
            "report_version_state": "single",
            "knowledge_retrieval": "normal",
            "answer_type": "strong",
            "provider_behavior": "normal",
            "runtime_exception": "checkpoint_recovery",
            "memory_mode": "disabled",
        },
        "expected_invariants": [
            "recovery routes only from the persisted decision",
            "the revision snapshot remains immutable",
            "artifact publication is idempotent and monotonic",
            "scoring and report paths do not construct Principal Memory dependencies",
        ],
        "cross_state_test_nodes": [
            "tests/unit/test_durable_interview_graph.py::test_adaptive_graph_routes_only_from_persisted_decision_and_replays_after_crash",
            "tests/unit/test_interview_plan_revision.py::test_saved_revision_cannot_be_mutated_in_place_or_through_a_returned_copy",
            "tests/unit/test_report_artifact_store.py::test_artifact_publish_is_immutable_monotonic_and_replay_idempotent",
            "tests/unit/test_principal_memory_runtime_isolation.py::test_disabled_runtime_does_not_construct_shadow_dependencies",
        ],
    },
    {
        "id": "p0-02-edited-plan-lease-reclaim-rescore",
        "title": "edited grounded plan survives generation lease reclaim and successful rescore",
        "axes": {
            "interview_graph": "durable_v2",
            "followup_policy": "adaptive_v1",
            "plan_state": "edited",
            "report_job_state": "running",
            "report_artifact": "partial",
            "report_version_state": "rescore_success",
            "knowledge_retrieval": "degraded",
            "answer_type": "partial",
            "provider_behavior": "retry",
            "runtime_exception": "lease_expired",
            "memory_mode": "disabled",
        },
        "expected_invariants": [
            "content edits invalidate stale knowledge bindings and expose only hashes in audit",
            "an expired generation attempt emits reset before replacement chunks",
            "partial coverage keeps its numerator and denominator",
            "successful rescore adds history and moves active only after commit",
        ],
        "cross_state_test_nodes": [
            "tests/unit/test_interview_plan_audit_and_knowledge.py::test_content_edit_invalidates_binding_and_audit_contains_only_hashes",
            "tests/integration/postgres/test_interview_generation_store.py::test_expired_attempt_is_replaced_with_reset_event",
            "tests/unit/test_report_degraded.py::test_partial_scores_keep_their_numerator_and_denominator_when_text_degrades",
            "tests/unit/test_report_artifact_store.py::test_rescore_creates_history_and_switches_active_only_on_success",
        ],
    },
    {
        "id": "p0-03-conflict-stale-worker-failed-rescore",
        "title": "plan conflict and stale worker cannot overwrite the winning revision or active report",
        "axes": {
            "interview_graph": "durable_v1",
            "followup_policy": "fixed_v1",
            "plan_state": "conflict",
            "report_job_state": "failed",
            "report_artifact": "degraded",
            "report_version_state": "rescore_fail",
            "knowledge_retrieval": "empty",
            "answer_type": "off_topic",
            "provider_behavior": "exhausted",
            "runtime_exception": "stale_worker",
            "memory_mode": "disabled",
        },
        "expected_invariants": [
            "the losing revision write returns current winner metadata without overwrite",
            "stale report workers are token fenced",
            "failed rescore retains the previous active artifact and reuses the job on retry",
            "repeated Provider failure for an off-topic answer advances through the safe bounded path",
        ],
        "cross_state_test_nodes": [
            "tests/unit/test_interview_plan_api.py::test_revision_conflict_contains_current_metadata_and_does_not_overwrite",
            "tests/integration/postgres/test_report_jobs.py::test_report_lease_token_fences_stale_worker",
            "tests/unit/test_report_artifact_store.py::test_failed_job_keeps_old_active_and_requeue_reuses_job",
            "tests/unit/test_followup_decision_service.py::test_repeated_provider_failure_for_off_topic_answer_forces_safe_next",
        ],
    },
    {
        "id": "p0-04-stale-plan-sse-skip-unscored",
        "title": "stale plan and SSE interruption cannot fabricate progress or a score for skipped answers",
        "axes": {
            "interview_graph": "legacy",
            "followup_policy": "fixed_v1",
            "plan_state": "stale",
            "report_job_state": "queued",
            "report_artifact": "unscored",
            "report_version_state": "active_history",
            "knowledge_retrieval": "empty",
            "answer_type": "skipped",
            "provider_behavior": "timeout",
            "runtime_exception": "sse_interrupt",
            "memory_mode": "disabled",
        },
        "expected_invariants": [
            "historical revision start is rejected with the latest winner",
            "SSE timeout retains a reconnect cursor",
            "all-skipped coverage is unscored and never zero",
            "report enqueue creates exactly one queued job and processing projection",
        ],
        "cross_state_test_nodes": [
            "tests/unit/test_interview_plan_api.py::test_start_rejects_a_historical_revision_and_returns_latest_winner",
            "tests/unit/test_interview_event_stream.py::test_pending_sse_times_out_with_reconnect_cursor",
            "tests/unit/test_report_coverage.py::test_all_skipped_is_unscored_and_never_fabricates_zero",
            "tests/integration/postgres/test_report_jobs.py::test_enqueue_report_request_creates_job_and_processing_report",
        ],
    },
)


def _covered_pairs(axes: dict[str, str]) -> set[tuple[str, str, str, str]]:
    return {
        (left, right, axes[left], axes[right])
        for left, right in RISK_PAIR_FAMILIES
    }


def _all_required_pairs() -> set[tuple[str, str, str, str]]:
    return {
        (left, right, left_value, right_value)
        for left, right in RISK_PAIR_FAMILIES
        for left_value in AXES[left]
        for right_value in AXES[right]
    }


def _candidate_axes() -> Iterable[dict[str, str]]:
    names = tuple(AXES)
    for values in itertools.product(*(AXES[name] for name in names)):
        yield dict(zip(names, values, strict=True))


def _test_nodes_for_axes(axes: dict[str, str]) -> list[str]:
    return sorted({TEST_NODE_BY_VALUE[name][value] for name, value in axes.items()})


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=1)
def build_matrix() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    uncovered = _all_required_pairs()

    for scenario in P0_SCENARIOS:
        row = {
            **scenario,
            "priority": "P0",
            "selection_kind": "manual_state_machine_cross",
        }
        row["test_nodes"] = sorted(
            set(_test_nodes_for_axes(row["axes"]))
            | set(row["cross_state_test_nodes"])
        )
        rows.append(row)
        uncovered -= _covered_pairs(row["axes"])

    candidates = list(_candidate_axes())
    pairwise_index = 1
    while uncovered:
        best_axes: dict[str, str] | None = None
        best_pairs: set[tuple[str, str, str, str]] = set()
        for axes in candidates:
            gained = _covered_pairs(axes) & uncovered
            if len(gained) > len(best_pairs):
                best_axes = axes
                best_pairs = gained
        if best_axes is None or not best_pairs:
            raise RuntimeError(f"unable to cover {len(uncovered)} required risk pairs")
        rows.append(
            {
                "id": f"pw-{pairwise_index:02d}",
                "title": "deterministic greedy risk-pair coverage row",
                "priority": "P1",
                "selection_kind": "risk_pairwise_greedy",
                "axes": best_axes,
                "expected_invariants": [
                    "all selected axis-value contracts retain their independently executable regression nodes"
                ],
                "cross_state_test_nodes": [],
                "test_nodes": _test_nodes_for_axes(best_axes),
            }
        )
        uncovered -= best_pairs
        pairwise_index += 1

    unique_nodes = sorted(
        {node for row in rows for node in row["test_nodes"]}
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": "t60-combination-matrix-v1",
        "builder_version": BUILDER_VERSION,
        "generation_is_deterministic": True,
        "full_cartesian_product_required": False,
        "axes": {name: list(values) for name, values in AXES.items()},
        "risk_pair_families": [list(pair) for pair in RISK_PAIR_FAMILIES],
        "required_risk_pair_count": len(_all_required_pairs()),
        "covered_risk_pair_count": len(
            set().union(*(_covered_pairs(row["axes"]) for row in rows))
        ),
        "scenario_count": len(rows),
        "manual_p0_scenario_count": len(P0_SCENARIOS),
        "unique_test_node_count": len(unique_nodes),
        "unique_test_nodes": unique_nodes,
        "scenarios": rows,
    }
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


def validate_matrix(payload: dict[str, Any], *, root: Path = Path(".")) -> None:
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["axes"] == {name: list(values) for name, values in AXES.items()}
    assert payload["manual_p0_scenario_count"] == 4
    rows = payload["scenarios"]
    assert len(rows) == payload["scenario_count"]
    assert len({row["id"] for row in rows}) == len(rows)
    assert sum(row["priority"] == "P0" for row in rows) == 4
    assert all(row["axes"]["memory_mode"] == "disabled" for row in rows)

    observed_values = {name: set() for name in AXES}
    observed_pairs: set[tuple[str, str, str, str]] = set()
    observed_nodes: set[str] = set()
    for row in rows:
        assert set(row["axes"]) == set(AXES)
        assert row["test_nodes"]
        for name, value in row["axes"].items():
            assert value in AXES[name]
            observed_values[name].add(value)
        observed_pairs |= _covered_pairs(row["axes"])
        observed_nodes.update(row["test_nodes"])

    assert observed_values == {name: set(values) for name, values in AXES.items()}
    assert observed_pairs >= _all_required_pairs()
    assert payload["covered_risk_pair_count"] == len(_all_required_pairs())
    assert payload["unique_test_nodes"] == sorted(observed_nodes)
    assert payload["unique_test_node_count"] == len(observed_nodes)

    for node in observed_nodes:
        file_name, test_name = node.split("::", 1)
        source_path = root / file_name
        assert source_path.is_file(), node
        base_name = test_name.split("[", 1)[0]
        source = source_path.read_text(encoding="utf-8")
        assert f"def {base_name}(" in source, node

    without_hash = dict(payload)
    recorded_hash = without_hash.pop("canonical_sha256")
    assert recorded_hash == _canonical_sha256(without_hash)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--print-json", action="store_true")
    args = parser.parse_args()

    payload = build_matrix()
    validate_matrix(payload)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"matrix drift: rebuild {args.output}")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    if args.print_json:
        print(rendered, end="")
    else:
        print(
            f"{payload['scenario_count']} scenarios, "
            f"{payload['required_risk_pair_count']} risk pairs, "
            f"{payload['unique_test_node_count']} unique test nodes, "
            f"sha256={payload['canonical_sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
