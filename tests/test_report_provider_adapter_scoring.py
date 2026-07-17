from app.services.report import DimensionScores
from app.services.report_provider_adapter import normalize_provider_payload


def test_normalize_provider_payload_does_not_trust_scores_without_evidence():
    result = normalize_provider_payload(
        {
            "question_results": [
                {
                    "question_id": "q1",
                    "score": 95,
                    "dimension_scores": {
                        "breadth": 95,
                        "depth": 95,
                        "architecture": 95,
                        "engineering": 95,
                        "communication": 95,
                    },
                }
            ]
        },
        [
            {
                "question_id": "q1",
                "question_text": "How do you handle Redis and database consistency?",
                "question_kind": "technical",
                "messages": [{"role": "candidate", "content": "1"}],
            }
        ],
    )

    feedback = result.question_results[0]
    assert feedback.score == 0
    assert feedback.dimension_scores == DimensionScores(
        breadth=0,
        depth=0,
        architecture=0,
        engineering=0,
        communication=0,
    )
    assert feedback.applicable_dimensions == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]
