from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain.knowledge.evidence import EvidenceAvailability, EvidenceSufficiency
from app.services.knowledge_evidence_eval import (
    EVIDENCE_METRIC_NAMES,
    EvidenceCalibrationCase,
    EvidenceCalibrationDataset,
    EvidenceEvalGovernance,
    EvidenceEvalIdentity,
    EvidenceEvalLineageRef,
    EvidenceEvalObservation,
    build_evidence_eval_artifact,
    build_evidence_observation_batch,
    build_evidence_threshold_registration,
    compare_evidence_eval_artifacts,
)


NOW = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)


def _case(index: int, *, split: str):
    state = index % 5
    if state == 0:
        availability = EvidenceAvailability.AVAILABLE
        sufficiency = EvidenceSufficiency.SUFFICIENT
        relevant = (f"chunk-{index}",)
        signals = ((f"{index:064x}"[-64:]),)
    elif state == 1:
        availability = EvidenceAvailability.DEGRADED
        sufficiency = EvidenceSufficiency.WEAK
        relevant = (f"chunk-{index}",)
        signals = ((f"{index:064x}"[-64:]),)
    elif state == 2:
        availability = EvidenceAvailability.AVAILABLE
        sufficiency = EvidenceSufficiency.INSUFFICIENT
        relevant = (f"chunk-{index}",)
        signals = ((f"{index:064x}"[-64:]),)
    elif state == 3:
        availability = EvidenceAvailability.AVAILABLE
        sufficiency = EvidenceSufficiency.EMPTY
        relevant = ()
        signals = ()
    else:
        availability = EvidenceAvailability.UNAVAILABLE
        sufficiency = EvidenceSufficiency.NOT_EVALUATED
        relevant = ()
        signals = ()
    return EvidenceCalibrationCase(
        case_id=f"case-{index}",
        case_family=f"family-{index}",
        split=split,
        topic_id="redis-lock" if index % 2 else "rocketmq-delivery",
        question_input_sha256=("a" if index % 2 else "b") * 64,
        relevant_evidence_ids=relevant,
        expected_signal_sha256s=signals,
        expected_availability=availability,
        expected_sufficiency=sufficiency,
        annotator_identity_sha256s=("1" * 64, "2" * 64),
        annotation_record_sha256s=("3" * 64, "4" * 64),
        consensus_record_sha256="5" * 64,
    )


def _dataset():
    cases = tuple(
        _case(index, split="holdout" if index < 8 else "tuning")
        for index in range(30)
    )
    return EvidenceCalibrationDataset(
        dataset_version="evidence-v1",
        corpus_manifest_sha256="c" * 64,
        governance=EvidenceEvalGovernance(
            annotation_protocol_version="evidence-protocol-v1",
            annotator_role="independent senior backend interviewer",
            minimum_annotators_per_case=2,
            implementation_output_blinded=True,
            split_frozen=True,
            agreement_metric="krippendorff_alpha",
            agreement_value=0.85,
            minimum_agreement=0.80,
            labeling_started_at=NOW - timedelta(days=2),
            split_frozen_at=NOW - timedelta(days=1),
            provenance_record_sha256="d" * 64,
        ),
        cases=cases,
    )


def _identity(engine: str):
    return EvidenceEvalIdentity(
        engine_version=engine,
        retrieval_artifact_sha256=("e" if engine == "legacy" else "f") * 64,
        code_revision=f"revision-{engine}",
        code_tree_sha256=("6" if engine == "legacy" else "7") * 64,
        selection_version=f"selection-{engine}",
        gate_version=f"gate-{engine}",
        corpus_manifest_sha256="c" * 64,
    )


def _observations(dataset, *, split: str, improved=False):
    observations = []
    for case in dataset.cases:
        if case.split != split:
            continue
        relevant = case.relevant_evidence_ids
        selected = relevant if relevant else ()
        availability = case.expected_availability
        sufficiency = case.expected_sufficiency
        if not improved and case.case_id in {"case-0", "case-10"}:
            selected = ("irrelevant",)
            sufficiency = EvidenceSufficiency.WEAK
        final = selected
        lineage = tuple(
            EvidenceEvalLineageRef(
                evidence_id=evidence_id,
                content_sha256=("9" if evidence_id == "irrelevant" else "a") * 64,
                corpus_manifest_sha256="c" * 64,
            )
            for evidence_id in final
        )
        observations.append(
            EvidenceEvalObservation(
                case_id=case.case_id,
                candidate_evidence_ids=(*relevant, "noise") if relevant else (),
                selected_evidence_ids=selected,
                final_evidence_ids=final,
                replayed_evidence_ids=final,
                final_evidence_lineage=lineage,
                covered_signal_sha256s=(
                    case.expected_signal_sha256s if improved else ()
                ),
                availability=availability,
                sufficiency=sufficiency,
                reason_codes=("evaluated",),
            )
        )
    return tuple(observations)


def _thresholds():
    minimum_deltas = {
        "observation_completeness_rate": 0.0,
        "question_binding_precision": 0.0,
        "evidence_precision_at_5": 0.0,
        "expected_signal_coverage": 0.0,
        "sufficiency_precision": 0.0,
        "sufficiency_recall": 0.0,
        "replay_stability_rate": 0.0,
    }
    maximum_deltas = {
        "irrelevant_fallback_binding_rate": 0.0,
        "failure_vs_no_evidence_confusion_rate": 0.0,
    }
    return minimum_deltas, maximum_deltas


def test_release_dataset_requires_all_states_topics_and_holdout_shape():
    dataset = _dataset()
    dataset.validate_release_shape()

    with pytest.raises(ValueError, match="30–100"):
        dataset.model_copy(update={"cases": dataset.cases[:10]}).validate_release_shape()


def test_case_rejects_unavailable_labeled_as_empty():
    payload = _case(4, split="holdout").model_dump()
    payload["expected_sufficiency"] = "empty"
    with pytest.raises(ValidationError, match="not_evaluated"):
        EvidenceCalibrationCase.model_validate(payload)


def test_metrics_report_all_plan_dimensions_and_topic_breakdown():
    dataset = _dataset()
    batch = build_evidence_observation_batch(
        dataset,
        _observations(dataset, split="tuning"),
        split="tuning",
        role="baseline",
        identity=_identity("legacy"),
        captured_at=NOW,
    )
    artifact = build_evidence_eval_artifact(
        dataset,
        batch,
        created_at=NOW + timedelta(minutes=1),
    )

    assert set(EVIDENCE_METRIC_NAMES) <= set(type(artifact.metrics).model_fields)
    assert artifact.metrics.observation_completeness_rate == 1.0
    assert artifact.metrics.irrelevant_fallback_binding_rate > 0
    assert set(artifact.metrics.topic_breakdown) == {"redis-lock", "rocketmq-delivery"}


def test_candidate_holdout_requires_registration_before_observations():
    dataset = _dataset()
    candidate_batch = build_evidence_observation_batch(
        dataset,
        _observations(dataset, split="holdout", improved=True),
        split="holdout",
        role="candidate",
        identity=_identity("hybrid"),
        captured_at=NOW + timedelta(hours=3),
    )
    with pytest.raises(ValueError, match="pre-registered"):
        build_evidence_eval_artifact(dataset, candidate_batch)

    baseline_batch = build_evidence_observation_batch(
        dataset,
        _observations(dataset, split="holdout"),
        split="holdout",
        role="baseline",
        identity=_identity("legacy"),
        captured_at=NOW,
    )
    baseline = build_evidence_eval_artifact(
        dataset,
        baseline_batch,
        created_at=NOW + timedelta(minutes=1),
    )
    minimum, maximum = _thresholds()
    registration = build_evidence_threshold_registration(
        baseline,
        candidate_identity=_identity("hybrid"),
        primary_metric="question_binding_precision",
        minimum_deltas=minimum,
        maximum_deltas=maximum,
        absolute_minimums={},
        absolute_maximums={},
        rationale_record_sha256="8" * 64,
        registered_at=NOW + timedelta(hours=4),
    )
    with pytest.raises(ValueError, match="predate candidate holdout observations"):
        build_evidence_eval_artifact(
            dataset,
            candidate_batch,
            registration=registration,
            created_at=NOW + timedelta(hours=5),
        )


def test_observation_requires_hash_and_corpus_lineage_for_every_final_evidence():
    with pytest.raises(ValidationError, match="complete hash and corpus lineage"):
        EvidenceEvalObservation(
            case_id="case-0",
            selected_evidence_ids=("chunk-0",),
            final_evidence_ids=("chunk-0",),
            replayed_evidence_ids=("chunk-0",),
            availability=EvidenceAvailability.AVAILABLE,
            sufficiency=EvidenceSufficiency.SUFFICIENT,
        )


def test_registration_rejects_incomplete_or_unstable_baseline():
    dataset = _dataset()
    observations = _observations(dataset, split="holdout")
    batch = build_evidence_observation_batch(
        dataset,
        observations[:-1],
        split="holdout",
        role="baseline",
        identity=_identity("legacy"),
        captured_at=NOW,
    )
    incomplete = build_evidence_eval_artifact(
        dataset, batch, created_at=NOW + timedelta(minutes=1)
    )
    minimum, maximum = _thresholds()
    with pytest.raises(ValueError, match="completeness must be 100%"):
        build_evidence_threshold_registration(
            incomplete,
            candidate_identity=_identity("hybrid"),
            primary_metric="question_binding_precision",
            minimum_deltas=minimum,
            maximum_deltas=maximum,
            absolute_minimums={},
            absolute_maximums={},
            rationale_record_sha256="8" * 64,
            registered_at=NOW + timedelta(hours=1),
        )


def test_paired_holdout_is_thresholded_and_privacy_safe():
    dataset = _dataset()
    baseline_batch = build_evidence_observation_batch(
        dataset,
        _observations(dataset, split="holdout"),
        split="holdout",
        role="baseline",
        identity=_identity("legacy"),
        captured_at=NOW,
    )
    baseline = build_evidence_eval_artifact(
        dataset,
        baseline_batch,
        created_at=NOW + timedelta(minutes=1),
    )
    minimum, maximum = _thresholds()
    registration = build_evidence_threshold_registration(
        baseline,
        candidate_identity=_identity("hybrid"),
        primary_metric="question_binding_precision",
        minimum_deltas=minimum,
        maximum_deltas=maximum,
        absolute_minimums={},
        absolute_maximums={},
        rationale_record_sha256="8" * 64,
        registered_at=NOW + timedelta(hours=1),
    )
    candidate_batch = build_evidence_observation_batch(
        dataset,
        _observations(dataset, split="holdout", improved=True),
        split="holdout",
        role="candidate",
        identity=_identity("hybrid"),
        captured_at=NOW + timedelta(hours=2),
    )
    candidate = build_evidence_eval_artifact(
        dataset,
        candidate_batch,
        registration=registration,
        created_at=NOW + timedelta(hours=3),
    )
    paired = compare_evidence_eval_artifacts(
        baseline,
        candidate,
        registration=registration,
        created_at=NOW + timedelta(hours=4),
    )

    assert paired.thresholds_passed is True
    assert paired.failed_thresholds == ()
    serialized = paired.model_dump_json()
    assert "question_input" not in serialized
    assert "candidate answer" not in serialized
    assert "knowledge body" not in serialized
