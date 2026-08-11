import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from app.ports.postgres_scope import PostgresCleanupReceipt, PostgresScopeError
from app.services.agent_runtime import AgentRunRecord
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.runtime_event_consumer import consume_round_review_event
from app.services.runtime_outbox_dispatcher import (
    CeleryRuntimeEventSink,
    RuntimeOutboxDispatcher,
)
from contracts.evidence import (
    AtomicEvidenceWriter,
    EvidenceIssuer,
    EvidenceRegistry,
    EvidenceVerifier,
    Stage43bRecoveryEvidencePayload,
)
from contracts.evidence.rendering import render_gate_lines
from contracts.evidence.status import VerificationStatus
from contracts.policies import Stage43bRecoveryEvidencePolicy
from scripts.audit_agent_runtime import audit_runtime_control_payloads
from scripts.postgres_acceptance_support import (
    AcceptanceConfigurationError,
    approved_postgres_scope,
    load_receipt_signer,
    require_environment_value,
)


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
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "stage43b" / "recovery-evidence-v1.json"


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


def _gate_code(value: object) -> str:
    normalized = re.sub(r"[^A-Z0-9_]+", "_", str(value).upper()).strip("_")
    return normalized or "STAGE43B_RECOVERY_FAILED"


def build_recovery_evidence(
    result: dict,
    *,
    cleanup_receipt: PostgresCleanupReceipt | None = None,
    synthetic: bool = False,
) -> Stage43bRecoveryEvidencePayload:
    passed = result.get("status") == "PASS"
    checks = result.get("checks")
    checked_count = len(checks) if isinstance(checks, dict) else 0
    cleanup_completed = cleanup_receipt is not None
    return Stage43bRecoveryEvidencePayload(
        schema_version="stage43b-recovery-evidence-v1",
        status="PASS" if passed else "FAIL",
        check_count=len(CHECKS),
        checks_passed=checked_count,
        cleanup_completed=cleanup_completed,
        cleanup_ownership_verified=(
            cleanup_receipt.ownership_verified if cleanup_receipt else False
        ),
        cleanup_target_verified=(
            cleanup_receipt.target_verified if cleanup_receipt else False
        ),
        cleanup_residue_count=(
            cleanup_receipt.residue_count if cleanup_receipt else None
        ),
        cleanup_receipt_sha256=(
            cleanup_receipt.receipt_sha256 if cleanup_receipt else None
        ),
        target_fingerprint=(
            cleanup_receipt.target_fingerprint if cleanup_receipt else None
        ),
        failure_code=None if passed else _gate_code(result.get("error_code")),
        failed_check=None if passed else result.get("failed_check"),
        synthetic=synthetic,
    )


class PostgresCeleryAcceptance:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        dsn: str,
        table_prefix: str,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.dsn = dsn
        self.prefix = table_prefix
        self.worker = None
        self.store = None
        self.control = None
        self.celery_app = None
        self.events = {}
        self._previous_environment = {
            name: os.environ.get(name)
            for name in (
                "INTERVIEW_RUNTIME_STORE",
                "INTERVIEW_RUNTIME_TABLE_PREFIX",
            )
        }

    def setup(self) -> None:
        from app.services.celery_app import celery_app

        self.celery_app = celery_app
        os.environ["INTERVIEW_RUNTIME_STORE"] = "postgres"
        os.environ["INTERVIEW_RUNTIME_TABLE_PREFIX"] = self.prefix
        self.store = PostgresInterviewSessionStore(
            dsn=self.dsn,
            table_prefix=self.prefix,
        )
        self.control = self.store._runtime_control
        self._start_worker()

    def run_check(self, name: str) -> dict:
        method = getattr(self, f"_check_{name}")
        return method()

    def cleanup(self) -> None:
        try:
            if self.worker is not None and self.worker.poll() is None:
                self.worker.terminate()
                try:
                    self.worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.worker.kill()
                    self.worker.wait(timeout=5)
        finally:
            for name, previous in self._previous_environment.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous

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

    @staticmethod
    def _require(condition, code):
        if not condition:
            raise AcceptanceFailure(code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--table-prefix",
        default="test_s43b_" + uuid4().hex[:12],
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-revision")
    parser.add_argument(
        "--output-scope",
        default="stage43b.recovery.acceptance",
    )
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args(argv)
    try:
        dsn = require_environment_value(os.environ, "POSTGRES_DSN")
        signer = load_receipt_signer(os.environ)
        output_revision = (
            args.output_revision
            or require_environment_value(os.environ, "EVIDENCE_REVISION")
        )
    except AcceptanceConfigurationError as exc:
        print("STAGE43B_RECOVERY_EVIDENCE=BLOCKED")
        print(f"GATE={exc.code}")
        return 1
    active = None
    try:
        with approved_postgres_scope(
            dsn=dsn,
            scope_prefix=args.table_prefix,
            environ=os.environ,
        ) as active:
            result = run_acceptance(
                PostgresCeleryAcceptance(
                    timeout_seconds=args.timeout,
                    dsn=dsn,
                    table_prefix=args.table_prefix,
                )
            )
    except (AcceptanceConfigurationError, PostgresScopeError) as exc:
        result = {
            "status": "FAIL",
            "error_code": exc.code,
            "failed_check": None,
            "checks": {},
        }
    cleanup_receipt = active.lease.cleanup_receipt if active is not None else None
    payload = build_recovery_evidence(
        result,
        cleanup_receipt=cleanup_receipt,
        synthetic=args.synthetic,
    )
    policy_result = Stage43bRecoveryEvidencePolicy().evaluate(payload)
    bundle = EvidenceIssuer(signer=signer).issue(
        payload_type="stage43b-recovery-evidence",
        payload=payload,
        policy_result=policy_result,
        producer="scripts.stage43b-recovery-acceptance",
        tool_version="2.0.0",
        revision=output_revision,
        scope=args.output_scope,
    )
    output_verifier = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=signer,
    )
    AtomicEvidenceWriter(
        post_write_verifier=lambda value: output_verifier.verify(
            value,
            expected_revision=output_revision,
            expected_scope=args.output_scope,
        )
    ).write(args.output, bundle)
    print(json.dumps(result, indent=2, sort_keys=True))
    print("\n".join(render_gate_lines(bundle)))
    return 0 if policy_result.verification_status is VerificationStatus.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
