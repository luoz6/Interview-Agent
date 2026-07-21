from __future__ import annotations

import hashlib
import json
import os
from time import perf_counter
from typing import Any

from pydantic import BaseModel

from app.ports.runtime import KnowledgeLookupResult, KnowledgeRepository
from app.services.config import (
    derive_pgvector_table_names,
    get_embedding_settings,
    get_pgvector_table,
    get_postgres_dsn,
)
from app.services.embedding_providers import (
    build_embedding_provider,
    validate_embedding_batch,
)


KnowledgeSearchStore = KnowledgeRepository
DEFAULT_KNOWLEDGE_MIN_SCORE = 0.45


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
        embedding_provider,
        minimum_score: float = DEFAULT_KNOWLEDGE_MIN_SCORE,
    ) -> None:
        self.dsn = dsn
        self.legacy_table = table_name
        self.table_name = table_name
        self.versions_table, self.releases_table = derive_pgvector_table_names(table_name)
        self.embedding_provider = embedding_provider
        self.embedding_dimension = embedding_provider.dimension
        self.minimum_score = float(minimum_score)
        self.last_search_trace: dict[str, Any] | None = None
        self.last_lookup_trace: dict[str, Any] | None = None

    @classmethod
    def from_env(cls) -> "PgVectorKnowledgeStore":
        settings = get_embedding_settings()
        return cls(
            dsn=get_postgres_dsn(),
            table_name=get_pgvector_table(),
            embedding_provider=build_embedding_provider(settings),
            minimum_score=float(
                os.getenv("KNOWLEDGE_MIN_SCORE", str(DEFAULT_KNOWLEDGE_MIN_SCORE))
            ),
        )

    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        started_at = perf_counter()
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
            ORDER BY embedding <=> %s::vector, chunk_id ASC
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

        results: list[KnowledgeChunk] = []
        seen_ids: set[str] = set()
        for row in rows:
            chunk = self._row_to_chunk(row)
            if chunk.chunk_id in seen_ids:
                continue
            if chunk.score is None or chunk.score < self.minimum_score:
                continue
            seen_ids.add(chunk.chunk_id)
            results.append(chunk)
        results.sort(key=lambda chunk: (-float(chunk.score or 0.0), chunk.chunk_id))
        self.last_search_trace = {
            "latency_ms": round((perf_counter() - started_at) * 1000, 3),
            "filters": {
                "job_tags": normalized_tags,
                "source_types": normalized_sources or [],
                "minimum_score": self.minimum_score,
                "limit": limit,
            },
            "hit_ids": [chunk.chunk_id for chunk in results],
        }
        return results

    def get_by_ids(
        self,
        ids: list[str],
        *,
        expected_hashes: dict[str, str] | None = None,
    ) -> KnowledgeLookupResult:
        requested = self._normalize_ids(ids)
        if not requested:
            result = KnowledgeLookupResult()
            self.last_lookup_trace = {
                "latency_ms": 0.0,
                "requested_ids": [],
                "found_ids": [],
                "missing_ids": [],
                "version_mismatch_ids": [],
            }
            return result

        started_at = perf_counter()
        psycopg2, sql = self._import_psycopg2()
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
                NULL::DOUBLE PRECISION AS score
            FROM {table}
            WHERE chunk_id = ANY(%s)
            """
        ).format(table=sql.Identifier(self.table_name))

        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(statement, (requested,))
                    rows = cursor.fetchall()
        except Exception as exc:
            raise RuntimeError("pgvector knowledge store is unavailable") from exc

        by_id = {row[0]: self._row_to_chunk(row) for row in rows}
        expected = expected_hashes or {}
        result = KnowledgeLookupResult()
        for chunk_id in requested:
            chunk = by_id.get(chunk_id)
            if chunk is None:
                result.missing.append(chunk_id)
                continue
            expected_hash = expected.get(chunk_id)
            actual_hash = chunk.metadata.get("content_sha256")
            if expected_hash is not None and actual_hash != expected_hash:
                result.version_mismatch.append(chunk_id)
                continue
            result.found.append(chunk)

        self.last_lookup_trace = {
            "latency_ms": round((perf_counter() - started_at) * 1000, 3),
            "requested_ids": requested,
            "found_ids": [chunk.chunk_id for chunk in result.found],
            "missing_ids": result.missing,
            "version_mismatch_ids": result.version_mismatch,
        }
        return result

    def embed_text(self, text: str) -> list[float]:
        payload = text.strip() or "general knowledge"
        return validate_embedding_batch(
            [self.embedding_provider.embed_query(payload)],
            expected_count=1,
            dimension=self.embedding_dimension,
        )[0]

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
                        sql.SQL(
                            """
                            SELECT COUNT(v.chunk_id)
                            FROM {releases} AS r
                            LEFT JOIN {versions} AS v
                              ON v.corpus_version = r.corpus_version
                            WHERE r.status = 'active'
                            """
                        ).format(
                            releases=sql.Identifier(self.releases_table),
                            versions=sql.Identifier(self.versions_table),
                        )
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            raise RuntimeError('pgvector knowledge store is unavailable') from exc
        return int(row[0]) if row is not None else 0

    def _ensure_schema(self, connection) -> None:
        _, sql = self._import_psycopg2()
        dimension_sql = sql.SQL(str(int(self.embedding_dimension)))
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {releases} (
                        corpus_version TEXT PRIMARY KEY,
                        manifest_sha256 TEXT NOT NULL,
                        embedding_provider TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        embedding_revision TEXT NOT NULL,
                        embedding_dimension INTEGER NOT NULL
                            CHECK (embedding_dimension > 0),
                        chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
                        status TEXT NOT NULL
                            CHECK (status IN ('staged', 'active', 'retired')),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        activated_at TIMESTAMPTZ
                    )
                    """
                ).format(releases=sql.Identifier(self.releases_table))
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS {index_name}
                    ON {releases} ((1)) WHERE status = 'active'
                    """
                ).format(
                    index_name=sql.Identifier(self._index_name("one_active")),
                    releases=sql.Identifier(self.releases_table),
                )
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {versions} (
                        corpus_version TEXT NOT NULL
                            REFERENCES {releases}(corpus_version) ON DELETE RESTRICT,
                        chunk_id TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        embedding_provider TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        embedding_revision TEXT NOT NULL,
                        embedding_dimension INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding VECTOR({dimension}) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (corpus_version, chunk_id)
                    )
                    """
                ).format(
                    versions=sql.Identifier(self.versions_table),
                    releases=sql.Identifier(self.releases_table),
                    dimension=dimension_sql,
                )
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON {versions} (corpus_version, source_type)
                    """
                ).format(
                    index_name=sql.Identifier(self._index_name("source")),
                    versions=sql.Identifier(self.versions_table),
                )
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON {versions} USING GIN (tags)
                    """
                ).format(
                    index_name=sql.Identifier(self._index_name("tags")),
                    versions=sql.Identifier(self.versions_table),
                )
            )
            cursor.execute(
                sql.SQL(
                    """
                    CREATE INDEX IF NOT EXISTS {index_name}
                    ON {versions} (
                        content_sha256,
                        embedding_provider,
                        embedding_model,
                        embedding_revision,
                        embedding_dimension
                    )
                    """
                ).format(
                    index_name=sql.Identifier(self._index_name("reuse")),
                    versions=sql.Identifier(self.versions_table),
                )
            )

    def migrate_legacy_rows(self) -> int:
        psycopg2, sql = self._import_psycopg2()
        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT to_regclass(%s)", (f"public.{self.legacy_table}",))
                    if cursor.fetchone()[0] is None:
                        return 0
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT chunk_id, title, content, source_type, domain,
                                   tags, metadata, embedding::text
                            FROM {legacy}
                            ORDER BY chunk_id
                            """
                        ).format(legacy=sql.Identifier(self.legacy_table))
                    )
                    source_rows = cursor.fetchall()
                    if not source_rows:
                        return 0

                    prepared = self._prepare_legacy_rows(source_rows)
                    manifest_sha256 = self._legacy_manifest_sha256(prepared)
                    identity = (
                        manifest_sha256,
                        "legacy-unknown",
                        "legacy-unknown",
                        "legacy-stage42-v1",
                        self.embedding_dimension,
                        len(prepared),
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT manifest_sha256, embedding_provider,
                                   embedding_model, embedding_revision,
                                   embedding_dimension, chunk_count
                            FROM {releases}
                            WHERE corpus_version = %s
                            """
                        ).format(releases=sql.Identifier(self.releases_table)),
                        ("legacy-stage42-v1",),
                    )
                    existing = cursor.fetchone()
                    if existing is not None and tuple(existing) != identity:
                        raise ValueError("legacy corpus identity conflict")
                    if existing is None:
                        cursor.execute(
                            sql.SQL(
                                """
                                INSERT INTO {releases} (
                                    corpus_version, manifest_sha256,
                                    embedding_provider, embedding_model,
                                    embedding_revision, embedding_dimension,
                                    chunk_count, status
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, 'retired')
                                """
                            ).format(releases=sql.Identifier(self.releases_table)),
                            ("legacy-stage42-v1", *identity),
                        )

                    insert_statement = sql.SQL(
                        """
                        INSERT INTO {versions} (
                            corpus_version, chunk_id, content_sha256,
                            embedding_provider, embedding_model,
                            embedding_revision, embedding_dimension,
                            title, content, source_type, domain, tags,
                            metadata, embedding
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::vector
                        )
                        ON CONFLICT (corpus_version, chunk_id) DO NOTHING
                        """
                    ).format(versions=sql.Identifier(self.versions_table))
                    for row in prepared:
                        cursor.execute(insert_statement, row["insert_values"])

                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT chunk_id, content_sha256,
                                   embedding_provider, embedding_model,
                                   embedding_revision, embedding_dimension
                            FROM {versions}
                            WHERE corpus_version = %s
                            ORDER BY chunk_id
                            """
                        ).format(versions=sql.Identifier(self.versions_table)),
                        ("legacy-stage42-v1",),
                    )
                    persisted = cursor.fetchall()
                    expected = [row["verification"] for row in prepared]
                    if [tuple(row) for row in persisted] != expected:
                        raise ValueError("legacy corpus identity conflict")
                    return len(prepared)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError("pgvector knowledge store is unavailable") from exc

    def find_reusable_embeddings(
        self,
        chunks: list[KnowledgeChunk],
        *,
        provider_name: str,
        model_name: str,
        model_revision: str,
        dimension: int,
    ) -> dict[str, list[float]]:
        if not chunks:
            return {}
        requested_hashes = {
            chunk.chunk_id: chunk.metadata.get("content_sha256") for chunk in chunks
        }
        psycopg2, sql = self._import_psycopg2()
        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT chunk_id, content_sha256, embedding::text
                            FROM {versions}
                            WHERE chunk_id = ANY(%s)
                              AND embedding_provider = %s
                              AND embedding_model = %s
                              AND embedding_revision = %s
                              AND embedding_dimension = %s
                            ORDER BY chunk_id, created_at DESC
                            """
                        ).format(versions=sql.Identifier(self.versions_table)),
                        (
                            list(requested_hashes),
                            provider_name,
                            model_name,
                            model_revision,
                            int(dimension),
                        ),
                    )
                    rows = cursor.fetchall()
        except Exception as exc:
            raise RuntimeError("pgvector knowledge store is unavailable") from exc

        reusable: dict[str, list[float]] = {}
        for chunk_id, content_sha256, vector_text in rows:
            if chunk_id in reusable:
                continue
            if requested_hashes.get(chunk_id) != content_sha256:
                continue
            try:
                vector = validate_embedding_batch(
                    [json.loads(vector_text)],
                    expected_count=1,
                    dimension=int(dimension),
                )[0]
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError("pgvector knowledge store is unavailable") from exc
            reusable[chunk_id] = vector
        return reusable

    def activate_corpus(
        self,
        *,
        corpus_version: str,
        manifest_sha256: str,
        provider,
        chunks,
    ) -> None:
        prepared = list(chunks)
        chunk_ids = [item.chunk.chunk_id for item in prepared]
        if not prepared or len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("corpus version identity conflict")
        vectors = validate_embedding_batch(
            [item.embedding for item in prepared],
            expected_count=len(prepared),
            dimension=provider.dimension,
        )
        rows = []
        for item, vector in zip(prepared, vectors, strict=True):
            chunk = item.chunk
            metadata = {**chunk.metadata, "content_sha256": item.content_sha256}
            rows.append(
                (
                    corpus_version,
                    chunk.chunk_id,
                    item.content_sha256,
                    provider.provider_name,
                    provider.model_name,
                    provider.model_revision,
                    provider.dimension,
                    chunk.title,
                    chunk.content,
                    chunk.source_type,
                    chunk.domain,
                    json.dumps(chunk.tags, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    self._to_vector_literal(vector),
                )
            )

        release_identity = (
            manifest_sha256,
            provider.provider_name,
            provider.model_name,
            provider.model_revision,
            int(provider.dimension),
            len(rows),
        )
        psycopg2, sql = self._import_psycopg2()
        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT manifest_sha256, embedding_provider,
                                   embedding_model, embedding_revision,
                                   embedding_dimension, chunk_count, status
                            FROM {releases}
                            WHERE corpus_version = %s
                            """
                        ).format(releases=sql.Identifier(self.releases_table)),
                        (corpus_version,),
                    )
                    existing = cursor.fetchone()
                    existing_status = None
                    if existing is not None:
                        existing_status = existing[6]
                        if tuple(existing[:6]) != release_identity:
                            raise ValueError("corpus version identity conflict")
                    else:
                        cursor.execute(
                            sql.SQL(
                                """
                                INSERT INTO {releases} (
                                    corpus_version, manifest_sha256,
                                    embedding_provider, embedding_model,
                                    embedding_revision, embedding_dimension,
                                    chunk_count, status
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, %s, 'staged')
                                """
                            ).format(releases=sql.Identifier(self.releases_table)),
                            (corpus_version, *release_identity),
                        )

                    insert_statement = sql.SQL(
                        """
                        INSERT INTO {versions} (
                            corpus_version, chunk_id, content_sha256,
                            embedding_provider, embedding_model,
                            embedding_revision, embedding_dimension,
                            title, content, source_type, domain, tags,
                            metadata, embedding
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::vector
                        )
                        ON CONFLICT (corpus_version, chunk_id) DO NOTHING
                        """
                    ).format(versions=sql.Identifier(self.versions_table))
                    for row in rows:
                        cursor.execute(insert_statement, row)

                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT chunk_id, content_sha256,
                                   embedding_provider, embedding_model,
                                   embedding_revision, embedding_dimension
                            FROM {versions}
                            WHERE corpus_version = %s
                            ORDER BY chunk_id
                            """
                        ).format(versions=sql.Identifier(self.versions_table)),
                        (corpus_version,),
                    )
                    persisted = [tuple(row) for row in cursor.fetchall()]
                    expected = sorted(
                        (
                            item.chunk.chunk_id,
                            item.content_sha256,
                            provider.provider_name,
                            provider.model_name,
                            provider.model_revision,
                            int(provider.dimension),
                        )
                        for item in prepared
                    )
                    if persisted != expected:
                        raise ValueError("corpus version identity conflict")
                    if existing_status == "active":
                        return

                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {releases}
                            SET status = 'retired'
                            WHERE status = 'active' AND corpus_version <> %s
                            """
                        ).format(releases=sql.Identifier(self.releases_table)),
                        (corpus_version,),
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {releases}
                            SET status = 'active', activated_at = NOW()
                            WHERE corpus_version = %s
                            """
                        ).format(releases=sql.Identifier(self.releases_table)),
                        (corpus_version,),
                    )
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError("pgvector knowledge store is unavailable") from exc

    def get_active_corpus_version(self) -> str | None:
        psycopg2, sql = self._import_psycopg2()
        try:
            with psycopg2.connect(self.dsn) as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT corpus_version
                            FROM {releases}
                            WHERE status = 'active'
                            """
                        ).format(releases=sql.Identifier(self.releases_table))
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            raise RuntimeError("pgvector knowledge store is unavailable") from exc
        return str(row[0]) if row is not None else None

    def _prepare_legacy_rows(self, source_rows) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for row in source_rows:
            tags = self._coerce_json_value(row[5], default=[])
            metadata = self._coerce_json_value(row[6], default={})
            content = str(row[2])
            source_content_sha256 = self._content_sha256(content)
            mapped_hash = metadata.get("content_sha256")
            content_sha256 = (
                mapped_hash.strip()
                if isinstance(mapped_hash, str) and mapped_hash.strip()
                else source_content_sha256
            )
            metadata = {**metadata, "content_sha256": content_sha256}
            vector = [float(value) for value in json.loads(row[7])]
            if len(vector) != self.embedding_dimension:
                raise ValueError("legacy corpus identity conflict")
            chunk_id = str(row[0])
            prepared.append(
                {
                    "source_identity": {
                        "chunk_id": chunk_id,
                        "source_content_sha256": source_content_sha256,
                        "content_sha256": content_sha256,
                        "embedding_dimension": len(vector),
                    },
                    "insert_values": (
                        "legacy-stage42-v1",
                        chunk_id,
                        content_sha256,
                        "legacy-unknown",
                        "legacy-unknown",
                        "legacy-stage42-v1",
                        self.embedding_dimension,
                        row[1],
                        content,
                        row[3],
                        row[4],
                        json.dumps(tags, ensure_ascii=False),
                        json.dumps(metadata, ensure_ascii=False),
                        self._to_vector_literal(vector),
                    ),
                    "verification": (
                        chunk_id,
                        content_sha256,
                        "legacy-unknown",
                        "legacy-unknown",
                        "legacy-stage42-v1",
                        self.embedding_dimension,
                    ),
                }
            )
        return prepared

    @staticmethod
    def _legacy_manifest_sha256(prepared: list[dict[str, Any]]) -> str:
        payload = [row["source_identity"] for row in prepared]
        serialized = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _content_sha256(content: str) -> str:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _index_name(self, purpose: str) -> str:
        digest = hashlib.sha256(
            f"{self.versions_table}:{purpose}".encode("ascii")
        ).hexdigest()[:12]
        return f"knowledge_{purpose}_{digest}"

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

    def _row_to_chunk(self, row) -> KnowledgeChunk:
        return KnowledgeChunk(
            chunk_id=row[0],
            title=row[1],
            content=row[2],
            source_type=row[3],
            domain=row[4],
            tags=self._coerce_json_value(row[5], default=[]),
            metadata=self._coerce_json_value(row[6], default={}),
            score=float(row[7]) if row[7] is not None else None,
        )

    @staticmethod
    def _normalize_ids(ids: list[str]) -> list[str]:
        normalized: list[str] = []
        for chunk_id in ids:
            value = str(chunk_id).strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

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
