from __future__ import annotations

import json
import os
from typing import Any, Protocol

from pydantic import BaseModel

from app.services.config import get_pgvector_table, get_postgres_dsn


class KnowledgeSearchStore(Protocol):
    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list["KnowledgeChunk" | dict]:
        """Search role-relevant knowledge chunks for evaluation."""


class KnowledgeChunk(BaseModel):
    chunk_id: str
    title: str
    content: str
    source_type: str
    domain: str
    tags: list[str]
    metadata: dict[str, str | int | float | bool | None]
    score: float | None = None


class PgVectorKnowledgeStore:
    def __init__(
        self,
        *,
        dsn: str,
        table_name: str,
        embedding_model_name: str,
        embedding_dimension: int,
        embedding_model=None,
    ) -> None:
        self.dsn = dsn
        self.table_name = table_name
        self.embedding_model_name = embedding_model_name
        self.embedding_dimension = embedding_dimension
        self._embedding_model = embedding_model

    @classmethod
    def from_env(cls) -> "PgVectorKnowledgeStore":
        return cls(
            dsn=get_postgres_dsn(),
            table_name=get_pgvector_table(),
            embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3"),
            embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
        )

    def upsert_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        if not chunks:
            return

        psycopg2, sql = self._import_psycopg2()
        statement = sql.SQL(
            """
            INSERT INTO {table} (
                chunk_id,
                title,
                content,
                source_type,
                domain,
                tags,
                metadata,
                embedding
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::vector)
            ON CONFLICT (chunk_id) DO UPDATE
            SET
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                source_type = EXCLUDED.source_type,
                domain = EXCLUDED.domain,
                tags = EXCLUDED.tags,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding,
                updated_at = NOW()
            """
        ).format(table=sql.Identifier(self.table_name))

        rows = []
        for chunk in chunks:
            embedding = self.embed_text(self._chunk_embedding_text(chunk))
            rows.append(
                (
                    chunk.chunk_id,
                    chunk.title,
                    chunk.content,
                    chunk.source_type,
                    chunk.domain,
                    json.dumps(chunk.tags, ensure_ascii=False),
                    json.dumps(chunk.metadata, ensure_ascii=False),
                    self._to_vector_literal(embedding),
                )
            )

        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    for row in rows:
                        cursor.execute(statement, row)
        except Exception as exc:
            raise RuntimeError("pgvector knowledge store is unavailable") from exc

    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        psycopg2, sql = self._import_psycopg2()
        normalized_tags = self._normalize_tags(job_tags)
        normalized_sources = self._normalize_source_types(source_types)
        query_embedding = self.embed_text(query_text)
        vector_literal = self._to_vector_literal(query_embedding)

        clauses: list[Any] = []
        params: list[Any] = []
        if normalized_sources:
            clauses.append(sql.SQL("source_type = ANY(%s)"))
            params.append(normalized_sources)
        if normalized_tags:
            clauses.append(
                sql.SQL(
                    """
                    EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements_text(tags) AS tag(value)
                        WHERE tag.value = ANY(%s)
                    )
                    """
                )
            )
            params.append(normalized_tags)

        where_sql = (
            sql.SQL("WHERE ") + sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("")
        )
        statement = sql.SQL(
            """
            SELECT
                chunk_id,
                title,
                content,
                source_type,
                domain,
                tags,
                metadata,
                1 - (embedding <=> %s::vector) AS score
            FROM {table}
            {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """
        ).format(
            table=sql.Identifier(self.table_name),
            where_sql=where_sql,
        )

        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(statement, [vector_literal, *params, vector_literal, limit])
                    rows = cursor.fetchall()
        except Exception as exc:
            raise RuntimeError("pgvector knowledge store is unavailable") from exc

        return [
            KnowledgeChunk(
                chunk_id=row[0],
                title=row[1],
                content=row[2],
                source_type=row[3],
                domain=row[4],
                tags=self._coerce_json_value(row[5], default=[]),
                metadata=self._coerce_json_value(row[6], default={}),
                score=float(row[7]) if row[7] is not None else None,
            )
            for row in rows
        ]

    def embed_text(self, text: str) -> list[float]:
        model = self._get_embedding_model()
        payload = text.strip() or "general knowledge"
        vector = model.encode(payload, normalize_embeddings=True)
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        normalized = [float(value) for value in vector]
        if len(normalized) != self.embedding_dimension:
            raise RuntimeError(
                f"embedding dimension mismatch: expected {self.embedding_dimension}, got {len(normalized)}"
            )
        return normalized

    def ensure_schema(self) -> None:
        psycopg2, _ = self._import_psycopg2()
        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
        except Exception as exc:
            raise RuntimeError('pgvector knowledge store is unavailable') from exc

    def count_chunks(self) -> int:
        psycopg2, sql = self._import_psycopg2()
        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL('SELECT COUNT(*) FROM {table}').format(
                            table=sql.Identifier(self.table_name)
                        )
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            raise RuntimeError('pgvector knowledge store is unavailable') from exc
        return int(row[0]) if row is not None else 0

    def _get_embedding_model(self):
        if self._embedding_model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("sentence-transformers is required") from exc
            self._embedding_model = SentenceTransformer(self.embedding_model_name)
        return self._embedding_model

    def _ensure_schema(self, connection) -> None:
        _, sql = self._import_psycopg2()
        dimension_sql = sql.SQL(str(int(self.embedding_dimension)))
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {table} (
                        chunk_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding VECTOR({dimension}) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ).format(
                    table=sql.Identifier(self.table_name),
                    dimension=dimension_sql,
                )
            )
            cursor.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {index_name} ON {table} USING GIN (tags)"
                ).format(
                    index_name=sql.Identifier(f"{self.table_name}_tags_gin"),
                    table=sql.Identifier(self.table_name),
                )
            )
            cursor.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {index_name} ON {table} (source_type)"
                ).format(
                    index_name=sql.Identifier(f"{self.table_name}_source_type_idx"),
                    table=sql.Identifier(self.table_name),
                )
            )

    def _chunk_embedding_text(self, chunk: KnowledgeChunk) -> str:
        return f"{chunk.title}\n{chunk.content}"

    @staticmethod
    def _to_vector_literal(values: list[float]) -> str:
        return "[" + ",".join(f"{value:.8f}" for value in values) + "]"

    @staticmethod
    def _coerce_json_value(value: Any, *, default):
        if value is None:
            return default
        if isinstance(value, (list, dict)):
            return value
        if isinstance(value, str):
            return json.loads(value)
        return default

    @staticmethod
    def _normalize_tags(job_tags: list[str]) -> list[str]:
        tags = [tag.strip().lower() for tag in job_tags if tag and tag.strip()]
        if "general" not in tags:
            tags.append("general")
        deduped: list[str] = []
        for tag in tags:
            if tag not in deduped:
                deduped.append(tag)
        return deduped

    @staticmethod
    def _normalize_source_types(source_types: list[str] | None) -> list[str] | None:
        if not source_types:
            return None
        deduped: list[str] = []
        for source_type in source_types:
            normalized = source_type.strip()
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped or None

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required") from exc
        return psycopg2, sql


_knowledge_store: PgVectorKnowledgeStore | None = None


def get_knowledge_store() -> PgVectorKnowledgeStore:
    global _knowledge_store
    if _knowledge_store is None:
        _knowledge_store = PgVectorKnowledgeStore.from_env()
    return _knowledge_store
