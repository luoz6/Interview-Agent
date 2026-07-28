from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ROLLOUT_DEFAULTS = (
    "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0",
    "REPORT_LANGGRAPH_ROLLOUT_PERCENT=0",
)


def _run_pytest(paths: list[str]) -> bool:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *paths],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _rollout_defaults_unchanged() -> bool:
    dotenv = (ROOT / ".env.example").read_text(encoding="utf-8")
    return all(value in dotenv for value in ROLLOUT_DEFAULTS)


def evaluate_acceptance(
    checks: dict[str, bool],
    *,
    postgres_configured: bool,
    rollout_defaults_changed: bool,
) -> dict:
    if not postgres_configured:
        status = "BLOCKED_POSTGRES_GATE"
    elif rollout_defaults_changed:
        status = "BLOCKED_ROLLOUT_DEFAULTS"
    elif not all(checks.values()):
        status = "FAILED_REPOSITORY_GATE"
    else:
        status = "READY_FOR_AGENT_TELEMETRY_CANARY"
    return {
        "status": status,
        "operator_observation": "NOT_RUN",
        "agent_runtime_schema": "agent-runtime-v1",
        "rollout_defaults_changed": rollout_defaults_changed,
        "checks": {
            name: "PASS" if passed else "FAIL"
            for name, passed in checks.items()
        },
    }


def main() -> int:
    postgres_configured = bool(os.getenv("POSTGRES_DSN"))
    checks = {
        "runtime_unit": _run_pytest(
            [
                "tests/test_agent_runtime.py",
                "tests/test_agent_runtime_hardening.py",
            ]
        ),
        "composition": _run_pytest(
            [
                "tests/test_agent_runtime_composition.py",
                "tests/test_agent_runtime_release_contract.py",
                "tests/test_prep_service.py",
            ]
        ),
        "privacy": _run_pytest(
            [
                "tests/test_agent_trace.py",
                "tests/test_agent_runtime_audit.py",
            ]
        ),
        "postgres": (
            postgres_configured
            and _run_pytest(
                [
                    "tests/test_agent_recorders.py",
                    "tests/test_agent_runtime_metrics_postgres.py",
                ]
            )
        ),
        "langgraph_regression": _run_pytest(
            [
                "tests/test_langgraph_stage47_release_contract.py",
                "tests/test_langgraph_stage47_1_release_contract.py",
                "tests/test_durable_interview_graph.py",
                "tests/test_durable_review_graph.py",
            ]
        ),
    }
    result = evaluate_acceptance(
        checks,
        postgres_configured=postgres_configured,
        rollout_defaults_changed=not _rollout_defaults_unchanged(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return (
        0
        if result["status"] == "READY_FOR_AGENT_TELEMETRY_CANARY"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
