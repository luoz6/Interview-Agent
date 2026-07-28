from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "langgraph-stage47-acceptance-v1"


def _run_pytest(*args: str) -> dict[str, object]:
    started = perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "return_code": completed.returncode,
        "duration_seconds": round(perf_counter() - started, 3),
    }


def main() -> int:
    checks = {
        "stage47_unit": _run_pytest(
            "tests/test_langgraph_canary_status.py",
            "tests/test_langgraph_canary_cli.py",
            "tests/test_runtime_signal_metrics.py",
            "tests/test_runtime_outbox_dispatcher.py",
            "tests/test_report_worker.py",
        ),
        "stage47_postgres": _run_pytest(
            "tests/test_runtime_signal_metrics_postgres.py",
            "tests/test_langgraph_stage47_canary_postgres.py",
        ),
        "stage47_1_heartbeat_unit": _run_pytest(
            "tests/test_langgraph_stage47_1_release_contract.py",
            "tests/test_durable_interview_graph.py",
            "tests/test_review_workflow.py",
            "tests/test_workflow_thread_lock.py",
            "tests/test_review_workflow_store.py",
            "tests/test_durable_review_graph.py",
            "tests/test_runtime_work.py",
            "tests/test_runtime_outbox_dispatcher.py",
            "tests/test_report_worker.py",
        ),
        "stage47_1_heartbeat_postgres": _run_pytest(
            "tests/test_langgraph_heartbeat_recovery_postgres.py",
        ),
    }
    status = (
        "READY_FOR_OPERATOR_FENCING_CANARY"
        if all(item["status"] == "PASS" for item in checks.values())
        else "BLOCKED"
    )
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "status": status,
                "checks": checks,
                "operator_observation": "NOT_RUN",
                "rollout_defaults_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if status == "READY_FOR_OPERATOR_FENCING_CANARY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
