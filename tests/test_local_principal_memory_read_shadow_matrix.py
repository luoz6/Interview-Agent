from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.in_memory_principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.services.memory_config import load_effective_memory_config
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)
from app.services.principal_memory_contracts import (
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)
from app.services.principal_memory_control import PrincipalMemoryControlService
from app.services.principal_memory_proposals import build_proposal_event_if_eligible
from app.services.principal_memory_retrieval import PrincipalMemoryRetriever
from app.services.principal_memory_shadow import (
    PrincipalMemoryShadowService,
    canonical_provider_context_digest,
)


NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


class Sessions:
    def get(self, session_id):
        return {"session_id": session_id, "deletion_status": "active"}


def build_local_read_shadow():
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "read_shadow",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
            "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
        }
    )
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        assurance="trusted_local",
    )
    controls = PrincipalMemoryControlService(
        identity_resolver=resolver,
        store=InMemoryPrincipalMemoryControlStore(),
        clock=lambda: NOW,
    )
    consent_store = InMemoryPrincipalMemoryConsentStore()
    consent_store.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            policy_version=config.long_term.consent_policy_version,
            allowed_purposes=["read_shadow"],
            granted_at=NOW,
        )
    )
    consent = PrincipalMemoryConsentService(
        identity_resolver=resolver,
        store=consent_store,
        policy_version=config.long_term.consent_policy_version,
        control_service=controls,
    )
    facts = InMemoryPrincipalMemoryFactStore()
    normalized = canonical_principal_fact({"confirmed_skill": "python"})
    identity_values = {
        "deployment_id": "single-tenant-local",
        "principal_id": "local-owner",
        "fact_type": "confirmed_skill",
        "normalized_fact": normalized,
        "source_manifest_sha256": "a" * 64,
        "source_excerpt_sha256": "b" * 64,
        "consent_policy_version": config.long_term.consent_policy_version,
        "taxonomy_version": config.long_term.taxonomy_version,
    }
    fact = PrincipalMemoryFact(
        fact_id=derive_principal_fact_id(**identity_values),
        **identity_values,
        confidence=1.0,
        authority="user_declared",
        status="active",
        source_session_id="local-user-declaration",
        user_confirmed=True,
        created_at=NOW,
        confirmed_at=NOW,
        expires_at=NOW + timedelta(days=180),
    )
    facts.declare_active(fact, exclusive_key=None, now=NOW)
    retriever = PrincipalMemoryRetriever(
        fact_store=facts,
        consent_service=consent,
        identity_resolver=resolver,
        session_store=Sessions(),
        config=config,
    )
    return config, resolver, consent, controls, consent_store, facts, retriever


def test_manual_fact_read_shadow_requires_no_write_shadow_or_proposal():
    config, resolver, consent, _, _, _, retriever = build_local_read_shadow()
    state = {
        "session_id": "session-read-only",
        "status": "finished",
        "deletion_status": "active",
        "state_version": 3,
    }

    assert config.long_term.write_shadow_enabled is False
    assert build_proposal_event_if_eligible(
        state=state,
        config=config,
        identity_resolver=resolver,
        consent_service=consent,
        clock=lambda: NOW,
    ) is None
    selected = retriever.select(
        current_tags={"python"},
        role_tags={"backend"},
        now=NOW,
        session_id=state["session_id"],
    )
    assert len(selected.selected) == 1
    assert selected.selected[0].authority == "user_declared"


def test_three_hundred_case_read_shadow_matrix_preserves_provider_digest():
    _, _, _, controls, consent_store, _, retriever = build_local_read_shadow()
    shadow = PrincipalMemoryShadowService(retriever=retriever)
    languages = ("English", "简体中文", "mixed 中英")

    for index in range(300):
        session_id = f"synthetic-session-{index}"
        context = [
            {"role": "system", "content": f"Stable instruction {index % 11}"},
            {
                "role": "candidate",
                "content": f"{languages[index % 3]} response {index}",
            },
        ]
        before = canonical_provider_context_digest(context)
        if index % 50 == 0:
            controls.set_session_ignored(session_id, True)
        result = shadow.observe(
            provider_context=context,
            current_tags={"python"} if index % 2 == 0 else {"kafka"},
            role_tags={"backend"},
            now=NOW,
            session_id=session_id,
        )
        after = canonical_provider_context_digest(context)

        assert after == before
        assert result.provider_context is context
        assert "confirmed_skill" not in repr(context)
        assert result.would_select_count in {0, 1}

    consent_store.revoke(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
        revoked_at=NOW,
    )
    assert retriever.select(
        current_tags={"python"},
        role_tags=set(),
        now=NOW,
        session_id="post-revoke",
    ).selected == ()
