from app.services.report import DimensionScores
from app.services.report_contract import (
    CanonicalQuestionResult,
    assemble_interview_report,
)


def make_question_result(
    *,
    question_id: str = "q1",
    score: int = 78,
    dimension_scores: DimensionScores | None = None,
    applicable_dimensions: list[str] | None = None,
    dimension_evidence: list[dict] | None = None,
    rationale: str = "The answer covered cache-aside and latency improvements.",
    critique: str = "It missed delayed double delete.",
    reference_chunk_ids: list[str] | None = None,
    highlights: list[str] | None = None,
) -> CanonicalQuestionResult:
    return CanonicalQuestionResult(
        question_id=question_id,
        question_text="Explain Redis cache invalidation.",
        user_answer="I delete cache after database writes.",
        score=score,
        dimension_scores=dimension_scores
        or DimensionScores(
            breadth=80,
            depth=72,
            architecture=0,
            engineering=82,
            communication=76,
        ),
        applicable_dimensions=applicable_dimensions
        or ["breadth", "depth", "engineering", "communication"],
        dimension_evidence=dimension_evidence or [],
        rationale=rationale,
        critique=critique,
        better_answer="Add delayed double delete and fallback behavior.",
        reference_chunk_ids=reference_chunk_ids or ["redis-1", "redis-2"],
        highlights=highlights or [],
    )


def test_assemble_interview_report_averages_scores_and_resolves_references():
    report = assemble_interview_report(
        session_id="s1",
        question_results=[
            make_question_result(
                question_id="q1",
                score=78,
                dimension_scores=DimensionScores(
                    breadth=80,
                    depth=72,
                    architecture=78,
                    engineering=82,
                    communication=76,
                ),
                applicable_dimensions=[
                    "breadth",
                    "depth",
                    "engineering",
                    "communication",
                ],
                reference_chunk_ids=["redis-1", "redis-2", "missing"],
                highlights=["Covered cache-aside tradeoffs"],
            ),
            make_question_result(
                question_id="q2",
                score=82,
                dimension_scores=DimensionScores(
                    breadth=60,
                    depth=68,
                    architecture=70,
                    engineering=74,
                    communication=88,
                ),
                applicable_dimensions=[
                    "depth",
                    "architecture",
                    "engineering",
                    "communication",
                ],
                reference_chunk_ids=["redis-2"],
                highlights=["Named delayed double delete"],
            ),
        ],
        reference_lookup={
            "redis-1": {
                "chunk_id": "redis-1",
                "title": "Redis cache consistency",
                "source_type": "theory",
                "excerpt": "Delete cache after database updates.",
            },
            "redis-2": {
                "chunk_id": "redis-2",
                "title": "High-score Redis answer",
                "source_type": "answer",
                "excerpt": "Use delayed double delete.",
            },
        },
    )

    assert report.is_fallback is False
    assert report.overall_score == 80
    assert report.overall_dimension_scores == DimensionScores(
        breadth=40,
        depth=70,
        architecture=35,
        engineering=78,
        communication=82,
    )
    assert [reference.chunk_id for reference in report.feedbacks[0].references] == [
        "redis-1",
        "redis-2",
    ]
    assert [reference.chunk_id for reference in report.feedbacks[1].references] == [
        "redis-2"
    ]


def test_assemble_interview_report_uses_explicit_highlights_not_rationale():
    report = assemble_interview_report(
        session_id="s1",
        question_results=[
            make_question_result(
                rationale="This cache-aside rationale should not become a highlight.",
                highlights=["Covered cache-aside tradeoffs"],
            )
        ],
        reference_lookup={},
    )

    assert report.highlights == ["Covered cache-aside tradeoffs"]
    assert "should not become a highlight" not in " ".join(
        report.highlights
    ).lower()
    assert "cache-aside" in report.summary.lower()


def test_assemble_interview_report_falls_back_to_short_critique_snippets():
    long_critique = (
        "Needs concrete metrics, rollback handling, race-window mitigation, "
        "and production monitoring details before this answer is convincing."
    )
    report = assemble_interview_report(
        session_id="s1",
        question_results=[
            make_question_result(
                critique=long_critique,
                highlights=[],
            )
        ],
        reference_lookup={},
    )

    assert report.highlights == [
        "Needs concrete metrics, rollback handling, race-window mitigation, and produc..."
    ]
