from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

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


def make_source_fact(
    excerpt_char,
    *,
    fact_type="confirmed_skill",
    value=None,
):
    value = value or {"confirmed_skill": "python"}
    normalized = canonical_principal_fact(value)
    identity = {
        "deployment_id": "single-tenant-local",
        "principal_id": "principal-life",
        "fact_type": fact_type,
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
        fact_id=first.fact_id,
        expected_version=1,
    )
    assert confirmed["status"] == "active"

    second = facts.create_proposal(make_source_fact("c"))
    second_result = service.confirm(
        fact_id=second.fact_id,
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


def test_exact_fact_id_controls_same_value_different_source_proposals():
    service, facts = build_service()
    first = facts.create_proposal(make_source_fact("7"))
    second = facts.create_proposal(make_source_fact("8"))

    service.confirm(fact_id=second.fact_id, expected_version=1)

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
    assert stored_first.status == "proposed"
    assert stored_second.status == "active"
    service.reject(fact_id=first.fact_id, expected_version=1)
    assert facts.get(
        deployment_id=first.deployment_id,
        principal_id=first.principal_id,
        fact_id=first.fact_id,
    ).status == "rejected"


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


def test_direct_user_declaration_activates_without_model_proposal():
    service, facts = build_service()

    payload = service.declare(
        fact_type="declared_preference",
        normalized_fact=canonical_principal_fact(
            {"interview_language": "zh_hans"}
        ),
    )

    assert payload["status"] == "active"
    stored = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-life",
        limit=10,
    )
    assert len(stored) == 1
    assert stored[0].authority == "user_declared"
    assert stored[0].user_confirmed is True
    assert stored[0].source_session_id == "local-user-declaration"
    assert stored[0].expires_at == NOW + timedelta(days=180)


def test_direct_declaration_rejects_noncanonical_or_unapproved_values():
    service, _ = build_service()

    with pytest.raises(ValueError, match="approved taxonomy"):
        service.declare(
            fact_type="declared_preference",
            normalized_fact='{"interview_language":"my private language"}',
        )
    with pytest.raises(ValueError, match="canonical JSON"):
        service.declare(
            fact_type="declared_preference",
            normalized_fact='{ "interview_language": "en" }',
        )


def test_exclusive_correction_supersedes_old_value_atomically():
    service, facts = build_service()
    service.declare(
        fact_type="declared_preference",
        normalized_fact=canonical_principal_fact(
            {"interview_language": "zh_hans"}
        ),
    )

    corrected = service.declare(
        fact_type="declared_preference",
        normalized_fact=canonical_principal_fact(
            {"interview_language": "en"}
        ),
    )

    assert corrected["status"] == "active"
    stored = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-life",
        limit=10,
        include_terminal=True,
    )
    assert [fact.status for fact in stored].count("active") == 1
    assert [fact.status for fact in stored].count("superseded") == 1
    active = next(fact for fact in stored if fact.status == "active")
    predecessor = next(fact for fact in stored if fact.status == "superseded")
    assert active.supersedes_fact_id == predecessor.fact_id


def test_concurrent_exclusive_corrections_leave_at_most_one_active_value():
    service, facts = build_service()
    values = ["zh_hans", "en", "mixed"] * 4

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(
            executor.map(
                lambda value: service.declare(
                    fact_type="declared_preference",
                    normalized_fact=canonical_principal_fact(
                        {"interview_language": value}
                    ),
                ),
                values,
            )
        )

    stored = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-life",
        limit=100,
        include_terminal=True,
    )
    active = [fact for fact in stored if fact.status == "active"]
    assert len(active) == 1


def test_concurrent_edits_with_one_expected_predecessor_allow_one_winner():
    service, facts = build_service()
    service.declare(
        fact_type="declared_preference",
        normalized_fact=canonical_principal_fact(
            {"interview_language": "zh_hans"}
        ),
    )
    predecessor = facts.list_shadow_eligible(
        deployment_id="single-tenant-local",
        principal_id="principal-life",
        now=NOW,
        limit=1,
    )[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                service.declare,
                fact_type="declared_preference",
                normalized_fact=canonical_principal_fact(
                    {"interview_language": value}
                ),
                expected_predecessor_fact_id=predecessor.fact_id,
                expected_predecessor_version=predecessor.version,
            )
            for value in ("en", "mixed")
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result()["status"])
            except RuntimeError:
                outcomes.append("conflict")

    assert sorted(outcomes) == ["active", "conflict"]
    stored = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-life",
        limit=100,
        include_terminal=True,
    )
    assert [fact.status for fact in stored].count("active") == 1


def test_nonexclusive_direct_declarations_coexist():
    service, facts = build_service()
    for value in ("python", "kafka"):
        service.declare(
            fact_type="confirmed_skill",
            normalized_fact=canonical_principal_fact(
                {"confirmed_skill": value}
            ),
        )

    active = facts.list_shadow_eligible(
        deployment_id="single-tenant-local",
        principal_id="principal-life",
        now=NOW,
        limit=10,
    )
    assert len(active) == 2


def test_model_confirmation_and_direct_correction_share_one_atomic_key():
    service, facts = build_service()
    proposal = facts.create_proposal(
        make_source_fact(
            "e",
            fact_type="declared_preference",
            value={"interview_language": "en"},
        )
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                service.confirm,
                fact_id=proposal.fact_id,
                expected_version=1,
            ),
            executor.submit(
                service.declare,
                fact_type="declared_preference",
                normalized_fact=canonical_principal_fact(
                    {"interview_language": "mixed"}
                ),
            ),
        ]
        for future in futures:
            future.result()

    stored = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-life",
        limit=100,
        include_terminal=True,
    )
    assert [fact.status for fact in stored].count("active") == 1


def test_expire_due_applies_seven_day_proposal_boundary():
    service, facts = build_service()
    due = make_source_fact("f").model_copy(
        update={"created_at": NOW - timedelta(days=7)}
    )
    facts.create_proposal(due)

    assert service.expire_due(limit=1) == 1
    assert facts.get(
        deployment_id=due.deployment_id,
        principal_id=due.principal_id,
        fact_id=due.fact_id,
    ).status == "expired"


def test_source_session_deletion_removes_all_source_bound_facts_only():
    service, facts = build_service()
    proposal = facts.create_proposal(make_source_fact("1"))
    service.confirm(
        fact_id=proposal.fact_id,
        expected_version=1,
    )
    service.declare(
        fact_type="learning_goal",
        normalized_fact=canonical_principal_fact({"learning_goal": "kafka"}),
    )

    assert facts.purge_by_session("session-life") == 1
    remaining = facts.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-life",
        limit=10,
        include_terminal=True,
    )
    assert len(remaining) == 1
    assert remaining[0].authority == "user_declared"
