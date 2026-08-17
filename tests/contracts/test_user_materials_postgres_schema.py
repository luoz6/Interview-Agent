from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from app.adapters.postgres.user_materials_schema import (
    USER_MATERIALS_SCHEMA_CHECKSUM,
    USER_MATERIALS_SCHEMA_MANIFEST,
    USER_MATERIALS_SCHEMA_MIGRATION_ID,
    USER_MATERIALS_SCHEMA_RELATION_SUFFIXES,
    migrate_user_materials_schema,
    user_materials_relation_names,
    user_materials_schema_statements,
    validate_user_materials_schema,
)
from app.runtime.config import load_rag_console_runtime_settings
from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentChunk,
    UserDocumentPublicStatus,
    UserDocumentRevision,
)
from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.postgres_schema_contract import (
    LATEST_RUNTIME_MIGRATION,
    RUNTIME_MIGRATIONS,
    required_check_tokens_for_relation,
    required_columns_for_relation,
    required_foreign_key_tokens_for_relation,
    required_index_tokens_for_relation,
    required_nullable_columns_for_relation,
    required_user_materials_check_tokens_for_relation,
    required_user_materials_columns_for_relation,
    required_user_materials_foreign_key_tokens_for_relation,
    required_user_materials_index_tokens_for_relation,
    required_user_materials_nullable_columns_for_relation,
    required_user_materials_strict_positive_columns_for_relation,
)


class _RecordingCursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, object | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None) -> None:
        self.statements.append((str(statement), params))


class _RecordingConnection:
    def __init__(self, cursor: _RecordingCursor) -> None:
        self.cursor_object = cursor
        self.commit_count = 0

    def cursor(self) -> _RecordingCursor:
        return self.cursor_object

    def commit(self) -> None:
        self.commit_count += 1


class _Provider:
    def __init__(self, connection) -> None:
        self.connection_object = connection

    @contextmanager
    def connection(self):
        yield self.connection_object


class _SchemaCursor:
    def __init__(
        self,
        *,
        relations_present: bool = True,
        forced_not_nullable: frozenset[str] = frozenset(),
        document_size_check: str = "CHECK (size_bytes > 0)",
    ) -> None:
        self.relations_present = relations_present
        self.forced_not_nullable = forced_not_nullable
        self.document_size_check = document_size_check
        self.rows: list[tuple[object, ...]] = []
        self.calls: list[tuple[str, object | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None) -> None:
        query = str(statement)
        self.calls.append((query, params))
        if "to_regclass" in query:
            names = tuple(params[0])
            self.rows = [
                (name, name if self.relations_present else None)
                for name in names
            ]
            return
        if "information_schema.columns" in query:
            self.rows = [
                (
                    relation,
                    column,
                    (
                        "YES"
                        if column
                        in required_user_materials_nullable_columns_for_relation(
                            relation
                        )
                        and column not in self.forced_not_nullable
                        else "NO"
                    ),
                )
                for relation in params[0]
                for column in required_user_materials_columns_for_relation(
                    relation
                )
            ]
            return
        if "FROM pg_indexes" in query:
            self.rows = _index_rows(tuple(params[0]))
            return
        if "rule.contype='c'" in query:
            self.rows = _check_rows(
                tuple(params[0]),
                document_size_check=self.document_size_check,
            )
            return
        if "rule.contype='f'" in query:
            self.rows = _foreign_key_rows(tuple(params[0]))
            return
        raise AssertionError(f"unexpected schema validation query: {query}")

    def fetchall(self):
        return list(self.rows)


def _index_rows(relations: tuple[str, ...]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for relation in relations:
        if relation.endswith("_user_documents"):
            rows.extend(
                (
                    (
                        relation,
                        "CREATE UNIQUE INDEX documents_pkey ON "
                        f"{relation} (owner_principal_id, document_id)",
                    ),
                    (
                        relation,
                        "CREATE INDEX documents_owner_created ON "
                        f"{relation} (owner_principal_id, created_at DESC)",
                    ),
                )
            )
        elif relation.endswith("_user_document_revisions"):
            rows.extend(
                (
                    (
                        relation,
                        "CREATE UNIQUE INDEX revisions_pkey ON "
                        f"{relation} (owner_principal_id, "
                        "document_revision_id)",
                    ),
                    (
                        relation,
                        "CREATE UNIQUE INDEX revisions_number_uq ON "
                        f"{relation} (owner_principal_id, document_id, "
                        "revision)",
                    ),
                    (
                        relation,
                        "CREATE INDEX revisions_owner_document ON "
                        f"{relation} (owner_principal_id, document_id, "
                        "revision DESC)",
                    ),
                )
            )
        elif relation.endswith("_user_document_chunks"):
            rows.extend(
                (
                    (
                        relation,
                        "CREATE UNIQUE INDEX chunks_pkey ON "
                        f"{relation} (owner_principal_id, chunk_id)",
                    ),
                    (
                        relation,
                        "CREATE UNIQUE INDEX chunks_position_uq ON "
                        f"{relation} (owner_principal_id, "
                        "document_revision_id, position)",
                    ),
                    (
                        relation,
                        "CREATE INDEX chunks_owner_revision ON "
                        f"{relation} (owner_principal_id, "
                        "document_revision_id, position)",
                    ),
                    (
                        relation,
                        "CREATE INDEX chunks_lexical ON "
                        f"{relation} USING GIN (lexical_document)",
                    ),
                    (
                        relation,
                        "CREATE INDEX chunks_embedding ON "
                        f"{relation} USING HNSW "
                        "(embedding vector_cosine_ops)",
                    ),
                )
            )
    return rows


def _check_rows(
    relations: tuple[str, ...],
    *,
    document_size_check: str = "CHECK (size_bytes > 0)",
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for relation in relations:
        if relation.endswith("_user_documents"):
            rows.append((relation, document_size_check))
        elif relation.endswith("_user_document_revisions"):
            rows.extend(
                (
                    (relation, "CHECK (revision > 0)"),
                    (
                        relation,
                        "CHECK (original_file_sha256 ~ "
                        "'^[0-9a-f]{64}$')",
                    ),
                    (
                        relation,
                        "CHECK (content_sha256 ~ '^[0-9a-f]{64}$')",
                    ),
                )
            )
        elif relation.endswith("_user_document_chunks"):
            rows.extend(
                (
                    (relation, "CHECK (position > 0)"),
                    (
                        relation,
                        "CHECK (content_sha256 ~ '^[0-9a-f]{64}$')",
                    ),
                )
            )
    return rows


def _foreign_key_rows(relations: tuple[str, ...]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for relation in relations:
        if relation.endswith("_user_documents"):
            rows.append(
                (
                    relation,
                    "FOREIGN KEY (owner_principal_id, document_id, "
                    "active_revision_id) REFERENCES "
                    "interview_user_document_revisions "
                    "(owner_principal_id, document_id, "
                    "document_revision_id) DEFERRABLE INITIALLY DEFERRED",
                )
            )
        elif relation.endswith("_user_document_revisions"):
            rows.append(
                (
                    relation,
                    "FOREIGN KEY (owner_principal_id, document_id) "
                    "REFERENCES interview_user_documents "
                    "(owner_principal_id, document_id) ON DELETE CASCADE",
                )
            )
        elif relation.endswith("_user_document_chunks"):
            rows.append(
                (
                    relation,
                    "FOREIGN KEY (owner_principal_id, document_id, "
                    "document_revision_id) REFERENCES "
                    "interview_user_document_revisions "
                    "(owner_principal_id, document_id, "
                    "document_revision_id) ON DELETE CASCADE",
                )
            )
    return rows


def test_materials_schema_has_an_independent_frozen_migration_identity():
    assert USER_MATERIALS_SCHEMA_MIGRATION_ID == "user_materials_schema_v1"
    assert len(USER_MATERIALS_SCHEMA_CHECKSUM) == 64
    assert USER_MATERIALS_SCHEMA_RELATION_SUFFIXES == (
        "user_documents",
        "user_document_revisions",
        "user_document_chunks",
    )
    assert '"owner_scope":"owner-principal-composite-keys-v1"' in (
        USER_MATERIALS_SCHEMA_MANIFEST
    )
    assert USER_MATERIALS_SCHEMA_MIGRATION_ID not in {
        migration.migration_id for migration in RUNTIME_MIGRATIONS
    }
    assert LATEST_RUNTIME_MIGRATION.migration_id == (
        "row_serialization_versions_v1_v29"
    )


def test_materials_relation_validation_registry_is_complete_and_scoped():
    relations = user_materials_relation_names("interview")
    assert relations == (
        "interview_user_documents",
        "interview_user_document_revisions",
        "interview_user_document_chunks",
    )
    for relation in relations:
        assert required_columns_for_relation(relation) == frozenset()
        assert required_index_tokens_for_relation(relation) == ()
        assert required_check_tokens_for_relation(relation) == ()
        assert required_foreign_key_tokens_for_relation(relation) == ()
        assert required_nullable_columns_for_relation(relation) == frozenset()
        assert "owner_principal_id" in (
            required_user_materials_columns_for_relation(relation)
        )
        assert required_user_materials_index_tokens_for_relation(relation)
        assert required_user_materials_check_tokens_for_relation(relation)
        assert required_user_materials_foreign_key_tokens_for_relation(
            relation
        )


def test_legal_domain_minima_map_to_matching_nullable_and_positive_schema():
    created_at = datetime(2026, 8, 15, tzinfo=timezone.utc)
    document = UserDocument(
        document_id="00000000-0000-0000-0000-000000000001",
        owner_principal_id="principal-1",
        display_title="A",
        original_filename="a.txt",
        media_type="text/plain",
        size_bytes=1,
        public_status=UserDocumentPublicStatus.PROCESSING,
        internal_stage=None,
        created_at=created_at,
        updated_at=created_at,
    )
    revision = UserDocumentRevision(
        document_revision_id="00000000-0000-0000-0000-000000000002",
        document_id=document.document_id,
        revision=1,
        original_file_sha256="a" * 64,
        content_sha256="b" * 64,
        extracted_text_ref="inline",
        parser_version="parser-v1",
        chunker_version="chunker-v1",
        embedding_identity="provider:model:revision:1",
        created_at=created_at,
    )
    chunk = UserDocumentChunk(
        chunk_id="00000000-0000-0000-0000-000000000003",
        owner_principal_id=document.owner_principal_id,
        document_id=document.document_id,
        document_revision_id=revision.document_revision_id,
        position=1,
        title="A",
        section_label=None,
        content="x",
        content_sha256="c" * 64,
        embedding=(0.0,),
        embedding_identity=revision.embedding_identity,
        created_at=created_at,
    )

    document_mapping = document.model_dump(mode="python")
    revision_mapping = revision.model_dump(mode="python")
    chunk_mapping = chunk.model_dump(mode="python")
    assert document_mapping["size_bytes"] == 1
    assert document_mapping["internal_stage"] is None
    assert revision_mapping["revision"] == 1
    assert chunk_mapping["position"] == 1
    assert chunk_mapping["section_label"] is None

    document_relation, revision_relation, chunk_relation = (
        user_materials_relation_names("interview")
    )
    assert required_user_materials_nullable_columns_for_relation(
        document_relation
    ) == frozenset(
        {
            "internal_stage",
            "active_revision_id",
            "safe_error_code",
            "deleted_at",
        }
    )
    assert required_user_materials_nullable_columns_for_relation(
        revision_relation
    ) == frozenset()
    assert required_user_materials_nullable_columns_for_relation(
        chunk_relation
    ) == frozenset({"section_label"})
    assert required_user_materials_strict_positive_columns_for_relation(
        document_relation
    ) == frozenset({"size_bytes"})
    assert required_user_materials_strict_positive_columns_for_relation(
        revision_relation
    ) == frozenset({"revision"})
    assert required_user_materials_strict_positive_columns_for_relation(
        chunk_relation
    ) == frozenset({"position"})

    document_ddl = next(
        statement
        for statement in user_materials_schema_statements(
            table_prefix="interview",
            embedding_dimension=1,
        )
        if 'CREATE TABLE IF NOT EXISTS "interview_user_documents"'
        in statement
    )
    assert "size_bytes BIGINT NOT NULL CHECK (size_bytes > 0)" in document_ddl
    assert "internal_stage TEXT," in document_ddl
    assert "internal_stage TEXT NOT NULL" not in document_ddl


def test_materials_migration_executes_only_the_frozen_additive_plan_once():
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    provider = _Provider(connection)
    expected = user_materials_schema_statements(
        table_prefix="interview",
        embedding_dimension=1536,
    )

    migrate_user_materials_schema(
        provider,
        table_prefix="interview",
        embedding_dimension=1536,
    )

    assert tuple(statement for statement, params in cursor.statements) == expected
    assert all(params is None for statement, params in cursor.statements)
    assert connection.commit_count == 1


def test_materials_schema_validate_accepts_the_complete_fake_contract():
    cursor = _SchemaCursor(relations_present=True)
    validate_user_materials_schema(
        _Provider(_RecordingConnection(cursor)),
        table_prefix="interview",
    )
    assert cursor.calls[0][1] == (
        list(user_materials_relation_names("interview")),
    )


def test_materials_schema_validate_rejects_non_nullable_internal_stage():
    cursor = _SchemaCursor(forced_not_nullable=frozenset({"internal_stage"}))
    with pytest.raises(PostgresSchemaNotReady, match="columns are incompatible"):
        validate_user_materials_schema(
            _Provider(_RecordingConnection(cursor)),
            table_prefix="interview",
        )


def test_materials_schema_validate_rejects_non_strict_size_check():
    cursor = _SchemaCursor(document_size_check="CHECK (size_bytes >= 0)")
    with pytest.raises(PostgresSchemaNotReady, match="checks are incompatible"):
        validate_user_materials_schema(
            _Provider(_RecordingConnection(cursor)),
            table_prefix="interview",
        )


def test_missing_materials_relations_fail_only_the_explicit_dependency_check():
    rag_before = load_rag_console_runtime_settings(
        {
            "RAG_CONSOLE_ENABLED": "true",
            "RAG_LIVE_EXECUTION_ENABLED": "false",
            "RAG_CORPUS_WRITE_ENABLED": "true",
        }
    )
    cursor = _SchemaCursor(relations_present=False)

    with pytest.raises(PostgresSchemaNotReady, match="schema is not ready"):
        validate_user_materials_schema(
            _Provider(_RecordingConnection(cursor)),
            table_prefix="interview",
        )

    rag_after = load_rag_console_runtime_settings(
        {
            "RAG_CONSOLE_ENABLED": "true",
            "RAG_LIVE_EXECUTION_ENABLED": "false",
            "RAG_CORPUS_WRITE_ENABLED": "true",
        }
    )
    assert rag_after == rag_before
    assert rag_after.safe_summary() == {
        "console_enabled": True,
        "live_execution_enabled": False,
        "corpus_write_enabled": True,
        "access_mode": "loopback",
    }
    assert len(cursor.calls) == 1


@pytest.mark.parametrize("embedding_dimension", (0, -1, True, 1.5))
def test_materials_schema_rejects_invalid_embedding_dimensions(
    embedding_dimension,
):
    with pytest.raises(ValueError, match="embedding_dimension"):
        user_materials_schema_statements(
            table_prefix="interview",
            embedding_dimension=embedding_dimension,
        )
