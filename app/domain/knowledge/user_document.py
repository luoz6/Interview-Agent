from __future__ import annotations

import math
import re
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.knowledge.source_scope import (
    DEFAULT_KNOWLEDGE_SCOPE_USAGES,
    KnowledgeScopeUsage,
    normalize_knowledge_scope_usages,
)


USER_DOCUMENT_MAX_BYTES = 1024 * 1024
USER_DOCUMENT_SUPPORTED_MEDIA_TYPES = frozenset(
    {"text/markdown", "text/plain"}
)
USER_DOCUMENT_SUPPORTED_EXTENSIONS = frozenset({".md", ".txt"})
USER_MATERIALS_CAPABILITIES = (
    "USER_MATERIALS_ENABLED",
    "USER_MATERIALS_INGEST_ENABLED",
)
USER_MATERIALS_PERSISTENCE_PORTS = (
    "UserDocumentStorePort",
    "UserDocumentChunkRepositoryPort",
)


class UserDocumentPublicStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DISABLED = "disabled"
    DELETING = "deleting"


class UserDocumentInternalStage(StrEnum):
    VALIDATION = "validation"
    EXTRACTION = "extraction"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"


class ImmutableUserDocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UserDocument(ImmutableUserDocumentModel):
    document_id: str
    owner_principal_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    display_title: str = Field(min_length=1, max_length=200)
    original_filename: str = Field(min_length=1, max_length=255)
    media_type: Literal["text/markdown", "text/plain"]
    size_bytes: int = Field(ge=1, le=USER_DOCUMENT_MAX_BYTES)
    public_status: UserDocumentPublicStatus
    internal_stage: UserDocumentInternalStage | None = None
    enabled: bool = True
    allowed_usages: tuple[KnowledgeScopeUsage, ...] = (
        DEFAULT_KNOWLEDGE_SCOPE_USAGES
    )
    active_revision_id: str | None = None
    safe_error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
    )
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    @field_validator("document_id", "active_revision_id")
    @classmethod
    def validate_uuid_fields(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _uuid_text(value, info.field_name)

    @field_validator("allowed_usages", mode="before")
    @classmethod
    def normalize_allowed_usages(
        cls, value: object
    ) -> tuple[KnowledgeScopeUsage, ...]:
        return normalize_knowledge_scope_usages(value)

    @field_validator("display_title", "original_filename", mode="before")
    @classmethod
    def normalize_safe_text(cls, value: object, info) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        normalized = " ".join(value.strip().split())
        if any(ord(character) < 32 for character in normalized):
            raise ValueError(f"{info.field_name} contains control characters")
        if info.field_name == "original_filename" and any(
            separator in normalized for separator in ("/", "\\")
        ):
            raise ValueError("original_filename must not contain a path")
        return normalized

    @field_validator("created_at", "updated_at", "deleted_at")
    @classmethod
    def validate_timestamps(cls, value: datetime | None, info) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self):
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.public_status in {
            UserDocumentPublicStatus.READY,
            UserDocumentPublicStatus.DISABLED,
        } and self.active_revision_id is None:
            raise ValueError("ready or disabled documents require an active revision")
        if self.public_status == UserDocumentPublicStatus.READY and not self.enabled:
            raise ValueError("ready documents must be enabled")
        if self.public_status in {
            UserDocumentPublicStatus.DISABLED,
            UserDocumentPublicStatus.DELETING,
        } and self.enabled:
            raise ValueError("disabled or deleting documents must not be enabled")
        if self.public_status == UserDocumentPublicStatus.FAILED:
            if self.safe_error_code is None:
                raise ValueError("failed documents require a safe error code")
        elif self.safe_error_code is not None:
            raise ValueError("only failed documents may expose a safe error code")
        if self.deleted_at is not None:
            raise ValueError("live UserDocument entities cannot be deleted tombstones")
        return self


class UserDocumentRevision(ImmutableUserDocumentModel):
    document_revision_id: str
    document_id: str
    revision: int = Field(ge=1)
    original_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extracted_text_ref: str = Field(min_length=1, max_length=512)
    parser_version: str = Field(min_length=1, max_length=64)
    chunker_version: str = Field(min_length=1, max_length=64)
    embedding_identity: str = Field(min_length=1, max_length=256)
    created_at: datetime

    @field_validator("document_revision_id", "document_id")
    @classmethod
    def validate_uuid_fields(cls, value: str, info) -> str:
        return _uuid_text(value, info.field_name)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value


class UserDocumentChunk(ImmutableUserDocumentModel):
    chunk_id: str
    owner_principal_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    document_id: str
    document_revision_id: str
    position: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    section_label: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=4000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    embedding: tuple[float, ...]
    embedding_identity: str = Field(min_length=1, max_length=256)
    created_at: datetime

    @field_validator("chunk_id", "document_id", "document_revision_id")
    @classmethod
    def validate_uuid_fields(cls, value: str, info) -> str:
        return _uuid_text(value, info.field_name)

    @field_validator("embedding", mode="before")
    @classmethod
    def validate_embedding(cls, value: object) -> tuple[float, ...]:
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("embedding must be a non-empty vector")
        vector = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in vector):
            raise ValueError("embedding must contain only finite values")
        return vector

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value


def embedding_identity_for(provider) -> str:
    values = (
        str(provider.provider_name),
        str(provider.model_name),
        str(provider.model_revision),
        str(provider.dimension),
    )
    if any(not value.strip() for value in values) or not re.fullmatch(r"\d+", values[3]):
        raise ValueError("embedding provider identity is invalid")
    return ":".join(values)


def _uuid_text(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be an opaque UUID") from exc
