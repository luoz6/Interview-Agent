from __future__ import annotations

import re
from typing import Any, Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from contracts.evidence.status import PromotionDecision, VerificationStatus


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,64}$")]
SchemaName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9._-]{2,127}$"),
]
GateCodeName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,127}$"),
]
RelativePath = Annotated[str, StringConstraints(min_length=1, max_length=512)]
UtcTimestamp = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
]
VerificationStatusValue = Annotated[VerificationStatus, Field(strict=False)]
PromotionDecisionValue = Annotated[PromotionDecision, Field(strict=False)]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class InputArtifact(StrictContractModel):
    path: RelativePath
    sha256: Sha256Hex
    receipt_sha256: Sha256Hex
    size_bytes: Annotated[StrictInt, Field(ge=0)]
    media_type: NonEmptyText

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:/", normalized)
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError("input artifact path must be a safe relative path")
        return normalized


class PrivacyMetadata(StrictContractModel):
    classification: Annotated[
        str,
        StringConstraints(pattern=r"^(public|internal|restricted)$"),
    ]
    contains_personal_data: StrictBool
    redaction_applied: StrictBool
    forbidden_fields_checked: StrictBool


class EvidenceEnvelope(StrictContractModel):
    schema_version: SchemaName
    producer: NonEmptyText
    tool_version: NonEmptyText
    revision: Revision
    scope: SchemaName
    input_manifest: list[InputArtifact]
    input_digest: Sha256Hex
    generated_at: UtcTimestamp
    expires_at: UtcTimestamp | None = None
    privacy: PrivacyMetadata
    receipt_id: Sha256Hex


class EvidenceArtifact(StrictContractModel):
    envelope: EvidenceEnvelope
    payload_type: SchemaName
    payload: dict[str, Any]
    verification_status: VerificationStatusValue
    promotion_decision: PromotionDecisionValue | None = None
    gate_codes: list[GateCodeName]

    @model_validator(mode="after")
    def prevent_decision_bypass(self) -> "EvidenceArtifact":
        if self.verification_status is not VerificationStatus.PASS and (
            self.promotion_decision not in {None, PromotionDecision.HOLD}
        ):
            raise ValueError(
                "a blocked or not-run verification can only have HOLD or no decision"
            )
        if self.verification_status is VerificationStatus.PASS and self.gate_codes:
            raise ValueError("PASS evidence cannot contain blocking gate codes")
        if self.verification_status is not VerificationStatus.PASS and not self.gate_codes:
            raise ValueError("blocked or not-run evidence requires at least one gate code")
        return self


class EvidenceBundle(StrictContractModel):
    artifact: EvidenceArtifact
    receipt: "EvidenceReceipt"


from contracts.evidence.receipt import EvidenceReceipt

EvidenceBundle.model_rebuild()
