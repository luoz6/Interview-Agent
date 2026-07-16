# Stage 43B Durable Multi-Agent Execution and Recovery Design

Status: APPROVED_FOR_SPEC_REVIEW

Date: 2026-07-16

## 1. Context

Stage 43A established a versioned execution context and one correlation chain
across Knowledge, Orchestrator, Examiner, Shadow Reviewer, and Report Coach.
The runtime is observable, but asynchronous delivery is not yet durable.

CeleryRuntimeEventPublisher currently calls send_task only after the session
transaction has committed. If the API process fails between those operations,
the state transition survives but its round_closed event can be lost. Repeated
delivery can also invoke Reviewer more than once because question-evaluation
upsert prevents duplicate rows, not duplicate model calls.

PostgresReportJobStore already provides leases and bounded report retries.
Stage 43B applies the same operational discipline to runtime events without
replacing report jobs or turning AgentRunRecord into a workflow engine.

## 2. Prerequisite

Stage 43A must move from PENDING_CELERY_ACCEPTANCE to PASS using the existing
authenticated Redis/Celery gate. Stage 43B starts from that named PASS commit.
The Stage 43A baseline artifacts remain immutable.

## 3. Goals

Stage 43B will:

1. Atomically persist PostgreSQL session changes and runtime events.
2. Dispatch events through Local or Celery transports with leases, bounded
   retry, and dead-letter status.
3. Deduplicate consumer execution by event_id before Agent invocation.
4. Recover dispatcher and consumer work after process loss.
5. Persist sanitized Agent execution records for diagnostics.
6. Add read-only runtime execution APIs.
7. Add CLI-only event replay and failed report requeue.
8. Preserve report jobs, scoring, evidence, and deterministic routing.

## 4. Non-Goals

Stage 43B will not:

- Add WebSocket transport or Redis checkpoints.
- Use Redis Streams as the event system of record.
- Add authentication, voice, public deployment, or multi-user authorization.
- Add Agent roles, Agent voting, dynamic selection, or LLM routing.
- Replace PostgresReportJobStore with a generic work queue.
- Use agent_runs to decide whether business work completed.
- Store prompts, answers, resumes, JD text, knowledge content, provider
  responses, exception messages, secrets, DSNs, embeddings, or absolute paths.
- Add a replay HTTP endpoint.
- Claim exactly-once transport. Delivery is at least once with idempotent
  consumer execution.

## 5. Options

### 5.1 PostgreSQL transactional outbox

Persist the transition and event in one PostgreSQL transaction, then use a
leased dispatcher. Selected because PostgreSQL already owns session state and
report jobs. This closes the commit/publish loss window without a second source
of truth.

### 5.2 Ledger and retries without outbox

Rejected because direct send_task still permits a committed transition to lose
its event. Diagnostics do not repair missing work.

### 5.3 Redis Streams

Rejected because state and event durability would span PostgreSQL and Redis
without one atomic boundary. It also introduces retention and pending-entry
operations that are unnecessary here.

## 6. Architecture

    API command
      -> PostgreSQL transaction
           -> optimistic session update
           -> message persistence
           -> runtime_outbox insert
      -> commit

    outbox dispatcher
      -> claim pending event with lease
      -> Local publisher or Celery send_task
      -> mark event published

    round-review consumer
      -> claim (event_id, consumer_name) receipt
      -> evaluate one question
      -> persist QuestionEvaluationRecord
      -> mark receipt completed

PostgreSQL is the system of record. Redis and Celery only transport and
schedule. The outbox guarantees that committed state produces an event. The
receipt prevents repeated event delivery from repeating Agent business work.

## 7. PostgreSQL Data Model

All tables use INTERVIEW_RUNTIME_TABLE_PREFIX.

### 7.1 runtime_outbox

The table name is <prefix>_runtime_outbox.

| Column | Contract |
| --- | --- |
| event_id TEXT PRIMARY KEY | RuntimeEventEnvelope event identity. |
| session_id TEXT NOT NULL | REFERENCES sessions(session_id) ON DELETE CASCADE. |
| correlation_id TEXT NOT NULL | Prep or legacy session correlation. |
| event_type TEXT NOT NULL | Initially round_closed. |
| schema_version TEXT NOT NULL | Initially runtime-event-v1. |
| payload_json JSONB NOT NULL | Versioned event envelope. |
| status TEXT NOT NULL | pending, running, retrying, published, dead_letter. |
| attempt_count INTEGER NOT NULL | Delivery attempts consumed. |
| max_attempts INTEGER NOT NULL | Default 5. |
| available_at TIMESTAMPTZ NOT NULL | Earliest next claim. |
| lease_owner TEXT | Current dispatcher identity. |
| lease_expires_at TIMESTAMPTZ | Expired work is reclaimable. |
| last_error_code TEXT | Stable classification only. |
| replay_count INTEGER NOT NULL | Explicit operator replays. |
| timestamps | Created, updated, published, and dead-letter times. |

Indexes cover (status, available_at), session_id, and correlation_id.

### 7.2 runtime_event_receipts

The table name is <prefix>_runtime_event_receipts. Its primary key is
(event_id, consumer_name). It stores:

- session_id with REFERENCES sessions(session_id) ON DELETE CASCADE.
- Correlation, event type, and schema identifiers.
- Status: running, retrying, completed, or dead_letter.
- Attempt and maximum-attempt counters.
- Available time, lease owner, and lease expiry.
- Stable error code and replay count.
- Start, completion, dead-letter, creation, and update timestamps.

A completed receipt is immutable during normal delivery. Only the recovery CLI
may reset a dead-letter receipt.

### 7.3 agent_runs

The table name is <prefix>_agent_runs. It stores sanitized AgentRunRecord data:

- Run, correlation, causation, session, question, command, and evidence IDs.
- session_id uses REFERENCES sessions(session_id) ON DELETE CASCADE.
- Agent, operation, phase, status, output type, and schema version.
- Start and finish timestamps, latency, fallback reason, and error code.
- Safe metadata JSON and attempt_number.

run_id is the primary key. Indexes cover:

- (session_id, started_at)
- (correlation_id, started_at)
- (agent, status, started_at)

AgentExecutionContext receives an additive optional attempt_number field.
Synchronous calls use 1. Async consumers inherit the receipt attempt.

## 8. Atomic Session and Event Commit

PostgresInterviewSessionStore already writes state and messages in one
transaction. Command mutations will additionally:

1. Capture before_state inside the store before orchestration mutates state.
2. Build post-command state and advance version metadata.
3. Derive RoundClosedEvent from the existing before/after transition helper.
4. Persist state and messages.
5. Insert the event with the same database cursor.
6. Commit both changes together.

Optimistic update or outbox insertion failure rolls back everything. Duplicate
commands return before mutation and create no second event. Report lifecycle
updates do not emit round_closed.

The persistence boundary is explicit:

    def _replace_state(
        self,
        state: InterviewState,
        *,
        expected_previous_version: int | None = None,
        outbox_event: RoundClosedEvent | None = None,
    ) -> None:

Each command method snapshots before_state, computes after_state, calls
round_closed_event_from_transition(before_state, after_state), and passes the
result into _replace_state. The outbox insert runs only after the optimistic
session UPDATE succeeds and uses the same cursor and connection. A zero
rowcount conflict raises SessionVersionConflict, causing message, session, and
outbox changes in that connection to roll back together.

Event-producing rules are:

- submit_answer, finish, and skip pass the transition result.
- prepare_streaming_answer always passes no outbox event.
- complete_streaming_answer compares its persisted prepared state with the
  finalized state. It writes an outbox row only when finalization advances from
  the current question or finishes the interview. record_command_id=False
  preserves the command ID already committed by prepare_stream for causation.
- mark_report_processing, save_report, fail_report, progress updates, and other
  review lifecycle mutations always pass no outbox event.

The current streaming API publishes round_closed after
complete_streaming_answer. Moving that event into the final _replace_state
transaction preserves this behavior; excluding complete_stream would lose
streaming round closures.

The store exposes a read-only transactional-event capability. API routes skip
direct post-commit publication for that capability. Memory stores keep their
existing direct Local or Noop publisher path.

## 9. Outbox Dispatcher

The dispatcher repository supports:

- claim_batch(worker_id, limit, lease_seconds)
- mark_published(event_id, worker_id)
- mark_retrying(event_id, worker_id, error_code, available_at)
- mark_dead_letter(event_id, worker_id, error_code)
- release_expired_leases()

Claims use FOR UPDATE SKIP LOCKED. Defaults are:

- Batch size: 20.
- Lease: 60 seconds.
- Maximum attempts: 5.
- Retry delays: 1, 5, 30, and 120 seconds.

A successful send_task marks delivery published; consumer completion is tracked
by the receipt.

Runtime modes:

- Memory: unchanged direct publisher.
- PostgreSQL and Local: FastAPI lifespan owns one background dispatcher thread.
  It claims outbox work, invokes the receipt-controlled consumer directly,
  persists the result, and then claims the next item. It replaces
  LocalRoundReviewEventPublisher's ThreadPoolExecutor for this runtime mode.
- PostgreSQL and Celery: a standalone outbox worker sends tasks.
- Noop: explicitly disabled delivery; persisted outbox rows remain pending.

## 10. Consumer Idempotency and Failure Classification

The round-review Celery task uses acks_late and reject_on_worker_lost. It claims
the receipt before invoking Reviewer.

Receipt behavior:

- Missing: create and claim attempt 1.
- Completed: acknowledge without Reviewer invocation.
- Running with active lease: reschedule for lease expiry. If the original
  worker completes first, the rescheduled delivery observes completed and
  exits without Agent invocation.
- Running with expired lease: reclaim and increment attempt.
- Retrying before available_at: reschedule for the remaining delay.
- Retryable failure: mark retrying and schedule one bounded Celery retry.
- Permanent or exhausted failure: mark dead_letter.

The receipt counter is authoritative. Agent runner, provider adapters, and
Celery must not each create independent retry loops.

QuestionEvaluationRecord persistence and receipt completion share one
PostgreSQL transaction. A crash before that commit may cause the provider call
to run again after lease recovery, because an external model call cannot be
made exactly once. It must never create two completed receipts, duplicate
question records, or conflicting business results. A crash after the atomic
commit observes the completed receipt and does not call Reviewer again.

The shared classifier initially defines:

| Code | Retryable |
| --- | --- |
| provider_timeout | yes |
| provider_unavailable | yes |
| knowledge_unavailable | yes |
| database_unavailable | yes |
| invalid_provider_output | no |
| invalid_runtime_event | no |
| domain_validation_failed | no |
| unexpected_error | retryable only within the receipt max_attempts |

Raw exception text may go to application logs but not control tables, Celery
results, Agent traces, APIs, or acceptance artifacts.

The existing round-review wrapper catches all exceptions. Stage 43B splits it
into a raising evaluation core and a compatibility wrapper. The receipt-driven
consumer uses the raising core. It persists a failed QuestionEvaluationRecord
with a stable code only when the receipt becomes dead-lettered. A transient
attempt never overwrites completed feedback.

## 11. Agent Run Persistence

A composite recorder writes to:

- Existing optional JSON traces when AGENT_TRACE_DIR is configured.
- PostgreSQL when the runtime store is PostgreSQL.

PostgreSQL insertion is synchronous and idempotent by run_id. Recorder failure
remains non-blocking for Agent business output and emits a structured log with
only run ID, agent, operation, and stable error code.

Recovery never depends on agent_runs. The local PostgreSQL acceptance target
for ledger insertion is p95 at or below 50 ms.

## 12. Report Job Integration

PostgresReportJobStore remains report workflow owner. Add:

- requeue_failed(session_id), only for terminal failed jobs.
- replay_count and stable last_error_code.
- Receipt attempt propagation into async Reviewer and Report Coach contexts.

requeue_failed updates report_jobs and reports in one transaction. The job
returns to queued, clears its lease, terminal timestamps, and last error, and
increments replay_count. The report row returns to processing, restores the
standard queued progress payload, and clears report_json, error, completed_at,
and failed_at. Unknown, active, retrying, and completed jobs are rejected.

Existing lease reclaim, retry, maximum attempts, session uniqueness, and report
state transitions remain unchanged.

## 13. Read-Only APIs

Add:

    GET /api/interviews/{session_id}/agent-runs
    GET /api/interviews/{session_id}/runtime-events

Unknown sessions return 404. limit is bounded from 1 to 100.

Agent-run filters are agent and status. Runtime-event filters are status and
event_type. Responses expose identifiers, statuses, timestamps, counts, and
stable codes only. They exclude payload JSON, safe-metadata internals, paths,
exception messages, and connection configuration.

No recovery write API is added.

## 14. Recovery CLI

Create:

    python -m scripts.runtime_recovery list --status dead_letter
    python -m scripts.runtime_recovery replay-event --event-id <event-id>
    python -m scripts.runtime_recovery requeue-report --session-id <session-id>

list returns sanitized identifiers and statuses.

replay-event accepts dead-letter work only. In one transaction it:

1. Locks the outbox row and the receipt row when one exists.
2. Increments replay counts.
3. Clears leases and stable errors.
4. Resets attempt counters.
5. Restores outbox pending and any existing receipt to retrying.
6. Preserves event, payload, session, and correlation identity.

requeue-report accepts a terminal failed report only and preserves job and
session identity. Completed, active, pending, and unknown work is rejected.

## 15. Runtime Metadata

GET /api/runtime adds non-sensitive fields for:

- Outbox enabled.
- Agent ledger enabled.
- Event backend.
- Dispatcher mode: direct, lifespan-local, or external.
- Schema versions.

It does not expose table names, DSNs, Redis URLs, worker IDs, lease owners, or
trace paths.

The existing agent_runtime object is extended with:

    outbox_enabled
    agent_ledger_enabled

Both are booleans. Existing schema_version, event_schema_version, and
trace_enabled fields remain unchanged.

Environment settings cover batch size, poll interval, lease seconds, and
maximum attempts. Defaults work without configuration and retries stay bounded.

## 16. Testing

### Unit contracts

- Status transition validation.
- Failure classification and retry schedule.
- API response sanitization.
- Replay eligibility.
- Composite recorder failure remains non-blocking.

### PostgreSQL integration

- State and outbox commit together.
- Forced outbox failure rolls back state and messages.
- Duplicate command writes one transition and event.
- Concurrent dispatchers claim distinct rows.
- Expired dispatcher and receipt leases are reclaimed.
- Completed receipt prevents duplicate Reviewer invocation.
- QuestionEvaluationRecord and receipt completion commit atomically.
- A simulated crash before result commit may repeat a provider call but
  produces one completed business result.
- Retry exhaustion creates dead-letter state.
- Replay preserves IDs and increments replay_count.
- Session deletion cascades to all three new tables.
- Failed report requeue is idempotent.

### Local runtime

- Memory mode keeps direct publication.
- PostgreSQL Local starts and stops its lifespan dispatcher.
- Publisher outage leaves a committed pending event.
- Restart dispatches pending work.

### Celery recovery acceptance

Using authenticated Redis and PostgreSQL:

1. Close a round and verify state plus outbox commit.
2. Start dispatcher and worker; verify one evaluation and receipt.
3. Redeliver the completed event; verify Reviewer run count remains one.
4. Terminate a worker during claimed work.
5. Reclaim the expired receipt with another worker.
6. Force transient failures and verify bounded retry timing.
7. Force permanent failure and verify dead-letter.
8. Replay dead-letter and verify identity remains unchanged.

### Regression gates

- Full Python suite.
- PostgreSQL runtime and report-job suites.
- Stage 40 score ownership.
- Stage 42 evidence continuity.
- Stage 43A correlation and privacy audit.
- Deterministic Playwright.
- JavaScript syntax, CSS build, and core/celery preflight.

## 17. Acceptance Criteria

Stage 43B is PASS only when:

- Every committed PostgreSQL round transition has one outbox row.
- Publisher outage cannot lose the event.
- Duplicate delivery after completion cannot repeat Reviewer work.
- Worker-loss recovery may repeat an uncommitted provider call but cannot
  produce duplicate completed business results.
- Expired leases recover after dispatcher or worker loss.
- Retry is bounded; dead-letter work is queryable and replayable.
- Replay preserves event, session, and correlation IDs.
- One session's five Agent runs are queryable from PostgreSQL.
- Privacy audit finds no raw text, blocked fields, secrets, DSNs, or paths.
- Memory and Local V1 remain compatible.
- Report jobs, scoring, and evidence continuity stay green.
- Authenticated PostgreSQL, Redis, and Celery recovery acceptance passes.

## 18. Risks and Controls

| Risk | Control |
| --- | --- |
| Duplicate model calls | Claim before Agent; completed delivery is deduplicated. A crash may repeat only an uncommitted external call. |
| Retry multiplication | Receipt attempt is authoritative. |
| State without event | State and outbox share one cursor and transaction. |
| Local regression | Memory direct path remains; PostgreSQL Local gets dispatcher. |
| Sensitive failures | Persist stable codes and audit tables/APIs. |
| Ledger latency | Measure p95 and keep recovery independent from ledger. |
| Published before consumer completion | Expected; receipt owns completion. |
| Replay changes identity | Preserve IDs and increment replay_count. |
| Workflow-platform scope creep | Keep report jobs separate and routing fixed. |

## 19. Delivery Order

1. Close the Stage 43A authenticated Celery gate.
2. Define status, failure, and retry contracts.
3. Add PostgreSQL outbox repository.
4. Commit session state and outbox atomically.
5. Add dispatcher and lifecycle integration.
6. Add receipt-controlled Local/Celery consumer.
7. Add PostgreSQL Agent run ledger.
8. Add read-only APIs and recovery CLI.
9. Add report failed-job requeue.
10. Run recovery, privacy, browser, and full regression acceptance.
