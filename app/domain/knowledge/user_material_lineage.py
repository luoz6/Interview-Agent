from __future__ import annotations

import re
from uuid import UUID

from app.domain.knowledge.models import KnowledgeChunk


SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
USER_MATERIAL_FORBIDDEN_METADATA = frozenset(
    {
        "owner",
        "owner_principal_id",
        "original_filename",
        "original_path",
        "path",
    }
)


def declares_user_material(chunk: KnowledgeChunk) -> bool:
    return (
        chunk.source_type == "user_material"
        or chunk.metadata.get("knowledge_source") == "user_material"
    )


def has_valid_user_material_lineage(chunk: KnowledgeChunk) -> bool:
    metadata = chunk.metadata
    if (
        chunk.source_type != "user_material"
        or metadata.get("knowledge_source") != "user_material"
        or USER_MATERIAL_FORBIDDEN_METADATA.intersection(metadata)
    ):
        return False
    if not _valid_sha256(metadata.get("content_sha256")):
        return False
    if not _valid_sha256(metadata.get("document_content_sha256")):
        return False
    try:
        UUID(str(metadata.get("document_id")))
        UUID(str(metadata.get("document_revision_id")))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


__all__ = [
    "SHA256_PATTERN",
    "declares_user_material",
    "has_valid_user_material_lineage",
]
