from datetime import datetime, timezone

from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.memory_config import load_effective_memory_config
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)
from app.services.principal_memory_proposals import (
    build_proposal_event_if_eligible,
    derive_principal_memory_effect_id,
)


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


def _setup():
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "write_shadow",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
        }
    )
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="principal-proposal",
    )
    store = InMemoryPrincipalMemoryConsentStore()
    store.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="principal-proposal",
            policy_version=config.long_term.consent_policy_version,
            allowed_purposes=["proposal_write", "fact_storage"],
            granted_at=NOW,
        )
    )
    consent = PrincipalMemoryConsentService(
        identity_resolver=identity,
        store=store,
        policy_version=config.long_term.consent_policy_version,
    )
    return config, identity, store, consent


def test_proposal_event_is_deterministic_opaque_and_safety_gated():
    config, identity, _, consent = _setup()
    state = {
        "session_id": "session-source",
        "status": "finished",
        "deletion_status": "active",
        "state_version": 9,
    }
    event = build_proposal_event_if_eligible(
        state=state,
        config=config,
        identity_resolver=identity,
        consent_service=consent,
        clock=lambda: NOW,
    )
    assert event.effect_id == derive_principal_memory_effect_id(
        deployment_id="single-tenant-local",
        principal_id="principal-proposal",
        session_id="session-source",
        source_state_version=9,
        consent_policy_version=config.long_term.consent_policy_version,
    )
    assert "resume" not in repr(event.model_dump()).casefold()
    assert "answer" not in repr(event.model_dump()).casefold()

    assert build_proposal_event_if_eligible(
        state={**state, "deletion_status": "deleting"},
        config=config,
        identity_resolver=identity,
        consent_service=consent,
    ) is None


def test_disabled_mode_never_enqueues_even_with_identity_and_consent():
    _, identity, _, consent = _setup()
    assert build_proposal_event_if_eligible(
        state={"session_id": "s", "status": "finished", "state_version": 1},
        config=load_effective_memory_config({}),
        identity_resolver=identity,
        consent_service=consent,
    ) is None
