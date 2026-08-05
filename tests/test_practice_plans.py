from copy import deepcopy

import pytest

from app.graphs.interview_state import build_initial_state
from app.services.in_memory_interview_launch_repository import (
    InMemoryInterviewLaunchRepository,
)
from app.services.in_memory_prep_plan_store import InMemoryPrepPlanStore
from app.services.practice_plans import PracticePlanError, PracticePlanService
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
from app.services.report_reliability import ReportReliability


def _scores(**overrides) -> DimensionScores:
    values = {
        "breadth": 76,
        "depth": 70,
        "architecture": 72,
        "engineering": 58,
        "communication": 74,
    }
    values.update(overrides)
    return DimensionScores(**values)


def _feedback(question_id: str, engineering: int) -> InterviewFeedback:
    return InterviewFeedback(
        question_id=question_id,
        question_text=f"原始题目 {question_id}",
        user_answer="候选人有效回答",
        answer_state="answered",
        score=68,
        dimension_scores=_scores(engineering=engineering),
        rationale="评分依据",
        critique="工程验证不足",
        better_answer="补充指标、回滚和复盘闭环",
        references=[],
    )


def _case():
    state = build_initial_state(
        session_id="practice-session",
        plan=InterviewPlan(
            title="后端工程师",
            questions=[
                InterviewQuestion(
                    id=f"q{index}",
                    kind="technical",
                    prompt=f"原始题目 q{index}",
                    focus="工程实践",
                )
                for index in range(1, 4)
            ],
        ),
        job_description="负责高可用后端服务",
        resume_text="具备生产故障处理经验",
        job_tags=["reliability"],
    )
    state["status"] = "finished"
    state["messages"] = [
        {"role": "candidate", "content": "回答一", "question_id": "q1"},
        {"role": "candidate", "content": "回答二", "question_id": "q2"},
    ]
    report = InterviewReport(
        session_id=state["session_id"],
        overall_score=68,
        overall_dimension_scores=_scores(),
        summary="工程验证是当前最需要改进的部分。",
        highlights=["能够说明基本方案"],
        feedbacks=[_feedback("q1", 62), _feedback("q2", 54)],
    )
    reliability = ReportReliability(
        planned_question_count=3,
        answered_question_count=2,
        skipped_question_count=1,
        unanswered_question_count=0,
        reviewed_answer_count=2,
        review_failed_answer_count=0,
        evidence_bound_question_count=0,
        degraded_question_count=0,
        generation_path="structured",
        degraded_reasons=[],
        score_applicability="normal",
    )
    plans = InMemoryPrepPlanStore()
    launches = InMemoryInterviewLaunchRepository()
    launches.create_pending(
        plan_id="source-plan",
        command_id="source-command",
        consumed_plan_version=1,
        session_id=state["session_id"],
        mappings=[
            {
                "plan_question_id": f"pq-source-{index}",
                "session_question_id": f"q{index}",
                "position": index,
                "kind": "technical",
            }
            for index in range(1, 4)
        ],
    )
    service = PracticePlanService(
        prep_plan_store=plans,
        launch_repository=launches,
    )
    return state, report, reliability, plans, launches, service


def test_practice_plan_creates_three_editable_questions_with_dual_id_provenance():
    state, report, reliability, _, _, service = _case()
    state_before = deepcopy(state)
    report_before = report.model_dump()

    plan = service.create(
        state=state,
        report=report,
        reliability=reliability,
        focus_dimension="engineering",
        session_question_ids=["q2"],
    )

    assert plan["state"] == "editable"
    assert plan["plan_version"] == 1
    assert len(plan["questions"]) == 3
    assert len({question["question_id"] for question in plan["questions"]}) == 3
    assert all(question["enabled"] for question in plan["questions"])
    assert {
        question["focus"] for question in plan["questions"]
    } == {"工程实践针对性复盘"}
    assert plan["practice_provenance"] == {
        "source_session_id": "practice-session",
        "source_session_question_ids": ["q2"],
        "source_plan_question_ids": ["pq-source-2"],
        "source_report_id": "practice-session",
        "focus_dimension": "engineering",
    }
    assert state == state_before
    assert report.model_dump() == report_before


@pytest.mark.parametrize(
    ("focus_dimension", "question_ids", "mutate_reliability", "expected_code"),
    [
        ("depth", ["q2"], None, "PRACTICE_DIMENSION_NOT_WEAKNESS"),
        ("engineering", ["q2", "q2"], None, "PRACTICE_INVALID_QUESTION_IDS"),
        ("engineering", ["q3"], None, "PRACTICE_QUESTION_NOT_ELIGIBLE"),
        (
            "engineering",
            ["q2"],
            {"score_applicability": "insufficient"},
            "PRACTICE_REPORT_INSUFFICIENT",
        ),
    ],
)
def test_practice_plan_rejects_ineligible_requests_without_partial_plan(
    focus_dimension, question_ids, mutate_reliability, expected_code
):
    state, report, reliability, plans, _, service = _case()
    if mutate_reliability:
        reliability = reliability.model_copy(update=mutate_reliability)

    with pytest.raises(PracticePlanError) as exc_info:
        service.create(
            state=state,
            report=report,
            reliability=reliability,
            focus_dimension=focus_dimension,
            session_question_ids=question_ids,
        )

    assert exc_info.value.code == expected_code
    assert plans._records == {}


def test_practice_plan_requires_persisted_session_question_mapping():
    state, report, reliability, plans, launches, _ = _case()
    launches.clear()
    service = PracticePlanService(
        prep_plan_store=plans,
        launch_repository=launches,
    )
    with pytest.raises(PracticePlanError) as exc_info:
        service.create(
            state=state,
            report=report,
            reliability=reliability,
            focus_dimension="engineering",
            session_question_ids=["q2"],
        )
    assert exc_info.value.code == "PRACTICE_MAPPING_UNAVAILABLE"
    assert plans._records == {}


def test_practice_plan_rejects_report_feedback_that_conflicts_with_answer_state():
    state, report, reliability, plans, _, service = _case()
    state["messages"] = [
        {"role": "candidate", "content": "回答一", "question_id": "q1"},
    ]

    with pytest.raises(PracticePlanError) as exc_info:
        service.create(
            state=state,
            report=report,
            reliability=reliability,
            focus_dimension="engineering",
            session_question_ids=["q2"],
        )

    assert exc_info.value.code == "PRACTICE_QUESTION_NOT_ELIGIBLE"
    assert plans._records == {}


def test_practice_plan_rejects_unresolved_tied_weaknesses():
    state, report, reliability, plans, _, service = _case()
    report = report.model_copy(
        update={
            "overall_dimension_scores": _scores(
                breadth=70,
                depth=70,
                architecture=70,
                engineering=70,
                communication=70,
            )
        }
    )

    with pytest.raises(PracticePlanError) as exc_info:
        service.create(
            state=state,
            report=report,
            reliability=reliability,
            focus_dimension="engineering",
            session_question_ids=["q2"],
        )

    assert exc_info.value.code == "PRACTICE_WEAKNESS_UNRESOLVED"
    assert plans._records == {}
