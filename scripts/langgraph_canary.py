from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.config import (
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
    payload = snapshot.model_dump(mode="json")
    privacy = audit_runtime_control_payloads([payload])
    if privacy["status"] != "PASS":
        raise ValueError("canary artifact privacy audit failed")
    (output_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# LangGraph Canary Snapshot",
        "",
        f"Recommendation: {snapshot.recommendation}",
        f"Generated at: {snapshot.generated_at}",
        f"Window minutes: {snapshot.window_minutes}",
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
    (output_dir / "result.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def build_snapshot(*, window_minutes: int) -> WorkflowCanarySnapshot:
    service = PostgresLangGraphCanaryStatusService(
        dsn=get_postgres_dsn(),
        table_prefix=get_runtime_table_prefix(),
    )
    return service.snapshot(
        window_minutes=window_minutes,
        interview_rollout_percent=get_interview_langgraph_rollout_percent(),
        review_rollout_percent=get_report_langgraph_rollout_percent(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read and evaluate privacy-safe LangGraph canary status"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "evaluate"):
        command = subparsers.add_parser(name)
        command.add_argument("--window-minutes", type=int, default=60)
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
        command.add_argument("--minimum-sample-size", type=int, default=10)
    args = parser.parse_args()
    snapshot = build_snapshot(window_minutes=args.window_minutes)
    if args.command == "evaluate":
        snapshot = evaluate_canary(
            snapshot,
            thresholds=CanaryThresholds(
                minimum_sample_size=args.minimum_sample_size
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
