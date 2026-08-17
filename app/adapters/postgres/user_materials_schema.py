from __future__ import annotations

import hashlib
import json

from app.services.postgres_connections import ConnectionProvider
from app.services.postgres_identifiers import (
    runtime_schema_identifier,
    validate_postgres_identifier,
    validate_runtime_table_prefix,
)
from app.services.postgres_schema import validate_relations
from app.services.postgres_schema_contract import (
    required_user_materials_check_tokens_for_relation,
    required_user_materials_columns_for_relation,
    required_user_materials_foreign_key_tokens_for_relation,
    required_user_materials_index_tokens_for_relation,
    required_user_materials_nullable_columns_for_relation,
    required_user_materials_strict_positive_columns_for_relation,
)


USER_MATERIALS_SCHEMA_MIGRATION_ID = "user_materials_schema_v1"
USER_MATERIALS_SCHEMA_TRANSACTION_MODE = "transactional"
USER_MATERIALS_SCHEMA_RELATION_SUFFIXES = (
    "user_documents",
    "user_document_revisions",
    "user_document_chunks",
)
USER_MATERIALS_SCHEMA_MANIFEST = json.dumps(
    {
        "embedding_storage": "user-document-chunk-row-vector",
        "lexical_storage": "generated-simple-tsvector",
        "owner_scope": "owner-principal-composite-keys-v1",
        "relations": list(USER_MATERIALS_SCHEMA_RELATION_SUFFIXES),
        "transaction_mode": USER_MATERIALS_SCHEMA_TRANSACTION_MODE,
    },
    sort_keys=True,
    separators=(",", ":"),
)
USER_MATERIALS_SCHEMA_CHECKSUM = hashlib.sha256(
    USER_MATERIALS_SCHEMA_MANIFEST.encode("utf-8")
).hexdigest()


def user_materials_relation_names(table_prefix: str) -> tuple[str, str, str]:
    """Return the three independently validated Materials relation names."""

    validate_runtime_table_prefix(table_prefix)
    relations = tuple(
        validate_postgres_identifier(f"{table_prefix}_{suffix}")
        for suffix in USER_MATERIALS_SCHEMA_RELATION_SUFFIXES
    )
    return relations  # type: ignore[return-value]


def user_materials_schema_statements(
    *,
    table_prefix: str,
    embedding_dimension: int,
) -> tuple[str, ...]:
    """Build the additive, idempotent SQL owned by the Materials schema."""

    if (
        isinstance(embedding_dimension, bool)
        or not isinstance(embedding_dimension, int)
        or embedding_dimension < 1
    ):
        raise ValueError("embedding_dimension must be a positive integer")

    document_name, revision_name, chunk_name = user_materials_relation_names(
        table_prefix
    )
    documents = _quoted_identifier(document_name)
    revisions = _quoted_identifier(revision_name)
    chunks = _quoted_identifier(chunk_name)

    revision_document_fk = _quoted_identifier(
        runtime_schema_identifier(
            table_prefix,
            "user_document_revisions_document_fk",
        )
    )
    chunk_revision_fk = _quoted_identifier(
        runtime_schema_identifier(
            table_prefix,
            "user_document_chunks_revision_fk",
        )
    )
    active_revision_fk_name = runtime_schema_identifier(
        table_prefix,
        "user_documents_active_revision_fk",
    )
    active_revision_fk = _quoted_identifier(active_revision_fk_name)

    document_owner_created_idx = _quoted_identifier(
        runtime_schema_identifier(
            table_prefix,
            "user_documents_owner_created_idx",
        )
    )
    revision_owner_document_idx = _quoted_identifier(
        runtime_schema_identifier(
            table_prefix,
            "user_document_revisions_owner_document_idx",
        )
    )
    chunk_owner_revision_idx = _quoted_identifier(
        runtime_schema_identifier(
            table_prefix,
            "user_document_chunks_owner_revision_idx",
        )
    )
    chunk_lexical_idx = _quoted_identifier(
        runtime_schema_identifier(
            table_prefix,
            "user_document_chunks_lexical_idx",
        )
    )
    chunk_embedding_idx = _quoted_identifier(
        runtime_schema_identifier(
            table_prefix,
            "user_document_chunks_embedding_idx",
        )
    )

    return (
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
        CREATE TABLE IF NOT EXISTS {documents} (
            owner_principal_id TEXT NOT NULL,
            document_id UUID NOT NULL,
            display_title TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
            public_status TEXT NOT NULL,
            internal_stage TEXT,
            enabled BOOLEAN NOT NULL,
            allowed_usages JSONB NOT NULL,
            active_revision_id UUID,
            safe_error_code TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ,
            PRIMARY KEY (owner_principal_id, document_id)
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {revisions} (
            owner_principal_id TEXT NOT NULL,
            document_revision_id UUID NOT NULL,
            document_id UUID NOT NULL,
            revision INTEGER NOT NULL CHECK (revision > 0),
            original_file_sha256 TEXT NOT NULL CHECK (
                original_file_sha256 ~ '^[0-9a-f]{{64}}$'
            ),
            content_sha256 TEXT NOT NULL CHECK (
                content_sha256 ~ '^[0-9a-f]{{64}}$'
            ),
            extracted_text_ref TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            chunker_version TEXT NOT NULL,
            embedding_identity TEXT NOT NULL,
            original_content BYTEA NOT NULL,
            extracted_text TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (owner_principal_id, document_revision_id),
            UNIQUE (owner_principal_id, document_id, revision),
            UNIQUE (
                owner_principal_id, document_id, document_revision_id
            ),
            CONSTRAINT {revision_document_fk}
                FOREIGN KEY (owner_principal_id, document_id)
                REFERENCES {documents} (owner_principal_id, document_id)
                ON DELETE CASCADE
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {chunks} (
            owner_principal_id TEXT NOT NULL,
            chunk_id UUID NOT NULL,
            document_id UUID NOT NULL,
            document_revision_id UUID NOT NULL,
            position INTEGER NOT NULL CHECK (position > 0),
            title TEXT NOT NULL,
            section_label TEXT,
            content TEXT NOT NULL,
            content_sha256 TEXT NOT NULL CHECK (
                content_sha256 ~ '^[0-9a-f]{{64}}$'
            ),
            embedding VECTOR({embedding_dimension}) NOT NULL,
            embedding_identity TEXT NOT NULL,
            lexical_document TSVECTOR GENERATED ALWAYS AS (
                to_tsvector(
                    'simple',
                    coalesce(title, '') || ' ' ||
                    coalesce(section_label, '') || ' ' ||
                    content
                )
            ) STORED,
            created_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (owner_principal_id, chunk_id),
            UNIQUE (
                owner_principal_id, document_revision_id, position
            ),
            CONSTRAINT {chunk_revision_fk}
                FOREIGN KEY (
                    owner_principal_id,
                    document_id,
                    document_revision_id
                )
                REFERENCES {revisions} (
                    owner_principal_id,
                    document_id,
                    document_revision_id
                ) ON DELETE CASCADE
        )
        """.strip(),
        f"""
        DO $materials_schema$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = '{active_revision_fk_name}'
                  AND conrelid = to_regclass('public.{document_name}')
            ) THEN
                ALTER TABLE {documents}
                    ADD CONSTRAINT {active_revision_fk}
                    FOREIGN KEY (
                        owner_principal_id,
                        document_id,
                        active_revision_id
                    )
                    REFERENCES {revisions} (
                        owner_principal_id,
                        document_id,
                        document_revision_id
                    ) DEFERRABLE INITIALLY DEFERRED;
            END IF;
        END
        $materials_schema$
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS {document_owner_created_idx}
        ON {documents} (owner_principal_id, created_at DESC)
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS {revision_owner_document_idx}
        ON {revisions} (owner_principal_id, document_id, revision DESC)
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS {chunk_owner_revision_idx}
        ON {chunks} (owner_principal_id, document_revision_id, position)
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS {chunk_lexical_idx}
        ON {chunks} USING GIN (lexical_document)
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS {chunk_embedding_idx}
        ON {chunks} USING HNSW (embedding vector_cosine_ops)
        """.strip(),
    )


def migrate_user_materials_schema(
    provider: ConnectionProvider,
    *,
    table_prefix: str,
    embedding_dimension: int,
) -> None:
    """Apply only the isolated Materials DDL using an injected provider."""

    statements = user_materials_schema_statements(
        table_prefix=table_prefix,
        embedding_dimension=embedding_dimension,
    )
    with provider.connection() as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        connection.commit()


def validate_user_materials_schema(
    provider: ConnectionProvider,
    *,
    table_prefix: str,
) -> None:
    """Fail closed only for the explicitly requested Materials relations."""

    validate_relations(
        provider,
        user_materials_relation_names(table_prefix),
        required_columns_resolver=(
            required_user_materials_columns_for_relation
        ),
        required_index_tokens_resolver=(
            required_user_materials_index_tokens_for_relation
        ),
        required_check_tokens_resolver=(
            required_user_materials_check_tokens_for_relation
        ),
        required_foreign_key_tokens_resolver=(
            required_user_materials_foreign_key_tokens_for_relation
        ),
        required_nullable_columns_resolver=(
            required_user_materials_nullable_columns_for_relation
        ),
        required_strict_positive_columns_resolver=(
            required_user_materials_strict_positive_columns_for_relation
        ),
    )


def _quoted_identifier(value: str) -> str:
    validate_postgres_identifier(value)
    return f'"{value}"'


__all__ = [
    "USER_MATERIALS_SCHEMA_CHECKSUM",
    "USER_MATERIALS_SCHEMA_MANIFEST",
    "USER_MATERIALS_SCHEMA_MIGRATION_ID",
    "USER_MATERIALS_SCHEMA_RELATION_SUFFIXES",
    "USER_MATERIALS_SCHEMA_TRANSACTION_MODE",
    "migrate_user_materials_schema",
    "user_materials_relation_names",
    "user_materials_schema_statements",
    "validate_user_materials_schema",
]
