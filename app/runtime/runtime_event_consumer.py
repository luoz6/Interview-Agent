from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from app.adapters.reliability.runtime_failure import (
    classify_runtime_failure,
    retry_delay_seconds,
)
from app.domain.runtime_events import RoundClosedEvent
from app.runtime.agent_execution import AgentExecutionRunner
from app.runtime.config.compatibility import get_runtime_receipt_lease_seconds
from app.runtime.round_review import (
    evaluate_round_review_event,
    failed_question_evaluation,
)


ROUND_REVIEW_CONSUMER = "round_review"


@dataclass(frozen=True)
class ConsumerOutcome:
    status: Literal[
        "completed",
        "duplicate_completed",
        "reschedule",
        "dead_letter",
    ]
    countdown_seconds: int | None = None
    error_code: str | None = None


def consume_round_review_event_payload(
    payload: dict,
    *,
    control_store=None,
    worker_id: str,
    store=None,
    get_runtime_control_store: Callable | None = None,
    run_event_payload: Callable | None = None,
    get_session_store: Callable | None = None,
    resolve_runtime_llm: Callable | None = None,
    get_knowledge_store: Callable | None = None,
    get_agent_execution_runner: Callable | None = None,
    **kwargs,
) -> ConsumerOutcome:
    event = RoundClosedEvent.model_validate(payload)
    if control_store is None:
        if get_runtime_control_store is None:
            raise RuntimeError("runtime control store resolver is unavailable")
        control_store = get_runtime_control_store()
    if control_store is None:
        if run_event_payload is None:
            raise RuntimeError("round review payload runner is unavailable")
        run_event_payload(payload)
        return ConsumerOutcome("completed")
    return consume_round_review_event(
        event,
        control_store=control_store,
        worker_id=worker_id,
        store=store,
        get_session_store=get_session_store,
        resolve_runtime_llm=resolve_runtime_llm,
        get_knowledge_store=get_knowledge_store,
        get_agent_execution_runner=get_agent_execution_runner,
        **kwargs,
    )


def consume_round_review_event(
    event: RoundClosedEvent,
    *,
    control_store,
    worker_id: str,
    store=None,
    llm=None,
    vector_store=None,
    reviewer_factory: Callable | None = None,
    execution_runner: AgentExecutionRunner | None = None,
    receipt_lease_seconds: int | None = None,
    get_session_store: Callable | None = None,
    resolve_runtime_llm: Callable | None = None,
    get_knowledge_store: Callable | None = None,
    get_agent_execution_runner: Callable | None = None,
) -> ConsumerOutcome:
    lease_seconds = (
        receipt_lease_seconds
        if receipt_lease_seconds is not None
        else get_runtime_receipt_lease_seconds()
    )
    receipt = control_store.claim_receipt(
        event,
        consumer_name=ROUND_REVIEW_CONSUMER,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )
    claim_status = receipt["claim_status"]
    if claim_status == "completed":
        return ConsumerOutcome("duplicate_completed")
    if claim_status == "dead_letter":
        return ConsumerOutcome(
            "dead_letter",
            error_code=receipt.get("last_error_code"),
        )
    if claim_status in {"active", "retry_wait"}:
        return ConsumerOutcome(
            "reschedule",
            countdown_seconds=receipt["countdown_seconds"],
            error_code="runtime_receipt_not_ready",
        )

    try:
        if store is None and get_session_store is None:
            raise RuntimeError("session store resolver is unavailable")
        resolved_store = store or get_session_store()
        state = resolved_store.get(event.session_id)
        if event.answer_state in {"skipped", "unanswered"}:
            resolved_llm = None
            resolved_vector_store = None
        else:
            if llm is None and resolve_runtime_llm is None:
                raise RuntimeError("runtime LLM resolver is unavailable")
            if vector_store is None and get_knowledge_store is None:
                raise RuntimeError("knowledge store resolver is unavailable")
            resolved_llm = llm or resolve_runtime_llm(resolved_store)
            resolved_vector_store = vector_store or get_knowledge_store()
        if execution_runner is None and get_agent_execution_runner is None:
            raise RuntimeError("agent execution runner resolver is unavailable")
        record = evaluate_round_review_event(
            event,
            state=state,
            llm=resolved_llm,
            vector_store=resolved_vector_store,
            reviewer_factory=reviewer_factory,
            execution_runner=(
                execution_runner or get_agent_execution_runner()
            ),
            attempt_number=receipt["attempt_count"],
        )
        control_store.complete_round_review(
            event.event_id,
            ROUND_REVIEW_CONSUMER,
            worker_id,
            record,
        )
        return ConsumerOutcome("completed")
    except Exception as exc:
        failure = classify_runtime_failure(exc)
        if (
            failure.retryable
            and receipt["attempt_count"] < receipt["max_attempts"]
        ):
            delay = retry_delay_seconds(receipt["attempt_count"])
            updated = control_store.mark_receipt_retrying(
                event.event_id,
                ROUND_REVIEW_CONSUMER,
                worker_id,
                error_code=failure.code,
                available_at=(
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ),
            )
            if updated is None:
                raise RuntimeError("runtime receipt lease was lost") from exc
            return ConsumerOutcome(
                "reschedule",
                countdown_seconds=delay,
                error_code=failure.code,
            )
        failed_record = failed_question_evaluation(
            session_id=event.session_id,
            question_id=event.question_id,
            answer_state=event.answer_state,
            error=failure.code,
        )
        control_store.fail_round_review(
            event.event_id,
            ROUND_REVIEW_CONSUMER,
            worker_id,
            failed_record,
            error_code=failure.code,
        )
        return ConsumerOutcome("dead_letter", error_code=failure.code)
