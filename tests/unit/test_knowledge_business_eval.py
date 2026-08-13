from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.services.knowledge_business_eval import (
    ALL_DIMENSIONS,
    BusinessEvalAnnotationGovernance,
    BusinessEvalAnnotationSet,
    BusinessEvalCase,
    BusinessEvalEngineIdentity,
    BusinessEvalGovernance,
    BusinessEvalOutput,
    BusinessEvalRatings,
    BusinessEvalTargetThresholds,
    KnowledgeBusinessEvalDataset,
    build_blind_business_eval_package,
    build_business_annotation_record,
    build_business_consensus_record,
    build_business_eval_threshold_registration,
    compare_blind_business_eval,
)


NOW = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)


def _identity(engine_id: str):
    return BusinessEvalEngineIdentity(
        engine_id=engine_id,
        engine_version=f"{engine_id}-v1",
        code_revision=f"revision-{engine_id}",
        code_tree_sha256=("a" if engine_id == "legacy" else "b") * 64,
        profile_sha256=("c" if engine_id == "legacy" else "d") * 64,
    )


def _case(
    case_id: str,
    *,
    target="followup",
    split="holdout",
    family=None,
    scenario="partial_answer",
    evidence_availability="available",
    evidence_sufficiency="sufficient",
    system_failure_scenario=False,
):
    output_kwargs = (
        {}
        if target == "followup"
        else {
            "score": 70,
            "repeated_scores": (68, 72),
            "confidence": "medium",
        }
    )
    return BusinessEvalCase(
        case_id=case_id,
        case_family=family or f"family-{case_id}",
        split=split,
        target=target,
        scenario_type=scenario,
        role="backend engineer",
        seniority="senior",
        question="How should a Redis lock be released safely?",
        candidate_answer="Compare the owner token before deleting the lock.",
        evidence_ids=("redis-lock",),
        evidence_availability=evidence_availability,
        evidence_sufficiency=evidence_sufficiency,
        system_failure_scenario=system_failure_scenario,
        baseline_output=BusinessEvalOutput(text="Please add details.", **output_kwargs),
        candidate_output=BusinessEvalOutput(
            text="How do you make the compare-and-delete atomic?",
            evidence_ids=("redis-lock",),
            **output_kwargs,
        ),
    )


def _dataset():
    return KnowledgeBusinessEvalDataset(
        dataset_version="business-v1",
        baseline_identity=_identity("legacy"),
        candidate_identity=_identity("hybrid"),
        governance=BusinessEvalGovernance(
            protocol_version="protocol-v1",
            split_frozen=True,
            outputs_frozen=True,
            randomized_blind_ab=True,
            minimum_annotators_per_case=2,
            annotator_roles=("independent senior backend interviewer",),
            minimum_qualification="five years backend interviewing",
            adjudication_rule="third expert adjudicates every disagreement",
            agreement_metric="krippendorff_alpha",
            minimum_agreement=0.8,
            frozen_at=NOW,
            provenance_record_sha256="e" * 64,
        ),
        cases=(
            _case("tuning-followup", split="tuning"),
            _case("tuning-reviewer", split="tuning", target="reviewer"),
            _case("holdout-followup"),
            _case("holdout-reviewer", target="reviewer"),
        ),
    )


def _release_dataset():
    seed = _dataset()
    scenarios = (
        "strong_answer",
        "partial_answer",
        "typical_error",
        "misunderstood_question",
        "skipped_or_empty",
        "terminology_stacking",
        "factual_hallucination",
        "cross_domain_answer",
    )
    cases = []
    for index in range(50):
        target = "followup" if index % 2 == 0 else "reviewer"
        split = "holdout" if index < 12 else "tuning"
        scenario = scenarios[index % len(scenarios)]
        case = _case(
            f"release-{index}",
            target=target,
            split=split,
            scenario=scenario,
            evidence_availability=(
                "unavailable" if target == "reviewer" and index == 1 else "available"
            ),
            evidence_sufficiency=(
                "empty" if target == "reviewer" and index == 3 else "sufficient"
            ),
            system_failure_scenario=(target == "reviewer" and index == 1),
        )
        if scenario == "skipped_or_empty":
            case = case.model_copy(update={"candidate_answer": ""})
        cases.append(case)
    return KnowledgeBusinessEvalDataset(
        dataset_version=seed.dataset_version,
        baseline_identity=seed.baseline_identity,
        candidate_identity=seed.candidate_identity,
        governance=seed.governance,
        cases=tuple(cases),
    )


def _ratings(target: str, value: float):
    return BusinessEvalRatings(
        A={dimension: value for dimension in ALL_DIMENSIONS[target]},
        B={dimension: value for dimension in ALL_DIMENSIONS[target]},
    )


def _annotations(dataset, package, *, started_at=NOW + timedelta(hours=2)):
    records = []
    consensus = []
    for case in package.cases:
        ratings = _ratings(case.target, 0.5)
        first = build_business_annotation_record(
            case_id=case.case_id,
            annotator_identity_sha256="1" * 64,
            ratings=ratings,
        )
        second = build_business_annotation_record(
            case_id=case.case_id,
            annotator_identity_sha256="2" * 64,
            ratings=ratings,
        )
        records.extend((first, second))
        consensus.append(
            build_business_consensus_record(
                case_id=case.case_id,
                adjudicator_identity_sha256="3" * 64,
                source_annotation_record_sha256s=(
                    first.annotation_record_sha256,
                    second.annotation_record_sha256,
                ),
                ratings=ratings,
            )
        )
    return BusinessEvalAnnotationSet(
        dataset_sha256=dataset.dataset_sha256(),
        package_sha256=package.package_sha256,
        split=package.split,
        governance=BusinessEvalAnnotationGovernance(
            protocol_version="protocol-v1",
            annotator_roles=("independent senior backend interviewer",),
            minimum_qualification="five years backend interviewing",
            minimum_annotators_per_case=2,
            blinded=True,
            adjudication_rule="third expert adjudicates every disagreement",
            agreement_metric="krippendorff_alpha",
            agreement_value=0.85,
            minimum_agreement=0.8,
            collection_started_at=started_at,
            collection_completed_at=started_at + timedelta(hours=1),
        ),
        records=tuple(records),
        consensus=tuple(consensus),
    )


def _thresholds():
    return {
        "followup": BusinessEvalTargetThresholds(
            primary_metric="answer_specificity",
            minimum_deltas={
                dimension: 0.0
                for dimension in ALL_DIMENSIONS["followup"][:5]
            },
            maximum_deltas={
                dimension: 0.0
                for dimension in ALL_DIMENSIONS["followup"][5:]
            },
        ),
        "reviewer": BusinessEvalTargetThresholds(
            primary_metric="expert_agreement",
            minimum_deltas={
                dimension: 0.0
                for dimension in ALL_DIMENSIONS["reviewer"][:6]
            },
            maximum_deltas={
                dimension: 0.0
                for dimension in ALL_DIMENSIONS["reviewer"][6:]
            },
        ),
    }


def test_blind_package_is_deterministic_and_does_not_expose_engine_labels():
    dataset = _dataset()
    package, mapping = build_blind_business_eval_package(
        dataset, split="holdout", seed="secret-seed", created_at=NOW
    )
    repeated, repeated_mapping = build_blind_business_eval_package(
        dataset, split="holdout", seed="secret-seed", created_at=NOW
    )

    assert package == repeated
    assert mapping == repeated_mapping
    public_payload = package.model_dump_json()
    assert "legacy" not in public_payload
    assert "hybrid" not in public_payload
    assert set(mapping.cases[0].model_dump().values()) >= {"legacy", "hybrid"}


def test_release_shape_rejects_small_dataset_and_family_leakage():
    dataset = _dataset()
    with pytest.raises(ValueError, match="50–100"):
        dataset.validate_release_shape()

    cases = list(dataset.cases)
    cases[0] = cases[0].model_copy(update={"case_family": cases[2].case_family})
    with pytest.raises(ValidationError, match="cannot cross"):
        KnowledgeBusinessEvalDataset.model_validate(
            {**dataset.model_dump(), "cases": [case.model_dump() for case in cases]}
        )

    _release_dataset().validate_release_shape()


def test_holdout_compare_requires_registration_before_annotations_begin():
    dataset = _release_dataset()
    package, mapping = build_blind_business_eval_package(
        dataset, split="holdout", seed="secret-seed", created_at=NOW
    )
    annotations = _annotations(dataset, package)
    with pytest.raises(ValueError, match="requires threshold registration"):
        compare_blind_business_eval(dataset, package, mapping, annotations)

    late_registration = build_business_eval_threshold_registration(
        dataset,
        package,
        mapping,
        target_thresholds=_thresholds(),
        rationale_record_sha256="f" * 64,
        registered_at=NOW + timedelta(hours=3),
    )
    with pytest.raises(ValueError, match="before holdout annotation"):
        compare_blind_business_eval(
            dataset,
            package,
            mapping,
            annotations,
            registration=late_registration,
        )


def test_compare_requires_independent_complete_annotations():
    dataset = _dataset()
    package, mapping = build_blind_business_eval_package(
        dataset, split="tuning", seed="secret-seed", created_at=NOW
    )
    annotations = _annotations(dataset, package)
    incomplete = annotations.model_copy(update={"records": annotations.records[:-1]})
    with pytest.raises(ValueError, match="independent annotators"):
        compare_blind_business_eval(dataset, package, mapping, incomplete)


def test_holdout_result_is_privacy_safe_and_thresholded():
    dataset = _release_dataset()
    package, mapping = build_blind_business_eval_package(
        dataset, split="holdout", seed="secret-seed", created_at=NOW
    )
    annotations = _annotations(dataset, package)
    registration = build_business_eval_threshold_registration(
        dataset,
        package,
        mapping,
        target_thresholds=_thresholds(),
        rationale_record_sha256="f" * 64,
        registered_at=NOW + timedelta(hours=1),
    )
    result = compare_blind_business_eval(
        dataset,
        package,
        mapping,
        annotations,
        registration=registration,
        created_at=NOW + timedelta(hours=4),
    )

    assert result.thresholds_passed is True
    assert result.failed_thresholds == ()
    assert set(result.metrics) == {"followup", "reviewer"}
    assert len(result.annotation_set_sha256) == 64
    frozen_payload = result.model_dump_json()
    assert "How should" not in frozen_payload
    assert "owner token" not in frozen_payload
    assert "candidate_answer" not in frozen_payload
