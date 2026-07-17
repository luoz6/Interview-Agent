from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from app.services.agent_runtime import AgentExecutionRunner
from app.services.config import get_runtime_receipt_lease_seconds
from app.services.round_review_runner import (
    evaluate_round_review_event,
    failed_question_evaluation,
    run_round_review_event_payload,
)
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.runtime_work import (
    classify_runtime_failure,
    retry_delay_seconds,
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
    **kwargs,
) -> ConsumerOutcome:
    event = RoundClosedEvent.model_validate(payload)
    if control_store is None:
        from app.services.runtime import get_runtime_control_store

        control_store = get_runtime_control_store()
    if control_store is None:
        run_round_review_event_payload(payload)
        return ConsumerOutcome("completed")
    return consume_round_review_event(
        event,
        control_store=control_store,
        worker_id=worker_id,
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
        resolved_store = store or _get_session_store()
        state = resolved_store.get(event.session_id)
        if event.answer_state in {"skipped", "unanswered"}:
            resolved_llm = None
            resolved_vector_store = None
        else:
            resolved_llm = llm or _resolve_runtime_llm(
                resolved_store
            )
            resolved_vector_store = (
                vector_store or _get_knowledge_store()
            )
        record = evaluate_round_review_event(
            event,
            state=state,
            llm=resolved_llm,
            vector_store=resolved_vector_store,
            reviewer_factory=reviewer_factory,
            execution_runner=(
                execution_runner or _get_agent_execution_runner()
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
                    datetime.now(timezone.utc)
                    + timedelta(seconds=delay)
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
        return ConsumerOutcome(
            "dead_letter",
            error_code=failure.code,
        )


def _get_session_store():
    from app.services.runtime import get_session_store

    return get_session_store()


def _resolve_runtime_llm(store):
    from app.services.runtime import resolve_runtime_llm

    return resolve_runtime_llm(store)


def _get_knowledge_store():
    from app.services.vector_store import get_knowledge_store

    return get_knowledge_store()


def _get_agent_execution_runner():
    from app.services.runtime import get_agent_execution_runner

    return get_agent_execution_runner()
