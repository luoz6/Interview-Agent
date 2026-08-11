from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from app.runtime.config.compatibility import (
    get_interview_langgraph_rollout_percent,
    get_postgres_dsn,
    get_report_langgraph_rollout_percent,
    get_runtime_table_prefix,
)
from app.services.langgraph_canary_status import (
    CanaryThresholds,
    PostgresLangGraphCanaryStatusService,
    WorkflowCanarySnapshot,
    evaluate_canary,
)
from scripts.audit_agent_runtime import audit_runtime_control_payloads


EXIT_BY_RECOMMENDATION = {
    "ELIGIBLE_TO_CONTINUE": 0,
    "HOLD": 2,
    "ROLL_BACK": 3,
}


def write_canary_artifacts(
    snapshot: WorkflowCanarySnapshot, output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_json = output_dir / "result.json"
    result_markdown = output_dir / "result.md"
    if result_json.exists() or result_markdown.exists():
        raise FileExistsError("canary phase artifacts already exist")
    payload = snapshot.model_dump(mode="json")
    privacy = audit_runtime_control_payloads([payload])
    if privacy["status"] != "PASS":
        raise ValueError("canary artifact privacy audit failed")
    result_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# LangGraph Canary Snapshot",
        "",
        f"Recommendation: {snapshot.recommendation}",
        f"Generated at: {snapshot.generated_at}",
        f"Observed since: {snapshot.observed_since}",
        f"Window seconds: {snapshot.window_seconds}",
        f"Phase: {snapshot.phase}",
        (
            "Rollout pair: "
            f"{snapshot.interview_rollout_percent}/"
            f"{snapshot.review_rollout_percent}"
        ),
        f"Privacy: {snapshot.privacy_audit}",
        "",
        "## Reasons",
        "",
    ]
    lines.extend(
        f"- {reason}" for reason in snapshot.reasons
    )
    lines.extend(["", "## Safe aggregate metrics", ""])
    for field_name in _MARKDOWN_METRIC_FIELDS:
        lines.append(f"- {field_name}: {getattr(snapshot, field_name)}")
    result_markdown.write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


_MARKDOWN_METRIC_FIELDS = (
    "interview_assigned_count",
    "interview_active_count",
    "interview_retrying_count",
    "interview_terminal_count",
    "review_assigned_count",
    "review_active_count",
    "review_retrying_count",
    "review_terminal_count",
    "review_failed_count",
    "outbox_pending_count",
    "outbox_retrying_count",
    "outbox_running_count",
    "oldest_unfinished_outbox_age_seconds",
    "expired_running_outbox_lease_count",
    "command_conflict_count",
    "projection_divergence_count",
    "report_commit_conflict_count",
    "workflow_thread_busy_count",
    "workflow_thread_lock_lost_count",
    "generation_lease_lost_count",
    "fenced_write_rejected_count",
    "report_lease_lost_count",
    "review_effect_busy_count",
    "review_effect_conflict_count",
    "expired_generation_lease_count",
    "expired_report_lease_count",
    "running_review_effect_count",
    "expired_review_effect_claim_count",
    "checkpoint_row_count",
    "generation_chunk_row_count",
    "review_artifact_row_count",
    "review_effect_row_count",
)


def parse_utc_timestamp(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "phase start must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise argparse.ArgumentTypeError(
            "phase start must be an ISO-8601 UTC timestamp"
        )
    return parsed.astimezone(timezone.utc)


def build_snapshot(
    *,
    window_minutes: int,
    phase=None,
    observed_since: datetime | None = None,
    lease_expiry_grace_seconds: int = 30,
) -> WorkflowCanarySnapshot:
    service = PostgresLangGraphCanaryStatusService(
        dsn=get_postgres_dsn(),
        table_prefix=get_runtime_table_prefix(),
    )
    snapshot = service.snapshot(
        window_minutes=window_minutes,
        phase=phase,
        observed_since=observed_since,
        interview_rollout_percent=get_interview_langgraph_rollout_percent(),
        review_rollout_percent=get_report_langgraph_rollout_percent(),
        lease_expiry_grace_seconds=lease_expiry_grace_seconds,
    )
    payload = snapshot.model_dump(mode="json")
    privacy = audit_runtime_control_payloads([payload])
    return snapshot.model_copy(
        update={"privacy_audit": privacy["status"]}
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read and evaluate privacy-safe LangGraph canary status"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "evaluate"):
        command = subparsers.add_parser(name)
        command.add_argument("--window-minutes", type=int, default=60)
        command.add_argument(
            "--phase",
            choices=(
                "baseline",
                "interview",
                "interview_drain",
                "review",
                "review_drain",
                "joint",
                "final_drain",
            ),
        )
        command.add_argument("--since-utc", type=parse_utc_timestamp)
        command.add_argument("--output-dir", type=Path)
        command.add_argument(
            "--external-stop-signal",
            action="append",
            default=[],
            choices=[
                "acknowledged_command_loss",
                "duplicate_business_projection",
                "public_version_regression",
                "unknown_graph_version",
            ],
        )
        command.add_argument(
            "--minimum-interview-sample", type=int, default=10
        )
        command.add_argument(
            "--minimum-review-sample", type=int, default=10
        )
        command.add_argument(
            "--max-oldest-outbox-age-seconds", type=float, default=300
        )
        command.add_argument(
            "--max-review-failure-rate", type=float, default=0.2
        )
        command.add_argument(
            "--max-workflow-thread-busy-count", type=int, default=0
        )
        command.add_argument(
            "--max-review-effect-busy-count", type=int, default=0
        )
        command.add_argument(
            "--lease-expiry-grace-seconds", type=int, default=30
        )
    args = parser.parse_args()
    if args.command == "evaluate" and (
        args.phase is None or args.since_utc is None
    ):
        parser.error("evaluate requires --phase and --since-utc")
    snapshot = build_snapshot(
        window_minutes=args.window_minutes,
        phase=args.phase,
        observed_since=args.since_utc,
        lease_expiry_grace_seconds=args.lease_expiry_grace_seconds,
    )
    if args.command == "evaluate":
        snapshot = evaluate_canary(
            snapshot,
            thresholds=CanaryThresholds(
                minimum_interview_sample=args.minimum_interview_sample,
                minimum_review_sample=args.minimum_review_sample,
                max_oldest_outbox_age_seconds=(
                    args.max_oldest_outbox_age_seconds
                ),
                max_review_failure_rate=args.max_review_failure_rate,
                max_workflow_thread_busy_count=(
                    args.max_workflow_thread_busy_count
                ),
                max_review_effect_busy_count=(
                    args.max_review_effect_busy_count
                ),
                lease_expiry_grace_seconds=(
                    args.lease_expiry_grace_seconds
                ),
            ),
            external_stop_signals=args.external_stop_signal,
        )
    payload = snapshot.model_dump(mode="json")
    if audit_runtime_control_payloads([payload])["status"] != "PASS":
        raise ValueError("canary output privacy audit failed")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if args.output_dir is not None:
        write_canary_artifacts(snapshot, args.output_dir)
    if args.command == "snapshot":
        return 0
    return EXIT_BY_RECOMMENDATION[snapshot.recommendation]


if __name__ == "__main__":
    raise SystemExit(main())
