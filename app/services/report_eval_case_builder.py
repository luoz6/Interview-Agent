from app.services.report_eval_dataset import EvaluationCase
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report_rule_score import applicable_dimensions_for_item


def build_report_evaluation_input(
    case: EvaluationCase,
) -> tuple[InterviewPlan, list[dict]]:
    plan = InterviewPlan(
        title=f"Stage 40: {case.group_id}",
        questions=[
            InterviewQuestion(
                id=case.case_id,
                kind=case.question_kind,
                prompt=case.question,
                focus=case.focus,
            )
        ],
    )
    reference = case.reference.model_dump(mode="json")
    reference["excerpt"] = reference["content"]
    evaluation_item = {
        "question_id": case.case_id,
        "question_text": case.question,
        "question_kind": case.question_kind,
        "focus": case.focus,
        "messages": [
            {
                "role": "candidate",
                "content": case.answer,
                "question_id": case.case_id,
            }
        ],
        "scoring_references": [reference],
        "answer_references": [reference],
    }
    evaluation_item["applicable_dimensions"] = applicable_dimensions_for_item(
        evaluation_item
    )
    return plan, [evaluation_item]
