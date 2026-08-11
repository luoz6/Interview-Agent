from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    ProductionBudgetAcceptanceEvidencePayload,
    ProductionBudgetObservationEvidencePayload,
    ProductionBudgetReadinessEvidencePayload,
    ProductionBudgetWindowDecisionEvidencePayload,
    ProductionShadowApprovalRequestPayload,
    ProductionShadowChangePreflightEvidencePayload,
    ProductionShadowEvidenceManifestEntry,
    ProductionShadowEvidenceManifestPayload,
    input_artifact_from_bundle,
)
from contracts.evidence.digest import canonical_sha256
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.evidence.verifier import VerifiedEvidence
from contracts.policies import (
    ProductionBudgetAcceptanceEvidencePolicy,
    ProductionBudgetObservationEvidencePolicy,
    ProductionBudgetReadinessEvidencePolicy,
    ProductionBudgetWindowDecisionEvidencePolicy,
    ProductionShadowApprovalRequestPolicy,
    ProductionShadowChangePreflightEvidencePolicy,
    ProductionShadowEvidenceManifestPolicy,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
MEMORY_REPORTS = ROOT / "reports" / "memory"
DEFAULT_OUTPUT = MEMORY_REPORTS / "production-shadow-evidence-manifest-v1.json"


class ManifestBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("production Shadow evidence manifest blocked")


@dataclass(frozen=True)
class ManifestSource:
    logical_name: str
    path: Path
    revision: str
    scope: str
    expected_type: type


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _policy_result(payload):
    if isinstance(payload, ProductionShadowApprovalRequestPayload):
        return ProductionShadowApprovalRequestPolicy().evaluate(payload)
    if isinstance(payload, ProductionBudgetReadinessEvidencePayload):
        return ProductionBudgetReadinessEvidencePolicy().evaluate(payload)
    if isinstance(payload, ProductionShadowChangePreflightEvidencePayload):
        return ProductionShadowChangePreflightEvidencePolicy().evaluate(payload)
    if isinstance(payload, ProductionBudgetObservationEvidencePayload):
        return ProductionBudgetObservationEvidencePolicy().evaluate(payload)
    if isinstance(payload, ProductionBudgetWindowDecisionEvidencePayload):
        return ProductionBudgetWindowDecisionEvidencePolicy().evaluate(payload)
    if isinstance(payload, ProductionBudgetAcceptanceEvidencePayload):
        return ProductionBudgetAcceptanceEvidencePolicy().evaluate(payload)
    raise ManifestBlocked(("MANIFEST_PAYLOAD_TYPE_INVALID",))


def verify_sources(
    sources: tuple[ManifestSource, ...],
    *,
    verifier: EvidenceVerifier,
) -> dict[str, VerifiedEvidence]:
    verified: dict[str, VerifiedEvidence] = {}
    for source in sources:
        try:
            value = json.loads(source.path.read_text(encoding="utf-8"))
            item = verifier.verify(
                value,
                expected_revision=source.revision,
                expected_scope=source.scope,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ManifestBlocked(("MANIFEST_EVIDENCE_UNVERIFIED",)) from exc
        if not isinstance(item.payload, source.expected_type):
            raise ManifestBlocked(("MANIFEST_PAYLOAD_TYPE_INVALID",))
        policy_result = _policy_result(item.payload)
        artifact = item.bundle.artifact
        if (
            artifact.verification_status is not VerificationStatus.PASS
            or artifact.verification_status is not policy_result.verification_status
            or artifact.promotion_decision is not policy_result.promotion_decision
            or tuple(artifact.gate_codes) != tuple(policy_result.gate_codes)
        ):
            raise ManifestBlocked(("MANIFEST_EVIDENCE_STATE_INVALID",))
        verified[source.logical_name] = item
    return verified


def _manifest_receipts(item: VerifiedEvidence) -> dict[str, str]:
    return {
        artifact.path: artifact.receipt_sha256
        for artifact in item.bundle.artifact.envelope.input_manifest
    }


def verify_chain(verified: dict[str, VerifiedEvidence]) -> None:
    receipt = {
        name: canonical_sha256(item.bundle.receipt)
        for name, item in verified.items()
    }
    readiness_inputs = _manifest_receipts(verified["readiness"])
    preflight_inputs = _manifest_receipts(verified["change-preflight"])
    observation_inputs = _manifest_receipts(verified["observation"])
    window_inputs = _manifest_receipts(verified["window-decision"])
    acceptance_inputs = _manifest_receipts(verified["acceptance"])
    if readiness_inputs != {
        "production-shadow-approval-request": receipt["approval-request"]
    }:
        raise ManifestBlocked(("MANIFEST_READINESS_CHAIN_UNBOUND",))
    if (
        set(preflight_inputs)
        != {
            "production-shadow-approval-request",
            "production-budget-readiness-evidence",
            "external-production-shadow-approval-record",
        }
        or preflight_inputs["production-shadow-approval-request"]
        != receipt["approval-request"]
        or preflight_inputs["production-budget-readiness-evidence"]
        != receipt["readiness"]
    ):
        raise ManifestBlocked(("MANIFEST_PREFLIGHT_CHAIN_UNBOUND",))
    if (
        set(observation_inputs)
        != {
            "production-shadow-change-preflight-evidence",
            "external-production-budget-aggregate",
        }
        or observation_inputs["production-shadow-change-preflight-evidence"]
        != receipt["change-preflight"]
    ):
        raise ManifestBlocked(("MANIFEST_OBSERVATION_CHAIN_UNBOUND",))
    if (
        set(window_inputs)
        != {
            "production-shadow-change-preflight-evidence",
            "external-production-budget-window-state",
        }
        or window_inputs["production-shadow-change-preflight-evidence"]
        != receipt["change-preflight"]
    ):
        raise ManifestBlocked(("MANIFEST_WINDOW_CHAIN_UNBOUND",))
    if acceptance_inputs != {
        "production-budget-observation-evidence": receipt["observation"],
        "production-budget-window-decision-evidence": receipt["window-decision"],
    }:
        raise ManifestBlocked(("MANIFEST_ACCEPTANCE_CHAIN_UNBOUND",))


def build_manifest_payload(
    verified: dict[str, VerifiedEvidence],
    *,
    source_revision: str,
) -> ProductionShadowEvidenceManifestPayload:
    verify_chain(verified)
    entries = []
    for logical_name, item in sorted(verified.items()):
        artifact = item.bundle.artifact
        decision = artifact.promotion_decision
        if decision is None:
            raise ManifestBlocked(("MANIFEST_PROMOTION_DECISION_MISSING",))
        entries.append(
            ProductionShadowEvidenceManifestEntry(
                logical_name=logical_name,
                payload_type=artifact.payload_type,
                revision=artifact.envelope.revision,
                scope=artifact.envelope.scope,
                receipt_sha256=canonical_sha256(item.bundle.receipt),
                evidence_sha256=item.bundle.receipt.evidence_sha256,
                verification_status=artifact.verification_status.value,
                promotion_decision=decision.value,
            )
        )
    acceptance = verified["acceptance"].payload
    if not isinstance(acceptance, ProductionBudgetAcceptanceEvidencePayload):
        raise ManifestBlocked(("MANIFEST_PAYLOAD_TYPE_INVALID",))
    synthetic = any(bool(getattr(item.payload, "synthetic", False)) for item in verified.values())
    return ProductionShadowEvidenceManifestPayload(
        schema_version="production-shadow-evidence-manifest-v1",
        source_revision=source_revision,
        artifact_count=len(entries),
        artifacts=entries,
        all_verified=True,
        chain_bound=True,
        final_acceptance_status=acceptance.decision_status,
        principal_write_shadow_production="NOT_AUTHORIZED",
        principal_read_shadow_production="NOT_AUTHORIZED",
        long_term_memory_consumption="BLOCKED",
        synthetic=synthetic,
    )


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a protected production Shadow evidence manifest."
    )
    parser.add_argument(
        "--approval-request",
        type=Path,
        default=MEMORY_REPORTS / "production-shadow-approval-request-v1.json",
    )
    parser.add_argument("--approval-request-revision")
    parser.add_argument(
        "--readiness-evidence",
        type=Path,
        default=MEMORY_REPORTS / "production-budget-readiness-evidence-v1.json",
    )
    parser.add_argument("--readiness-revision")
    parser.add_argument(
        "--preflight-evidence",
        type=Path,
        default=MEMORY_REPORTS / "production-shadow-change-preflight-evidence-v1.json",
    )
    parser.add_argument("--preflight-revision")
    parser.add_argument(
        "--observation-evidence",
        type=Path,
        default=MEMORY_REPORTS / "production-budget-observation-evidence-v1.json",
    )
    parser.add_argument("--observation-revision")
    parser.add_argument(
        "--window-evidence",
        type=Path,
        default=MEMORY_REPORTS / "production-budget-window-decision-evidence-v1.json",
    )
    parser.add_argument("--window-revision")
    parser.add_argument(
        "--acceptance-evidence",
        type=Path,
        default=MEMORY_REPORTS / "production-budget-acceptance-evidence-v1.json",
    )
    parser.add_argument("--acceptance-revision")
    parser.add_argument("--source-revision")
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.production-shadow.evidence-manifest",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        signer = load_receipt_signer(os.environ)
        revisions = {
            "approval-request": args.approval_request_revision
            or require_environment_value(os.environ, "APPROVAL_REQUEST_REVISION"),
            "readiness": args.readiness_revision
            or require_environment_value(os.environ, "READINESS_EVIDENCE_REVISION"),
            "change-preflight": args.preflight_revision
            or require_environment_value(os.environ, "PREFLIGHT_EVIDENCE_REVISION"),
            "observation": args.observation_revision
            or require_environment_value(os.environ, "OBSERVATION_EVIDENCE_REVISION"),
            "window-decision": args.window_revision
            or require_environment_value(os.environ, "WINDOW_EVIDENCE_REVISION"),
            "acceptance": args.acceptance_revision
            or require_environment_value(os.environ, "ACCEPTANCE_EVIDENCE_REVISION"),
        }
        output_revision = args.output_revision or require_environment_value(
            os.environ,
            "EVIDENCE_REVISION",
        )
        source_revision = args.source_revision or _git_revision()
        sources = (
            ManifestSource(
                "approval-request",
                args.approval_request,
                revisions["approval-request"],
                "memory.production-shadow.approval-request",
                ProductionShadowApprovalRequestPayload,
            ),
            ManifestSource(
                "readiness",
                args.readiness_evidence,
                revisions["readiness"],
                "memory.production-budget-shadow.readiness",
                ProductionBudgetReadinessEvidencePayload,
            ),
            ManifestSource(
                "change-preflight",
                args.preflight_evidence,
                revisions["change-preflight"],
                "memory.production-shadow.change-preflight",
                ProductionShadowChangePreflightEvidencePayload,
            ),
            ManifestSource(
                "observation",
                args.observation_evidence,
                revisions["observation"],
                "memory.production-budget-shadow.observation",
                ProductionBudgetObservationEvidencePayload,
            ),
            ManifestSource(
                "window-decision",
                args.window_evidence,
                revisions["window-decision"],
                "memory.production-budget-shadow.window-decision",
                ProductionBudgetWindowDecisionEvidencePayload,
            ),
            ManifestSource(
                "acceptance",
                args.acceptance_evidence,
                revisions["acceptance"],
                "memory.production-budget-shadow.acceptance",
                ProductionBudgetAcceptanceEvidencePayload,
            ),
        )
        verifier = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        )
        verified = verify_sources(sources, verifier=verifier)
        payload = build_manifest_payload(
            verified,
            source_revision=source_revision,
        )
    except (AcceptanceConfigurationError, ManifestBlocked, ValueError) as exc:
        codes = exc.codes if isinstance(exc, ManifestBlocked) else ("MANIFEST_INPUT_INVALID",)
        print("\n".join(format_blocked_output(codes)))
        return 1

    policy_result = ProductionShadowEvidenceManifestPolicy().evaluate(payload)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="production-shadow-evidence-manifest",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-production-shadow-evidence-manifest",
        tool_version="3.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=tuple(
            input_artifact_from_bundle(
                path=source.path,
                logical_path=f"production-shadow-chain/{source.logical_name}",
                bundle=verified[source.logical_name].bundle,
            )
            for source in sources
        ),
    )
    output_verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda persisted: output_verifier.verify(
            persisted,
            expected_revision=output_revision,
            expected_scope=args.output_scope,
        )
    ).write(args.output, bundle)
    print("\n".join(render_gate_lines(bundle)))
    print("MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=VERIFIED")
    print(f"ARTIFACTS={payload.artifact_count}")
    print("LONG_TERM_MEMORY_CONSUMPTION=BLOCKED")
    return 0 if policy_result.verification_status is VerificationStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
