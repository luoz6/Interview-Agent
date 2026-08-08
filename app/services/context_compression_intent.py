from __future__ import annotations

from hashlib import sha256
import hmac
import json
import re
from typing import Any, Literal, Mapping, TypeAlias
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator


ConsumerOperation: TypeAlias = Literal[
    "followup",
    "question_review",
    "report",
    "prep",
]
CompressionPhase: TypeAlias = Literal["prep", "interview", "review", "report"]
PreservationRule: TypeAlias = Literal[
    "candidate_claims",
    "numbers",
    "identifiers",
    "tradeoffs",
    "failure_boundaries",
    "unresolved_topics",
    "evidence_provenance",
]
ProhibitedAuthorityUpgrade: TypeAlias = Literal[
    "candidate_exact_quote",
    "authoritative_scoring_evidence",
    "new_fact",
    "identity_inference",
]

MAX_FOCUS_CHARACTERS = 512
MAX_RAW_FOCUS_CHARACTERS = 4096
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WHITESPACE_RE = re.compile(r"\s+")
_PRESERVATION_ORDER = (
    "candidate_claims",
    "numbers",
    "identifiers",
    "tradeoffs",
    "failure_boundaries",
    "unresolved_topics",
    "evidence_provenance",
)
_AUTHORITY_UPGRADE_ORDER = (
    "candidate_exact_quote",
    "authoritative_scoring_evidence",
    "new_fact",
    "identity_inference",
)
CONVERSATION_PRESERVATION_RULES: tuple[PreservationRule, ...] = (
    "candidate_claims",
    "numbers",
    "identifiers",
    "tradeoffs",
    "failure_boundaries",
    "unresolved_topics",
)
EVIDENCE_PRESERVATION_RULES: tuple[PreservationRule, ...] = (
    "numbers",
    "identifiers",
    "evidence_provenance",
)
ALL_PROHIBITED_AUTHORITY_UPGRADES: tuple[
    ProhibitedAuthorityUpgrade, ...
] = _AUTHORITY_UPGRADE_ORDER
_BIDI_FORMAT_CONTROLS = frozenset(
    {
        "\u202a",  # LEFT-TO-RIGHT EMBEDDING
        "\u202b",  # RIGHT-TO-LEFT EMBEDDING
        "\u202c",  # POP DIRECTIONAL FORMATTING
        "\u202d",  # LEFT-TO-RIGHT OVERRIDE
        "\u202e",  # RIGHT-TO-LEFT OVERRIDE
        "\u2066",  # LEFT-TO-RIGHT ISOLATE
        "\u2067",  # RIGHT-TO-LEFT ISOLATE
        "\u2068",  # FIRST STRONG ISOLATE
        "\u2069",  # POP DIRECTIONAL ISOLATE
    }
)


class CompressionIntent(BaseModel):
    """Bounded, canonical semantic intent for one compression operation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    schema_version: Literal["compression-intent-v1"]
    consumer_operation: ConsumerOperation
    phase: CompressionPhase
    source_focus: str | None = Field(default=None, max_length=MAX_FOCUS_CHARACTERS)
    current_focus: str | None = Field(default=None, max_length=MAX_FOCUS_CHARACTERS)
    # Empty rule sets are valid. The finite enums and explicit maximum lengths
    # bound both collections without inventing a minimum the contract does not
    # declare.
    preserve: tuple[PreservationRule, ...] = Field(max_length=7)
    authority: Literal["non_authoritative"]
    prohibited_authority_upgrades: tuple[
        ProhibitedAuthorityUpgrade, ...
    ] = Field(max_length=4)

    @field_validator("source_focus", "current_focus", mode="before")
    @classmethod
    def normalize_focus(cls, value: Any) -> Any:
        if value is None or not isinstance(value, str):
            return value
        if len(value) > MAX_RAW_FOCUS_CHARACTERS:
            raise ValueError("raw focus text exceeds the maximum length")
        if "\x00" in value:
            raise ValueError("focus text must not contain NUL")
        normalized = unicodedata.normalize("NFC", value)
        normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
        if any(
            unicodedata.category(character) in {"Cc", "Cs"}
            or character in _BIDI_FORMAT_CONTROLS
            for character in normalized
        ):
            raise ValueError("focus text contains an unsafe Unicode control")
        try:
            normalized.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("focus text must be valid UTF-8") from exc
        return normalized or None

    @field_validator("preserve")
    @classmethod
    def normalize_preservation_rules(
        cls,
        values: tuple[PreservationRule, ...],
    ) -> tuple[PreservationRule, ...]:
        return tuple(
            sorted(set(values), key=_PRESERVATION_ORDER.index)
        )

    @field_validator("prohibited_authority_upgrades")
    @classmethod
    def normalize_authority_upgrades(
        cls,
        values: tuple[ProhibitedAuthorityUpgrade, ...],
    ) -> tuple[ProhibitedAuthorityUpgrade, ...]:
        return tuple(
            sorted(set(values), key=_AUTHORITY_UPGRADE_ORDER.index)
        )


def canonical_compression_intent_payload(
    intent: CompressionIntent | Mapping[str, Any],
) -> str:
    # model_copy(update=...) does not validate updates. Treat even an existing
    # model instance as untrusted at the immutable identity boundary.
    validated = CompressionIntent.model_validate(intent)
    return json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def compression_intent_sha256(
    intent: CompressionIntent | Mapping[str, Any],
) -> str:
    return sha256(
        canonical_compression_intent_payload(intent).encode("utf-8")
    ).hexdigest()


def validate_compression_intent_digest(
    intent: CompressionIntent | Mapping[str, Any],
    expected_sha256: str,
) -> None:
    if _SHA256_RE.fullmatch(expected_sha256) is None:
        raise ValueError(
            "expected compression intent digest must be a lowercase SHA-256 digest"
        )
    actual_sha256 = compression_intent_sha256(intent)
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        raise ValueError("compression intent digest mismatch")
