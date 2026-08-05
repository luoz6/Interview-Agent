from app.services.report import DimensionScores
from app.services.report_rule_score import (
    REPORT_SCORING_RUBRIC_VERSION,
    REPORT_SCORING_RUBRIC_SHA256,
    DimensionEvidence,
    aggregate_feedback_scores,
    answer_quality_score_cap,
    applicable_dimensions_for_item,
    derive_quality_signals,
    score_dimension_evidence,
    score_question_from_evidence,
)


def test_report_scoring_rubric_has_stable_version():
    assert REPORT_SCORING_RUBRIC_VERSION == "interview-quality-rubric-v3.3-candidate"
    assert len(REPORT_SCORING_RUBRIC_SHA256) == 64
    assert set(REPORT_SCORING_RUBRIC_SHA256) <= set("0123456789abcdef")


def test_chinese_process_tradeoff_risk_recovery_metrics_and_operations_are_detected():
    item = {
        "messages": [{
            "role": "candidate",
            "content": (
                "首先提交数据库事务，然后删除 Redis 缓存；因为存在并发窗口，"
                "所以要权衡一致性和延迟。失败时重试并降级到数据库，线上监控 p95、"
                "错误率和告警，最后通过灰度发布验证。"
            ),
        }]
    }

    assert derive_quality_signals(item, dimension="engineering") == [
        "concept",
        "concrete_steps",
        "risk",
        "fallback",
        "metric",
        "production",
        "code_or_api",
        "clarity",
    ]
    assert "tradeoff" in derive_quality_signals(item, dimension="depth")


def test_negated_monitoring_and_metrics_do_not_create_positive_signals():
    item = {
        "messages": [{
            "role": "candidate",
            "content": "这个方案没有监控，也未设置告警，没有 p95 指标，不需要重试。",
        }]
    }

    signals = derive_quality_signals(item, dimension="engineering")

    assert "production" not in signals
    assert "metric" not in signals
    assert "fallback" not in signals


def test_positive_recovery_after_negated_history_is_still_detected():
    item = {
        "messages": [{
            "role": "candidate",
            "content": "最初没有监控，复盘后补充监控和告警，并设置失败重试。",
        }]
    }

    signals = derive_quality_signals(item, dimension="engineering")

    assert "production" in signals
    assert "fallback" in signals


def test_chinese_producer_word_does_not_count_as_production_operations():
    item = {
        "messages": [{
            "role": "candidate",
            "content": "Kafka 生产者设置 acks=all，消费者成功后提交 offset。",
        }]
    }

    assert "production" not in derive_quality_signals(item, dimension="engineering")


def test_chinese_unsafe_absolute_claim_caps_score():
    item = {
        "question_kind": "technical",
        "messages": [{
            "role": "candidate",
            "content": "这个缓存方案永远一致，所以无需回滚，也没有必要做监控。",
        }],
    }
    evidence = [
        DimensionEvidence(
            dimension="depth",
            observed=["这个缓存方案永远一致"],
        )
    ]

    result = score_question_from_evidence(item, evidence)

    assert result.score <= 35


def test_answer_quality_cap_warns_when_answer_payload_is_missing(caplog):
    with caplog.at_level("WARNING"):
        cap = answer_quality_score_cap({"question_id": "q-missing"})

    assert cap == 100
    assert "score item has no answer payload" in caplog.text


def test_rule_quality_signals_are_derived_from_answer_and_dimension():
    item = {
        "messages": [{
            "role": "candidate",
            "content": (
                "First commit the transaction, then delete the Redis cache; "
                "retry on failure, alert in production, and monitor API p95."
            ),
        }]
    }
    assert derive_quality_signals(item, dimension="engineering") == [
        "concept",
        "concrete_steps",
        "risk",
        "fallback",
        "metric",
        "production",
        "code_or_api",
        "clarity",
    ]
    assert derive_quality_signals(item, dimension="communication") == ["clarity"]


def test_unsafe_absolute_claim_caps_score_even_with_observed_evidence():
    item = {
        "question_kind": "technical",
        "messages": [{
            "role": "candidate",
            "content": "The cache is always consistent, so we do not need retries.",
        }],
    }
    evidence = [
        DimensionEvidence(
            dimension="depth",
            observed=["The cache is always consistent."],
            missing=["No failure handling was provided."],
        )
    ]

    result = score_question_from_evidence(item, evidence)

    assert result.score <= 35
    assert result.dimension_scores.depth <= 35


def test_explicit_off_topic_answer_is_capped():
    item = {
        "question_kind": "project",
        "messages": [{
            "role": "candidate",
            "content": "Redis is an in-memory database, but this does not answer the incident review question.",
        }],
    }
    evidence = [
        DimensionEvidence(
            dimension="communication",
            observed=["Redis is an in-memory database."],
        )
    ]

    result = score_question_from_evidence(item, evidence)

    assert result.score == 0


def test_score_is_stable_when_observed_evidence_moves_between_dimensions():
    item = {
        "question_kind": "technical",
        "messages": [{
            "role": "candidate",
            "content": "First update the database, then delete the Redis cache and monitor API p95.",
        }],
    }
    depth_evidence = [DimensionEvidence(dimension="depth", observed=["First update the database."])]
    engineering_evidence = [DimensionEvidence(dimension="engineering", observed=["First update the database."])]

    assert score_question_from_evidence(item, depth_evidence) == score_question_from_evidence(
        item,
        engineering_evidence,
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


def test_structured_missing_points_override_false_positive_advanced_signals():
    evidence = DimensionEvidence(
        dimension="architecture",
        observed=["The answer explicitly says it lacks a complete tradeoff and metrics."],
        missing=[
            "tradeoff_gap: incomplete tradeoff",
            "metric_gap: missing measurable metrics",
            "production_gap: missing production feedback loop",
            "recovery_gap: missing recovery loop",
        ],
        quality_signals=[
            "concept",
            "concrete_steps",
            "tradeoff",
            "risk",
            "fallback",
            "metric",
            "production",
            "clarity",
        ],
    )

    assert score_dimension_evidence(evidence) == 70


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
        "messages": [{
            "role": "candidate",
            "content": "First update the database, then delete the Redis cache.",
        }],
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
        "messages": [{
            "role": "candidate",
            "content": "First update the database, then delete the Redis cache.",
        }],
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

    assert result.score == 66
    assert result.dimension_scores.architecture is None
    assert result.dimension_scores.depth == 70
    assert result.dimension_scores.engineering == 70
    assert result.dimension_scores.breadth == 50
    assert result.dimension_scores.communication == 45
    assert result.applicable_dimensions == [
        "depth",
        "engineering",
        "breadth",
        "communication",
    ]


def test_score_question_caps_nonsense_answer_even_when_provider_claims_evidence():
    item = {
        "question_id": "q2",
        "question_kind": "technical",
        "focus": "Redis cache consistency",
        "question_text": "How do you handle Redis and database consistency?",
        "messages": [{"role": "candidate", "content": "1"}],
    }
    evidence = [
        DimensionEvidence(
            dimension="depth",
            observed=["Candidate explained delayed double delete and p95 monitoring."],
            missing=[],
            quality_signals=[
                "concrete_steps",
                "tradeoff",
                "risk",
                "fallback",
                "metric",
                "production",
            ],
        ),
        DimensionEvidence(
            dimension="engineering",
            observed=["Candidate described production rollback behavior."],
            missing=[],
            quality_signals=["concrete_steps", "production"],
        ),
    ]

    result = score_question_from_evidence(item, evidence)

    assert result.score == 0
    assert result.dimension_scores == DimensionScores(
        breadth=0,
        depth=0,
        architecture=None,
        engineering=0,
        communication=0,
    )


def test_aggregate_feedback_scores_excludes_non_applicable_dimensions():
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
        breadth=None,
        depth=80,
        architecture=60,
        engineering=70,
        communication=70,
    )
