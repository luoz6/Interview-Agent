from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.materials.models import MaterialPatchRequest, MaterialResponse
from app.main import app
from app.runtime.config import (
    load_rag_console_runtime_settings,
    load_user_materials_runtime_settings,
)
from app.services import runtime
from tests.vector_store_fixtures import FakeEmbeddingProvider


def test_materials_api_freezes_five_operations_and_safe_models_only():
    schema = app.openapi()
    operations = {
        (path, method)
        for path, item in schema["paths"].items()
        if path.startswith("/api/materials")
        for method in item
        if method in {"get", "post", "patch", "delete"}
    }
    assert operations == {
        ("/api/materials", "get"),
        ("/api/materials", "post"),
        ("/api/materials/{document_id}", "patch"),
        ("/api/materials/{document_id}", "delete"),
        ("/api/materials/{document_id}/retry", "post"),
    }
    assert set(MaterialResponse.model_fields) == {
        "document_id",
        "display_name",
        "media_type",
        "size_bytes",
        "status",
        "enabled",
        "allowed_usage",
        "created_at",
        "updated_at",
        "error_code",
    }
    assert set(MaterialPatchRequest.model_fields) == {
        "display_name",
        "enabled",
        "allowed_usage",
    }


@pytest.mark.parametrize(
    ("enabled", "ingest_enabled"),
    ((False, False), (False, True), (True, False), (True, True)),
)
def test_materials_capabilities_are_independent_fail_closed_strict_boole(
    enabled,
    ingest_enabled,
):
    settings = load_user_materials_runtime_settings(
        {
            "USER_MATERIALS_ENABLED": str(enabled).lower(),
            "USER_MATERIALS_INGEST_ENABLED": str(ingest_enabled).lower(),
        }
    )
    assert settings.enabled is enabled
    assert settings.ingest_enabled is ingest_enabled

    rag = load_rag_console_runtime_settings(
        {
            "RAG_CONSOLE_ENABLED": "true",
            "RAG_LIVE_EXECUTION_ENABLED": "false",
            "RAG_CORPUS_WRITE_ENABLED": "true",
        }
    )
    assert rag.console_enabled is True
    assert rag.live_execution_enabled is False
    assert rag.corpus_write_enabled is True


def test_materials_capability_defaults_and_invalid_values_fail_closed():
    assert load_user_materials_runtime_settings({}).enabled is False
    assert load_user_materials_runtime_settings({}).ingest_enabled is False
    with pytest.raises(ValueError, match="USER_MATERIALS_ENABLED"):
        load_user_materials_runtime_settings({"USER_MATERIALS_ENABLED": "1"})
    with pytest.raises(ValueError, match="USER_MATERIALS_INGEST_ENABLED"):
        load_user_materials_runtime_settings(
            {"USER_MATERIALS_INGEST_ENABLED": "yes"}
        )


def test_runtime_assembly_is_singleton_in_memory_and_embedder_is_lazy(monkeypatch):
    calls = 0

    def build_fake_embedder():
        nonlocal calls
        calls += 1
        return FakeEmbeddingProvider()

    monkeypatch.setattr(
        "app.services.embedding_providers.build_embedding_provider",
        build_fake_embedder,
    )
    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "memory")
    runtime.reset_runtime_for_tests()
    try:
        store = runtime.get_user_document_store()
        chunks = runtime.get_user_document_chunk_repository()
        documents = runtime.get_user_document_service()
        deletion = runtime.get_user_document_deletion_service()
        assert calls == 0
        assert runtime.get_user_document_store() is store
        assert runtime.get_user_document_chunk_repository() is chunks
        assert runtime.get_user_document_service() is documents
        assert runtime.get_user_document_deletion_service() is deletion

        ingestion = runtime.get_user_document_ingestion_service()
        assert calls == 1
        assert runtime.get_user_document_ingestion_service() is ingestion
        assert calls == 1
    finally:
        runtime.reset_runtime_for_tests()


def test_postgres_runtime_assembles_validating_materials_adapters(monkeypatch):
    business = object()
    domains = SimpleNamespace(business=business)
    store = object()
    chunks = object()
    calls: list[tuple[str, dict[str, object]]] = []

    def build_store(**kwargs):
        calls.append(("store", kwargs))
        return store

    def build_chunks(**kwargs):
        calls.append(("chunks", kwargs))
        return chunks

    monkeypatch.setattr(runtime, "get_runtime_store", lambda: "postgres")
    monkeypatch.setattr(
        runtime,
        "get_postgres_connection_domains",
        lambda: domains,
    )
    monkeypatch.setattr(runtime, "get_runtime_table_prefix", lambda: "materials")
    monkeypatch.setattr(
        "app.runtime.config.compatibility.get_embedding_settings",
        lambda: SimpleNamespace(dimension=3),
    )
    monkeypatch.setattr(
        "app.adapters.postgres.user_documents.PostgresUserDocumentStore",
        build_store,
    )
    monkeypatch.setattr(
        "app.adapters.pgvector.user_document_repository."
        "PgVectorUserDocumentChunkRepository",
        build_chunks,
    )

    runtime.reset_runtime_for_tests()
    try:
        assert runtime.get_user_document_store() is store
        assert runtime.get_user_document_chunk_repository() is chunks
        assert calls == [
            (
                "store",
                {
                    "connection_provider": business,
                    "table_prefix": "materials",
                    "schema_mode": "validate",
                },
            ),
            (
                "chunks",
                {
                    "embedding_dimension": 3,
                    "connection_provider": business,
                    "table_prefix": "materials",
                    "schema_mode": "validate",
                },
            ),
        ]
    finally:
        runtime.reset_runtime_for_tests()
