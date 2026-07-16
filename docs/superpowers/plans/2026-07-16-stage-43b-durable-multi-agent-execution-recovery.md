# Stage 43B Durable Multi-Agent Execution and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make PostgreSQL-backed Agent event delivery durable, idempotent, recoverable, queryable, and replayable without changing deterministic routing, report score ownership, or evidence ownership.

**Architecture:** Commit versioned round events into a PostgreSQL transactional outbox beside session state, deliver them through a leased dispatcher, and guard consumer execution with leased receipts. Persist sanitized Agent runs through a composite recorder, keep report_jobs separate, and expose only read-only safe runtime views plus CLI recovery.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, psycopg2, PostgreSQL, Celery, authenticated Redis, pytest, Playwright, PowerShell.

---

## Execution Preconditions

Use F:\python3.11\python.exe for every Python command. Do not start Task 1
until Task 0 records Stage 43A PASS. Do not use an unauthenticated Redis URL.
Do not add WebSocket, Redis Streams, Redis checkpoints, replay HTTP, new Agent
roles, dynamic routing, or a generic replacement for report_jobs.

Design source:

    docs/superpowers/specs/2026-07-16-stage-43b-durable-multi-agent-execution-recovery-design.md

## File Map

New modules:

- app/services/runtime_work.py: statuses, failure classifications, retry schedule.
- app/services/postgres_runtime_control.py: outbox, receipts, Agent ledger.
- app/services/runtime_outbox_dispatcher.py: leased dispatch and polling.
- app/services/runtime_event_consumer.py: receipt-controlled round review.
- app/services/agent_recorders.py: composite recorder construction.
- app/services/runtime_outbox_worker.py: external dispatcher entry point.
- scripts/runtime_recovery.py: replay and failed report requeue.
- scripts/stage43b_recovery_acceptance.py: authenticated recovery gate.
- tests/test_runtime_work.py
- tests/test_postgres_runtime_control.py
- tests/test_runtime_outbox_dispatcher.py
- tests/test_runtime_event_consumer.py
- tests/test_agent_recorders.py
- tests/test_runtime_recovery.py
- docs/stage-43b-durable-agent-runtime-acceptance.md

Primary integration files:

- app/services/agent_runtime.py
- app/services/postgres_session.py
- app/services/session.py
- app/api/routes.py
- app/services/round_review_runner.py
- app/services/round_review_tasks.py
- app/services/celery_app.py
- app/services/runtime.py
- app/main.py
- app/services/report_jobs.py
- app/services/report_tasks.py
- app/services/config.py
- scripts/runtime_preflight.py
- tests/test_postgres_session_store.py
- tests/test_report_jobs.py
- tests/test_event_publisher.py
- tests/test_runtime_boundary_api.py
- tests/test_api.py
- tests/test_round_review.py
- tests/test_local_v1_docs.py
- tests/browser_support_app.py
- tests/browser/local-v1.spec.js
- .env.example
- README.md
- docs/local-v1-runbook.md

### Task 0: Close the Stage 43A Authenticated Celery Baseline

**Files:**

- Modify: docs/stage-43a-multi-agent-runtime-acceptance.md
- Modify: tests/test_local_v1_docs.py
- Test: scripts/celery_acceptance.py

- [ ] **Step 1: Configure authenticated dependencies**

Set real local values without committing them:

    $env:POSTGRES_DSN="postgresql://<user>:<password>@127.0.0.1:5432/interview"
    $env:REDIS_URL="redis://:<password>@127.0.0.1:6379/0"
    $env:INTERVIEW_RUNTIME_STORE="postgres"
    $env:INTERVIEW_EVENT_BACKEND="celery"

Expected: PostgreSQL and Redis accept authenticated connections.

- [ ] **Step 2: Start the existing worker**

    & 'F:\python3.11\python.exe' -m celery -A app.services.celery_app.celery_app worker --loglevel=info --pool=solo

Expected: run_closed_round_review is registered.

- [ ] **Step 3: Run the baseline**

    & 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile celery
    & 'F:\python3.11\python.exe' -m scripts.celery_acceptance --timeout 150

Expected: both exit 0 and persist one evaluation.

- [ ] **Step 4: Record PASS without credentials**

Record timestamp, profile, task/session IDs, and persisted status. Do not record
URLs, usernames, passwords, DSNs, or absolute paths.

- [ ] **Step 5: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py -q
    git add docs/stage-43a-multi-agent-runtime-acceptance.md tests/test_local_v1_docs.py
    git commit -m "docs: close stage 43a celery acceptance"

### Task 1: Define Durable Work and Attempt Contracts

**Files:**

- Create: app/services/runtime_work.py
- Create: tests/test_runtime_work.py
- Modify: app/services/agent_runtime.py
- Modify: tests/test_agent_runtime.py

- [ ] **Step 1: Write failing tests**

~~~~python
from app.services.runtime_work import (
    RuntimeFailure,
    classify_runtime_failure,
    retry_delay_seconds,
)
from app.services.report import ReportGenerationTimeout, ReportOutputFormatError


def test_retry_schedule_is_bounded():
    assert [retry_delay_seconds(value) for value in range(1, 6)] == [
        1, 5, 30, 120, 120
    ]


def test_provider_timeout_is_retryable_without_message():
    failure = classify_runtime_failure(ReportGenerationTimeout("secret"))
    assert failure == RuntimeFailure("provider_timeout", True)
    assert "secret" not in repr(failure)


def test_invalid_output_is_permanent():
    failure = classify_runtime_failure(ReportOutputFormatError("raw"))
    assert failure == RuntimeFailure("invalid_provider_output", False)


def test_unexpected_error_is_bounded_by_receipt():
    assert classify_runtime_failure(RuntimeError("x")) == RuntimeFailure(
        "unexpected_error", True
    )
~~~~

Append:

~~~~python
def test_agent_context_accepts_positive_attempt():
    assert make_context().model_copy(
        update={"attempt_number": 3}
    ).attempt_number == 3


def test_agent_context_rejects_zero_attempt():
    with pytest.raises(ValidationError):
        AgentExecutionContext(
            correlation_id="prep-1",
            agent="shadow_reviewer",
            operation="evaluate_round",
            phase="review",
            attempt_number=0,
        )
~~~~

- [ ] **Step 2: Confirm failure**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_work.py tests/test_agent_runtime.py -q

Expected: missing import or field failures.

- [ ] **Step 3: Implement contracts**

~~~~python
from dataclasses import dataclass
from typing import Literal

from app.services.report import (
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
)

OutboxStatus = Literal[
    "pending", "running", "retrying", "published", "dead_letter"
]
ReceiptStatus = Literal["running", "retrying", "completed", "dead_letter"]
RETRY_DELAYS_SECONDS = (1, 5, 30, 120)


@dataclass(frozen=True)
class RuntimeFailure:
    code: str
    retryable: bool


def retry_delay_seconds(attempt_count: int) -> int:
    index = min(max(attempt_count, 1) - 1, len(RETRY_DELAYS_SECONDS) - 1)
    return RETRY_DELAYS_SECONDS[index]


def classify_runtime_failure(exc: Exception) -> RuntimeFailure:
    if isinstance(exc, ReportGenerationTimeout):
        return RuntimeFailure("provider_timeout", True)
    if isinstance(exc, ReportOutputFormatError):
        return RuntimeFailure("invalid_provider_output", False)
    if isinstance(exc, ReportGenerationFailed):
        return RuntimeFailure("provider_unavailable", True)
    if exc.__class__.__module__.startswith("psycopg2"):
        return RuntimeFailure("database_unavailable", True)
    if isinstance(exc, (ValueError, TypeError)):
        return RuntimeFailure("domain_validation_failed", False)
    return RuntimeFailure("unexpected_error", True)
~~~~

Add to AgentExecutionContext:

~~~~python
attempt_number: int = Field(default=1, ge=1)
~~~~

- [ ] **Step 4: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_work.py tests/test_agent_runtime.py tests/test_agent_trace.py -q
    git add app/services/runtime_work.py app/services/agent_runtime.py tests/test_runtime_work.py tests/test_agent_runtime.py
    git commit -m "feat: define durable runtime work contracts"

### Task 2: Add PostgreSQL Runtime Control Schema

**Files:**

- Create: app/services/postgres_runtime_control.py
- Create: tests/test_postgres_runtime_control.py
- Modify: pytest.ini

- [ ] **Step 1: Register marker and write failing tests**

Add:

    pg_control: tests requiring PostgreSQL runtime control tables

In tests/test_postgres_runtime_control.py, copy the existing require_dsn and
unique table-prefix pattern from tests/test_postgres_session_store.py. Define
make_round_event with a fixed event_id and a valid RoundClosedEvent. The stores
fixture must create PostgresInterviewSessionStore first so the referenced
sessions and question-evaluation tables exist, then construct
PostgresRuntimeControlStore with the same DSN and prefix:

~~~~python
@pytest.fixture
def stores():
    dsn = require_dsn()
    prefix = "test_control_" + uuid4().hex[:12]
    session = PostgresInterviewSessionStore(
        dsn=dsn, table_prefix=prefix
    )
    control = PostgresRuntimeControlStore(
        dsn=dsn, table_prefix=prefix
    )
    yield {"session": session, "control": control}
    drop_runtime_tables(dsn, prefix)
~~~~

drop_runtime_tables drops control tables before the existing session tables
and verifies that all prefixed tables are gone.

~~~~python
pytestmark = pytest.mark.pg_control


def test_schema_has_cascading_session_foreign_keys(stores):
    assert stores["control"].list_foreign_keys() == {
        stores["control"].outbox_table: ("session_id", "CASCADE"),
        stores["control"].receipts_table: ("session_id", "CASCADE"),
        stores["control"].agent_runs_table: ("session_id", "CASCADE"),
    }


def test_enqueue_is_idempotent_by_event_id(stores):
    event = make_round_event()
    with stores["control"].connection() as connection:
        with connection.cursor() as cursor:
            assert stores["control"].enqueue_event(cursor, event) is True
            assert stores["control"].enqueue_event(cursor, event) is False
    assert stores["control"].count_outbox(event.event_id) == 1
~~~~

- [ ] **Step 2: Confirm failure**

    & 'F:\python3.11\python.exe' -m pytest tests/test_postgres_runtime_control.py -q

Expected: missing store, or documented skip without POSTGRES_DSN.

- [ ] **Step 3: Create store and exact outbox DDL**

~~~~python
class PostgresRuntimeControlStore:
    def __init__(self, *, dsn: str, table_prefix: str = "interview") -> None:
        self.dsn = dsn
        self.sessions_table = f"{table_prefix}_sessions"
        self.question_evaluations_table = (
            f"{table_prefix}_question_evaluations"
        )
        self.outbox_table = f"{table_prefix}_runtime_outbox"
        self.receipts_table = f"{table_prefix}_runtime_event_receipts"
        self.agent_runs_table = f"{table_prefix}_agent_runs"
        self._ensure_schema()
~~~~

~~~~sql
CREATE TABLE IF NOT EXISTS {outbox} (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
        REFERENCES {sessions}(session_id) ON DELETE CASCADE,
    correlation_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending','running','retrying','published','dead_letter')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT,
    replay_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    dead_lettered_at TIMESTAMPTZ
)
~~~~

Create receipt and Agent-run tables with the design columns, status checks,
session foreign keys using ON DELETE CASCADE, receipt composite primary key,
run_id primary key, and specified indexes. Receipt event_id also references
outbox with ON DELETE CASCADE. Agent-run session_id is nullable for the
pre-session Knowledge run; non-null values reference sessions with CASCADE.

- [ ] **Step 4: Implement event insertion**

~~~~python
def enqueue_event(self, cursor, event: RoundClosedEvent) -> bool:
    _, sql = self._import_psycopg2()
    cursor.execute(
        sql.SQL(
            """
            INSERT INTO {outbox} (
                event_id, session_id, correlation_id, event_type,
                schema_version, payload_json, status
            )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'pending')
            ON CONFLICT (event_id) DO NOTHING
            """
        ).format(outbox=sql.Identifier(self.outbox_table)),
        (
            event.event_id, event.session_id, event.correlation_id,
            event.event_type, event.schema_version, event.model_dump_json(),
        ),
    )
    return cursor.rowcount == 1
~~~~

- [ ] **Step 5: Implement outbox claims and guarded transitions**

claim_batch selects pending, available retrying, and expired running rows with
FOR UPDATE SKIP LOCKED, ordered by available_at and created_at, and updates the
lease in the same transaction. Completion methods require matching event_id,
running status, and lease_owner.

- [ ] **Step 6: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_postgres_runtime_control.py -q
    git add app/services/postgres_runtime_control.py tests/test_postgres_runtime_control.py pytest.ini
    git commit -m "feat: add postgres runtime control store"

### Task 3: Commit Session State and Outbox Atomically

**Files:**

- Modify: app/services/postgres_session.py
- Modify: app/services/session.py
- Modify: app/api/routes.py
- Modify: tests/test_postgres_session_store.py
- Modify: tests/test_api.py

- [ ] **Step 1: Write atomicity and streaming tests**

In tests/test_postgres_session_store.py, reuse require_dsn, make_table_prefix,
and make_plan. Add a stores fixture that creates one
PostgresInterviewSessionStore and exposes its _runtime_control as control. Add:

~~~~python
def start_session(store):
    return store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
~~~~

~~~~python
def test_closed_round_commits_one_event(stores):
    turn = start_session(stores["session"])
    stores["session"].skip(
        turn.session_id, expected_version=1, command_id="cmd-skip"
    )
    events = stores["control"].list_outbox(session_id=turn.session_id)
    assert len(events) == 1
    assert events[0]["payload"]["causation_id"] == "cmd-skip"
    assert events[0]["payload"]["state_version"] == 2


def test_outbox_failure_rolls_back_state_and_messages(stores, monkeypatch):
    turn = start_session(stores["session"])
    monkeypatch.setattr(
        stores["session"]._runtime_control,
        "enqueue_event",
        lambda cursor, event: (_ for _ in ()).throw(
            RuntimeError("insert failed")
        ),
    )
    with pytest.raises(RuntimeError, match="insert failed"):
        stores["session"].skip(turn.session_id, expected_version=1)
    snapshot = stores["session"].snapshot(turn.session_id)
    assert snapshot["state_version"] == 1
    assert snapshot["status"] == "active"
    assert stores["control"].list_outbox(
        session_id=turn.session_id
    ) == []
~~~~

Add two streaming tests. For a first answer, prepare chooses follow_up without
changing current_index and both prepare/finalize leave the outbox empty. For a
second answer, assert prepare sets decision.action to next_question or finish
but still leaves current_index/status unchanged and writes no event; complete
then applies speaker_node, advances or finishes, and writes exactly one event.
Also assert report lifecycle mutations write no events. These assertions lock
the invariant that prepare_answer runs brain_node but not speaker_node.

- [ ] **Step 2: Confirm failure**

    & 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py -q -k "outbox or streaming"

Expected: no outbox rows or missing repository.

- [ ] **Step 3: Inject runtime control and capability**

~~~~python
self._ensure_schema()
self._runtime_control = PostgresRuntimeControlStore(
    dsn=dsn,
    table_prefix=table_prefix,
)
self.runtime_event_delivery = "transactional_outbox"
~~~~

Construct runtime control only after sessions and question_evaluations exist.
PostgreSQL fixtures construct the session store before a separate control
store. Set runtime_event_delivery = "direct" in InterviewSessionStore.

- [ ] **Step 4: Extend the write boundary**

~~~~python
def _replace_state(
    self,
    state: InterviewState,
    *,
    expected_previous_version: int | None = None,
    outbox_event: RoundClosedEvent | None = None,
) -> None:
~~~~

Keep this exact order inside the existing connection and cursor:

1. Diff and DELETE/INSERT message rows.
2. Run the optimistic session UPDATE.
3. Check rowcount and raise SessionVersionConflict when it is zero.
4. Insert the outbox event.
5. Leave the connection context so session, messages, and event commit together.

Never insert the outbox row before the version-conflict check. After that check:

~~~~python
if outbox_event is not None:
    inserted = self._runtime_control.enqueue_event(cursor, outbox_event)
    if not inserted:
        raise RuntimeError("runtime event already exists for new transition")
~~~~

- [ ] **Step 5: Pass explicit command events**

submit_answer, finish, and skip use:

~~~~python
before_state = deepcopy(state)
# existing orchestration and _advance_state_metadata
event = round_closed_event_from_transition(before_state, new_state)
self._replace_state(
    new_state,
    expected_previous_version=previous_version,
    outbox_event=event,
)
~~~~

prepare_streaming_answer passes no event because prepare_answer only records the
answer and decision; it does not call speaker_node or close the question.
complete_streaming_answer snapshots prepared_state, finalizes it through
speaker_node, derives the prepared-to-finalized transition, and passes the
result. If prepare_answer is ever changed to advance current_index/status, move
event creation into the prepare transaction instead of carrying an in-memory
pre-prepare snapshot into complete. Review/report lifecycle writes pass None.

- [ ] **Step 6: Suppress duplicate route publication**

~~~~python
def _publish_round_closed_event(
    publisher, store, before_state, after_state
):
    if getattr(
        store, "runtime_event_delivery", "direct"
    ) == "transactional_outbox":
        return
    event = round_closed_event_from_transition(before_state, after_state)
    if event is not None:
        publisher.publish(event)
~~~~

Update answer, stream finalize, skip, and finish. Test Memory direct publish
and PostgreSQL capability suppression.

- [ ] **Step 7: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py tests/test_session_service.py tests/test_api.py tests/test_interview_rounds.py -q
    git add app/services/postgres_session.py app/services/session.py app/api/routes.py tests/test_postgres_session_store.py tests/test_api.py
    git commit -m "feat: commit interview events transactionally"

### Task 4: Add Leased Dispatch and Runtime Lifecycle

**Files:**

- Create: app/services/runtime_outbox_dispatcher.py
- Create: app/services/runtime_outbox_worker.py
- Create: tests/test_runtime_outbox_dispatcher.py
- Modify: app/services/runtime.py
- Modify: app/services/config.py
- Modify: app/main.py
- Modify: tests/test_runtime_lifecycle.py
- Modify: tests/test_event_publisher.py

- [ ] **Step 1: Write failing dispatcher tests**

~~~~python
def test_success_is_marked_published():
    repository = FakeRepository([make_claim("event-1")])
    sink = CapturingSink()
    assert RuntimeOutboxDispatcher(
        repository, sink
    ).run_once("worker-1") == 1
    assert repository.published == [("event-1", "worker-1")]


def test_transient_delivery_uses_bounded_delay():
    repository = FakeRepository([
        make_claim("event-1", attempt_count=2)
    ])
    RuntimeOutboxDispatcher(
        repository, FailingSink(RuntimeError())
    ).run_once("worker-1")
    assert repository.retried[0].error_code == "unexpected_error"
    assert repository.retried[0].delay_seconds == 5


def test_exhausted_delivery_dead_letters():
    repository = FakeRepository([
        make_claim("event-1", attempt_count=5, max_attempts=5)
    ])
    RuntimeOutboxDispatcher(
        repository, FailingSink(RuntimeError())
    ).run_once("worker-1")
    assert repository.dead_lettered == [
        ("event-1", "worker-1", "unexpected_error")
    ]
~~~~

- [ ] **Step 2: Confirm failure**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_outbox_dispatcher.py -q

Expected: missing module.

- [ ] **Step 3: Implement one dispatch cycle**

~~~~python
class RuntimeOutboxDispatcher:
    def __init__(self, repository, sink, *, batch_size=20) -> None:
        self.repository = repository
        self.sink = sink
        self.batch_size = batch_size

    def run_once(self, worker_id: str) -> int:
        claims = self.repository.claim_batch(
            worker_id=worker_id, limit=self.batch_size
        )
        for claim in claims:
            try:
                self.sink.publish(claim.payload)
            except Exception as exc:
                failure = classify_runtime_failure(exc)
                if claim.attempt_count >= claim.max_attempts:
                    self.repository.mark_dead_letter(
                        claim.event_id, worker_id, failure.code
                    )
                else:
                    self.repository.mark_retrying(
                        claim.event_id,
                        worker_id,
                        failure.code,
                        retry_delay_seconds(claim.attempt_count),
                    )
            else:
                self.repository.mark_published(
                    claim.event_id, worker_id
                )
        return len(claims)
~~~~

- [ ] **Step 4: Add the polling service and worker**

RuntimeOutboxService owns threading.Event plus one Thread. Its loop calls
run_once and waits on the stop event; do not use time.sleep. start is
idempotent and shutdown(wait=True) joins.

runtime_outbox_worker builds PostgreSQL repository and Celery sink, handles
SIGINT/SIGTERM, and runs the same service in the foreground.

- [ ] **Step 5: Integrate Local lifecycle**

~~~~python
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_runtime()
    try:
        yield
    finally:
        shutdown_runtime()
~~~~

Start the dispatcher only for PostgreSQL plus local backend. Its Local sink
invokes the receipt consumer directly. Memory keeps the existing publisher.

- [ ] **Step 6: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_outbox_dispatcher.py tests/test_runtime_lifecycle.py tests/test_event_publisher.py -q
    git add app/services/runtime_outbox_dispatcher.py app/services/runtime_outbox_worker.py app/services/runtime.py app/services/config.py app/main.py tests/test_runtime_outbox_dispatcher.py tests/test_runtime_lifecycle.py tests/test_event_publisher.py
    git commit -m "feat: dispatch persisted runtime events"

### Task 5: Add Receipt-Controlled Round Review

**Files:**

- Create: app/services/runtime_event_consumer.py
- Create: tests/test_runtime_event_consumer.py
- Modify: app/services/postgres_runtime_control.py
- Modify: app/services/round_review_runner.py
- Modify: app/services/round_review_tasks.py
- Modify: app/services/celery_app.py
- Modify: tests/test_round_review.py
- Modify: tests/test_postgres_runtime_control.py

- [ ] **Step 1: Write failing consumer tests**

~~~~python
def test_completed_receipt_skips_reviewer():
    control = FakeControl(receipt_status="completed")
    reviewer = CountingReviewer()
    outcome = consume_round_review_event(
        make_payload(), control_store=control,
        reviewer_factory=reviewer,
    )
    assert outcome.status == "duplicate_completed"
    assert reviewer.calls == 0


def test_active_lease_reschedules():
    control = FakeControl(
        receipt_status="running", lease_remaining=12
    )
    outcome = consume_round_review_event(
        make_payload(), control_store=control
    )
    assert outcome.status == "reschedule"
    assert outcome.countdown_seconds == 12


def test_result_and_receipt_complete_atomically():
    control = FakeControl(receipt_status="claimed")
    consume_round_review_event(
        make_payload(), control_store=control,
        reviewer_factory=SuccessfulReviewer,
    )
    assert control.atomic_completions == [
        ("event-1", "round_review", "q1")
    ]
~~~~

Add a test that the new evaluation core raises without persistence while the
compatibility wrapper still persists a failed record.

- [ ] **Step 2: Confirm failure**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_event_consumer.py tests/test_round_review.py -q

- [ ] **Step 3: Split evaluation from persistence**

~~~~python
def evaluate_round_review_event(
    event,
    *,
    state,
    llm,
    vector_store,
    reviewer_factory=None,
    execution_runner=None,
) -> QuestionEvaluationRecord:
    review_state = build_single_question_review_state(
        state, event.question_id
    )
    reviewer = (reviewer_factory or ShadowReviewerAgent)(
        llm=llm, vector_store=vector_store
    )
    report = (execution_runner or AgentExecutionRunner()).run(
        _review_context(event, state),
        lambda: reviewer.evaluate(review_state),
    )
    return _question_record_from_report(event, reviewer, report)
~~~~

The compatibility wrapper calls this function, catches, creates its existing
failed record, and upserts as before.

- [ ] **Step 4: Implement receipt operations**

Add claim_receipt, mark_receipt_retrying, mark_receipt_dead_letter, and
complete_round_review. complete_round_review opens one transaction, upserts
question_evaluations, and updates the matching running receipt to completed.
A zero receipt rowcount raises so the question upsert rolls back.

- [ ] **Step 5: Implement typed consumer outcome**

~~~~python
@dataclass(frozen=True)
class ConsumerOutcome:
    status: Literal[
        "completed", "duplicate_completed",
        "reschedule", "dead_letter",
    ]
    countdown_seconds: int | None = None
    error_code: str | None = None
~~~~

Claim before Reviewer. Pass receipt attempt_number to context. Reschedule active
leases; retry only while receipt attempts remain; persist stable codes only.
Permanent or exhausted failures atomically dead-letter the receipt and upsert
one failed QuestionEvaluationRecord containing the stable code. Transient
attempts do not persist failed feedback.

- [ ] **Step 6: Bind Celery task**

~~~~python
@celery_app.task(
    bind=True,
    name="app.services.round_review_tasks.run_closed_round_review",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
def run_closed_round_review(self, payload: dict) -> None:
    outcome = consume_round_review_event_payload(
        payload, worker_id=self.request.id
    )
    if outcome.status == "reschedule":
        raise self.retry(
            countdown=outcome.countdown_seconds,
            exc=RuntimeError(
                outcome.error_code or "runtime_work_retry"
            ),
        )
~~~~

Receipt max_attempts is authoritative; Celery has no independent finite count.

- [ ] **Step 7: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_event_consumer.py tests/test_round_review.py tests/test_postgres_runtime_control.py tests/test_event_publisher.py -q
    git add app/services/runtime_event_consumer.py app/services/postgres_runtime_control.py app/services/round_review_runner.py app/services/round_review_tasks.py app/services/celery_app.py tests/test_runtime_event_consumer.py tests/test_round_review.py tests/test_postgres_runtime_control.py
    git commit -m "feat: consume runtime events idempotently"

### Task 6: Persist Agent Runs Through a Composite Recorder

**Files:**

- Create: app/services/agent_recorders.py
- Create: tests/test_agent_recorders.py
- Modify: app/services/postgres_runtime_control.py
- Modify: app/services/agent_runtime.py
- Modify: app/services/runtime.py
- Modify: app/api/routes.py
- Modify: app/services/session.py
- Modify: app/services/postgres_session.py
- Modify: app/services/round_review_runner.py
- Modify: app/services/report_tasks.py

- [ ] **Step 1: Write failing recorder tests**

~~~~python
def test_composite_continues_after_one_recorder_fails():
    record = make_record()
    healthy = CapturingRecorder()
    CompositeAgentRunRecorder([
        FailingRecorder(), healthy
    ]).record(record)
    assert healthy.records == [record]


def test_postgres_insert_is_idempotent(pg_control):
    record = make_record()
    recorder = PostgresAgentRunRecorder(pg_control)
    recorder.record(record)
    recorder.record(record)
    assert pg_control.count_agent_runs(record.run_id) == 1


def test_public_query_excludes_safe_metadata(pg_control):
    pg_control.record_agent_run(make_record())
    item = pg_control.list_agent_runs(session_id="s1")[0]
    assert "safe_metadata" not in item
~~~~

- [ ] **Step 2: Confirm failure**

    & 'F:\python3.11\python.exe' -m pytest tests/test_agent_recorders.py -q

- [ ] **Step 3: Implement composition**

~~~~python
class CompositeAgentRunRecorder:
    def __init__(self, recorders) -> None:
        self.recorders = list(recorders)

    def record(self, record: AgentRunRecord) -> None:
        for recorder in self.recorders:
            try:
                recorder.record(record)
            except Exception:
                logger.warning(
                    "agent recorder failed",
                    extra={
                        "run_id": record.run_id,
                        "agent": record.agent,
                        "operation": record.operation,
                        "error_code": "agent_recorder_unavailable",
                    },
                )
~~~~

PostgresAgentRunRecorder delegates to record_agent_run. SQL uses INSERT with
ON CONFLICT (run_id) DO NOTHING.

- [ ] **Step 4: Build one cached runtime runner**

runtime.get_agent_execution_runner composes AgentTraceRecorder.from_env and,
for PostgreSQL mode, PostgresAgentRunRecorder. Memory uses trace only. Reset
and shutdown clear the cache.

Pass this runner through existing optional injection points:

- API prepare_interview.
- InterviewSessionStore into Orchestrator and Examiner.
- Round-review consumer.
- Full-session report execution.

Do not make standalone Agent constructors connect to PostgreSQL.

- [ ] **Step 5: Propagate attempts**

~~~~python
context = existing_context.model_copy(
    update={"attempt_number": receipt.attempt_count}
)
~~~~

Report jobs pass attempt_count to async Reviewer and Coach. Compatibility and
synchronous calls default to 1.

- [ ] **Step 6: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_agent_recorders.py tests/test_agent_runtime.py tests/test_agents.py tests/test_interview_graph.py tests/test_round_review.py tests/test_report_tasks.py -q
    git add app/services/agent_recorders.py app/services/postgres_runtime_control.py app/services/agent_runtime.py app/services/runtime.py app/api/routes.py app/services/session.py app/services/postgres_session.py app/services/round_review_runner.py app/services/report_tasks.py tests/test_agent_recorders.py
    git commit -m "feat: persist sanitized agent run ledger"

### Task 7: Add Safe Read-Only Runtime APIs

**Files:**

- Modify: app/api/routes.py
- Modify: app/services/postgres_runtime_control.py
- Modify: app/services/runtime.py
- Modify: tests/test_api.py
- Modify: tests/test_runtime_boundary_api.py

- [ ] **Step 1: Write failing API tests**

Extend tests/test_api.py's make_client to accept an optional control store and
override the new get_runtime_control_store dependency. Use a FakeRuntimeControl
whose list_agent_runs and list_runtime_events return one fully populated safe
row and record the received filters. Start a real in-memory session first and
use its session_id in the requests; bind runtime_client to that client and ID.
This proves session existence checks and response projection without requiring
PostgreSQL in the API unit suite.

~~~~python
def test_agent_runs_returns_only_safe_fields(runtime_client):
    response = runtime_client.get(
        "/api/interviews/s1/agent-runs?agent=examiner"
    )
    item = response.json()["items"][0]
    assert set(item) == {
        "run_id", "correlation_id", "causation_id",
        "agent", "operation", "phase", "session_id",
        "question_id", "state_version", "command_id",
        "evidence_ids", "attempt_number", "status",
        "started_at", "finished_at", "latency_ms",
        "fallback_reason", "error_code", "output_type",
    }
    assert "safe_metadata" not in response.text


def test_runtime_events_excludes_payload_and_lease(runtime_client):
    response = runtime_client.get(
        "/api/interviews/s1/runtime-events"
    )
    assert response.status_code == 200
    assert "payload_json" not in response.text
    assert "lease_owner" not in response.text


def test_limit_above_one_hundred_is_rejected(runtime_client):
    assert runtime_client.get(
        "/api/interviews/s1/agent-runs?limit=101"
    ).status_code == 422
~~~~

- [ ] **Step 2: Confirm failure**

    & 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_runtime_boundary_api.py -q -k "agent_runs or runtime_events or outbox_enabled"

- [ ] **Step 3: Add bounded routes**

~~~~python
@router.get("/interviews/{session_id}/agent-runs")
def list_agent_runs(
    session_id: str,
    agent: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    store=Depends(get_session_store),
    control=Depends(get_runtime_control_store),
):
    state = store.get(session_id)
    correlation_id = correlation_id_from_plan(
        state["plan"], session_id=session_id
    )
    return {
        "session_id": session_id,
        "items": control.list_agent_runs(
            session_id=session_id,
            correlation_id=correlation_id,
            agent=agent,
            status=status,
            limit=limit,
        ),
    }
~~~~

Add the analogous event route. Repository API methods SELECT explicit public
columns and never select payload_json or safe_metadata. Agent-run lookup uses
session_id OR the persisted plan correlation so the pre-session Knowledge run
is included while abandoned Prep previews are excluded.

- [ ] **Step 4: Extend runtime metadata**

~~~~python
"agent_runtime": {
    "schema_version": "agent-runtime-v1",
    "event_schema_version": "runtime-event-v1",
    "trace_enabled": bool(os.getenv("AGENT_TRACE_DIR")),
    "outbox_enabled": runtime_store == "postgres",
    "agent_ledger_enabled": runtime_store == "postgres",
}
~~~~

- [ ] **Step 5: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_runtime_boundary_api.py tests/test_report_api.py -q
    git add app/api/routes.py app/services/postgres_runtime_control.py app/services/runtime.py tests/test_api.py tests/test_runtime_boundary_api.py
    git commit -m "feat: expose safe runtime execution status"

### Task 8: Add Dead-Letter Replay and Report Requeue

**Files:**

- Create: scripts/runtime_recovery.py
- Create: tests/test_runtime_recovery.py
- Modify: app/services/postgres_runtime_control.py
- Modify: app/services/report_jobs.py
- Modify: app/services/report_worker.py
- Modify: tests/test_postgres_runtime_control.py
- Modify: tests/test_report_jobs.py
- Modify: tests/test_report_worker.py

- [ ] **Step 1: Write failing recovery tests**

For PostgreSQL cases, reuse the Task 2 require_dsn/prefix/cleanup fixture and
its make_round_event helper. seed_dead_letter and seed_pending insert through
the control store and use guarded transition methods, never direct SQL state
rewrites. In tests/test_report_jobs.py, extend the existing stores fixture with
the current session_store/job_store keys; seed_failed_report calls
create_session, enqueue_report_request, claim_next, and mark_failed so it
exercises the public job lifecycle.

~~~~python
def test_replay_preserves_event_identity(pg_control):
    event = seed_dead_letter(pg_control)
    result = pg_control.replay_dead_letter(event.event_id)
    assert result["event_id"] == event.event_id
    assert result["correlation_id"] == event.correlation_id
    assert result["status"] == "pending"
    assert result["attempt_count"] == 0
    assert result["replay_count"] == 1


def test_replay_rejects_pending_event(pg_control):
    event = seed_pending(pg_control)
    with pytest.raises(
        ValueError, match="event is not dead-lettered"
    ):
        pg_control.replay_dead_letter(event.event_id)


def test_report_requeue_updates_both_tables(stores):
    session_id, job_id = seed_failed_report(stores)
    job = stores["jobs"].requeue_failed(session_id)
    report = stores["jobs"].get_report_row(session_id)
    assert job["job_id"] == job_id
    assert job["status"] == "queued"
    assert job["replay_count"] == 1
    assert report["status"] == "processing"


def test_report_failure_persists_stable_error_code(stores):
    session_id, job_id = seed_running_report(stores)
    stores["jobs"].mark_failed(
        job_id,
        "internal detail",
        error_code="domain_validation_failed",
    )
    job = stores["jobs"].get_job_by_session(session_id)
    assert job["last_error_code"] == "domain_validation_failed"
~~~~

- [ ] **Step 2: Confirm failure**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_recovery.py tests/test_report_jobs.py -q

- [ ] **Step 3: Implement atomic event replay**

Lock the outbox row and optional receipt rows with FOR UPDATE. Require
dead_letter, preserve event and payload identities, clear leases/error codes,
reset attempts, increment replay_count, set outbox pending, and set existing
receipts retrying.

- [ ] **Step 4: Implement atomic report requeue**

Add replay_count and last_error_code with ALTER TABLE IF NOT EXISTS. Extend
mark_retryable_failure and mark_failed with a keyword-only error_code and
persist it in the same jobs UPDATE. Include last_error_code in private job-store
rows, but expose only that stable code through recovery/status surfaces.

Update report_worker catch branches without changing existing retryability:
ReportGenerationTimeout uses provider_timeout; a retryable
ReportGenerationFailed uses provider_unavailable; a non-retryable
ReportGenerationFailed and ValueError use domain_validation_failed; the final
Exception branch uses unexpected_error. Pass both the internal error text and
stable code to the job store. Then implement requeue with:

~~~~sql
WITH requeued AS (
    UPDATE {jobs}
    SET status = 'queued',
        lease_owner = NULL,
        lease_expires_at = NULL,
        attempt_count = 0,
        last_error = NULL,
        last_error_code = NULL,
        replay_count = replay_count + 1,
        started_at = NULL,
        finished_at = NULL,
        updated_at = NOW()
    WHERE session_id = %s AND status = 'failed'
    RETURNING *
)
UPDATE {reports}
SET status = 'processing',
    progress_json = %s::jsonb,
    report_json = NULL,
    error = NULL,
    completed_at = NULL,
    failed_at = NULL,
    updated_at = NOW()
FROM requeued
WHERE {reports}.session_id = requeued.session_id
RETURNING requeued.*
~~~~

Zero rows raises ValueError("report job is not failed").

- [ ] **Step 5: Implement CLI**

Use argparse subcommands list, replay-event, and requeue-report. Resolve config
through app.services.config. Output only IDs, status, attempts, replay count,
timestamps, and stable codes. Invalid state exits 1 with a stable code.

- [ ] **Step 6: Verify and commit**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_recovery.py tests/test_postgres_runtime_control.py tests/test_report_jobs.py tests/test_report_worker.py -q
    git add scripts/runtime_recovery.py app/services/postgres_runtime_control.py app/services/report_jobs.py app/services/report_worker.py tests/test_runtime_recovery.py tests/test_postgres_runtime_control.py tests/test_report_jobs.py tests/test_report_worker.py
    git commit -m "feat: replay dead-letter runtime work"

### Task 9: Add Documentation, Privacy Audit, and Local Gates

**Files:**

- Modify: scripts/audit_agent_runtime.py
- Modify: tests/test_agent_runtime_audit.py
- Modify: scripts/runtime_preflight.py
- Modify: tests/test_runtime_preflight.py
- Modify: tests/browser_support_app.py
- Modify: tests/browser/local-v1.spec.js
- Modify: .env.example
- Modify: README.md
- Modify: docs/local-v1-runbook.md
- Modify: tests/test_local_v1_docs.py
- Create: docs/stage-43b-durable-agent-runtime-acceptance.md

- [ ] **Step 1: Extend privacy tests**

Add sanitized row fixtures for outbox, receipts, and Agent runs. Assert failure
for payload exposure, safe_metadata exposure, raw exception text, DSN, absolute
path, resume/JD/answer keys, and natural-language values. A metadata-only
control export must pass.

- [ ] **Step 2: Extend preflight**

For PostgreSQL runtime, verify all tables and indexes, ON DELETE CASCADE session
foreign keys, and positive lease/max-attempt settings. Measure 20 Agent ledger
inserts; fail if local p95 exceeds 50 ms. Never print credentials.

- [ ] **Step 3: Preserve browser acceptance**

Memory browser support keeps direct events and the existing five-Agent
prep_run_id audit. Assert runtime metadata reports outbox disabled in Memory
and no control response includes candidate text.

- [ ] **Step 4: Document bounded settings**

Add:

    RUNTIME_OUTBOX_BATCH_SIZE=20
    RUNTIME_OUTBOX_LEASE_SECONDS=60
    RUNTIME_OUTBOX_POLL_SECONDS=0.5
    RUNTIME_OUTBOX_MAX_ATTEMPTS=5
    RUNTIME_RECEIPT_LEASE_SECONDS=300

Document:

    python -m app.services.runtime_outbox_worker
    python -m scripts.runtime_recovery list --status dead_letter

State PostgreSQL is the source of truth and Redis/Celery is transport.

- [ ] **Step 5: Create pending acceptance record**

Use status PENDING_RECOVERY_ACCEPTANCE. Include gates for unit, PostgreSQL,
Local, Celery, duplicate delivery, worker loss, retry/dead-letter, replay,
privacy, ledger latency, Stage 40, Stage 42, Stage 43A, browser, and full suite.

- [ ] **Step 6: Verify deterministic gates**

    & 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime_audit.py tests/test_runtime_preflight.py tests/test_local_v1_docs.py tests/test_runtime_boundary_api.py -q
    $env:STAGE41_PYTHON='F:\python3.11\python.exe'
    npm run test:browser

Expected: Python gates pass and Playwright has eight deterministic passes with
only real-model opt-in skips.

- [ ] **Step 7: Commit**

    git add scripts/audit_agent_runtime.py scripts/runtime_preflight.py tests/test_agent_runtime_audit.py tests/test_runtime_preflight.py tests/browser_support_app.py tests/browser/local-v1.spec.js .env.example README.md docs/local-v1-runbook.md docs/stage-43b-durable-agent-runtime-acceptance.md tests/test_local_v1_docs.py
    git commit -m "test: document durable agent recovery gates"

### Task 10: Run Authenticated Recovery and Release Gates

**Files:**

- Create: scripts/stage43b_recovery_acceptance.py
- Create: tests/test_stage43b_recovery_acceptance.py
- Modify: docs/stage-43b-durable-agent-runtime-acceptance.md

- [ ] **Step 1: Write failing acceptance-runner tests**

Use fake process/repository adapters. Every failed check must exit nonzero,
emit a stable code, terminate owned processes, and omit credentials and paths.

- [ ] **Step 2: Implement named checks**

~~~~python
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
~~~~

Use a unique table prefix, own only started processes, clean tables in finally,
and write sanitized JSON to tmp/stage-43b-recovery-acceptance.json.

- [ ] **Step 3: Verify focused suites**

    & 'F:\python3.11\python.exe' -m pytest tests/test_runtime_work.py tests/test_postgres_runtime_control.py tests/test_runtime_outbox_dispatcher.py tests/test_runtime_event_consumer.py tests/test_agent_recorders.py tests/test_runtime_recovery.py tests/test_report_jobs.py tests/test_postgres_session_store.py -q

Expected: PASS with authenticated PostgreSQL.

- [ ] **Step 4: Run authenticated Celery recovery**

Start owned Celery and outbox workers, then run:

    & 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile celery
    & 'F:\python3.11\python.exe' -m scripts.stage43b_recovery_acceptance --timeout 180

Expected: all ten checks PASS.

- [ ] **Step 5: Run full regression**

    & 'F:\python3.11\python.exe' -m pytest -q
    node --check app/static/api.js
    node --check app/static/shared-ui.js
    node --check app/static/prep.js
    node --check app/static/interview.js
    node --check app/static/report-processing.js
    node --check app/static/report-detail.js
    npm run build:prototype-css
    $env:STAGE41_PYTHON='F:\python3.11\python.exe'
    npm run test:browser
    & 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile core
    & 'F:\python3.11\python.exe' -m scripts.audit_stage40_artifacts
    & 'F:\python3.11\python.exe' -m scripts.audit_stage42_artifacts --run-dir reports/stage42-acceptance/20260716T062331Z-real-model-rc --run-id 20260716T062331Z-real-model-rc

Expected: all exit 0. Generic pytest may skip only documented opt-in services
and real-model tests.

- [ ] **Step 6: Record PASS**

Record exact counts, timestamp, unique prefix, retries, replay metrics, ledger
p95, correlation rate, privacy result, and process cleanup. Set PASS only when
Task 0 and every Task 10 command passed.

- [ ] **Step 7: Commit**

    git add scripts/stage43b_recovery_acceptance.py tests/test_stage43b_recovery_acceptance.py docs/stage-43b-durable-agent-runtime-acceptance.md
    git commit -m "test: accept durable multi-agent recovery"

## Final Review Checklist

- [ ] Stage 43A authenticated Celery baseline is PASS first.
- [ ] Session state and outbox share one transaction.
- [ ] prepare_stream creates no event; closing complete_stream creates one.
- [ ] Memory remains direct; PostgreSQL Local uses lifespan dispatcher.
- [ ] Completed duplicate delivery invokes no Reviewer.
- [ ] Crash recovery may repeat an uncommitted provider call but never duplicates a completed business result.
- [ ] Receipt max_attempts is the only consumer retry authority.
- [ ] Agent ledger failure cannot fail business output.
- [ ] Recovery never infers completion from agent_runs.
- [ ] Requeue updates report_jobs and reports atomically.
- [ ] APIs exclude payload_json, safe_metadata, raw errors, leases, paths, and configuration.
- [ ] All new tables use session ON DELETE CASCADE.
- [ ] Redis Streams, WebSocket, Redis checkpoints, replay HTTP, new Agents, and dynamic routing remain out of scope.
- [ ] Authenticated PostgreSQL/Redis/Celery recovery passes before PASS.
