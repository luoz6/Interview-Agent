from datetime import datetime, timezone

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
from app.services.principal_memory_extractor import StructuredPrincipalMemoryExtractor
from app.services.principal_memory_proposals import build_proposal_event_if_eligible
from app.services.principal_memory_tasks import PrincipalMemoryProposalProcessor


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


class SessionStore:
    def __init__(self, state):
        self.state = state

    def get(self, session_id):
        assert session_id == self.state["session_id"]
        return self.state


def _processor(candidates, *, max_proposals=8, ignored=False):
    config = load_effective_memory_config(
        {
            "MEMORY_LONG_TERM_MODE": "write_shadow",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_MAX_PROPOSALS_PER_SESSION": str(max_proposals),
        }
    )
    identity = ExplicitPrincipalIdentityResolver(
        deployment_id="single-tenant-local",
        principal_id="principal-task",
    )
    consent_store = InMemoryPrincipalMemoryConsentStore()
    consent_store.grant(
        PrincipalMemoryConsent(
            deployment_id="single-tenant-local",
            principal_id="principal-task",
            policy_version=config.long_term.consent_policy_version,
            allowed_purposes=["proposal_write", "fact_storage", "read_shadow"],
            granted_at=NOW,
        )
    )
    control_service = PrincipalMemoryControlService(
        identity_resolver=identity,
        store=InMemoryPrincipalMemoryControlStore(),
        clock=lambda: NOW,
    )
    if ignored:
        control_service.set_session_ignored("session-task", True)
    consent = PrincipalMemoryConsentService(
        identity_resolver=identity,
        store=consent_store,
        policy_version=config.long_term.consent_policy_version,
        control_service=control_service,
    )
    state = {
        "session_id": "session-task",
        "status": "finished",
        "deletion_status": "active",
        "state_version": 4,
        "messages": [
            {
                "message_id": "m1",
                "question_id": "q1",
                "role": "candidate",
                "content": "I explicitly use Python and want to learn Kafka.",
            }
        ],
    }
    facts = InMemoryPrincipalMemoryFactStore()
    processor = PrincipalMemoryProposalProcessor(
        session_store=SessionStore(state),
        identity_resolver=identity,
        consent_service=consent,
        fact_store=facts,
        extractor=StructuredPrincipalMemoryExtractor(lambda **kwargs: candidates),
        config=config,
        clock=lambda: NOW,
    )
    event = build_proposal_event_if_eligible(
        state=state,
        config=config,
        identity_resolver=identity,
        consent_service=consent,
        clock=lambda: NOW,
    )
    return processor, facts, event, consent_store


def test_processor_validates_exact_excerpt_dedupes_replay_and_never_activates():
    candidate = {
        "fact_type": "confirmed_skill",
        "fact": {"confirmed_skill": "python"},
        "confidence": 1.0,
        "exact_excerpt": "explicitly use Python",
        "source_message_id": "m1",
        "source_question_id": "q1",
    }
    processor, facts, event, _ = _processor([candidate])

    assert processor.consume(event.model_dump()) == {
        "status": "completed", "reason": None, "count": 1
    }
    assert processor.consume(event.model_dump())["count"] == 1
    stored = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-task",
        limit=10,
    )
    assert len(stored) == 1
    assert stored[0].status == "proposed"
    assert stored[0].user_confirmed is False
    assert not hasattr(stored[0], "exact_excerpt")


def test_processor_rejects_ungrounded_and_inferred_accessibility_candidates():
    candidates = [
        {
            "fact_type": "confirmed_skill",
            "fact": {"confirmed_skill": "python"},
            "confidence": 0.9,
            "exact_excerpt": "fabricated excerpt",
            "source_message_id": "m1",
        },
        {
            "fact_type": "accessibility_preference",
            "fact": {"accessibility_preference": "extra_time"},
            "confidence": 0.9,
            "exact_excerpt": "explicitly use Python",
            "source_message_id": "m1",
            "direct_user_statement": False
        }
    ]
    processor, facts, event, _ = _processor(candidates)
    assert processor.consume(event.model_dump())["count"] == 0
    assert facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-task",
        limit=10,
    ) == []


def test_consent_revoked_after_enqueue_cancels_execution():
    candidate = {
        "fact_type": "confirmed_skill",
        "fact": {"confirmed_skill": "python"},
        "confidence": 0.9,
        "exact_excerpt": "explicitly use Python",
        "source_message_id": "m1",
    }
    processor, _, event, consent_store = _processor([candidate])
    consent_store.revoke(
        deployment_id="single-tenant-local",
        principal_id="principal-task",
        revoked_at=NOW,
    )
    result = processor.consume(event.model_dump())
    assert result["status"] == "cancelled"
    assert result["reason"] == "consent_unavailable"


def test_session_ignore_blocks_enqueue_and_cancels_already_enqueued_work():
    candidate = {
        "fact_type": "confirmed_skill",
        "fact": {"confirmed_skill": "python"},
        "confidence": 0.9,
        "exact_excerpt": "explicitly use Python",
        "source_message_id": "m1",
    }
    _, _, ignored_event, _ = _processor([candidate], ignored=True)
    assert ignored_event is None

    processor, facts, event, _ = _processor([candidate])
    processor.consent_service.control_service.set_session_ignored(
        "session-task",
        True,
    )

    result = processor.consume(event.model_dump())

    assert result == {
        "status": "cancelled",
        "reason": "consent_unavailable",
        "count": 0,
    }
    assert facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-task",
        limit=10,
    ) == []
