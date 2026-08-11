import json
from pathlib import Path

import pytest

from app.services.report import InterviewReport
from app.services.report_contract import CanonicalQuestionResult
from app.services.report_provider_adapter import (
    build_reference_lookup,
    normalize_provider_payload,
)
from app.services.report_replay import replay_fixture


FIXTURE_DIR = Path("tests/fixtures/report_payloads")


@pytest.mark.parametrize(
    "fixture_name",
    [
        "deepseek_adjacent.json",
        "deepseek_sparse.json",
        "deepseek_evaluation_results.json",
    ],
)
def test_normalize_provider_payload_converts_known_deepseek_shapes(fixture_name: str):
    fixture = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))
    result = normalize_provider_payload(
        fixture["provider_payload"],
        fixture["evaluation_items"],
    )

    assert len(result.question_results) == 1
    assert isinstance(result.question_results[0], CanonicalQuestionResult)
    assert result.question_results[0].question_id == "q1"
    assert result.question_results[0].reference_chunk_ids
    assert "redis-1" in build_reference_lookup(
        fixture["provider_payload"],
        fixture["evaluation_items"],
        result.provider_reference_ids,
    )


def test_projection_only_chunk_id_cannot_create_backend_provenance():
    result = normalize_provider_payload(
        {
            "references": [
                {
                    "chunk_id": "projection-only",
                    "title": "Provider fabricated reference",
                    "content": "Provider fabricated content",
                }
            ],
            "question_results": [
                {
                    "question_id": "q1",
                    "reference_chunk_ids": ["projection-only"],
                }
            ]
        },
        [
            {
                "question_id": "q1",
                "question_text": "Question",
                "messages": [],
                "scoring_references": [],
                "answer_references": [],
                "non_authoritative_reference_context": [
                    {
                        "chunk_id": "projection-only",
                        "authority": "non_authoritative",
                        "content": "Projection content",
                    }
                ],
            }
        ],
    )

    assert result.reference_lookup == {}
    assert result.question_results[0].reference_chunk_ids == []


def test_provider_reference_cannot_override_same_id_backend_raw_content():
    result = normalize_provider_payload(
        {
            "references": [
                {
                    "chunk_id": "raw-1",
                    "title": "Provider title",
                    "source_type": "provider",
                    "content": "Provider fabricated content",
                }
            ],
            "question_results": [
                {
                    "question_id": "q1",
                    "reference_chunk_ids": ["raw-1"],
                }
            ],
        },
        [
            {
                "question_id": "q1",
                "question_text": "Question",
                "messages": [],
                "scoring_references": [
                    {
                        "chunk_id": "raw-1",
                        "title": "Backend raw title",
                        "source_type": "theory",
                        "content": "Backend authoritative content",
                    }
                ],
                "answer_references": [],
                "non_authoritative_reference_context": [
                    {
                        "chunk_id": "raw-1",
                        "authority": "non_authoritative",
                        "content": "Compressed guidance",
                    }
                ],
            }
        ],
    )

    assert result.reference_lookup == {
        "raw-1": {
            "chunk_id": "raw-1",
            "title": "Backend raw title",
            "source_type": "theory",
            "excerpt": "Backend authoritative content",
        }
    }
    assert result.question_results[0].reference_chunk_ids == ["raw-1"]


def test_hidden_raw_reference_id_is_not_in_projection_whitelist():
    result = normalize_provider_payload(
        {
            "question_results": [
                {
                    "question_id": "q1",
                    "reference_chunk_ids": ["hidden-raw"],
                }
            ]
        },
        [
            {
                "question_id": "q1",
                "question_text": "Question",
                "messages": [],
                "scoring_references": [
                    {
                        "chunk_id": "visible-raw",
                        "content": "Visible raw source",
                    }
                ],
                "answer_references": [
                    {
                        "chunk_id": "hidden-raw",
                        "content": "Hidden raw source",
                    }
                ],
                "non_authoritative_reference_context": [
                    {
                        "chunk_id": "visible-raw",
                        "authority": "non_authoritative",
                        "content": "Visible projection",
                    }
                ],
            }
        ],
    )

    assert "hidden-raw" in result.reference_lookup
    assert result.question_results[0].reference_chunk_ids == ["visible-raw"]


def _mixed_projection_evaluation_items():
    return [
        {
            "question_id": "q1",
            "question_text": "Projected question",
            "messages": [],
            "scoring_references": [
                {"chunk_id": "q1-raw", "content": "Q1 raw source"}
            ],
            "answer_references": [],
            "non_authoritative_reference_context": [
                {
                    "chunk_id": "q1-raw",
                    "authority": "non_authoritative",
                    "content": "Q1 projection",
                }
            ],
        },
        {
            "question_id": "q2",
            "question_text": "Unprojected question",
            "messages": [],
            "scoring_references": [
                {"chunk_id": "q2-raw", "content": "Q2 raw source"}
            ],
            "answer_references": [],
        },
    ]


def test_mixed_batch_unprojected_question_accepts_its_own_raw_reference():
    result = normalize_provider_payload(
        {
            "question_results": [
                {
                    "question_id": "q2",
                    "reference_chunk_ids": ["q2-raw"],
                }
            ]
        },
        _mixed_projection_evaluation_items(),
    )

    assert result.question_results[0].reference_chunk_ids == ["q2-raw"]


@pytest.mark.parametrize(
    "provider_reference",
    [
        {"reference_chunk_ids": ["q1-raw"]},
        {"references": [{"chunk_id": "q1-raw"}]},
        {"gaps": [{"reference_chunk_id": "q1-raw"}]},
    ],
    ids=["reference_chunk_ids", "references", "gaps"],
)
def test_mixed_batch_unprojected_question_rejects_other_question_raw_reference(
    provider_reference,
):
    result = normalize_provider_payload(
        {
            "question_results": [
                {"question_id": "q2", **provider_reference}
            ]
        },
        _mixed_projection_evaluation_items(),
    )

    assert "q1-raw" not in result.question_results[0].reference_chunk_ids
    assert result.question_results[0].reference_chunk_ids == ["q2-raw"]


def test_replay_fixture_returns_grounded_report_for_deepseek_adjacent():
    report = replay_fixture(str(FIXTURE_DIR / "deepseek_adjacent.json"))

    assert isinstance(report, InterviewReport)
    assert report.session_id == "s1"
    assert report.is_fallback is False
    assert report.overall_score == 44
    assert report.feedbacks[0].user_answer == "I delete cache after database writes."
    assert report.feedbacks[0].dimension_scores.depth == 45
    assert report.feedbacks[0].dimension_scores.engineering == 45
    assert report.feedbacks[0].applicable_dimensions == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]
    assert report.feedbacks[0].dimension_evidence
    assert [reference.chunk_id for reference in report.feedbacks[0].references] == [
        "redis-1",
        "redis-2",
    ]
    assert report.feedbacks[0].references[1].source_type == "answer"
    assert "延迟双删" in report.feedbacks[0].references[1].excerpt
