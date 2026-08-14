from __future__ import annotations

import json
from pathlib import Path

from app.services.knowledge_eval_artifacts_v3 import canonical_sha256
from scripts.validate_knowledge_diagnostic_dataset import (
    validate_diagnostic_dataset,
)


MANIFEST = Path("app/data/knowledge_v2/manifest.json")
DATASET = Path("eval/knowledge-v3/machine-preannotation/dataset.json")
PROVENANCE = Path("eval/knowledge-v3/machine-preannotation/provenance.json")


def test_repository_demo_diagnostic_dataset_has_complete_frozen_integrity():
    summary = validate_diagnostic_dataset(DATASET, PROVENANCE, MANIFEST)

    assert summary["status"] == "valid_demo_diagnostic_dataset"
    assert summary["case_count"] == 100
    assert summary["tuning_count"] == 75
    assert summary["diagnostic_holdout_count"] == 25
    assert summary["case_type_count"] == 14
    assert summary["family_count"] == 100
    assert summary["curation"] == "Curated / Machine-assisted"
    assert summary["production_claim"] is False
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    assert summary["dataset_canonical_sha256"] == canonical_sha256(payload)


def test_demo_diagnostic_dataset_truthfully_has_no_human_governance_claims():
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))

    assert dataset["governance"] is None
    assert provenance["human_annotator_count"] == 0
    assert provenance["eligible_as_independent_eval_evidence"] is False
    assert all(case["annotator_identity_sha256s"] == [] for case in dataset["cases"])
    assert all(case["annotation_record_sha256s"] == [] for case in dataset["cases"])
    assert all(case["label_consensus_record_sha256"] is None for case in dataset["cases"])
