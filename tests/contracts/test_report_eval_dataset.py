from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.report_eval_dataset import (
    EvaluationCase,
    EvaluationDataset,
    load_evaluation_dataset,
)


DATASET_PATH = Path("tests/golden/report_quality_v1.json")
EXPECTED_DOMAINS = {"redis", "mysql", "kafka", "system-design", "project"}
EXPECTED_GROUPS = {
    "redis-cache-consistency",
    "mysql-index-transaction",
    "kafka-reliability",
    "system-design-job-queue",
    "project-incident-review",
}
CORE_LEVELS = {"strong", "medium", "incorrect"}


def valid_case_payload(**overrides):
    payload = {
        "case_id": "redis-cache-consistency-strong",
        "group_id": "redis-cache-consistency",
        "domain": "redis",
        "quality_level": "strong",
        "question_kind": "technical",
        "question": "如何处理 Redis 缓存与数据库的一致性？",
        "focus": "Redis 缓存一致性",
        "answer": "采用 cache-aside，在数据库事务提交后删除缓存。",
        "expected_score_range": [80, 95],
        "expected_applicable_dimensions": [
            "depth",
            "engineering",
            "breadth",
            "communication",
        ],
        "required_observations": ["事务提交后删除缓存"],
        "required_missing_points": [],
        "forbidden_claims": ["强一致"],
        "reference": {
            "chunk_id": "redis-cache-consistency-reference",
            "title": "Redis 缓存一致性参考",
            "content": "Cache-aside 应在数据库事务提交后删除缓存。",
            "source_type": "theory",
            "domain": "redis",
            "tags": ["redis", "cache-aside"],
            "metadata": {"benchmark": "report-quality-v1"},
            "score": 0.95,
        },
    }
    payload.update(overrides)
    return payload


def test_stage40_dataset_has_required_shape_and_budget():
    dataset = load_evaluation_dataset(DATASET_PATH)

    assert dataset.version == "report-quality-v1"
    assert len(dataset.cases) == 20
    assert dataset.target_attempt_count(runs_per_case=2) == 40
    assert {case.domain for case in dataset.cases} == EXPECTED_DOMAINS
    assert set(dataset.grouped_cases()) == EXPECTED_GROUPS


def test_every_group_has_exactly_four_ordered_core_levels():
    dataset = load_evaluation_dataset(DATASET_PATH)

    for cases in dataset.grouped_cases().values():
        assert len(cases) == 4
        assert CORE_LEVELS <= {case.quality_level for case in cases}
        assert sum(case.quality_level in {"off_topic", "empty"} for case in cases) == 1
        assert len({case.domain for case in cases}) == 1

    assert any(case.quality_level == "off_topic" for case in dataset.cases)
    assert any(case.quality_level == "empty" for case in dataset.cases)


def test_dataset_answers_and_references_are_complete_chinese_content():
    dataset = load_evaluation_dataset(DATASET_PATH)

    for case in dataset.cases:
        if case.quality_level == "empty":
            assert case.answer in {"", "1"}
        else:
            assert len(case.answer) >= 20
            assert "?" not in case.answer
        assert case.reference.domain == case.domain
        assert case.reference.content
        assert case.reference.score > 0


def test_duplicate_case_id_is_rejected():
    case = valid_case_payload()

    with pytest.raises(ValueError, match="duplicate case_id"):
        EvaluationDataset.model_validate(
            {"version": "report-quality-v1", "cases": [case, case]}
        )


@pytest.mark.parametrize(
    ("score_range", "message"),
    [
        ([80, 95, 100], None),
        ([95, 80], "ordered values"),
        ([-1, 80], "0 to 100"),
        ([80, 101], "0 to 100"),
    ],
)
def test_score_range_requires_exactly_two_ordered_values(score_range, message):
    case = valid_case_payload(expected_score_range=score_range)

    error = pytest.raises((ValidationError, ValueError), match=message) if message else pytest.raises((ValidationError, ValueError))
    with error:
        EvaluationCase.model_validate(case)


def test_dataset_rejects_invalid_group_composition():
    cases = [
        valid_case_payload(case_id=f"case-{index}", quality_level=level)
        for index, level in enumerate(["strong", "medium", "off_topic", "empty"], start=1)
    ]

    with pytest.raises(ValueError, match="group must contain"):
        EvaluationDataset.model_validate(
            {"version": "report-quality-v1", "cases": cases}
        )


def test_target_attempt_count_requires_positive_runs_per_case():
    levels = ["strong", "medium", "incorrect", "empty"]
    dataset = EvaluationDataset.model_validate(
        {
            "version": "custom",
            "cases": [
                valid_case_payload(
                    case_id=f"case-{index}",
                    quality_level=level,
                    answer="" if level == "empty" else "这是用于验证目标运行次数的完整测试回答。",
                )
                for index, level in enumerate(levels, start=1)
            ],
        }
    )

    assert dataset.target_attempt_count(runs_per_case=3) == 12
    with pytest.raises(ValueError, match="positive integer"):
        dataset.target_attempt_count(runs_per_case=0)
    with pytest.raises(ValueError, match="positive integer"):
        dataset.target_attempt_count(runs_per_case=True)
