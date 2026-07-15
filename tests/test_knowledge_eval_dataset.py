import json
from pathlib import Path

import pytest

from app.services.knowledge_eval_dataset import (
    KnowledgeRetrievalCase,
    KnowledgeRetrievalDataset,
    load_knowledge_retrieval_dataset,
)


def test_stage42_dataset_has_required_rc_shape_and_valid_chunk_ids():
    dataset = load_knowledge_retrieval_dataset()
    manifest = json.loads(
        Path("app/data/knowledge/manifest.json").read_text(encoding="utf-8")
    )
    known_ids = {item["chunk_id"] for item in manifest["chunks"]}

    assert len(dataset.cases) >= 30
    assert sum(case.category == "relevant" for case in dataset.cases) >= 20
    assert sum(case.category == "weak_keyword" for case in dataset.cases) >= 5
    assert sum(case.category == "negative" for case in dataset.cases) >= 5
    assert {case.domain for case in dataset.cases if case.category != "negative"} >= {
        "redis",
        "fastapi",
        "mysql",
        "kafka",
        "system-design",
    }
    assert all(
        relevant_id in known_ids
        for case in dataset.cases
        for relevant_id in case.relevant_chunk_ids
    )


def test_dataset_rejects_duplicate_case_ids_and_queries():
    case = KnowledgeRetrievalCase(
        case_id="duplicate",
        category="relevant",
        domain="redis",
        query_text="redis cache consistency",
        canonical_tags=["redis"],
        relevant_chunk_ids=["redis_consistency"],
    )

    with pytest.raises(ValueError, match="duplicate retrieval case id"):
        KnowledgeRetrievalDataset(version="test", cases=[case, case])

    duplicate_query = case.model_copy(update={"case_id": "another"})
    with pytest.raises(ValueError, match="duplicate retrieval query"):
        KnowledgeRetrievalDataset(version="test", cases=[case, duplicate_query])


def test_negative_and_positive_reference_contracts_are_enforced():
    with pytest.raises(ValueError, match="negative case cannot declare relevant chunks"):
        KnowledgeRetrievalCase(
            case_id="negative",
            category="negative",
            domain="redis",
            query_text="vacation policy",
            canonical_tags=["redis"],
            relevant_chunk_ids=["redis_consistency"],
        )

    with pytest.raises(ValueError, match="positive case requires relevant chunks"):
        KnowledgeRetrievalCase(
            case_id="positive",
            category="relevant",
            domain="redis",
            query_text="cache consistency",
            canonical_tags=["redis"],
        )
