import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.knowledge_eval_dataset import KnowledgeRetrievalCase, KnowledgeRetrievalDataset
from app.services.knowledge_eval_dataset_v2 import (
    EVALUATION_GROUP_DOMAIN_MAP,
    KnowledgeRetrievalCaseV2,
    KnowledgeRetrievalDatasetV2,
    load_knowledge_retrieval_dataset_v2,
)


MANIFEST_PATH = Path("app/data/knowledge/manifest.json")
PILOT_PATH = Path("tests/golden/knowledge_retrieval_v2_pilot.json")


def _case(**overrides):
    payload = {
        "case_id": "redis-consistency",
        "evaluation_group": "redis",
        "query_text": "缓存与数据库更新时怎样处理并发一致性窗口？",
        "canonical_tags": ["redis", "一致性"],
        "source_types": ["theory"],
        "allowed_domains": ["redis"],
        "primary_relevant_chunk_ids": ["redis_consistency"],
        "accepted_related_chunk_ids": ["redis_operations"],
        "excluded_chunk_ids": ["redis_distributed_lock"],
    }
    payload.update(overrides)
    return payload


def test_v1_model_shape_and_defaults_remain_frozen():
    assert set(KnowledgeRetrievalCase.model_fields) == {
        "case_id",
        "category",
        "domain",
        "query_text",
        "canonical_tags",
        "source_types",
        "relevant_chunk_ids",
        "top_k",
    }
    assert KnowledgeRetrievalCase.model_fields["top_k"].default == 3
    assert set(KnowledgeRetrievalDataset.model_fields) == {"version", "cases"}


def test_v2_case_has_independent_shape():
    case = KnowledgeRetrievalCaseV2(**_case())

    assert case.top_k == 5
    assert "category" not in KnowledgeRetrievalCaseV2.model_fields
    assert set(KnowledgeRetrievalCaseV2.model_fields) == {
        "case_id",
        "evaluation_group",
        "query_text",
        "canonical_tags",
        "source_types",
        "allowed_domains",
        "primary_relevant_chunk_ids",
        "accepted_related_chunk_ids",
        "excluded_chunk_ids",
        "top_k",
    }


def test_v2_case_rejects_category_and_non_five_top_k():
    with pytest.raises(ValidationError):
        KnowledgeRetrievalCaseV2(**_case(category="relevant"))
    with pytest.raises(ValidationError):
        KnowledgeRetrievalCaseV2(**_case(top_k=3))


def test_v2_case_rejects_non_chinese_query_and_invalid_group_domains():
    with pytest.raises(ValueError, match="Chinese"):
        KnowledgeRetrievalCaseV2(**_case(query_text="redis cache consistency"))
    with pytest.raises(ValueError, match="allowed_domains"):
        KnowledgeRetrievalCaseV2(
            **_case(evaluation_group="redis", allowed_domains=["mysql"])
        )


def test_v2_reference_sets_are_pairwise_disjoint():
    with pytest.raises(ValueError, match="disjoint"):
        KnowledgeRetrievalCaseV2(
            **_case(
                accepted_related_chunk_ids=["redis_consistency"],
            )
        )


def test_evaluation_group_domain_mapping_is_explicit():
    assert EVALUATION_GROUP_DOMAIN_MAP == {
        "fastapi": {"python", "fastapi"},
        "redis": {"redis"},
        "relational-database": {"mysql", "postgresql"},
        "kafka": {"kafka"},
        "system-design": {"system-design"},
        "reliability": {"reliability", "system-design"},
    }


def test_pilot_has_12_cases_and_two_cases_per_group_with_manifest_ids():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    dataset = load_knowledge_retrieval_dataset_v2(
        PILOT_PATH,
        expected_case_count=12,
        manifest=manifest,
    )

    assert dataset.version == "stage44b1-knowledge-retrieval-v2-pilot"
    assert len(dataset.cases) == 12
    counts = {group: 0 for group in EVALUATION_GROUP_DOMAIN_MAP}
    known_ids = {item["chunk_id"] for item in manifest["chunks"]}
    for case in dataset.cases:
        counts[case.evaluation_group] += 1
        assert any("\u3400" <= char <= "\u9fff" for char in case.query_text)
        referenced_ids = (
            set(case.primary_relevant_chunk_ids)
            | set(case.accepted_related_chunk_ids)
            | set(case.excluded_chunk_ids)
        )
        assert referenced_ids <= known_ids
    assert counts == {group: 2 for group in EVALUATION_GROUP_DOMAIN_MAP}


def test_v2_loader_rejects_missing_manifest_id(tmp_path):
    payload = {"version": "test", "cases": [_case(primary_relevant_chunk_ids=["missing"])]}
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="missing chunk IDs"):
        load_knowledge_retrieval_dataset_v2(
            path,
            expected_case_count=1,
            manifest={"chunks": [{"chunk_id": "redis_consistency"}]},
        )


def test_v2_loader_rejects_wrong_case_count(tmp_path):
    payload = {"version": "test", "cases": [_case()]}
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="expected 12 retrieval cases"):
        load_knowledge_retrieval_dataset_v2(
            path,
            expected_case_count=12,
            manifest=json.loads(MANIFEST_PATH.read_text(encoding="utf-8")),
        )


def test_v2_loader_rejects_uneven_evaluation_groups(tmp_path):
    cases = [
        _case(
            case_id=f"redis-case-{index}",
            query_text=f"第{index}条缓存一致性中文查询如何处理？",
        )
        for index in range(12)
    ]
    path = tmp_path / "dataset.json"
    path.write_text(
        json.dumps({"version": "test", "cases": cases}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="evenly distributed"):
        load_knowledge_retrieval_dataset_v2(
            path,
            expected_case_count=12,
            manifest=json.loads(MANIFEST_PATH.read_text(encoding="utf-8")),
        )


def test_v2_dataset_rejects_duplicate_case_ids_and_queries():
    case = KnowledgeRetrievalCaseV2(**_case())
    with pytest.raises(ValueError, match="duplicate retrieval case id"):
        KnowledgeRetrievalDatasetV2(version="test", cases=[case, case])

    duplicate_query = case.model_copy(update={"case_id": "another-case"})
    with pytest.raises(ValueError, match="duplicate retrieval query"):
        KnowledgeRetrievalDatasetV2(version="test", cases=[case, duplicate_query])
