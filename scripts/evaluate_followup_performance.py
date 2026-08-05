from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.followup_performance import (
    FollowupPerformanceArtifact,
    build_synthetic_performance_artifact,
    evaluate_followup_performance,
)
from app.services.interview_quality_gate import load_gate_config
from app.services.interview_quality_provider_authorization import (
    load_provider_authorization,
)
from app.services.report_eval_artifacts import EvaluationArtifactStore


DEFAULT_GATE = ROOT / "config" / "interview_quality_v1_gate.json"
DEFAULT_AUTHORIZATION = (
    ROOT / "config" / "interview_quality_v1_provider_authorization.json"
)
DEFAULT_OUT = ROOT / "tmp" / "interview-quality-v1-provider-runs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate T37 follow-up latency, usage, cost, and recovery gates"
    )
    parser.add_argument(
        "--mode",
        choices=("fixture-replay", "saved-replay"),
        default="fixture-replay",
    )
    parser.add_argument("--responses", type=Path)
    parser.add_argument(
        "--source-capture",
        type=Path,
        help="local redacted Provider capture referenced by a saved replay",
    )
    parser.add_argument("--gate-config", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id")
    parser.add_argument("--samples-per-cohort", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "saved-replay" and args.responses is None:
        raise SystemExit("--responses is required for saved-replay")
    if args.mode == "fixture-replay" and args.responses is not None:
        raise SystemExit("--responses is only valid for saved-replay")
    if args.mode == "fixture-replay" and args.source_capture is not None:
        raise SystemExit("--source-capture is only valid for saved-replay")
    if args.samples_per_cohort < 30:
        raise SystemExit("--samples-per-cohort must be at least 30")

    gate_path = args.gate_config.resolve()
    authorization_path = args.authorization.resolve()
    authorization = load_provider_authorization(authorization_path)
    gate_config = load_gate_config(gate_path)
    run_id = args.run_id or datetime.now(timezone.utc).strftime(
        "followup-t37-%Y%m%dT%H%M%SZ"
    )
    output_root = args.out.resolve()
    run_dir = output_root / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(
            f"run directory already exists; choose a new --run-id: {run_dir}"
        )

    response_path = args.responses.resolve() if args.responses is not None else None
    try:
        artifact = (
            _load_saved_artifact(response_path)
            if response_path is not None
            else build_synthetic_performance_artifact(
                samples_per_cohort=args.samples_per_cohort
            )
        )
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise SystemExit(f"invalid performance artifact: {exc}") from exc
    if args.mode == "saved-replay" and artifact.source_kind == "synthetic_fixture":
        raise SystemExit("saved-replay requires real saved or live Provider evidence")
    if args.mode == "saved-replay" and args.source_capture is None:
        raise SystemExit("--source-capture is required for saved-replay")
    source_capture_path = (
        args.source_capture.resolve() if args.source_capture is not None else None
    )

    revision, tree, dirty = _implementation_identity()
    manifest = {
        "schema_version": "followup-performance-run-v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": "T37",
        "mode": args.mode,
        "source_kind": artifact.source_kind,
        "capture_status": artifact.capture_status,
        "sample_count": len(artifact.samples),
        "gate_config_sha256": _sha256(gate_path),
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": _sha256(authorization_path),
        "authorized_provider": authorization.provider.name,
        "authorized_model": authorization.provider.model_id,
        "implementation_revision": revision,
        "implementation_tree": tree,
        "implementation_worktree_dirty": dirty,
        "input_artifact_sha256": (
            _sha256(response_path) if response_path is not None else None
        ),
        "source_capture_sha256": (
            _sha256(source_capture_path)
            if source_capture_path is not None and source_capture_path.is_file()
            else None
        ),
        "provider_called": False,
        "first_data_request_sent": False,
        "hard_stop_conditions": [],
        "decision": "RUNNING",
    }
    store = EvaluationArtifactStore.create(
        root=output_root,
        run_id=run_id,
        manifest=manifest,
    )

    mismatch = _authorization_mismatch(
        artifact,
        authorization,
        source_capture_path=source_capture_path,
    )
    if mismatch is not None:
        blocked = {
            "schema_version": "followup-performance-metrics-v1",
            "sample_count": len(artifact.samples),
            "source_kind": artifact.source_kind,
            "capture_status": artifact.capture_status,
            "engineering_status": "PASS",
            "quality_status": "BLOCKED_PROVIDER_OR_MODEL_MISMATCH",
            "overall_status": "BLOCKED",
            "automated_gate_status": "NOT_RUN",
            "gate_results": [],
            "cohort_summaries": [],
            "fixed_adaptive_same_path_comparisons": [],
            "session_usage": [],
            "anomaly_cases": [],
            "hard_stop_conditions": [mismatch],
            "fixed_decision_latency_baseline": None,
            "fixed_decision_latency_baseline_reason": (
                "fixed_v1 has no Decision stage; a zero baseline is prohibited"
            ),
        }
        manifest.update(
            {
                "hard_stop_conditions": [mismatch],
                "decision": f"BLOCKED_{mismatch}",
                "engineering_status": "PASS",
                "quality_status": blocked["quality_status"],
                "overall_status": "BLOCKED",
            }
        )
        store.write_metrics(blocked)
        store.write_report(_render_report(blocked))
        store.write_manifest(manifest)
        print(json.dumps(_console_result(store.run_dir, blocked), ensure_ascii=False))
        return 2

    metrics = evaluate_followup_performance(artifact, gate_config=gate_config)
    manifest.update(
        {
            "decision": metrics["overall_status"],
            "engineering_status": metrics["engineering_status"],
            "quality_status": metrics["quality_status"],
            "overall_status": metrics["overall_status"],
        }
    )
    _write_json(store.run_dir / "performance-artifact.json", artifact.model_dump(mode="json"))
    store.write_metrics(metrics)
    store.write_report(_render_report(metrics))
    store.write_manifest(manifest)
    print(json.dumps(_console_result(store.run_dir, metrics), ensure_ascii=False))
    if metrics["engineering_status"] == "FAIL" or metrics["quality_status"] == "FAIL":
        return 1
    if metrics["engineering_status"] == "BLOCKED":
        return 2
    return 0


def _load_saved_artifact(path: Path | None) -> FollowupPerformanceArtifact:
    if path is None:  # pragma: no cover - guarded by parser contract.
        raise ValueError("saved artifact path is required")
    return FollowupPerformanceArtifact.model_validate_json(path.read_text(encoding="utf-8"))


def _authorization_mismatch(
    artifact,
    authorization,
    *,
    source_capture_path: Path | None,
) -> str | None:
    if artifact.source_kind == "synthetic_fixture":
        return None
    if (
        source_capture_path is None
        or not source_capture_path.is_file()
        or _sha256(source_capture_path) != artifact.source_capture_sha256
    ):
        return "EVIDENCE_PERSISTENCE_UNAVAILABLE"
    if artifact.provider_name != authorization.provider.name:
        return "PROVIDER_OR_MODEL_MISMATCH"
    if artifact.model_id != authorization.provider.model_id:
        return "MODEL_VERSION_DRIFT"
    return None


def _render_report(metrics: dict) -> str:
    lines = [
        "# T37 Follow-up Performance Evaluation",
        "",
        f"- Engineering status: `{metrics['engineering_status']}`",
        f"- Quality status: `{metrics['quality_status']}`",
        f"- Overall status: `{metrics['overall_status']}`",
        f"- Source: `{metrics['source_kind']}`",
        f"- Sample count: `{metrics['sample_count']}`",
        "- fixed_v1 Decision baseline: `None` (the stage does not exist)",
        "",
        "| Metric | Cohort | Status | Actual | Effective threshold | Sample |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for item in metrics["gate_results"]:
        cohort = ", ".join(
            f"{key}={value}" for key, value in item.get("cohort", {}).items()
        ) or "all"
        threshold = (
            "record-only"
            if item["effective_threshold"] is None
            else f"{item['effective_threshold']:g}"
        )
        lines.append(
            f"| `{item['metric_key']}` | {cohort} | **{item['status']}** | "
            f"{item['actual']:g} | {threshold} | "
            f"{item['sample_size']}/{item['minimum_sample_size']} |"
        )
    if metrics["hard_stop_conditions"]:
        lines.extend(
            [
                "",
                "## Hard stops",
                "",
                *[f"- `{item}`" for item in metrics["hard_stop_conditions"]],
            ]
        )
    lines.extend(
        [
            "",
            "Synthetic fixture timing proves evaluator behavior only. It is never "
            "eligible for a real Provider Quality PASS.",
        ]
    )
    return "\n".join(lines) + "\n"


def _implementation_identity() -> tuple[str | None, str | None, bool]:
    def git(*arguments: str) -> str | None:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    revision = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    dirty = bool(git("status", "--porcelain"))
    return revision, tree, dirty


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _console_result(run_dir: Path, metrics: dict) -> dict:
    return {
        "run_dir": str(run_dir),
        "engineering_status": metrics["engineering_status"],
        "quality_status": metrics["quality_status"],
        "overall_status": metrics["overall_status"],
        "sample_count": metrics["sample_count"],
        "hard_stop_conditions": metrics["hard_stop_conditions"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
