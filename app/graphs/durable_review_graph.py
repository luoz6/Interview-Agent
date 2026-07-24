from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graphs.durable_review_state import DurableReviewState


@dataclass(frozen=True)
class DurableReviewGraphDependencies:
    workflow_store: object
    review_question: object
    generate_report: object
    validate_report: object
    commit_report: object
    max_provider_attempts: int = 3
    max_quality_repairs: int = 2
    repair_report: object | None = None


def initialize_review(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    deps.workflow_store.initialize_run(
        job_id=state["job_id"],
        session_id=state["session_id"],
        graph_schema_version=state["review_graph_schema_version"],
        input_sha256=state["review_input_manifest"]["input_sha256"],
    )
    return {}


def plan_question_work(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    completed = set(
        deps.workflow_store.reusable_question_ids(
            state["session_id"], state["review_input_manifest"], state["review_graph_schema_version"]
        )
    )
    question_ids = [item["question_id"] for item in state["review_input_manifest"]["questions"]]
    return {
        "completed_question_ids": [item for item in question_ids if item in completed],
        "missing_question_ids": [item for item in question_ids if item not in completed],
    }


def review_one_question(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    question_id = state["missing_question_ids"][0]
    deps.review_question(state, question_id)
    return {
        "current_question_id": question_id,
        "completed_question_ids": [*state["completed_question_ids"], question_id],
        "missing_question_ids": state["missing_question_ids"][1:],
    }


def generate_coach_report(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    try:
        artifact = (
            deps.repair_report(state)
            if state["quality_repair_count"] > 0 and deps.repair_report is not None
            else deps.generate_report(state)
        )
    except Exception:
        return {
            "generation_outcome": (
                "retryable"
                if state["provider_attempt"] < deps.max_provider_attempts
                else "terminal"
            ),
            "error_code": "provider_unavailable",
        }
    return {
        "report_ref": artifact["report_ref"],
        "report_sha256": artifact["report_sha256"],
        "generation_outcome": "completed",
        "error_code": None,
    }


def validate_report_quality(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    result = deps.validate_report(state)
    if isinstance(result, tuple):
        outcome, issues = result
    else:
        outcome, issues = result, []
    return {"validation_outcome": outcome, "quality_issues": issues}


def commit_report(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    deps.commit_report(state)
    return {}


def route_after_plan(state: DurableReviewState) -> str:
    return "review_one_question" if state["missing_question_ids"] else "generate_coach_report"


def route_after_review(state: DurableReviewState) -> str:
    return "review_one_question" if state["missing_question_ids"] else "generate_coach_report"


def route_after_validation(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> str:
    if state["validation_outcome"] == "passed":
        return "commit_report"
    return "prepare_quality_repair" if state["quality_repair_count"] < deps.max_quality_repairs else "fail_review"


def prepare_quality_repair(state: DurableReviewState) -> dict:
    return {
        "quality_repair_count": state["quality_repair_count"] + 1,
        "validation_outcome": None,
        "generation_outcome": None,
    }


def route_after_generation(state: DurableReviewState) -> str:
    if state["generation_outcome"] == "completed":
        return "validate_report_quality"
    if state["generation_outcome"] == "retryable":
        return "enqueue_retry"
    return "fail_review"


def enqueue_retry(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    next_attempt = state["provider_attempt"] + 1
    deps.workflow_store.schedule_retry(
        job_id=state["job_id"],
        next_attempt_number=next_attempt,
        delay_seconds=0.25 * (2 ** (state["provider_attempt"] - 1)),
    )
    return {"expected_retry_attempt": next_attempt}


def wait_for_retry(state: DurableReviewState) -> dict:
    payload = interrupt({
        "kind": "review_retry_timer",
        "job_id": state["job_id"],
        "next_attempt_number": state["expected_retry_attempt"],
    })
    return {"retry_resume_attempt": payload["next_attempt_number"]}


def validate_retry(state: DurableReviewState) -> dict:
    if state["retry_resume_attempt"] != state["expected_retry_attempt"]:
        return {"retry_resume_attempt": None}
    return {
        "provider_attempt": state["expected_retry_attempt"],
        "expected_retry_attempt": None,
        "retry_resume_attempt": None,
        "generation_outcome": None,
    }


def route_validated_retry(state: DurableReviewState) -> str:
    return "wait_for_retry" if state["expected_retry_attempt"] is not None else "generate_coach_report"


def fail_review(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    deps.workflow_store.fail_review(state["job_id"], "report_quality_failed")
    return {"error_code": "report_quality_failed"}


def build_durable_review_graph(deps: DurableReviewGraphDependencies, *, checkpointer):
    builder = StateGraph(DurableReviewState)
    builder.add_node("initialize_review", partial(initialize_review, deps=deps))
    builder.add_node("plan_question_work", partial(plan_question_work, deps=deps))
    builder.add_node("review_one_question", partial(review_one_question, deps=deps))
    builder.add_node("generate_coach_report", partial(generate_coach_report, deps=deps))
    builder.add_node("validate_report_quality", partial(validate_report_quality, deps=deps))
    builder.add_node("prepare_quality_repair", prepare_quality_repair)
    builder.add_node("commit_report", partial(commit_report, deps=deps))
    builder.add_node("fail_review", partial(fail_review, deps=deps))
    builder.add_node("enqueue_retry", partial(enqueue_retry, deps=deps))
    builder.add_node("wait_for_retry", wait_for_retry)
    builder.add_node("validate_retry", validate_retry)
    builder.add_edge(START, "initialize_review")
    builder.add_edge("initialize_review", "plan_question_work")
    builder.add_conditional_edges("plan_question_work", route_after_plan)
    builder.add_conditional_edges("review_one_question", route_after_review)
    builder.add_conditional_edges("generate_coach_report", route_after_generation)
    builder.add_conditional_edges(
        "validate_report_quality", partial(route_after_validation, deps=deps)
    )
    builder.add_edge("prepare_quality_repair", "generate_coach_report")
    builder.add_edge("commit_report", END)
    builder.add_edge("fail_review", END)
    builder.add_edge("enqueue_retry", "wait_for_retry")
    builder.add_edge("wait_for_retry", "validate_retry")
    builder.add_conditional_edges("validate_retry", route_validated_retry)
    return builder.compile(checkpointer=checkpointer)
