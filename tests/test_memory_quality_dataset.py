import json

import pytest
from pydantic import ValidationError

from app.services.memory_quality_dataset import (
    MemoryQualityDataset,
    load_memory_quality_dataset,
)


def test_memory_quality_dataset_is_synthetic_balanced_and_long_context():
    dataset = load_memory_quality_dataset()

    assert dataset.synthetic_only is True
    assert {case.language_bucket for case in dataset.cases} == {
        "zh_hans",
        "en",
        "mixed",
    }
    assert all(20 <= len(case.turns) <= 50 for case in dataset.cases)
    assert all(case.principal_memory_facts for case in dataset.cases)
    assert all(case.foreign_principal_facts for case in dataset.cases)


def test_memory_quality_dataset_rejects_real_or_unbalanced_payload():
    payload = json.loads(
        open("tests/golden/memory_long_context_v1.json", encoding="utf-8").read()
    )
    payload["synthetic_only"] = False
    with pytest.raises(ValidationError):
        MemoryQualityDataset.model_validate(payload)
