from fastapi.testclient import TestClient

from app.main import app
from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
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
        deployment_id="single-tenant-local", principal_id="principal-a"
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
