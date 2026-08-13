from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.domain.knowledge.knowledge_unit import KnowledgeUnit


class MetadataKnowledgeUnitResolver:
    """Loads a pilot unit from metadata of evidence already bound to a question."""

    def resolve(self, references: list[Any]) -> KnowledgeUnit | None:
        units: list[KnowledgeUnit] = []
        for reference in references:
            metadata = _value(reference, "metadata") or {}
            raw = metadata.get("knowledge_unit") if isinstance(metadata, dict) else None
            if not isinstance(raw, dict):
                continue
            try:
                units.append(KnowledgeUnit.model_validate(raw))
            except ValidationError:
                return None
        if not units:
            return None
        first = units[0]
        return first if all(unit == first for unit in units[1:]) else None


def _value(item: Any, key: str):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)
