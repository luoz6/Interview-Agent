from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping

from app.runtime.config.memory import load_effective_memory_config
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    OperationalShadowEvidencePayload,
    ProductionShadowApprovalRequestPayload,
    input_artifact_from_bundle,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.policies import (
    OperationalShadowEvidencePolicy,
    ProductionShadowApprovalRequestPolicy,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    ROOT / "reports" / "memory" / "production-shadow-approval-request-v1.json"
)
DEFAULT_OPERATIONAL_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-shadow-evidence-v1.json"
)
PENDING_LINES = (
    "MEMORY_PRODUCTION_SHADOW_PACKET=READY_FOR_REVIEW",
    "REQUESTED_PHASE=BUDGET_SHADOW_ONLY",
    "APPROVAL_STATUS=PENDING",
    "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)
class ApprovalPacketBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Memory Shadow approval packet blocked")


def evaluate_approval_readiness(
    inputs: Mapping[str, object]
) -> tuple[str, ...]:
    codes: list[str] = []
    operational_value = inputs.get("operational")
    operational = (
        operational_value
        if isinstance(operational_value, OperationalShadowEvidencePayload)
        else None
    )
    if operational is None:
        codes.append("OPERATIONAL_EVIDENCE_UNVERIFIED")
    elif (
        OperationalShadowEvidencePolicy().evaluate(operational).verification_status
        is not VerificationStatus.PASS
        or not operational.operational_gates_passed
    ):
        codes.append("OPERATIONAL_SHADOW_NOT_ACCEPTED")
    if operational is not None and not operational.production_approval_required:
        codes.append("PRODUCTION_APPROVAL_REQUEST_STATE_INVALID")
    if operational is not None and any(
        value != 0
        for value in (
            operational.test_listener_residue,
            operational.isolated_relation_residue,
            operational.private_data_residue,
        )
    ):
        codes.append("OPERATIONAL_CLEANUP_RESIDUE")
    if operational is not None and (
        not operational.safe_defaults or not operational.consume_rejected
    ):
        codes.append("OPERATIONAL_SAFE_DEFAULTS_INVALID")

    repository_value = inputs.get("repository")
    repository = (
        repository_value if isinstance(repository_value, Mapping) else {}
    )
    if repository.get("safe_defaults") is not True:
        codes.append("SAFE_DEFAULTS_CHANGED")
    if repository.get("consume_rejected") is not True:
        codes.append("CONSUME_NOT_REJECTED")

    if operational is not None:
        if not operational.production_observation_not_run:
            codes.append("PRODUCTION_OBSERVATION_ALREADY_STARTED")
        if not operational.long_term_consumption_blocked:
            codes.append("CONSUMPTION_BOUNDARY_INVALID")
    if codes:
        raise ApprovalPacketBlocked(codes)
    return PENDING_LINES


def build_approval_packet(
    inputs: Mapping[str, object]
) -> ProductionShadowApprovalRequestPayload:
    evaluate_approval_readiness(inputs)
    operational = inputs["operational"]
    if not isinstance(operational, OperationalShadowEvidencePayload):
        raise ApprovalPacketBlocked(("OPERATIONAL_EVIDENCE_UNVERIFIED",))
    packet = ProductionShadowApprovalRequestPayload(
        schema_version="production-shadow-approval-request-v1",
        validated_rc_revision=operational.validated_rc_revision,
        validation_revision=operational.validation_revision,
        evidence_environment=operational.environment_category,
        evidence_profile=operational.observation_profile,
        requested_phase="BUDGET_SHADOW_ONLY",
        approval_status="PENDING",
        required_approval_roles=[
            "change_owner",
            "operations",
            "privacy",
            "security",
            "fairness",
        ],
        maximum_traffic_percent=1.0,
        initial_warmup_traffic_percent=0.1,
        minimum_warmup_minutes=30,
        minimum_warmup_followup_samples=20,
        minimum_observation_hours=24,
        minimum_followup_samples=200,
        provider_input_change=False,
        budget_enforcement=False,
        compression_consumption=False,
        principal_write_shadow=False,
        principal_read_shadow=False,
        principal_memory_consumption=False,
        production_migration=False,
        configuration_changed=False,
        production_observation_not_run=True,
        long_term_consumption_blocked=True,
        synthetic=operational.synthetic,
    )
    return packet


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "MEMORY_PRODUCTION_SHADOW_PACKET=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )


def repository_snapshot() -> dict[str, bool]:
    config = load_effective_memory_config({})
    safe_defaults = (
        config.budget.mode == "disabled"
        and config.compression.mode == "disabled"
        and config.long_term.mode == "disabled"
        and not config.long_term.write_shadow_enabled
        and not config.long_term.read_shadow_enabled
        and not config.long_term.trusted_local_api_enabled
    )
    consume_rejected = False
    try:
        load_effective_memory_config({"MEMORY_LONG_TERM_MODE": "consume"})
    except ValueError:
        consume_rejected = True
    return {
        "safe_defaults": safe_defaults,
        "consume_rejected": consume_rejected,
    }


def load_default_inputs(
    *,
    operational: OperationalShadowEvidencePayload,
) -> dict[str, object]:
    return {
        "operational": operational,
        "repository": repository_snapshot(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a pending production Budget Shadow approval packet."
    )
    parser.add_argument(
        "--operational-evidence",
        type=Path,
        default=DEFAULT_OPERATIONAL_EVIDENCE,
    )
    parser.add_argument("--operational-revision")
    parser.add_argument(
        "--operational-scope",
        default="memory.operational-shadow.controlled",
    )
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.production-shadow.approval-request",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        operational_revision = (
            args.operational_revision
            or require_environment_value(os.environ, "OPERATIONAL_EVIDENCE_REVISION")
        )
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
        signer = load_receipt_signer(os.environ)
        operational_value = json.loads(
            args.operational_evidence.read_text(encoding="utf-8")
        )
        verified_operational = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ).verify(
            operational_value,
            expected_revision=operational_revision,
            expected_scope=args.operational_scope,
        )
        if not isinstance(
            verified_operational.payload,
            OperationalShadowEvidencePayload,
        ):
            raise ValueError("operational payload type is invalid")
    except (AcceptanceConfigurationError, OSError, ValueError, json.JSONDecodeError):
        print("\n".join(format_blocked_output(("OPERATIONAL_EVIDENCE_UNVERIFIED",))))
        return 1
    inputs = load_default_inputs(operational=verified_operational.payload)
    try:
        lines = evaluate_approval_readiness(inputs)
        packet = build_approval_packet(inputs)
    except ApprovalPacketBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1
    policy_result = ProductionShadowApprovalRequestPolicy().evaluate(packet)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="production-shadow-approval-request",
        payload=packet,
        policy_result=policy_result,
        producer="scripts.memory-production-shadow-approval-packet",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=(
            input_artifact_from_bundle(
                path=args.operational_evidence,
                logical_path="operational-shadow-evidence",
                bundle=verified_operational.bundle,
            ),
        ),
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
    print("\n".join((*render_gate_lines(bundle), *lines)))
    return 0 if policy_result.verification_status is VerificationStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
