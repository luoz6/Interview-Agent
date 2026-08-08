from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256 as _sha256
import json
import re
from typing import Any, Literal


CONTEXT_SOURCE_IDENTITY_SCHEMA_VERSION = "context-source-identity-v1"
SOURCE_REPRESENTATION_IDENTITY_SCHEMA_VERSION = (
    "context-source-representation-v1"
)

ConversationRole = Literal["interviewer", "candidate"]
ExactDeduplicationMode = Literal["disabled", "shadow", "enforce"]
ConversationSequenceContract = Literal[
    "authoritative-v1",
    "state-order-v1",
]
SourceRepresentation = Literal[
    "authoritative_raw",
    "bounded_raw",
    "compressed_projection",
]

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_CONVERSATION_ROLES = frozenset({"interviewer", "candidate"})
_SEQUENCE_CONTRACTS = frozenset({"authoritative-v1", "state-order-v1"})
_REPRESENTATIONS = frozenset(
    {"authoritative_raw", "bounded_raw", "compressed_projection"}
)
_EXACT_DEDUPLICATION_MODES = frozenset({"disabled", "shadow", "enforce"})


@dataclass(frozen=True)
class ContextSourceIdentityConfig:
    exact_deduplication_mode: ExactDeduplicationMode = "disabled"
    identity_schema_version: str = CONTEXT_SOURCE_IDENTITY_SCHEMA_VERSION
    representation_schema_version: str = (
        SOURCE_REPRESENTATION_IDENTITY_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        if self.exact_deduplication_mode not in _EXACT_DEDUPLICATION_MODES:
            raise ValueError("unsupported exact deduplication mode")
        _require_schema_version(self.identity_schema_version)
        if (
            self.representation_schema_version
            != SOURCE_REPRESENTATION_IDENTITY_SCHEMA_VERSION
        ):
            raise ValueError("unsupported source representation identity version")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and normalized")
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8") from exc
    return value


def _require_sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def source_value_sha256(value: str) -> str:
    """Digest an authoritative identifier without treating content as identity."""

    normalized = _require_text(value, field_name="source_value")
    return _sha256(normalized.encode("utf-8")).hexdigest()


def content_sha256(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("content must be a string")
    if "\x00" in content:
        raise ValueError("content must not contain NUL")
    try:
        encoded = content.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("content must be valid UTF-8") from exc
    return _sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ConversationSourceIdentity:
    owner_scope: str
    question_id: str
    sequence_no: int
    sequence_contract: ConversationSequenceContract
    role: ConversationRole
    content_sha256: str
    schema_version: str = CONTEXT_SOURCE_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.owner_scope, field_name="owner_scope")
        _require_text(self.question_id, field_name="question_id")
        if (
            not isinstance(self.sequence_no, int)
            or isinstance(self.sequence_no, bool)
            or self.sequence_no <= 0
        ):
            raise ValueError("sequence_no must be a positive integer")
        if self.sequence_contract not in _SEQUENCE_CONTRACTS:
            raise ValueError("conversation sequence contract is unsupported")
        if self.role not in _CONVERSATION_ROLES:
            raise ValueError("conversation source role is unsupported")
        _require_sha256(self.content_sha256, field_name="content_sha256")

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "content_sha256": self.content_sha256,
            "owner_scope": self.owner_scope,
            "question_id": self.question_id,
            "role": self.role,
            "schema_version": self.schema_version,
            "sequence_contract": self.sequence_contract,
            "sequence_no": self.sequence_no,
            "source_kind": "conversation",
        }

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_payload)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceSourceIdentity:
    owner_scope: str
    provenance: str
    chunk_or_evidence_id_sha256: str
    content_sha256: str
    corpus_manifest_sha256: str
    role: str = "knowledge_evidence"
    schema_version: str = CONTEXT_SOURCE_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_schema_version(self.schema_version)
        _require_text(self.owner_scope, field_name="owner_scope")
        _require_text(self.provenance, field_name="provenance")
        _require_sha256(
            self.chunk_or_evidence_id_sha256,
            field_name="chunk_or_evidence_id_sha256",
        )
        _require_sha256(self.content_sha256, field_name="content_sha256")
        _require_sha256(
            self.corpus_manifest_sha256,
            field_name="corpus_manifest_sha256",
        )
        if self.role != "knowledge_evidence":
            raise ValueError("evidence source role is unsupported")

    @property
    def canonical_payload(self) -> dict[str, str]:
        return {
            "chunk_or_evidence_id_sha256": self.chunk_or_evidence_id_sha256,
            "content_sha256": self.content_sha256,
            "corpus_manifest_sha256": self.corpus_manifest_sha256,
            "owner_scope": self.owner_scope,
            "provenance": self.provenance,
            "role": self.role,
            "schema_version": self.schema_version,
            "source_kind": "evidence",
        }

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_payload)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceRepresentationIdentity:
    source_identity_sha256: str
    role: str
    representation: SourceRepresentation
    content_sha256: str
    schema_version: str = SOURCE_REPRESENTATION_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_REPRESENTATION_IDENTITY_SCHEMA_VERSION:
            raise ValueError("unsupported source representation identity version")
        _require_sha256(
            self.source_identity_sha256,
            field_name="source_identity_sha256",
        )
        _require_text(self.role, field_name="role")
        if self.representation not in _REPRESENTATIONS:
            raise ValueError("source representation is unsupported")
        _require_sha256(self.content_sha256, field_name="content_sha256")

    @property
    def canonical_payload(self) -> dict[str, str]:
        return {
            "content_sha256": self.content_sha256,
            "representation": self.representation,
            "role": self.role,
            "schema_version": self.schema_version,
            "source_identity_sha256": self.source_identity_sha256,
        }

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.canonical_payload)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_json.encode("utf-8")).hexdigest()


def _require_schema_version(value: object) -> None:
    if value != CONTEXT_SOURCE_IDENTITY_SCHEMA_VERSION:
        raise ValueError("unsupported context source identity version")


def build_conversation_source_identity(
    **kwargs: object,
) -> ConversationSourceIdentity:
    return ConversationSourceIdentity(**kwargs)  # type: ignore[arg-type]


def build_evidence_source_identity(**kwargs: object) -> EvidenceSourceIdentity:
    return EvidenceSourceIdentity(**kwargs)  # type: ignore[arg-type]
