from __future__ import annotations

import json
import re
from uuid import UUID

from app.adapters.postgres.user_materials_schema import (
    user_materials_relation_names,
    validate_user_materials_schema,
)
from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentRevision,
)
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode


class PostgresUserDocumentStore:
    """Owner-scoped PostgreSQL implementation of UserDocumentStorePort."""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
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
        documents, revisions, _chunks = user_materials_relation_names(
            table_prefix
        )
        self.documents_table = documents
        self.revisions_table = revisions
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

    def create_document(
        self, *, owner_principal_id: str, document: UserDocument
    ) -> UserDocument:
        owner = _require_owner(owner_principal_id)
        document = _validated_document(document, owner)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:create-document */
                        INSERT INTO {documents} (
                            owner_principal_id, document_id, display_title,
                            original_filename, media_type, size_bytes,
                            public_status, internal_stage, enabled,
                            allowed_usages, active_revision_id,
                            safe_error_code, created_at, updated_at, deleted_at
                        ) VALUES (
                            %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s::uuid, %s, %s, %s, %s
                        )
                        ON CONFLICT (owner_principal_id, document_id)
                        DO NOTHING
                        RETURNING {document_columns}
                        """
                    ),
                    self._document_insert_values(document),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("document already exists")
        return self._document_from_row(row)

    def get_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocument | None:
        owner = _require_owner(owner_principal_id)
        document_id = _require_uuid(document_id, "document_id")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:get-document */
                        SELECT {document_columns}
                        FROM {documents}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        """
                    ),
                    (owner, document_id),
                )
                row = cursor.fetchone()
        return None if row is None else self._document_from_row(row)

    def list_documents(
        self, *, owner_principal_id: str
    ) -> tuple[UserDocument, ...]:
        owner = _require_owner(owner_principal_id)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:list-documents */
                        SELECT {document_columns}
                        FROM {documents}
                        WHERE owner_principal_id=%s
                        ORDER BY created_at DESC, document_id DESC
                        """
                    ),
                    (owner,),
                )
                rows = cursor.fetchall()
        return tuple(self._document_from_row(row) for row in rows)

    def save_document(
        self, *, owner_principal_id: str, document: UserDocument
    ) -> UserDocument | None:
        owner = _require_owner(owner_principal_id)
        document = _validated_document(document, owner)
        values = self._document_update_values(document)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:save-document */
                        UPDATE {documents}
                        SET display_title=%s,
                            original_filename=%s,
                            media_type=%s,
                            size_bytes=%s,
                            public_status=%s,
                            internal_stage=%s,
                            enabled=%s,
                            allowed_usages=%s::jsonb,
                            active_revision_id=%s::uuid,
                            safe_error_code=%s,
                            created_at=%s,
                            updated_at=%s,
                            deleted_at=%s
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        RETURNING {document_columns}
                        """
                    ),
                    (*values, owner, document.document_id),
                )
                row = cursor.fetchone()
        return None if row is None else self._document_from_row(row)

    def create_revision(
        self,
        *,
        owner_principal_id: str,
        revision: UserDocumentRevision,
        original_content: bytes,
        extracted_text: str,
    ) -> UserDocumentRevision:
        owner = _require_owner(owner_principal_id)
        revision = UserDocumentRevision.model_validate(
            revision.model_dump(mode="python")
        )
        if not isinstance(original_content, bytes) or not original_content:
            raise ValueError("revision content must not be empty")
        if not isinstance(extracted_text, str) or not extracted_text:
            raise ValueError("revision content must not be empty")

        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:lock-revision-document */
                        SELECT document_id::text
                        FROM {documents}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        FOR UPDATE
                        """
                    ),
                    (owner, revision.document_id),
                )
                if cursor.fetchone() is None:
                    raise ValueError("document not found")

                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:get-revision-for-create */
                        SELECT {revision_columns},
                               original_content, extracted_text
                        FROM {revisions}
                        WHERE owner_principal_id=%s
                          AND document_revision_id=%s::uuid
                        FOR UPDATE
                        """
                    ),
                    (owner, revision.document_revision_id),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    stored_revision = self._revision_from_row(existing[:10])
                    stored_payload = (
                        bytes(existing[10]),
                        str(existing[11]),
                    )
                    if stored_revision != revision or stored_payload != (
                        original_content,
                        extracted_text,
                    ):
                        raise ValueError("revision identity conflict")
                    return stored_revision

                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:get-latest-revision-number */
                        SELECT COALESCE(MAX(revision), 0)
                        FROM {revisions}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        """
                    ),
                    (owner, revision.document_id),
                )
                latest_row = cursor.fetchone()
                latest_revision = int(latest_row[0]) if latest_row else 0
                if revision.revision != latest_revision + 1:
                    raise ValueError("revision number must be contiguous")

                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:create-revision */
                        INSERT INTO {revisions} (
                            owner_principal_id, document_revision_id,
                            document_id, revision, original_file_sha256,
                            content_sha256, extracted_text_ref,
                            parser_version, chunker_version,
                            embedding_identity, original_content,
                            extracted_text, created_at
                        ) VALUES (
                            %s, %s::uuid, %s::uuid, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s
                        )
                        RETURNING {revision_columns}
                        """
                    ),
                    (
                        owner,
                        revision.document_revision_id,
                        revision.document_id,
                        revision.revision,
                        revision.original_file_sha256,
                        revision.content_sha256,
                        revision.extracted_text_ref,
                        revision.parser_version,
                        revision.chunker_version,
                        revision.embedding_identity,
                        original_content,
                        extracted_text,
                        revision.created_at,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("revision write conflict")
        return self._revision_from_row(row)

    def get_revision(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> UserDocumentRevision | None:
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
                        /* user-materials:get-revision */
                        SELECT {revision_columns}
                        FROM {revisions}
                        WHERE owner_principal_id=%s
                          AND document_revision_id=%s::uuid
                        """
                    ),
                    (owner, revision_id),
                )
                row = cursor.fetchone()
        return None if row is None else self._revision_from_row(row)

    def get_latest_revision(
        self, *, owner_principal_id: str, document_id: str
    ) -> UserDocumentRevision | None:
        owner = _require_owner(owner_principal_id)
        document_id = _require_uuid(document_id, "document_id")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:get-latest-revision */
                        SELECT {revision_columns}
                        FROM {revisions}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        ORDER BY revision DESC
                        LIMIT 1
                        """
                    ),
                    (owner, document_id),
                )
                row = cursor.fetchone()
        return None if row is None else self._revision_from_row(row)

    def list_revisions(
        self, *, owner_principal_id: str, document_id: str
    ) -> tuple[UserDocumentRevision, ...]:
        owner = _require_owner(owner_principal_id)
        document_id = _require_uuid(document_id, "document_id")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:list-revisions */
                        SELECT {revision_columns}
                        FROM {revisions}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        ORDER BY revision ASC
                        """
                    ),
                    (owner, document_id),
                )
                rows = cursor.fetchall()
        return tuple(self._revision_from_row(row) for row in rows)

    def get_revision_content(
        self, *, owner_principal_id: str, document_revision_id: str
    ) -> tuple[bytes, str] | None:
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
                        /* user-materials:get-revision-content */
                        SELECT original_content, extracted_text
                        FROM {revisions}
                        WHERE owner_principal_id=%s
                          AND document_revision_id=%s::uuid
                        """
                    ),
                    (owner, revision_id),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return bytes(row[0]), str(row[1])

    def delete_document(
        self, *, owner_principal_id: str, document_id: str
    ) -> tuple[int, int] | None:
        owner = _require_owner(owner_principal_id)
        document_id = _require_uuid(document_id, "document_id")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:lock-document-for-delete */
                        SELECT document_id::text
                        FROM {documents}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        FOR UPDATE
                        """
                    ),
                    (owner, document_id),
                )
                if cursor.fetchone() is None:
                    return None

                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:count-delete-payloads */
                        SELECT COUNT(*),
                               COUNT(*) FILTER (
                                   WHERE original_content IS NOT NULL
                                     AND extracted_text IS NOT NULL
                               )
                        FROM {revisions}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        """
                    ),
                    (owner, document_id),
                )
                counts = cursor.fetchone() or (0, 0)

                cursor.execute(
                    self._sql(
                        """
                        /* user-materials:delete-document */
                        DELETE FROM {documents}
                        WHERE owner_principal_id=%s
                          AND document_id=%s::uuid
                        RETURNING document_id::text
                        """
                    ),
                    (owner, document_id),
                )
                if cursor.fetchone() is None:
                    raise ValueError("document delete conflict")
        return int(counts[0]), int(counts[1])

    @staticmethod
    def _document_columns() -> str:
        return (
            "document_id::text,owner_principal_id,display_title,"
            "original_filename,media_type,size_bytes,public_status,"
            "internal_stage,enabled,allowed_usages,"
            "active_revision_id::text,safe_error_code,created_at,"
            "updated_at,deleted_at"
        )

    @staticmethod
    def _revision_columns() -> str:
        return (
            "document_revision_id::text,document_id::text,revision,"
            "original_file_sha256,content_sha256,extracted_text_ref,"
            "parser_version,chunker_version,embedding_identity,created_at"
        )

    @staticmethod
    def _document_insert_values(document: UserDocument) -> tuple[object, ...]:
        return (
            document.owner_principal_id,
            document.document_id,
            document.display_title,
            document.original_filename,
            document.media_type,
            document.size_bytes,
            document.public_status.value,
            (
                document.internal_stage.value
                if document.internal_stage is not None
                else None
            ),
            document.enabled,
            json.dumps(list(document.allowed_usages)),
            document.active_revision_id,
            document.safe_error_code,
            document.created_at,
            document.updated_at,
            document.deleted_at,
        )

    @classmethod
    def _document_update_values(
        cls,
        document: UserDocument,
    ) -> tuple[object, ...]:
        return cls._document_insert_values(document)[2:]

    @staticmethod
    def _document_from_row(row) -> UserDocument:
        return UserDocument(
            document_id=str(row[0]),
            owner_principal_id=str(row[1]),
            display_title=row[2],
            original_filename=row[3],
            media_type=row[4],
            size_bytes=row[5],
            public_status=row[6],
            internal_stage=row[7],
            enabled=row[8],
            allowed_usages=_json_array(row[9]),
            active_revision_id=(str(row[10]) if row[10] is not None else None),
            safe_error_code=row[11],
            created_at=row[12],
            updated_at=row[13],
            deleted_at=row[14],
        )

    @staticmethod
    def _revision_from_row(row) -> UserDocumentRevision:
        return UserDocumentRevision(
            document_revision_id=str(row[0]),
            document_id=str(row[1]),
            revision=row[2],
            original_file_sha256=row[3],
            content_sha256=row[4],
            extracted_text_ref=row[5],
            parser_version=row[6],
            chunker_version=row[7],
            embedding_identity=row[8],
            created_at=row[9],
        )

    def _sql(self, statement: str):
        from psycopg2 import sql

        return sql.SQL(statement).format(
            documents=sql.Identifier(self.documents_table),
            revisions=sql.Identifier(self.revisions_table),
            document_columns=sql.SQL(self._document_columns()),
            revision_columns=sql.SQL(self._revision_columns()),
        )


def _validated_document(
    document: UserDocument,
    owner_principal_id: str,
) -> UserDocument:
    document = UserDocument.model_validate(document.model_dump(mode="python"))
    if document.owner_principal_id != owner_principal_id:
        raise ValueError("document owner does not match store scope")
    return document


def _require_owner(owner_principal_id: str) -> str:
    if not isinstance(owner_principal_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,128}",
        owner_principal_id,
    ):
        raise ValueError("owner_principal_id is required")
    return owner_principal_id


def _require_uuid(value: str, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be an opaque UUID") from exc


def _json_array(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, (list, tuple)):
        raise ValueError("stored allowed usages are invalid")
    return tuple(str(item) for item in value)


__all__ = ["PostgresUserDocumentStore"]
