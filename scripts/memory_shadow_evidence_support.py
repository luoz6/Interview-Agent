from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path

from pydantic import BaseModel

from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceBundle,
    EvidenceIssuer,
    InputArtifact,
    EvidenceRegistry,
    EvidenceVerifier,
    ShadowEvidencePayload,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.policies import ShadowEvidencePolicy
from scripts.postgres_acceptance_support import (
    approved_postgres_scope,
    load_receipt_signer,
    require_environment_value,
)


def strict_nonnegative_int(record: Mapping[str, object], field: str) -> int:
    value = record[field]
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def strict_finite_float(record: Mapping[str, object], field: str) -> float:
    value = record[field]
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite float")
    return value


def zero_count_violations(
    record: Mapping[str, object],
    gates_by_field: Mapping[str, str],
) -> list[str]:
    violations = []
    for field, gate in gates_by_field.items():
        if strict_nonnegative_int(record, field) != 0:
            violations.append(gate)
    return violations


def publish_shadow_evidence(
    *,
    payload: ShadowEvidencePayload,
    output: Path,
    producer: str,
    scope: str,
    environ: Mapping[str, str],
    minimum_samples: int,
    input_manifest: Sequence[InputArtifact] = (),
    tool_version: str = "2.0.0",
) -> EvidenceBundle:
    signer = load_receipt_signer(environ)
    revision = require_environment_value(environ, "EVIDENCE_REVISION")
    result = ShadowEvidencePolicy(minimum_samples=minimum_samples).evaluate(
        payload,
        production_scope=False,
    )
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="shadow-evidence",
        payload=payload,
        policy_result=result,
        producer=producer,
        tool_version=tool_version,
        revision=revision,
        scope=scope,
        input_manifest=input_manifest,
    )
    verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda value: verifier.verify(
            value,
            expected_revision=revision,
            expected_scope=scope,
        )
    ).write(output, bundle)
    return bundle


def verify_policy_bound_evidence(
    *,
    path: Path,
    revision: str,
    scope: str,
    payload_type: type[BaseModel],
    evaluate_policy,
    verifier: EvidenceVerifier,
):
    """Verify Receipt/bindings and recompute the payload's domain policy."""

    verified = verifier.verify(
        json.loads(path.read_text(encoding="utf-8")),
        expected_revision=revision,
        expected_scope=scope,
    )
    if not isinstance(verified.payload, payload_type):
        raise ValueError("evidence payload type is invalid")
    policy_result = evaluate_policy(verified.payload)
    artifact = verified.bundle.artifact
    if (
        policy_result.verification_status is not VerificationStatus.PASS
        or artifact.verification_status != policy_result.verification_status
        or artifact.promotion_decision != policy_result.promotion_decision
        or tuple(artifact.gate_codes) != policy_result.gate_codes
    ):
        raise ValueError("evidence policy state is invalid")
    return verified


def print_evidence_result(bundle: EvidenceBundle, output: Path) -> None:
    for line in render_gate_lines(bundle):
        print(line)
    print(f"artifact={output.as_posix()}")
