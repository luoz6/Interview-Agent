from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.services.knowledge_eval_dataset_v3 import load_knowledge_retrieval_dataset_v3
from scripts.build_knowledge_eval_v3_machine_preannotations import (
    _load_slots,
    build_machine_dataset,
    canonical_sha256,
    validate_machine_dataset,
)


MANIFEST = Path("app/data/knowledge_v2/manifest.json")
AUTHORING = Path("eval/knowledge-v3/authoring")


def test_machine_preannotation_builds_complete_truthful_candidate():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dataset, provenance = build_machine_dataset(manifest, _load_slots(AUTHORING))

    assert len(dataset["cases"]) == 100
    assert Counter(case["split"] for case in dataset["cases"]) == {
        "tuning": 75,
        "holdout": 25,
    }
    assert len({case["query_text"] for case in dataset["cases"]}) == 100
    assert all(case["annotator_identity_sha256s"] == [] for case in dataset["cases"])
    assert all(case["annotation_record_sha256s"] == [] for case in dataset["cases"])
    assert all(case["label_consensus_record_sha256"] is None for case in dataset["cases"])
    assert dataset["governance"] is None
    assert provenance["human_annotator_count"] == 0
    assert provenance["eligible_as_independent_eval_evidence"] is False
    assert len({row["semantic_family_key"] for row in provenance["cases"]}) == 100


def test_machine_preannotation_labels_are_disjoint_and_no_evidence_is_truthful():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    dataset, _ = build_machine_dataset(manifest, _load_slots(AUTHORING))
    known_ids = {chunk["chunk_id"] for chunk in manifest["chunks"]}

    for case in dataset["cases"]:
        primary = set(case["primary_relevant_chunk_ids"])
        related = set(case["accepted_related_chunk_ids"])
        excluded = set(case["excluded_chunk_ids"])
        assert primary.isdisjoint(related)
        assert primary.isdisjoint(excluded)
        assert related.isdisjoint(excluded)
        assert primary | related | excluded <= known_ids
        if case["expected_no_evidence"]:
            assert case["case_type"] in {"no_evidence", "out_of_domain"}
            assert not primary
            assert not related
        else:
            assert primary
            assert excluded


def test_cross_domain_and_filter_boundary_cases_have_real_confusers():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    chunks = {chunk["chunk_id"]: chunk for chunk in manifest["chunks"]}
    dataset, _ = build_machine_dataset(manifest, _load_slots(AUTHORING))

    for case in dataset["cases"]:
        if case["case_type"] in {"cross_domain_confusion", "metadata_routing_error"}:
            primary_domains = {
                chunks[item]["domain"] for item in case["primary_relevant_chunk_ids"]
            }
            assert any(
                chunks[item]["domain"] not in primary_domains
                for item in case["excluded_chunk_ids"]
            )
        if case["case_type"] == "filter_boundary":
            assert any(
                chunks[item]["domain"] not in case["allowed_domains"]
                or chunks[item]["source_type"] not in case["source_types"]
                for item in case["excluded_chunk_ids"]
            )


def test_repository_machine_preannotation_candidate_validates():
    summary = validate_machine_dataset(
        Path("eval/knowledge-v3/machine-preannotation/dataset.json"),
        Path("eval/knowledge-v3/machine-preannotation/provenance.json"),
        MANIFEST,
    )

    assert summary["case_count"] == 100
    assert summary["case_type_count"] == 14
    assert summary["family_count"] == 100
    assert summary["human_annotator_count"] == 0
    assert summary["eligible_as_independent_eval_evidence"] is False
    payload = json.loads(
        Path("eval/knowledge-v3/machine-preannotation/dataset.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["dataset_canonical_sha256"] == canonical_sha256(payload)


def test_formal_release_validator_rejects_machine_candidate_without_governance():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="annotation governance"):
        load_knowledge_retrieval_dataset_v3(
            Path("eval/knowledge-v3/machine-preannotation/dataset.json"),
            manifest=manifest,
            require_release_shape=True,
        )
