from app.services.static_knowledge_store import StaticKnowledgeStore


def test_static_knowledge_store_returns_deterministic_preview_references():
    store = StaticKnowledgeStore()

    first = store.search(
        "Redis consistency",
        job_tags=["redis"],
        source_types=["theory", "expert_benchmark"],
        limit=5,
    )
    second = store.search(
        "Different query",
        job_tags=["python"],
        source_types=["theory", "expert_benchmark"],
        limit=5,
    )

    assert [item.chunk_id for item in first] == [item.chunk_id for item in second]
    assert all(item.metadata["preview"] is True for item in first)
    assert store.last_search_trace["corpus_version"] == "static-preview-v1"


def test_static_knowledge_store_supports_bound_id_lookup():
    store = StaticKnowledgeStore()

    result = store.get_by_ids(
        ["preview-theory-1", "missing"],
        expected_hashes={"preview-theory-1": "preview-theory-1"},
    )

    assert [item.chunk_id for item in result.found] == ["preview-theory-1"]
    assert result.missing == ["missing"]
    assert result.version_mismatch == []


def test_static_knowledge_store_honors_explicit_domain_filter():
    store = StaticKnowledgeStore()

    result = store.search(
        "preview",
        job_tags=["general"],
        domains=["interview"],
        limit=5,
    )

    assert [item.chunk_id for item in result] == ["preview-theory-1"]
