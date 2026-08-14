import json

import pytest
from pydantic import ValidationError

from app.services.knowledge_eval_dataset_v3 import (
    CaseType,
    KnowledgeRetrievalCaseV3,
    KnowledgeRetrievalDatasetV3,
    load_knowledge_retrieval_dataset_v3,
)


MANIFEST_HASH = "a" * 64


def _case(**overrides):
    payload = {
        "case_id": "redis-lock-owner",
        "case_family": "family-redis-lock-owner",
        "case_type": "exact_technical_term",
        "split": "tuning",
        "evaluation_group": "redis",
        "query_text": "Redis 分布式锁为什么需要 owner token？",
        "canonical_tags": ["redis"],
        "source_types": ["theory"],
        "allowed_domains": ["redis"],
        "primary_relevant_chunk_ids": ["redis-lock"],
        "accepted_related_chunk_ids": [],
        "excluded_chunk_ids": ["redis-cache"],
    }
    payload.update(overrides)
    return payload


def _manifest():
    return {
        "corpus_manifest_sha256": MANIFEST_HASH,
        "chunks": [
            {"chunk_id": "redis-lock"},
            {"chunk_id": "redis-cache"},
        ],
    }


def test_v3_case_supports_no_evidence_without_fake_relevant_chunks():
    case = KnowledgeRetrievalCaseV3(
        **_case(
            case_id="unknown-topic",
            case_family="family-unknown-topic",
            case_type="no_evidence",
            split="holdout",
            query_text="不存在的缓存协议应该怎样配置？",
            primary_relevant_chunk_ids=[],
            accepted_related_chunk_ids=[],
            expected_no_evidence=True,
        )
    )

    assert case.expected_no_evidence is True
    with pytest.raises(ValueError, match="cannot be converted"):
        case.as_v2()


def test_v3_case_rejects_inconsistent_no_evidence_contract():
    with pytest.raises(ValidationError, match="cannot declare relevant"):
        KnowledgeRetrievalCaseV3(
            **_case(case_type="no_evidence", expected_no_evidence=True)
        )
    with pytest.raises(ValidationError, match="require primary"):
        KnowledgeRetrievalCaseV3(**_case(primary_relevant_chunk_ids=[]))


def test_v3_dataset_requires_both_diagnostic_splits():
    tuning = KnowledgeRetrievalCaseV3(**_case())
    with pytest.raises(ValidationError, match="holdout"):
        KnowledgeRetrievalDatasetV3(
            version="v3-test",
            corpus_manifest_sha256=MANIFEST_HASH,
            cases=[tuning],
        )


def test_v3_loader_checks_manifest_hash_and_chunk_references(tmp_path):
    payload = {
        "version": "v3-test",
        "corpus_manifest_sha256": MANIFEST_HASH,
        "cases": [
            _case(),
            _case(
                case_id="holdout-lock",
                case_family="family-holdout-lock",
                split="holdout",
                query_text="如何校验 Redis 锁的持有者？",
            ),
        ],
    }
    path = tmp_path / "v3.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    dataset = load_knowledge_retrieval_dataset_v3(
        path,
        manifest=_manifest(),
        require_diagnostic_integrity=False,
    )
    assert len(dataset.cases) == 2

    with pytest.raises(ValueError, match="manifest"):
        load_knowledge_retrieval_dataset_v3(
            path,
            manifest={**_manifest(), "corpus_manifest_sha256": "b" * 64},
            require_diagnostic_integrity=False,
        )

    payload["cases"][1]["primary_relevant_chunk_ids"] = ["missing"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="missing chunk IDs"):
        load_knowledge_retrieval_dataset_v3(
            path,
            manifest=_manifest(),
            require_diagnostic_integrity=False,
        )


def _diagnostic_dataset(*, leak_family=False, omit_type=None):
    cases = []
    case_types = [item for item in CaseType.__args__ if item != omit_type]
    for index, case_type in enumerate(case_types):
        is_no_evidence = case_type in {"no_evidence", "out_of_domain"}
        split = "holdout" if index < 4 else "tuning"
        family = (
            "shared-family"
            if leak_family and index in {0, 4}
            else f"family-{index}"
        )
        cases.append(
            KnowledgeRetrievalCaseV3(
                **_case(
                    case_id=f"case-{index}",
                    case_family=family,
                    case_type=case_type,
                    split=split,
                    query_text=f"第 {index} 个独立检索案例是什么？",
                    primary_relevant_chunk_ids=(
                        [] if is_no_evidence else ["redis-lock"]
                    ),
                    accepted_related_chunk_ids=[],
                    expected_no_evidence=is_no_evidence,
                )
            )
        )
    return KnowledgeRetrievalDatasetV3(
        version="diagnostic-v3",
        corpus_manifest_sha256=MANIFEST_HASH,
        cases=cases,
    )


def test_diagnostic_integrity_requires_family_isolation_and_all_case_types():
    valid = _diagnostic_dataset()
    valid.validate_diagnostic_integrity(minimum_cases=len(valid.cases))

    with pytest.raises(ValueError, match="cannot cross"):
        _diagnostic_dataset(leak_family=True).validate_diagnostic_integrity()
    with pytest.raises(ValueError, match="missing case types"):
        _diagnostic_dataset(omit_type="alias_only").validate_diagnostic_integrity()


def test_active_diagnostic_schema_excludes_release_governance():
    dataset = _diagnostic_dataset()

    assert "governance" not in KnowledgeRetrievalDatasetV3.model_fields
    assert "annotator_identity_sha256s" not in KnowledgeRetrievalCaseV3.model_fields
    assert "annotation_record_sha256s" not in KnowledgeRetrievalCaseV3.model_fields
    assert "label_consensus_record_sha256" not in KnowledgeRetrievalCaseV3.model_fields
    dataset.validate_diagnostic_integrity()
