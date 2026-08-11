from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from contracts.evidence import (
    AtomicEvidenceWriter,
    CleanupEvidencePayload,
    CleanupRecord,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    InputArtifact,
    ReleaseEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.digest import sha256_bytes
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.policies import CleanupEvidencePolicy, ReleaseEvidencePolicy
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE_EVIDENCE = (
    ROOT / "reports" / "memory" / "release-preflight-evidence-v1.json"
)
DEFAULT_OUTPUT = ROOT / "reports" / "memory" / "cleanup-evidence-v1.json"


def build_cleanup_evidence(record: CleanupRecord) -> CleanupEvidencePayload:
    return CleanupEvidencePayload(
        schema_version="cleanup-evidence-v1",
        target_fingerprint=record.target_fingerprint,
        ownership_verified=record.ownership_verified,
        resources_examined=record.resources_examined,
        resources_removed=record.resources_removed,
        residue_count=record.residue_count,
        synthetic=record.synthetic,
    )


def external_record_input(
    *,
    path: Path,
    expected_sha256: str,
) -> InputArtifact:
    persisted = path.read_bytes()
    return InputArtifact(
        path="external-cleanup-record",
        sha256=sha256_bytes(persisted),
        receipt_sha256=expected_sha256,
        size_bytes=len(persisted),
        media_type="application/json",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify release evidence and issue protected cleanup evidence."
    )
    parser.add_argument(
        "--release-evidence",
        type=Path,
        default=DEFAULT_RELEASE_EVIDENCE,
    )
    parser.add_argument("--release-revision", required=True)
    parser.add_argument(
        "--release-scope",
        default="memory.shadow.release-preflight",
    )
    parser.add_argument("--cleanup-record", type=Path, required=True)
    parser.add_argument("--expected-record-sha256", required=True)
    parser.add_argument("--expected-target-fingerprint", required=True)
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.cleanup.evidence",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "CLEANUP_EVIDENCE=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        signer = load_receipt_signer(os.environ)
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
        verified_release = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ).verify(
            json.loads(args.release_evidence.read_text(encoding="utf-8")),
            expected_revision=args.release_revision,
            expected_scope=args.release_scope,
        )
        if not isinstance(verified_release.payload, ReleaseEvidencePayload):
            raise ValueError("release evidence payload type is invalid")
        release_result = ReleaseEvidencePolicy().evaluate(verified_release.payload)
        release_artifact = verified_release.bundle.artifact
        if (
            release_artifact.verification_status is not VerificationStatus.PASS
            or release_artifact.verification_status
            is not release_result.verification_status
            or release_artifact.promotion_decision
            is not release_result.promotion_decision
            or tuple(release_artifact.gate_codes) != release_result.gate_codes
        ):
            raise ValueError("release evidence policy state is invalid")
        record_bytes = args.cleanup_record.read_bytes()
        if sha256_bytes(record_bytes) != args.expected_record_sha256:
            raise ValueError("cleanup record hash mismatch")
        record = CleanupRecord.model_validate_json(record_bytes)
        if (
            record.validated_revision != args.release_revision
            or output_revision != record.validated_revision
            or record.target_fingerprint != args.expected_target_fingerprint
        ):
            raise ValueError("cleanup record binding is invalid")
    except (
        AcceptanceConfigurationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ):
        print("\n".join(format_blocked_output(("CLEANUP_INPUT_UNVERIFIED",))))
        return 1

    payload = build_cleanup_evidence(record)
    policy_result = CleanupEvidencePolicy().evaluate(payload)
    record_artifact = external_record_input(
        path=args.cleanup_record,
        expected_sha256=args.expected_record_sha256,
    )
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="cleanup-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-cleanup-evidence",
        tool_version="1.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=(
            input_artifact_from_bundle(
                path=args.release_evidence,
                logical_path="release-preflight-evidence",
                bundle=verified_release.bundle,
            ),
            record_artifact,
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
    print("\n".join(render_gate_lines(bundle)))
    return 0 if policy_result.verification_status is VerificationStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
