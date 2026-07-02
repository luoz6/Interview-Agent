import scripts.load_knowledge as load_knowledge


class FakeStore:
    def __init__(self):
        self.received = None

    def upsert_chunks(self, chunks):
        self.received = list(chunks)


def test_build_chunks_marks_sources_and_general_tag():
    chunks = load_knowledge.build_chunks()
    by_id = {chunk.chunk_id: chunk for chunk in chunks}

    assert by_id["redis_backend"].source_type == "expert_benchmark"
    assert by_id["redis_consistency"].source_type == "theory"
    assert by_id["redis_backend"].tags == ["redis", "backend", "cache"]
    assert "redis" in by_id["redis_backend"].tags


def test_load_knowledge_upserts_all_discovered_chunks_and_returns_summary():
    fake_store = FakeStore()

    summary = load_knowledge.load_knowledge(store=fake_store)

    assert summary["discovered"] == len(fake_store.received)
    assert summary["upserted"] == len(fake_store.received)
    assert {"redis_backend", "redis_consistency"}.issubset(
        {chunk.chunk_id for chunk in fake_store.received}
    )


def test_build_chunks_covers_core_domains_after_knowledge_expansion():
    chunks = load_knowledge.build_chunks()
    domains = {chunk.domain for chunk in chunks}

    assert {"redis", "fastapi", "mysql", "kafka", "system-design"}.issubset(domains)


def test_parse_front_matter_reads_explicit_domain_and_tags():
    raw = """---
domain: general
source_type: theory
tags: [general, communication]
title: Communication Review
---
This note mentions MySQL and Redis but should stay general.
"""

    metadata, body = load_knowledge.parse_front_matter(raw)

    assert metadata["domain"] == "general"
    assert metadata["source_type"] == "theory"
    assert metadata["tags"] == ["general", "communication"]
    assert body == "This note mentions MySQL and Redis but should stay general."


def test_metadata_domain_wins_over_keyword_inference():
    raw = """---
domain: general
source_type: theory
tags: [general]
---
This note mentions MySQL and Redis but should stay general.
"""

    metadata, body = load_knowledge.parse_front_matter(raw)
    domain = load_knowledge.resolve_domain(
        metadata=metadata,
        path=load_knowledge.KNOWLEDGE_ROOT / "theory" / "communication.md",
        content=body,
    )

    assert domain == "general"


def test_missing_metadata_falls_back_to_keyword_inference():
    domain = load_knowledge.resolve_domain(
        metadata={},
        path=load_knowledge.KNOWLEDGE_ROOT / "theory" / "cache_breakdown.md",
        content="Redis cache breakdown needs single-flight protection.",
    )

    assert domain == "redis"
