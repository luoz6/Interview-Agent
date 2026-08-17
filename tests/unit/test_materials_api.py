from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.adapters.memory.user_documents import (
    InMemoryUserDocumentChunkRepository,
    InMemoryUserDocumentStore,
)
from app.api.shared.dependencies import (
    get_principal_identity_resolver,
    get_user_document_deletion_service,
    get_user_document_ingestion_service,
    get_user_document_service,
    get_user_materials_runtime_settings,
)
from app.application.materials.deletion_service import UserDocumentDeletionService
from app.application.materials.ingestion_service import UserDocumentIngestionService
from app.application.materials.service import UserDocumentService
from app.main import app
from app.runtime.config.models import UserMaterialsRuntimeSettings
from app.services.embedding_providers import EmbeddingProviderError
from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.principal_identity import (
    ExplicitPrincipalIdentityResolver,
    NullPrincipalIdentityResolver,
)
from tests.vector_store_fixtures import FakeEmbeddingProvider


OWNER_A = "principal-a"
OWNER_B = "principal-b"
CLIENT = TestClient(app)


class FlakyEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        if self.calls == 1:
            raise EmbeddingProviderError("synthetic_unavailable", retryable=True)
        return super().embed_documents(texts)


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _bundle(embedder=None):
    store = InMemoryUserDocumentStore()
    chunks = InMemoryUserDocumentChunkRepository()
    return SimpleNamespace(
        store=store,
        chunks=chunks,
        documents=UserDocumentService(store=store),
        ingestion=UserDocumentIngestionService(
            store=store,
            chunks=chunks,
            embedder=embedder or FakeEmbeddingProvider(),
        ),
        deletion=UserDocumentDeletionService(store=store, chunks=chunks),
    )


def _configure(
    bundle,
    *,
    enabled: bool,
    ingest_enabled: bool,
    principal_id: str = OWNER_A,
):
    settings = UserMaterialsRuntimeSettings(
        enabled=enabled,
        ingest_enabled=ingest_enabled,
    )
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-test",
        principal_id=principal_id,
    )
    app.dependency_overrides[get_user_materials_runtime_settings] = lambda: settings
    app.dependency_overrides[get_principal_identity_resolver] = lambda: resolver
    app.dependency_overrides[get_user_document_service] = lambda: bundle.documents
    app.dependency_overrides[get_user_document_ingestion_service] = (
        lambda: bundle.ingestion
    )
    app.dependency_overrides[get_user_document_deletion_service] = (
        lambda: bundle.deletion
    )


def _ingest_direct(bundle, *, owner=OWNER_A, filename="redis.txt", content=b"Redis"):
    return bundle.ingestion.ingest(
        owner_principal_id=owner,
        original_filename=filename,
        media_type="text/plain",
        content=content,
    )


def _upload(*, filename="redis.txt", content=b"Redis", display_name=None, data=None):
    form = dict(data or {})
    if display_name is not None:
        form["display_name"] = display_name
    return CLIENT.post(
        "/api/materials",
        files={"file": (filename, content, "text/plain")},
        data=form,
    )


def test_both_capabilities_false_hide_operations_but_preserve_owner_delete():
    bundle = _bundle()
    document = _ingest_direct(bundle)
    _configure(bundle, enabled=False, ingest_enabled=False)

    assert CLIENT.get("/api/materials").status_code == 404
    assert _upload().status_code == 404
    assert CLIENT.patch(
        f"/api/materials/{document.document_id}",
        json={"display_name": "Renamed"},
    ).status_code == 404
    assert CLIENT.post(
        f"/api/materials/{document.document_id}/retry"
    ).status_code == 404

    deleted = CLIENT.delete(f"/api/materials/{document.document_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"document_id": document.document_id, "deleted": True}
    assert bundle.store.list_documents(owner_principal_id=OWNER_A) == ()


def test_ingest_disabled_allows_list_patch_and_delete_only():
    bundle = _bundle()
    document = _ingest_direct(bundle)
    _configure(bundle, enabled=True, ingest_enabled=False)

    listed = CLIENT.get("/api/materials")
    assert listed.status_code == 200
    assert [item["document_id"] for item in listed.json()["items"]] == [
        document.document_id
    ]

    patched = CLIENT.patch(
        f"/api/materials/{document.document_id}",
        json={
            "display_name": "Redis Notes",
            "enabled": False,
            "allowed_usage": ["feedback", "question"],
        },
    )
    assert patched.status_code == 200
    assert patched.json()["display_name"] == "Redis Notes"
    assert patched.json()["status"] == "disabled"
    assert patched.json()["allowed_usage"] == ["question", "feedback"]

    assert _upload().status_code == 404
    assert CLIENT.post(
        f"/api/materials/{document.document_id}/retry"
    ).status_code == 404
    assert CLIENT.delete(f"/api/materials/{document.document_id}").status_code == 200


def test_enabled_materials_support_full_upload_list_patch_retry_delete_lifecycle():
    bundle = _bundle()
    _configure(bundle, enabled=True, ingest_enabled=True)

    uploaded = _upload(
        filename="redis.txt",
        content=b"Redis cache aside",
        display_name="  Redis Interview Notes  ",
    )
    assert uploaded.status_code == 201
    body = uploaded.json()
    document_id = body["document_id"]
    assert body["display_name"] == "Redis Interview Notes"
    assert body["status"] == "ready"
    assert body["allowed_usage"] == ["question", "follow_up", "feedback"]

    assert CLIENT.get("/api/materials").json()["items"] == [body]
    disabled = CLIENT.patch(
        f"/api/materials/{document_id}",
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    enabled = CLIENT.patch(
        f"/api/materials/{document_id}",
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "ready"
    assert CLIENT.post(f"/api/materials/{document_id}/retry").status_code == 200
    assert CLIENT.delete(f"/api/materials/{document_id}").status_code == 200
    assert CLIENT.get("/api/materials").json() == {"items": []}


def test_failed_upload_retry_reuses_revision_and_never_reflects_provider_error():
    provider = FlakyEmbeddingProvider()
    bundle = _bundle(provider)
    _configure(bundle, enabled=True, ingest_enabled=True)

    failed = _upload(content=b"Redis retry content")
    assert failed.status_code == 201
    failed_body = failed.json()
    assert failed_body["status"] == "failed"
    assert failed_body["error_code"] == "embedding_unavailable"
    assert "synthetic_unavailable" not in failed.text
    document_id = failed_body["document_id"]
    revisions_before = bundle.store.list_revisions(
        owner_principal_id=OWNER_A,
        document_id=document_id,
    )

    retried = CLIENT.post(f"/api/materials/{document_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "ready"
    assert provider.calls == 2
    assert bundle.store.list_revisions(
        owner_principal_id=OWNER_A,
        document_id=document_id,
    ) == revisions_before


def test_public_document_response_is_an_exact_safe_projection():
    bundle = _bundle()
    _configure(bundle, enabled=True, ingest_enabled=True)
    raw_text = b"RAW_PRIVATE_MATERIAL_TEXT"
    uploaded = _upload(filename="private-original.txt", content=raw_text)
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert set(body) == {
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

    document = bundle.store.get_document(
        owner_principal_id=OWNER_A,
        document_id=body["document_id"],
    )
    revision = bundle.store.get_latest_revision(
        owner_principal_id=OWNER_A,
        document_id=body["document_id"],
    )
    rendered = uploaded.text
    for secret in (
        OWNER_A,
        document.active_revision_id,
        revision.document_revision_id,
        revision.content_sha256,
        revision.original_file_sha256,
        revision.embedding_identity,
        revision.extracted_text_ref,
        "private-original.txt",
        raw_text.decode(),
        "internal_stage",
        "Chunk",
        "Manifest",
    ):
        assert secret not in rendered


@pytest.mark.parametrize(
    "field_name",
    (
        "owner_principal_id",
        "principal_id",
        "document_revision_id",
        "content_sha256",
        "embedding_identity",
        "internal_stage",
        "corpus_version",
        "database_path",
    ),
)
def test_client_supplied_internal_patch_fields_are_rejected(field_name):
    bundle = _bundle()
    document = _ingest_direct(bundle)
    _configure(bundle, enabled=True, ingest_enabled=True)

    response = CLIENT.patch(
        f"/api/materials/{document.document_id}",
        json={"display_name": "Safe", field_name: "forged"},
    )
    assert response.status_code == 422


def test_client_supplied_internal_multipart_field_is_rejected():
    bundle = _bundle()
    _configure(bundle, enabled=True, ingest_enabled=True)

    response = _upload(data={"owner_principal_id": OWNER_B})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert bundle.store.list_documents(owner_principal_id=OWNER_A) == ()


def test_principal_b_cannot_enumerate_or_mutate_principal_a_document():
    bundle = _bundle()
    document = _ingest_direct(bundle)
    _configure(
        bundle,
        enabled=True,
        ingest_enabled=True,
        principal_id=OWNER_B,
    )

    assert CLIENT.get("/api/materials").json() == {"items": []}
    for response in (
        CLIENT.patch(
            f"/api/materials/{document.document_id}",
            json={"display_name": "Stolen"},
        ),
        CLIENT.post(f"/api/materials/{document.document_id}/retry"),
        CLIENT.delete(f"/api/materials/{document.document_id}"),
    ):
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "document_not_found"
        assert OWNER_A not in response.text
    assert bundle.store.get_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) == document


def test_missing_principal_fails_closed_even_for_delete():
    bundle = _bundle()
    document = _ingest_direct(bundle)
    _configure(bundle, enabled=True, ingest_enabled=True)
    app.dependency_overrides[get_principal_identity_resolver] = (
        NullPrincipalIdentityResolver
    )

    assert CLIENT.get("/api/materials").status_code == 404
    assert CLIENT.delete(f"/api/materials/{document.document_id}").status_code == 404
    assert bundle.store.get_document(
        owner_principal_id=OWNER_A,
        document_id=document.document_id,
    ) == document


def test_missing_postgres_materials_schema_fails_closed_without_details(
    monkeypatch,
):
    settings = UserMaterialsRuntimeSettings(enabled=True, ingest_enabled=True)
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="materials-test",
        principal_id=OWNER_A,
    )
    private_detail = "postgresql://secret@private-host/materials"

    def unavailable_service():
        raise PostgresSchemaNotReady(private_detail)

    app.dependency_overrides[get_user_materials_runtime_settings] = lambda: settings
    app.dependency_overrides[get_principal_identity_resolver] = lambda: resolver
    monkeypatch.setattr(
        "app.api.shared.dependencies._get_user_document_service",
        unavailable_service,
    )

    response = CLIENT.get("/api/materials")

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "PRODUCT_STORE_UNAVAILABLE"
    assert payload["message"] == "服务数据存储暂时不可用，请稍后重试。"
    assert payload["retryable"] is True
    assert payload["request_id"].startswith("req_")
    assert private_detail not in response.text
