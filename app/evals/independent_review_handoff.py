from __future__ import annotations

import hashlib
import base64
import binascii
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


HANDOFF_SCHEMA_VERSION = "independent-review-handoff-v1"
ATTESTATION_SCHEMA_VERSION = "independent-review-attestation-v1"
RECEIPT_SCHEMA_VERSION = "independent-review-sheet-receipt-v1"
LEDGER_SCHEMA_VERSION = "independent-review-ledger-v1"
DOMAIN_RESULT_SCHEMA_VERSION = "independent-review-domain-result-v1"
IDENTITY_RECEIPT_SCHEMA_VERSION = "external-human-identity-receipt-v1"
DETACHED_SIGNATURE_SCHEMA_VERSION = "detached-signature-evidence-v1"
FREEZE_AUTHORIZATION_SCHEMA_VERSION = "coordinator-freeze-authorization-v1"
HASH_ALGORITHM = "sha256-canonical-json-v1"

ReviewKind = Literal[
    "gate2_calibration",
    "gate3_dataset",
    "gate3_fixed_adaptive",
    "t49_semantic",
]
HandoffFileRole = Literal[
    "protocol",
    "packet",
    "empty_sheet",
    "public_validation",
]
LedgerEventType = Literal[
    "HANDOFF_EXPORTED",
    "SHEET_FROZEN",
    "UNSEAL_AUTHORIZED",
    "DOMAIN_RESULT_RECORDED",
]
DomainOutcome = Literal["UNKNOWN", "PASS", "FAIL", "BLOCKED"]

REQUIRED_EXPORT_ROLES = frozenset({"protocol", "packet", "empty_sheet"})
ALLOWED_EXPORT_ROLES = REQUIRED_EXPORT_ROLES | {"public_validation"}
STAGING_PATHS: dict[str, str] = {
    "protocol": "protocol.md",
    "packet": "reviewer/packet.json",
    "empty_sheet": "reviewer/empty-review-sheet.json",
    "public_validation": "public/dataset-validation.json",
}
DENIED_PATH_PARTS = frozenset(
    {
        ".git",
        "coordinator-only",
        "coordinator_only",
        "private",
        "secrets",
    }
)
DENIED_FILENAME_FRAGMENTS = (
    "assignment-key",
    "assignment_key",
    "randomization-seed",
    "randomization_seed",
    "unblind-map",
    "unblind_map",
    "unseal-token",
    "unseal_token",
    "api-key",
    "api_key",
    "credential",
    "secret",
)
DENIED_JSON_KEYS = frozenset(
    {
        "randomization_seed",
        "assignments",
        "assignment_key",
        "version_map",
        "policy_mapping",
        "model_mapping",
        "unblind_map",
        "unseal_token",
        "api_key",
        "access_token",
        "private_key",
        "credentials",
        "credential",
        "secret",
    }
)
NON_HUMAN_REVIEWER_MARKERS = (
    "agent",
    "assistant",
    "bot",
    "codex",
    "gpt",
    "llm",
    "model",
)
MACHINE_PATH_PATTERNS = (
    re.compile(r"(?:^|[\s'\"])[A-Za-z]:[\\/]"),
    re.compile(r"(?:^|[\s'\"])\\\\"),
    re.compile(r"(?:^|[\s'\"])/(?:Users|home|root|var|tmp)/"),
)
CONNECTION_URI_PATTERN = re.compile(
    r"\b(?:https?|ftp|file|ssh|postgres(?:ql)?|mysql|redis|mongodb)://",
    re.IGNORECASE,
)
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.IGNORECASE,
)
API_KEY_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b(?:api[_ -]?key|access[_ -]?token)\s*[:=]", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
)
PROTOCOL_PRIVATE_MARKERS = (
    "coordinator-only",
    "coordinator_only",
    "assignment-key",
    "assignment key",
    "unblind",
    "unblinding",
    "unblind-map",
    "解盲",
)
JSON_VALUE_ADAPTER = TypeAdapter(Any)
# Gate-trusted authority keys must be frozen by a separately reviewed code/config
# revision. Runtime callers cannot add trust anchors. An empty set is deliberately
# fail-closed: locally generated signatures can exercise mechanics but never PASS.
TRUSTED_GATE_AUTHORITY_PUBLIC_KEY_SHA256: frozenset[str] = frozenset()


def canonical_json_bytes(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    else:
        value = JSON_VALUE_ADAPTER.dump_python(value, mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _self_hash(value: BaseModel, field_name: str) -> str:
    return canonical_sha256(
        value.model_dump(mode="json", exclude={field_name})
    )


def safe_repo_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or ":" in value:
        raise ValueError("UNSAFE_REPO_RELATIVE_PATH")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        raise ValueError("UNSAFE_REPO_RELATIVE_PATH")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("UNSAFE_REPO_RELATIVE_PATH")
    lowered_parts = tuple(part.casefold() for part in path.parts)
    if any(part in DENIED_PATH_PARTS for part in lowered_parts):
        raise ValueError("COORDINATOR_ONLY_PATH_PROHIBITED")
    filename = lowered_parts[-1]
    if any(fragment in filename for fragment in DENIED_FILENAME_FRAGMENTS):
        raise ValueError("SENSITIVE_FILENAME_PROHIBITED")
    return path


def _is_machine_path(value: str) -> bool:
    return any(pattern.search(value) for pattern in MACHINE_PATH_PATTERNS)


def _validate_public_json(value: Any, *, location: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if normalized_key in DENIED_JSON_KEYS:
                raise ValueError(f"PROHIBITED_PUBLIC_JSON_KEY:{location}.{key}")
            _validate_public_json(child, location=f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_public_json(child, location=f"{location}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if "coordinator-only" in lowered or "assignment-key" in lowered:
            raise ValueError(f"PROHIBITED_PUBLIC_PATH_VALUE:{location}")
        if _is_machine_path(value):
            raise ValueError(f"MACHINE_PATH_PROHIBITED:{location}")


def _load_public_json(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"INVALID_PUBLIC_JSON:{path.name}") from exc
    _validate_public_json(value)
    return value


def _load_public_protocol(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("PROTOCOL_NOT_UTF8") from exc
    lowered = content.casefold()
    if CONNECTION_URI_PATTERN.search(content):
        raise ValueError("CONNECTION_URI_PROHIBITED_IN_PROTOCOL")
    if PRIVATE_KEY_PATTERN.search(content):
        raise ValueError("PRIVATE_KEY_PROHIBITED_IN_PROTOCOL")
    if any(pattern.search(content) for pattern in API_KEY_PATTERNS):
        raise ValueError("API_KEY_PROHIBITED_IN_PROTOCOL")
    if _is_machine_path(content):
        raise ValueError("MACHINE_PATH_PROHIBITED_IN_PROTOCOL")
    if any(marker in lowered for marker in PROTOCOL_PRIVATE_MARKERS):
        raise ValueError("PRIVATE_COORDINATOR_CONTENT_PROHIBITED_IN_PROTOCOL")
    return content


def _has_reparse_point(path: Path) -> bool:
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _reject_reparse_components(root: Path, relative: PurePosixPath) -> None:
    current = root
    if _has_reparse_point(current):
        raise ValueError("REPARSE_POINT_SOURCE_PROHIBITED")
    for part in relative.parts:
        current = current / part
        if _has_reparse_point(current):
            raise ValueError("REPARSE_POINT_SOURCE_PROHIBITED")


def _reject_denied_resolved_parts(relative: Path) -> None:
    lowered_parts = tuple(part.casefold() for part in relative.parts)
    if any(part in DENIED_PATH_PARTS for part in lowered_parts):
        raise ValueError("RESOLVED_COORDINATOR_ONLY_PATH_PROHIBITED")
    if any(
        fragment in part
        for part in lowered_parts
        for fragment in DENIED_FILENAME_FRAGMENTS
    ):
        raise ValueError("RESOLVED_SENSITIVE_PATH_PROHIBITED")


def _resolve_repo_file(workspace_root: Path, relative_path: str) -> Path:
    safe = safe_repo_relative_path(relative_path)
    root = workspace_root.resolve()
    _reject_reparse_components(root, safe)
    candidate = root.joinpath(*safe.parts)
    resolved = candidate.resolve(strict=True)
    try:
        resolved_relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("SOURCE_OUTSIDE_WORKSPACE") from exc
    _reject_denied_resolved_parts(resolved_relative)
    _reject_reparse_components(root, PurePosixPath(resolved_relative.as_posix()))
    if not resolved.is_file():
        raise ValueError("HANDOFF_SOURCE_NOT_FILE")
    return resolved


class HandoffFileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: HandoffFileRole
    source_repo_relative_path: str
    staging_relative_path: str
    bytes: int = Field(ge=0)
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("source_repo_relative_path", "staging_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        safe_repo_relative_path(value)
        return value


class HandoffManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["independent-review-handoff-v1"]
    review_kind: ReviewKind
    handoff_id: str = Field(min_length=1)
    created_at: datetime
    hash_algorithm: Literal["sha256-canonical-json-v1"]
    packet_canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: list[HandoffFileRecord] = Field(min_length=3, max_length=4)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_manifest(self):
        roles = [item.role for item in self.files]
        if len(roles) != len(set(roles)):
            raise ValueError("DUPLICATE_HANDOFF_FILE_ROLE")
        if not REQUIRED_EXPORT_ROLES <= set(roles):
            raise ValueError("HANDOFF_REQUIRED_FILE_MISSING")
        packet = next(item for item in self.files if item.role == "packet")
        protocol = next(item for item in self.files if item.role == "protocol")
        if packet.canonical_sha256 != self.packet_canonical_sha256:
            raise ValueError("PACKET_HASH_BINDING_MISMATCH")
        if protocol.raw_sha256 != self.protocol_raw_sha256:
            raise ValueError("PROTOCOL_HASH_BINDING_MISMATCH")
        if _self_hash(self, "manifest_sha256") != self.manifest_sha256:
            raise ValueError("HANDOFF_MANIFEST_HASH_MISMATCH")
        return self


class ReviewerIndependenceAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["independent-review-attestation-v1"]
    attestation_id: str = Field(min_length=1)
    review_kind: ReviewKind
    reviewer_id: str = Field(min_length=3)
    reviewer_kind: Literal["human"]
    reviewer_role: Literal["independent_technical_reviewer"]
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    no_implementation_involvement: Literal[True]
    no_dataset_annotation_involvement: Literal[True]
    no_coordinator_key_access: Literal[True]
    assignment_was_hidden: Literal[True]
    no_conflict_of_interest: Literal[True]
    declared_conflicts: list[str] = Field(default_factory=list)
    human_identity_verified_by_coordinator: Literal[True]
    attested_at: datetime
    attestation_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_attestation(self):
        reviewer_id = self.reviewer_id.casefold()
        if any(marker in reviewer_id for marker in NON_HUMAN_REVIEWER_MARKERS):
            raise ValueError("NON_HUMAN_REVIEWER_PROHIBITED")
        if self.declared_conflicts:
            raise ValueError("REVIEWER_CONFLICT_DECLARED")
        if _self_hash(self, "attestation_sha256") != self.attestation_sha256:
            raise ValueError("ATTESTATION_HASH_MISMATCH")
        return self


class DetachedSignatureEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["detached-signature-evidence-v1"]
    signature_id: str = Field(min_length=1)
    signer_authority_id: str = Field(min_length=3)
    signed_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    algorithm: Literal["ed25519-sha256-binding-v1"]
    public_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_base64: str = Field(min_length=80)
    signed_at: datetime
    synthetic_fixture: bool
    signature_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_signature_record(self):
        try:
            signature = base64.b64decode(self.signature_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("INVALID_DETACHED_SIGNATURE_ENCODING") from exc
        if len(signature) != 64:
            raise ValueError("INVALID_ED25519_SIGNATURE_LENGTH")
        if _self_hash(self, "signature_record_sha256") != self.signature_record_sha256:
            raise ValueError("DETACHED_SIGNATURE_RECORD_HASH_MISMATCH")
        return self


class ExternalIdentityReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["external-human-identity-receipt-v1"]
    identity_receipt_id: str = Field(min_length=1)
    review_kind: ReviewKind
    reviewer_id: str = Field(min_length=3)
    identity_authority_id: str = Field(min_length=3)
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_subject_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_identity_verified: Literal[True]
    evidence_origin: Literal["external_identity_authority", "synthetic_fixture"]
    synthetic_fixture: bool
    issued_at: datetime
    expires_at: datetime
    receipt_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity_receipt(self):
        if self.identity_authority_id == self.reviewer_id:
            raise ValueError("IDENTITY_AUTHORITY_MUST_BE_EXTERNAL")
        if self.expires_at <= self.issued_at:
            raise ValueError("IDENTITY_RECEIPT_EXPIRY_INVALID")
        expected_origin = (
            "synthetic_fixture"
            if self.synthetic_fixture
            else "external_identity_authority"
        )
        if self.evidence_origin != expected_origin:
            raise ValueError("IDENTITY_EVIDENCE_ORIGIN_MISMATCH")
        if _self_hash(self, "receipt_payload_sha256") != self.receipt_payload_sha256:
            raise ValueError("IDENTITY_RECEIPT_HASH_MISMATCH")
        return self


class CoordinatorFreezeAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["coordinator-freeze-authorization-v1"]
    authorization_id: str = Field(min_length=1)
    handoff_id: str = Field(min_length=1)
    review_kind: ReviewKind
    reviewer_id: str = Field(min_length=3)
    reviewer_authority_id: str = Field(min_length=3)
    coordinator_authority_id: str = Field(min_length=3)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sheet_canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_signature_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    received_at: datetime
    frozen_at: datetime
    synthetic_fixture: bool
    authorization_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_freeze_authorization(self):
        if self.reviewer_authority_id == self.coordinator_authority_id:
            raise ValueError("REVIEWER_COORDINATOR_AUTHORITY_COLLISION")
        if self.frozen_at < self.received_at:
            raise ValueError("AUTHORIZATION_FREEZE_TIME_INVALID")
        if _self_hash(self, "authorization_payload_sha256") != (
            self.authorization_payload_sha256
        ):
            raise ValueError("FREEZE_AUTHORIZATION_HASH_MISMATCH")
        return self


class FrozenSheetReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["independent-review-sheet-receipt-v1"]
    receipt_id: str = Field(min_length=1)
    handoff_id: str = Field(min_length=1)
    review_kind: ReviewKind
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sheet_raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sheet_canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attestation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_signature_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_signature_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    freeze_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinator_signature_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_id: str = Field(min_length=3)
    reviewer_authority_id: str = Field(min_length=3)
    coordinator_authority_id: str = Field(min_length=3)
    judgment_count: int = Field(ge=1)
    external_identity_receipt_present: Literal[True]
    detached_signatures_verified: Literal[True]
    dual_authority_verified: Literal[True]
    synthetic_fixture: bool
    gate_evidence_ready: bool
    received_at: datetime
    frozen_at: datetime
    coordinator_id: str = Field(min_length=1)
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_receipt(self):
        if self.frozen_at < self.received_at:
            raise ValueError("SHEET_FROZEN_BEFORE_RECEIPT")
        if self.reviewer_authority_id == self.coordinator_authority_id:
            raise ValueError("REVIEWER_COORDINATOR_AUTHORITY_COLLISION")
        if self.gate_evidence_ready == self.synthetic_fixture:
            raise ValueError("SYNTHETIC_GATE_EVIDENCE_STATE_INVALID")
        if _self_hash(self, "receipt_sha256") != self.receipt_sha256:
            raise ValueError("SHEET_RECEIPT_HASH_MISMATCH")
        return self


class DomainResultRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["independent-review-domain-result-v1"]
    result_id: str = Field(min_length=1)
    review_kind: ReviewKind
    outcome: DomainOutcome
    domain_result_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    blocker_code: str | None = None
    review_receipt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    evaluator_authority_id: str | None = None
    evaluator_signature_record_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    synthetic_fixture: bool
    gate_evidence_ready: bool
    recorded_at: datetime
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_domain_result(self):
        if self.outcome == "UNKNOWN":
            if self.domain_result_sha256 is not None:
                raise ValueError("UNKNOWN_DOMAIN_RESULT_HAS_HASH")
            if self.blocker_code != "BLOCKED_DOMAIN_RESULT_UNKNOWN":
                raise ValueError("UNKNOWN_DOMAIN_RESULT_MUST_BLOCK")
            if any(
                value is not None
                for value in (
                    self.review_receipt_sha256,
                    self.evaluator_authority_id,
                    self.evaluator_signature_record_sha256,
                )
            ):
                raise ValueError("UNKNOWN_DOMAIN_RESULT_HAS_GATE_EVIDENCE")
            if self.gate_evidence_ready:
                raise ValueError("UNKNOWN_DOMAIN_RESULT_CANNOT_BE_GATE_READY")
        else:
            if self.domain_result_sha256 is None:
                raise ValueError("KNOWN_DOMAIN_RESULT_HASH_REQUIRED")
            if self.outcome == "BLOCKED" and not self.blocker_code:
                raise ValueError("BLOCKED_DOMAIN_RESULT_CODE_REQUIRED")
            if self.outcome in {"PASS", "FAIL"} and self.blocker_code is not None:
                raise ValueError("TERMINAL_DOMAIN_RESULT_HAS_BLOCKER")
            if not all(
                (
                    self.review_receipt_sha256,
                    self.evaluator_authority_id,
                    self.evaluator_signature_record_sha256,
                )
            ):
                raise ValueError("KNOWN_DOMAIN_RESULT_EVIDENCE_REQUIRED")
            if self.synthetic_fixture or not self.gate_evidence_ready:
                raise ValueError("SYNTHETIC_FIXTURE_NOT_GATE_EVIDENCE")
        if _self_hash(self, "record_sha256") != self.record_sha256:
            raise ValueError("DOMAIN_RESULT_RECORD_HASH_MISMATCH")
        return self


class HandoffLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    event_type: LedgerEventType
    recorded_at: datetime
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    sheet_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    attestation_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    coordinator_key_contents_read: bool | None = None
    domain_result_record_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    outcome_status: str
    previous_entry_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_entry(self):
        if self.event_type == "UNSEAL_AUTHORIZED":
            if self.coordinator_key_contents_read is not False:
                raise ValueError("UNSEAL_AUTHORIZATION_MUST_NOT_READ_KEY")
        elif self.coordinator_key_contents_read is not None:
            raise ValueError("UNEXPECTED_KEY_READ_FIELD")
        if _self_hash(self, "entry_sha256") != self.entry_sha256:
            raise ValueError("HANDOFF_LEDGER_ENTRY_HASH_MISMATCH")
        return self


class HandoffLedger(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["independent-review-ledger-v1"]
    handoff_id: str = Field(min_length=1)
    review_kind: ReviewKind
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: list[HandoffLedgerEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_chain_and_transitions(self):
        event_ids = [entry.event_id for entry in self.entries]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("DUPLICATE_HANDOFF_EVENT_ID")
        previous_hash = None
        previous_time = None
        previous_event = None
        allowed_transitions = {
            None: {"HANDOFF_EXPORTED"},
            "HANDOFF_EXPORTED": {"SHEET_FROZEN"},
            "SHEET_FROZEN": {"UNSEAL_AUTHORIZED"},
            "UNSEAL_AUTHORIZED": {"DOMAIN_RESULT_RECORDED"},
            "DOMAIN_RESULT_RECORDED": {"DOMAIN_RESULT_RECORDED"},
        }
        for entry in self.entries:
            if entry.manifest_sha256 != self.manifest_sha256:
                raise ValueError("LEDGER_MANIFEST_HASH_MISMATCH")
            if entry.packet_sha256 != self.packet_sha256:
                raise ValueError("LEDGER_PACKET_HASH_MISMATCH")
            if entry.previous_entry_sha256 != previous_hash:
                raise ValueError("HANDOFF_LEDGER_CHAIN_BROKEN")
            if entry.event_type not in allowed_transitions[previous_event]:
                raise ValueError("INVALID_HANDOFF_STATE_TRANSITION")
            if previous_time is not None and entry.recorded_at < previous_time:
                raise ValueError("NON_MONOTONIC_HANDOFF_EVENT_TIME")
            previous_hash = entry.entry_sha256
            previous_time = entry.recorded_at
            previous_event = entry.event_type
        return self


def _load_ed25519_public_key(public_key_pem: bytes) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(public_key_pem)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_DETACHED_SIGNATURE_PUBLIC_KEY") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("DETACHED_SIGNATURE_KEY_TYPE_PROHIBITED")
    return key


def _ed25519_public_key_sha256(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def verify_detached_signature(
    signature: DetachedSignatureEvidence,
    *,
    public_key_pem: bytes,
    expected_artifact_sha256: str,
    expected_authority_id: str,
    require_gate_trust: bool = False,
) -> None:
    signature = DetachedSignatureEvidence.model_validate(
        signature.model_dump(mode="json")
    )
    if signature.signed_artifact_sha256 != expected_artifact_sha256:
        raise ValueError("DETACHED_SIGNATURE_ARTIFACT_MISMATCH")
    if signature.signer_authority_id != expected_authority_id:
        raise ValueError("DETACHED_SIGNATURE_AUTHORITY_MISMATCH")
    key = _load_ed25519_public_key(public_key_pem)
    public_key_sha256 = _ed25519_public_key_sha256(key)
    if public_key_sha256 != signature.public_key_sha256:
        raise ValueError("DETACHED_SIGNATURE_PUBLIC_KEY_MISMATCH")
    if (
        require_gate_trust
        and public_key_sha256 not in TRUSTED_GATE_AUTHORITY_PUBLIC_KEY_SHA256
    ):
        raise ValueError("EXTERNAL_GATE_AUTHORITY_NOT_TRUSTED")
    try:
        key.verify(
            base64.b64decode(signature.signature_base64, validate=True),
            bytes.fromhex(expected_artifact_sha256),
        )
    except InvalidSignature as exc:
        raise ValueError("DETACHED_SIGNATURE_INVALID") from exc


def export_reviewer_handoff(
    *,
    workspace_root: Path,
    staging_dir: Path,
    review_kind: ReviewKind,
    handoff_id: str,
    created_at: datetime,
    sources: dict[str, str],
) -> HandoffManifest:
    roles = set(sources)
    if not REQUIRED_EXPORT_ROLES <= roles or not roles <= ALLOWED_EXPORT_ROLES:
        raise ValueError("INVALID_HANDOFF_EXPORT_ALLOWLIST")
    root = workspace_root.resolve()
    staging = staging_dir.resolve()
    if staging == root:
        raise ValueError("STAGING_DIRECTORY_CANNOT_BE_WORKSPACE")
    if staging.exists() and (not staging.is_dir() or any(staging.iterdir())):
        raise ValueError("STAGING_DIRECTORY_NOT_EMPTY")

    source_files: dict[str, Path] = {}
    json_values: dict[str, Any] = {}
    records: list[HandoffFileRecord] = []
    for role in sorted(roles):
        relative = sources[role]
        source = _resolve_repo_file(root, relative)
        source_files[role] = source
        canonical_hash = None
        if role == "protocol":
            _load_public_protocol(source)
        else:
            value = _load_public_json(source)
            json_values[role] = value
            canonical_hash = canonical_sha256(value)
        records.append(
            HandoffFileRecord(
                role=role,
                source_repo_relative_path=relative,
                staging_relative_path=STAGING_PATHS[role],
                bytes=source.stat().st_size,
                raw_sha256=file_sha256(source),
                canonical_sha256=canonical_hash,
            )
        )

    packet_record = next(item for item in records if item.role == "packet")
    protocol_record = next(item for item in records if item.role == "protocol")
    empty_sheet = json_values["empty_sheet"]
    if not isinstance(empty_sheet, dict):
        raise ValueError("EMPTY_REVIEW_SHEET_MUST_BE_OBJECT")
    if empty_sheet.get("judgments") != []:
        raise ValueError("HANDOFF_SHEET_MUST_BE_EMPTY")
    if empty_sheet.get("packet_sha256") != packet_record.canonical_sha256:
        raise ValueError("EMPTY_SHEET_PACKET_HASH_MISMATCH")

    manifest_payload = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "review_kind": review_kind,
        "handoff_id": handoff_id,
        "created_at": created_at,
        "hash_algorithm": HASH_ALGORITHM,
        "packet_canonical_sha256": packet_record.canonical_sha256,
        "protocol_raw_sha256": protocol_record.raw_sha256,
        "files": records,
    }
    manifest = HandoffManifest(
        **manifest_payload,
        manifest_sha256=canonical_sha256(manifest_payload),
    )

    staging.mkdir(parents=True, exist_ok=True)
    for record in records:
        destination = staging.joinpath(*PurePosixPath(record.staging_relative_path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(source_files[record.role].read_bytes())
    manifest_path = staging / "handoff-manifest.json"
    with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(
            manifest.model_dump(mode="json"),
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        handle.write("\n")
    return manifest


def freeze_review_sheet(
    *,
    manifest: HandoffManifest,
    sheet_path: Path,
    attestation: ReviewerIndependenceAttestation,
    identity_receipt: ExternalIdentityReceipt,
    identity_signature: DetachedSignatureEvidence,
    identity_public_key_pem: bytes,
    reviewer_signature: DetachedSignatureEvidence,
    reviewer_public_key_pem: bytes,
    freeze_authorization: CoordinatorFreezeAuthorization,
    coordinator_signature: DetachedSignatureEvidence,
    coordinator_public_key_pem: bytes,
    receipt_id: str,
    coordinator_id: str,
    received_at: datetime,
    frozen_at: datetime,
) -> FrozenSheetReceipt:
    if not all(
        (
            isinstance(identity_receipt, ExternalIdentityReceipt),
            isinstance(identity_signature, DetachedSignatureEvidence),
            bool(identity_public_key_pem),
            isinstance(reviewer_signature, DetachedSignatureEvidence),
            bool(reviewer_public_key_pem),
            isinstance(freeze_authorization, CoordinatorFreezeAuthorization),
            isinstance(coordinator_signature, DetachedSignatureEvidence),
            bool(coordinator_public_key_pem),
        )
    ):
        raise ValueError("EXTERNAL_REVIEW_EVIDENCE_REQUIRED")
    manifest = HandoffManifest.model_validate(manifest.model_dump(mode="json"))
    attestation = ReviewerIndependenceAttestation.model_validate(
        attestation.model_dump(mode="json")
    )
    identity_receipt = ExternalIdentityReceipt.model_validate(
        identity_receipt.model_dump(mode="json")
    )
    freeze_authorization = CoordinatorFreezeAuthorization.model_validate(
        freeze_authorization.model_dump(mode="json")
    )
    if attestation.review_kind != manifest.review_kind:
        raise ValueError("ATTESTATION_REVIEW_KIND_MISMATCH")
    if attestation.packet_sha256 != manifest.packet_canonical_sha256:
        raise ValueError("ATTESTATION_PACKET_HASH_MISMATCH")
    if attestation.protocol_sha256 != manifest.protocol_raw_sha256:
        raise ValueError("ATTESTATION_PROTOCOL_HASH_MISMATCH")
    if attestation.attested_at > received_at:
        raise ValueError("ATTESTATION_AFTER_SHEET_RECEIPT")
    if sheet_path.is_symlink() or not sheet_path.is_file():
        raise ValueError("REVIEW_SHEET_NOT_REGULAR_FILE")
    sheet = _load_public_json(sheet_path)
    if not isinstance(sheet, dict):
        raise ValueError("REVIEW_SHEET_MUST_BE_OBJECT")
    if sheet.get("packet_sha256") != manifest.packet_canonical_sha256:
        raise ValueError("REVIEW_SHEET_PACKET_HASH_MISMATCH")
    if not isinstance(sheet.get("judgments"), list):
        raise ValueError("REVIEW_SHEET_JUDGMENTS_REQUIRED")
    if not sheet["judgments"]:
        raise ValueError("EMPTY_FROZEN_REVIEW_SHEET_PROHIBITED")
    sheet_canonical_sha256 = canonical_sha256(sheet)
    if identity_receipt.review_kind != manifest.review_kind:
        raise ValueError("IDENTITY_RECEIPT_REVIEW_KIND_MISMATCH")
    if identity_receipt.reviewer_id != attestation.reviewer_id:
        raise ValueError("IDENTITY_RECEIPT_REVIEWER_MISMATCH")
    if identity_receipt.packet_sha256 != manifest.packet_canonical_sha256:
        raise ValueError("IDENTITY_RECEIPT_PACKET_MISMATCH")
    if identity_receipt.expires_at < frozen_at:
        raise ValueError("IDENTITY_RECEIPT_EXPIRED")
    synthetic_fixture = any(
        (
            identity_receipt.synthetic_fixture,
            identity_signature.synthetic_fixture,
            reviewer_signature.synthetic_fixture,
            freeze_authorization.synthetic_fixture,
            coordinator_signature.synthetic_fixture,
        )
    )
    verify_detached_signature(
        identity_signature,
        public_key_pem=identity_public_key_pem,
        expected_artifact_sha256=identity_receipt.receipt_payload_sha256,
        expected_authority_id=identity_receipt.identity_authority_id,
        require_gate_trust=not synthetic_fixture,
    )
    verify_detached_signature(
        reviewer_signature,
        public_key_pem=reviewer_public_key_pem,
        expected_artifact_sha256=sheet_canonical_sha256,
        expected_authority_id=freeze_authorization.reviewer_authority_id,
        require_gate_trust=not synthetic_fixture,
    )
    if freeze_authorization.handoff_id != manifest.handoff_id:
        raise ValueError("FREEZE_AUTHORIZATION_HANDOFF_MISMATCH")
    if freeze_authorization.review_kind != manifest.review_kind:
        raise ValueError("FREEZE_AUTHORIZATION_REVIEW_KIND_MISMATCH")
    if freeze_authorization.reviewer_id != attestation.reviewer_id:
        raise ValueError("FREEZE_AUTHORIZATION_REVIEWER_MISMATCH")
    if freeze_authorization.coordinator_authority_id != coordinator_id:
        raise ValueError("COORDINATOR_AUTHORITY_MISMATCH")
    if freeze_authorization.manifest_sha256 != manifest.manifest_sha256:
        raise ValueError("FREEZE_AUTHORIZATION_MANIFEST_MISMATCH")
    if freeze_authorization.packet_sha256 != manifest.packet_canonical_sha256:
        raise ValueError("FREEZE_AUTHORIZATION_PACKET_MISMATCH")
    if freeze_authorization.sheet_canonical_sha256 != sheet_canonical_sha256:
        raise ValueError("FREEZE_AUTHORIZATION_SHEET_MISMATCH")
    if freeze_authorization.attestation_sha256 != attestation.attestation_sha256:
        raise ValueError("FREEZE_AUTHORIZATION_ATTESTATION_MISMATCH")
    if freeze_authorization.identity_receipt_sha256 != (
        identity_receipt.receipt_payload_sha256
    ):
        raise ValueError("FREEZE_AUTHORIZATION_IDENTITY_MISMATCH")
    if freeze_authorization.reviewer_signature_record_sha256 != (
        reviewer_signature.signature_record_sha256
    ):
        raise ValueError("FREEZE_AUTHORIZATION_REVIEWER_SIGNATURE_MISMATCH")
    if (
        freeze_authorization.received_at != received_at
        or freeze_authorization.frozen_at != frozen_at
    ):
        raise ValueError("FREEZE_AUTHORIZATION_TIME_MISMATCH")
    verify_detached_signature(
        coordinator_signature,
        public_key_pem=coordinator_public_key_pem,
        expected_artifact_sha256=freeze_authorization.authorization_payload_sha256,
        expected_authority_id=freeze_authorization.coordinator_authority_id,
        require_gate_trust=not synthetic_fixture,
    )
    payload = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "handoff_id": manifest.handoff_id,
        "review_kind": manifest.review_kind,
        "manifest_sha256": manifest.manifest_sha256,
        "packet_sha256": manifest.packet_canonical_sha256,
        "protocol_sha256": manifest.protocol_raw_sha256,
        "sheet_raw_sha256": file_sha256(sheet_path),
        "sheet_canonical_sha256": sheet_canonical_sha256,
        "attestation_sha256": attestation.attestation_sha256,
        "identity_receipt_sha256": identity_receipt.receipt_payload_sha256,
        "identity_signature_record_sha256": (
            identity_signature.signature_record_sha256
        ),
        "reviewer_signature_record_sha256": (
            reviewer_signature.signature_record_sha256
        ),
        "freeze_authorization_sha256": (
            freeze_authorization.authorization_payload_sha256
        ),
        "coordinator_signature_record_sha256": (
            coordinator_signature.signature_record_sha256
        ),
        "reviewer_id": attestation.reviewer_id,
        "reviewer_authority_id": freeze_authorization.reviewer_authority_id,
        "coordinator_authority_id": (
            freeze_authorization.coordinator_authority_id
        ),
        "judgment_count": len(sheet["judgments"]),
        "external_identity_receipt_present": True,
        "detached_signatures_verified": True,
        "dual_authority_verified": True,
        "synthetic_fixture": synthetic_fixture,
        "gate_evidence_ready": not synthetic_fixture,
        "received_at": received_at,
        "frozen_at": frozen_at,
        "coordinator_id": coordinator_id,
    }
    return FrozenSheetReceipt(
        **payload,
        receipt_sha256=canonical_sha256(payload),
    )


def make_unknown_domain_result(
    *,
    result_id: str,
    review_kind: ReviewKind,
    recorded_at: datetime,
) -> DomainResultRecord:
    payload = {
        "schema_version": DOMAIN_RESULT_SCHEMA_VERSION,
        "result_id": result_id,
        "review_kind": review_kind,
        "outcome": "UNKNOWN",
        "domain_result_sha256": None,
        "blocker_code": "BLOCKED_DOMAIN_RESULT_UNKNOWN",
        "review_receipt_sha256": None,
        "evaluator_authority_id": None,
        "evaluator_signature_record_sha256": None,
        "synthetic_fixture": False,
        "gate_evidence_ready": False,
        "recorded_at": recorded_at,
    }
    return DomainResultRecord(
        **payload,
        record_sha256=canonical_sha256(payload),
    )


def make_domain_result(
    *,
    result_id: str,
    review_kind: ReviewKind,
    outcome: Literal["PASS", "FAIL", "BLOCKED"],
    evaluator_result_path: Path,
    review_receipt: FrozenSheetReceipt,
    evaluator_signature: DetachedSignatureEvidence,
    evaluator_public_key_pem: bytes,
    recorded_at: datetime,
    blocker_code: str | None = None,
) -> DomainResultRecord:
    review_receipt = FrozenSheetReceipt.model_validate(
        review_receipt.model_dump(mode="json")
    )
    if review_receipt.synthetic_fixture or not review_receipt.gate_evidence_ready:
        raise ValueError("SYNTHETIC_FIXTURE_NOT_GATE_EVIDENCE")
    if evaluator_result_path.is_symlink() or not evaluator_result_path.is_file():
        raise ValueError("EVALUATOR_RESULT_NOT_REGULAR_FILE")
    evaluator_result = _load_public_json(evaluator_result_path)
    if not isinstance(evaluator_result, dict) or not evaluator_result:
        raise ValueError("REAL_EVALUATOR_RESULT_REQUIRED")
    if evaluator_result.get("evidence_kind") != "real_independent_human_review":
        raise ValueError("REAL_EVALUATOR_RESULT_REQUIRED")
    if evaluator_result.get("synthetic_fixture") is not False:
        raise ValueError("SYNTHETIC_FIXTURE_NOT_GATE_EVIDENCE")
    if evaluator_result.get("review_receipt_sha256") != review_receipt.receipt_sha256:
        raise ValueError("EVALUATOR_RESULT_RECEIPT_MISMATCH")
    if evaluator_result.get("quality_status") != outcome:
        raise ValueError("EVALUATOR_RESULT_OUTCOME_MISMATCH")
    if evaluator_result.get("human_review_status") != "COMPLETE":
        raise ValueError("EVALUATOR_HUMAN_REVIEW_INCOMPLETE")
    if int(evaluator_result.get("completed_judgment_count") or 0) <= 0:
        raise ValueError("EVALUATOR_JUDGMENTS_MISSING")
    evaluator_authority_id = evaluator_result.get("evaluator_authority_id")
    if not isinstance(evaluator_authority_id, str) or not evaluator_authority_id:
        raise ValueError("EVALUATOR_AUTHORITY_MISSING")
    domain_result_sha256 = canonical_sha256(evaluator_result)
    verify_detached_signature(
        evaluator_signature,
        public_key_pem=evaluator_public_key_pem,
        expected_artifact_sha256=domain_result_sha256,
        expected_authority_id=evaluator_authority_id,
        require_gate_trust=True,
    )
    if evaluator_signature.synthetic_fixture:
        raise ValueError("SYNTHETIC_FIXTURE_NOT_GATE_EVIDENCE")
    payload = {
        "schema_version": DOMAIN_RESULT_SCHEMA_VERSION,
        "result_id": result_id,
        "review_kind": review_kind,
        "outcome": outcome,
        "domain_result_sha256": domain_result_sha256,
        "blocker_code": blocker_code,
        "review_receipt_sha256": review_receipt.receipt_sha256,
        "evaluator_authority_id": evaluator_authority_id,
        "evaluator_signature_record_sha256": (
            evaluator_signature.signature_record_sha256
        ),
        "synthetic_fixture": False,
        "gate_evidence_ready": True,
        "recorded_at": recorded_at,
    }
    return DomainResultRecord(
        **payload,
        record_sha256=canonical_sha256(payload),
    )


def empty_handoff_ledger(manifest: HandoffManifest) -> HandoffLedger:
    return HandoffLedger(
        schema_version=LEDGER_SCHEMA_VERSION,
        handoff_id=manifest.handoff_id,
        review_kind=manifest.review_kind,
        manifest_sha256=manifest.manifest_sha256,
        packet_sha256=manifest.packet_canonical_sha256,
        entries=[],
    )


def append_handoff_event(
    ledger: HandoffLedger,
    *,
    event_id: str,
    event_type: LedgerEventType,
    recorded_at: datetime,
    receipt: FrozenSheetReceipt | None = None,
    domain_result: DomainResultRecord | None = None,
) -> HandoffLedger:
    ledger = HandoffLedger.model_validate(ledger.model_dump(mode="json"))
    if receipt is not None:
        receipt = FrozenSheetReceipt.model_validate(receipt.model_dump(mode="json"))
    if domain_result is not None:
        domain_result = DomainResultRecord.model_validate(
            domain_result.model_dump(mode="json")
        )
    if any(entry.event_id == event_id for entry in ledger.entries):
        raise ValueError("DUPLICATE_HANDOFF_EVENT_ID")
    if receipt is not None:
        if receipt.handoff_id != ledger.handoff_id:
            raise ValueError("RECEIPT_HANDOFF_ID_MISMATCH")
        if receipt.review_kind != ledger.review_kind:
            raise ValueError("RECEIPT_REVIEW_KIND_MISMATCH")
        if receipt.manifest_sha256 != ledger.manifest_sha256:
            raise ValueError("RECEIPT_MANIFEST_HASH_MISMATCH")
        if receipt.packet_sha256 != ledger.packet_sha256:
            raise ValueError("RECEIPT_PACKET_HASH_MISMATCH")
        if recorded_at < receipt.frozen_at:
            raise ValueError("HANDOFF_EVENT_BEFORE_SHEET_FREEZE")
    if domain_result is not None and domain_result.review_kind != ledger.review_kind:
        raise ValueError("DOMAIN_RESULT_REVIEW_KIND_MISMATCH")
    if domain_result is not None and recorded_at < domain_result.recorded_at:
        raise ValueError("HANDOFF_EVENT_BEFORE_DOMAIN_RESULT")

    if event_type == "HANDOFF_EXPORTED":
        if receipt is not None or domain_result is not None:
            raise ValueError("HANDOFF_EXPORTED_HAS_UNEXPECTED_PAYLOAD")
        outcome_status = "READY_FOR_INDEPENDENT_REVIEW"
    elif event_type == "SHEET_FROZEN":
        if receipt is None or domain_result is not None:
            raise ValueError("SHEET_FROZEN_RECEIPT_REQUIRED")
        outcome_status = "SHEET_FROZEN"
    elif event_type == "UNSEAL_AUTHORIZED":
        if receipt is None or domain_result is not None:
            raise ValueError("UNSEAL_RECEIPT_REQUIRED")
        frozen_receipts = {
            entry.receipt_sha256
            for entry in ledger.entries
            if entry.event_type == "SHEET_FROZEN"
        }
        if receipt.receipt_sha256 not in frozen_receipts:
            raise ValueError("UNSEAL_BEFORE_SHEET_FREEZE")
        outcome_status = "UNSEAL_AUTHORIZED"
    else:
        if domain_result is None or receipt is not None:
            raise ValueError("DOMAIN_RESULT_RECORD_REQUIRED")
        if domain_result.outcome != "UNKNOWN":
            if not TRUSTED_GATE_AUTHORITY_PUBLIC_KEY_SHA256:
                raise ValueError("EXTERNAL_GATE_AUTHORITY_TRUST_NOT_CONFIGURED")
            frozen_receipts = {
                entry.receipt_sha256
                for entry in ledger.entries
                if entry.event_type == "SHEET_FROZEN"
            }
            if domain_result.review_receipt_sha256 not in frozen_receipts:
                raise ValueError("DOMAIN_RESULT_WITHOUT_FROZEN_RECEIPT")
        outcome_status = (
            "BLOCKED_DOMAIN_RESULT_UNKNOWN"
            if domain_result.outcome == "UNKNOWN"
            else domain_result.outcome
        )

    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "recorded_at": recorded_at,
        "manifest_sha256": ledger.manifest_sha256,
        "packet_sha256": ledger.packet_sha256,
        "receipt_sha256": receipt.receipt_sha256 if receipt else None,
        "sheet_sha256": receipt.sheet_canonical_sha256 if receipt else None,
        "attestation_sha256": receipt.attestation_sha256 if receipt else None,
        "coordinator_key_contents_read": (
            False if event_type == "UNSEAL_AUTHORIZED" else None
        ),
        "domain_result_record_sha256": (
            domain_result.record_sha256 if domain_result else None
        ),
        "outcome_status": outcome_status,
        "previous_entry_sha256": (
            ledger.entries[-1].entry_sha256 if ledger.entries else None
        ),
    }
    entry = HandoffLedgerEntry(
        **payload,
        entry_sha256=canonical_sha256(payload),
    )
    return HandoffLedger.model_validate(
        {
            **ledger.model_dump(mode="json"),
            "entries": [
                *[item.model_dump(mode="json") for item in ledger.entries],
                entry.model_dump(mode="json"),
            ],
        }
    )
