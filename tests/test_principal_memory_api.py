from fastapi.testclient import TestClient

from app.main import app
from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
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
    monkeypatch.setattr(
        "app.api.routes.get_principal_identity_resolver",
        lambda: resolver,
    )
    monkeypatch.setattr(
        "app.api.routes.get_principal_memory_consent_store",
        lambda: consents,
    )
    client = TestClient(app)
    granted = client.post(
        "/api/runtime/principal-memory/consent",
        json={"allowed_purposes": ["fact_storage", "local_consume"]},
    )
    assert granted.status_code == 200

    response = client.delete("/api/runtime/principal-memory/consent")

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
    monkeypatch.setattr(
        "app.api.routes.get_principal_identity_resolver",
        lambda: resolver,
    )

    response = TestClient(app).get("/api/runtime/principal-memory/facts")

    assert response.status_code == 404
