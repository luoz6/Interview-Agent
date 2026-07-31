from pathlib import Path

import pytest

from app.services.knowledge_corpus_schema import load_knowledge_document_v2
from app.services.principal_memory_contracts import PrincipalMemoryFact


def test_knowledge_and_manifest_paths_cannot_import_principal_fact_store():
    paths = [
        Path("app/services/vector_store.py"),
        Path("app/services/knowledge_corpus_schema.py"),
        Path("scripts/load_knowledge_v2.py"),
        Path("scripts/build_knowledge_manifest_v2.py"),
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "principal_memory" not in source.casefold()
        assert "PrincipalMemoryFactStore" not in source


def test_principal_fact_has_no_knowledge_chunk_or_embedding_conversion():
    methods = set(dir(PrincipalMemoryFact))
    assert "to_knowledge_chunk" not in methods
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


def test_public_retrieval_and_observation_paths_do_not_accept_principal_scope():
    paths = [
        Path("app/services/knowledge_query.py"),
        Path("app/services/knowledge_grounding.py"),
        Path("app/services/vector_store.py"),
        Path("app/services/report.py"),
        Path("app/services/knowledge_trace.py"),
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "principal_memory" not in source.casefold()
        assert "normalized_fact" not in source.casefold()


def test_principal_deletion_has_no_public_knowledge_dependency():
    source = Path("app/services/principal_memory_deletion.py").read_text(
        encoding="utf-8"
    )
    assert "vector" not in source.casefold()
    assert "knowledge" not in source.casefold()
    assert "embedding" not in source.casefold()
