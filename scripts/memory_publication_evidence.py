from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from contracts.evidence import (
    AtomicEvidenceWriter,
    CleanupEvidencePayload,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    InputArtifact,
    PublicationEvidencePayload,
    PublicationRecord,
    ReleaseEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.digest import sha256_bytes
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.evidence.digest import canonical_sha256
from contracts.policies import (
    CleanupEvidencePolicy,
    PublicationEvidencePolicy,
    ReleaseEvidencePolicy,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE_EVIDENCE = (
    ROOT / "reports" / "memory" / "release-preflight-evidence-v1.json"
)
DEFAULT_CLEANUP_EVIDENCE = (
    ROOT / "reports" / "memory" / "cleanup-evidence-v1.json"
)
DEFAULT_OUTPUT = ROOT / "reports" / "memory" / "publication-evidence-v1.json"


class PublicationBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("publication evidence blocked")


def build_publication_evidence(
    record: PublicationRecord,
) -> PublicationEvidencePayload:
    return PublicationEvidencePayload(
        schema_version="publication-evidence-v1",
        validated_revision=record.validated_revision,
        release_evidence_verified=True,
        cleanup_evidence_verified=True,
        publication_ref=record.publication_ref,
        publication_scope=record.publication_scope,
        external_ref_verified=record.external_ref_verified,
        artifact_count=record.artifact_count,
        required_test_skipped=record.required_test_skipped,
        cleanup_residue_count=record.cleanup_residue_count,
        private_data_finding_count=record.private_data_finding_count,
        synthetic=record.synthetic,
    )


def external_record_input(
    *,
    path: Path,
    expected_sha256: str,
) -> InputArtifact:
    persisted = path.read_bytes()
    return InputArtifact(
        path="external-publication-record",
        sha256=sha256_bytes(persisted),
        receipt_sha256=expected_sha256,
        size_bytes=len(persisted),
        media_type="application/json",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify release evidence and issue protected publication evidence."
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
    parser.add_argument(
        "--cleanup-evidence",
        type=Path,
        default=DEFAULT_CLEANUP_EVIDENCE,
    )
    parser.add_argument("--cleanup-revision", required=True)
    parser.add_argument(
        "--cleanup-scope",
        default="memory.cleanup.evidence",
    )
    parser.add_argument("--publication-record", type=Path, required=True)
    parser.add_argument("--expected-record-sha256", required=True)
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.publication.evidence",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "PUBLICATION_EVIDENCE=BLOCKED",
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
        verified_cleanup = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        ).verify(
            json.loads(args.cleanup_evidence.read_text(encoding="utf-8")),
            expected_revision=args.cleanup_revision,
            expected_scope=args.cleanup_scope,
        )
        if not isinstance(verified_cleanup.payload, CleanupEvidencePayload):
            raise ValueError("cleanup evidence payload type is invalid")
        cleanup_result = CleanupEvidencePolicy().evaluate(verified_cleanup.payload)
        cleanup_artifact = verified_cleanup.bundle.artifact
        cleanup_manifest = cleanup_artifact.envelope.input_manifest
        if (
            cleanup_artifact.verification_status is not VerificationStatus.PASS
            or cleanup_artifact.verification_status
            is not cleanup_result.verification_status
            or cleanup_artifact.promotion_decision
            is not cleanup_result.promotion_decision
            or tuple(cleanup_artifact.gate_codes) != cleanup_result.gate_codes
            or len(cleanup_manifest) != 2
            or cleanup_manifest[0].path != "release-preflight-evidence"
            or cleanup_manifest[0].receipt_sha256
            != canonical_sha256(verified_release.bundle.receipt)
        ):
            raise ValueError("cleanup evidence policy or chain state is invalid")
        record_bytes = args.publication_record.read_bytes()
        if sha256_bytes(record_bytes) != args.expected_record_sha256:
            raise ValueError("publication record hash mismatch")
        record = PublicationRecord.model_validate_json(record_bytes)
        if (
            record.validated_revision != args.release_revision
            or args.cleanup_revision != record.validated_revision
            or output_revision != record.validated_revision
            or record.cleanup_residue_count
            != verified_cleanup.payload.residue_count
            or record.synthetic != verified_cleanup.payload.synthetic
        ):
            raise ValueError("publication revision binding is invalid")
    except (
        AcceptanceConfigurationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ):
        print("\n".join(format_blocked_output(("PUBLICATION_INPUT_UNVERIFIED",))))
        return 1

    payload = build_publication_evidence(record)
    policy_result = PublicationEvidencePolicy().evaluate(payload)
    record_artifact = external_record_input(
        path=args.publication_record,
        expected_sha256=args.expected_record_sha256,
    )
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="publication-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-publication-evidence",
        tool_version="1.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=(
            input_artifact_from_bundle(
                path=args.release_evidence,
                logical_path="release-preflight-evidence",
                bundle=verified_release.bundle,
            ),
            input_artifact_from_bundle(
                path=args.cleanup_evidence,
                logical_path="cleanup-evidence",
                bundle=verified_cleanup.bundle,
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
