import json

import pytest
from pydantic import ValidationError

from app.services.knowledge_eval_dataset_v3 import (
    CaseType,
    KnowledgeEvalGovernanceV3,
    KnowledgeRetrievalCaseV3,
    KnowledgeRetrievalDatasetV3,
    load_knowledge_retrieval_dataset_v3,
)
from datetime import datetime, timezone


MANIFEST_HASH = "a" * 64


def _case(**overrides):
    payload = {
        "case_id": "redis-lock-owner",
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
            **_case(
                case_type="no_evidence",
                expected_no_evidence=True,
            )
        )
    with pytest.raises(ValidationError, match="require primary"):
        KnowledgeRetrievalCaseV3(
            **_case(primary_relevant_chunk_ids=[])
        )


def test_v3_dataset_requires_frozen_tuning_and_holdout_splits():
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
                split="holdout",
                query_text="如何校验 Redis 锁的持有者？",
            ),
        ],
    }
    path = tmp_path / "v3.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    dataset = load_knowledge_retrieval_dataset_v3(
        path, manifest=_manifest(), require_release_shape=False
    )
    assert len(dataset.cases) == 2

    with pytest.raises(ValueError, match="manifest"):
        load_knowledge_retrieval_dataset_v3(
            path,
            manifest={**_manifest(), "corpus_manifest_sha256": "b" * 64},
            require_release_shape=False,
        )

    payload["cases"][1]["primary_relevant_chunk_ids"] = ["missing"]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="missing chunk IDs"):
        load_knowledge_retrieval_dataset_v3(
            path, manifest=_manifest(), require_release_shape=False
        )


def test_v3_release_shape_fails_closed_for_seed_dataset():
    dataset = KnowledgeRetrievalDatasetV3(
        version="v3-seed",
        corpus_manifest_sha256=MANIFEST_HASH,
        cases=[
            KnowledgeRetrievalCaseV3(**_case()),
            KnowledgeRetrievalCaseV3(
                **_case(
                    case_id="holdout-lock",
                    split="holdout",
                    query_text="如何校验 Redis 锁的持有者？",
                )
            ),
        ],
    )

    with pytest.raises(ValueError, match="at least 80"):
        dataset.validate_release_shape()


def _release_dataset(*, blinded=True, frozen=True, leak_family=False):
    case_types = list(CaseType.__args__)
    cases = []
    expanded_case_types = case_types * 3
    holdout_count = 10
    for index, case_type in enumerate(expanded_case_types):
        is_no_evidence = case_type in {"no_evidence", "out_of_domain"}
        split = "holdout" if index < holdout_count else "tuning"
        family = (
            "shared-family"
            if leak_family and index in {0, holdout_count}
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
                    primary_relevant_chunk_ids=[] if is_no_evidence else ["redis-lock"],
                    accepted_related_chunk_ids=[],
                    expected_no_evidence=is_no_evidence,
                    annotator_identity_sha256s=["1" * 64, "2" * 64],
                    annotation_record_sha256s=["3" * 64, "4" * 64],
                    label_consensus_record_sha256="5" * 64,
                )
            )
        )
    return KnowledgeRetrievalDatasetV3(
        version="release-v3",
        corpus_manifest_sha256=MANIFEST_HASH,
        governance=KnowledgeEvalGovernanceV3(
            annotation_protocol_version="annotation-v1",
            annotator_role="independent backend interviewer",
            minimum_annotators_per_case=2,
            implementation_output_blinded=blinded,
            split_frozen=frozen,
            agreement_metric="krippendorff_alpha",
            agreement_value=0.82,
            minimum_agreement=0.80,
            labeling_started_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            split_frozen_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            provenance_record_sha256="c" * 64,
        ),
        cases=cases,
    )


def test_v3_release_shape_requires_blind_frozen_family_isolated_annotation():
    valid = _release_dataset()
    valid.validate_release_shape(minimum_cases=len(valid.cases))

    with pytest.raises(ValueError, match="blinded"):
        _release_dataset(blinded=False).validate_release_shape(
            minimum_cases=len(valid.cases)
        )
    with pytest.raises(ValueError, match="must be frozen"):
        _release_dataset(frozen=False).validate_release_shape(
            minimum_cases=len(valid.cases)
        )
    with pytest.raises(ValueError, match="cannot cross"):
        _release_dataset(leak_family=True).validate_release_shape(
            minimum_cases=len(valid.cases)
        )


def test_v3_release_shape_requires_independent_records_and_agreement():
    dataset = _release_dataset()
    incomplete = dataset.model_copy(deep=True)
    incomplete.cases[0].annotation_record_sha256s = ["3" * 64]
    with pytest.raises(ValueError, match="independent annotation"):
        incomplete.validate_release_shape(minimum_cases=len(incomplete.cases))

    governance = dataset.governance.model_copy(
        update={"agreement_value": 0.70, "minimum_agreement": 0.80}
    )
    payload = governance.model_dump()
    with pytest.raises(ValidationError, match="below the registered minimum"):
        KnowledgeEvalGovernanceV3.model_validate(payload)


def test_v3_release_shape_requires_more_than_one_or_two_cases_per_core_type():
    dataset = _release_dataset()
    reduced = dataset.model_copy(deep=True)
    reduced.cases = [
        case
        for case in reduced.cases
        if case.case_type != "alias_only" or case.case_id.endswith(("-1", "-15"))
    ]

    with pytest.raises(ValueError, match="at least 3 cases each"):
        reduced.validate_release_shape(minimum_cases=len(reduced.cases))
