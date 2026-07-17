import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from app.services.agent_runtime import AgentRunRecord
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.runtime_event_consumer import consume_round_review_event
from app.services.runtime_outbox_dispatcher import (
    CeleryRuntimeEventSink,
    RuntimeOutboxDispatcher,
)
from scripts.audit_agent_runtime import audit_runtime_control_payloads


CHECKS = (
    "atomic_state_outbox_commit",
    "publisher_outage_retains_pending",
    "dispatcher_recovery_publishes",
    "duplicate_delivery_one_business_result",
    "expired_receipt_reclaimed",
    "transient_failure_bounded_retry",
    "permanent_failure_dead_letter",
    "dead_letter_replay_preserves_identity",
    "agent_ledger_five_agents",
    "control_plane_privacy",
)


class AcceptanceFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def run_acceptance(adapter) -> dict:
    results = {}
    failed_check = None
    try:
        adapter.setup()
        for name in CHECKS:
            failed_check = name
            results[name] = adapter.run_check(name)
    except AcceptanceFailure as exc:
        return {
            "status": "FAIL",
            "error_code": exc.code,
            "failed_check": failed_check,
            "checks": results,
        }
    except Exception:
        return {
            "status": "FAIL",
            "error_code": "unexpected_acceptance_error",
            "failed_check": failed_check,
            "checks": results,
        }
    finally:
        adapter.cleanup()
    return {"status": "PASS", "checks": results}


class PostgresCeleryAcceptance:
    def __init__(self, *, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.prefix = "stage43b_accept_" + uuid4().hex[:10]
        self.worker = None
        self.store = None
        self.control = None
        self.celery_app = None
        self.events = {}

    def setup(self) -> None:
        from app.services.celery_app import celery_app
        from app.services.config import get_postgres_dsn

        self.celery_app = celery_app
        os.environ["INTERVIEW_RUNTIME_STORE"] = "postgres"
        os.environ["INTERVIEW_RUNTIME_TABLE_PREFIX"] = self.prefix
        self.store = PostgresInterviewSessionStore(
            dsn=get_postgres_dsn(),
            table_prefix=self.prefix,
        )
        self.control = self.store._runtime_control
        self._start_worker()

    def run_check(self, name: str) -> dict:
        method = getattr(self, f"_check_{name}")
        return method()

    def cleanup(self) -> None:
        if self.worker is not None and self.worker.poll() is None:
            self.worker.terminate()
            try:
                self.worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.wait(timeout=5)
        if self.control is not None:
            self._drop_tables()

    def _new_closed_event(self, label: str):
        turn = self.store.start(
            InterviewPlan(
                title="Stage 43B recovery",
                questions=[
                    InterviewQuestion(
                        id="q1",
                        kind="technical",
                        prompt="Explain durable delivery.",
                        focus="runtime recovery",
                    )
                ],
            ),
            job_description="Backend reliability role",
            resume_text="Built durable workers",
            job_tags=["python", "postgresql"],
        )
        self.store.skip(
            turn.session_id,
            expected_version=1,
            command_id=f"cmd-{label}",
        )
        row = self.control.list_outbox(
            session_id=turn.session_id
        )[0]
        event = RoundClosedEvent.model_validate(row["payload"])
        self.events[label] = (event, turn.session_id)
        return event, turn.session_id

    def _new_answered_event(self, label: str):
        turn = self.store.start(
            InterviewPlan(
                title="Stage 43B failure classification",
                questions=[
                    InterviewQuestion(
                        id="q1",
                        kind="technical",
                        prompt="Explain bounded retries.",
                        focus="failure classification",
                    )
                ],
            ),
            job_description="Backend reliability role",
            resume_text="Built retrying workers",
            job_tags=["python"],
        )
        event = RoundClosedEvent(
            session_id=turn.session_id,
            correlation_id=turn.session_id,
            causation_id=f"cmd-{label}",
            state_version=1,
            question_id="q1",
            answer_state="answered",
            job_tags=["python"],
        )
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                self.control.enqueue_event(cursor, event)
        self.events[label] = (event, turn.session_id)
        return event, turn.session_id

    def _check_atomic_state_outbox_commit(self):
        event, session_id = self._new_closed_event("atomic")
        state = self.store.get(session_id)
        rows = self.control.list_outbox(session_id=session_id)
        self._require(state["status"] == "finished", "atomic_state_failed")
        self._require(len(rows) == 1, "atomic_outbox_failed")
        return {"status": "PASS", "event_id": event.event_id}

    def _check_publisher_outage_retains_pending(self):
        event, session_id = self._new_closed_event("outage")
        row = self.control.list_outbox(session_id=session_id)[0]
        self._require(row["status"] == "pending", "pending_event_lost")
        return {"status": "PASS", "event_id": event.event_id}

    def _check_dispatcher_recovery_publishes(self):
        event, session_id = self._new_closed_event("celery")
        dispatcher = RuntimeOutboxDispatcher(
            self.control,
            CeleryRuntimeEventSink(celery_app=self.celery_app),
            batch_size=20,
            lease_seconds=60,
        )
        dispatcher.run_once("acceptance-dispatcher")
        self._wait_for(
            lambda: self.control.get_receipt(
                event.event_id,
                "round_review",
            ),
            lambda row: row is not None and row["status"] == "completed",
        )
        row = self.control.list_outbox(session_id=session_id)[0]
        self._require(row["status"] == "published", "event_not_published")
        return {"status": "PASS", "event_id": event.event_id}

    def _check_duplicate_delivery_one_business_result(self):
        event, session_id = self.events["celery"]
        result = self.celery_app.send_task(
            "app.services.round_review_tasks.run_closed_round_review",
            args=[event.model_dump(mode="json")],
        )
        result.get(timeout=self.timeout_seconds)
        records = self.store.list_question_evaluations(session_id)
        receipt = self.control.get_receipt(
            event.event_id,
            "round_review",
        )
        self._require(len(records) == 1, "duplicate_business_result")
        self._require(
            receipt["attempt_count"] == 1,
            "duplicate_provider_attempt",
        )
        return {"status": "PASS", "event_id": event.event_id}

    def _check_expired_receipt_reclaimed(self):
        event, _ = self._new_closed_event("expired")
        first = self.control.claim_receipt(
            event,
            consumer_name="round_review",
            worker_id="receipt-1",
            lease_seconds=1,
        )
        time.sleep(1.2)
        second = self.control.claim_receipt(
            event,
            consumer_name="round_review",
            worker_id="receipt-2",
            lease_seconds=60,
        )
        self._require(first["attempt_count"] == 1, "first_claim_failed")
        self._require(second["attempt_count"] == 2, "expired_not_reclaimed")
        return {"status": "PASS", "attempt_count": 2}

    def _check_transient_failure_bounded_retry(self):
        event, _ = self._new_answered_event("transient")

        class RetryReviewer:
            def __init__(self, **kwargs):
                pass

            def evaluate(self, state, on_progress=None):
                raise RuntimeError("private provider detail")

        outcome = consume_round_review_event(
            event,
            control_store=self.control,
            worker_id="transient-consumer",
            store=self.store,
            llm=object(),
            vector_store=object(),
            reviewer_factory=RetryReviewer,
        )
        receipt = self.control.get_receipt(
            event.event_id,
            "round_review",
        )
        self._require(outcome.status == "reschedule", "transient_not_retried")
        self._require(receipt["status"] == "retrying", "retry_not_persisted")
        return {
            "status": "PASS",
            "attempt_count": receipt["attempt_count"],
        }

    def _check_permanent_failure_dead_letter(self):
        event, session_id = self._new_answered_event("permanent")

        class PermanentReviewer:
            def __init__(self, **kwargs):
                pass

            def evaluate(self, state, on_progress=None):
                raise ValueError("private invalid output")

        outcome = consume_round_review_event(
            event,
            control_store=self.control,
            worker_id="permanent-consumer",
            store=self.store,
            llm=object(),
            vector_store=object(),
            reviewer_factory=PermanentReviewer,
        )
        records = self.store.list_question_evaluations(session_id)
        self._require(outcome.status == "dead_letter", "permanent_not_dead")
        self._require(
            records[0].error == "domain_validation_failed",
            "raw_error_persisted",
        )
        return {"status": "PASS", "error_code": outcome.error_code}

    def _check_dead_letter_replay_preserves_identity(self):
        event, _ = self._new_closed_event("replay")
        self.control.claim_batch(
            worker_id="dead-dispatcher",
            limit=20,
            lease_seconds=60,
        )
        self.control.mark_dead_letter(
            event.event_id,
            "dead-dispatcher",
            error_code="provider_unavailable",
        )
        replayed = self.control.replay_dead_letter(event.event_id)
        self._require(replayed["event_id"] == event.event_id, "event_changed")
        self._require(replayed["replay_count"] == 1, "replay_not_counted")
        return {"status": "PASS", "event_id": event.event_id}

    def _check_agent_ledger_five_agents(self):
        correlation = f"acceptance-{uuid4().hex}"
        agents = (
            "knowledge",
            "orchestrator",
            "examiner",
            "shadow_reviewer",
            "report_coach",
        )
        for agent in agents:
            self.control.record_agent_run(
                AgentRunRecord(
                    correlation_id=correlation,
                    agent=agent,
                    operation="acceptance",
                    phase=(
                        "review"
                        if agent in {"shadow_reviewer", "report_coach"}
                        else "interview"
                    ),
                    status="completed",
                    started_at="2026-07-17T00:00:00Z",
                    finished_at="2026-07-17T00:00:00Z",
                    latency_ms=0,
                )
            )
        rows = self.control.list_agent_runs(
            correlation_id=correlation,
        )
        self._require(
            {row["agent"] for row in rows} == set(agents),
            "agent_ledger_incomplete",
        )
        self.ledger_rows = rows
        return {"status": "PASS", "agent_count": len(rows)}

    def _check_control_plane_privacy(self):
        rows = self.control.list_recovery_events(
            status="pending",
            limit=100,
        ) + getattr(self, "ledger_rows", [])
        audit = audit_runtime_control_payloads(rows)
        self._require(audit["status"] == "PASS", "privacy_violation")
        return {"status": "PASS", "privacy_violations": 0}

    def _start_worker(self):
        command = [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.services.celery_app.celery_app",
            "worker",
            "--loglevel=warning",
            "--pool=solo",
            "--hostname=stage43b-acceptance@%h",
        ]
        flags = (
            subprocess.CREATE_NO_WINDOW
            if os.name == "nt"
            else 0
        )
        self.worker = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.worker.poll() is not None:
                raise AcceptanceFailure("celery_worker_exited")
            ping = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "app.services.celery_app.celery_app",
                    "inspect",
                    "ping",
                    "--timeout",
                    "2",
                ],
                cwd=str(Path(__file__).resolve().parents[1]),
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if ping.returncode == 0:
                return
            time.sleep(1)
        raise AcceptanceFailure("celery_worker_not_ready")

    def _wait_for(self, load, predicate):
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            value = load()
            if predicate(value):
                return value
            time.sleep(0.25)
        raise AcceptanceFailure("acceptance_timeout")

    def _drop_tables(self):
        _, sql = self.control._import_psycopg2()
        names = [
            self.control.receipts_table,
            self.control.agent_runs_table,
            self.control.outbox_table,
            self.store.question_evaluations_table,
            self.store.reports_table,
            self.store.messages_table,
            self.store.sessions_table,
        ]
        with self.control.connection() as connection:
            with connection.cursor() as cursor:
                for name in names:
                    cursor.execute(
                        sql.SQL("DROP TABLE IF EXISTS {table}").format(
                            table=sql.Identifier(name)
                        )
                    )

    @staticmethod
    def _require(condition, code):
        if not condition:
            raise AcceptanceFailure(code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    result = run_acceptance(
        PostgresCeleryAcceptance(timeout_seconds=args.timeout)
    )
    target = Path("tmp/stage-43b-recovery-acceptance.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
