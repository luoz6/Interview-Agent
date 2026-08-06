import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.report_semantic_dataset import (
    SemanticReviewEvidenceLedger,
    T49_REQUIRED_SCENARIOS,
    T49SemanticCaseManifest,
    append_semantic_review_evidence,
    empty_semantic_review_evidence_ledger,
    load_t49_semantic_dataset_manifest,
    validate_t49_semantic_dataset,
)
from app.services.report_semantic_review import (
    BlindedReviewPacket,
    HumanReviewSheet,
    ReviewAssignmentKey,
    build_blinded_review_artifacts,
    canonical_sha256,
    disabled_offline_judge_config,
    empty_human_review_sheet,
    evaluate_semantic_review_gate,
    load_semantic_review_dataset,
    semantic_review_dataset_sha256,
)


DATASET_PATH = Path("tests/fixtures/report_semantic_blind_test_v1.json")
MANIFEST_PATH = Path(
    "tests/fixtures/report_semantic_blind_test_manifest_v1.json"
)
GATE_CONFIG_PATH = Path("config/interview_quality_v1_gate.json")
ARTIFACT_DIR = Path("reports/interview-quality-v1/t49-blind-review-v1")
SEED = "t49-semantic-review-frozen-seed-2026-08-06"


def _dataset():
    return load_semantic_review_dataset(DATASET_PATH)


def _manifest():
    return load_t49_semantic_dataset_manifest(MANIFEST_PATH)


def _artifacts():
    return build_blinded_review_artifacts(
        _dataset(),
        randomization_seed=SEED,
    )


def _not_run_gate(artifacts):
    return evaluate_semantic_review_gate(
        source_dataset=_dataset(),
        packet=artifacts.packet,
        assignment_key=artifacts.assignment_key,
        review_sheet=empty_human_review_sheet(artifacts.packet),
        judge_config=disabled_offline_judge_config(artifacts.packet),
    )


def test_frozen_t49_dataset_and_manifest_close_the_plan_sample_scope():
    dataset = _dataset()
    manifest = _manifest()
    result = validate_t49_semantic_dataset(
        dataset=dataset,
        dataset_path=DATASET_PATH,
        manifest=manifest,
        gate_config_path=GATE_CONFIG_PATH,
    )

    assert result.status == "PASS"
    assert result.issue_codes == []
    assert result.sample_size == 24
    assert result.critical_case_count == 20
    assert set(result.covered_scenarios) == T49_REQUIRED_SCENARIOS
    assert result.missing_scenarios == []
    assert result.publish_count == 22
    assert result.publish_degraded_count == 2
    assert result.cases_with_saved_v1_rejection_reasons == 22
    assert result.cases_with_saved_v2_rejection_reasons == 0
    assert semantic_review_dataset_sha256(DATASET_PATH) == (
        manifest.dataset_raw_sha256
    )
    assert canonical_sha256(dataset) == manifest.dataset_canonical_sha256


def test_frozen_t49_source_boundary_and_rejection_reasons_are_explicit():
    dataset = _dataset()
    manifest = _manifest()
    manifest_by_id = {case.pair_id: case for case in manifest.cases}

    assert all(pair.source_classification == "synthetic" for pair in dataset.pairs)
    assert not any(pair.contains_real_candidate_data for pair in dataset.pairs)
    assert not any(pair.contains_principal_memory for pair in dataset.pairs)
    assert set(manifest_by_id) == {pair.pair_id for pair in dataset.pairs}

    for pair in dataset.pairs:
        case = manifest_by_id[pair.pair_id]
        assert case.critical_fabrication_case == pair.critical_fabrication_case
        assert case.expected_v2_rejection_reasons == []
        if pair.critical_fabrication_case:
            assert case.expected_v1_rejection_reasons
            assert case.forbidden_candidate_claims


def test_provider_component_failure_cases_must_publish_degraded():
    manifest = _manifest()
    degraded = {
        case.pair_id
        for case in manifest.cases
        if case.expected_v2_disposition == "publish_degraded"
    }
    assert degraded == {
        "t49-summary-provider-failure",
        "t49-action-provider-failure",
    }

    provider_failure_case = next(
        case
        for case in manifest.cases
        if case.pair_id == "t49-summary-provider-failure"
    )
    payload = provider_failure_case.model_dump(mode="json")
    payload["expected_v2_disposition"] = "publish"
    with pytest.raises(
        ValidationError,
        match="must publish degraded",
    ):
        T49SemanticCaseManifest.model_validate(payload)


def test_t49_dataset_meets_every_report_quality_semantic_minimum():
    gate = json.loads(GATE_CONFIG_PATH.read_text(encoding="utf-8"))
    metrics = gate["metric_groups"]["report_quality"]
    dataset = _dataset()
    sample_size = len(dataset.pairs)
    critical_count = sum(
        pair.critical_fabrication_case for pair in dataset.pairs
    )

    assert critical_count >= metrics[
        "adversarial_experience_fabrication_observed_count"
    ]["min_sample_size"]
    for metric in (
        "cross_question_summary_coverage_rate",
        "technical_correctness_blind_review_pass_rate",
        "actionability_blind_review_pass_rate",
    ):
        assert sample_size >= metrics[metric]["min_sample_size"]


def test_blinded_packet_has_full_cohort_without_version_mapping():
    artifacts = _artifacts()
    packet_payload = artifacts.packet.model_dump(mode="json")
    serialized = json.dumps(packet_payload, ensure_ascii=False, sort_keys=True)

    assert len(artifacts.packet.pairs) == 24
    assert sum(
        pair.critical_fabrication_case for pair in artifacts.packet.pairs
    ) == 20
    assert artifacts.assignment_key.packet_sha256 == canonical_sha256(
        artifacts.packet
    )
    assert "variant_a_version" not in serialized
    assert "variant_b_version" not in serialized
    assert "randomization_seed" not in serialized
    assert not hasattr(artifacts.packet, "assignments")


def test_empty_t49_human_sheet_preserves_not_run_and_null_statistics():
    artifacts = _artifacts()
    result = _not_run_gate(artifacts)

    assert result.quality_status == (
        "BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN"
    )
    assert result.human_review_status == "NOT_RUN"
    assert result.sample_size == 24
    assert result.critical_case_count == 20
    assert result.completed_judgment_count == 0
    assert result.independent_reviewer_count == 0
    assert result.provider_calls == 0
    assert result.offline_judge_used is False
    assert result.issue_codes == []
    assert result.v2_fabrication_observed_count is None
    assert result.v2_fabrication_uncertain_count is None
    assert result.false_positive_count is None
    assert result.false_negative_count is None
    assert result.v2_preferred_count is None
    assert result.v1_preferred_count is None
    assert result.tie_count is None
    assert result.v2_technical_correctness_pass_rate is None
    assert result.v2_answer_support_pass_rate is None
    assert result.v2_summary_coverage_pass_rate is None
    assert result.v2_actionability_pass_rate is None
    assert result.v2_tone_calibration_pass_rate is None
    assert result.v2_helpfulness_noninferiority_rate is None


def test_committed_handoff_artifacts_replay_to_the_frozen_not_run_result():
    expected = _artifacts()
    packet = BlindedReviewPacket.model_validate_json(
        (ARTIFACT_DIR / "reviewer/packet.json").read_text(encoding="utf-8")
    )
    assignment_key = ReviewAssignmentKey.model_validate_json(
        (
            ARTIFACT_DIR / "coordinator-only/assignment-key.json"
        ).read_text(encoding="utf-8")
    )
    review_sheet = HumanReviewSheet.model_validate_json(
        (
            ARTIFACT_DIR / "reviewer/empty-review-sheet.json"
        ).read_text(encoding="utf-8")
    )
    ledger = SemanticReviewEvidenceLedger.model_validate_json(
        (ARTIFACT_DIR / "evidence-ledger.json").read_text(encoding="utf-8")
    )

    assert packet == expected.packet
    assert assignment_key == expected.assignment_key
    assert assignment_key.packet_sha256 == canonical_sha256(packet)
    assert review_sheet.judgments == []
    assert review_sheet.packet_sha256 == canonical_sha256(packet)
    assert len(ledger.entries) == 1
    assert ledger.entries[0].gate_result == evaluate_semantic_review_gate(
        source_dataset=_dataset(),
        packet=packet,
        assignment_key=assignment_key,
        review_sheet=review_sheet,
        judge_config=disabled_offline_judge_config(packet),
    )
    assert ledger.entries[0].gate_result.quality_status == (
        "BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN"
    )
    assert ledger.entries[0].gate_result.provider_calls == 0


def test_semantic_review_evidence_ledger_appends_without_overwrite():
    artifacts = _artifacts()
    sheet = empty_human_review_sheet(artifacts.packet)
    gate_result = _not_run_gate(artifacts)
    ledger = empty_semantic_review_evidence_ledger(
        dataset_id=artifacts.packet.dataset_id,
        packet_sha256=canonical_sha256(artifacts.packet),
    )

    first = append_semantic_review_evidence(
        ledger,
        entry_id="t49-not-run-initial",
        recorded_at="2026-08-06T00:00:00Z",
        review_sheet=sheet,
        gate_result=gate_result,
    )
    first_entry_payload = first.entries[0].model_dump(mode="json")
    second = append_semantic_review_evidence(
        first,
        entry_id="t49-not-run-recheck",
        recorded_at="2026-08-06T00:01:00Z",
        review_sheet=sheet,
        gate_result=gate_result,
    )

    assert ledger.entries == []
    assert len(first.entries) == 1
    assert len(second.entries) == 2
    assert second.entries[0].model_dump(mode="json") == first_entry_payload
    assert second.entries[1].previous_entry_sha256 == (
        second.entries[0].entry_sha256
    )

    with pytest.raises(ValueError, match="already exists"):
        append_semantic_review_evidence(
            second,
            entry_id="t49-not-run-initial",
            recorded_at="2026-08-06T00:02:00Z",
            review_sheet=sheet,
            gate_result=gate_result,
        )


def test_semantic_review_evidence_ledger_rejects_tampering_on_load_and_append():
    artifacts = _artifacts()
    sheet = empty_human_review_sheet(artifacts.packet)
    gate_result = _not_run_gate(artifacts)
    first = append_semantic_review_evidence(
        empty_semantic_review_evidence_ledger(
            dataset_id=artifacts.packet.dataset_id,
            packet_sha256=canonical_sha256(artifacts.packet),
        ),
        entry_id="t49-not-run-initial",
        recorded_at="2026-08-06T00:00:00Z",
        review_sheet=sheet,
        gate_result=gate_result,
    )
    ledger = append_semantic_review_evidence(
        first,
        entry_id="t49-not-run-recheck",
        recorded_at="2026-08-06T00:01:00Z",
        review_sheet=sheet,
        gate_result=gate_result,
    )

    payload = ledger.model_dump(mode="json")
    payload["entries"][0]["gate_result"]["provider_calls"] = 1
    with pytest.raises(ValidationError, match="gate result hash mismatch"):
        SemanticReviewEvidenceLedger.model_validate(payload)

    old_entry_tampering = ledger.model_dump(mode="json")
    old_entry_tampering["entries"][0]["recorded_at"] = (
        "2026-08-06T00:00:01Z"
    )
    with pytest.raises(ValidationError, match="evidence entry hash mismatch"):
        SemanticReviewEvidenceLedger.model_validate(old_entry_tampering)

    chain_tampering = ledger.model_dump(mode="json")
    chain_tampering["entries"][1]["previous_entry_sha256"] = "0" * 64
    second_entry_payload = {
        key: value
        for key, value in chain_tampering["entries"][1].items()
        if key != "entry_sha256"
    }
    chain_tampering["entries"][1]["entry_sha256"] = canonical_sha256(
        second_entry_payload
    )
    with pytest.raises(ValidationError, match="evidence chain is broken"):
        SemanticReviewEvidenceLedger.model_validate(chain_tampering)

    invalid_in_memory_ledger = ledger.model_copy(
        update={"packet_sha256": "0" * 64}
    )
    with pytest.raises(
        ValidationError,
        match="evidence packet hash mismatch",
    ):
        append_semantic_review_evidence(
            invalid_in_memory_ledger,
            entry_id="t49-invalid-append",
            recorded_at="2026-08-06T00:03:00Z",
            review_sheet=sheet,
            gate_result=gate_result,
        )


def test_t49_offline_review_code_is_not_imported_by_runtime_modules():
    allowed = {
        Path("app/services/report_semantic_review.py"),
        Path("app/services/report_semantic_dataset.py"),
    }
    offenders = []
    for path in Path("app").rglob("*.py"):
        if path in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if (
            "report_semantic_review" in source
            or "report_semantic_dataset" in source
        ):
            offenders.append(path.as_posix())

    assert offenders == []
