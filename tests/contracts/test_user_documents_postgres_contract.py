from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.adapters.postgres.user_documents import PostgresUserDocumentStore
from app.domain.knowledge.user_document import (
    UserDocument,
    UserDocumentInternalStage,
    UserDocumentPublicStatus,
    UserDocumentRevision,
)
from app.ports.user_documents import UserDocumentStorePort


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)
OWNER_A = "principal-a"
OWNER_B = "principal-b"
ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = ROOT / "app" / "adapters" / "postgres" / "user_documents.py"


class ScriptedCursor:
    def __init__(self, connection) -> None:
        self.connection = connection
        self._row = None
        self._rows: list[tuple[object, ...]] = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None) -> None:
        rendered = str(statement)
        self.connection.calls.append((rendered, params))
        result = (
            self.connection.results.pop(0)
            if self.connection.results
            else None
        )
        if isinstance(result, list):
            self._rows = result
            self._row = None
            self.rowcount = len(result)
        else:
            self._row = result
            self._rows = []
            self.rowcount = 0 if result is None else 1

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class ScriptedConnection:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, object | None]] = []

    def cursor(self) -> ScriptedCursor:
        return ScriptedCursor(self)


class ScriptedProvider:
    def __init__(self, results=()) -> None:
        self.connection_object = ScriptedConnection(results)
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def connection(self):
        try:
            yield self.connection_object
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


def _store(results=()) -> tuple[PostgresUserDocumentStore, ScriptedProvider]:
    provider = ScriptedProvider(results)
    store = object.__new__(PostgresUserDocumentStore)
    store._connection_provider = provider
    store.table_prefix = "interview"
    store.documents_table = "interview_user_documents"
    store.revisions_table = "interview_user_document_revisions"
    store.schema_mode = "validate"
    return store, provider


def _document(*, active_revision_id: str | None = None) -> UserDocument:
    return UserDocument(
        document_id=str(uuid4()),
        owner_principal_id=OWNER_A,
        display_title="PostgreSQL notes",
        original_filename="postgres.md",
        media_type="text/markdown",
        size_bytes=24,
        public_status=(
            UserDocumentPublicStatus.READY
            if active_revision_id is not None
            else UserDocumentPublicStatus.PROCESSING
        ),
        internal_stage=(
            None
            if active_revision_id is not None
            else UserDocumentInternalStage.EXTRACTION
        ),
        active_revision_id=active_revision_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _document_row(document: UserDocument, *, usages=None):
    return (
        document.document_id,
        document.owner_principal_id,
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
        json.dumps(usages or list(document.allowed_usages)),
        document.active_revision_id,
        document.safe_error_code,
        document.created_at,
        document.updated_at,
        document.deleted_at,
    )


def _revision(
    document: UserDocument,
    *,
    revision_number: int = 1,
    revision_id: str | None = None,
) -> UserDocumentRevision:
    revision_id = revision_id or str(uuid4())
    return UserDocumentRevision(
        document_revision_id=revision_id,
        document_id=document.document_id,
        revision=revision_number,
        original_file_sha256="a" * 64,
        content_sha256="b" * 64,
        extracted_text_ref=f"postgres:user-material:{revision_id}",
        parser_version="utf8-text-v1",
        chunker_version="paragraph-v1",
        embedding_identity="fake:model:revision:3",
        created_at=NOW + timedelta(seconds=revision_number),
    )


def _revision_row(revision: UserDocumentRevision):
    return (
        revision.document_revision_id,
        revision.document_id,
        revision.revision,
        revision.original_file_sha256,
        revision.content_sha256,
        revision.extracted_text_ref,
        revision.parser_version,
        revision.chunker_version,
        revision.embedding_identity,
        revision.created_at,
    )


def _calls(provider: ScriptedProvider):
    return provider.connection_object.calls


def test_postgres_store_is_runtime_checkable_existing_port():
    store, _provider = _store()
    assert isinstance(store, UserDocumentStorePort)


def test_constructor_requires_explicit_validate_mode_for_injected_provider(
    monkeypatch,
):
    provider = ScriptedProvider()
    with pytest.raises(ValueError, match="explicit schema_mode"):
        PostgresUserDocumentStore(connection_provider=provider)

    validated = []
    monkeypatch.setattr(
        "app.adapters.postgres.user_documents.validate_user_materials_schema",
        lambda supplied, *, table_prefix: validated.append(
            (supplied, table_prefix)
        ),
    )
    store = PostgresUserDocumentStore(
        connection_provider=provider,
        schema_mode="validate",
    )
    assert store.documents_table == "interview_user_documents"
    assert store.revisions_table == "interview_user_document_revisions"
    assert validated == [(provider, "interview")]

    with pytest.raises(ValueError, match="operator-owned"):
        PostgresUserDocumentStore(
            connection_provider=provider,
            schema_mode="migrate",
        )


def test_document_crud_maps_json_enums_nullable_stage_and_owner_parameters():
    document = _document()
    ready_revision_id = str(uuid4())
    ready = document.model_copy(
        update={
            "public_status": UserDocumentPublicStatus.READY,
            "internal_stage": None,
            "active_revision_id": ready_revision_id,
            "updated_at": NOW + timedelta(seconds=1),
        }
    )
    store, provider = _store(
        [
            _document_row(document),
            _document_row(document, usages=["feedback", "question"]),
            [_document_row(document)],
            _document_row(ready),
            None,
        ]
    )

    assert store.create_document(
        owner_principal_id=OWNER_A,
        document=document,
    ) == document
    loaded = store.get_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    )
    assert loaded is not None
    assert loaded.allowed_usages == ("question", "feedback")
    assert loaded.internal_stage == UserDocumentInternalStage.EXTRACTION
    assert store.list_documents(owner_principal_id=OWNER_A) == (document,)
    assert store.save_document(
        owner_principal_id=OWNER_A,
        document=ready,
    ) == ready
    assert store.save_document(
        owner_principal_id=OWNER_B,
        document=ready.model_copy(update={"owner_principal_id": OWNER_B}),
    ) is None

    create_params = _calls(provider)[0][1]
    assert create_params[0] == OWNER_A
    assert json.loads(create_params[9]) == ["question", "follow_up", "feedback"]
    assert _calls(provider)[1][1] == (OWNER_A, document.document_id)
    assert _calls(provider)[2][1] == (OWNER_A,)
    assert _calls(provider)[3][1][-2:] == (OWNER_A, document.document_id)
    assert _calls(provider)[4][1][-2:] == (OWNER_B, document.document_id)

    conflict_store, conflict_provider = _store([None])
    with pytest.raises(ValueError, match="already exists"):
        conflict_store.create_document(
            owner_principal_id=OWNER_A,
            document=document,
        )
    assert conflict_provider.rollbacks == 1


def test_document_reads_and_writes_do_not_enumerate_another_owner():
    document = _document()
    store, provider = _store([_document_row(document), None, None])

    assert store.get_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) == document
    assert store.get_document(
        owner_principal_id=OWNER_B,
        document_id=document.document_id,
    ) is None
    assert store.delete_document(
        owner_principal_id=OWNER_B,
        document_id=document.document_id,
    ) is None

    assert [params[0] for _sql, params in _calls(provider)] == [
        OWNER_A,
        OWNER_B,
        OWNER_B,
    ]


def test_document_store_revalidates_forged_models_before_sql():
    document = _document()
    forged = document.model_copy(
        update={
            "public_status": UserDocumentPublicStatus.READY,
            "internal_stage": None,
        }
    )
    store, provider = _store()
    with pytest.raises(ValidationError, match="active revision"):
        store.save_document(owner_principal_id=OWNER_A, document=forged)
    assert _calls(provider) == []


def test_revision_create_is_idempotent_for_identical_identity_and_payload():
    document = _document()
    revision = _revision(document)
    raw = b"PostgreSQL raw bytes"
    text = "PostgreSQL extracted text"
    existing = (*_revision_row(revision), memoryview(raw), text)
    store, provider = _store([(document.document_id,), existing])

    assert store.create_revision(
        owner_principal_id=OWNER_A,
        revision=revision,
        original_content=raw,
        extracted_text=text,
    ) == revision
    assert not any(
        "user-materials:create-revision" in statement
        for statement, _params in _calls(provider)
    )
    assert all(params[0] == OWNER_A for _statement, params in _calls(provider))

    conflict_store, conflict_provider = _store(
        [(document.document_id,), existing]
    )
    with pytest.raises(ValueError, match="identity conflict"):
        conflict_store.create_revision(
            owner_principal_id=OWNER_A,
            revision=revision,
            original_content=raw + b" changed",
            extracted_text=text,
        )
    assert conflict_provider.rollbacks == 1


def test_revision_create_requires_owner_document_and_contiguous_number():
    document = _document()
    revision = _revision(document)
    store, provider = _store(
        [(document.document_id,), None, (0,), _revision_row(revision)]
    )
    assert store.create_revision(
        owner_principal_id=OWNER_A,
        revision=revision,
        original_content=b"raw",
        extracted_text="text",
    ) == revision
    assert all(params[0] == OWNER_A for _statement, params in _calls(provider))

    missing, missing_provider = _store([None])
    with pytest.raises(ValueError, match="document not found"):
        missing.create_revision(
            owner_principal_id=OWNER_B,
            revision=revision,
            original_content=b"raw",
            extracted_text="text",
        )
    assert missing_provider.rollbacks == 1

    non_contiguous, non_contiguous_provider = _store(
        [(document.document_id,), None, (1,)]
    )
    with pytest.raises(ValueError, match="contiguous"):
        non_contiguous.create_revision(
            owner_principal_id=OWNER_A,
            revision=revision,
            original_content=b"raw",
            extracted_text="text",
        )
    assert non_contiguous_provider.rollbacks == 1


def test_revision_reads_list_latest_and_payload_are_owner_scoped():
    document = _document()
    first = _revision(document)
    second = _revision(document, revision_number=2)
    store, provider = _store(
        [
            _revision_row(first),
            _revision_row(second),
            [_revision_row(first), _revision_row(second)],
            (memoryview(b"raw"), "text"),
            None,
            None,
        ]
    )
    assert store.get_revision(
        owner_principal_id=OWNER_A,
        document_revision_id=first.document_revision_id,
    ) == first
    assert store.get_latest_revision(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) == second
    assert store.list_revisions(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) == (first, second)
    assert store.get_revision_content(
        owner_principal_id=OWNER_A,
        document_revision_id=first.document_revision_id,
    ) == (b"raw", "text")
    assert store.get_revision(
        owner_principal_id=OWNER_B,
        document_revision_id=first.document_revision_id,
    ) is None
    assert store.get_revision_content(
        owner_principal_id=OWNER_B,
        document_revision_id=first.document_revision_id,
    ) is None
    assert [params[0] for _sql, params in _calls(provider)] == [
        OWNER_A,
        OWNER_A,
        OWNER_A,
        OWNER_A,
        OWNER_B,
        OWNER_B,
    ]


def test_delete_returns_revision_payload_counts_and_is_owner_scoped():
    document = _document()
    store, provider = _store(
        [(document.document_id,), (2, 2), (document.document_id,)]
    )
    assert store.delete_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) == (2, 2)
    assert all(params[0] == OWNER_A for _statement, params in _calls(provider))
    assert provider.commits == 1

    missing, missing_provider = _store([None])
    assert missing.delete_document(
        owner_principal_id=OWNER_B,
        document_id=document.document_id,
    ) is None
    assert missing_provider.commits == 1


def test_every_store_sql_statement_has_explicit_owner_scope():
    tree = ast.parse(STORE_PATH.read_text(encoding="utf-8"))
    statements = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "user-materials:" in node.value
    ]
    assert len(statements) == 15
    for statement in statements:
        normalized = " ".join(statement.split()).casefold()
        assert "owner_principal_id" in normalized
        if " insert into " in f" {normalized} ":
            assert "owner_principal_id," in normalized
        else:
            assert "where owner_principal_id=%s" in normalized


def test_store_source_has_no_repository_search_fusion_or_corpus_lifecycle():
    source = STORE_PATH.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "search_semantic",
        "search_lexical",
        "pgvectoruserdocumentchunkrepository",
        "create_version",
        "activate_version",
        "retire_version",
        "reciprocal_rank_fusion",
        "corpus_version",
        "manifest_sha256",
    ):
        assert forbidden not in source
