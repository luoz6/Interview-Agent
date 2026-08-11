import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.adapters.pgvector.repository import (
    KnowledgeChunk,
    PgVectorKnowledgeStore,
    _normalize_technical_terms,
    _rerank_chunks,
)
from app.adapters.pgvector.codec import PgVectorCodec
from tests.vector_store_fixtures import FakeEmbeddingProvider


def make_store() -> PgVectorKnowledgeStore:
    return PgVectorKnowledgeStore(
        dsn="postgresql://placeholder",
        table_name="knowledge_chunks",
        embedding_provider=FakeEmbeddingProvider(),
        minimum_score=0.35,
    )


def make_chunk() -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id="redis-1",
        title="Redis cache consistency",
        content="Delete cache after updating the database.",
        source_type="theory",
        domain="redis",
        tags=["redis", "backend"],
        metadata={"section": "consistency"},
    )


def make_scored_chunk(
    chunk_id,
    *,
    score,
    title,
    domain,
    tags,
    metadata=None,
):
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content="test content",
        source_type="theory",
        domain=domain,
        tags=tags,
        metadata=metadata or {},
        score=score,
    )


def test_knowledge_chunk_preserves_metadata():
    chunk = make_chunk()

    assert chunk.tags == ["redis", "backend"]
    assert chunk.metadata["section"] == "consistency"


def test_embed_text_uses_injected_provider():
    store = make_store()

    vector = store.embed_text("redis cache consistency")

    assert vector == pytest.approx([0.1, 0.2, 0.3])


def test_vector_literal_format_is_pgvector_compatible():
    literal = PgVectorCodec.vector_literal([0.1, 0.2, 0.3])

    assert literal == "[0.10000000,0.20000000,0.30000000]"


def test_normalize_technical_terms_has_a_fixed_dependency_free_contract():
    assert _normalize_technical_terms("FastAPI PostgreSQL") == {
        "fastapi",
        "postgresql",
    }
    assert _normalize_technical_terms("cache-aside") == {"cache", "aside"}
    assert _normalize_technical_terms("Ｃ＋＋ Redis") == {"c++", "redis"}
    assert _normalize_technical_terms("缓存一致性 与 数据库") == {
        "缓存一致性",
        "数据库",
    }
    assert _normalize_technical_terms("the cache and database") == {
        "cache",
        "database",
    }


@pytest.mark.parametrize(
    ("aliases", "expected_score"),
    [
        ("cache-aside", 0.56),
        (["cache-aside", 7], 0.56),
        (None, 0.50),
    ],
)
def test_rerank_normalizes_alias_metadata_shapes(aliases, expected_score):
    metadata = {} if aliases is None else {"aliases": aliases}
    chunk = make_scored_chunk(
        "cache",
        score=0.50,
        title="General material",
        domain="redis",
        tags=["redis"],
        metadata=metadata,
    )

    ranked = _rerank_chunks(
        [chunk],
        query_text="cache-aside",
        requested_tags=[],
        minimum_score=0.45,
        limit=5,
    )

    assert ranked[0].score == pytest.approx(expected_score)


def test_rerank_applies_each_signal_once_and_breaks_ties_by_id():
    chunks = [
        make_scored_chunk(
            "b",
            score=0.80,
            title="General cache",
            domain="redis",
            tags=["redis"],
        ),
        make_scored_chunk(
            "a",
            score=0.80,
            title="Redis consistency",
            domain="redis",
            tags=["redis"],
        ),
    ]

    ranked = _rerank_chunks(
        chunks,
        query_text="redis consistency consistency",
        requested_tags=["redis", "redis"],
        minimum_score=0.45,
        limit=5,
    )

    assert [item.chunk_id for item in ranked] == ["a", "b"]
    assert ranked[0].score == pytest.approx(0.90)
    assert ranked[1].score == pytest.approx(0.84)


def test_from_env_requires_postgres_dsn(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.delenv("PGVECTOR_TABLE", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    with pytest.raises(ValueError, match="POSTGRES_DSN must be configured"):
        PgVectorKnowledgeStore.from_env()


def test_repository_errors_do_not_expose_dsn_credentials():
    dsn = "postgresql://secret-user:secret-pass@127.0.0.1:1/private-db"
    store = PgVectorKnowledgeStore(
        dsn=dsn,
        table_name="knowledge_chunks",
        embedding_provider=FakeEmbeddingProvider(),
    )

    with pytest.raises(RuntimeError) as exc:
        store.get_by_ids(["redis-1"])

    message = str(exc.value)
    assert "secret-user" not in message
    assert "secret-pass" not in message
    assert "private-db" not in message


def test_disabled_provider_constructs_without_adapter_import_or_model_cache(tmp_path):
    cache = tmp_path / "model-cache"
    cache.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "EMBEDDING_PROVIDER": "disabled",
            "POSTGRES_DSN": "postgresql://user:pass@localhost/interview",
            "HF_HOME": str(cache),
            "TRANSFORMERS_CACHE": str(cache),
            "SENTENCE_TRANSFORMERS_HOME": str(cache),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from app.adapters.pgvector.repository import PgVectorKnowledgeStore; "
                "store = PgVectorKnowledgeStore.from_env(); "
                "print(store.embedding_provider.provider_name); "
                "print('app.services.siliconflow_embeddings' in sys.modules)"
            ),
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines() == ["disabled", "False"]
    assert list(cache.iterdir()) == []
