from app.services.principal_memory_shadow import (
    PrincipalMemoryShadowService, canonical_provider_context_digest,
)
from tests.test_principal_memory_retrieval import NOW, build_retriever, make_active_fact


def test_read_shadow_reports_would_select_but_returns_same_provider_context():
    retriever, facts, _ = build_retriever()
    make_active_fact(
        facts,
        fact_type="confirmed_skill",
        value={"confirmed_skill": "python"},
    )
    service = PrincipalMemoryShadowService(retriever=retriever, mode="read_shadow")
    context = [{"role": "candidate", "content": "original prompt bytes"}]
    before = repr(context).encode("utf-8")

    result = service.observe(
        provider_context=context,
        current_tags={"python"},
        role_tags={"backend"},
        now=NOW,
    )

    assert repr(result.provider_context).encode("utf-8") == before
    assert result.provider_context is context
    assert result.would_select_count == 1
    assert "confirmed_skill" not in repr(result.provider_context)


def test_shadow_failure_is_fail_open_and_does_not_mutate_context():
    class FailingRetriever:
        def select(self, **kwargs):
            raise RuntimeError("private backend failure")

    context = [{"role": "candidate", "content": "unchanged"}]
    result = PrincipalMemoryShadowService(
        retriever=FailingRetriever(),
        mode="read_shadow",
    ).observe(
        provider_context=context,
        current_tags=set(),
        role_tags=set(),
        now=NOW,
    )
    assert result.outcome == "failed"
    assert context == [{"role": "candidate", "content": "unchanged"}]


def test_disabled_shadow_service_is_content_free_zero_activity(monkeypatch):
    class UnexpectedRetriever:
        def select(self, **kwargs):
            raise AssertionError("disabled mode must not retrieve facts")

    monkeypatch.setattr(
        "app.services.principal_memory_shadow.publish_principal_read_shadow_metric",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled mode must not publish a metric")
        ),
    )
    context = [{"role": "candidate", "content": "unchanged"}]
    result = PrincipalMemoryShadowService(
        retriever=UnexpectedRetriever(),
        mode="disabled",
    ).observe(
        provider_context=context,
        current_tags={"python"},
        role_tags={"backend"},
        now=NOW,
    )

    assert result.provider_context is context
    assert result.would_select_count == 0
    assert result.conflict_count == 0
    assert result.estimated_tokens == 0
    assert result.outcome == "disabled"
    assert context == [{"role": "candidate", "content": "unchanged"}]


def test_canonical_digest_normalizes_unicode_and_preserves_message_order():
    composed = [{"content": "é", "role": "candidate"}]
    decomposed = [{"role": "candidate", "content": "e\u0301"}]
    reversed_messages = [
        {"role": "interviewer", "content": "second"},
        {"role": "candidate", "content": "first"},
    ]
    assert canonical_provider_context_digest(composed) == canonical_provider_context_digest(decomposed)
    assert canonical_provider_context_digest(reversed_messages) != canonical_provider_context_digest(list(reversed(reversed_messages)))
