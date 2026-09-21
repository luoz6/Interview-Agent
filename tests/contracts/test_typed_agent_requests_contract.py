import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.interview.scheduling import (
    EvaluateAnswerRequest,
    EvaluateInterviewRequest,
    GenerateFollowupRequest,
    GenerateInterviewPlanRequest,
    GenerateMainQuestionRequest,
    GenerateReportRequest,
    REQUEST_CONTRACTS,
    parse_agent_request,
)


ROOT = Path(__file__).resolve().parents[2]


def test_all_required_skills_have_typed_request_contracts():
    assert set(REQUEST_CONTRACTS) == {
        "generate-interview-plan",
        "generate-main-question",
        "generate-followup",
        "evaluate-answer",
        "evaluate-interview",
        "generate-report",
    }
    assert (
        parse_agent_request(
            "generate-followup",
            {"question_id": "q1", "context": [], "focus": "tradeoff"},
        ).contract_key()
        == "generate-followup-request:v1"
    )


def test_typed_request_models_validate_skill_specific_shapes():
    plan = GenerateInterviewPlanRequest(
        job_description="Backend role",
        resume_text="Python engineer",
    )
    main_question = GenerateMainQuestionRequest(intent={"kind": "technical"})
    followup = GenerateFollowupRequest(question_id="q1")
    answer = EvaluateAnswerRequest(state={"messages": []}, question_id="q1")
    interview = EvaluateInterviewRequest(state={"messages": []})
    report = GenerateReportRequest(
        plan={"schema_version": "interview-plan-v2"},
        session_id="session-1",
        evaluation_items=({"question_id": "q1", "score": 80},),
    )
    assert plan.contract_id.endswith("request")
    assert main_question.contract_id.endswith("request")
    assert followup.question_id == "q1"
    assert answer.question_id == "q1"
    assert interview.state == {"messages": []}
    assert report.session_id == "session-1"


def test_report_request_requires_typed_evaluation_input():
    with pytest.raises(ValidationError, match="evaluation_items"):
        GenerateReportRequest(
            plan={"schema_version": "interview-plan-v2"},
            session_id="session-1",
        )
    with pytest.raises(ValidationError):
        GenerateInterviewPlanRequest(job_description="", resume_text="resume")


def test_typed_request_module_is_pure_domain_and_not_a2a_adapter():
    path = ROOT / "app" / "domain" / "interview" / "scheduling" / "requests.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert all(
        not module.startswith(("app.adapters", "app.a2a", "app.runtime"))
        for module in imported_modules
    )
