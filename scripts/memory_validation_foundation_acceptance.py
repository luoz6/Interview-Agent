from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from app.services.knowledge_profile import (
    P1_REQUIRED_COVERED_TAGS,
    load_active_knowledge_covered_tags,
)
from app.runtime.config.memory import load_effective_memory_config
from app.services.memory_quality_dataset import load_memory_quality_dataset
from app.services.memory_quality_eval import evaluate_memory_quality
from app.services.postgres_schema_contract import RUNTIME_MIGRATIONS
from scripts.memory_shadow_release_preflight import RETIRED_STATIC_ASSETS
from contracts.evidence import (
    EvidenceRegistry,
    EvidenceVerifier,
    OperationalRcEvidencePayload,
)
from contracts.evidence.status import VerificationStatus
from contracts.policies import OperationalRcEvidencePolicy
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/interview-agent-memory-system-optimization-spec.md"
PLAN = ROOT / "docs/superpowers/plans/2026-07-30-memory-validation-and-long-term-memory-foundation.md"
DEFAULT_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-rc-evidence-v1.json"
)
SUCCESS_LINES = (
    "READY_FOR_MEMORY_VALIDATION_SHADOW",
    "LONG_TERM_MEMORY_WRITE_SHADOW_READY",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)


class AcceptanceBlocked(RuntimeError):
    def __init__(self, codes):
        self.codes = tuple(sorted(set(codes)))
        super().__init__("memory validation acceptance blocked")


def verify_traceability() -> None:
    plan_ids = set(re.findall(r"MEM-[A-Z]+-\d{3}", PLAN.read_text(encoding="utf-8")))
    spec_ids = set(re.findall(r"MEM-[A-Z]+-\d{3}", SPEC.read_text(encoding="utf-8")))
    missing = sorted(plan_ids - spec_ids)
    if missing:
        raise AcceptanceBlocked(["requirement_traceability_missing"])


def repository_gate_codes() -> list[str]:
    codes = []
    if any((ROOT / relative_path).exists() for relative_path in RETIRED_STATIC_ASSETS):
        codes.append("retired_static_asset_restored")
    config = load_effective_memory_config({})
    if config.long_term.mode != "disabled":
        codes.append("long_term_default_not_disabled")
    if config.long_term.write_shadow_enabled or config.long_term.read_shadow_enabled:
        codes.append("long_term_shadow_default_enabled")
    if config.long_term.trusted_local_api_enabled:
        codes.append("trusted_local_principal_api_default_enabled")
    try:
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})
    except ValueError:
        pass
    else:
        codes.append("long_term_consume_not_rejected")
    if not any(
        migration.migration_id == "principal_memory_v1"
        for migration in RUNTIME_MIGRATIONS
    ):
        codes.append("principal_memory_migration_missing")
    coverage = load_active_knowledge_covered_tags()
    if not P1_REQUIRED_COVERED_TAGS <= set(coverage):
        codes.append("knowledge_p1_not_ready")
    quality = evaluate_memory_quality(load_memory_quality_dataset())
    if not quality["passed"]:
        codes.append("long_context_quality_failed")
    required = (
        "app/domain/memory/contracts.py",
        "app/domain/memory/facts.py",
        "app/adapters/memory/principal_memory.py",
        "app/adapters/postgres/principal_memory.py",
        "app/services/principal_memory_tasks.py",
        "app/services/principal_memory_shadow.py",
        "docs/principal-memory-threat-model.md",
    )
    if any(not (ROOT / path).exists() for path in required):
        codes.append("principal_memory_artifacts_missing")
    return codes


def operational_gate_codes(evidence: OperationalRcEvidencePayload) -> list[str]:
    return list(OperationalRcEvidencePolicy().evaluate(evidence).gate_codes)


def run_acceptance(evidence: OperationalRcEvidencePayload) -> tuple[str, ...]:
    verify_traceability()
    codes = repository_gate_codes() + operational_gate_codes(evidence)
    if codes:
        raise AcceptanceBlocked(codes)
    return SUCCESS_LINES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate memory validation foundation")
    parser.add_argument("--evidence", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--evidence-revision")
    args = parser.parse_args(argv)
    try:
        revision = args.evidence_revision or require_environment_value(
            os.environ,
            "OPERATIONAL_INPUT_REVISION",
        )
        signer = load_receipt_signer(os.environ)
        verified = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ).verify(
            json.loads(Path(args.evidence).read_text(encoding="utf-8")),
            expected_revision=revision,
            expected_scope="memory.operational-rc.controlled",
        )
        if not isinstance(verified.payload, OperationalRcEvidencePayload):
            raise ValueError("operational RC payload type is invalid")
        policy_result = OperationalRcEvidencePolicy().evaluate(verified.payload)
        artifact = verified.bundle.artifact
        if (
            policy_result.verification_status is not VerificationStatus.PASS
            or artifact.verification_status is not policy_result.verification_status
            or artifact.promotion_decision is not policy_result.promotion_decision
            or tuple(artifact.gate_codes) != policy_result.gate_codes
        ):
            raise ValueError("operational RC policy state is invalid")
        lines = run_acceptance(verified.payload)
    except (
        AcceptanceBlocked,
        AcceptanceConfigurationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print("MEMORY_VALIDATION_FOUNDATION=BLOCKED")
        codes = (
            exc.codes
            if isinstance(exc, AcceptanceBlocked)
            else ("OPERATIONAL_RC_EVIDENCE_UNVERIFIED",)
        )
        for code in codes:
            print(f"GATE={code}")
        print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
        print("PRODUCTION_OBSERVATION=NOT_RUN")
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
