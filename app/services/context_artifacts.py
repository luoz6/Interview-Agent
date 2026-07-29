from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
import re
from typing import Any, Literal, Mapping, TypeAlias
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


ArtifactType = Literal[
    "question_conversation",
    "evidence_compression",
    "prep_context",
]
OwnerType = Literal["prep_run", "interview_session", "review_job"]
ArtifactPurpose = Literal[
    "prep_plan_context",
    "interview_conversation_context",
    "interview_evidence_context",
    "review_context",
    "review_evidence_context",
]
ArtifactStatus = Literal["running", "completed", "failed"]

_ARTIFACT_TYPES = frozenset(
    {"question_conversation", "evidence_compression", "prep_context"}
)
_CLAIM_STATUSES = frozenset({"running", "completed"})
_RECORD_STATUSES = frozenset({"completed", "failed"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MACHINE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ContextArtifactBusy(RuntimeError):
    """A live Context Artifact claim is owned by another worker."""


class ContextArtifactLeaseLost(RuntimeError):
    """A Context Artifact mutation no longer owns the fenced claim."""


class ContextArtifactConflict(RuntimeError):
    """Stored Context Artifact state conflicts with the expected identity."""


class ContextArtifactMissing(RuntimeError):
    """An owner-bound Context Artifact reference cannot be resolved."""


class ContextArtifactValidationFailed(ValueError):
    """A compressed payload failed a stable, content-free validation rule."""


class ContextArtifactProviderFailed(RuntimeError):
    """The dedicated Context Compressor provider failed."""


def _require_nonempty(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


def _require_sha256(value: str | None, *, field_name: str) -> None:
    if value is not None and _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        serializable = value.model_dump(mode="json")
    elif hasattr(value, "__dataclass_fields__"):
        serializable = asdict(value)
    else:
        serializable = value
    return json.dumps(
        serializable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class ContextCompressionPolicy:
    artifact_type: ArtifactType
    policy_version: str
    prompt_contract_version: str
    output_schema_version: str
    compressor_operation: str
    compressor_input_cap_tokens: int
    target_output_tokens: int
    max_output_units: int
    max_supporting_excerpt_tokens: int

    def __post_init__(self) -> None:
        if self.artifact_type not in _ARTIFACT_TYPES:
            raise ValueError("artifact_type is unsupported")
        for field_name in (
            "policy_version",
            "prompt_contract_version",
            "output_schema_version",
            "compressor_operation",
        ):
            _require_nonempty(getattr(self, field_name), field_name=field_name)
        for field_name in (
            "compressor_input_cap_tokens",
            "target_output_tokens",
            "max_output_units",
            "max_supporting_excerpt_tokens",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")


def normalize_base_url_identity(value: str | None) -> str | None:
    if value is None:
        return None
    _require_nonempty(value, field_name="base_url_identity")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url_identity must be an HTTP(S) endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base_url_identity must not contain credentials or query data")
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url_identity contains an invalid port") from exc
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


@dataclass(frozen=True)
class ContextCompressorConfig:
    provider: str
    model: str
    base_url_identity: str | None
    temperature: float
    request_timeout_seconds: float
    timeout_policy_version: str
    max_retries: int
    structured_output_mode: str
    tokenizer_family: str | None

    def __post_init__(self) -> None:
        for field_name in (
            "provider",
            "model",
            "timeout_policy_version",
            "structured_output_mode",
        ):
            _require_nonempty(getattr(self, field_name), field_name=field_name)
        if not math.isfinite(self.temperature) or self.temperature < 0:
            raise ValueError("temperature must be a non-negative finite number")
        if (
            not math.isfinite(self.request_timeout_seconds)
            or self.request_timeout_seconds <= 0
        ):
            raise ValueError("request_timeout_seconds must be positive and finite")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.tokenizer_family is not None:
            _require_nonempty(self.tokenizer_family, field_name="tokenizer_family")
        normalized = normalize_base_url_identity(self.base_url_identity)
        if normalized != self.base_url_identity:
            object.__setattr__(self, "base_url_identity", normalized)


def canonical_compressor_settings_payload(config: ContextCompressorConfig) -> str:
    return canonical_json(
        {
            "base_url_identity": config.base_url_identity,
            "temperature": config.temperature,
            "request_timeout_seconds": config.request_timeout_seconds,
            "timeout_policy_version": config.timeout_policy_version,
            "max_retries": config.max_retries,
            "structured_output_mode": config.structured_output_mode,
            "tokenizer_family": config.tokenizer_family,
        }
    )


def compressor_settings_sha256(config: ContextCompressorConfig) -> str:
    return sha256(
        canonical_compressor_settings_payload(config).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ContextArtifactIdentityMaterial:
    artifact_type: ArtifactType
    privacy_scope_sha256: str
    source_sha256: str
    source_manifest_sha256: str | None
    semantic_focus_sha256: str | None
    compression_policy_version: str
    prompt_contract_version: str
    output_schema_version: str
    compressor_provider: str
    compressor_model: str
    compressor_settings_sha256: str
    target_output_tokens: int

    def __post_init__(self) -> None:
        if self.artifact_type not in _ARTIFACT_TYPES:
            raise ValueError("artifact_type is unsupported")
        for field_name in (
            "privacy_scope_sha256",
            "source_sha256",
            "source_manifest_sha256",
            "semantic_focus_sha256",
            "compressor_settings_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name=field_name)
        for field_name in (
            "compression_policy_version",
            "prompt_contract_version",
            "output_schema_version",
            "compressor_provider",
            "compressor_model",
        ):
            _require_nonempty(getattr(self, field_name), field_name=field_name)
        if self.target_output_tokens <= 0:
            raise ValueError("target_output_tokens must be positive")


def canonical_identity_payload(material: ContextArtifactIdentityMaterial) -> str:
    if not isinstance(material, ContextArtifactIdentityMaterial):
        raise TypeError("canonical identity requires identity material")
    return canonical_json(material)


@dataclass(frozen=True)
class ContextArtifactIdentity:
    artifact_key: str
    material: ContextArtifactIdentityMaterial

    def __post_init__(self) -> None:
        _require_sha256(self.artifact_key, field_name="artifact_key")
        expected = sha256(
            canonical_identity_payload(self.material).encode("utf-8")
        ).hexdigest()
        if self.artifact_key != expected:
            raise ValueError("artifact_key does not match identity material")

    @classmethod
    def from_material(
        cls,
        material: ContextArtifactIdentityMaterial,
    ) -> "ContextArtifactIdentity":
        payload = canonical_identity_payload(material)
        return cls(
            artifact_key=sha256(payload.encode("utf-8")).hexdigest(),
            material=material,
        )


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContextArtifactRef(_ArtifactModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    artifact_ref: str = Field(
        pattern=r"^context-artifact-ref:[A-Za-z0-9-]{1,128}$"
    )
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_type: ArtifactType
    compression_policy_version: str = Field(min_length=1, max_length=128)


class CompressionSourceSegment(_ArtifactModel):
    segment_index: int = Field(ge=0)
    segment_type: Literal[
        "conversation_message",
        "evidence_paragraph",
        "job_section",
        "resume_section",
        "knowledge_metadata",
    ]
    content: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_content_digest(self):
        if "\x00" in self.content:
            raise ValueError("source content must not contain NUL")
        try:
            encoded = self.content.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("source content must be valid UTF-8") from exc
        expected = sha256(encoded).hexdigest()
        if self.content_sha256 != expected:
            raise ValueError("content_sha256 does not match source content")
        return self


class AnchoredCompressedUnit(_ArtifactModel):
    summary: str = Field(min_length=1)
    source_segment_sha256: list[str] = Field(min_length=1)
    supporting_excerpts: list[str] = Field(default_factory=list)

    @field_validator("source_segment_sha256")
    @classmethod
    def validate_source_digests(cls, values: list[str]) -> list[str]:
        if any(_SHA256_RE.fullmatch(value) is None for value in values):
            raise ValueError("source segment anchors must be SHA-256 digests")
        if len(set(values)) != len(values):
            raise ValueError("source segment anchors must be unique within a unit")
        return values

    @field_validator("supporting_excerpts")
    @classmethod
    def validate_excerpts(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("supporting excerpts must be non-empty")
        if any("\x00" in value for value in values):
            raise ValueError("supporting excerpts must not contain NUL")
        return values

    @field_validator("summary")
    @classmethod
    def validate_summary_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("summary must not contain NUL")
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("summary must be valid UTF-8") from exc
        return value


class QuestionConversationArtifact(_ArtifactModel):
    schema_version: Literal["question-conversation-v1"]
    question_id_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    units: list[AnchoredCompressedUnit]
    unresolved_topics: list[AnchoredCompressedUnit] = Field(default_factory=list)
    source_message_count: int = Field(ge=0)


class EvidenceCompressionArtifact(_ArtifactModel):
    schema_version: Literal["evidence-compression-v1"]
    evidence_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    units: list[AnchoredCompressedUnit]
    exact_excerpts: list[str] = Field(default_factory=list)

    @field_validator("exact_excerpts")
    @classmethod
    def validate_exact_excerpts(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("exact excerpts must be non-empty")
        if any("\x00" in value for value in values):
            raise ValueError("exact excerpts must not contain NUL")
        return values


class PrepContextArtifact(_ArtifactModel):
    schema_version: Literal["prep-context-v1"]
    role_units: list[AnchoredCompressedUnit]
    responsibility_units: list[AnchoredCompressedUnit]
    experience_units: list[AnchoredCompressedUnit]
    project_units: list[AnchoredCompressedUnit]
    constraint_units: list[AnchoredCompressedUnit]


ArtifactPayload: TypeAlias = (
    QuestionConversationArtifact
    | EvidenceCompressionArtifact
    | PrepContextArtifact
)

_PAYLOAD_MODELS: dict[ArtifactType, type[ArtifactPayload]] = {
    "question_conversation": QuestionConversationArtifact,
    "evidence_compression": EvidenceCompressionArtifact,
    "prep_context": PrepContextArtifact,
}
_SCHEMA_ARTIFACT_TYPES = {
    "question-conversation-v1": "question_conversation",
    "evidence-compression-v1": "evidence_compression",
    "prep-context-v1": "prep_context",
}


def parse_artifact_payload(
    artifact_type: ArtifactType,
    payload: Mapping[str, Any] | ArtifactPayload,
) -> ArtifactPayload:
    model = _PAYLOAD_MODELS[artifact_type]
    return model.model_validate(
        payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    )


def _infer_artifact_type(payload: Mapping[str, Any] | ArtifactPayload) -> ArtifactType:
    if isinstance(payload, QuestionConversationArtifact):
        return "question_conversation"
    if isinstance(payload, EvidenceCompressionArtifact):
        return "evidence_compression"
    if isinstance(payload, PrepContextArtifact):
        return "prep_context"
    schema_version = payload.get("schema_version")
    try:
        return _SCHEMA_ARTIFACT_TYPES[str(schema_version)]  # type: ignore[return-value]
    except KeyError as exc:
        raise ValueError("unknown Context Artifact payload schema") from exc


def artifact_payload_sha256(
    payload: Mapping[str, Any] | ArtifactPayload,
) -> str:
    artifact_type = _infer_artifact_type(payload)
    validated = parse_artifact_payload(artifact_type, payload)
    return sha256(canonical_json(validated).encode("utf-8")).hexdigest()


def _validate_completed_payload_contract(
    *,
    identity: ContextArtifactIdentity,
    output_sha256: str,
    payload: dict[str, Any],
) -> None:
    validated = parse_artifact_payload(identity.material.artifact_type, payload)
    if validated.schema_version != identity.material.output_schema_version:
        raise ValueError("completed payload schema conflicts with identity")
    if artifact_payload_sha256(validated) != output_sha256:
        raise ValueError("completed payload digest does not match output_sha256")


@dataclass(frozen=True)
class ContextArtifactClaim:
    artifact_id: str
    artifact_key: str
    status: Literal["running", "completed"]
    claim_token: str | None
    fencing_version: int
    claim_owner: str | None
    output_sha256: str | None
    payload: dict[str, Any] | None

    def __post_init__(self) -> None:
        _require_nonempty(self.artifact_id, field_name="artifact_id")
        _require_sha256(self.artifact_key, field_name="artifact_key")
        if self.status not in _CLAIM_STATUSES:
            raise ValueError("claim status must be running or completed")
        if self.fencing_version < 1:
            raise ValueError("fencing_version must be positive")
        if self.status == "running":
            _require_nonempty(self.claim_token or "", field_name="claim_token")
            _require_nonempty(self.claim_owner or "", field_name="claim_owner")
            if self.output_sha256 is not None or self.payload is not None:
                raise ValueError("running claim cannot contain output")
        else:
            if self.claim_token is not None or self.claim_owner is not None:
                raise ValueError("completed claim cannot retain ownership")
            _require_sha256(self.output_sha256, field_name="output_sha256")
            if self.output_sha256 is None or self.payload is None:
                raise ValueError("completed claim requires output")


@dataclass(frozen=True)
class ContextArtifactRecord:
    artifact_id: str
    identity: ContextArtifactIdentity
    status: Literal["completed", "failed"]
    output_sha256: str | None
    payload: dict[str, Any] | None
    last_error_code: str | None
    completed_at: datetime | None

    def __post_init__(self) -> None:
        _require_nonempty(self.artifact_id, field_name="artifact_id")
        if self.status not in _RECORD_STATUSES:
            raise ValueError("record status must be completed or failed")
        if self.status == "completed":
            _require_sha256(self.output_sha256, field_name="output_sha256")
            if self.output_sha256 is None or self.payload is None:
                raise ValueError("completed record requires output")
            if self.completed_at is None:
                raise ValueError("completed record requires completed_at")
            _require_aware(self.completed_at, field_name="completed_at")
            if self.last_error_code is not None:
                raise ValueError("completed record cannot contain an error")
            _validate_completed_payload_contract(
                identity=self.identity,
                output_sha256=self.output_sha256,
                payload=self.payload,
            )
        else:
            if self.output_sha256 is not None or self.payload is not None:
                raise ValueError("failed record cannot contain output")
            if self.completed_at is not None:
                raise ValueError("failed record cannot contain completed_at")
            if (
                self.last_error_code is None
                or _MACHINE_CODE_RE.fullmatch(self.last_error_code) is None
            ):
                raise ValueError("failed record requires a stable error code")


@dataclass(frozen=True)
class ContextArtifactCleanupPolicy:
    completed_before: datetime
    failed_before: datetime
    prep_ref_expires_before: datetime
    batch_size: int

    def __post_init__(self) -> None:
        for field_name in (
            "completed_before",
            "failed_before",
            "prep_ref_expires_before",
        ):
            _require_aware(getattr(self, field_name), field_name=field_name)
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")


@dataclass(frozen=True)
class ContextArtifactCleanupResult:
    deleted_owner_refs: int = 0
    deleted_completed_artifacts: int = 0
    deleted_failed_artifacts: int = 0

    def __post_init__(self) -> None:
        if min(
            self.deleted_owner_refs,
            self.deleted_completed_artifacts,
            self.deleted_failed_artifacts,
        ) < 0:
            raise ValueError("cleanup counts must be non-negative")
