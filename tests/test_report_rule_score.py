from app.services.report import DimensionScores
from app.services.report_rule_score import (
    DimensionEvidence,
    aggregate_feedback_scores,
    applicable_dimensions_for_item,
    score_dimension_evidence,
    score_question_from_evidence,
)


def test_score_dimension_evidence_returns_zero_without_observed_evidence():
    evidence = DimensionEvidence(
        dimension="architecture",
        observed=[],
        missing=["没有说明容量估算和故障隔离。"],
        quality_signals=["tradeoff", "fallback"],
    )

    assert score_dimension_evidence(evidence) == 0


def test_score_dimension_evidence_caps_concept_only_answer_below_pass_level():
    evidence = DimensionEvidence(
        dimension="depth",
        observed=["候选人只提到了 Redis 和缓存击穿。"],
        missing=["没有解释并发窗口、失败场景和一致性取舍。"],
        quality_signals=["concept"],
    )

    assert score_dimension_evidence(evidence) == 40


def test_score_dimension_evidence_rewards_tradeoff_metrics_and_fallback():
    evidence = DimensionEvidence(
        dimension="architecture",
        observed=[
            "候选人说明了库存服务、订单服务和 Redis 预扣库存的边界。",
            "候选人给出了 p95、超卖风险、MQ 补偿和降级策略。",
        ],
        missing=[],
        quality_signals=[
            "concrete_steps",
            "tradeoff",
            "risk",
            "fallback",
            "metric",
            "production",
        ],
    )

    assert score_dimension_evidence(evidence) == 95


def test_applicable_dimensions_use_question_kind_before_focus_text():
    item = {
        "question_id": "q1",
        "question_kind": "system-design",
        "focus": "系统设计",
        "question_text": "如何设计一个高并发秒杀系统？",
    }

    assert applicable_dimensions_for_item(item) == [
        "architecture",
        "engineering",
        "depth",
        "communication",
    ]


def test_technical_question_does_not_score_architecture_by_default():
    item = {
        "question_id": "q2",
        "question_kind": "technical",
        "focus": "Redis 缓存一致性",
        "question_text": "如何处理缓存和数据库一致性？",
    }

    assert applicable_dimensions_for_item(item) == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]


def test_score_question_ignores_non_applicable_architecture_evidence():
    item = {
        "question_id": "q2",
        "question_kind": "technical",
        "focus": "Redis 缓存一致性",
        "question_text": "如何处理缓存和数据库一致性？",
    }
    evidence = [
        DimensionEvidence(
            dimension="architecture",
            observed=["模型误把技术题扩展成系统设计。"],
            missing=[],
            quality_signals=["production", "metric", "tradeoff"],
        ),
        DimensionEvidence(
            dimension="depth",
            observed=["说明了先更新数据库再删除缓存。"],
            missing=["没有说明并发窗口。"],
            quality_signals=["concrete_steps"],
        ),
    ]

    result = score_question_from_evidence(item, evidence)

    assert result.score == 25
    assert result.dimension_scores.architecture == 0
    assert result.dimension_scores.depth == 55
    assert result.applicable_dimensions == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]


def test_aggregate_feedback_scores_ignores_non_applicable_dimensions():
    class Feedback:
        def __init__(self, score, dimension_scores, applicable_dimensions):
            self.score = score
            self.dimension_scores = dimension_scores
            self.applicable_dimensions = applicable_dimensions

    feedbacks = [
        Feedback(
            80,
            DimensionScores(
                breadth=0,
                depth=80,
                architecture=0,
                engineering=80,
                communication=80,
            ),
            ["depth", "engineering", "communication"],
        ),
        Feedback(
            60,
            DimensionScores(
                breadth=0,
                depth=0,
                architecture=60,
                engineering=60,
                communication=60,
            ),
            ["architecture", "engineering", "communication"],
        ),
    ]

    overall_score, overall_dimensions = aggregate_feedback_scores(feedbacks)

    assert overall_score == 70
    assert overall_dimensions == DimensionScores(
        breadth=0,
        depth=80,
        architecture=60,
        engineering=70,
        communication=70,
    )
