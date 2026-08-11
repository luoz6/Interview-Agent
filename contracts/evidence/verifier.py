from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, TypeVar

from pydantic import BaseModel

from contracts.evidence.digest import canonical_sha256
from contracts.evidence.envelope import EvidenceBundle
from contracts.evidence.payloads import (
    CapacityEvidencePayload,
    CleanupEvidencePayload,
    OperationalRcEvidencePayload,
    OperationalRegressionEvidencePayload,
    OperationalSecurityEvidencePayload,
    OperationalShadowEvidencePayload,
    OperationalStagingEvidencePayload,
    OperationalStatusEvidencePayload,
    ProposalReviewEvidencePayload,
    ProposalReviewCaseSetPayload,
    ProductionBudgetReadinessEvidencePayload,
    ProductionBudgetObservationEvidencePayload,
    ProductionBudgetAcceptanceEvidencePayload,
    ProductionShadowEvidenceManifestPayload,
    ProductionBudgetWindowDecisionEvidencePayload,
    ProductionShadowChangePreflightEvidencePayload,
    ProductionShadowApprovalRequestPayload,
    PublicationEvidencePayload,
    ReleaseEvidencePayload,
    RestoreDrillEvidencePayload,
    ShadowEvidencePayload,
    Stage38AcceptanceEvidencePayload,
    Stage43bRecoveryEvidencePayload,
    Stage49ContextBudgetCanaryEvidencePayload,
)
from contracts.evidence.privacy import assert_privacy_safe
from contracts.evidence.receipt import HmacReceiptSigner


PayloadModel = TypeVar("PayloadModel", bound=BaseModel)


class EvidenceVerificationError(ValueError):
    pass


class EvidenceRegistry:
    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}

    def register(self, payload_type: str, model: type[PayloadModel]) -> None:
        if payload_type in self._models:
            raise ValueError(f"payload type already registered: {payload_type}")
        self._models[payload_type] = model

    def parse(self, payload_type: str, payload: Mapping[str, Any]) -> BaseModel:
        model = self._models.get(payload_type)
        if model is None:
            raise EvidenceVerificationError(f"unknown payload type: {payload_type}")
        return model.model_validate(payload)

    @classmethod
    def default(cls) -> "EvidenceRegistry":
        registry = cls()
        registry.register("capacity-evidence", CapacityEvidencePayload)
        registry.register("proposal-review-evidence", ProposalReviewEvidencePayload)
        registry.register("proposal-review-case-set", ProposalReviewCaseSetPayload)
        registry.register("shadow-evidence", ShadowEvidencePayload)
        registry.register(
            "stage38-acceptance-evidence",
            Stage38AcceptanceEvidencePayload,
        )
        registry.register(
            "stage43b-recovery-evidence",
            Stage43bRecoveryEvidencePayload,
        )
        registry.register(
            "stage49-context-budget-canary-evidence",
            Stage49ContextBudgetCanaryEvidencePayload,
        )
        registry.register("release-evidence", ReleaseEvidencePayload)
        registry.register(
            "publication-evidence",
            PublicationEvidencePayload,
        )
        registry.register(
            "restore-drill-evidence",
            RestoreDrillEvidencePayload,
        )
        registry.register("cleanup-evidence", CleanupEvidencePayload)
        registry.register(
            "operational-shadow-evidence",
            OperationalShadowEvidencePayload,
        )
        registry.register(
            "operational-rc-evidence",
            OperationalRcEvidencePayload,
        )
        registry.register(
            "operational-regression-evidence",
            OperationalRegressionEvidencePayload,
        )
        registry.register(
            "operational-staging-evidence",
            OperationalStagingEvidencePayload,
        )
        registry.register(
            "operational-status-evidence",
            OperationalStatusEvidencePayload,
        )
        registry.register(
            "operational-security-evidence",
            OperationalSecurityEvidencePayload,
        )
        registry.register(
            "production-shadow-approval-request",
            ProductionShadowApprovalRequestPayload,
        )
        registry.register(
            "production-budget-readiness-evidence",
            ProductionBudgetReadinessEvidencePayload,
        )
        registry.register(
            "production-shadow-change-preflight-evidence",
            ProductionShadowChangePreflightEvidencePayload,
        )
        registry.register(
            "production-budget-observation-evidence",
            ProductionBudgetObservationEvidencePayload,
        )
        registry.register(
            "production-budget-window-decision-evidence",
            ProductionBudgetWindowDecisionEvidencePayload,
        )
        registry.register(
            "production-budget-acceptance-evidence",
            ProductionBudgetAcceptanceEvidencePayload,
        )
        registry.register(
            "production-shadow-evidence-manifest",
            ProductionShadowEvidenceManifestPayload,
        )
        return registry


@dataclass(frozen=True)
class VerifiedEvidence:
    bundle: EvidenceBundle
    payload: BaseModel


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


class EvidenceVerifier:
    def __init__(
        self,
        *,
        registry: EvidenceRegistry,
        receipt_signer: HmacReceiptSigner | None = None,
    ) -> None:
        self._registry = registry
        self._receipt_signer = receipt_signer

    def verify(
        self,
        value: Mapping[str, Any],
        *,
        expected_revision: str,
        expected_scope: str,
        now: datetime | None = None,
    ) -> VerifiedEvidence:
        try:
            bundle = EvidenceBundle.model_validate(value)
            payload = self._registry.parse(
                bundle.artifact.payload_type,
                bundle.artifact.payload,
            )
        except EvidenceVerificationError:
            raise
        except Exception as exc:
            raise EvidenceVerificationError("evidence schema validation failed") from exc

        envelope = bundle.artifact.envelope
        receipt = bundle.receipt
        if envelope.revision != expected_revision:
            raise EvidenceVerificationError("evidence revision mismatch")
        if envelope.scope != expected_scope:
            raise EvidenceVerificationError("evidence scope mismatch")
        manifest_value = [item.model_dump(mode="json") for item in envelope.input_manifest]
        if canonical_sha256(manifest_value) != envelope.input_digest:
            raise EvidenceVerificationError("evidence input manifest digest mismatch")
        if receipt.receipt_id != envelope.receipt_id:
            raise EvidenceVerificationError("receipt identity mismatch")
        artifact_value = bundle.artifact.model_dump(mode="json")
        if canonical_sha256(artifact_value) != receipt.evidence_sha256:
            raise EvidenceVerificationError("receipt evidence digest mismatch")
        if (
            receipt.producer != envelope.producer
            or receipt.revision != envelope.revision
            or receipt.scope != envelope.scope
        ):
            raise EvidenceVerificationError("receipt binding mismatch")
        if receipt.signature is not None:
            if self._receipt_signer is None or not self._receipt_signer.verify(receipt):
                raise EvidenceVerificationError("receipt signature verification failed")
        elif receipt.trusted_storage_reference is None:
            raise EvidenceVerificationError("receipt protection is missing")

        current = now or datetime.now(timezone.utc)
        generated_at = _parse_utc(envelope.generated_at)
        if generated_at > current:
            raise EvidenceVerificationError("evidence generation time is in the future")
        if envelope.expires_at is not None and _parse_utc(envelope.expires_at) <= current:
            raise EvidenceVerificationError("evidence has expired")
        if envelope.privacy.forbidden_fields_checked is not True:
            raise EvidenceVerificationError("privacy field scan was not attested")
        assert_privacy_safe(bundle.artifact.payload)
        return VerifiedEvidence(bundle=bundle, payload=payload)
