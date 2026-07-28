from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from time import perf_counter


SCHEMA_VERSION = "langgraph-dual-release-acceptance-v1"


@dataclass(frozen=True)
class AcceptanceCommand:
    label: str
    checks: tuple[str, ...]
    argv: tuple[str, ...]
    env: dict[str, str] | None = None


def _pytest(*paths: str, marker: str | None = None) -> tuple[str, ...]:
    command = [sys.executable, "-m", "pytest", *paths, "-q"]
    if marker:
        command.extend(["-m", marker])
    return tuple(command)


def focused_commands() -> list[AcceptanceCommand]:
    return [
        AcceptanceCommand(
            "interview-focused",
            (
                "interview_focused_contracts",
                "out_of_order_command_rejected",
            ),
            _pytest(
                "tests/test_durable_interview_state.py",
                "tests/test_durable_interview_graph.py",
                "tests/test_interview_workflow_store.py",
                "tests/test_interview_generation_store.py",
                "tests/test_interview_workflow_consumer.py",
                "tests/test_interview_event_stream.py",
                "tests/test_dual_langgraph_rollout.py",
            ),
        ),
        AcceptanceCommand(
            "interview-postgres-recovery",
            ("interview_postgres_restart_recovery",),
            _pytest(
                "tests/test_langgraph_recovery_postgres.py",
                marker="langgraph_recovery",
            ),
        ),
        AcceptanceCommand(
            "review-focused",
            (
                "review_focused_regression",
                "wrong_engine_events_discarded",
            ),
            _pytest(
                "tests/test_durable_review_runtime_contract.py",
                "tests/test_durable_review_state.py",
                "tests/test_durable_review_graph.py",
                "tests/test_review_workflow.py",
                "tests/test_review_workflow_consumer.py",
                "tests/test_review_workflow_store.py",
                "tests/test_report_worker.py",
                "tests/test_report_jobs.py",
            ),
        ),
        AcceptanceCommand(
            "review-postgres-recovery",
            ("review_postgres_restart_recovery",),
            _pytest(
                "tests/test_durable_review_recovery_postgres.py",
                marker="langgraph_review_recovery",
            ),
        ),
        AcceptanceCommand(
            "dual-postgres-handoff",
            (
                "assignment_matrix",
                "rollback_existing_interview_resume",
                "rollback_existing_review_resume",
                "joint_postgres_handoff",
                "review_cold_start_fenced",
                "shared_saver_namespace_isolation",
            ),
            _pytest(
                "tests/test_dual_langgraph_canary_postgres.py",
                marker="langgraph_dual_canary",
            ),
        ),
        AcceptanceCommand(
            "privacy-and-maintenance",
            (
                "interview_privacy_allowlist",
                "retention_maintenance_active",
            ),
            _pytest(
                "tests/test_durable_workflow_maintenance.py",
                "tests/test_langgraph_canary_status.py",
                "tests/test_langgraph_canary_cli.py",
                "tests/test_agent_runtime_audit.py",
                "tests/test_runtime_boundary_api.py",
            ),
        ),
        *preflight_commands(),
    ]


def preflight_commands() -> list[AcceptanceCommand]:
    pairs = (
        ("zero_zero", "0", "0"),
        ("interview_only", "1", "0"),
        ("review_only", "0", "1"),
        ("joint", "1", "1"),
    )
    return [
        AcceptanceCommand(
            f"preflight-{name}",
            (f"runtime_preflight_{name}",),
            (
                sys.executable,
                "-m",
                "scripts.runtime_preflight",
                "--profile",
                "core",
            ),
            env={
                "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT": interview,
                "REPORT_LANGGRAPH_ROLLOUT_PERCENT": review,
                "INTERVIEW_RUNTIME_STORE": "postgres",
            },
        )
        for name, interview, review in pairs
    ]


def full_only_commands() -> list[AcceptanceCommand]:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    return [
        AcceptanceCommand(
            "full-python",
            ("full_python_regression",),
            _pytest(),
        ),
        AcceptanceCommand(
            "full-browser",
            (
                "interview_browser_reconnect",
                "full_browser_regression",
            ),
            (npm, "run", "test:browser"),
            env={
                "AGENT_TRACE_DIR": tempfile.mkdtemp(
                    prefix="stage45-browser-traces-"
                )
            },
        ),
    ]


def _parse_counts(output: str) -> dict[str, int]:
    passed = re.findall(r"(\d+) passed", output)
    skipped = re.findall(r"(\d+) skipped", output)
    return {
        "passed": sum(int(value) for value in passed),
        "skipped": sum(int(value) for value in skipped),
    }


def _commit_id() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_acceptance(
    *, mode: str, timeout: int, runner=subprocess.run
) -> dict:
    if mode not in {"focused", "full"}:
        raise ValueError("mode must be focused or full")
    started = perf_counter()
    commands = focused_commands()
    if mode == "full":
        commands.extend(full_only_commands())
    check_results: list[dict[str, str]] = []
    total = {"passed": 0, "skipped": 0}
    failed = False
    if not os.getenv("POSTGRES_DSN"):
        return {
            "schema_version": SCHEMA_VERSION,
            "repository_status": "FAIL",
            "operator_canary_status": "NOT_RUN",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "commit_id": _commit_id(),
            "duration_seconds": 0.0,
            "test_counts": total,
            "checks": [
                {"name": "postgres_configuration", "status": "FAIL"}
            ],
            "privacy_result": "FAIL",
        }
    for command in commands:
        environment = os.environ.copy()
        environment.update(command.env or {})
        try:
            completed = runner(
                list(command.argv),
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
            output = f"{completed.stdout}\n{completed.stderr}"
            counts = _parse_counts(output)
            total["passed"] += counts["passed"]
            total["skipped"] += counts["skipped"]
            status = "PASS" if completed.returncode == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            status = "FAIL"
        failed = failed or status == "FAIL"
        check_results.extend(
            {"name": name, "status": status} for name in command.checks
        )
        if status == "FAIL":
            break
    repository_status = "FAIL" if failed else "PASS"
    return {
        "schema_version": SCHEMA_VERSION,
        "repository_status": repository_status,
        "operator_canary_status": "NOT_RUN",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit_id": _commit_id(),
        "duration_seconds": round(perf_counter() - started, 3),
        "test_counts": total,
        "checks": check_results,
        "privacy_result": "PASS" if not failed else "FAIL",
    }


def write_artifacts(result: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# LangGraph Dual-Workflow Repository Acceptance",
        "",
        f"Repository status: {result['repository_status']}",
        f"Operator canary: {result['operator_canary_status']}",
        f"Commit: {result['commit_id']}",
        f"Duration seconds: {result['duration_seconds']}",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("focused", "full"), default="focused")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/langgraph-dual-workflow-acceptance"),
    )
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    result = run_acceptance(mode=args.mode, timeout=args.timeout)
    write_artifacts(result, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["repository_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
