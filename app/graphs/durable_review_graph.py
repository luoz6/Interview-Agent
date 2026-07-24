from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from langgraph.graph import END, START, StateGraph

from app.graphs.durable_review_state import DurableReviewState


@dataclass(frozen=True)
class DurableReviewGraphDependencies:
    workflow_store: object
    review_question: object
    generate_report: object
    validate_report: object
    commit_report: object


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
    artifact = deps.generate_report(state)
    return {
        "report_ref": artifact["report_ref"],
        "report_sha256": artifact["report_sha256"],
    }


def validate_report_quality(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    return {"validation_outcome": deps.validate_report(state)}


def commit_report(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    deps.commit_report(state)
    return {}


def route_after_plan(state: DurableReviewState) -> str:
    return "review_one_question" if state["missing_question_ids"] else "generate_coach_report"


def route_after_review(state: DurableReviewState) -> str:
    return "review_one_question" if state["missing_question_ids"] else "generate_coach_report"


def route_after_validation(state: DurableReviewState) -> str:
    return "commit_report" if state["validation_outcome"] == "passed" else "fail_review"


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
    builder.add_node("commit_report", partial(commit_report, deps=deps))
    builder.add_node("fail_review", partial(fail_review, deps=deps))
    builder.add_edge(START, "initialize_review")
    builder.add_edge("initialize_review", "plan_question_work")
    builder.add_conditional_edges("plan_question_work", route_after_plan)
    builder.add_conditional_edges("review_one_question", route_after_review)
    builder.add_edge("generate_coach_report", "validate_report_quality")
    builder.add_conditional_edges("validate_report_quality", route_after_validation)
    builder.add_edge("commit_report", END)
    builder.add_edge("fail_review", END)
    return builder.compile(checkpointer=checkpointer)
