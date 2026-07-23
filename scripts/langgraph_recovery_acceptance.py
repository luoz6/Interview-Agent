from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter


CHECKS = (
    "command_commit_rpo_zero",
    "candidate_projection_idempotent",
    "generation_prepare_recovery",
    "partial_stream_reset",
    "completed_generation_reuse",
    "projection_version_reuse",
    "report_enqueue_idempotent",
    "retry_timer_not_early",
    "duplicate_command_one_message",
    "privacy_allowlist",
)


def build_acceptance_result(
    *,
    status: str,
    duration_seconds: float,
    test_count: int,
    commit_id: str,
) -> dict:
    return {
        "schema_version": "langgraph-recovery-acceptance-v1",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit_id": commit_id,
        "duration_seconds": round(duration_seconds, 3),
        "test_count": test_count,
        "rpo": "zero_acknowledged_commands" if status == "PASS" else "unverified",
        "privacy_result": "PASS" if status == "PASS" else "unverified",
        "checks": [
            {"name": name, "status": status} for name in CHECKS
        ],
    }


def write_artifacts(result: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# LangGraph Recovery Acceptance",
        "",
        f"Status: {result['status']}",
        f"Commit: {result['commit_id']}",
        f"Duration seconds: {result['duration_seconds']}",
        f"Test count: {result['test_count']}",
        f"Privacy: {result['privacy_result']}",
        "",
        "## Checks",
        "",
    ]
    lines.extend(
        f"- {item['name']}: {item['status']}"
        for item in result["checks"]
    )
    (output_dir / "result.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _commit_id() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_acceptance(*, timeout: int, output_dir: Path) -> dict:
    started = perf_counter()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_langgraph_recovery_postgres.py",
                "-q",
                "-m",
                "langgraph_recovery",
            ],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(r"(\d+) passed", output)
        test_count = int(match.group(1)) if match else 0
        status = "PASS" if completed.returncode == 0 else "FAIL"
    except subprocess.TimeoutExpired:
        test_count = 0
        status = "FAIL"
    result = build_acceptance_result(
        status=status,
        duration_seconds=perf_counter() - started,
        test_count=test_count,
        commit_id=_commit_id(),
    )
    write_artifacts(result, output_dir)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/langgraph-recovery-acceptance"),
    )
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    result = run_acceptance(
        timeout=args.timeout, output_dir=args.output_dir
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
