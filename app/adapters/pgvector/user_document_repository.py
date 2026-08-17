from __future__ import annotations

import json
import math
import re
from uuid import UUID

from app.adapters.pgvector.codec import PgVectorCodec
from app.adapters.postgres.user_materials_schema import (
    user_materials_relation_names,
    validate_user_materials_schema,
)
from app.domain.knowledge.user_document import UserDocumentChunk
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode


class PgVectorUserDocumentChunkRepository:
    """Owner-scoped pgvector persistence for User Document chunks."""

    def __init__(
        self,
        *,
        embedding_dimension: int,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if (
            isinstance(embedding_dimension, bool)
            or not isinstance(embedding_dimension, int)
            or embedding_dimension < 1
        ):
            raise ValueError("embedding_dimension must be a positive integer")
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            provider_is_owned = True
        else:
            provider_is_owned = False
        self.dsn = dsn or ""
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        documents, revisions, chunks = user_materials_relation_names(
            table_prefix
        )
        self.documents_table = documents
        self.revisions_table = revisions
        self.chunks_table = chunks
        self.embedding_dimension = embedding_dimension
        self.codec = PgVectorCodec()
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=provider_is_owned,
        )
        if self.schema_mode == "migrate":
            raise ValueError(
                "Materials schema migration is operator-owned; "
                "use schema_mode='validate'"
            )
        validate_user_materials_schema(
            self._connection_provider,
            table_prefix=table_prefix,
        )

    def replace_revision_chunks(
        self,
        *,
        owner_principal_id: str,
        document_id: str,
        document_revision_id: str,
        chunks: tuple[UserDocumentChunk, ...],
    ) -> int:
        owner = _require_owner(owner_principal_id)
        document_id = _require_uuid(document_id, "document_id")
        revision_id = _require_uuid(
            document_revision_id,
            "document_revision_id",
        )
        chunks = tuple(
            UserDocumentChunk.model_validate(chunk.model_dump(mode="python"))
            for chunk in chunks
        )
        for chunk in chunks:
            if (
                chunk.owner_principal_id != owner
                or chunk.document_id != document_id
                or chunk.document_revision_id != revision_id
            ):
                raise ValueError("chunk scope does not match repository scope")
            self._validate_vector(chunk.embedding)
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("chunk IDs must be unique")
        if [chunk.position for chunk in chunks] != list(
            range(1, len(chunks) + 1)
        ):
            raise ValueError("chunk positions must be contiguous")

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:lock-chunk-revision */
                        SELECT document_revision_id::text
                        FROM {revisions}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                          AND document_revision_id=%s::uuid
                        FOR UPDATE
                        """
                    ),
                    (owner, document_id, revision_id),
                )
                if cursor.fetchone() is None:
                    raise ValueError("document revision not found")

                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:replace-delete-chunks */
                        DELETE FROM {chunks}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                          AND document_revision_id=%s::uuid
                        """
                    ),
                    (owner, document_id, revision_id),
                )
                if chunks:
                    cursor.executemany(
                        self._sql(
                            """
                            /* user-materials:replace-insert-chunks */
                            INSERT INTO {chunks} (
                                owner_principal_id, chunk_id, document_id,
                                document_revision_id, position, title,
                                section_label, content, content_sha256,
                                embedding, embedding_identity, created_at
                            ) VALUES (
                                %s, %s::uuid, %s::uuid, %s::uuid, %s,
                                %s, %s, %s, %s, %s::vector, %s, %s
                            )
                            """
                        ),
                        [self._chunk_values(chunk) for chunk in chunks],
                    )
        return len(chunks)

    def list_revision_chunks(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> tuple[UserDocumentChunk, ...]:
        owner = _require_owner(owner_principal_id)
        revision_id = _require_uuid(
            document_revision_id,
            "document_revision_id",
        )
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:list-revision-chunks */
                        SELECT {chunk_columns}
                        FROM {chunks}
                        WHERE owner_principal_id=%s
                          AND document_revision_id=%s::uuid
                        ORDER BY position ASC, chunk_id ASC
                        """
                    ),
                    (owner, revision_id),
                )
                rows = cursor.fetchall()
        return tuple(self._chunk_from_row(row) for row in rows)

    def search_semantic(
        self,
        *,
        owner_principal_id: str,
        allowed_document_revision_ids: tuple[str, ...],
        query_embedding: tuple[float, ...],
        limit: int,
    ) -> tuple[UserDocumentChunk, ...]:
        owner = _require_search(owner_principal_id, limit)
        allowed = _normalize_revision_ids(allowed_document_revision_ids)
        if not allowed:
            return ()
        self._validate_vector(query_embedding)
        vector = self.codec.vector_literal(list(query_embedding))
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:search-semantic-chunks */
                        SELECT {chunk_columns}
                        FROM {chunks}
                        WHERE owner_principal_id=%s
                          AND document_revision_id=ANY(%s::uuid[])
                        ORDER BY embedding <=> %s::vector ASC,
                                 chunk_id ASC
                        LIMIT %s
                        """
                    ),
                    (owner, list(allowed), vector, limit),
                )
                rows = cursor.fetchall()
        return tuple(self._chunk_from_row(row) for row in rows)

    def search_lexical(
        self,
        *,
        owner_principal_id: str,
        allowed_document_revision_ids: tuple[str, ...],
        query_text: str,
        limit: int,
    ) -> tuple[UserDocumentChunk, ...]:
        owner = _require_search(owner_principal_id, limit)
        if not isinstance(query_text, str):
            raise ValueError("query_text must be a string")
        query = query_text.strip()
        allowed = _normalize_revision_ids(allowed_document_revision_ids)
        if not query or not allowed:
            return ()
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:search-lexical-chunks */
                        SELECT {chunk_columns}
                        FROM {chunks}
                        WHERE owner_principal_id=%s
                          AND document_revision_id=ANY(%s::uuid[])
                          AND lexical_document @@
                              websearch_to_tsquery('simple', %s)
                        ORDER BY ts_rank_cd(
                                     lexical_document,
                                     websearch_to_tsquery('simple', %s)
                                 ) DESC,
                                 chunk_id ASC
                        LIMIT %s
                        """
                    ),
                    (owner, list(allowed), query, query, limit),
                )
                rows = cursor.fetchall()
        return tuple(self._chunk_from_row(row) for row in rows)

    def delete_by_revision(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> int:
        owner = _require_owner(owner_principal_id)
        revision_id = _require_uuid(
            document_revision_id,
            "document_revision_id",
        )
        return self._execute_delete(
            self._sql(
                """
                /* user-materials:delete-revision-chunks */
                DELETE FROM {chunks}
                WHERE owner_principal_id=%s
                  AND document_revision_id=%s::uuid
                """
            ),
            (owner, revision_id),
        )

    def delete_by_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> int:
        owner = _require_owner(owner_principal_id)
        document_id = _require_uuid(document_id, "document_id")
        return self._execute_delete(
            self._sql(
                """
                /* user-materials:delete-document-chunks */
                DELETE FROM {chunks}
                WHERE owner_principal_id=%s
                  AND document_id=%s::uuid
                """
            ),
            (owner, document_id),
        )

    def _execute_delete(self, statement, params: tuple[object, ...]) -> int:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, params)
                deleted = cursor.rowcount
        return int(deleted)

    def _validate_vector(self, vector: tuple[float, ...]) -> None:
        if not isinstance(vector, (list, tuple)) or len(vector) != (
            self.embedding_dimension
        ):
            raise ValueError("embedding dimension is incompatible")
        try:
            finite = all(math.isfinite(float(value)) for value in vector)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("embedding dimension is incompatible") from exc
        if not finite:
            raise ValueError("embedding dimension is incompatible")

    def _chunk_from_row(self, row) -> UserDocumentChunk:
        vector = _parse_vector(row[9])
        self._validate_vector(vector)
        return UserDocumentChunk(
            chunk_id=str(row[0]),
            owner_principal_id=str(row[1]),
            document_id=str(row[2]),
            document_revision_id=str(row[3]),
            position=row[4],
            title=row[5],
            section_label=row[6],
            content=row[7],
            content_sha256=row[8],
            embedding=vector,
            embedding_identity=row[10],
            created_at=row[11],
        )

    def _chunk_values(self, chunk: UserDocumentChunk) -> tuple[object, ...]:
        return (
            chunk.owner_principal_id,
            chunk.chunk_id,
            chunk.document_id,
            chunk.document_revision_id,
            chunk.position,
            chunk.title,
            chunk.section_label,
            chunk.content,
            chunk.content_sha256,
            self.codec.vector_literal(list(chunk.embedding)),
            chunk.embedding_identity,
            chunk.created_at,
        )

    @staticmethod
    def _chunk_columns() -> str:
        return (
            "chunk_id::text,owner_principal_id,document_id::text,"
            "document_revision_id::text,position,title,section_label,content,"
            "content_sha256,embedding::text,embedding_identity,created_at"
        )

    def _sql(self, statement: str):
        from psycopg2 import sql

        return sql.SQL(statement).format(
            revisions=sql.Identifier(self.revisions_table),
            chunks=sql.Identifier(self.chunks_table),
            chunk_columns=sql.SQL(self._chunk_columns()),
        )


def _require_owner(owner_principal_id: str) -> str:
    if not isinstance(owner_principal_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,128}",
        owner_principal_id,
    ):
        raise ValueError("owner_principal_id is required")
    return owner_principal_id


def _require_search(owner_principal_id: str, limit: int) -> str:
    owner = _require_owner(owner_principal_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("search limit must be a positive integer")
    return owner


def _require_uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be an opaque UUID") from exc


def _normalize_revision_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("allowed_document_revision_ids must be a sequence")
    return tuple(
        dict.fromkeys(
            _require_uuid(value, "document_revision_id") for value in values
        )
    )


def _parse_vector(value: object) -> tuple[float, ...]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("stored embedding is invalid") from exc
    elif isinstance(value, (list, tuple)):
        decoded = value
    else:
        raise ValueError("stored embedding is invalid")
    if not isinstance(decoded, (list, tuple)) or not decoded:
        raise ValueError("stored embedding is invalid")
    vector = tuple(float(item) for item in decoded)
    if not all(math.isfinite(item) for item in vector):
        raise ValueError("stored embedding is invalid")
    return vector


__all__ = ["PgVectorUserDocumentChunkRepository"]
