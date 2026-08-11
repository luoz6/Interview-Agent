from __future__ import annotations

import hmac
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from contracts.evidence.canonical import canonical_json_bytes
from contracts.evidence.digest import canonical_sha256
from contracts.evidence.envelope import (
    NonEmptyText,
    Revision,
    SchemaName,
    Sha256Hex,
    StrictContractModel,
    UtcTimestamp,
)


SignatureHex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class EvidenceReceipt(StrictContractModel):
    schema_version: Literal["evidence-receipt-v1"]
    receipt_id: Sha256Hex
    evidence_sha256: Sha256Hex
    producer: NonEmptyText
    revision: Revision
    scope: SchemaName
    issued_at: UtcTimestamp
    signature_algorithm: Literal["hmac-sha256"] | None = None
    key_id: NonEmptyText | None = None
    signature: SignatureHex | None = None
    trusted_storage_reference: NonEmptyText | None = None

    @model_validator(mode="after")
    def require_protection(self) -> "EvidenceReceipt":
        signature_fields = (
            self.signature_algorithm,
            self.key_id,
            self.signature,
        )
        has_signature = all(value is not None for value in signature_fields)
        has_partial_signature = any(value is not None for value in signature_fields)
        if has_partial_signature and not has_signature:
            raise ValueError("receipt signature fields must be supplied together")
        if not has_signature and self.trusted_storage_reference is None:
            raise ValueError("receipt requires a signature or trusted storage reference")
        return self

    def unsigned_payload(self) -> dict:
        return self.model_dump(
            mode="json",
            exclude={"signature"},
        )


class HmacReceiptSigner:
    def __init__(self, *, key_id: str, secret: bytes):
        if not key_id.strip():
            raise ValueError("receipt key_id cannot be empty")
        if len(secret) < 32:
            raise ValueError("receipt HMAC secret must contain at least 32 bytes")
        self._key_id = key_id
        self._secret = bytes(secret)

    def issue(
        self,
        *,
        artifact,
        issued_at: str,
    ) -> EvidenceReceipt:
        artifact_value = artifact.model_dump(mode="json")
        receipt = EvidenceReceipt(
            schema_version="evidence-receipt-v1",
            receipt_id=artifact.envelope.receipt_id,
            evidence_sha256=canonical_sha256(artifact_value),
            producer=artifact.envelope.producer,
            revision=artifact.envelope.revision,
            scope=artifact.envelope.scope,
            issued_at=issued_at,
            signature_algorithm="hmac-sha256",
            key_id=self._key_id,
            signature="0" * 64,
        )
        signature = hmac.new(
            self._secret,
            canonical_json_bytes(receipt.unsigned_payload()),
            sha256,
        ).hexdigest()
        return receipt.model_copy(update={"signature": signature})

    def verify(self, receipt: EvidenceReceipt) -> bool:
        if (
            receipt.signature_algorithm != "hmac-sha256"
            or receipt.key_id != self._key_id
            or receipt.signature is None
        ):
            return False
        expected = hmac.new(
            self._secret,
            canonical_json_bytes(receipt.unsigned_payload()),
            sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, receipt.signature)
