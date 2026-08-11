from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceBundle,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    ProposalReviewCaseSetPayload,
    ProposalReviewEvidencePayload,
    ShadowEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.digest import canonical_sha256
from contracts.evidence.rendering import render_gate_lines
from contracts.policies import ProposalReviewEvidencePolicy
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "memory" / "proposal-review-evidence-v1.json"
TOOL_VERSION = "2.0.0"


def evaluate_quality(
    *,
    write_bundle: EvidenceBundle,
    write_observation: ShadowEvidencePayload,
    review_cases: ProposalReviewCaseSetPayload,
) -> ProposalReviewEvidencePayload:
    source_revision = write_bundle.artifact.envelope.revision
    source_receipt_sha256 = canonical_sha256(write_bundle.receipt)
    if review_cases.source_write_revision != source_revision:
        raise ValueError("review cases source revision does not match Write Shadow")
    if review_cases.source_write_receipt_sha256 != source_receipt_sha256:
        raise ValueError("review cases source receipt does not match Write Shadow")
    if write_observation.sample_count < len(review_cases.cases):
        raise ValueError("Write Shadow has fewer observations than review cases")
    if write_observation.violations:
        raise ValueError("Write Shadow contains blocking violations")

    counts = Counter(item.label for item in review_cases.cases)
    approved = sum(item.accepted for item in review_cases.cases)
    unresolved = counts["review_unavailable"]
    rejected = sum(
        not item.accepted and item.label != "review_unavailable"
        for item in review_cases.cases
    )
    label_counts = {
        label: counts[label]
        for label in (
            "correct",
            "unsupported",
            "over_generalized",
            "wrong_taxonomy",
            "stale_source",
            "conflict",
            "privacy_sensitive",
            "not_useful",
            "duplicate",
            "review_unavailable",
        )
    }
    return ProposalReviewEvidencePayload(
        schema_version="proposal-review-evidence-v1",
        review_case_count=len(review_cases.cases),
        revision_count=len({source_revision, review_cases.review_revision}),
        source_write_revision=source_revision,
        source_write_receipt_sha256=source_receipt_sha256,
        review_revision=review_cases.review_revision,
        approved_count=approved,
        rejected_count=rejected,
        unresolved_count=unresolved,
        label_counts=label_counts,
        stale_source_accepted_count=sum(
            item.accepted and item.label == "stale_source"
            for item in review_cases.cases
        ),
        raw_content_persisted=False,
        synthetic=write_observation.synthetic or review_cases.synthetic,
        review_digest=canonical_sha256(review_cases),
    )


def _load_verified(
    path: Path,
    *,
    verifier: EvidenceVerifier,
    revision: str,
    scope: str,
) -> tuple[EvidenceBundle, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    verified = verifier.verify(
        value,
        expected_revision=revision,
        expected_scope=scope,
    )
    return verified.bundle, verified.payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Principal Memory proposal review")
    parser.add_argument("--write-observation", type=Path, required=True)
    parser.add_argument("--review-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--write-scope",
        default="memory.write-shadow.controlled",
    )
    parser.add_argument(
        "--review-cases-scope",
        default="memory.proposal-review-cases.controlled",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        signer = load_receipt_signer(os.environ)
        output_revision = require_environment_value(os.environ, "EVIDENCE_REVISION")
        write_revision = require_environment_value(
            os.environ,
            "WRITE_SHADOW_EVIDENCE_REVISION",
        )
        cases_revision = require_environment_value(
            os.environ,
            "PROPOSAL_REVIEW_CASES_REVISION",
        )
        verifier = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        )
        write_bundle, write_payload = _load_verified(
            args.write_observation,
            verifier=verifier,
            revision=write_revision,
            scope=args.write_scope,
        )
        cases_bundle, cases_payload = _load_verified(
            args.review_cases,
            verifier=verifier,
            revision=cases_revision,
            scope=args.review_cases_scope,
        )
        if not isinstance(write_payload, ShadowEvidencePayload):
            raise ValueError("Write Shadow input has the wrong payload type")
        if not isinstance(cases_payload, ProposalReviewCaseSetPayload):
            raise ValueError("review cases input has the wrong payload type")
        payload = evaluate_quality(
            write_bundle=write_bundle,
            write_observation=write_payload,
            review_cases=cases_payload,
        )
        result = ProposalReviewEvidencePolicy().evaluate(payload)
        output_scope = (
            "memory.proposal-review.controlled"
            if payload.synthetic
            else "memory.proposal-review.production"
        )
        input_manifest = (
            input_artifact_from_bundle(
                path=args.write_observation,
                logical_path="inputs/write-shadow-evidence.json",
                bundle=write_bundle,
            ),
            input_artifact_from_bundle(
                path=args.review_cases,
                logical_path="inputs/proposal-review-cases.json",
                bundle=cases_bundle,
            ),
        )
        issuer = EvidenceIssuer(signer=signer)
        bundle = issuer.issue(
            payload_type="proposal-review-evidence",
            payload=payload,
            policy_result=result,
            producer="scripts.principal-memory-proposal-review",
            tool_version=TOOL_VERSION,
            revision=output_revision,
            scope=output_scope,
            input_manifest=input_manifest,
        )

        def verify_written(value: dict) -> None:
            verifier.verify(
                value,
                expected_revision=output_revision,
                expected_scope=output_scope,
            )

        AtomicEvidenceWriter(post_write_verifier=verify_written).write(
            args.output,
            bundle,
        )
    except (AcceptanceConfigurationError, OSError, ValueError):
        print("VERIFICATION_STATUS=BLOCKED")
        print("PROMOTION_DECISION=HOLD")
        print("GATE=PROPOSAL_REVIEW_INPUT_INVALID")
        return 1

    for line in render_gate_lines(bundle):
        print(line)
    print("READ_SHADOW_AUTHORIZED=false")
    print(f"artifact={args.output.as_posix()}")
    return 0 if bundle.artifact.verification_status.value == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
