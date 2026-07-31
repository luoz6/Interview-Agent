from datetime import datetime, timezone

from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.memory_config import load_effective_memory_config
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)
from app.services.principal_memory_contracts import (
    CONSENT_POLICY_VERSION,
    TAXONOMY_VERSION,
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)
from app.services.principal_memory_lifecycle import PrincipalMemoryLifecycleService


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


class Sessions:
    def get(self, session_id):
        return {"session_id": session_id, "deletion_status": "active"}


def make_source_fact(excerpt_char):
    normalized = canonical_principal_fact({"confirmed_skill": "python"})
    identity = {
        "deployment_id": "single-tenant-local",
        "principal_id": "principal-life",
        "fact_type": "confirmed_skill",
        "normalized_fact": normalized,
        "source_manifest_sha256": "a" * 64,
        "source_excerpt_sha256": excerpt_char * 64,
        "consent_policy_version": CONSENT_POLICY_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
    }
    return PrincipalMemoryFact(
        fact_id=derive_principal_fact_id(**identity),
        **identity,
        confidence=0.9,
        authority="model_proposed",
        source_session_id="session-life",
        created_at=NOW,
    )


def build_service():
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "write_shadow",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
        }
    )
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local", principal_id="principal-life"
    )
    consent_store = InMemoryPrincipalMemoryConsentStore()
    consent_store.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="principal-life",
            policy_version=CONSENT_POLICY_VERSION,
            allowed_purposes=["proposal_write", "fact_storage", "read_shadow"],
            granted_at=NOW,
        )
    )
    facts = InMemoryPrincipalMemoryFactStore()
    service = PrincipalMemoryLifecycleService(
        identity_resolver=identity,
        consent_service=PrincipalMemoryConsentService(
            identity_resolver=identity,
            store=consent_store,
            policy_version=CONSENT_POLICY_VERSION,
        ),
        fact_store=facts,
        session_store=Sessions(),
        config=config,
        clock=lambda: NOW,
    )
    return service, facts


def test_confirm_and_same_fact_key_supersede_create_direct_predecessor_chain():
    service, facts = build_service()
    first = facts.create_proposal(make_source_fact("b"))
    confirmed = service.confirm(
        fact_type="confirmed_skill",
        normalized_fact=first.normalized_fact,
        expected_version=1,
    )
    assert confirmed["status"] == "active"

    second = facts.create_proposal(make_source_fact("c"))
    second_result = service.confirm(
        fact_type="confirmed_skill",
        normalized_fact=second.normalized_fact,
        expected_version=1,
    )
    assert second_result["status"] == "active"
    stored_first = facts.get(
        deployment_id=first.deployment_id,
        principal_id=first.principal_id,
        fact_id=first.fact_id,
    )
    stored_second = facts.get(
        deployment_id=second.deployment_id,
        principal_id=second.principal_id,
        fact_id=second.fact_id,
    )
    assert stored_first.status == "superseded"
    assert stored_second.supersedes_fact_id == stored_first.fact_id


def test_safe_list_excludes_internal_fact_and_source_locators():
    service, facts = build_service()
    fact = facts.create_proposal(make_source_fact("d"))
    payload = service.list_safe()[0]
    rendered = repr(payload)
    assert payload["status"] == "proposed"
    for forbidden in (
        fact.fact_id,
        fact.source_session_id,
        fact.source_excerpt_sha256,
        fact.source_manifest_sha256,
    ):
        assert forbidden not in rendered
