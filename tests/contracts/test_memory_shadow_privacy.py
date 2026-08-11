from datetime import datetime, timezone
from hashlib import sha256
from inspect import signature
from pathlib import Path

import pytest

from app.adapters.memory.principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.in_memory_principal_memory_consent import (
    InMemoryPrincipalMemoryConsentStore,
)
from app.runtime.config.memory import load_effective_memory_config
from app.services.principal_identity import (
    ExplicitPrincipalIdentityResolver,
    NullPrincipalIdentityResolver,
)
from app.services.principal_memory_consent import (
    PrincipalMemoryConsent,
    PrincipalMemoryConsentService,
)
from app.services.principal_memory_extractor import StructuredPrincipalMemoryExtractor
from app.services.principal_memory_proposals import build_proposal_event_if_eligible
from app.services.principal_memory_tasks import PrincipalMemoryProposalProcessor
from scripts.memory_shadow_security_review import PROMPT_ATTACK_CASES
from scripts.memory_shadow_security_review import audit_observation_artifacts
from tests.principal_memory_fixtures import make_fact


NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)


class Sessions:
    def __init__(self, state):
        self.state = state

    def get(self, session_id):
        assert session_id == self.state["session_id"]
        return self.state


def config():
    return load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "write_shadow",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
        }
    )


def corpus_digest():
    digest = sha256()
    for root in (Path("app/data/knowledge"), Path("app/data/knowledge_v2")):
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            digest.update(path.as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_identity_is_explicit_or_absent_and_never_inferred_from_request_material():
    assert NullPrincipalIdentityResolver().resolve() is None
    resolver = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="explicit-subject",
    )
    identity = resolver.resolve()
    assert identity.deployment_id == "single-tenant-local"
    assert identity.principal_id == "explicit-subject"

    parameters = set(signature(ExplicitPrincipalIdentityResolver).parameters)
    for forbidden_parameter in (
        "resume_text",
        "email",
        "phone",
        "ip_address",
        "user_agent",
        "browser_id",
        "embedding",
    ):
        assert forbidden_parameter not in parameters


def test_consent_purposes_are_separate_and_old_policy_fails_closed():
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local", principal_id="consent-subject"
    )
    store = InMemoryPrincipalMemoryConsentStore()
    store.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="consent-subject",
            policy_version="principal-memory-consent-v1",
            allowed_purposes=["proposal_write"],
            granted_at=NOW,
        )
    )
    current = PrincipalMemoryConsentService(
        identity_resolver=identity,
        store=store,
        policy_version="principal-memory-consent-v1",
    )
    future = PrincipalMemoryConsentService(
        identity_resolver=identity,
        store=store,
        policy_version="principal-memory-consent-v2",
    )

    assert current.authorize("proposal_write") is True
    assert current.authorize("read_shadow") is False
    assert current.authorize("fact_storage") is False
    assert future.authorize("proposal_write") is False


def test_same_taxonomy_value_is_never_shared_across_principals():
    store = InMemoryPrincipalMemoryFactStore()
    first = store.create_proposal(
        make_fact(principal_id="principal-one", session_id="session-one")
    )
    second = store.create_proposal(
        make_fact(principal_id="principal-two", session_id="session-two")
    )

    assert first.fact_id != second.fact_id
    assert store.list_by_principal(
        deployment_id="single-tenant-local", principal_id="principal-one", limit=10
    ) == [first]
    assert store.list_by_principal(
        deployment_id="single-tenant-local", principal_id="principal-two", limit=10
    ) == [second]


def test_prompt_injection_matrix_can_only_create_unconfirmed_taxonomy_proposals():
    before = corpus_digest()
    cfg = config()
    for index, attack in enumerate(PROMPT_ATTACK_CASES):
        session_id = f"synthetic-attack-session-{index}"
        principal_id = f"synthetic-attack-principal-{index}"
        state = {
            "session_id": session_id,
            "status": "finished",
            "deletion_status": "active",
            "state_version": 4,
            "messages": [
                {
                    "message_id": "message-1",
                    "question_id": "q1",
                    "role": "candidate",
                    "content": attack,
                }
            ],
        }
        identity = ExplicitPrincipalIdentityResolver(
            deployment_id="single-tenant-local", principal_id=principal_id
        )
        consents = InMemoryPrincipalMemoryConsentStore()
        consents.grant(
            PrincipalMemoryConsent(
                deployment_id="single-tenant-local",
                principal_id=principal_id,
                policy_version=cfg.long_term.consent_policy_version,
                allowed_purposes=["proposal_write"],
                granted_at=NOW,
            )
        )
        consent = PrincipalMemoryConsentService(
            identity_resolver=identity,
            store=consents,
            policy_version=cfg.long_term.consent_policy_version,
        )
        facts = InMemoryPrincipalMemoryFactStore()
        processor = PrincipalMemoryProposalProcessor(
            session_store=Sessions(state),
            identity_resolver=identity,
            consent_service=consent,
            fact_store=facts,
            extractor=StructuredPrincipalMemoryExtractor(
                lambda **kwargs: [
                    {
                        "fact_type": "confirmed_skill",
                        "fact": {"confirmed_skill": "python"},
                        "confidence": 0.9,
                        "exact_excerpt": attack,
                        "source_message_id": "message-1",
                        "source_question_id": "q1",
                        "direct_user_statement": True,
                    }
                ]
            ),
            config=cfg,
            clock=lambda: NOW,
        )
        event = build_proposal_event_if_eligible(
            state=state,
            config=cfg,
            identity_resolver=identity,
            consent_service=consent,
            clock=lambda: NOW,
        )

        assert processor.consume(event.model_dump())["count"] == 1
        stored = facts.list_by_principal(
            deployment_id="single-tenant-local",
            principal_id=principal_id,
            limit=10,
        )
        assert len(stored) == 1
        assert stored[0].status == "proposed"
        assert stored[0].user_confirmed is False
        assert stored[0].authority == "model_proposed"

    assert corpus_digest() == before


def test_no_consent_means_no_prompt_injection_event_is_created():
    cfg = config()
    state = {
        "session_id": "synthetic-no-consent",
        "status": "finished",
        "deletion_status": "active",
        "state_version": 1,
        "messages": [],
    }
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local", principal_id="no-consent"
    )
    consent = PrincipalMemoryConsentService(
        identity_resolver=identity,
        store=InMemoryPrincipalMemoryConsentStore(),
        policy_version=cfg.long_term.consent_policy_version,
    )
    assert (
        build_proposal_event_if_eligible(
            state=state,
            config=cfg,
            identity_resolver=identity,
            consent_service=consent,
            clock=lambda: NOW,
        )
        is None
    )


def test_extractor_rejects_model_attempt_to_set_active_status_or_other_principal():
    extractor = StructuredPrincipalMemoryExtractor(
        lambda **kwargs: [
            {
                "fact_type": "confirmed_skill",
                "fact": {"confirmed_skill": "python"},
                "confidence": 1.0,
                "exact_excerpt": "synthetic",
                "source_message_id": "message-1",
                "status": "active",
                "principal_id": "other-subject",
            }
        ]
    )
    with pytest.raises(Exception):
        extractor.extract(messages=[], max_proposals=1)


def test_artifact_audit_counts_violations_without_returning_private_content(tmp_path):
    safe = tmp_path / "safe-observation.json"
    safe.write_text('{"sample_count":300}', encoding="utf-8")
    private = tmp_path / "private-evidence.json"
    private.write_text('{"principal_id":"do-not-return"}', encoding="utf-8")
    dsn = tmp_path / "unsafe-status.md"
    dsn.write_text("postgresql://user:secret@host/database", encoding="utf-8")

    result = audit_observation_artifacts([safe, private, dsn])

    assert result == {"artifacts_audited": 3, "artifact_violations": 2}
    assert "do-not-return" not in str(result)
