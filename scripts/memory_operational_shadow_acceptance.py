from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from typing import Mapping

from app.runtime.config.memory import load_effective_memory_config
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    OperationalRcEvidencePayload,
    OperationalRegressionEvidencePayload,
    OperationalSecurityEvidencePayload,
    OperationalShadowEvidencePayload,
    OperationalStagingEvidencePayload,
    OperationalStatusEvidencePayload,
    ProposalReviewEvidencePayload,
    RestoreDrillEvidencePayload,
    ShadowEvidencePayload,
    input_artifact_from_bundle,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.policies import (
    OperationalRcEvidencePolicy,
    OperationalRegressionEvidencePolicy,
    OperationalSecurityEvidencePolicy,
    OperationalShadowEvidencePolicy,
    OperationalStagingEvidencePolicy,
    OperationalStatusEvidencePolicy,
    ProposalReviewEvidencePolicy,
    RestoreDrillEvidencePolicy,
    ShadowEvidencePolicy,
)
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RC_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-rc-evidence-v1.json"
)
DEFAULT_REGRESSION_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-regression-evidence-v1.json"
)
DEFAULT_STAGING_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-staging-evidence-v1.json"
)
DEFAULT_STATUS_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-status-evidence-v1.json"
)
DEFAULT_SECURITY_EVIDENCE = (
    ROOT / "reports" / "memory" / "operational-security-evidence-v1.json"
)
DEFAULT_OUTPUT = (
    ROOT / "reports" / "memory" / "operational-shadow-evidence-v1.json"
)
DEFAULT_PROPOSAL_REVIEW = (
    ROOT / "reports" / "memory" / "proposal-review-evidence-v1.json"
)
DEFAULT_BUDGET_EVIDENCE = (
    ROOT / "reports" / "memory" / "budget-shadow-evidence-v1.json"
)
DEFAULT_WRITE_EVIDENCE = (
    ROOT / "reports" / "memory" / "write-shadow-evidence-v1.json"
)
DEFAULT_READ_EVIDENCE = (
    ROOT / "reports" / "memory" / "read-shadow-evidence-v1.json"
)
DEFAULT_LIFECYCLE_EVIDENCE = (
    ROOT / "reports" / "memory" / "lifecycle-shadow-evidence-v1.json"
)
DEFAULT_RESTORE_EVIDENCE = (
    ROOT / "reports" / "memory" / "restore-drill-evidence-v1.json"
)
SUCCESS_LINES = (
    "MEMORY_SHADOW_RC=REPRODUCIBLE",
    "BUDGET_SHADOW_STAGING=PASS",
    "PRINCIPAL_WRITE_SHADOW_STAGING=PASS",
    "PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS",
    "CONSENT_DELETION_RESTORE_DRILL=PASS",
    "PRODUCTION_SHADOW_APPROVAL_REQUIRED",
    "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    "PRODUCTION_OBSERVATION=NOT_RUN",
)
_PRIVATE_KEYS = frozenset(
    {
        "session_id",
        "principal_id",
        "fact_id",
        "question_id",
        "message_id",
        "normalized_fact",
        "source_excerpt",
        "source_manifest_sha256",
        "artifact_ref",
        "provider_payload",
        "prompt",
        "answer",
        "resume",
        "dsn",
        "database_fingerprint",
        "table_prefix",
    }
)


class AcceptanceBlocked(RuntimeError):
    def __init__(self, codes) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__("operational Memory Shadow acceptance blocked")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _shadow_evidence_passes(
    value: object,
    *,
    minimum_samples: int,
) -> bool:
    return (
        isinstance(value, ShadowEvidencePayload)
        and ShadowEvidencePolicy(minimum_samples=minimum_samples)
        .evaluate(value, production_scope=False)
        .verification_status
        is VerificationStatus.PASS
    )


def _restore_evidence_passes(value: object) -> bool:
    return (
        isinstance(value, RestoreDrillEvidencePayload)
        and RestoreDrillEvidencePolicy().evaluate(value).verification_status
        is VerificationStatus.PASS
    )


def _operational_input_passes(value: object, payload_type, policy) -> bool:
    return (
        isinstance(value, payload_type)
        and policy.evaluate(value).verification_status is VerificationStatus.PASS
    )


def evaluate_operational_shadow(
    bundle: Mapping[str, object]
) -> tuple[str, ...]:
    codes: list[str] = []
    rc = bundle.get("rc")
    if not _operational_input_passes(
        rc,
        OperationalRcEvidencePayload,
        OperationalRcEvidencePolicy(),
    ):
        codes.append("RC_EVIDENCE_UNVERIFIED")

    regression = bundle.get("regression")
    if not _operational_input_passes(
        regression,
        OperationalRegressionEvidencePayload,
        OperationalRegressionEvidencePolicy(),
    ):
        codes.append("REGRESSION_EVIDENCE_UNVERIFIED")

    staging = bundle.get("staging")
    if not _operational_input_passes(
        staging,
        OperationalStagingEvidencePayload,
        OperationalStagingEvidencePolicy(),
    ):
        codes.append("STAGING_EVIDENCE_UNVERIFIED")

    budget = bundle.get("budget")
    if not _shadow_evidence_passes(budget, minimum_samples=300):
        codes.append("BUDGET_SHADOW_EVIDENCE_UNVERIFIED")

    write = bundle.get("write")
    if not _shadow_evidence_passes(write, minimum_samples=300):
        codes.append("PRINCIPAL_WRITE_SHADOW_EVIDENCE_UNVERIFIED")

    quality_value = bundle.get("quality")
    quality = (
        quality_value
        if isinstance(quality_value, ProposalReviewEvidencePayload)
        else None
    )
    if quality is None:
        codes.append("PROPOSAL_REVIEW_EVIDENCE_UNVERIFIED")
    elif (
        ProposalReviewEvidencePolicy().evaluate(quality).verification_status
        is not VerificationStatus.PASS
        or quality.review_case_count < 300
    ):
        codes.append("PROPOSAL_QUALITY_FAILED")
    if quality is not None and quality.label_counts.privacy_sensitive != 0:
        codes.append("PROPOSAL_PRIVACY_SENSITIVE")
    if quality is not None and quality.stale_source_accepted_count != 0:
        codes.append("PROPOSAL_STALE_SOURCE_ACCEPTED")

    read = bundle.get("read")
    if not _shadow_evidence_passes(read, minimum_samples=300):
        codes.append("PRINCIPAL_READ_SHADOW_EVIDENCE_UNVERIFIED")

    lifecycle = bundle.get("lifecycle")
    if not _shadow_evidence_passes(lifecycle, minimum_samples=5):
        codes.append("CONSENT_LIFECYCLE_EVIDENCE_UNVERIFIED")

    restore = bundle.get("restore")
    if not _restore_evidence_passes(restore):
        codes.append("RESTORE_DRILL_EVIDENCE_UNVERIFIED")

    status = bundle.get("status")
    if not _operational_input_passes(
        status,
        OperationalStatusEvidencePayload,
        OperationalStatusEvidencePolicy(),
    ):
        codes.append("STATUS_EVIDENCE_UNVERIFIED")

    security = bundle.get("security")
    if not _operational_input_passes(
        security,
        OperationalSecurityEvidencePayload,
        OperationalSecurityEvidencePolicy(),
    ):
        codes.append("SECURITY_EVIDENCE_UNVERIFIED")

    repository = _mapping(bundle.get("repository"))
    if not repository.get("safe_defaults"):
        codes.append("SAFE_DEFAULTS_NOT_DISABLED")
    if not repository.get("consume_rejected"):
        codes.append("CONSUME_NOT_REJECTED")
    if not repository.get("rc_revision_is_ancestor"):
        codes.append("RC_REVISION_NOT_ANCESTOR")

    if isinstance(restore, RestoreDrillEvidencePayload):
        if restore.production_observation != "NOT_RUN":
            codes.append("PRODUCTION_OBSERVATION_CONTRACT_INVALID")
        if restore.long_term_memory_consumption != "BLOCKED":
            codes.append("LONG_TERM_CONSUMPTION_NOT_BLOCKED")

    if codes:
        raise AcceptanceBlocked(codes)
    return SUCCESS_LINES


def build_acceptance_evidence(
    bundle: Mapping[str, object]
) -> OperationalShadowEvidencePayload:
    evaluate_operational_shadow(bundle)
    rc = bundle["rc"]
    if not isinstance(rc, OperationalRcEvidencePayload):
        raise AcceptanceBlocked(("RC_EVIDENCE_UNVERIFIED",))
    regression = bundle["regression"]
    if not isinstance(regression, OperationalRegressionEvidencePayload):
        raise AcceptanceBlocked(("REGRESSION_EVIDENCE_UNVERIFIED",))
    budget = bundle["budget"]
    write = bundle["write"]
    quality = bundle["quality"]
    if not isinstance(quality, ProposalReviewEvidencePayload):
        raise AcceptanceBlocked(("PROPOSAL_REVIEW_EVIDENCE_UNVERIFIED",))
    if not isinstance(budget, ShadowEvidencePayload):
        raise AcceptanceBlocked(("BUDGET_SHADOW_EVIDENCE_UNVERIFIED",))
    if not isinstance(write, ShadowEvidencePayload):
        raise AcceptanceBlocked(("PRINCIPAL_WRITE_SHADOW_EVIDENCE_UNVERIFIED",))
    read = bundle["read"]
    if not isinstance(read, ShadowEvidencePayload):
        raise AcceptanceBlocked(("PRINCIPAL_READ_SHADOW_EVIDENCE_UNVERIFIED",))
    restore = bundle["restore"]
    if not isinstance(restore, RestoreDrillEvidencePayload):
        raise AcceptanceBlocked(("RESTORE_DRILL_EVIDENCE_UNVERIFIED",))
    security = bundle["security"]
    if not isinstance(security, OperationalSecurityEvidencePayload):
        raise AcceptanceBlocked(("SECURITY_EVIDENCE_UNVERIFIED",))
    repository = _mapping(bundle["repository"])
    evidence = OperationalShadowEvidencePayload(
        schema_version="operational-shadow-evidence-v1",
        validated_rc_revision=rc.validated_rc_revision,
        validation_revision=regression.validated_revision,
        environment_category="isolated_staging",
        observation_profile="B",
        full_python_passed=regression.full_python_passed_count,
        postgres_executed=regression.postgres_executed,
        frontend_modules=regression.frontend_modules_transformed,
        browser_passed=regression.browser_passed_count,
        budget_followup_samples=budget.sample_count,
        principal_write_samples=write.sample_count,
        proposal_review_cases=quality.review_case_count,
        principal_read_samples=read.sample_count,
        restore_cycles=restore.restore_cycles,
        restore_fault_boundaries=restore.fault_boundaries_exercised,
        artifacts_audited=security.artifacts_audited,
        test_listener_residue=regression.test_listener_residue,
        isolated_relation_residue=regression.isolated_relation_residue,
        private_data_residue=restore.restored_private_data_residue,
        operational_gates_passed=True,
        safe_defaults=repository.get("safe_defaults") is True,
        consume_rejected=repository.get("consume_rejected") is True,
        production_approval_required=True,
        long_term_consumption_blocked=True,
        production_observation_not_run=True,
        synthetic=True,
    )
    validate_acceptance_artifact(evidence)
    return evidence


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "MEMORY_OPERATIONAL_SHADOW=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    )


def repository_snapshot(rc_revision: str) -> dict[str, bool]:
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
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", rc_revision, "HEAD"],
        cwd=ROOT,
        capture_output=True,
    ).returncode == 0
    return {
        "safe_defaults": safe_defaults,
        "consume_rejected": consume_rejected,
        "rc_revision_is_ancestor": ancestor,
    }


def _policy_matches_verified_artifact(verified, policy_result) -> bool:
    artifact = verified.bundle.artifact
    return (
        policy_result.verification_status is VerificationStatus.PASS
        and artifact.verification_status == policy_result.verification_status
        and artifact.promotion_decision == policy_result.promotion_decision
        and tuple(artifact.gate_codes) == policy_result.gate_codes
    )


def verify_shadow_input(
    *,
    path: Path,
    revision: str,
    scope: str,
    minimum_samples: int,
    verifier: EvidenceVerifier,
):
    verified = verifier.verify(
        json.loads(path.read_text(encoding="utf-8")),
        expected_revision=revision,
        expected_scope=scope,
    )
    if not isinstance(verified.payload, ShadowEvidencePayload):
        raise ValueError("shadow input payload type is invalid")
    policy_result = ShadowEvidencePolicy(
        minimum_samples=minimum_samples
    ).evaluate(verified.payload, production_scope=False)
    if not _policy_matches_verified_artifact(verified, policy_result):
        raise ValueError("shadow input policy result is invalid")
    return verified


def verify_restore_input(
    *,
    path: Path,
    revision: str,
    scope: str,
    verifier: EvidenceVerifier,
):
    verified = verifier.verify(
        json.loads(path.read_text(encoding="utf-8")),
        expected_revision=revision,
        expected_scope=scope,
    )
    if not isinstance(verified.payload, RestoreDrillEvidencePayload):
        raise ValueError("restore input payload type is invalid")
    policy_result = RestoreDrillEvidencePolicy().evaluate(verified.payload)
    if not _policy_matches_verified_artifact(verified, policy_result):
        raise ValueError("restore input policy result is invalid")
    return verified


def verify_operational_input(
    *,
    path: Path,
    revision: str,
    scope: str,
    payload_type,
    policy,
    verifier: EvidenceVerifier,
):
    verified = verifier.verify(
        json.loads(path.read_text(encoding="utf-8")),
        expected_revision=revision,
        expected_scope=scope,
    )
    if not isinstance(verified.payload, payload_type):
        raise ValueError("operational input payload type is invalid")
    policy_result = policy.evaluate(verified.payload)
    if not _policy_matches_verified_artifact(verified, policy_result):
        raise ValueError("operational input policy result is invalid")
    return verified


def load_default_bundle(
    *,
    rc: OperationalRcEvidencePayload,
    regression: OperationalRegressionEvidencePayload,
    staging: OperationalStagingEvidencePayload,
    status: OperationalStatusEvidencePayload,
    security: OperationalSecurityEvidencePayload,
    proposal_review: ProposalReviewEvidencePayload,
    budget: ShadowEvidencePayload,
    write: ShadowEvidencePayload,
    read: ShadowEvidencePayload,
    lifecycle: ShadowEvidencePayload,
    restore: RestoreDrillEvidencePayload,
) -> dict[str, object]:
    return {
        "rc": rc,
        "regression": regression,
        "staging": staging,
        "budget": budget,
        "write": write,
        "quality": proposal_review,
        "read": read,
        "lifecycle": lifecycle,
        "restore": restore,
        "status": status,
        "security": security,
        "repository": repository_snapshot(rc.validated_rc_revision),
    }


def _audit_private_keys(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _PRIVATE_KEYS or _audit_private_keys(item):
                return True
    elif isinstance(value, list):
        return any(_audit_private_keys(item) for item in value)
    return False


def validate_acceptance_artifact(
    value: Mapping[str, object] | OperationalShadowEvidencePayload,
) -> None:
    record = (
        value.model_dump(mode="json")
        if isinstance(value, OperationalShadowEvidencePayload)
        else value
    )
    if _audit_private_keys(record):
        raise RuntimeError("acceptance evidence contains private data")
    rendered = json.dumps(record, sort_keys=True, ensure_ascii=False).casefold()
    if "postgresql://" in rendered or "redis://" in rendered:
        raise RuntimeError("acceptance evidence contains private connection data")
    if record.get("production_observation_not_run") is not True:
        raise RuntimeError("acceptance evidence production state is invalid")
    if record.get("long_term_consumption_blocked") is not True:
        raise RuntimeError("acceptance evidence consumption state is invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gate operational Memory Shadow approval material."
    )
    parser.add_argument(
        "--rc-evidence",
        type=Path,
        default=DEFAULT_RC_EVIDENCE,
    )
    parser.add_argument(
        "--regression-evidence",
        type=Path,
        default=DEFAULT_REGRESSION_EVIDENCE,
    )
    parser.add_argument(
        "--staging-evidence",
        type=Path,
        default=DEFAULT_STAGING_EVIDENCE,
    )
    parser.add_argument(
        "--status-evidence",
        type=Path,
        default=DEFAULT_STATUS_EVIDENCE,
    )
    parser.add_argument(
        "--security-evidence",
        type=Path,
        default=DEFAULT_SECURITY_EVIDENCE,
    )
    parser.add_argument(
        "--proposal-review-evidence",
        type=Path,
        default=DEFAULT_PROPOSAL_REVIEW,
    )
    parser.add_argument("--proposal-review-revision")
    parser.add_argument(
        "--proposal-review-scope",
        default="memory.proposal-review.controlled",
    )
    parser.add_argument("--input-revision")
    parser.add_argument(
        "--budget-evidence",
        type=Path,
        default=DEFAULT_BUDGET_EVIDENCE,
    )
    parser.add_argument(
        "--write-evidence",
        type=Path,
        default=DEFAULT_WRITE_EVIDENCE,
    )
    parser.add_argument(
        "--read-evidence",
        type=Path,
        default=DEFAULT_READ_EVIDENCE,
    )
    parser.add_argument(
        "--lifecycle-evidence",
        type=Path,
        default=DEFAULT_LIFECYCLE_EVIDENCE,
    )
    parser.add_argument(
        "--restore-evidence",
        type=Path,
        default=DEFAULT_RESTORE_EVIDENCE,
    )
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="memory.operational-shadow.controlled",
    )
    parser.add_argument("--evidence-output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        proposal_review_revision = (
            args.proposal_review_revision
            or require_environment_value(os.environ, "PROPOSAL_REVIEW_REVISION")
        )
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
        input_revision = (
            args.input_revision
            or require_environment_value(
                os.environ,
                "OPERATIONAL_INPUT_REVISION",
            )
        )
        signer = load_receipt_signer(os.environ)
        input_verifier = EvidenceVerifier(
            registry=EvidenceRegistry.default(),
            receipt_signer=signer,
        )
        verified_rc = verify_operational_input(
            path=args.rc_evidence,
            revision=input_revision,
            scope="memory.operational-rc.controlled",
            payload_type=OperationalRcEvidencePayload,
            policy=OperationalRcEvidencePolicy(),
            verifier=input_verifier,
        )
        verified_regression = verify_operational_input(
            path=args.regression_evidence,
            revision=input_revision,
            scope="memory.operational-regression.controlled",
            payload_type=OperationalRegressionEvidencePayload,
            policy=OperationalRegressionEvidencePolicy(),
            verifier=input_verifier,
        )
        verified_staging = verify_operational_input(
            path=args.staging_evidence,
            revision=input_revision,
            scope="memory.staging-preflight.controlled",
            payload_type=OperationalStagingEvidencePayload,
            policy=OperationalStagingEvidencePolicy(),
            verifier=input_verifier,
        )
        verified_status = verify_operational_input(
            path=args.status_evidence,
            revision=input_revision,
            scope="memory.shadow-status.controlled",
            payload_type=OperationalStatusEvidencePayload,
            policy=OperationalStatusEvidencePolicy(),
            verifier=input_verifier,
        )
        verified_security = verify_operational_input(
            path=args.security_evidence,
            revision=input_revision,
            scope="memory.shadow-security.controlled",
            payload_type=OperationalSecurityEvidencePayload,
            policy=OperationalSecurityEvidencePolicy(),
            verifier=input_verifier,
        )
        proposal_value = json.loads(
            args.proposal_review_evidence.read_text(encoding="utf-8")
        )
        verified_review = input_verifier.verify(
            proposal_value,
            expected_revision=proposal_review_revision,
            expected_scope=args.proposal_review_scope,
        )
        if not isinstance(verified_review.payload, ProposalReviewEvidencePayload):
            raise ValueError("proposal review payload type is invalid")
        review_policy_result = ProposalReviewEvidencePolicy().evaluate(
            verified_review.payload
        )
        if not _policy_matches_verified_artifact(
            verified_review,
            review_policy_result,
        ):
            raise ValueError("proposal review policy result is invalid")
        verified_budget = verify_shadow_input(
            path=args.budget_evidence,
            revision=input_revision,
            scope="memory.budget-shadow.controlled",
            minimum_samples=300,
            verifier=input_verifier,
        )
        verified_write = verify_shadow_input(
            path=args.write_evidence,
            revision=input_revision,
            scope="memory.write-shadow.controlled",
            minimum_samples=300,
            verifier=input_verifier,
        )
        verified_read = verify_shadow_input(
            path=args.read_evidence,
            revision=input_revision,
            scope="memory.read-shadow.controlled",
            minimum_samples=300,
            verifier=input_verifier,
        )
        verified_lifecycle = verify_shadow_input(
            path=args.lifecycle_evidence,
            revision=input_revision,
            scope="memory.lifecycle-shadow.controlled",
            minimum_samples=5,
            verifier=input_verifier,
        )
        verified_restore = verify_restore_input(
            path=args.restore_evidence,
            revision=input_revision,
            scope="memory.shadow.restore-drill",
            verifier=input_verifier,
        )
    except (AcceptanceConfigurationError, OSError, ValueError, json.JSONDecodeError):
        print(
            "\n".join(
                format_blocked_output(("OPERATIONAL_INPUT_EVIDENCE_UNVERIFIED",))
            )
        )
        return 1
    bundle = load_default_bundle(
        rc=verified_rc.payload,
        regression=verified_regression.payload,
        staging=verified_staging.payload,
        status=verified_status.payload,
        security=verified_security.payload,
        proposal_review=verified_review.payload,
        budget=verified_budget.payload,
        write=verified_write.payload,
        read=verified_read.payload,
        lifecycle=verified_lifecycle.payload,
        restore=verified_restore.payload,
    )
    try:
        lines = evaluate_operational_shadow(bundle)
        evidence = build_acceptance_evidence(bundle)
    except AcceptanceBlocked as exc:
        print("\n".join(format_blocked_output(exc.codes)))
        return 1
    policy_result = OperationalShadowEvidencePolicy().evaluate(evidence)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="operational-shadow-evidence",
        payload=evidence,
        policy_result=policy_result,
        producer="scripts.memory-operational-shadow-acceptance",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
        input_manifest=(
            input_artifact_from_bundle(
                path=args.rc_evidence,
                logical_path="operational-rc-evidence",
                bundle=verified_rc.bundle,
            ),
            input_artifact_from_bundle(
                path=args.regression_evidence,
                logical_path="operational-regression-evidence",
                bundle=verified_regression.bundle,
            ),
            input_artifact_from_bundle(
                path=args.staging_evidence,
                logical_path="operational-staging-evidence",
                bundle=verified_staging.bundle,
            ),
            input_artifact_from_bundle(
                path=args.status_evidence,
                logical_path="operational-status-evidence",
                bundle=verified_status.bundle,
            ),
            input_artifact_from_bundle(
                path=args.security_evidence,
                logical_path="operational-security-evidence",
                bundle=verified_security.bundle,
            ),
            input_artifact_from_bundle(
                path=args.proposal_review_evidence,
                logical_path="proposal-review-evidence",
                bundle=verified_review.bundle,
            ),
            input_artifact_from_bundle(
                path=args.budget_evidence,
                logical_path="budget-shadow-evidence",
                bundle=verified_budget.bundle,
            ),
            input_artifact_from_bundle(
                path=args.write_evidence,
                logical_path="write-shadow-evidence",
                bundle=verified_write.bundle,
            ),
            input_artifact_from_bundle(
                path=args.read_evidence,
                logical_path="read-shadow-evidence",
                bundle=verified_read.bundle,
            ),
            input_artifact_from_bundle(
                path=args.lifecycle_evidence,
                logical_path="lifecycle-shadow-evidence",
                bundle=verified_lifecycle.bundle,
            ),
            input_artifact_from_bundle(
                path=args.restore_evidence,
                logical_path="restore-drill-evidence",
                bundle=verified_restore.bundle,
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
    ).write(args.evidence_output, bundle)
    print("\n".join((*render_gate_lines(bundle), *lines)))
    return 0 if policy_result.verification_status is VerificationStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
