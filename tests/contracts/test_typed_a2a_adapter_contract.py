import ast
from pathlib import Path

import pytest

from app.a2a.adapters import _typed_handler
from app.a2a.contracts.errors import A2AAgentError
from app.domain.interview.scheduling.requests import GenerateFollowupRequest


ROOT = Path(__file__).resolve().parents[2]


def test_a2a_adapter_deserializes_wire_dict_to_typed_request_once_at_boundary():
    seen = []
    handler = _typed_handler(
        "generate-followup",
        lambda request, _context: seen.append(request) or request,
    )

    result = handler(
        {
            "question_id": "q1",
            "context": [],
            "focus": "tradeoffs",
        },
        None,
    )

    assert isinstance(result, GenerateFollowupRequest)
    assert seen == [result]
    assert result.question_id == "q1"


def test_a2a_adapter_rejects_invalid_typed_request_as_stable_error():
    handler = _typed_handler(
        "generate-followup",
        lambda request, _context: request,
    )

    with pytest.raises(A2AAgentError) as exc_info:
        handler({"focus": "missing-question-id"}, None)

    assert exc_info.value.code == "invalid_request"
    assert exc_info.value.terminal is True


def test_builtin_a2a_handlers_use_typed_requests_and_do_not_guess_fields():
    path = ROOT / "app" / "a2a" / "adapters.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    typed_annotations = {
        "handler": {
            "GenerateFollowupRequest",
            "GenerateInterviewPlanRequest",
            "EvaluateAnswerRequest",
            "GenerateReportRequest",
        },
        "evaluate_interview_handler": {"EvaluateInterviewRequest"},
    }

    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in typed_annotations:
            continue
        annotation = node.args.args[0].annotation
        annotation_name = ast.unparse(annotation)
        assert annotation_name in typed_annotations[node.name]
        found.add((node.name, annotation_name))
        for child in ast.walk(node):
            if isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name):
                assert child.value.id != "request"
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
            ):
                assert not (
                    child.func.value.id == "request" and child.func.attr == "get"
                )

    assert found == {
        ("handler", "GenerateFollowupRequest"),
        ("handler", "GenerateInterviewPlanRequest"),
        ("handler", "EvaluateAnswerRequest"),
        ("handler", "GenerateReportRequest"),
        ("evaluate_interview_handler", "EvaluateInterviewRequest"),
    }

