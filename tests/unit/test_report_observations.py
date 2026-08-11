import json

import pytest
from pydantic import ValidationError

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    ReportObservationV2,
)
from app.services.report_contract import build_report_evidence_refs
from app.services.report_coverage import (
    aggregate_report_coverage,
    dimension_evaluations,
    populate_feedback_dimension_evaluations,
)
from app.services.report_observations import aggregate_report_observations


def _feedback(
    question_id: str,
    *,
    dimension_evidence=None,
    critique="Needs latency metrics.",
    highlights=None,
    answer="Candidate answer",
    applicable=None,
    references=None,
):
    return InterviewFeedback(
        question_id=question_id,
        question_text="Explain the production design.",
        user_answer=answer,
        answer_state="answered",
        score=75,
        dimension_scores=DimensionScores(
            breadth=70,
            depth=74,
            architecture=76,
            engineering=78,
            communication=72,
        ),
        evaluation_status="evaluated",
        evaluation_reason_code="sufficient_evidence",
        applicable_dimensions=applicable
        or ["depth", "architecture", "engineering", "communication"],
        dimension_evidence=dimension_evidence or [],
        highlights=highlights or [],
        rationale="The answer names concrete technical steps.",
        critique=critique,
        better_answer="Add failure handling and verification.",
        references=references or [],
    )


def _aggregate(feedbacks, *, role_relevance=None, evidence_refs=None):
    feedbacks = populate_feedback_dimension_evaluations(list(feedbacks))
    coverage = aggregate_report_coverage(feedbacks)
    refs = (
        list(evidence_refs)
        if evidence_refs is not None
        else build_report_evidence_refs(feedbacks)
    )
    return aggregate_report_observations(
        feedbacks=feedbacks,
        dimension_evaluations=dimension_evaluations(coverage),
        evidence_refs=refs,
        role_relevance_by_dimension=role_relevance,
    )


def test_repeated_synonymous_signals_merge_with_stable_cross_question_order():
    knowledge = FeedbackReference(
        chunk_id="knowledge-1",
        title="Production metrics",
        source_type="theory",
        excerpt="Track latency and error rate.",
    )
    first = _feedback(
        "q1",
        dimension_evidence=[
            {
                "dimension": "engineering",
                "observed": ["I compared consistency and latency."],
                "missing": ["metric_gap: missing verification metric"],
                "quality_signals": ["tradeoff"],
            }
        ],
        critique="Needs a latency metric.",
        references=[knowledge],
    )
    second = _feedback(
        "q2",
        dimension_evidence=[
            {
                "dimension": "engineering",
                "observed": ["I described the cost of the fallback."],
                "missing": ["The response has no throughput metric."],
                "quality_signals": ["tradeoff"],
            }
        ],
        critique="Missing measurable error rate evidence.",
        references=[knowledge],
    )

    forward = _aggregate([first, second])
    reverse = _aggregate([second, first])

    assert [item.model_dump() for item in forward] == [
        item.model_dump() for item in reverse
    ]
    gap = next(
        item
        for item in forward
        if item.type == "gap" and item.normalized_topic == "measurable_outcomes"
    )
    strength = next(
        item
        for item in forward
        if item.type == "strength" and item.normalized_topic == "tradeoff_analysis"
    )
    assert gap.frequency == 2
    assert gap.question_refs == ["q1", "q2"]
    assert gap.answer_evidence_refs == [
        "candidate:q1:answer",
        "candidate:q2:answer",
    ]
    assert gap.evidence_strength == "high"
    assert gap.role_relevance == "high"
    assert gap.confidence_band == "high"
    assert strength.frequency == 2
    assert strength.severity == "medium"


def test_single_severe_risk_is_retained_without_frequency_inflation():
    observations = _aggregate(
        [
            _feedback(
                "q-risk",
                dimension_evidence=[
                    {
                        "dimension": "engineering",
                        "observed": ["I would deploy directly."],
                        "missing": ["security: unsafe data loss claim"],
                        "quality_signals": ["risk"],
                    }
                ],
                critique="This unsafe path can cause data loss.",
            )
        ]
    )

    risk = next(item for item in observations if item.type == "risk")
    assert risk.normalized_topic == "risk_identification"
    assert risk.frequency == 1
    assert risk.severity == "high"
    assert risk.answer_evidence_refs == ["candidate:q-risk:answer"]


def test_unassessed_dimension_produces_limitation_not_weakness():
    observations = _aggregate(
        [
            _feedback(
                "q1",
                dimension_evidence=[],
                critique="",
                applicable=["depth", "engineering", "communication"],
            )
        ]
    )

    architecture = [
        item for item in observations if item.dimension == "architecture"
    ]
    assert len(architecture) == 1
    assert architecture[0].type == "limitation"
    assert architecture[0].normalized_topic == "coverage_architecture"
    assert architecture[0].question_refs == ["q1"]
    assert architecture[0].answer_evidence_refs == []


def test_critique_cannot_create_gap_in_its_unassessed_preferred_dimension():
    observations = _aggregate(
        [
            _feedback(
                "q1",
                dimension_evidence=[],
                critique="Needs a latency metric.",
                applicable=["communication"],
            )
        ]
    )

    assert not any(
        item.type == "gap"
        and item.dimension == "engineering"
        and item.normalized_topic == "measurable_outcomes"
        for item in observations
    )
    assert any(
        item.type == "limitation" and item.dimension == "engineering"
        for item in observations
    )


def test_strength_gap_and_risk_without_answer_evidence_are_not_published():
    feedback = _feedback(
        "q1",
        dimension_evidence=[
            {
                "dimension": "engineering",
                "observed": ["A technical statement"],
                "missing": ["metric_gap: missing metric"],
                "quality_signals": ["metric"],
            }
        ],
    )

    observations = _aggregate([feedback], evidence_refs=[])

    assert all(item.type == "limitation" for item in observations)


def test_observations_never_copy_candidate_experience_facts():
    observations = _aggregate(
        [
            _feedback(
                "q1",
                answer=(
                    "At Acme I managed 50 engineers and raised QPS to 900000, "
                    "creating $12M in revenue."
                ),
                highlights=["Raised Acme QPS to 900000"],
                critique="Needs a latency metric.",
            )
        ]
    )

    serialized = json.dumps(
        [item.model_dump(mode="json") for item in observations],
        ensure_ascii=False,
    )
    for forbidden in ("Acme", "50 engineers", "900000", "$12M", "revenue"):
        assert forbidden not in serialized


def test_observation_model_rejects_unreferenced_publishable_claim():
    with pytest.raises(ValidationError, match="require answer evidence"):
        ReportObservationV2(
            observation_id="obs-0123456789abcdef",
            type="gap",
            dimension="engineering",
            normalized_topic="measurable_outcomes",
            severity="medium",
            frequency=1,
            role_relevance="high",
            evidence_strength="low",
            question_refs=["q1"],
            answer_evidence_refs=[],
            knowledge_refs=[],
            confidence_band="low",
        )
