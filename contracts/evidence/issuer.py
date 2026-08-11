from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from contracts.evidence.digest import canonical_sha256
from contracts.evidence.digest import sha256_bytes
from contracts.evidence.envelope import (
    EvidenceArtifact,
    EvidenceBundle,
    EvidenceEnvelope,
    InputArtifact,
    PrivacyMetadata,
)
from contracts.evidence.privacy import assert_privacy_safe
from contracts.evidence.receipt import HmacReceiptSigner
from contracts.evidence.status import PromotionDecision, VerificationStatus
from contracts.evidence.verifier import EvidenceRegistry, EvidenceVerifier


class PolicyResult(Protocol):
    verification_status: VerificationStatus
    promotion_decision: PromotionDecision
    gate_codes: tuple[str, ...]


def utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.strftime("%Y-%m-%dT%H:%M:%SZ")


class EvidenceIssuer:
    """Create, protect and self-verify a complete evidence bundle."""

    def __init__(
        self,
        *,
        signer: HmacReceiptSigner,
        registry: EvidenceRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._signer = signer
        self._registry = registry or EvidenceRegistry.default()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def issue(
        self,
        *,
        payload_type: str,
        payload: BaseModel,
        policy_result: PolicyResult,
        producer: str,
        tool_version: str,
        revision: str,
        scope: str,
        input_manifest: Sequence[InputArtifact] = (),
        privacy: PrivacyMetadata | None = None,
        expires_at: datetime | None = None,
    ) -> EvidenceBundle:
        issued_at = self._clock()
        issued_at_text = utc_timestamp(issued_at)
        expires_at_text = utc_timestamp(expires_at) if expires_at is not None else None
        manifest = list(input_manifest)
        manifest_value = [item.model_dump(mode="json") for item in manifest]
        payload_value = payload.model_dump(mode="json")
        assert_privacy_safe(payload_value)
        input_digest = canonical_sha256(manifest_value)
        receipt_id = canonical_sha256(
            {
                "producer": producer,
                "tool_version": tool_version,
                "revision": revision,
                "scope": scope,
                "input_digest": input_digest,
                "generated_at": issued_at_text,
                "expires_at": expires_at_text,
                "payload_type": payload_type,
                "payload": payload_value,
                "verification_status": policy_result.verification_status,
                "promotion_decision": policy_result.promotion_decision,
                "gate_codes": sorted(policy_result.gate_codes),
            }
        )
        envelope = EvidenceEnvelope(
            schema_version="evidence-envelope-v1",
            producer=producer,
            tool_version=tool_version,
            revision=revision,
            scope=scope,
            input_manifest=manifest,
            input_digest=input_digest,
            generated_at=issued_at_text,
            expires_at=expires_at_text,
            privacy=privacy
            or PrivacyMetadata(
                classification="internal",
                contains_personal_data=False,
                redaction_applied=False,
                forbidden_fields_checked=True,
            ),
            receipt_id=receipt_id,
        )
        artifact = EvidenceArtifact(
            envelope=envelope,
            payload_type=payload_type,
            payload=payload_value,
            verification_status=policy_result.verification_status,
            promotion_decision=policy_result.promotion_decision,
            gate_codes=sorted(policy_result.gate_codes),
        )
        receipt = self._signer.issue(artifact=artifact, issued_at=issued_at_text)
        bundle = EvidenceBundle(artifact=artifact, receipt=receipt)
        EvidenceVerifier(
            registry=self._registry,
            receipt_signer=self._signer,
        ).verify(
            bundle.model_dump(mode="json"),
            expected_revision=revision,
            expected_scope=scope,
            now=issued_at,
        )
        return bundle


def input_artifact_from_bundle(
    *,
    path: Path,
    logical_path: str,
    bundle: EvidenceBundle,
) -> InputArtifact:
    persisted = path.read_bytes()
    return InputArtifact(
        path=logical_path,
        sha256=sha256_bytes(persisted),
        receipt_sha256=canonical_sha256(bundle.receipt),
        size_bytes=len(persisted),
        media_type="application/json",
    )
