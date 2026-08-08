import hashlib
import json

from app.services.interview_quality_dataset import InterviewQualityDataset
from scripts.build_followup_decision_dataset import build_dataset


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_followup_dataset_builder_is_deterministic_and_contract_valid():
    first = build_dataset()
    second = build_dataset()

    assert canonical_sha256(first) == canonical_sha256(second)
    dataset = InterviewQualityDataset.model_validate(first)
    assert dataset.dataset_id == "followup-decision-quality-v2"
    assert len(dataset.cases) == 100


def test_followup_dataset_sequences_and_partitions_are_non_overlapping():
    dataset = InterviewQualityDataset.model_validate(build_dataset())
    sequence_partitions: dict[str, set[str]] = {}
    sequence_steps: dict[str, set[int]] = {}
    case_ids_by_partition: dict[str, set[str]] = {
        "train": set(),
        "dev": set(),
        "blind-test": set(),
    }
    for case in dataset.cases:
        case_ids_by_partition[case.partition].add(case.case_id)
        sequence_id = case.input.get("sequence_id")
        if sequence_id:
            sequence_partitions.setdefault(sequence_id, set()).add(case.partition)
            sequence_steps.setdefault(sequence_id, set()).add(
                case.input["sequence_step"]
            )

    assert len(sequence_steps) == 20
    assert all(steps == {1, 2} for steps in sequence_steps.values())
    assert all(len(partitions) == 1 for partitions in sequence_partitions.values())
    assert case_ids_by_partition["train"].isdisjoint(
        case_ids_by_partition["dev"] | case_ids_by_partition["blind-test"]
    )
    assert case_ids_by_partition["dev"].isdisjoint(
        case_ids_by_partition["blind-test"]
    )


def test_followup_dataset_has_no_real_candidate_or_principal_memory_payloads():
    dataset = InterviewQualityDataset.model_validate(build_dataset())

    assert all(
        case.source_boundary.classification == "synthetic"
        and case.source_boundary.contains_real_candidate_data is False
        and case.source_boundary.contains_employer_confidential_data is False
        and case.source_boundary.contains_principal_memory is False
        for case in dataset.cases
    )
    assert all(case.gate_eligible is False for case in dataset.cases)
    assert all(case.annotation.review_status == "pending" for case in dataset.cases)
