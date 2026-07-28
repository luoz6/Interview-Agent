from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CAPACITY_ARTIFACT = (
    ROOT / "reports" / "stage48-acceptance" / "postgres-capacity-v1.json"
)


def _run_pytest(arguments: list[str]) -> bool:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *arguments],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _capacity_artifact_eligible() -> bool:
    if not CAPACITY_ARTIFACT.exists():
        return False
    payload = json.loads(CAPACITY_ARTIFACT.read_text(encoding="utf-8"))
    return (
        payload.get("schema_version") == "postgres-capacity-v1"
        and payload.get("status") == "ELIGIBLE_FOR_CAPACITY_CANARY"
        and payload.get("production_observation") == "NOT_RUN"
        and payload.get("privacy_violations") == 0
    )


def evaluate_stage48_acceptance(
    checks: dict[str, bool],
    *,
    postgres_configured: bool,
) -> dict:
    if not postgres_configured:
        status = "BLOCKED_POSTGRES_GATE"
    elif not all(checks.values()):
        status = "FAILED_REPOSITORY_GATE"
    else:
        status = "READY_FOR_CAPACITY_AWARE_FENCING_CANARY"
    return {
        "status": status,
        "production_observation": "NOT_RUN",
        "capacity_schema": "postgres-capacity-v1",
        "checks": {
            name: "PASS" if passed else "FAIL"
            for name, passed in checks.items()
        },
    }


def main() -> int:
    postgres_configured = bool(os.getenv("POSTGRES_DSN"))
    checks = {
        "contracts": _run_pytest(
            [
                "tests/test_postgres_identifiers.py",
                "tests/test_postgres_connections.py",
                "tests/test_postgres_capacity.py",
                "tests/test_postgres_runtime_migrations.py",
                "tests/test_postgres_connection_domains.py",
                "tests/test_stage48_release_contract.py",
            ]
        ),
        "postgres": postgres_configured
        and _run_pytest(["tests/test_stage48_postgres_capacity.py"]),
        "recovery_fencing": postgres_configured
        and _run_pytest(
            [
                "-m",
                "langgraph_recovery or langgraph_review_recovery or "
                "langgraph_single_writer or langgraph_fencing or "
                "langgraph_effect_replay or langgraph_fencing_canary or "
                "langgraph_heartbeat_recovery",
            ]
        ),
        "agent_telemetry": _run_pytest(
            [
                "tests/test_agent_runtime_hardening.py",
                "tests/test_agent_runtime_release_contract.py",
                "tests/test_agent_runtime_audit.py",
            ]
        ),
        "capacity_artifact": _capacity_artifact_eligible(),
    }
    result = evaluate_stage48_acceptance(
        checks,
        postgres_configured=postgres_configured,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return (
        0
        if result["status"]
        == "READY_FOR_CAPACITY_AWARE_FENCING_CANARY"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
