import hashlib
import json

import pytest

from app.services.report import (
    DimensionScores,
    ReportCoverageV2,
    ReportEvidenceRefV2,
    ReportObservationV2,
    ScoreEvaluation,
)
from app.services.report_contract import (
    CanonicalQuestionResult,
    assemble_interview_report,
)
from app.services.report_summary import (
    REPORT_SUMMARY_PROMPT_SHA256,
    REPORT_SUMMARY_PROMPT_TEMPLATE,
    REPORT_SUMMARY_PROMPT_VERSION,
    build_cross_question_summary,
    build_summary_input,
)


def _coverage(status: str = "partial") -> ReportCoverageV2:
    per_dimension = {
        dimension: ScoreEvaluation(
            status=("evaluated" if dimension != "architecture" else "not_evaluated"),
            reason_code=(
                "sufficient_evidence"
                if dimension != "architecture"
                else "not_applicable"
            ),
            score=(75 if dimension != "architecture" else None),
            evidence_count=(1 if dimension != "architecture" else 0),
            eligible_count=(1 if dimension != "architecture" else 0),
            evaluated_count=(1 if dimension != "architecture" else 0),
        )
        for dimension in (
            "breadth",
            "depth",
            "architecture",
            "engineering",
            "communication",
        )
    }
    return ReportCoverageV2(
        status=status,
        evaluated_count=1,
        total_eligible_count=1,
        evidence_count=4,
        per_dimension=per_dimension,
    )


def _observation(
    suffix: str,
    *,
    type: str,
    topic: str,
    dimension: str = "engineering",
    severity: str = "medium",
    frequency: int = 1,
    questions: list[str] | None = None,
) -> ReportObservationV2:
    question_refs = questions or ["q1"]
    answer_refs = (
        []
        if type == "limitation"
        else [f"candidate:{question_id}:answer" for question_id in question_refs]
    )
    return ReportObservationV2(
        observation_id=f"obs-{suffix:0>16}",
        type=type,
        dimension=dimension,
        normalized_topic=topic,
        severity=severity,
        frequency=frequency,
        role_relevance="high",
        evidence_strength="high" if frequency >= 2 else "medium",
        question_refs=question_refs,
        answer_evidence_refs=answer_refs,
        knowledge_refs=[],
        confidence_band="high" if frequency >= 2 else "medium",
    )


def _evidence(*question_ids: str) -> list[ReportEvidenceRefV2]:
    return [
        ReportEvidenceRefV2(
            evidence_ref_id=f"candidate:{question_id}:answer",
            namespace="candidate",
            question_id=question_id,
            excerpt=f"Allowed answer evidence for {question_id}.",
        )
        for question_id in question_ids
    ]


def _observations() -> list[ReportObservationV2]:
    return [
        _observation(
            "1",
            type="gap",
            topic="measurable_outcomes",
            frequency=3,
            questions=["q1", "q2", "q3"],
        ),
        _observation(
            "2",
            type="risk",
            topic="risk_identification",
            severity="high",
            questions=["q4"],
        ),
        _observation(
            "3",
            type="strength",
            topic="tradeoff_analysis",
            frequency=2,
            questions=["q1", "q2"],
        ),
        _observation(
            "4",
            type="limitation",
            topic="coverage_architecture",
            dimension="architecture",
        ),
    ]


def test_deterministic_summary_prioritizes_high_risk_then_repeated_gap():
    result = build_cross_question_summary(
        observations=_observations(),
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3", "q4"),
    )

    assert result.generation_mode == "deterministic"
    assert result.degraded is False
    assert result.summary_observations[0].kind == "conclusion"
    assert result.summary_observations[0].observation_refs == [
        "obs-0000000000000002"
    ]
    assert result.summary_observations[1].kind == "gap"
    assert result.strengths[0].kind == "strength"
    assert result.limitations[0].text == "本轮未充分考察架构设计。"
    assert "结论仅限题目q4中的已引用回答" in result.summary


def test_single_question_signal_is_scoped_and_every_claim_is_traceable():
    result = build_cross_question_summary(
        observations=_observations(),
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3", "q4"),
    )

    claims = [*result.summary_observations, *result.strengths]
    assert all(claim.observation_refs for claim in claims)
    assert all(claim.evidence_refs for claim in claims)
    assert "题目q4" in result.summary_observations[0].text
    assert not any(
        set(left.observation_refs) & set(right.observation_refs)
        for index, left in enumerate(claims)
        for right in claims[index + 1 :]
    )


def test_summary_input_contains_only_referenced_structured_evidence():
    payload = build_summary_input(
        observations=_observations(),
        coverage=_coverage(),
        evidence_refs=[
            *_evidence("q1", "q2", "q3", "q4"),
            ReportEvidenceRefV2(
                evidence_ref_id="candidate:q-secret:answer",
                namespace="candidate",
                question_id="q-secret",
                excerpt="Principal Memory and private unreferenced text",
            ),
        ],
    )

    assert set(payload) == {
        "schema_version",
        "prompt_version",
        "prompt_sha256",
        "coverage",
        "observations",
        "evidence_snippets",
    }
    assert {item["evidence_ref_id"] for item in payload["evidence_snippets"]} == {
        "candidate:q1:answer",
        "candidate:q2:answer",
        "candidate:q3:answer",
        "candidate:q4:answer",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "q-secret" not in serialized
    assert "Principal Memory" not in serialized
    assert '"messages"' not in serialized
    assert '"feedbacks"' not in serialized


def test_provider_text_is_grounded_by_backend_resolved_refs():
    observations = _observations()

    def provider(payload):
        assert payload["prompt_version"] == REPORT_SUMMARY_PROMPT_VERSION
        return {
            "conclusion": {
                "text": "本轮应优先核查风险识别。",
                "observation_refs": ["obs-0000000000000002"],
            },
            "strengths": [
                {
                    "text": "本轮多题体现了技术取舍。",
                    "observation_refs": ["obs-0000000000000003"],
                }
            ],
            "gaps": [
                {
                    "text": "本轮多题缺少量化验证。",
                    "observation_refs": ["obs-0000000000000001"],
                }
            ],
            "limitations": [
                {
                    "text": "本轮未充分考察架构设计。",
                    "observation_refs": ["obs-0000000000000004"],
                }
            ],
        }

    result = build_cross_question_summary(
        observations=observations,
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3", "q4"),
        provider=provider,
    )

    assert result.generation_mode == "provider"
    assert result.provider_attempted is True
    assert result.summary_observations[0].evidence_refs == [
        "candidate:q4:answer"
    ]
    assert result.strengths[0].evidence_refs == [
        "candidate:q1:answer",
        "candidate:q2:answer",
    ]


def test_invalid_unknown_or_repeated_provider_claim_falls_back_deterministically():
    def invalid_provider(_payload):
        return {
            "conclusion": {
                "text": "风险结论。",
                "observation_refs": ["obs-0000000000000002"],
            },
            "strengths": [],
            "gaps": [
                {
                    "text": "同一观察换一种说法。",
                    "observation_refs": ["obs-0000000000000002"],
                }
            ],
            "limitations": [],
        }

    result = build_cross_question_summary(
        observations=_observations(),
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3", "q4"),
        provider=invalid_provider,
    )

    assert result.generation_mode == "deterministic_fallback"
    assert result.degraded is True
    assert result.reason_code == "summary_generation_failed"
    assert result.provider_attempted is True
    assert "题目q4" in result.summary_observations[0].text


def test_synonymous_provider_claims_with_different_refs_fall_back():
    observations = [
        *_observations(),
        _observation(
            "5",
            type="gap",
            topic="risk_identification",
            questions=["q5"],
        ),
    ]

    def duplicate_topic_provider(_payload):
        return {
            "conclusion": {
                "text": "优先核查风险。",
                "observation_refs": ["obs-0000000000000002"],
            },
            "strengths": [],
            "gaps": [
                {
                    "text": "风险说明不足。",
                    "observation_refs": ["obs-0000000000000005"],
                }
            ],
            "limitations": [],
        }

    result = build_cross_question_summary(
        observations=observations,
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3", "q4", "q5"),
        provider=duplicate_topic_provider,
    )

    assert result.generation_mode == "deterministic_fallback"
    claims = [*result.summary_observations, *result.strengths]
    assert len(
        {
            ref
            for claim in claims
            for ref in claim.observation_refs
            if ref in {
                "obs-0000000000000002",
                "obs-0000000000000005",
            }
        }
    ) == 1


def test_unpublished_observation_evidence_is_rejected_before_generation():
    with pytest.raises(ValueError, match="unpublished evidence ref"):
        build_cross_question_summary(
            observations=[
                _observation(
                    "6",
                    type="gap",
                    topic="technical_specificity",
                    questions=["q-missing"],
                )
            ],
            coverage=_coverage(),
            evidence_refs=[],
        )


def test_provider_exception_degrades_text_without_changing_score_state():
    def failing_provider(_payload):
        raise TimeoutError("provider timeout")

    result = assemble_interview_report(
        session_id="s-summary-fallback",
        question_results=[
            CanonicalQuestionResult(
                question_id="q1",
                question_text="Explain production verification.",
                user_answer="I compare latency before and after the rollout.",
                score=78,
                dimension_scores=DimensionScores(
                    breadth=76,
                    depth=77,
                    architecture=None,
                    engineering=80,
                    communication=79,
                ),
                applicable_dimensions=[
                    "breadth",
                    "depth",
                    "engineering",
                    "communication",
                ],
                dimension_evidence=[
                    {
                        "dimension": "engineering",
                        "observed": ["compare latency before and after"],
                        "missing": ["missing rollback verification"],
                        "quality_signals": ["metric"],
                    }
                ],
                rationale="The answer includes a measurable comparison.",
                critique="It misses rollback verification.",
                better_answer="Add rollback triggers and success criteria.",
                reference_chunk_ids=[],
                highlights=["Legacy highlight must not become the summary."],
            )
        ],
        reference_lookup={},
        summary_provider=failing_provider,
    )

    assert result.generation_status == "degraded"
    assert result.generation_reason_code == "summary_generation_failed"
    assert result.overall_score == 78
    assert result.score_status == "scored"
    assert result.summary != "Legacy highlight must not become the summary."
    assert result.technical_appendix.summary_generation_mode == (
        "deterministic_fallback"
    )


def test_prompt_hash_and_deterministic_output_are_stable():
    assert REPORT_SUMMARY_PROMPT_SHA256 == hashlib.sha256(
        REPORT_SUMMARY_PROMPT_TEMPLATE.encode("utf-8")
    ).hexdigest()

    forward = build_cross_question_summary(
        observations=_observations(),
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3", "q4"),
    )
    reverse = build_cross_question_summary(
        observations=list(reversed(_observations())),
        coverage=_coverage(),
        evidence_refs=list(reversed(_evidence("q1", "q2", "q3", "q4"))),
    )

    assert forward.model_dump(mode="json") == reverse.model_dump(mode="json")
