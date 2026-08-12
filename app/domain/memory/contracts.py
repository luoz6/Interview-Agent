from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CANONICALIZATION_VERSION = "principal-memory-canonical-json-v1"
FACT_SCHEMA_VERSION = "principal-memory-fact-v1"
CONSENT_POLICY_VERSION = "principal-memory-consent-v1"
TAXONOMY_VERSION = "principal-memory-taxonomy-v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

ALLOWED_TAXONOMY: dict[str, frozenset[str]] = {
    "interview_language": frozenset({"zh_hans", "en", "mixed"}),
    "target_role_family": frozenset(
        {"backend", "frontend", "fullstack", "data", "platform", "mobile", "qa", "security"}
    ),
    "focus_topic": frozenset(
        {"python", "java", "sql", "fastapi", "redis", "mysql", "postgresql", "kafka", "system-design", "reliability"}
    ),
    "confirmed_skill": frozenset(
        {"python", "java", "sql", "fastapi", "redis", "mysql", "postgresql", "kafka", "system-design", "reliability"}
    ),
    "learning_goal": frozenset(
        {"python", "java", "sql", "fastapi", "redis", "mysql", "postgresql", "kafka", "system-design", "reliability"}
    ),
    "accessibility_preference": frozenset(
        {"reduced_motion", "high_contrast", "keyboard_only", "screen_reader", "extra_time", "text_only"}
    ),
}
FACT_TYPE_KEYS = {
    "declared_preference": frozenset(
        {"interview_language", "target_role_family", "focus_topic"}
    ),
    "confirmed_skill": frozenset({"confirmed_skill"}),
    "learning_goal": frozenset({"learning_goal"}),
    "accessibility_preference": frozenset({"accessibility_preference"}),
}
EXCLUSIVE_TAXONOMY_KEYS = frozenset(
    {"interview_language", "target_role_family", "accessibility_preference"}
)
# These policies are intentionally domain-owned.  API and UI projections must
# consume them instead of inferring permissions from the taxonomy shape.
USER_DECLARABLE_TAXONOMY_KEYS = frozenset(ALLOWED_TAXONOMY)
USER_EDITABLE_TAXONOMY_KEYS = EXCLUSIVE_TAXONOMY_KEYS


def principal_memory_fact_type_for_taxonomy_key(key: str) -> str:
    matches = [fact_type for fact_type, keys in FACT_TYPE_KEYS.items() if key in keys]
    if len(matches) != 1:
        raise ValueError("principal memory taxonomy key has no unique fact type")
    return matches[0]


def _canonical_scalar(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError("principal fact scalar must be a bounded non-empty string")
    normalized = unicodedata.normalize("NFC", value)
    if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in normalized):
        raise ValueError("principal fact scalar contains unsupported characters")
    return normalized


def canonical_principal_fact(value: dict[str, str]) -> str:
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("principal fact must contain exactly one taxonomy value")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        normalized_key = _canonical_scalar(key)
        normalized_value = _canonical_scalar(item)
        if normalized_key in normalized:
            raise ValueError("principal fact keys collide after NFC")
        allowed = ALLOWED_TAXONOMY.get(normalized_key)
        if allowed is None or normalized_value not in allowed:
            raise ValueError("principal fact is outside the approved taxonomy")
        normalized[normalized_key] = normalized_value
    return json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def validate_normalized_fact(*, fact_type: str, normalized_fact: str) -> str:
    try:
        payload = json.loads(normalized_fact)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("normalized_fact must be canonical JSON") from exc
    canonical = canonical_principal_fact(payload)
    if canonical != normalized_fact:
        raise ValueError("normalized_fact is not canonical JSON")
    key = next(iter(payload))
    if key not in FACT_TYPE_KEYS.get(fact_type, frozenset()):
        raise ValueError("normalized_fact key conflicts with fact_type")
    return canonical


def derive_principal_fact_taxonomy_keys(
    *,
    fact_type: str,
    normalized_fact: str,
) -> tuple[str, str | None]:
    """Derive database-owned taxonomy keys from canonical fact content."""

    canonical = validate_normalized_fact(
        fact_type=fact_type,
        normalized_fact=normalized_fact,
    )
    taxonomy_key = next(iter(json.loads(canonical)))
    exclusive_scope_key = (
        taxonomy_key if taxonomy_key in EXCLUSIVE_TAXONOMY_KEYS else None
    )
    return taxonomy_key, exclusive_scope_key


def derive_principal_fact_id(
    *,
    deployment_id: str,
    principal_id: str,
    fact_type: str,
    normalized_fact: str,
    source_manifest_sha256: str,
    source_excerpt_sha256: str,
    consent_policy_version: str,
    taxonomy_version: str,
    canonicalization_version: str = CANONICALIZATION_VERSION,
) -> str:
    payload = json.dumps(
        {
            "canonicalization_version": canonicalization_version,
            "consent_policy_version": consent_policy_version,
            "deployment_id": deployment_id,
            "fact_type": fact_type,
            "normalized_fact": normalized_fact,
            "principal_id": principal_id,
            "source_excerpt_sha256": source_excerpt_sha256,
            "source_manifest_sha256": source_manifest_sha256,
            "taxonomy_version": taxonomy_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PrincipalMemoryFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["principal-memory-fact-v1"] = FACT_SCHEMA_VERSION
    fact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    deployment_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    principal_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    fact_type: Literal[
        "declared_preference",
        "confirmed_skill",
        "learning_goal",
        "accessibility_preference",
    ]
    normalized_fact: str = Field(min_length=2, max_length=512)
    confidence: float = Field(ge=0, le=1)
    authority: Literal["user_declared", "model_proposed"]
    canonicalization_version: Literal[
        "principal-memory-canonical-json-v1"
    ] = CANONICALIZATION_VERSION
    status: Literal[
        "proposed",
        "active",
        "rejected",
        "superseded",
        "expired",
        "revoked",
        "deleted",
    ] = "proposed"
    source_session_id: str = Field(min_length=1, max_length=128)
    source_question_id: str | None = Field(default=None, max_length=128)
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_excerpt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    consent_policy_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    taxonomy_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    user_confirmed: bool = False
    version: int = Field(default=1, ge=1)
    created_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime | None = None
    supersedes_fact_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    revoked_at: datetime | None = None
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def validate_contract(self):
        validate_normalized_fact(
            fact_type=self.fact_type,
            normalized_fact=self.normalized_fact,
        )
        expected = derive_principal_fact_id(
            deployment_id=self.deployment_id,
            principal_id=self.principal_id,
            fact_type=self.fact_type,
            normalized_fact=self.normalized_fact,
            source_manifest_sha256=self.source_manifest_sha256,
            source_excerpt_sha256=self.source_excerpt_sha256,
            consent_policy_version=self.consent_policy_version,
            taxonomy_version=self.taxonomy_version,
            canonicalization_version=self.canonicalization_version,
        )
        if expected != self.fact_id:
            raise ValueError("fact_id does not match immutable fact identity")
        if self.status == "active" and (
            not self.user_confirmed or self.confirmed_at is None
        ):
            raise ValueError("active principal facts require user confirmation")
        if self.status == "proposed" and self.user_confirmed:
            raise ValueError("proposed principal facts cannot be confirmed")
        if self.revoked_at is not None and self.status != "revoked":
            raise ValueError("revoked_at requires revoked status")
        if self.deleted_at is not None and self.status != "deleted":
            raise ValueError("deleted_at requires deleted status")
        for timestamp in (
            self.created_at,
            self.confirmed_at,
            self.expires_at,
            self.revoked_at,
            self.deleted_at,
        ):
            if timestamp is not None and timestamp.tzinfo is None:
                raise ValueError("principal memory timestamps must be timezone-aware")
        return self
