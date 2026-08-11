from datetime import datetime, timedelta, timezone

from app.adapters.memory.principal_memory import (
    InMemoryPrincipalMemoryFactStore,
    transition_fact,
)
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.services.in_memory_principal_memory_control import (
    InMemoryPrincipalMemoryControlStore,
)
from app.runtime.config.memory import load_effective_memory_config
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)
from app.services.principal_memory_control import PrincipalMemoryControlService
from app.domain.memory.contracts import (
    CONSENT_POLICY_VERSION,
    TAXONOMY_VERSION,
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
    derive_principal_fact_taxonomy_keys,
)
from app.services.principal_memory_retrieval import PrincipalMemoryRetriever
from app.services.principal_memory_rights import (
    InMemoryPrincipalMemoryDeletionTombstoneStore,
)


FACT_NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)
RIGHTS_NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


class PrincipalMemorySessions:
    def __init__(self, deleted=None):
        self.deleted = set(deleted or ())

    def get(self, session_id):
        return {
            "session_id": session_id,
            "deletion_status": "deleted" if session_id in self.deleted else "active",
        }


def make_fact(*, principal_id="principal-a", session_id="session-a", value="python"):
    normalized = canonical_principal_fact({"confirmed_skill": value})
    identity = {
        "deployment_id": "single-tenant-local",
        "principal_id": principal_id,
        "fact_type": "confirmed_skill",
        "normalized_fact": normalized,
        "source_manifest_sha256": "a" * 64,
        "source_excerpt_sha256": "b" * 64,
        "consent_policy_version": CONSENT_POLICY_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
    }
    return PrincipalMemoryFact(
        fact_id=derive_principal_fact_id(**identity),
        **identity,
        confidence=0.9,
        authority="model_proposed",
        source_session_id=session_id,
        created_at=FACT_NOW,
    )


def make_active_fact(
    store,
    *,
    fact_type,
    value,
    source="session-shadow",
    digest="b",
    unsafe_seed=False,
):
    normalized = canonical_principal_fact(value)
    identity = {
        "deployment_id": "single-tenant-local",
        "principal_id": "principal-shadow",
        "fact_type": fact_type,
        "normalized_fact": normalized,
        "source_manifest_sha256": "a" * 64,
        "source_excerpt_sha256": digest * 64,
        "consent_policy_version": CONSENT_POLICY_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
    }
    proposal = PrincipalMemoryFact(
        fact_id=derive_principal_fact_id(**identity),
        **identity,
        confidence=0.9,
        authority="model_proposed",
        source_session_id=source,
        created_at=FACT_NOW,
    )
    store.create_proposal(proposal)
    if unsafe_seed:
        active = transition_fact(
            proposal,
            expected_version=1,
            target_status="active",
            now=FACT_NOW,
            expires_at=FACT_NOW + timedelta(days=365),
        )
        store._facts[(
            active.deployment_id,
            active.principal_id,
            active.fact_id,
        )] = active
        return active
    _, exclusive_scope_key = derive_principal_fact_taxonomy_keys(
        fact_type=fact_type,
        normalized_fact=normalized,
    )
    return store.activate_proposal(
        deployment_id=proposal.deployment_id,
        principal_id=proposal.principal_id,
        fact_id=proposal.fact_id,
        expected_version=1,
        exclusive_key=exclusive_scope_key,
        now=FACT_NOW,
        expires_at=FACT_NOW + timedelta(days=365),
    )


def build_retriever(*, sessions=None):
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "read_shadow",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
        }
    )
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local", principal_id="principal-shadow"
    )
    consent_store = InMemoryPrincipalMemoryConsentStore()
    consent_store.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="principal-shadow",
            policy_version=CONSENT_POLICY_VERSION,
            allowed_purposes=["read_shadow"],
            granted_at=FACT_NOW,
        )
    )
    facts = InMemoryPrincipalMemoryFactStore()
    control_service = PrincipalMemoryControlService(
        identity_resolver=resolver,
        store=InMemoryPrincipalMemoryControlStore(),
        clock=lambda: FACT_NOW,
    )
    retriever = PrincipalMemoryRetriever(
        fact_store=facts,
        consent_service=PrincipalMemoryConsentService(
            identity_resolver=resolver,
            store=consent_store,
            policy_version=CONSENT_POLICY_VERSION,
            control_service=control_service,
        ),
        identity_resolver=resolver,
        session_store=sessions or PrincipalMemorySessions(),
        config=config,
    )
    return retriever, facts, consent_store


def completed_tombstone_for(principal_id):
    store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: RIGHTS_NOW)
    return store.mark(
        store.record_requested(
            deployment_id="single-tenant-local",
            principal_id=principal_id,
        ),
        status="completed",
    )


def completed_tombstone():
    return completed_tombstone_for("local-owner")
