from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.knowledge.source_scope import (
    KnowledgeScopeUsage,
    normalize_knowledge_scope_usages,
)
from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentPublicStatus,
)


class ImmutableMaterialsApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MaterialResponse(ImmutableMaterialsApiModel):
    document_id: str
    display_name: str
    media_type: Literal["text/markdown", "text/plain"]
    size_bytes: int
    status: UserDocumentPublicStatus
    enabled: bool
    allowed_usage: tuple[KnowledgeScopeUsage, ...]
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None

    @classmethod
    def from_document(cls, document: UserDocument) -> "MaterialResponse":
        return cls(
            document_id=document.document_id,
            display_name=document.display_title,
            media_type=document.media_type,
            size_bytes=document.size_bytes,
            status=document.public_status,
            enabled=document.enabled,
            allowed_usage=document.allowed_usages,
            created_at=document.created_at,
            updated_at=document.updated_at,
            error_code=document.safe_error_code,
        )


class MaterialsListResponse(ImmutableMaterialsApiModel):
    items: tuple[MaterialResponse, ...]


class MaterialPatchRequest(ImmutableMaterialsApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    allowed_usage: tuple[KnowledgeScopeUsage, ...] | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("display_name must contain safe visible text")
        return normalized

    @field_validator("allowed_usage", mode="before")
    @classmethod
    def normalize_allowed_usage(
        cls, value: object
    ) -> tuple[KnowledgeScopeUsage, ...] | None:
        if value is None:
            return None
        return normalize_knowledge_scope_usages(value)

    @model_validator(mode="after")
    def require_non_null_patch(self):
        if not self.model_fields_set or any(
            getattr(self, field_name) is None
            for field_name in self.model_fields_set
        ):
            raise ValueError("at least one non-null patch field is required")
        return self


class MaterialDeleteResponse(ImmutableMaterialsApiModel):
    document_id: str
    deleted: Literal[True] = True


__all__ = [
    "MaterialDeleteResponse",
    "MaterialPatchRequest",
    "MaterialResponse",
    "MaterialsListResponse",
]
