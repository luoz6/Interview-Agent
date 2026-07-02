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
    assert "general" in by_id["redis_backend"].tags


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
