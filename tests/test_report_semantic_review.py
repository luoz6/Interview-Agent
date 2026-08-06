import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.report_semantic_review import (
    FabricationAssessment,
    HumanReviewSheet,
    HumanSemanticJudgment,
    OfflineJudgeBundle,
    OfflineJudgeConfig,
    OfflineJudgeFinding,
    VariantScores,
    build_blinded_review_artifacts,
    canonical_sha256,
    disabled_offline_judge_config,
    empty_human_review_sheet,
    evaluate_semantic_review_gate,
    load_semantic_review_dataset,
    semantic_review_dataset_sha256,
    text_sha256,
)


DATASET_PATH = Path("tests/fixtures/report_semantic_review_pairs_v1.json")
FACT_BOUNDARY_PATH = Path("tests/fixtures/report_fact_boundary_v1.json")
SEED = "semantic-review-frozen-seed-2026-08-06"


def _artifacts(seed: str = SEED):
    dataset = load_semantic_review_dataset(DATASET_PATH)
    return build_blinded_review_artifacts(dataset, randomization_seed=seed)


def _judgment(
    pair_id: str,
    reviewer_id: str,
    *,
    fabrication_a: str = "not_observed",
    fabrication_b: str = "not_observed",
    preferred_variant: str = "A",
    critical_checked: bool = True,
    false_positive: bool = False,
    false_negative: bool = False,
) -> HumanSemanticJudgment:
    scores = VariantScores(A=4, B=5)
    return HumanSemanticJudgment(
        pair_id=pair_id,
        reviewer_id=reviewer_id,
        reviewer_role="independent_technical_reviewer",
        independence_attested=True,
        assignment_was_hidden=True,
        technical_correctness=scores,
        answer_support=scores,
        experience_fabrication_A=FabricationAssessment(
            status=fabrication_a,
            rationale="Compared the report text with the candidate answer.",
            evidence_fragments=(
                ["unsupported candidate fact"]
                if fabrication_a == "observed"
                else []
            ),
        ),
        experience_fabrication_B=FabricationAssessment(
            status=fabrication_b,
            rationale="Compared the report text with the candidate answer.",
            evidence_fragments=(
                ["unsupported candidate fact"]
                if fabrication_b == "observed"
                else []
            ),
        ),
        summary_coverage=scores,
        actionability=scores,
        tone_calibration=scores,
        preferred_variant=preferred_variant,
        preference_rationale="The selected variant is more grounded and actionable.",
        critical_forbidden_item_checked=critical_checked,
        false_positive=false_positive,
        false_negative=false_negative,
        error_notes=(
            "Recorded during adjudication."
            if false_positive or false_negative
            else None
        ),
    )


def _sheet(artifacts, judgments):
    return HumanReviewSheet(
        protocol_version="report-semantic-blind-review-v1",
        packet_sha256=canonical_sha256(artifacts.packet),
        judgments=judgments,
    )


def _evaluate(artifacts, sheet, **kwargs):
    return evaluate_semantic_review_gate(
        source_dataset=load_semantic_review_dataset(DATASET_PATH),
        packet=artifacts.packet,
        assignment_key=artifacts.assignment_key,
        review_sheet=sheet,
        judge_config=kwargs.pop(
            "judge_config", disabled_offline_judge_config(artifacts.packet)
        ),
        **kwargs,
    )


def test_frozen_pairs_cover_the_t43_fact_boundary_cases_without_real_data():
    dataset = load_semantic_review_dataset(DATASET_PATH)
    fact_boundary = json.loads(FACT_BOUNDARY_PATH.read_text(encoding="utf-8"))

    assert len(dataset.pairs) == 6
    assert {pair.case_id for pair in dataset.pairs} == {
        case["case_id"] for case in fact_boundary["cases"]
    }
    assert all(pair.critical_fabrication_case for pair in dataset.pairs)
    assert all(pair.source_classification == "synthetic" for pair in dataset.pairs)
    assert not any(pair.contains_real_candidate_data for pair in dataset.pairs)
    assert not any(pair.contains_principal_memory for pair in dataset.pairs)
    assert len(semantic_review_dataset_sha256(DATASET_PATH)) == 64


def test_randomization_is_reproducible_and_assignment_key_is_separate():
    first = _artifacts()
    replay = _artifacts()
    other = _artifacts("semantic-review-another-frozen-seed")

    assert first == replay
    assert first.packet == replay.packet
    assert first.assignment_key.assignments != other.assignment_key.assignments
    assert first.assignment_key.packet_sha256 == canonical_sha256(first.packet)
    assert first.packet.seed_commitment_sha256 == text_sha256(SEED)
    assert not hasattr(first.packet, "assignments")
    assert not hasattr(first.packet, "randomization_seed")
    assert {
        variant.label
        for pair in first.packet.pairs
        for variant in (pair.variant_a, pair.variant_b)
    } == {"A", "B"}


def test_reviewer_packet_contains_no_source_version_mapping():
    packet_payload = _artifacts().packet.model_dump(mode="json")
    serialized = json.dumps(packet_payload, ensure_ascii=False, sort_keys=True)

    assert "variant_a_version" not in serialized
    assert "variant_b_version" not in serialized
    assert "randomization_seed" not in serialized
    for pair in packet_payload["pairs"]:
        assert set(pair) == {
            "pair_id",
            "case_id",
            "candidate_answer",
            "candidate_answer_sha256",
            "coverage_types",
            "critical_fabrication_case",
            "variant_a",
            "variant_b",
        }


def test_blank_human_sheet_is_truthfully_blocked_with_zero_provider_calls():
    artifacts = _artifacts()
    result = _evaluate(artifacts, empty_human_review_sheet(artifacts.packet))

    assert result.quality_status == "BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN"
    assert result.human_review_status == "NOT_RUN"
    assert result.sample_size == 6
    assert result.critical_case_count == 6
    assert result.completed_judgment_count == 0
    assert result.v2_fabrication_observed_count is None
    assert result.false_positive_count is None
    assert result.v2_preferred_count is None
    assert result.v2_technical_correctness_pass_rate is None
    assert result.v2_summary_coverage_pass_rate is None
    assert result.provider_calls == 0
    assert result.offline_judge_used is False
    assert result.issue_codes == []


def test_one_independent_reviewer_does_not_satisfy_critical_double_review():
    artifacts = _artifacts()
    judgments = [
        _judgment(pair.pair_id, "reviewer-one")
        for pair in artifacts.packet.pairs
    ]
    result = _evaluate(artifacts, _sheet(artifacts, judgments))

    assert result.quality_status == "BLOCKED_CRITICAL_DOUBLE_REVIEW_NOT_RUN"
    assert result.human_review_status == "INCOMPLETE"
    assert result.missing_primary_review_pair_ids == []
    assert result.missing_second_review_pair_ids == [
        pair.pair_id for pair in artifacts.packet.pairs
    ]
    assert result.independent_reviewer_count == 1


def test_two_distinct_independent_reviewers_can_complete_the_protocol():
    artifacts = _artifacts()
    judgments = [
        _judgment(pair.pair_id, reviewer_id)
        for pair in artifacts.packet.pairs
        for reviewer_id in ("reviewer-one", "reviewer-two")
    ]
    result = _evaluate(artifacts, _sheet(artifacts, judgments))

    assert result.quality_status == "PASS"
    assert result.human_review_status == "COMPLETE"
    assert result.completed_judgment_count == 12
    assert result.independent_reviewer_count == 2
    assert result.missing_primary_review_pair_ids == []
    assert result.missing_second_review_pair_ids == []
    assert result.v2_fabrication_observed_count == 0
    assert result.v2_technical_correctness_pass_rate == 1.0
    assert result.v2_answer_support_pass_rate == 1.0
    assert result.v2_summary_coverage_pass_rate == 1.0
    assert result.v2_actionability_pass_rate == 1.0
    assert result.v2_tone_calibration_pass_rate == 1.0


def test_duplicate_rows_from_one_reviewer_fail_protocol_integrity():
    artifacts = _artifacts()
    judgments = [
        _judgment(pair.pair_id, "reviewer-one")
        for pair in artifacts.packet.pairs
        for _ in range(2)
    ]
    result = _evaluate(artifacts, _sheet(artifacts, judgments))

    assert result.quality_status == "FAIL_PROTOCOL_INTEGRITY"
    assert "DUPLICATE_REVIEWER_PAIR_JUDGMENT" in result.issue_codes
    assert result.independent_reviewer_count == 1


def test_observed_v2_experience_fabrication_fails_after_unblinding():
    artifacts = _artifacts()
    first_assignment = artifacts.assignment_key.assignments[0]
    v2_label = (
        "A" if first_assignment.variant_a_version == "v2" else "B"
    )
    judgments = []
    for pair in artifacts.packet.pairs:
        for reviewer_id in ("reviewer-one", "reviewer-two"):
            kwargs = {}
            if pair.pair_id == first_assignment.pair_id:
                kwargs[f"fabrication_{v2_label.lower()}"] = "observed"
            judgments.append(_judgment(pair.pair_id, reviewer_id, **kwargs))
    result = _evaluate(artifacts, _sheet(artifacts, judgments))

    assert result.quality_status == "FAIL_CANDIDATE_EXPERIENCE_FABRICATION"
    assert result.human_review_status == "COMPLETE"
    assert result.v2_fabrication_observed_count == 2


def test_uncertain_v2_forbidden_item_cannot_be_reported_as_pass():
    artifacts = _artifacts()
    first_assignment = artifacts.assignment_key.assignments[0]
    v2_label = (
        "A" if first_assignment.variant_a_version == "v2" else "B"
    )
    judgments = []
    for pair in artifacts.packet.pairs:
        for reviewer_id in ("reviewer-one", "reviewer-two"):
            kwargs = {}
            if pair.pair_id == first_assignment.pair_id:
                kwargs[f"fabrication_{v2_label.lower()}"] = "uncertain"
            judgments.append(_judgment(pair.pair_id, reviewer_id, **kwargs))
    result = _evaluate(artifacts, _sheet(artifacts, judgments))

    assert result.quality_status == "BLOCKED_CRITICAL_FORBIDDEN_ITEM_UNRESOLVED"
    assert result.v2_fabrication_uncertain_count == 2


def test_false_positive_and_false_negative_fields_are_counted():
    artifacts = _artifacts()
    judgments = []
    for index, pair in enumerate(artifacts.packet.pairs):
        for reviewer_id in ("reviewer-one", "reviewer-two"):
            judgments.append(
                _judgment(
                    pair.pair_id,
                    reviewer_id,
                    false_positive=index == 0 and reviewer_id == "reviewer-one",
                    false_negative=index == 1 and reviewer_id == "reviewer-two",
                )
            )
    result = _evaluate(artifacts, _sheet(artifacts, judgments))

    assert result.quality_status == "PASS"
    assert result.false_positive_count == 1
    assert result.false_negative_count == 1


def test_completed_review_below_frozen_semantic_threshold_fails():
    artifacts = _artifacts()
    low_scores = VariantScores(A=3, B=3)
    judgments = [
        _judgment(pair.pair_id, reviewer_id).model_copy(
            update={"technical_correctness": low_scores}
        )
        for pair in artifacts.packet.pairs
        for reviewer_id in ("reviewer-one", "reviewer-two")
    ]
    result = _evaluate(artifacts, _sheet(artifacts, judgments))

    assert result.quality_status == "FAIL_SEMANTIC_THRESHOLDS"
    assert result.human_review_status == "COMPLETE"
    assert result.v2_technical_correctness_pass_rate == 0.0


def test_optional_offline_judge_cannot_replace_missing_human_review():
    artifacts = _artifacts()
    prompt = "Compare A and B using the frozen semantic review rubric."
    config = OfflineJudgeConfig(
        enabled=True,
        provider="frozen-test-provider",
        model="frozen-test-model-2026-08-06",
        prompt_version="semantic-judge-prompt-v1",
        prompt_text=prompt,
        prompt_sha256=text_sha256(prompt),
        dataset_sha256=artifacts.packet.dataset_sha256,
    )
    bundle = OfflineJudgeBundle(
        config_sha256=canonical_sha256(config),
        packet_sha256=canonical_sha256(artifacts.packet),
        provider_calls=6,
        findings=[
            OfflineJudgeFinding(
                pair_id=pair.pair_id,
                fabrication_A="not_observed",
                fabrication_B="not_observed",
                preferred_variant="B",
                rationale="Injected offline result for protocol validation.",
            )
            for pair in artifacts.packet.pairs
        ],
    )

    result = _evaluate(
        artifacts,
        empty_human_review_sheet(artifacts.packet),
        judge_config=config,
        judge_bundle=bundle,
    )

    assert result.quality_status == "BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN"
    assert result.provider_calls == 6
    assert result.offline_judge_used is True


def test_semantic_judge_is_fail_closed_in_online_execution_context():
    artifacts = _artifacts()
    prompt = "Frozen offline-only prompt."
    config = OfflineJudgeConfig(
        enabled=True,
        provider="frozen-test-provider",
        model="frozen-test-model",
        prompt_version="semantic-judge-prompt-v1",
        prompt_text=prompt,
        prompt_sha256=text_sha256(prompt),
        dataset_sha256=artifacts.packet.dataset_sha256,
    )

    result = _evaluate(
        artifacts,
        empty_human_review_sheet(artifacts.packet),
        judge_config=config,
        execution_context="online",
    )

    assert result.quality_status == "FAIL_PROTOCOL_INTEGRITY"
    assert "ONLINE_SEMANTIC_JUDGE_PROHIBITED" in result.issue_codes
    assert result.provider_calls == 0


def test_enabled_offline_judge_requires_a_matching_frozen_prompt_hash():
    artifacts = _artifacts()

    with pytest.raises(ValidationError, match="prompt_sha256 mismatch"):
        OfflineJudgeConfig(
            enabled=True,
            provider="provider",
            model="model-version",
            prompt_version="prompt-version",
            prompt_text="frozen prompt",
            prompt_sha256="0" * 64,
            dataset_sha256=artifacts.packet.dataset_sha256,
        )


def test_packet_or_assignment_tampering_fails_protocol_integrity():
    artifacts = _artifacts()
    bad_key = artifacts.assignment_key.model_copy(
        update={"packet_sha256": "0" * 64}
    )

    result = evaluate_semantic_review_gate(
        source_dataset=load_semantic_review_dataset(DATASET_PATH),
        packet=artifacts.packet,
        assignment_key=bad_key,
        review_sheet=empty_human_review_sheet(artifacts.packet),
        judge_config=disabled_offline_judge_config(artifacts.packet),
    )

    assert result.quality_status == "FAIL_PROTOCOL_INTEGRITY"
    assert "ASSIGNMENT_PACKET_HASH_MISMATCH" in result.issue_codes


def test_blinded_content_cannot_drift_from_the_frozen_source_dataset():
    artifacts = _artifacts()
    pair = artifacts.packet.pairs[0]
    changed_presentation = pair.variant_a.presentation.model_copy(
        update={"summary": "A coordinator-mutated summary."}
    )
    changed_variant = pair.variant_a.model_copy(
        update={
            "presentation": changed_presentation,
            "content_sha256": canonical_sha256(changed_presentation),
        }
    )
    changed_pair = pair.model_copy(update={"variant_a": changed_variant})
    changed_packet = artifacts.packet.model_copy(
        update={"pairs": [changed_pair, *artifacts.packet.pairs[1:]]}
    )
    changed_key = artifacts.assignment_key.model_copy(
        update={"packet_sha256": canonical_sha256(changed_packet)}
    )

    result = evaluate_semantic_review_gate(
        source_dataset=load_semantic_review_dataset(DATASET_PATH),
        packet=changed_packet,
        assignment_key=changed_key,
        review_sheet=empty_human_review_sheet(changed_packet),
        judge_config=disabled_offline_judge_config(changed_packet),
    )

    assert result.quality_status == "FAIL_PROTOCOL_INTEGRITY"
    assert "BLINDED_VARIANT_SOURCE_MISMATCH" in result.issue_codes


def test_observed_fabrication_requires_a_review_evidence_fragment():
    with pytest.raises(ValidationError, match="requires evidence fragments"):
        FabricationAssessment(
            status="observed",
            rationale="The report adds an unsupported fact.",
            evidence_fragments=[],
        )
