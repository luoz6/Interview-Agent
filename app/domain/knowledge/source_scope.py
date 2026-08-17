from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


INTERVIEW_KNOWLEDGE_SCOPE_SCHEMA_VERSION = "interview-knowledge-scope-v1"
KNOWLEDGE_SOURCE_SCOPE_SCHEMA_VERSION = "knowledge-source-scope-v1"
KnowledgeScopeUsage = Literal["question", "follow_up", "feedback"]
_USAGE_ORDER = {"question": 0, "follow_up": 1, "feedback": 2}
DEFAULT_KNOWLEDGE_SCOPE_USAGES: tuple[KnowledgeScopeUsage, ...] = (
    "question",
    "follow_up",
    "feedback",
)


def normalize_knowledge_scope_usages(
    value: object,
) -> tuple[KnowledgeScopeUsage, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("allowed usages must be a list or tuple")
    usages = tuple(value)
    if not usages:
        raise ValueError("allowed usages must not be empty")
    if any(usage not in _USAGE_ORDER for usage in usages):
        raise ValueError("allowed usages contain an unsupported usage")
    if len(usages) != len(set(usages)):
        raise ValueError("allowed usages must be unique")
    return tuple(sorted(usages, key=_USAGE_ORDER.__getitem__))


class ImmutableKnowledgeScopeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SelectedUserDocumentRevision(ImmutableKnowledgeScopeModel):
    document_id: str
    document_revision_id: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_usages: tuple[KnowledgeScopeUsage, ...]

    @field_validator("document_id", "document_revision_id")
    @classmethod
    def validate_uuid_fields(cls, value: str, info) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"{info.field_name} must be an opaque UUID") from exc

    @field_validator("allowed_usages", mode="before")
    @classmethod
    def normalize_allowed_usages(
        cls, value: object
    ) -> tuple[KnowledgeScopeUsage, ...]:
        return normalize_knowledge_scope_usages(value)


class InterviewKnowledgeScopeSnapshot(ImmutableKnowledgeScopeModel):
    schema_version: Literal["interview-knowledge-scope-v1"] = (
        INTERVIEW_KNOWLEDGE_SCOPE_SCHEMA_VERSION
    )
    include_system_knowledge: bool = True
    selected_documents: tuple[SelectedUserDocumentRevision, ...] = ()
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime | None = None

    @field_validator("selected_documents", mode="before")
    @classmethod
    def normalize_selected_documents(cls, value: object):
        if not isinstance(value, (list, tuple)):
            raise ValueError("selected_documents must be a list or tuple")
        documents = tuple(
            SelectedUserDocumentRevision.model_validate(item) for item in value
        )
        return tuple(
            sorted(
                documents,
                key=lambda item: (item.document_id, item.document_revision_id),
            )
        )

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("knowledge scope created_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_snapshot(self):
        document_ids = [item.document_id for item in self.selected_documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("selected_documents must contain one revision per document")
        if self.created_at is None and (
            not self.include_system_knowledge or self.selected_documents
        ):
            raise ValueError(
                "only the legacy system-knowledge-only scope may omit created_at"
            )
        return self


class KnowledgeSourceScope(ImmutableKnowledgeScopeModel):
    """Internal retrieval constraint derived from one frozen Plan/Session scope."""

    schema_version: Literal["knowledge-source-scope-v1"] = (
        KNOWLEDGE_SOURCE_SCOPE_SCHEMA_VERSION
    )
    include_system_knowledge: bool
    owner_principal_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]{1,128}$",
    )
    usage: KnowledgeScopeUsage
    selected_documents: tuple[SelectedUserDocumentRevision, ...] = ()
    source_scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("selected_documents", mode="before")
    @classmethod
    def normalize_selected_documents(cls, value: object):
        if not isinstance(value, (list, tuple)):
            raise ValueError("selected_documents must be a list or tuple")
        documents = tuple(
            SelectedUserDocumentRevision.model_validate(item) for item in value
        )
        return tuple(
            sorted(
                documents,
                key=lambda item: (item.document_id, item.document_revision_id),
            )
        )

    @model_validator(mode="after")
    def validate_source_scope(self):
        if self.selected_documents and self.owner_principal_id is None:
            raise ValueError("user material retrieval requires a current principal")
        document_ids = [item.document_id for item in self.selected_documents]
        revision_ids = [
            item.document_revision_id for item in self.selected_documents
        ]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("source scope must contain one revision per document")
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("source scope revision IDs must be unique")
        if any(self.usage not in item.allowed_usages for item in self.selected_documents):
            raise ValueError("source scope contains a disallowed usage")
        expected = knowledge_source_scope_sha256(
            include_system_knowledge=self.include_system_knowledge,
            usage=self.usage,
            selected_documents=self.selected_documents,
        )
        if self.source_scope_sha256 != expected:
            raise ValueError("source_scope_sha256 does not match source scope")
        return self

    @property
    def allowed_document_revision_ids(self) -> tuple[str, ...]:
        return tuple(
            item.document_revision_id for item in self.selected_documents
        )

    @property
    def selected_document_by_revision_id(
        self,
    ) -> dict[str, SelectedUserDocumentRevision]:
        return {
            item.document_revision_id: item for item in self.selected_documents
        }


def build_knowledge_source_scope(
    snapshot: InterviewKnowledgeScopeSnapshot,
    *,
    owner_principal_id: str | None,
    usage: KnowledgeScopeUsage,
) -> KnowledgeSourceScope:
    validated = InterviewKnowledgeScopeSnapshot.model_validate(
        snapshot.model_dump(mode="json")
    )
    selected_documents = tuple(
        item for item in validated.selected_documents if usage in item.allowed_usages
    )
    return KnowledgeSourceScope(
        include_system_knowledge=validated.include_system_knowledge,
        owner_principal_id=owner_principal_id,
        usage=usage,
        selected_documents=selected_documents,
        source_scope_sha256=knowledge_source_scope_sha256(
            include_system_knowledge=validated.include_system_knowledge,
            usage=usage,
            selected_documents=selected_documents,
        ),
    )


def knowledge_source_scope_sha256(
    *,
    include_system_knowledge: bool,
    usage: KnowledgeScopeUsage,
    selected_documents: tuple[SelectedUserDocumentRevision, ...],
) -> str:
    payload = {
        "schema_version": KNOWLEDGE_SOURCE_SCOPE_SCHEMA_VERSION,
        "include_system_knowledge": include_system_knowledge,
        "usage": usage,
        "selected_documents": [
            item.model_dump(mode="json") for item in selected_documents
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def knowledge_scope_selection_payload(
    *,
    include_system_knowledge: bool,
    selected_documents: tuple[SelectedUserDocumentRevision, ...],
) -> dict[str, object]:
    return {
        "schema_version": INTERVIEW_KNOWLEDGE_SCOPE_SCHEMA_VERSION,
        "include_system_knowledge": include_system_knowledge,
        "selected_documents": [
            item.model_dump(mode="json") for item in selected_documents
        ],
    }
