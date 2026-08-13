from app.services.report import (
    ReportCoverageV2,
    ReportEvidenceRefV2,
    ReportObservationV2,
    ScoreEvaluation,
)
from app.services.report_actions import (
    REPORT_ACTION_PLANNER_VERSION,
    plan_priority_actions,
)


DIMENSIONS = (
    "breadth",
    "depth",
    "architecture",
    "engineering",
    "communication",
)


def _coverage(*, unevaluated: set[str] | None = None) -> ReportCoverageV2:
    unevaluated = unevaluated or set()
    return ReportCoverageV2(
        status="partial" if unevaluated else "complete",
        evaluated_count=3,
        total_eligible_count=3,
        evidence_count=8,
        per_dimension={
            dimension: ScoreEvaluation(
                status=(
                    "not_evaluated"
                    if dimension in unevaluated
                    else "evaluated"
                ),
                reason_code=(
                    "not_applicable"
                    if dimension in unevaluated
                    else "sufficient_evidence"
                ),
                score=None if dimension in unevaluated else 75,
                evidence_count=0 if dimension in unevaluated else 1,
                eligible_count=0 if dimension in unevaluated else 1,
                evaluated_count=0 if dimension in unevaluated else 1,
            )
            for dimension in DIMENSIONS
        },
    )


def _observation(
    suffix: str,
    *,
    topic: str,
    questions: list[str],
    type: str = "gap",
    dimension: str = "engineering",
    severity: str = "medium",
    relevance: str = "high",
    evidence_strength: str = "high",
) -> ReportObservationV2:
    return ReportObservationV2(
        observation_id=f"obs-{suffix:0>16}",
        type=type,
        dimension=dimension,
        normalized_topic=topic,
        severity=severity,
        frequency=len(questions),
        role_relevance=relevance,
        evidence_strength=evidence_strength,
        question_refs=questions,
        answer_evidence_refs=[
            f"candidate:{question_id}:answer" for question_id in questions
        ],
        knowledge_refs=[],
        confidence_band="high",
    )


def _evidence(*question_ids: str) -> list[ReportEvidenceRefV2]:
    return [
        ReportEvidenceRefV2(
            evidence_ref_id=f"candidate:{question_id}:answer",
            namespace="candidate",
            question_id=question_id,
            excerpt=f"Answer evidence for {question_id}",
        )
        for question_id in question_ids
    ]


def test_actions_rank_by_impact_then_severity_and_keep_traceable_sources():
    observations = [
        _observation(
            "1",
            topic="measurable_outcomes",
            questions=["q1", "q2", "q3"],
        ),
        _observation(
            "2",
            topic="recovery_strategy",
            questions=["q1", "q2"],
        ),
        _observation(
            "3",
            topic="risk_identification",
            questions=["q4"],
            type="risk",
            severity="high",
        ),
    ]

    actions = plan_priority_actions(
        observations=observations,
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3", "q4"),
    )

    assert [item.title for item in actions] == [
        "加入可量化的验证指标",
        "补全失败恢复与兜底",
        "先列失败模式与风险边界",
    ]
    assert actions[0].question_refs == ["q1", "q2", "q3"]
    assert actions[0].observation_refs == ["obs-0000000000000001"]
    assert actions[0].evidence_refs == [
        "candidate:q1:answer",
        "candidate:q2:answer",
        "candidate:q3:answer",
    ]
    assert "题目q1、q2、q3" in actions[0].practice
    assert actions[0].completion_criteria
    assert "不推断未观察到" in actions[0].limitation


def test_same_topic_is_merged_across_observations_and_dimensions():
    observations = [
        _observation(
            "1",
            topic="tradeoff_analysis",
            questions=["q1"],
            dimension="depth",
        ),
        _observation(
            "2",
            topic="tradeoff_analysis",
            questions=["q2"],
            dimension="architecture",
        ),
    ]

    actions = plan_priority_actions(
        observations=observations,
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2"),
    )

    assert len(actions) == 1
    assert actions[0].question_refs == ["q1", "q2"]
    assert actions[0].observation_refs == [
        "obs-0000000000000001",
        "obs-0000000000000002",
    ]


def test_unassessed_strength_limitation_and_unpublished_evidence_make_no_action():
    observations = [
        _observation(
            "1",
            topic="architecture_design",
            questions=["q1"],
            dimension="architecture",
        ),
        _observation(
            "2",
            topic="tradeoff_analysis",
            questions=["q2"],
            type="strength",
        ),
        _observation(
            "3",
            topic="technical_specificity",
            questions=["q-missing"],
        ),
    ]

    actions = plan_priority_actions(
        observations=observations,
        coverage=_coverage(unevaluated={"architecture"}),
        evidence_refs=_evidence("q1", "q2"),
    )

    assert actions == []


def test_mismatched_candidate_evidence_question_makes_no_action():
    observation = _observation(
        "1",
        topic="measurable_outcomes",
        questions=["q1"],
    ).model_copy(
        update={"answer_evidence_refs": ["candidate:q2:answer"]}
    )

    actions = plan_priority_actions(
        observations=[observation],
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2"),
    )

    assert actions == []


def test_role_relevance_then_evidence_then_actionability_break_ranking_ties():
    observations = [
        _observation(
            "1",
            topic="technical_specificity",
            questions=["q1"],
            relevance="low",
            evidence_strength="high",
        ),
        _observation(
            "2",
            topic="architecture_design",
            questions=["q2"],
            relevance="high",
            evidence_strength="low",
        ),
        _observation(
            "3",
            topic="communication_clarity",
            questions=["q3"],
            relevance="high",
            evidence_strength="high",
        ),
        _observation(
            "4",
            topic="tradeoff_analysis",
            questions=["q4"],
            relevance="high",
            evidence_strength="high",
        ),
    ]

    actions = plan_priority_actions(
        observations=observations,
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3", "q4"),
    )

    assert [item.title for item in actions] == [
        "用结论—依据—边界组织回答",
        "显式说明关键技术取舍",
        "补全架构边界与扩展路径",
    ]


def test_action_order_and_ids_are_stable_and_output_is_capped():
    observations = [
        _observation(
            str(index),
            topic=topic,
            questions=[f"q{index}"],
        )
        for index, topic in enumerate(
            (
                "technical_foundation",
                "structured_execution",
                "tradeoff_analysis",
                "recovery_strategy",
            ),
            1,
        )
    ]
    evidence = _evidence("q1", "q2", "q3", "q4")

    forward = plan_priority_actions(
        observations=observations,
        coverage=_coverage(),
        evidence_refs=evidence,
    )
    reverse = plan_priority_actions(
        observations=reversed(observations),
        coverage=_coverage(),
        evidence_refs=reversed(evidence),
    )

    assert REPORT_ACTION_PLANNER_VERSION == "report-priority-action-planner-v1"
    assert len(forward) == 3
    assert [item.model_dump() for item in forward] == [
        item.model_dump() for item in reverse
    ]
    assert len({item.action_id for item in forward}) == 3


def test_low_evidence_signals_do_not_fill_all_action_slots():
    observations = [
        _observation(
            str(index),
            topic=topic,
            questions=[f"q{index}"],
            evidence_strength="low",
        )
        for index, topic in enumerate(
            (
                "technical_foundation",
                "structured_execution",
                "tradeoff_analysis",
            ),
            1,
        )
    ]

    actions = plan_priority_actions(
        observations=observations,
        coverage=_coverage(),
        evidence_refs=_evidence("q1", "q2", "q3"),
    )

    assert len(actions) == 1
