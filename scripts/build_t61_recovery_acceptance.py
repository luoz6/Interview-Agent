from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "interview-quality-v1-t61-recovery-acceptance-v1"
ACCEPTANCE_ID = "t61-recovery-acceptance-v1"
DEFAULT_OUTPUT = Path(
    "tests/golden/interview_quality_v1/t61-recovery-acceptance-v1.json"
)


REQUIREMENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "T61-R01",
        "requirement": "plan revision concurrent writes serialize one winner",
        "stable_evidence_codes": ["plan_revision_conflict"],
        "test_nodes": [
            "tests/test_postgres_plan_revision_store.py::test_postgres_expected_revision_serializes_concurrent_writers",
        ],
    },
    {
        "id": "T61-R02",
        "requirement": "duplicate session start request replays one session",
        "stable_evidence_codes": [
            "same_session_replay",
            "session_start_request_conflict",
        ],
        "test_nodes": [
            "tests/test_interview_plan_api.py::test_duplicate_session_start_request_replays_one_business_session",
            "tests/test_interview_plan_api.py::test_session_start_request_id_reuse_with_changed_contract_is_conflict",
            "tests/test_interview_plan_api.py::test_session_start_request_id_reuse_with_new_revision_is_conflict",
            "tests/test_session_service.py::test_duplicate_server_session_identity_is_thread_safe",
            "tests/test_stage38_postgres_api_contract.py::test_duplicate_start_replays_after_session_store_reinstantiation",
        ],
    },
    {
        "id": "T61-R03",
        "requirement": "decision lease loss fences stale completion and bounds retry",
        "stable_evidence_codes": [
            "answer_complete",
            "decision_store_conflict",
            "invalid_output",
            "provider_timeout",
            "timeout",
        ],
        "test_nodes": [
            "tests/test_decision_store.py::test_decision_fencing_rejects_late_worker_and_failure_creates_bounded_retry",
            "tests/test_postgres_decision_store.py::test_postgres_decision_unique_prepare_lease_fencing_and_retry",
        ],
    },
    {
        "id": "T61-R04",
        "requirement": "generation lease loss fences stale mutations and preserves the SSE cursor",
        "stable_evidence_codes": [
            "generation_lease_lost",
            "generation_reset",
            "reconnect",
        ],
        "test_nodes": [
            "tests/test_interview_event_stream.py::test_chunk_sse_has_replay_cursor",
            "tests/test_interview_event_stream.py::test_pending_sse_times_out_with_reconnect_cursor",
            "tests/test_interview_event_stream.py::test_reset_event_precedes_replacement_chunks",
            "tests/test_interview_generation_store.py::test_expired_attempt_is_replaced_with_reset_event",
            "tests/test_interview_generation_store.py::test_reclaimed_attempt_rejects_every_stale_mutation",
        ],
    },
    {
        "id": "T61-R05",
        "requirement": "report job lease is fenced through artifact commit",
        "stable_evidence_codes": [
            "completed",
            "job_fencing_token_inactive",
            "report_lease_fenced",
        ],
        "test_nodes": [
            "tests/test_postgres_report_artifact_store.py::test_postgres_publish_completes_review_run_in_same_transaction",
            "tests/test_report_jobs.py::test_report_lease_token_fences_stale_worker",
            "tests/test_report_jobs.py::test_terminal_transitions_are_fenced_against_reclaimed_worker",
        ],
    },
    {
        "id": "T61-R06",
        "requirement": "failure before artifact insert leaves no partial publication",
        "stable_evidence_codes": [
            "injected_failure_before_artifact",
            "transaction_rolled_back",
        ],
        "test_nodes": [
            "tests/test_postgres_report_artifact_store.py::test_postgres_artifact_publish_rolls_back_all_steps[before_artifact]",
            "tests/test_report_artifact_store.py::test_publish_is_atomic_when_any_commit_step_fails[before_artifact]",
        ],
    },
    {
        "id": "T61-R07",
        "requirement": "artifact insert followed by failure before head switch rolls back",
        "stable_evidence_codes": [
            "injected_failure_artifact",
            "transaction_rolled_back",
        ],
        "test_nodes": [
            "tests/test_postgres_report_artifact_store.py::test_postgres_artifact_publish_rolls_back_all_steps[artifact]",
            "tests/test_report_artifact_store.py::test_publish_is_atomic_when_any_commit_step_fails[artifact]",
        ],
    },
    {
        "id": "T61-R08",
        "requirement": "head switch followed by failure before job completion rolls back",
        "stable_evidence_codes": [
            "injected_failure_head",
            "transaction_rolled_back",
        ],
        "test_nodes": [
            "tests/test_postgres_report_artifact_store.py::test_postgres_artifact_publish_rolls_back_all_steps[head]",
            "tests/test_report_artifact_store.py::test_publish_is_atomic_when_any_commit_step_fails[head]",
        ],
    },
    {
        "id": "T61-R09",
        "requirement": "job completion followed by review session or outbox failure rolls back",
        "stable_evidence_codes": [
            "injected_failure_job",
            "injected_failure_review_run",
            "injected_failure_session",
            "outbox_write_failed",
            "transaction_rolled_back",
        ],
        "test_nodes": [
            "tests/test_postgres_report_artifact_store.py::test_postgres_artifact_publish_rolls_back_all_steps[job]",
            "tests/test_postgres_report_artifact_store.py::test_postgres_artifact_publish_rolls_back_all_steps[review_run]",
            "tests/test_postgres_report_artifact_store.py::test_postgres_artifact_publish_rolls_back_all_steps[session]",
            "tests/test_postgres_session_store.py::test_outbox_failure_rolls_back_state_and_messages",
            "tests/test_review_workflow_store.py::test_stale_report_lease_rolls_back_all_final_projections",
        ],
    },
    {
        "id": "T61-R10",
        "requirement": "commit success with lost response replays the same artifact by source job and hash",
        "stable_evidence_codes": [
            "replayed_job_payload_conflicts",
            "same_artifact_sha256",
            "same_source_job",
        ],
        "test_nodes": [
            "tests/test_postgres_report_artifact_store.py::test_postgres_publish_replays_after_response_loss_by_source_job_and_hash",
            "tests/test_report_artifact_store.py::test_artifact_publish_is_immutable_monotonic_and_replay_idempotent",
        ],
    },
    {
        "id": "T61-R11",
        "requirement": "failed rescore preserves the old active artifact",
        "stable_evidence_codes": [
            "active_artifact_preserved",
            "provider_timeout",
        ],
        "test_nodes": [
            "tests/test_postgres_report_artifact_store.py::test_postgres_artifact_history_active_pointer_and_failed_requeue",
            "tests/test_report_artifact_store.py::test_failed_job_keeps_old_active_and_requeue_reuses_job",
        ],
    },
    {
        "id": "T61-R12",
        "requirement": "active artifact source job and latest failed job remain visible together",
        "stable_evidence_codes": [
            "active_artifact_visible",
            "latest_failed_job_visible",
        ],
        "test_nodes": [
            "tests/test_report_artifact_api.py::test_report_version_endpoints_keep_active_artifact_when_rescore_fails",
        ],
    },
    {
        "id": "T61-R13",
        "requirement": "multiple rescores preserve history and enforce one active job per session",
        "stable_evidence_codes": [
            "active_report_job_unique",
            "monotonic_artifact_revision",
        ],
        "test_nodes": [
            "tests/test_postgres_report_artifact_store.py::test_postgres_multiple_rescores_keep_history_and_one_active_job",
        ],
    },
    {
        "id": "T61-R14",
        "requirement": "orphan report jobs are projected and repaired deterministically",
        "stable_evidence_codes": [
            "report_job_missing",
            "report_job_repaired",
        ],
        "test_nodes": [
            "tests/test_report_jobs.py::test_repair_orphan_processing_reports_enqueues_missing_job",
            "tests/test_report_orphan_recovery.py::test_stale_processing_record_without_job_is_projected_as_orphaned",
        ],
    },
    {
        "id": "T61-R15",
        "requirement": "command replay after service restart produces no duplicate business output",
        "stable_evidence_codes": [
            "command_replay",
            "persisted_decision_replay",
            "single_business_output",
        ],
        "test_nodes": [
            "tests/test_durable_interview_graph.py::test_adaptive_graph_routes_only_from_persisted_decision_and_replays_after_crash",
            "tests/test_durable_interview_graph.py::test_conflicted_command_replay_is_idempotent",
            "tests/test_langgraph_recovery_postgres.py::test_restart_recovers_without_duplicate_business_output",
        ],
    },
    {
        "id": "T61-R16",
        "requirement": "maximum retry terminates safely",
        "stable_evidence_codes": [
            "generation_retry_exhausted",
            "provider_unavailable",
            "report_retry_exhausted",
        ],
        "test_nodes": [
            "tests/test_durable_interview_graph.py::test_third_generation_failure_safely_advances",
            "tests/test_report_jobs.py::test_retryable_failure_marks_retrying_until_max_attempts",
        ],
    },
    {
        "id": "T61-R17",
        "requirement": "bounded guards prevent infinite loops",
        "stable_evidence_codes": [
            "followup_limit_reached",
            "generation_stream_event_limit",
            "repeated_state",
        ],
        "test_nodes": [
            "tests/test_durable_interview_graph.py::test_followup_guard_detects_same_state_and_action_repetition",
            "tests/test_durable_interview_graph.py::test_followup_guard_has_stable_fail_closed_reasons",
            "tests/test_durable_interview_graph.py::test_graph_derived_two_followup_limit_makes_zero_decision_provider_calls",
            "tests/test_durable_interview_graph.py::test_stream_event_limit_fails_attempt_closed_with_diagnostic_code",
        ],
    },
)


ACCEPTANCE_INVARIANTS: tuple[dict[str, Any], ...] = (
    {
        "id": "T61-A01",
        "invariant": "the same command creates at most one business side effect",
        "requirement_ids": ["T61-R02", "T61-R03", "T61-R15"],
    },
    {
        "id": "T61-A02",
        "invariant": "the same source job creates at most one artifact revision",
        "requirement_ids": ["T61-R10", "T61-R13"],
    },
    {
        "id": "T61-A03",
        "invariant": "a stale worker cannot overwrite a newer result",
        "requirement_ids": ["T61-R03", "T61-R04", "T61-R05"],
    },
    {
        "id": "T61-A04",
        "invariant": "every recovery path has stable coded evidence",
        "requirement_ids": [item["id"] for item in REQUIREMENTS],
    },
)


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_acceptance() -> dict[str, Any]:
    requirements = [dict(item) for item in REQUIREMENTS]
    unique_test_nodes = sorted(
        {node for item in requirements for node in item["test_nodes"]}
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "acceptance_id": ACCEPTANCE_ID,
        "plan_task": "T61",
        "postgresql_required": True,
        "skip_policy": "forbidden",
        "provider_calls_expected": 0,
        "requirement_count": len(requirements),
        "acceptance_invariant_count": len(ACCEPTANCE_INVARIANTS),
        "unique_test_node_count": len(unique_test_nodes),
        "requirements": requirements,
        "acceptance_invariants": [dict(item) for item in ACCEPTANCE_INVARIANTS],
        "unique_test_nodes": unique_test_nodes,
    }
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


def validate_acceptance(
    payload: dict[str, Any], *, root: Path | None = None
) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected T61 acceptance schema")
    if payload.get("acceptance_id") != ACCEPTANCE_ID:
        raise ValueError("unexpected T61 acceptance id")
    if payload.get("plan_task") != "T61":
        raise ValueError("T61 acceptance must target T61")
    if payload.get("postgresql_required") is not True:
        raise ValueError("T61 acceptance must require PostgreSQL")
    if payload.get("skip_policy") != "forbidden":
        raise ValueError("T61 acceptance must forbid skips")
    if payload.get("provider_calls_expected") != 0:
        raise ValueError("T61 deterministic acceptance must not call a Provider")

    requirements = payload.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != 17:
        raise ValueError("T61 acceptance must map all 17 requirements")
    expected_ids = [f"T61-R{index:02d}" for index in range(1, 18)]
    actual_ids = [item.get("id") for item in requirements]
    if actual_ids != expected_ids:
        raise ValueError("T61 requirement ids must be complete and ordered")

    code_pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for item in requirements:
        if not str(item.get("requirement", "")).strip():
            raise ValueError(f"{item['id']} requirement text is missing")
        codes = item.get("stable_evidence_codes")
        if not isinstance(codes, list) or not codes:
            raise ValueError(f"{item['id']} stable evidence codes are missing")
        if codes != sorted(set(codes)) or any(
            not code_pattern.fullmatch(str(code)) for code in codes
        ):
            raise ValueError(f"{item['id']} stable evidence codes are invalid")
        nodes = item.get("test_nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError(f"{item['id']} test nodes are missing")
        if nodes != sorted(set(nodes)):
            raise ValueError(f"{item['id']} test nodes must be unique and sorted")

    all_nodes = sorted(
        {node for item in requirements for node in item["test_nodes"]}
    )
    if payload.get("unique_test_nodes") != all_nodes:
        raise ValueError("T61 unique test node projection is stale")
    if payload.get("unique_test_node_count") != len(all_nodes):
        raise ValueError("T61 unique test node count is stale")
    if payload.get("requirement_count") != 17:
        raise ValueError("T61 requirement count is stale")

    invariants = payload.get("acceptance_invariants")
    if not isinstance(invariants, list) or len(invariants) != 4:
        raise ValueError("T61 must map all four acceptance invariants")
    if [item.get("id") for item in invariants] != [
        "T61-A01",
        "T61-A02",
        "T61-A03",
        "T61-A04",
    ]:
        raise ValueError("T61 acceptance invariant ids are incomplete")
    known_ids = set(actual_ids)
    for invariant in invariants:
        linked = invariant.get("requirement_ids")
        if not linked or not set(linked).issubset(known_ids):
            raise ValueError(f"{invariant['id']} has invalid requirement links")

    expected_hash = payload.get("canonical_sha256")
    unhashed = dict(payload)
    unhashed.pop("canonical_sha256", None)
    if expected_hash != _canonical_sha256(unhashed):
        raise ValueError("T61 acceptance canonical hash mismatch")

    if root is not None:
        for node in all_nodes:
            file_name, separator, test_name = node.partition("::")
            if not separator or not test_name:
                raise ValueError(f"invalid pytest node: {node}")
            if not (root / file_name).is_file():
                raise ValueError(f"pytest node file is missing: {node}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    payload = build_acceptance()
    validate_acceptance(payload, root=root)
    output = args.output if args.output.is_absolute() else root / args.output
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("checked-in T61 acceptance manifest is stale")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8", newline="\n")
    print(payload["canonical_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
