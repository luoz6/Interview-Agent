import json
from typing import Any

from app.domain.knowledge.models import KnowledgeChunk


class PgVectorCodec:
    """Encode vectors and decode PostgreSQL rows at the PGVector boundary."""

    @staticmethod
    def vector_literal(values: list[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in values) + "]"

    @staticmethod
    def json_value(value: Any, *, default):
        if value is None:
            return default
        if isinstance(value, (list, dict)):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return default

    def row_to_chunk(self, row) -> KnowledgeChunk:
        return KnowledgeChunk(
            chunk_id=row[0],
            title=row[1],
            content=row[2],
            source_type=row[3],
            domain=row[4],
            tags=self.json_value(row[5], default=[]),
            metadata=self.json_value(row[6], default={}),
            score=float(row[7]) if row[7] is not None else None,
        )
