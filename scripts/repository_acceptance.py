from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from collections.abc import Mapping

from contracts.evidence import (
    AtomicEvidenceWriter,
    CapacityEvidencePayload,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    PromotionDecision,
    Stage49ContextBudgetCanaryEvidencePayload,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.policies import (
    CapacityEvidencePolicy,
    Stage49ContextBudgetCanaryEvidencePolicy,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "stage49" / "canary-evidence-v1.json"
CAPACITY_ARTIFACT = (
    ROOT / "reports" / "stage48-acceptance" / "postgres-capacity-v2.json"
)
STAGE47_SCHEMA_VERSION = "langgraph-stage47-acceptance-v1"
STAGE47_2_ROLLOUT_DEFAULTS = (
    "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0",
    "REPORT_LANGGRAPH_ROLLOUT_PERCENT=0",
)


def run_pytest_result(arguments: list[str]) -> dict[str, object]:
    started = perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *arguments],
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


def run_pytest(arguments: list[str]) -> bool:
    return run_pytest_result(arguments)["return_code"] == 0


def required_defaults_are_present(required: tuple[str, ...]) -> bool:
    content = (ROOT / ".env.example").read_text(encoding="utf-8")
    return all(value in content for value in required)


def release_defaults_are_safe() -> bool:
    required = (
        "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0",
        "REPORT_LANGGRAPH_ROLLOUT_PERCENT=0",
        "CONTEXT_BUDGET_SHADOW_ENABLED=false",
        "CONTEXT_BUDGET_PREP_ENFORCEMENT=false",
        "CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false",
        "CONTEXT_BUDGET_REVIEW_ENFORCEMENT=false",
        "CONTEXT_BUDGET_REPORT_ROUTING=false",
    )
    return required_defaults_are_present(required)


def evaluate_stage47_acceptance(checks: dict[str, dict[str, object]]) -> dict:
    status = (
        "READY_FOR_OPERATOR_FENCING_CANARY"
        if checks and all(item.get("status") == "PASS" for item in checks.values())
        else "BLOCKED"
    )
    return {
        "schema_version": STAGE47_SCHEMA_VERSION,
        "status": status,
        "checks": checks,
        "operator_observation": "NOT_RUN",
        "rollout_defaults_changed": False,
    }


def blocked_infrastructure_check() -> dict[str, object]:
    return {
        "status": "FAIL",
        "return_code": 1,
        "duration_seconds": 0.0,
    }


def stage47_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 47 LangGraph acceptance")
    parser.parse_args(argv)
    postgres_configured = bool(os.getenv("POSTGRES_DSN"))
    checks = {
        "stage47_unit": run_pytest_result(
            [
                "tests/unit/test_langgraph_canary_status.py",
                "tests/contracts/test_langgraph_canary_cli.py",
                "tests/contracts/test_runtime_signal_metrics.py",
                "tests/unit/test_runtime_outbox_dispatcher.py",
                "tests/unit/test_report_worker.py",
            ]
        ),
        "stage47_postgres": (
            run_pytest_result(
                [
                    "tests/integration/postgres/test_runtime_signal_metrics_postgres.py",
                    "tests/integration/postgres/test_langgraph_stage47_canary_postgres.py",
                    "tests/integration/postgres/test_report_worker.py",
                ]
            )
            if postgres_configured
            else blocked_infrastructure_check()
        ),
        "stage47_1_heartbeat_unit": run_pytest_result(
            [
                "tests/contracts/test_langgraph_stage47_1_release_contract.py",
                "tests/unit/test_durable_interview_graph.py",
                "tests/integration/postgres/test_durable_interview_graph.py",
                "tests/unit/test_review_workflow.py",
                "tests/unit/test_workflow_thread_lock.py",
                "tests/integration/postgres/test_review_workflow_store.py",
                "tests/unit/test_durable_review_graph.py",
                "tests/unit/test_runtime_work.py",
                "tests/unit/test_runtime_outbox_dispatcher.py",
                "tests/unit/test_report_worker.py",
            ]
        ),
        "stage47_1_heartbeat_postgres": (
            run_pytest_result(
                [
                    "tests/integration/postgres/test_langgraph_heartbeat_recovery_postgres.py",
                ]
            )
            if postgres_configured
            else blocked_infrastructure_check()
        ),
    }
    result = evaluate_stage47_acceptance(checks)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "READY_FOR_OPERATOR_FENCING_CANARY" else 1


def evaluate_stage47_2_acceptance(
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


def stage47_2_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 47.2 agent runtime telemetry acceptance"
    )
    parser.parse_args(argv)
    postgres_configured = bool(os.getenv("POSTGRES_DSN"))
    checks = {
        "runtime_unit": run_pytest(
            [
                "tests/unit/test_agent_runtime.py",
                "tests/unit/test_agent_runtime_hardening.py",
                "tests/unit/test_agent_recorders.py",
            ]
        ),
        "composition": run_pytest(
            [
                "tests/unit/test_agent_runtime_composition.py",
                "tests/contracts/test_agent_runtime_release_contract.py",
                "tests/unit/test_prep_service.py",
            ]
        ),
        "privacy": run_pytest(
            [
                "tests/contracts/test_agent_trace.py",
                "tests/contracts/test_agent_runtime_audit.py",
            ]
        ),
        "postgres": postgres_configured
        and run_pytest(
            [
                "tests/integration/postgres/test_agent_recorders.py",
                "tests/integration/postgres/test_agent_runtime_metrics_postgres.py",
            ]
        ),
        "langgraph_regression": run_pytest(
            [
                "tests/contracts/test_langgraph_stage47_release_contract.py",
                "tests/contracts/test_langgraph_stage47_1_release_contract.py",
                "tests/unit/test_durable_interview_graph.py",
                "tests/integration/postgres/test_durable_interview_graph.py",
                "tests/unit/test_durable_review_graph.py",
            ]
        ),
    }
    result = evaluate_stage47_2_acceptance(
        checks,
        postgres_configured=postgres_configured,
        rollout_defaults_changed=not required_defaults_are_present(
            STAGE47_2_ROLLOUT_DEFAULTS
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "READY_FOR_AGENT_TELEMETRY_CANARY" else 1


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


def build_stage49_evidence(
    result: dict,
    *,
    synthetic: bool = False,
) -> Stage49ContextBudgetCanaryEvidencePayload:
    checks = result.get("checks")
    check_values = checks if isinstance(checks, dict) else {}
    return Stage49ContextBudgetCanaryEvidencePayload(
        schema_version="stage49-context-budget-canary-evidence-v1",
        status=result["status"],
        context_policy_version=result["context_policy_version"],
        check_count=len(check_values),
        checks_passed=sum(value == "PASS" for value in check_values.values()),
        release_defaults_safe=check_values.get("release_defaults") == "PASS",
        production_observation=result["production_observation"],
        synthetic=synthetic,
    )


def stage49_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Stage 49 repository gates and issue protected evidence."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="stage49.context-budget.canary",
    )
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args(argv)
    try:
        signer = load_receipt_signer(os.environ)
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
    except AcceptanceConfigurationError as exc:
        print("STAGE49_CONTEXT_BUDGET_CANARY=BLOCKED")
        print(f"GATE={exc.code}")
        return 1
    checks = {
        "foundation": run_pytest(
            [
                "tests/unit/test_token_estimation.py",
                "tests/unit/test_context_budget.py",
                "tests/unit/test_context_selection.py",
                "tests/unit/test_context_runtime.py",
                "tests/unit/test_context_enforcement.py",
            ]
        ),
        "provider_usage_privacy": run_pytest(
            [
                "tests/unit/test_provider_usage.py",
                "tests/unit/test_agent_runtime_hardening.py",
            ]
        ),
        "interview_knowledge": run_pytest(
            [
                "tests/unit/test_interview_graph.py",
                "tests/unit/test_durable_interview_graph.py",
                "tests/integration/postgres/test_durable_interview_graph.py",
                "tests/unit/test_knowledge_binding_resolver.py",
            ]
        ),
        "review_report": run_pytest(
            [
                "tests/unit/test_report_evaluator.py",
                "tests/unit/test_report_microbatch.py",
                "tests/integration/providers/test_report_provider_adapter.py",
            ]
        ),
        "runtime_canary": run_pytest(
            [
                "tests/unit/test_runtime_work.py",
                "tests/unit/test_langgraph_canary_status.py",
            ]
        ),
        "release_defaults": release_defaults_are_safe(),
    }
    result = evaluate_stage49_acceptance(checks)
    payload = build_stage49_evidence(result, synthetic=args.synthetic)
    policy_result = Stage49ContextBudgetCanaryEvidencePolicy().evaluate(payload)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="stage49-context-budget-canary-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.langgraph-stage49-acceptance",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
    )
    output_verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda value: output_verifier.verify(
            value,
            expected_revision=output_revision,
            expected_scope=args.output_scope,
        )
    ).write(args.output, bundle)
    print(json.dumps(result, indent=2, sort_keys=True))
    print("\n".join(render_gate_lines(bundle)))
    return 0 if policy_result.verification_status is VerificationStatus.PASS else 1


def capacity_artifact_eligible(
    path: Path = CAPACITY_ARTIFACT,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    if not path.exists():
        return False
    resolved_environment = os.environ if environ is None else environ
    try:
        signer = load_receipt_signer(resolved_environment)
        revision = require_environment_value(
            resolved_environment,
            "EVIDENCE_REVISION",
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        verified = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ).verify(
            value,
            expected_revision=revision,
            expected_scope="capacity.controlled",
        )
        if not isinstance(verified.payload, CapacityEvidencePayload):
            return False
        policy_result = CapacityEvidencePolicy(
            minimum_samples=1,
            minimum_headroom_percent=0.0,
        ).evaluate(
            verified.payload,
            production_scope=False,
        )
        artifact = verified.bundle.artifact
        return (
            policy_result.verification_status is VerificationStatus.PASS
            and policy_result.promotion_decision
            is PromotionDecision.READY_FOR_REVIEW
            and artifact.verification_status is policy_result.verification_status
            and artifact.promotion_decision is policy_result.promotion_decision
            and tuple(artifact.gate_codes) == policy_result.gate_codes
        )
    except (OSError, ValueError):
        return False


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
        "capacity_schema": "capacity-evidence-v1",
        "checks": {
            name: "PASS" if passed else "FAIL"
            for name, passed in checks.items()
        },
    }


def stage48_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 48 PostgreSQL acceptance")
    parser.add_argument(
        "--capacity-artifact",
        type=Path,
        default=CAPACITY_ARTIFACT,
    )
    args = parser.parse_args(argv)
    postgres_configured = bool(os.getenv("POSTGRES_DSN"))
    checks = {
        "contracts": run_pytest(
            [
                "tests/unit/test_postgres_identifiers.py",
                "tests/unit/test_postgres_connections.py",
                "tests/contracts/test_postgres_capacity.py",
                "tests/integration/postgres/test_postgres_runtime_migrations.py",
                "tests/unit/test_postgres_connection_domains.py",
                "tests/contracts/test_stage48_release_contract.py",
            ]
        ),
        "postgres": postgres_configured
        and run_pytest(
            ["tests/integration/postgres/test_stage48_postgres_capacity.py"]
        ),
        "recovery_fencing": postgres_configured
        and run_pytest(
            [
                "-m",
                "langgraph_recovery or langgraph_review_recovery or "
                "langgraph_single_writer or langgraph_fencing or "
                "langgraph_effect_replay or langgraph_fencing_canary or "
                "langgraph_heartbeat_recovery",
            ]
        ),
        "agent_telemetry": run_pytest(
            [
                "tests/unit/test_agent_runtime_hardening.py",
                "tests/contracts/test_agent_runtime_release_contract.py",
                "tests/contracts/test_agent_runtime_audit.py",
            ]
        ),
        "capacity_artifact": capacity_artifact_eligible(args.capacity_artifact),
    }
    result = evaluate_stage48_acceptance(
        checks,
        postgres_configured=postgres_configured,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return (
        0
        if result["status"] == "READY_FOR_CAPACITY_AWARE_FENCING_CANARY"
        else 1
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repository acceptance gates")
    parser.add_argument(
        "profile",
        choices=("stage47", "stage47_2", "stage48", "stage49"),
    )
    args, remaining = parser.parse_known_args(argv)
    if args.profile == "stage47":
        return stage47_main(remaining)
    if args.profile == "stage47_2":
        return stage47_2_main(remaining)
    if args.profile == "stage48":
        return stage48_main(remaining)
    return stage49_main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
