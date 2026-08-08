import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    ReportEvidenceRefV2,
    ReportObservationV2,
)
from app.services.report_answer_guidance import (
    ANSWER_STRUCTURE_SUGGESTION,
    REPORT_ANSWER_GUIDANCE_VERSION,
    apply_safe_answer_guidance,
)


FIXTURE = Path(__file__).parent / "fixtures" / "report_fact_boundary_v1.json"


def _feedback(
    *,
    candidate_answer: str,
    proposed_rewrite: str,
) -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Explain the production design.",
        user_answer=candidate_answer,
        answer_state="answered",
        score=75,
        dimension_scores=DimensionScores(
            breadth=72,
            depth=74,
            architecture=76,
            engineering=78,
            communication=75,
        ),
        evaluation_status="evaluated",
        evaluation_reason_code="sufficient_evidence",
        applicable_dimensions=["engineering"],
        rationale="The answer contains one candidate-supported statement.",
        critique="The answer needs a measurable validation loop.",
        better_answer=proposed_rewrite,
        references=[
            FeedbackReference(
                chunk_id="knowledge-1",
                title="Generic production guidance",
                source_type="theory",
                excerpt="Track latency and error rate after rollout.",
            )
        ],
    )


def _observation(*, topic: str = "measurable_outcomes") -> ReportObservationV2:
    return ReportObservationV2(
        observation_id="obs-0000000000000001",
        type="gap",
        dimension="engineering",
        normalized_topic=topic,
        severity="medium",
        frequency=1,
        role_relevance="high",
        evidence_strength="medium",
        question_refs=["q1"],
        answer_evidence_refs=["candidate:q1:answer"],
        knowledge_refs=["reference:knowledge-1"],
        confidence_band="medium",
    )


def _evidence(
    candidate_answer: str = "Candidate answer evidence.",
) -> list[ReportEvidenceRefV2]:
    return [
        ReportEvidenceRefV2(
            evidence_ref_id="candidate:q1:answer",
            namespace="candidate",
            question_id="q1",
            excerpt=candidate_answer,
        ),
        ReportEvidenceRefV2(
            evidence_ref_id="reference:knowledge-1",
            namespace="reference",
            question_id="q1",
            source_id="knowledge-1",
            excerpt="Generic reference evidence.",
        ),
    ]


def test_frozen_adversarial_rewrites_publish_no_fabricated_experience():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    coverage_types = {
        item
        for case in fixture["cases"]
        for item in case["coverage_types"]
    }

    assert fixture["schema_version"] == "report-fact-boundary-adversarial-v1"
    assert fixture["limitations"]
    assert {
        "company",
        "scale",
        "responsibility",
        "metric",
        "money",
        "latency",
        "result",
        "knowledge_boundary",
        "memory_boundary",
    }.issubset(coverage_types)

    for case in fixture["cases"]:
        result = apply_safe_answer_guidance(
            feedbacks=[
                _feedback(
                    candidate_answer=case["candidate_answer"],
                    proposed_rewrite=case["proposed_rewrite"],
                )
            ],
            observations=[_observation()],
            evidence_refs=_evidence(case["candidate_answer"]),
        )
        feedback = result.feedbacks[0]
        serialized = json.dumps(
            {
                "better_answer": feedback.better_answer,
                "answer_structure_suggestion": (
                    feedback.answer_structure_suggestion
                ),
                "missing_technical_points": [
                    item.model_dump(mode="json")
                    for item in feedback.missing_technical_points
                ],
                "example_rewrite": feedback.example_rewrite,
            },
            ensure_ascii=False,
        )
        assert feedback.example_rewrite is None, case["case_id"]
        assert feedback.example_rewrite_evidence_refs == []
        assert result.unsafe_rewrite_omitted_count == 1
        for forbidden in case["forbidden_fragments"]:
            assert forbidden not in serialized, case["case_id"]


def test_exact_candidate_text_can_be_published_only_with_candidate_evidence():
    candidate_answer = "I used Redis and verified cache misses after rollout."
    result = apply_safe_answer_guidance(
        feedbacks=[
            _feedback(
                candidate_answer=candidate_answer,
                proposed_rewrite=candidate_answer,
            )
        ],
        observations=[_observation()],
        evidence_refs=_evidence(candidate_answer),
    )
    feedback = result.feedbacks[0]

    assert feedback.example_rewrite == candidate_answer
    assert feedback.example_rewrite_evidence_refs == ["candidate:q1:answer"]
    assert result.example_rewrite_published_count == 1
    assert "reference:knowledge-1" not in feedback.example_rewrite_evidence_refs


def test_memory_marker_is_omitted_even_when_it_appears_in_candidate_text():
    value = "Principal Memory says I led the migration."
    result = apply_safe_answer_guidance(
        feedbacks=[
            _feedback(candidate_answer=value, proposed_rewrite=value)
        ],
        observations=[_observation()],
        evidence_refs=_evidence(value),
    )

    assert result.feedbacks[0].example_rewrite is None
    assert "Principal Memory" not in result.feedbacks[0].better_answer


def test_missing_points_are_controlled_merged_and_fully_traceable():
    second = _observation().model_copy(
        update={
            "observation_id": "obs-0000000000000002",
            "dimension": "depth",
        }
    )
    result = apply_safe_answer_guidance(
        feedbacks=[
            _feedback(
                candidate_answer="I described the cache path.",
                proposed_rewrite="Invent a free-form answer.",
            )
        ],
        observations=[_observation(), second],
        evidence_refs=_evidence("I described the cache path."),
    )
    feedback = result.feedbacks[0]

    assert REPORT_ANSWER_GUIDANCE_VERSION == "report-answer-guidance-v1"
    assert feedback.answer_structure_suggestion == ANSWER_STRUCTURE_SUGGESTION
    assert len(feedback.missing_technical_points) == 1
    point = feedback.missing_technical_points[0]
    assert point.topic == "measurable_outcomes"
    assert point.observation_refs == [
        "obs-0000000000000001",
        "obs-0000000000000002",
    ]
    assert point.evidence_refs == [
        "candidate:q1:answer",
        "reference:knowledge-1",
    ]
    assert "[实际指标值]" in point.text
    assert "Invent a free-form answer" not in feedback.better_answer
    assert "当前事实不足" in feedback.better_answer


def test_feedback_model_enforces_example_rewrite_ref_coupling():
    payload = _feedback(
        candidate_answer="Candidate answer.",
        proposed_rewrite="Candidate answer.",
    ).model_dump(mode="json")
    payload["example_rewrite"] = "Candidate answer."

    with pytest.raises(ValidationError, match="requires candidate evidence refs"):
        InterviewFeedback.model_validate(payload)
