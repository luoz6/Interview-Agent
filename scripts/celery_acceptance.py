import argparse
import json
import time

from app.services.celery_app import celery_app
from app.services.config import get_postgres_dsn, get_runtime_table_prefix
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.runtime_domain_events import RoundClosedEvent


def wait_for_evaluation(store, session_id: str, *, timeout_seconds: float) -> object | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        records = store.list_question_evaluations(session_id)
        if records:
            return records[0]
        time.sleep(0.25)
    return None


def run_acceptance(*, timeout_seconds: float = 30) -> dict:
    store = PostgresInterviewSessionStore(
        dsn=get_postgres_dsn(),
        table_prefix=get_runtime_table_prefix(),
    )
    turn = store.start(
        InterviewPlan(
            title="Stage 41 Celery acceptance",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain how Redis-backed task delivery is verified.",
                    focus="reliable asynchronous processing",
                )
            ],
        ),
        job_description="Backend engineer using Python, PostgreSQL, and Redis.",
        resume_text="Built asynchronous Python services.",
        job_tags=["python", "redis"],
    )
    store.skip(turn.session_id)
    event = RoundClosedEvent(
        session_id=turn.session_id,
        question_id="q1",
        answer_state="skipped",
        job_tags=["python", "redis"],
    )
    task_result = celery_app.send_task(
        "app.services.round_review_tasks.run_closed_round_review",
        args=[event.model_dump(mode="json")],
    )
    record = wait_for_evaluation(
        store,
        turn.session_id,
        timeout_seconds=timeout_seconds,
    )
    if record is None:
        raise TimeoutError(
            "Celery worker did not persist a question evaluation; "
            f"task_state={task_result.state}; task_info={task_result.info}"
        )
    return {
        "session_id": turn.session_id,
        "task_id": task_result.id,
        "task": "app.services.round_review_tasks.run_closed_round_review",
        "evaluation_status": record.status,
        "answer_state": record.answer_state,
        "persisted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Redis/Celery round review delivery")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    try:
        result = run_acceptance(timeout_seconds=args.timeout)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
