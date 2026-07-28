from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _run_pytest(arguments: list[str]) -> bool:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *arguments],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def _release_defaults_are_safe() -> bool:
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    required = (
        "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0",
        "REPORT_LANGGRAPH_ROLLOUT_PERCENT=0",
        "CONTEXT_BUDGET_SHADOW_ENABLED=false",
        "CONTEXT_BUDGET_PREP_ENFORCEMENT=false",
        "CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false",
        "CONTEXT_BUDGET_REVIEW_ENFORCEMENT=false",
        "CONTEXT_BUDGET_REPORT_ROUTING=false",
    )
    return all(value in content for value in required)


def evaluate_stage49_acceptance(checks: dict[str, bool]) -> dict:
    status = (
        "READY_FOR_CONTEXT_BUDGET_CANARY"
        if checks and all(checks.values())
        else "FAILED_REPOSITORY_GATE"
    )
    return {
        "status": status,
        "production_observation": "NOT_RUN",
        "context_policy_version": "context-v1",
        "checks": {
            name: "PASS" if passed else "FAIL"
            for name, passed in sorted(checks.items())
        },
    }


def main() -> int:
    checks = {
        "foundation": _run_pytest(
            [
                "tests/test_token_estimation.py",
                "tests/test_context_budget.py",
                "tests/test_context_selection.py",
                "tests/test_context_enforcement.py",
            ]
        ),
        "provider_usage_privacy": _run_pytest(
            [
                "tests/test_provider_usage.py",
                "tests/test_agent_runtime_hardening.py",
            ]
        ),
        "interview_knowledge": _run_pytest(
            [
                "tests/test_interview_graph.py",
                "tests/test_durable_interview_graph.py",
                "tests/test_knowledge_binding_resolver.py",
            ]
        ),
        "review_report": _run_pytest(
            [
                "tests/test_report_evaluator.py",
                "tests/test_report_microbatch.py",
                "tests/test_report_provider_adapter.py",
            ]
        ),
        "runtime_canary": _run_pytest(
            [
                "tests/test_runtime_work.py",
                "tests/test_langgraph_canary_status.py",
            ]
        ),
        "release_defaults": _release_defaults_are_safe(),
    }
    result = evaluate_stage49_acceptance(checks)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "READY_FOR_CONTEXT_BUDGET_CANARY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
