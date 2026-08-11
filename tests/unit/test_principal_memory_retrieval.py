from tests.principal_memory_fixtures import (
    FACT_NOW as NOW,
    PrincipalMemorySessions as Sessions,
    build_retriever,
    make_active_fact,
)


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
        unsafe_seed=True,
    )
    make_active_fact(
        facts,
        fact_type="declared_preference",
        value={"interview_language": "en"},
        digest="f",
        unsafe_seed=True,
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
