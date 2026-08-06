from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from app.graphs.durable_review_state import DurableReviewState
from app.services.runtime_work import RuntimeFailure, classify_runtime_failure


@dataclass(frozen=True)
class DurableReviewGraphDependencies:
    workflow_store: object
    review_question: object
    generate_report: object
    validate_report: object
    commit_report: object
    generate_degraded_report: object | None = None
    max_provider_attempts: int = 3
    max_quality_repairs: int = 2
    repair_report: object | None = None
    max_parallel_reviews: int = 3
    fault_injector: object | None = None
    failure_classifier: Callable[[Exception], RuntimeFailure] = (
        classify_runtime_failure
    )


def _inject_fault(deps: DurableReviewGraphDependencies, point: str, state) -> None:
    if deps.fault_injector is not None:
        deps.fault_injector(point, state)


def initialize_review(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    deps.workflow_store.initialize_run(
        job_id=state["job_id"],
        session_id=state["session_id"],
        graph_schema_version=state["review_graph_schema_version"],
        input_sha256=state["review_input_manifest"]["input_sha256"],
    )
    _inject_fault(deps, "after_review_run_initialize", state)
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


def prepare_question_batch(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    batch = state["missing_question_ids"][: deps.max_parallel_reviews]
    return {"current_batch_question_ids": batch}


def dispatch_question_batch(state: DurableReviewState):
    if not state["current_batch_question_ids"]:
        return "generate_coach_report"
    inputs = {
        item["question_id"]: item["input_sha256"]
        for item in state["review_input_manifest"]["questions"]
    }
    return [
        Send(
            "review_question",
            {
                "job_id": state["job_id"],
                "session_id": state["session_id"],
                "review_graph_schema_version": state["review_graph_schema_version"],
                "review_input_manifest": state["review_input_manifest"],
                "provider_attempt": state["provider_attempt"],
                "current_question_id": question_id,
                "question_input_sha256": inputs[question_id],
                "question_outcomes": [],
            },
        )
        for question_id in state["current_batch_question_ids"]
    ]


def review_one_question(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    question_id = state["current_question_id"]
    try:
        deps.review_question(state, question_id)
    except Exception as exc:
        failure = deps.failure_classifier(exc)
        return {
            "question_outcomes": [
                {
                    "question_id": question_id,
                    "question_input_sha256": state[
                        "question_input_sha256"
                    ],
                    "outcome": "failed",
                    "retryable": failure.retryable,
                    "error_code": failure.code,
                }
            ],
        }
    _inject_fault(deps, "after_question_projection", state)
    return {
        "question_outcomes": [
            {
                "question_id": question_id,
                "question_input_sha256": state["question_input_sha256"],
                "outcome": "completed",
            }
        ],
    }


def join_question_reviews(state: DurableReviewState) -> dict:
    batch = state["current_batch_question_ids"]
    completed_outcomes = {
        item["question_id"]
        for item in state["question_outcomes"]
        if item.get("outcome") == "completed"
    }
    if any(question_id not in completed_outcomes for question_id in batch):
        failed = [
            question_id
            for question_id in batch
            if question_id not in completed_outcomes
        ]
        failure_codes = [
            item.get("error_code")
            for item in state["question_outcomes"]
            if item.get("question_id") in failed and item.get("error_code")
        ]
        return {
            "error_code": failure_codes[0]
            if failure_codes
            else "question_review_failed",
            "failed_question_ids": failed,
        }
    return {
        "completed_question_ids": [*state["completed_question_ids"], *batch],
        "missing_question_ids": [item for item in state["missing_question_ids"] if item not in batch],
        "next_batch_start": state["next_batch_start"] + len(batch),
        "current_batch_question_ids": [],
    }


def generate_coach_report(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    try:
        artifact = (
            deps.repair_report(state)
            if state["quality_repair_count"] > 0 and deps.repair_report is not None
            else deps.generate_report(state)
        )
    except Exception as exc:
        failure = deps.failure_classifier(exc)
        retryable = (
            failure.retryable
            and state["provider_attempt"] < deps.max_provider_attempts
        )
        if not retryable and _can_publish_degraded_report(failure.code, deps):
            try:
                artifact = deps.generate_degraded_report(state, failure.code)
            except Exception as degraded_exc:
                degraded_failure = deps.failure_classifier(degraded_exc)
                return {
                    "generation_outcome": "terminal",
                    "error_code": degraded_failure.code,
                }
            return {
                "report_ref": artifact["report_ref"],
                "report_sha256": artifact["report_sha256"],
                "generation_outcome": "completed",
                "error_code": None,
            }
        return {
            "generation_outcome": (
                "retryable"
                if retryable
                else "terminal"
            ),
            "error_code": failure.code,
        }
    _inject_fault(deps, "after_coach_generation", state)
    return {
        "report_ref": artifact["report_ref"],
        "report_sha256": artifact["report_sha256"],
        "generation_outcome": "completed",
        "error_code": None,
    }


def _can_publish_degraded_report(
    failure_code: str,
    deps: DurableReviewGraphDependencies,
) -> bool:
    return deps.generate_degraded_report is not None and failure_code in {
        "provider_timeout",
        "provider_unavailable",
        "provider_auth_failed",
        "invalid_provider_output",
    }


def validate_report_quality(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    try:
        result = deps.validate_report(state)
    except Exception:
        result = (
            "failed",
            [
                {
                    "code": "report_validation_failed",
                    "description": "runtime report validation did not complete",
                    "question_id": None,
                }
            ],
        )
    _inject_fault(deps, "after_quality_validation", state)
    if isinstance(result, tuple):
        outcome, issues = result
    else:
        outcome, issues = result, []
    return {"validation_outcome": outcome, "quality_issues": issues}


def commit_report(state: DurableReviewState, deps: DurableReviewGraphDependencies) -> dict:
    deps.commit_report(state)
    _inject_fault(deps, "after_final_commit", state)
    return {}


def route_after_plan(state: DurableReviewState) -> str:
    return "prepare_question_batch" if state["missing_question_ids"] else "generate_coach_report"


def route_after_review(state: DurableReviewState) -> str:
    if state.get("failed_question_ids"):
        return "fail_review"
    return "prepare_question_batch" if state["missing_question_ids"] else "generate_coach_report"


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
    error_code = state.get("error_code") or "report_quality_failed"
    deps.workflow_store.fail_review(state["job_id"], error_code)
    return {"error_code": error_code}


def build_durable_review_graph(deps: DurableReviewGraphDependencies, *, checkpointer):
    builder = StateGraph(DurableReviewState)
    builder.add_node("initialize_review", partial(initialize_review, deps=deps))
    builder.add_node("plan_question_work", partial(plan_question_work, deps=deps))
    builder.add_node("review_question", partial(review_one_question, deps=deps))
    builder.add_node("prepare_question_batch", partial(prepare_question_batch, deps=deps))
    builder.add_node("join_question_reviews", join_question_reviews)
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
    builder.add_conditional_edges("prepare_question_batch", dispatch_question_batch)
    builder.add_edge("review_question", "join_question_reviews")
    builder.add_conditional_edges("join_question_reviews", route_after_review)
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
