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
from typing import Callable


RECOVERY_CHECKS = (
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
DUAL_SCHEMA_VERSION = "langgraph-dual-release-acceptance-v1"


def _commit_id() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _write_artifacts(
    result: dict,
    output_dir: Path,
    *,
    markdown_lines: list[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "result.md").write_text(
        "\n".join(markdown_lines) + "\n",
        encoding="utf-8",
    )


def build_recovery_result(
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
            {"name": name, "status": status} for name in RECOVERY_CHECKS
        ],
    }


def write_recovery_artifacts(result: dict, output_dir: Path) -> None:
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
        f"- {item['name']}: {item['status']}" for item in result["checks"]
    )
    _write_artifacts(result, output_dir, markdown_lines=lines)


def run_recovery_acceptance(
    *,
    timeout: int,
    output_dir: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    started = perf_counter()
    test_count = 0
    status = "FAIL"
    if os.getenv("POSTGRES_DSN"):
        try:
            completed = runner(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/integration/postgres/test_langgraph_recovery_postgres.py",
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
            if completed.returncode == 0 and test_count > 0:
                status = "PASS"
        except subprocess.TimeoutExpired:
            pass
    result = build_recovery_result(
        status=status,
        duration_seconds=perf_counter() - started,
        test_count=test_count,
        commit_id=_commit_id(),
    )
    write_recovery_artifacts(result, output_dir)
    return result


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


def dual_preflight_commands() -> list[AcceptanceCommand]:
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


def dual_focused_commands() -> list[AcceptanceCommand]:
    return [
        AcceptanceCommand(
            "interview-focused",
            ("interview_focused_contracts", "out_of_order_command_rejected"),
            _pytest(
                "tests/unit/test_durable_interview_state.py",
                "tests/unit/test_durable_interview_graph.py",
                "tests/integration/postgres/test_durable_interview_graph.py",
                "tests/integration/postgres/test_interview_workflow_store.py",
                "tests/unit/test_interview_generation_store.py",
                "tests/integration/postgres/test_interview_generation_store.py",
                "tests/unit/test_interview_workflow_consumer.py",
                "tests/unit/test_interview_event_stream.py",
                "tests/unit/test_dual_langgraph_rollout.py",
            ),
        ),
        AcceptanceCommand(
            "interview-postgres-recovery",
            ("interview_postgres_restart_recovery",),
            _pytest(
                "tests/integration/postgres/test_langgraph_recovery_postgres.py",
                marker="langgraph_recovery",
            ),
        ),
        AcceptanceCommand(
            "review-focused",
            ("review_focused_regression", "wrong_engine_events_discarded"),
            _pytest(
                "tests/contracts/test_durable_review_runtime_contract.py",
                "tests/unit/test_durable_review_state.py",
                "tests/unit/test_durable_review_graph.py",
                "tests/unit/test_review_workflow.py",
                "tests/unit/test_review_workflow_consumer.py",
                "tests/integration/postgres/test_review_workflow_store.py",
                "tests/unit/test_report_worker.py",
                "tests/integration/postgres/test_report_worker.py",
                "tests/integration/postgres/test_report_jobs.py",
            ),
        ),
        AcceptanceCommand(
            "review-postgres-recovery",
            ("review_postgres_restart_recovery",),
            _pytest(
                "tests/integration/postgres/test_durable_review_recovery_postgres.py",
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
                "tests/integration/postgres/test_dual_langgraph_canary_postgres.py",
                marker="langgraph_dual_canary",
            ),
        ),
        AcceptanceCommand(
            "privacy-and-maintenance",
            ("interview_privacy_allowlist", "retention_maintenance_active"),
            _pytest(
                "tests/unit/test_durable_workflow_maintenance.py",
                "tests/unit/test_langgraph_canary_status.py",
                "tests/contracts/test_langgraph_canary_cli.py",
                "tests/contracts/test_agent_runtime_audit.py",
                "tests/acceptance/test_runtime_boundary_api.py",
            ),
        ),
        *dual_preflight_commands(),
    ]


def dual_full_only_commands() -> list[AcceptanceCommand]:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    return [
        AcceptanceCommand("full-python", ("full_python_regression",), _pytest()),
        AcceptanceCommand(
            "full-browser",
            ("interview_browser_reconnect", "full_browser_regression"),
            (npm, "run", "test:browser"),
            env={"AGENT_TRACE_DIR": tempfile.mkdtemp(prefix="stage45-browser-traces-")},
        ),
    ]


def _parse_counts(output: str) -> dict[str, int]:
    passed = re.findall(r"(\d+) passed", output)
    skipped = re.findall(r"(\d+) skipped", output)
    return {
        "passed": sum(int(value) for value in passed),
        "skipped": sum(int(value) for value in skipped),
    }


def run_dual_acceptance(
    *,
    mode: str,
    timeout: int,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    if mode not in {"focused", "full"}:
        raise ValueError("mode must be focused or full")
    started = perf_counter()
    commands = dual_focused_commands()
    if mode == "full":
        commands.extend(dual_full_only_commands())
    check_results: list[dict[str, str]] = []
    total = {"passed": 0, "skipped": 0}
    failed = False
    if not os.getenv("POSTGRES_DSN"):
        return {
            "schema_version": DUAL_SCHEMA_VERSION,
            "repository_status": "FAIL",
            "operator_canary_status": "NOT_RUN",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "commit_id": _commit_id(),
            "duration_seconds": 0.0,
            "test_counts": total,
            "checks": [{"name": "postgres_configuration", "status": "FAIL"}],
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
        "schema_version": DUAL_SCHEMA_VERSION,
        "repository_status": repository_status,
        "operator_canary_status": "NOT_RUN",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit_id": _commit_id(),
        "duration_seconds": round(perf_counter() - started, 3),
        "test_counts": total,
        "checks": check_results,
        "privacy_result": "PASS" if not failed else "FAIL",
    }


def write_dual_artifacts(result: dict, output_dir: Path) -> None:
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
        f"- {item['name']}: {item['status']}" for item in result["checks"]
    )
    _write_artifacts(result, output_dir, markdown_lines=lines)


def recovery_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LangGraph recovery acceptance")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/langgraph-recovery-acceptance"),
    )
    args = parser.parse_args(argv)
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    result = run_recovery_acceptance(
        timeout=args.timeout,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


def dual_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dual LangGraph acceptance")
    parser.add_argument("--mode", choices=("focused", "full"), default="focused")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/langgraph-dual-workflow-acceptance"),
    )
    args = parser.parse_args(argv)
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    result = run_dual_acceptance(mode=args.mode, timeout=args.timeout)
    write_dual_artifacts(result, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["repository_status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run LangGraph acceptance profiles")
    parser.add_argument("profile", choices=("recovery", "dual"))
    args, remaining = parser.parse_known_args(argv)
    if args.profile == "recovery":
        return recovery_main(remaining)
    return dual_main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
