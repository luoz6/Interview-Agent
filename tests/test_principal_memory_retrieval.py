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
from app.services.principal_memory_control import PrincipalMemoryControlService
from app.services.principal_memory_contracts import (
    CONSENT_POLICY_VERSION,
    TAXONOMY_VERSION,
    PrincipalMemoryFact,
    canonical_principal_fact,
    derive_principal_fact_id,
)
from app.services.principal_memory_retrieval import PrincipalMemoryRetriever


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


class Sessions:
    def __init__(self, deleted=None):
        self.deleted = set(deleted or ())

    def get(self, session_id):
        return {
            "session_id": session_id,
            "deletion_status": "deleted" if session_id in self.deleted else "active",
        }


def make_active_fact(store, *, fact_type, value, source="session-shadow", digest="b"):
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
        created_at=NOW,
    )
    store.create_proposal(proposal)
    return store.transition(
        deployment_id=proposal.deployment_id,
        principal_id=proposal.principal_id,
        fact_id=proposal.fact_id,
        expected_version=1,
        target_status="active",
        now=NOW,
        expires_at=NOW + timedelta(days=365),
    )


def build_retriever(*, sessions=None):
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "read_shadow",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
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
            granted_at=NOW,
        )
    )
    facts = InMemoryPrincipalMemoryFactStore()
    control_service = PrincipalMemoryControlService(
        identity_resolver=resolver,
        store=InMemoryPrincipalMemoryControlStore(),
        clock=lambda: NOW,
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
        session_store=sessions or Sessions(),
        config=config,
    )
    return retriever, facts, consent_store


def test_bounded_exact_taxonomy_selection_and_deleted_source_filtering():
    sessions = Sessions(deleted={"session-deleted"})
    retriever, facts, _ = build_retriever(sessions=sessions)
    make_active_fact(
        facts,
        fact_type="accessibility_preference",
        value={"accessibility_preference": "extra_time"},
        digest="b",
    )
    make_active_fact(
        facts,
        fact_type="confirmed_skill",
        value={"confirmed_skill": "python"},
        digest="c",
    )
    make_active_fact(
        facts,
        fact_type="confirmed_skill",
        value={"confirmed_skill": "kafka"},
        source="session-deleted",
        digest="d",
    )

    result = retriever.select(
        current_tags={"python"}, role_tags={"backend"}, now=NOW
    )

    assert len(result.selected) == 2
    assert all(fact.source_session_id != "session-deleted" for fact in result.selected)
    assert result.estimated_tokens <= retriever.config.long_term.max_shadow_tokens


def test_conflicting_exclusive_values_are_both_excluded():
    retriever, facts, _ = build_retriever()
    make_active_fact(
        facts,
        fact_type="declared_preference",
        value={"interview_language": "zh_hans"},
        digest="e",
    )
    make_active_fact(
        facts,
        fact_type="declared_preference",
        value={"interview_language": "en"},
        digest="f",
    )
    result = retriever.select(current_tags=set(), role_tags=set(), now=NOW)
    assert result.conflict_count == 1
    assert result.selected == ()


def test_revoked_consent_disables_read_shadow_immediately():
    retriever, facts, consent_store = build_retriever()
    make_active_fact(
        facts,
        fact_type="confirmed_skill",
        value={"confirmed_skill": "python"},
    )
    consent_store.revoke(
        deployment_id="single-tenant-local",
        principal_id="principal-shadow",
        revoked_at=NOW,
    )
    assert retriever.select(
        current_tags={"python"}, role_tags=set(), now=NOW
    ).selected == ()


def test_session_ignore_blocks_only_the_current_session():
    retriever, facts, _ = build_retriever()
    make_active_fact(
        facts,
        fact_type="confirmed_skill",
        value={"confirmed_skill": "python"},
    )
    retriever.consent_service.control_service.set_session_ignored(
        "session-current",
        True,
    )

    ignored = retriever.select(
        current_tags={"python"},
        role_tags=set(),
        now=NOW,
        session_id="session-current",
    )
    allowed = retriever.select(
        current_tags={"python"},
        role_tags=set(),
        now=NOW,
        session_id="session-other",
    )

    assert ignored.selected == ()
    assert len(allowed.selected) == 1
