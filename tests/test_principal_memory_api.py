from fastapi.testclient import TestClient

from app.main import app
from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.services.principal_memory_rights import (
    InMemoryPrincipalMemoryDeletionTombstoneStore,
    InMemoryPrincipalMemoryExportStore,
)
from app.services.principal_memory_safe_refs import InMemoryPrincipalMemorySafeRefStore
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from tests.test_in_memory_principal_memory import make_fact


class Sessions:
    def get(self, session_id):
        return {"session_id": session_id, "deletion_status": "active"}


def test_principal_memory_api_is_hidden_by_default(monkeypatch):
    monkeypatch.delenv(
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED", raising=False
    )
    assert TestClient(app).get("/api/runtime/principal-memory/facts").status_code == 404
    client = TestClient(app)
    for method, path, payload in (
        ("get", "/api/runtime/principal-memory/status", None),
        (
            "put",
            "/api/runtime/principal-memory/consent",
            {"allowed_purposes": ["fact_storage"]},
        ),
        ("post", "/api/runtime/principal-memory/disable", None),
        ("post", "/api/runtime/principal-memory/enable", None),
        (
            "post",
            "/api/runtime/principal-memory/facts",
            {
                "fact_type": "confirmed_skill",
                "normalized_value": {"confirmed_skill": "python"},
            },
        ),
        ("post", "/api/runtime/principal-memory/export", None),
        ("delete", "/api/runtime/principal-memory", None),
    ):
        kwargs = {"json": payload} if payload is not None else {}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 404


def test_trusted_local_fact_list_returns_no_internal_locators(monkeypatch):
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="principal-a",
        assurance="trusted_local",
    )
    facts = InMemoryPrincipalMemoryFactStore()
    fact = facts.create_proposal(make_fact())
    monkeypatch.setenv(
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED", "true"
    )
    monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", "true")
    monkeypatch.setattr(
        "app.api.routes.get_principal_identity_resolver", lambda: resolver
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_fact_store", lambda: facts
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_consent_store",
        lambda: InMemoryPrincipalMemoryConsentStore(),
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_control_store",
        lambda: InMemoryPrincipalMemoryControlStore(),
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_safe_ref_store",
        lambda: InMemoryPrincipalMemorySafeRefStore(),
    )
    monkeypatch.setattr("app.api.routes.get_session_store", lambda: Sessions())

    response = TestClient(app).get("/api/runtime/principal-memory/facts")

    assert response.status_code == 200
    rendered = response.text
    for forbidden in (
        fact.fact_id,
        fact.source_session_id,
        fact.source_excerpt_sha256,
        fact.source_manifest_sha256,
    ):
        assert forbidden not in rendered


def test_revoking_consent_does_not_delete_principal_facts(monkeypatch):
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="principal-a",
        assurance="trusted_local",
    )
    consents = InMemoryPrincipalMemoryConsentStore()
    monkeypatch.setenv(
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED",
        "true",
    )
    monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", "true")
    monkeypatch.setattr(
        "app.api.routes.get_principal_identity_resolver",
        lambda: resolver,
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_consent_store",
        lambda: consents,
    )
    client = TestClient(app)
    granted = client.put(
        "/api/runtime/principal-memory/consent",
        json={"allowed_purposes": ["fact_storage", "local_consume"]},
        headers={"x-local-memory-action": "1"},
    )
    assert granted.status_code == 200

    response = client.delete(
        "/api/runtime/principal-memory/consent",
        headers={"x-local-memory-action": "1"},
    )

    assert response.status_code == 200
    assert response.json() == {"revoked": True, "facts_retained": True}


def test_principal_memory_api_rejects_non_local_identity_assurance(monkeypatch):
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="principal-a",
        assurance="authenticated",
    )
    monkeypatch.setenv(
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED",
        "true",
    )
    monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", "true")
    monkeypatch.setattr(
        "app.api.routes.get_principal_identity_resolver",
        lambda: resolver,
    )

    response = TestClient(app).get("/api/runtime/principal-memory/facts")

    assert response.status_code == 404


def test_memory_center_api_full_local_workflow_and_forbidden_fields(monkeypatch):
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
    )
    facts = InMemoryPrincipalMemoryFactStore()
    consents = InMemoryPrincipalMemoryConsentStore()
    controls = InMemoryPrincipalMemoryControlStore()
    refs = InMemoryPrincipalMemorySafeRefStore()
    exports = InMemoryPrincipalMemoryExportStore()
    tombstones = InMemoryPrincipalMemoryDeletionTombstoneStore()
    monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", "true")
    monkeypatch.setenv(
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED",
        "true",
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_identity_resolver",
        lambda: resolver,
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_fact_store",
        lambda: facts,
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_consent_store",
        lambda: consents,
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_control_store",
        lambda: controls,
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_safe_ref_store",
        lambda: refs,
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_export_store",
        lambda: exports,
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_deletion_tombstone_store",
        lambda: tombstones,
    )
    monkeypatch.setattr("app.api.routes.get_session_store", lambda: Sessions())
    client = TestClient(app)
    mutation = {"x-local-memory-action": "1", "origin": "http://localhost:8000"}

    assert client.put(
        "/api/runtime/principal-memory/consent",
        json={"allowed_purposes": ["fact_storage", "read_shadow"]},
        headers=mutation,
    ).status_code == 200
    declared = client.post(
        "/api/runtime/principal-memory/facts",
        json={
            "fact_type": "declared_preference",
            "normalized_value": {"interview_language": "zh_hans"},
        },
        headers=mutation,
    )
    assert declared.status_code == 200
    listed = client.get("/api/runtime/principal-memory/facts")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["safe_ref"].startswith("pm-ref-")
    rendered = listed.text
    for forbidden in ("local-owner", "source_manifest", "source_excerpt", "fact_id"):
        assert forbidden not in rendered

    corrected = client.put(
        f"/api/runtime/principal-memory/facts/{item['safe_ref']}",
        json={
            "expected_version": item["version"],
            "normalized_value": {"interview_language": "en"},
        },
        headers=mutation,
    )
    assert corrected.status_code == 200
    stale = client.post(
        f"/api/runtime/principal-memory/facts/{item['safe_ref']}/revoke",
        json={"expected_version": item["version"]},
        headers=mutation,
    )
    assert stale.status_code == 409
    ignored = client.post(
        "/api/runtime/principal-memory/sessions/session-local/ignore",
        headers=mutation,
    )
    assert ignored.status_code == 200
    assert ignored.json()["session_ignored"] is True
    allowed = client.delete(
        "/api/runtime/principal-memory/sessions/session-local/ignore",
        headers=mutation,
    )
    assert allowed.status_code == 200
    assert allowed.json()["session_ignored"] is False
    assert client.post(
        "/api/runtime/principal-memory/disable",
        headers=mutation,
    ).json()["facts_retained"] is True
    status = client.get("/api/runtime/principal-memory/status")
    assert status.status_code == 200
    assert status.json()["global_enabled"] is False

    exported = client.post(
        "/api/runtime/principal-memory/export",
        headers=mutation,
    )
    assert exported.status_code == 200
    for forbidden in ("local-owner", "principal_id", "session_id", "fact_id"):
        assert forbidden not in exported.text

    deleted = client.delete(
        "/api/runtime/principal-memory",
        headers=mutation,
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "completed"
    assert facts.count_by_principal(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    ) == 0


def test_memory_center_mutations_require_local_header_and_origin(monkeypatch):
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
    )
    monkeypatch.setenv("MEMORY_LOCAL_PRINCIPAL_ENABLED", "true")
    monkeypatch.setenv(
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED",
        "true",
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_identity_resolver",
        lambda: resolver,
    )
    client = TestClient(app)

    assert client.post("/api/runtime/principal-memory/disable").status_code == 403
    assert client.post(
        "/api/runtime/principal-memory/disable",
        headers={
            "x-local-memory-action": "1",
            "origin": "https://attacker.example",
        },
    ).status_code == 403


def test_memory_center_openapi_contract_uses_safe_ref_routes():
    paths = app.openapi()["paths"]
    expected = {
        "/api/runtime/principal-memory/status": {"get"},
        "/api/runtime/principal-memory/consent": {"put", "delete"},
        "/api/runtime/principal-memory/disable": {"post"},
        "/api/runtime/principal-memory/enable": {"post"},
        "/api/runtime/principal-memory/facts": {"get", "post"},
        "/api/runtime/principal-memory/facts/{safe_ref}": {"put"},
        "/api/runtime/principal-memory/facts/{safe_ref}/confirm": {"post"},
        "/api/runtime/principal-memory/facts/{safe_ref}/reject": {"post"},
        "/api/runtime/principal-memory/facts/{safe_ref}/revoke": {"post"},
        "/api/runtime/principal-memory/sessions/{session_id}/ignore": {
            "post",
            "delete",
        },
        "/api/runtime/principal-memory/export": {"post"},
        "/api/runtime/principal-memory": {"delete"},
    }
    for path, methods in expected.items():
        assert path in paths
        assert methods.issubset(paths[path])
    for obsolete in (
        "/api/runtime/principal-memory/facts/confirm",
        "/api/runtime/principal-memory/facts/reject",
        "/api/runtime/principal-memory/facts/revoke",
    ):
        assert obsolete not in paths
