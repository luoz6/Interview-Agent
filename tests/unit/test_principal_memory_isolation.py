from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.adapters.memory.principal_memory import InMemoryPrincipalMemoryFactStore
from app.services.knowledge_corpus_schema import load_knowledge_document_v2
from app.domain.memory.contracts import PrincipalMemoryFact
from app.services.principal_memory_shadow import PrincipalMemoryShadowService
from tests.principal_memory_fixtures import build_retriever, make_active_fact, make_fact


def test_same_fact_value_is_isolated_between_principals_and_deletions():
    store = InMemoryPrincipalMemoryFactStore()
    alpha = make_fact(principal_id="principal-a", session_id="session-a")
    beta = make_fact(principal_id="principal-b", session_id="session-b")
    store.create_proposal(alpha)
    store.create_proposal(beta)

    assert store.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-a",
        limit=10,
    ) == [alpha]
    assert store.purge_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-a",
    ) == 1
    assert store.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-b",
        limit=10,
    ) == [beta]


def test_unknown_principal_query_does_not_reveal_fact_existence():
    store = InMemoryPrincipalMemoryFactStore()
    store.create_proposal(make_fact(principal_id="principal-a"))
    assert store.list_by_principal(
        deployment_id="single-tenant-local",
        principal_id="principal-unknown",
        limit=10,
    ) == []


def test_principal_fact_has_no_knowledge_chunk_or_embedding_conversion():
    assert "to_knowledge_chunk" not in set(dir(PrincipalMemoryFact))
    assert "embedding" not in PrincipalMemoryFact.model_fields


def test_knowledge_loader_rejects_principal_memory_schema(tmp_path):
    path = tmp_path / "principal.md"
    path.write_text(
        "---\nschema_version: principal-memory-fact-v1\n"
        "fact_id: private\n---\nprivate\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        load_knowledge_document_v2(path)


def test_read_shadow_observation_never_mutates_provider_context():
    retriever, facts, _ = build_retriever()
    make_active_fact(
        facts,
        fact_type="accessibility_preference",
        value={"accessibility_preference": "extra_time"},
    )
    context = [
        {"role": "system", "content": "Stable synthetic instruction"},
        {"role": "candidate", "content": "Stable synthetic response"},
    ]
    before = [dict(item) for item in context]

    result = PrincipalMemoryShadowService(
        retriever=retriever,
        mode="read_shadow",
    ).observe(
        provider_context=context,
        current_tags={"python"},
        role_tags={"backend"},
        now=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )

    assert result.would_select_count == 1
    assert context == before
    assert result.provider_context == before
    assert result.outcome == "completed"
