from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Callable, Mapping

from pydantic import BaseModel

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    InputArtifact,
    OperationalRcEvidencePayload,
    OperationalRegressionEvidencePayload,
    OperationalSecurityEvidencePayload,
    OperationalStagingEvidencePayload,
    OperationalStatusEvidencePayload,
)
from contracts.evidence.digest import sha256_bytes
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.policies import (
    OperationalRcEvidencePolicy,
    OperationalRegressionEvidencePolicy,
    OperationalSecurityEvidencePolicy,
    OperationalStagingEvidencePolicy,
    OperationalStatusEvidencePolicy,
)
from contracts.policies.evidence import EvidencePolicyResult
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    load_receipt_signer,
    require_environment_value,
)


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class OperationalInputProfile:
    payload_type: str
    model: type[BaseModel]
    build_payload: Callable[[Mapping[str, object], bool], BaseModel]
    policy: Callable[[BaseModel], EvidencePolicyResult]
    scope: str
    output: Path


def _object(value: Mapping[str, object], field: str) -> Mapping[str, object]:
    item = value.get(field)
    if not isinstance(item, Mapping):
        raise ValueError(f"{field} must be an object")
    return item


def _strict_bool(value: Mapping[str, object], field: str) -> bool:
    item = value.get(field)
    if type(item) is not bool:
        raise ValueError(f"{field} must be a strict boolean")
    return item


def _strict_int(
    value: Mapping[str, object],
    field: str,
    *,
    default: int | None = None,
) -> int:
    item = value.get(field, default)
    if type(item) is not int or item < 0:
        raise ValueError(f"{field} must be a non-negative strict integer")
    return item


def _strict_text(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} must be non-empty text")
    return item


def _strict_codes(value: Mapping[str, object], field: str) -> list[str]:
    item = value.get(field)
    if not isinstance(item, list) or not all(isinstance(code, str) for code in item):
        raise ValueError(f"{field} must be a list of gate codes")
    return list(item)


def _build_rc_payload(
    value: Mapping[str, object],
    synthetic: bool,
) -> OperationalRcEvidencePayload:
    if value.get("schema_version") != "memory-validation-operational-evidence-v1":
        raise ValueError("RC record schema is invalid")
    release = _object(value, "release_candidate")
    full_python = _object(value, "full_python")
    postgres = _object(value, "pg_runtime")
    frontend = _object(value, "frontend_build")
    browser = _object(value, "full_browser")
    metrics = _object(value, "durable_metrics")
    cleanup = _object(value, "cleanup")
    safe_defaults = _object(value, "safe_defaults")
    principal_memory = _object(value, "principal_memory")
    return OperationalRcEvidencePayload(
        schema_version="operational-rc-evidence-v1",
        validated_rc_revision=_strict_text(value, "validated_rc_revision"),
        release_candidate_passed=_strict_bool(release, "passed"),
        clean_detached_worktree=_strict_bool(release, "clean_detached_worktree"),
        shadow_modes_changed=_strict_bool(release, "shadow_modes_changed"),
        full_python_passed=_strict_bool(full_python, "passed"),
        full_python_passed_count=_strict_int(full_python, "passed_count"),
        full_python_skipped=_strict_int(full_python, "skipped"),
        full_python_failed=_strict_int(full_python, "failed"),
        postgres_passed=_strict_bool(postgres, "passed"),
        postgres_executed=_strict_int(postgres, "executed"),
        postgres_failed=_strict_int(postgres, "failed", default=0),
        postgres_cleanup_verified=_strict_bool(postgres, "cleanup_verified"),
        frontend_build_passed=_strict_bool(frontend, "passed"),
        frontend_modules_transformed=_strict_int(frontend, "modules_transformed"),
        browser_passed=_strict_bool(browser, "passed"),
        browser_scope=_strict_text(browser, "scope"),
        browser_passed_count=_strict_int(browser, "passed_count"),
        browser_skipped=_strict_int(browser, "skipped"),
        browser_failed=_strict_int(browser, "failed"),
        durable_metrics_passed=_strict_bool(metrics, "passed"),
        durable_metrics_store_kind=_strict_text(metrics, "store_kind"),
        durable_metrics_data_complete=_strict_bool(metrics, "data_complete"),
        test_listener_residue=_strict_int(cleanup, "test_listeners"),
        isolated_relation_residue=_strict_int(
            cleanup,
            "isolated_test_relation_residue",
        ),
        safe_defaults_passed=_strict_bool(safe_defaults, "passed"),
        consume_rejected=_strict_bool(safe_defaults, "consume_rejected"),
        long_term_memory_consumption=_strict_text(
            principal_memory,
            "consumption",
        ),
        production_observation=_strict_text(value, "production_observation"),
        synthetic=synthetic,
    )


def _build_regression_payload(
    value: Mapping[str, object],
    synthetic: bool,
) -> OperationalRegressionEvidencePayload:
    if value.get("schema_version") != "memory-operational-regression-evidence-v1":
        raise ValueError("regression record schema is invalid")
    full_python = _object(value, "full_python")
    postgres = _object(value, "pg_runtime")
    frontend = _object(value, "frontend_build")
    browser = _object(value, "full_browser")
    compileall = _object(value, "compileall")
    diff_check = _object(value, "diff_check")
    cleanup = _object(value, "cleanup")
    return OperationalRegressionEvidencePayload(
        schema_version="operational-regression-evidence-v1",
        validated_revision=_strict_text(value, "validated_revision"),
        clean_detached_worktree=_strict_bool(value, "clean_detached_worktree"),
        real_provider_calls=_strict_int(value, "real_provider_calls"),
        full_python_passed=_strict_bool(full_python, "passed"),
        full_python_passed_count=_strict_int(full_python, "passed_count"),
        full_python_skipped=_strict_int(full_python, "skipped"),
        full_python_failed=_strict_int(full_python, "failed"),
        postgres_passed=_strict_bool(postgres, "passed"),
        postgres_executed=_strict_int(postgres, "executed"),
        postgres_failed=_strict_int(postgres, "failed"),
        frontend_build_passed=_strict_bool(frontend, "passed"),
        frontend_modules_transformed=_strict_int(frontend, "modules_transformed"),
        browser_passed=_strict_bool(browser, "passed"),
        browser_scope=_strict_text(browser, "scope"),
        browser_passed_count=_strict_int(browser, "passed_count"),
        browser_skipped=_strict_int(browser, "skipped"),
        browser_failed=_strict_int(browser, "failed"),
        compileall_passed=_strict_bool(compileall, "passed"),
        diff_check_passed=_strict_bool(diff_check, "passed"),
        test_listener_residue=_strict_int(cleanup, "test_listeners"),
        isolated_relation_residue=_strict_int(
            cleanup,
            "isolated_test_relation_residue",
        ),
        long_term_memory_consumption=_strict_text(
            value,
            "long_term_memory_consumption",
        ),
        production_observation=_strict_text(value, "production_observation"),
        synthetic=synthetic,
    )


def _build_staging_payload(
    value: Mapping[str, object],
    synthetic: bool,
) -> OperationalStagingEvidencePayload:
    if value.get("schema_version") != "memory-shadow-staging-preflight-v1":
        raise ValueError("staging record schema is invalid")
    return OperationalStagingEvidencePayload(
        schema_version="operational-staging-evidence-v1",
        mode=_strict_text(value, "mode"),
        passed=_strict_bool(value, "passed"),
        gate_codes=_strict_codes(value, "gate_codes"),
        validated_rc_revision=_strict_text(value, "validated_rc_revision"),
        environment_category=_strict_text(value, "environment_category"),
        observation_profile=_strict_text(value, "observation_profile"),
        configuration_changed=_strict_bool(value, "configuration_changed"),
        all_memory_shadows_disabled=_strict_bool(
            value,
            "all_memory_shadows_disabled",
        ),
        real_provider_allowed=_strict_bool(value, "real_provider_allowed"),
        migration_scope=_strict_text(value, "migration_scope"),
        database_fingerprint_matches=_strict_bool(
            value,
            "database_fingerprint_matches",
        ),
        prefix_valid=_strict_bool(value, "prefix_valid"),
        migration_validated=_strict_bool(value, "migration_validated"),
        durable_metrics_validated=_strict_bool(
            value,
            "durable_metrics_validated",
        ),
        rollback_verified=_strict_bool(value, "rollback_verified"),
        cleanup_residue=_strict_int(value, "cleanup_residue"),
        live_validation_executed=_strict_bool(
            value,
            "live_validation_executed",
        ),
        worker_leasing_started=_strict_bool(value, "worker_leasing_started"),
        long_term_memory_consumption=_strict_text(
            value,
            "long_term_memory_consumption",
        ),
        production_observation=_strict_text(value, "production_observation"),
        synthetic=synthetic,
    )


def _build_status_payload(
    value: Mapping[str, object],
    synthetic: bool,
) -> OperationalStatusEvidencePayload:
    if value.get("schema_version") != "memory-shadow-status-v1":
        raise ValueError("status record schema is invalid")
    automatic_stop = _object(value, "automatic_stop")
    budget = _object(value, "budget")
    write = _object(value, "write")
    read = _object(value, "read")
    return OperationalStatusEvidencePayload(
        schema_version="operational-status-evidence-v1",
        automatic_stop_triggered=_strict_bool(automatic_stop, "triggered"),
        automatic_stop_gate_codes=_strict_codes(automatic_stop, "gate_codes"),
        expansion_allowed=_strict_bool(automatic_stop, "expansion_allowed"),
        hold_codes=_strict_codes(value, "hold_codes"),
        budget_data_complete=_strict_bool(budget, "data_complete"),
        budget_sample_sufficient=_strict_bool(budget, "sample_sufficient"),
        write_sample_sufficient=_strict_bool(write, "sample_sufficient"),
        read_sample_sufficient=_strict_bool(read, "sample_sufficient"),
        prompt_isolation_violation_count=_strict_int(
            read,
            "prompt_isolation_violation_count",
        ),
        configuration_changed=_strict_bool(value, "configuration_changed"),
        long_term_memory_consumption=_strict_text(
            value,
            "long_term_memory_consumption",
        ),
        production_observation=_strict_text(value, "production_observation"),
        synthetic=synthetic,
    )


def _build_security_payload(
    value: Mapping[str, object],
    synthetic: bool,
) -> OperationalSecurityEvidencePayload:
    if value.get("schema_version") != "memory-shadow-security-review-v1":
        raise ValueError("security record schema is invalid")
    return OperationalSecurityEvidencePayload(
        schema_version="operational-security-evidence-v1",
        review_status=_strict_text(value, "review_status"),
        artifact_violations=_strict_int(value, "artifact_violations"),
        artifacts_audited=_strict_int(value, "artifacts_audited"),
        hard_stop_count=_strict_int(value, "hard_stop_count"),
        knowledge_firewall_violations=_strict_int(
            value,
            "knowledge_firewall_violations",
        ),
        protected_taxonomy_hits=_strict_int(value, "protected_taxonomy_hits"),
        prompt_attack_unsafe_writes=_strict_int(
            value,
            "prompt_attack_unsafe_writes",
        ),
        provider_calls=_strict_int(value, "provider_calls"),
        public_knowledge_unchanged=_strict_bool(
            value,
            "public_knowledge_unchanged",
        ),
        configuration_changed=_strict_bool(value, "configuration_changed"),
        long_term_memory_consumption=_strict_text(
            value,
            "long_term_memory_consumption",
        ),
        production_observation=_strict_text(value, "production_observation"),
        synthetic=synthetic,
    )


PROFILES = {
    "rc": OperationalInputProfile(
        payload_type="operational-rc-evidence",
        model=OperationalRcEvidencePayload,
        build_payload=_build_rc_payload,
        policy=OperationalRcEvidencePolicy().evaluate,
        scope="memory.operational-rc.controlled",
        output=ROOT / "reports" / "memory" / "operational-rc-evidence-v1.json",
    ),
    "regression": OperationalInputProfile(
        payload_type="operational-regression-evidence",
        model=OperationalRegressionEvidencePayload,
        build_payload=_build_regression_payload,
        policy=OperationalRegressionEvidencePolicy().evaluate,
        scope="memory.operational-regression.controlled",
        output=(
            ROOT
            / "reports"
            / "memory"
            / "operational-regression-evidence-v1.json"
        ),
    ),
    "staging": OperationalInputProfile(
        payload_type="operational-staging-evidence",
        model=OperationalStagingEvidencePayload,
        build_payload=_build_staging_payload,
        policy=OperationalStagingEvidencePolicy().evaluate,
        scope="memory.staging-preflight.controlled",
        output=(
            ROOT / "reports" / "memory" / "operational-staging-evidence-v1.json"
        ),
    ),
    "status": OperationalInputProfile(
        payload_type="operational-status-evidence",
        model=OperationalStatusEvidencePayload,
        build_payload=_build_status_payload,
        policy=OperationalStatusEvidencePolicy().evaluate,
        scope="memory.shadow-status.controlled",
        output=ROOT / "reports" / "memory" / "operational-status-evidence-v1.json",
    ),
    "security": OperationalInputProfile(
        payload_type="operational-security-evidence",
        model=OperationalSecurityEvidencePayload,
        build_payload=_build_security_payload,
        policy=OperationalSecurityEvidencePolicy().evaluate,
        scope="memory.shadow-security.controlled",
        output=(
            ROOT
            / "reports"
            / "memory"
            / "operational-security-evidence-v1.json"
        ),
    ),
}


def external_record_input(
    *,
    profile: str,
    record_bytes: bytes,
    expected_sha256: str,
) -> InputArtifact:
    return InputArtifact(
        path=f"external-{profile}-operational-record",
        sha256=sha256_bytes(record_bytes),
        receipt_sha256=expected_sha256,
        size_bytes=len(record_bytes),
        media_type="application/json",
    )


def _revision_matches_payload(payload: BaseModel, revision: str) -> bool:
    if isinstance(payload, OperationalRcEvidencePayload):
        return payload.validated_rc_revision == revision
    if isinstance(payload, OperationalRegressionEvidencePayload):
        return payload.validated_revision == revision
    if isinstance(payload, OperationalStagingEvidencePayload):
        return payload.validated_rc_revision == revision
    return True


def format_blocked_output(codes) -> tuple[str, ...]:
    return (
        "OPERATIONAL_INPUT_EVIDENCE=BLOCKED",
        *(f"GATE={code}" for code in sorted(set(codes))),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Issue one protected Operational Shadow prerequisite Bundle."
    )
    parser.add_argument("profile", choices=tuple(PROFILES))
    parser.add_argument("--input-record", type=Path, required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Attest that the source record contains only synthetic evidence.",
    )
    parser.add_argument("--output-revision")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = PROFILES[args.profile]
    output = args.output or profile.output
    try:
        signer = load_receipt_signer(os.environ)
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
        record_bytes = args.input_record.read_bytes()
        if sha256_bytes(record_bytes) != args.expected_input_sha256:
            raise ValueError("operational input record hash mismatch")
        raw = json.loads(record_bytes)
        if not isinstance(raw, dict):
            raise ValueError("operational input record must be an object")
        if args.synthetic is not True:
            raise ValueError("operational input requires synthetic attestation")
        payload = profile.build_payload(raw, args.synthetic)
        if not _revision_matches_payload(payload, output_revision):
            raise ValueError("operational input revision binding is invalid")
        policy_result = profile.policy(payload)
    except (
        AcceptanceConfigurationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ):
        print(
            "\n".join(
                format_blocked_output(("OPERATIONAL_INPUT_RECORD_UNVERIFIED",))
            )
        )
        return 1

    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type=profile.payload_type,
        payload=payload,
        policy_result=policy_result,
        producer="scripts.memory-operational-input-evidence",
        tool_version="1.0.0",
        revision=output_revision,
        scope=profile.scope,
        input_manifest=(
            external_record_input(
                profile=args.profile,
                record_bytes=record_bytes,
                expected_sha256=args.expected_input_sha256,
            ),
        ),
    )
    verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda value: verifier.verify(
            value,
            expected_revision=output_revision,
            expected_scope=profile.scope,
        )
    ).write(output, bundle)
    print("\n".join(render_gate_lines(bundle)))
    print(f"artifact={output.as_posix()}")
    return (
        0
        if policy_result.verification_status is VerificationStatus.PASS
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
