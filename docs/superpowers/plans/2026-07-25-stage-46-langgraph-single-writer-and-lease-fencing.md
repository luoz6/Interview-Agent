# Stage 46 LangGraph Single-Writer, Lease Fencing, and Replay-Safe Effects Implementation Plan

> **Execution note:** Implement this plan task by task. Start every task with the
> stated failing test, run the focused gate before each commit, and keep both
> LangGraph rollout percentages at zero. Do not mutate an already registered
> State schema in place.

**Goal:** Make Durable Interview and Durable Review execution safe under
concurrent delivery, duplicate resume events, expired leases, process loss after
business writes, and non-deterministic provider replay, while preserving all
existing `langgraph-v1` and `langgraph-review-v1` checkpoints.

**Architecture:** PostgreSQL becomes the explicit execution-authority layer in
front of the existing shared LangGraph checkpointer. Every graph invocation is
guarded by a session-level advisory lock derived from a namespaced thread
identity. Generation attempts and Report Jobs use unique lease tokens plus
fencing predicates on every side-effecting write. Review retries return to the
Report Job claim path instead of invoking the graph directly. Question-review
and report-generation outputs are stored as immutable operation artifacts so a
node replay reuses the first committed effect rather than calling the provider
again or overwriting the result.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, LangGraph 1.2,
`langgraph-checkpoint-postgres` 3.1, PostgreSQL advisory locks, psycopg2 and
psycopg 3, pytest, Playwright, SSE, the existing runtime outbox, Report Job
store, Interview generation store, and privacy-safe canary tooling.

**Working-tree baseline:** The pre-Stage-46 optimization batch adds SQL cursor
pagination for SSE, bounded SSE reconnect, duplicate-index cleanup, an
expired-running Outbox index, batched Outbox lease heartbeat, shared runtime
failure classification, Review configuration wiring, conflict replay
idempotency, generation heartbeat throttling, and durable Report Worker error
containment. Its local full Python gate is `960 passed, 106 skipped`; PostgreSQL
markers remain an explicit Task 1 gate because the local baseline did not have a
reachable `POSTGRES_DSN`.

---

## Execution Preconditions

1. Commit or otherwise preserve the pre-Stage-46 optimization batch before
   beginning Task 1. Do not mix those edits with the first fencing commit.
2. Use an isolated PostgreSQL database or a unique
   `INTERVIEW_RUNTIME_TABLE_PREFIX`. Never run the first schema experiment
   against production tables.
3. Keep these committed defaults unchanged throughout the stage:

   ```text
   INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
   REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
   INTERVIEW_LANGGRAPH_RUNTIME_ENABLED=true
   REPORT_LANGGRAPH_RUNTIME_ENABLED=true
   LANGGRAPH_STRICT_MSGPACK=true
   ```

4. Existing `langgraph-v1` Interview threads and
   `langgraph-review-v1` Review threads must remain resumable after every task.
5. A schema addition must be backward compatible and idempotent. Removing or
   renaming a State channel requires a future graph version and is prohibited in
   this stage.
6. Deterministic tests use fake providers. Real providers are permitted only in
   separately authorized smoke tests and never in crash-recovery acceptance.
7. Committed test output and acceptance artifacts contain safe counters, stable
   error codes, durations, and short commit IDs only. They must not contain
   answers, resumes, report bodies, provider payloads, lease tokens, checkpoint
   IDs, DSNs, or raw thread IDs.

## Scope

This stage covers:

- closing the PostgreSQL verification gap for the preceding optimization batch;
- deriving privacy-safe, stable advisory-lock keys from namespaced workflow
  thread identities;
- serializing every Interview and Review graph invocation per thread;
- treating lock contention as retryable control flow rather than a domain
  conflict;
- rejecting public projection states that are unexpectedly ahead of the graph;
- adding unique generation lease tokens and monotonically increasing fencing
  versions;
- fencing generation chunk append, heartbeat, fail, abandon, and complete;
- detecting generation lease loss even when a provider emits no chunks;
- routing Review retry timers back through Report Job due/claim ownership;
- fencing Review side effects and final report commit with the active Report Job
  lease token;
- storing question-review and report-generation effects as write-once operation
  artifacts;
- reconciling Interview and Review work that lost the process before the first
  checkpoint;
- adding deterministic concurrency, lease-loss, effect-replay, and atomicity
  tests;
- extending preflight, canary status, acceptance documents, and the operator
  runbook without changing rollout.

## Non-Goals

- Do not register `langgraph-v2` or `langgraph-review-v2`.
- Do not remove or rename `next_batch_start` or any other existing State field.
- Do not externalize `DurableInterviewState.messages` in this stage.
- Do not replace `plan_snapshot` or `review_input_manifest` with references.
- Do not add question-level retry State or change the Review graph's public
  schema. A failed question may be classified more accurately, but independent
  question retry belongs to a future version.
- Do not add an application-wide PostgreSQL connection pool. Advisory-lock
  connections may use a small dedicated provider, but Store migration to a
  shared pool is a separate stage.
- Do not replace SSE with WebSocket or redesign the five-page UI.
- Do not merge Prep, Interview, and Review into one StateGraph.
- Do not migrate Legacy Sessions or Legacy Report Jobs.
- Do not delete completed checkpoint history or introduce checkpoint retention.
- Do not automatically change deployed environment variables or promote a
  rollout percentage above zero.
- Do not claim exactly-once provider execution. The contract is one logical,
  stable business effect under replay, implemented through write-once artifacts
  and idempotent reuse.

## Fixed Decisions

1. **PostgreSQL owns execution authority.** The LangGraph checkpointer persists
   graph progress; it is not used as a distributed mutex for business side
   effects.
2. **Locks are namespaced.** Canonical identities are
   `interview:{session_id}` and `review:{job_id}`. An Interview and Review with
   similar raw identifiers never contend.
3. **Lock keys are stable signed 64-bit values.** Derive the key from SHA-256 of
   the canonical identity and convert the first eight bytes to a signed bigint.
   Do not use Python's process-randomized `hash()`. PostgreSQL exposes either
   one `bigint` key or two `integer` keys; it has no two-`bigint` advisory-lock
   overload, and the two-integer form does not increase the total key space
   beyond 64 bits. The SHA-256 truncation risk is accepted for this runtime
   scale. A future zero-logical-collision requirement needs a unique
   identity-to-lock-ID registry table, not a different advisory-lock overload.
4. **Use session-level advisory locks.** Acquire with
   `pg_try_advisory_lock(bigint)` on a dedicated connection, hold no ordinary
   SQL transaction during provider work, and release in `finally`. Connection
   loss releases the lock automatically.
5. **Do not hold locks across interrupts.** One `graph.invoke()` owns the lock
   until it reaches an interrupt, terminal state, or exception; then it releases
   the lock. The next resume reacquires it.
6. **Lock contention is retryable.** Return the stable code
   `workflow_thread_busy`; do not mark the command conflict, failed, or applied.
7. **Projection divergence fails closed.** The only legal projection replay is
   the same next version with the same digest. A database version greater than
   the graph's next version raises `ProjectionConflict`.
8. **Worker identity and lease identity are different.** `lease_owner` identifies
   a worker instance; `lease_token` identifies one claim; `fencing_version`
   orders claims for one logical attempt.
9. **Every generation write is fenced.** Append, heartbeat, fail, abandon, and
   complete require the active token, fencing version, running status, and a
   non-expired lease where applicable.
10. **Lease loss is not provider failure.** Use `generation_lease_lost` or
    `report_lease_lost`; the old executor stops and does not mutate the new
    owner's attempt.
11. **Review timers schedule work; they do not own work.** A
    `review_retry_due` event makes the Report Job due. Only a Report Worker that
    claims a fresh lease may resume the graph.
12. **Final Review commit is lease-fenced and atomic.** Session, Report, Report
    Job, and Review Run updates succeed together under the active lease or none
    succeed.
13. **Provider effects are immutable by operation identity.** A stable operation
    key maps to one input digest and one output digest. A completed duplicate
    reuses the stored artifact. A different input or output for the same key
    fails closed. The thread lock is the primary concurrent-provider guard; an
    effect claim lease adds crash ownership and defense in depth before the
    artifact is completed.
14. **Raw effect payloads stay outside LangGraph State.** State stores references
    and digests only. Full feedback and report JSON remain in business artifact
    tables.
15. **Cold-start recovery is deterministic.** A durable shell/run with no
    checkpoint may rebuild the same initial graph input under the thread lock.
    A conflicting plan, manifest, graph version, or input digest is terminal.
16. **Rollout remains zero.** Repository completion may produce
    `READY_FOR_FENCING_CANARY`; it does not authorize production assignment.

## Target Execution Model

### Durable Interview

```text
Interview command/retry event
        |
        v
validate engine + event cursor
        |
        v
acquire advisory lock(interview:{session_id})
        |
        +-- busy --> retryable workflow_thread_busy
        |
        v
load checkpoint + validate command
        |
        v
claim generation attempt(token, fencing_version)
        |
        v
provider stream + independently heartbeated lease
        |
        v
append chunks with token/version predicates
        |
        v
fenced complete -> project state -> checkpoint/interrupt
        |
        v
release advisory lock
```

### Durable Review

```text
review_retry_due
        |
        v
mark Report Job retrying and due
        |
        v
Report Worker claim(token)
        |
        v
acquire advisory lock(review:{job_id})
        |
        +-- busy --> release/retry job without graph invoke
        |
        v
resume graph
        |
        v
reuse or create write-once question/report effects
        |
        v
validate active lease
        |
        v
atomic fenced final commit
        |
        v
release advisory lock
```

## File Map

| Area | Primary files |
| --- | --- |
| Thread locking | `app/services/workflow_thread_lock.py`, `app/services/runtime.py` |
| Interview invocation | `app/services/interview_workflow.py`, `app/services/interview_workflow_consumer.py` |
| Interview projection | `app/services/interview_workflow_store.py` |
| Generation fencing | `app/services/interview_generation_store.py`, `app/graphs/durable_interview_graph.py` |
| Review ownership | `app/services/review_workflow.py`, `app/services/review_workflow_consumer.py`, `app/services/report_jobs.py` |
| Review commit/effects | `app/services/review_workflow_store.py`, `app/graphs/durable_review_graph.py`, `app/services/runtime.py` |
| Shared failures | `app/services/runtime_work.py`, `app/services/runtime_domain_events.py` |
| Preflight/canary | `scripts/runtime_preflight.py`, `scripts/langgraph_canary.py`, `app/services/langgraph_canary_status.py` |
| Acceptance | `tests/test_workflow_thread_lock.py`, PostgreSQL marker tests, recovery tests, browser recovery tests |

---

## Task 1: Close the PostgreSQL Baseline Gate

**Files:**

- Modify: `tests/test_interview_generation_store.py`
- Modify: `tests/test_postgres_runtime_control.py`
- Modify: `tests/test_interview_event_stream.py`
- Modify: `tests/test_runtime_preflight.py`
- Create: `docs/langgraph-stage46-acceptance.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Create an isolated PostgreSQL baseline**

Use a dedicated database or prefix. Keep both rollouts at zero. Record only the
database major version, schema prefix category, named checks, pass counts, and
durations. Do not record the DSN or credentials.

- [ ] **Step 2: Add PostgreSQL tests for the optimization batch**

Cover:

```python
def test_generation_cursor_reads_only_rows_after_attempt_sequence(): ...
def test_generation_cursor_uses_primary_key_order_across_reset(): ...
def test_redundant_generation_indexes_are_absent(): ...
def test_outbox_has_partial_running_lease_index(): ...
def test_batch_heartbeat_extends_every_claimed_running_event(): ...
```

Use PostgreSQL catalog assertions for index definitions. Do not assert only an
index name; require the expected columns and partial predicate.

- [ ] **Step 3: Run query-plan evidence locally**

Run `EXPLAIN (ANALYZE, BUFFERS)` for the SSE tuple cursor and expired-running
Outbox claim using representative rows. Keep raw plans local. The committed
acceptance record stores only `used_expected_index: true/false` and row-count
buckets.

- [ ] **Step 4: Run the existing PostgreSQL recovery gates**

```powershell
python -m pytest -q -m pg_runtime
python -m pytest -q -m pg_control
python -m pytest -q -m langgraph_recovery
python -m pytest -q -m langgraph_review_recovery
python -m scripts.runtime_preflight
```

Stop Stage 46 if any existing recovery test fails. Do not compensate by
weakening assertions or raising retry limits.

- [ ] **Step 5: Create the pending Stage 46 acceptance record**

Begin with:

```text
Status: PENDING_IMPLEMENTATION
```

Add empty sections for baseline, thread lock, generation fencing, Review
fencing, effect replay, cold-start recovery, privacy, full regression, and
release decision.

- [ ] **Step 6: Verify and commit**

```powershell
python -m pytest -q tests/test_interview_generation_store.py tests/test_postgres_runtime_control.py tests/test_interview_event_stream.py tests/test_runtime_preflight.py tests/test_local_v1_docs.py
git diff --check
git add tests/test_interview_generation_store.py tests/test_postgres_runtime_control.py tests/test_interview_event_stream.py tests/test_runtime_preflight.py docs/langgraph-stage46-acceptance.md tests/test_local_v1_docs.py
git commit -m "test: close stage 46 postgres baseline"
```

---

## Task 2: Define the Single-Writer and Fencing Domain Contract

**Files:**

- Create: `app/services/workflow_thread_lock.py`
- Modify: `app/services/runtime_work.py`
- Create: `tests/test_workflow_thread_lock.py`
- Modify: `tests/test_runtime_work.py`

- [ ] **Step 1: Write failing identity and classification tests**

Require:

```python
def test_lock_key_is_stable_across_processes(): ...
def test_interview_and_review_namespaces_do_not_collide(): ...
def test_python_hash_is_not_used(): ...
def test_thread_busy_is_retryable_control_failure(): ...
def test_generation_and_report_lease_loss_have_distinct_codes(): ...
```

Test fixed input/output vectors for key derivation so a future refactor cannot
silently change the lock identity of existing threads.

- [ ] **Step 2: Add explicit domain errors**

Define:

```python
class WorkflowThreadBusy(RuntimeError): ...
class WorkflowThreadLockLost(RuntimeError): ...
class GenerationLeaseLost(RuntimeError): ...
class ReportLeaseLost(RuntimeError): ...
class FencedWriteRejected(RuntimeError): ...
```

Errors carry safe workflow metadata only. They do not embed the raw session ID,
job ID, lease token, payload, or database message.

- [ ] **Step 3: Implement canonical identity and key derivation**

Provide:

```python
def interview_thread_identity(session_id: str) -> str: ...
def review_thread_identity(job_id: str) -> str: ...
def advisory_lock_key(identity: str) -> int: ...
```

Validate non-empty identifiers and convert the first eight SHA-256 bytes to a
signed big-endian integer.

- [ ] **Step 4: Extend the shared failure classifier**

Map:

```text
WorkflowThreadBusy      -> workflow_thread_busy, retryable
GenerationLeaseLost     -> generation_lease_lost, retryable
ReportLeaseLost         -> report_lease_lost, retryable
FencedWriteRejected     -> fenced_write_rejected, non-retryable alert
WorkflowThreadLockLost  -> workflow_thread_lock_lost, retryable
```

Do not collapse these into `database_unavailable` or `provider_unavailable`.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest -q tests/test_workflow_thread_lock.py tests/test_runtime_work.py
git diff --check
git add app/services/workflow_thread_lock.py app/services/runtime_work.py tests/test_workflow_thread_lock.py tests/test_runtime_work.py
git commit -m "feat: define workflow fencing contracts"
```

---

## Task 3: Implement the PostgreSQL Advisory Lock Runtime

**Files:**

- Modify: `app/services/workflow_thread_lock.py`
- Modify: `app/services/runtime.py`
- Create: `tests/test_workflow_thread_lock_postgres.py`
- Modify: `tests/test_runtime_lifecycle.py`
- Modify: `pytest.ini`

- [ ] **Step 1: Add a PostgreSQL concurrency marker and failing tests**

Register `langgraph_single_writer`. With two independent connections, prove:

```python
def test_same_thread_has_one_lock_owner(): ...
def test_different_threads_do_not_block_each_other(): ...
def test_connection_close_releases_lock(): ...
def test_exception_releases_lock(): ...
def test_lock_timeout_does_not_open_a_graph_transaction(): ...
def test_lock_contention_uses_bounded_backoff_not_busy_spin(): ...
```

- [ ] **Step 2: Implement `PostgresWorkflowThreadLock`**

The context manager:

1. obtains a dedicated connection;
2. enables autocommit;
3. calls `pg_try_advisory_lock` until acquired or the bounded timeout expires,
   with bounded sleep/backoff and jitter rather than a tight CPU spin;
4. yields a safe ownership object;
5. calls `pg_advisory_unlock` in `finally` when the connection is still valid;
6. closes or returns the connection;
7. raises `WorkflowThreadBusy` without exposing the raw key.

Do not hold a SQL transaction open around provider work.

Blocking `pg_advisory_lock` with a local `lock_timeout` is acceptable only if
tests prove timeout cleanup, shutdown responsiveness, and connection reset. Do
not mix blocking and polling acquisition policies across workflow call paths.

- [ ] **Step 3: Add runtime lifecycle ownership**

Build one lock service per PostgreSQL runtime and inject it into Interview and
Review workflow services. Runtime shutdown prevents new acquisition and waits
only for bounded local cleanup; PostgreSQL connection loss remains the ultimate
lock release.

- [ ] **Step 4: Add safe metrics hooks**

Record acquisition outcome and wait duration by workflow type. Do not record
lock key, thread ID, session ID, or job ID in canary output.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest -q tests/test_workflow_thread_lock.py tests/test_workflow_thread_lock_postgres.py tests/test_runtime_lifecycle.py
git diff --check
git add app/services/workflow_thread_lock.py app/services/runtime.py tests/test_workflow_thread_lock_postgres.py tests/test_runtime_lifecycle.py pytest.ini
git commit -m "feat: add postgres workflow thread locks"
```

---

## Task 4: Serialize Every Durable Interview Invocation

**Files:**

- Modify: `app/services/interview_workflow.py`
- Modify: `app/services/interview_workflow_consumer.py`
- Modify: `app/services/runtime_outbox_dispatcher.py`
- Modify: `app/services/interview_workflow_tasks.py`
- Modify: `tests/test_interview_workflow_consumer.py`
- Modify: `tests/test_dual_langgraph_rollout.py`
- Create: `tests/test_interview_single_writer_postgres.py`

- [ ] **Step 1: Write failing invocation-ownership tests**

Cover initial graph bootstrap, command resume, and retry resume. Two workers that
receive events for one Session must produce:

```text
graph invocation count = 1 at a time
provider invocation count = 1 per logical operation
second event outcome = retryable workflow_thread_busy or stale
command status is not changed by lock contention
```

- [ ] **Step 2: Add one locked invocation helper**

Create a private workflow method that is the only production path to
`graph.invoke()`:

```python
def _invoke_locked(self, session_id, graph_input, *, reason): ...
```

It derives `interview:{session_id}`, acquires the advisory lock, resolves the
immutable graph version after lock acquisition, invokes the graph, and releases
the lock in `finally`.

- [ ] **Step 3: Route command and timer consumers through the helper**

Keep the existing stale-retry precheck, then reacquire and recheck after the
lock is held. This closes the time-of-check/time-of-use gap. Lock contention
must raise a retryable error to the Outbox dispatcher instead of returning
`completed`.

- [ ] **Step 4: Lock initial bootstrap**

The durable shell insert remains outside the graph. The first graph invocation
uses the same lock as later resumes. A duplicate bootstrap reads the checkpoint
under lock and becomes a no-op rather than creating another initial projection.

- [ ] **Step 5: Prove interrupt release**

After `wait_for_answer` or `wait_for_retry`, a second invocation must be able to
acquire the lock. Never retain an advisory-lock connection in State or in an
interrupt payload.

- [ ] **Step 6: Verify and commit**

```powershell
python -m pytest -q tests/test_interview_workflow_consumer.py tests/test_dual_langgraph_rollout.py tests/test_interview_single_writer_postgres.py
git diff --check
git add app/services/interview_workflow.py app/services/interview_workflow_consumer.py app/services/runtime_outbox_dispatcher.py app/services/interview_workflow_tasks.py tests/test_interview_workflow_consumer.py tests/test_dual_langgraph_rollout.py tests/test_interview_single_writer_postgres.py
git commit -m "feat: serialize durable interview threads"
```

---

## Task 5: Fail Closed on Projection Divergence

**Files:**

- Modify: `app/services/interview_workflow_store.py`
- Modify: `tests/test_interview_workflow_store.py`
- Modify: `tests/test_langgraph_recovery_postgres.py`
- Modify: `app/services/langgraph_canary_status.py`

- [ ] **Step 1: Write the complete projection-version matrix**

Test:

| Database version | Graph input version | Expected result |
| ---: | ---: | --- |
| N | N | commit N+1 |
| N+1 with same digest | N | idempotently reuse N+1 |
| N+1 with different digest | N | `ProjectionConflict` |
| N+2 or greater | N | `ProjectionConflict` |
| less than N | N | `ProjectionConflict` |

Add a deterministic two-worker boundary test rather than relying only on the
table-driven cases:

1. worker A projects version N+1;
2. worker B replays graph input version N;
3. the same projection digest idempotently reuses N+1;
4. a different projection digest raises `ProjectionConflict`.

Task 4's thread lock should make the divergent branch unreachable during
ordinary production execution. This task remains an independent fail-closed
defence against lock regressions, legacy callers, manual repair mistakes, and
data written before the single-writer invariant existed.

- [ ] **Step 2: Remove silent fast-forward**

Delete the branch that returns the current database version when it is greater
than the graph's next version. Never mutate only `state_version` while keeping
stale messages or generation control fields.

- [ ] **Step 3: Preserve the legal crash-replay window**

The same next version with the same projection digest remains idempotent. The
test that injects process loss after the projection write but before the graph
checkpoint must still recover.

- [ ] **Step 4: Add privacy-safe conflict metrics**

Increment a projection-divergence counter by workflow version. Do not export
session identity, digest, message count, or checkpoint identity.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest -q tests/test_interview_workflow_store.py tests/test_langgraph_recovery_postgres.py tests/test_langgraph_canary_status.py
git diff --check
git add app/services/interview_workflow_store.py app/services/langgraph_canary_status.py tests/test_interview_workflow_store.py tests/test_langgraph_recovery_postgres.py tests/test_langgraph_canary_status.py
git commit -m "fix: reject interview projection divergence"
```

---

## Task 6: Fence Generation Attempts with Unique Lease Tokens

**Files:**

- Modify: `app/services/interview_generation_store.py`
- Modify: `app/graphs/durable_interview_graph.py`
- Modify: `app/services/interview_event_stream.py`
- Modify: `tests/test_interview_generation_store.py`
- Modify: `tests/test_durable_interview_graph.py`
- Modify: `tests/test_langgraph_recovery_postgres.py`
- Modify: `scripts/runtime_preflight.py`

- [ ] **Step 1: Write failing token and fencing tests**

Require:

```python
def test_claim_returns_unique_lease_token(): ...
def test_same_worker_cannot_reuse_an_active_claim_without_token(): ...
def test_reclaim_increments_fencing_version(): ...
def test_old_token_cannot_append_chunk(): ...
def test_old_token_cannot_heartbeat(): ...
def test_old_token_cannot_fail_or_abandon(): ...
def test_old_token_cannot_complete(): ...
```

- [ ] **Step 2: Add backward-compatible columns**

Add to generation attempts:

```sql
lease_token UUID,
fencing_version BIGINT NOT NULL DEFAULT 0
```

Existing non-running rows may retain a null token. A running row created after
the migration requires a token. Add a partial index only if query-plan evidence
shows it is required; do not duplicate the attempt primary key.

- [ ] **Step 3: Return a fenced claim object**

Extend `GenerationAttempt` with the token and fencing version. A fresh claim
uses a new UUID. Reclaim abandons the expired row or advances to the next legal
attempt according to existing retry bounds, and increments the fencing version
under row lock.

- [ ] **Step 4: Fence every mutating method**

All mutations include token and version predicates. `append_chunk` keeps its
existing `(generation_id, attempt_number, sequence)` idempotency check, but an
old owner may not use that idempotency path to read or validate a new owner's
chunk.

- [ ] **Step 5: Update the graph and stream surfaces**

Keep the lease token in runtime-local execution data only. Do not add it to
`DurableInterviewState`, SSE events, snapshots, logs, Agent metadata, or
acceptance artifacts. The graph receives it from the attempt claim and passes it
directly to Store calls within the node invocation.

- [ ] **Step 6: Add preflight schema checks**

Require the columns and constraints without printing token values. Existing v1
checkpoint State remains unchanged.

- [ ] **Step 7: Verify and commit**

```powershell
python -m pytest -q tests/test_interview_generation_store.py tests/test_durable_interview_graph.py tests/test_langgraph_recovery_postgres.py tests/test_runtime_preflight.py
git diff --check
git add app/services/interview_generation_store.py app/graphs/durable_interview_graph.py app/services/interview_event_stream.py scripts/runtime_preflight.py tests/test_interview_generation_store.py tests/test_durable_interview_graph.py tests/test_langgraph_recovery_postgres.py tests/test_runtime_preflight.py
git commit -m "feat: fence interview generation attempts"
```

---

## Task 7: Detect Generation Lease Loss During Silent Provider Work

**Files:**

- Modify: `app/graphs/durable_interview_graph.py`
- Modify: `app/services/interview_generation_store.py`
- Modify: `app/services/runtime_work.py`
- Modify: `tests/test_durable_interview_graph.py`
- Modify: `tests/test_interview_generation_store.py`
- Modify: `tests/test_langgraph_recovery_postgres.py`

- [ ] **Step 1: Write timing and cancellation tests**

Use an injected clock and blocking fake provider to prove:

```python
def test_silent_provider_is_heartbeated_at_lease_third(): ...
def test_frequent_chunks_do_not_create_frequent_heartbeats(): ...
def test_failed_heartbeat_stops_future_chunk_writes(): ...
def test_lease_loss_prevents_complete(): ...
def test_old_executor_does_not_fail_the_new_claim(): ...
```

- [ ] **Step 2: Add an invocation-local heartbeat controller**

Use one bounded background heartbeat per active generation invocation. It owns
only the token, fencing version, stop event, and lease-lost event. It does not
own graph State or provider content.

- [ ] **Step 3: Add cooperative lease checks**

Check the lease-lost flag before each persisted chunk, before final flush, and
before complete. A provider API that supports cancellation may receive a stop
token; otherwise discard late provider output after lease loss.

- [ ] **Step 4: Preserve error ownership**

On lease loss, do not call `fail_attempt` with the old token. Raise
`GenerationLeaseLost` so the valid owner or later retry controls the row.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest -q tests/test_durable_interview_graph.py tests/test_interview_generation_store.py tests/test_langgraph_recovery_postgres.py tests/test_runtime_work.py
git diff --check
git add app/graphs/durable_interview_graph.py app/services/interview_generation_store.py app/services/runtime_work.py tests/test_durable_interview_graph.py tests/test_interview_generation_store.py tests/test_langgraph_recovery_postgres.py tests/test_runtime_work.py
git commit -m "feat: stop interview work after lease loss"
```

---

## Task 8: Return Durable Review Retries to Report Job Claiming

**Files:**

- Modify: `app/services/review_workflow_consumer.py`
- Modify: `app/services/review_workflow.py`
- Modify: `app/services/review_workflow_store.py`
- Modify: `app/services/report_jobs.py`
- Modify: `app/services/report_worker.py`
- Modify: `app/services/runtime_outbox_dispatcher.py`
- Create: `app/services/review_workflow_tasks.py`
- Modify: `app/services/celery_app.py`
- Modify: `tests/test_review_workflow_consumer.py`
- Modify: `tests/test_review_workflow.py`
- Modify: `tests/test_report_jobs.py`
- Modify: `tests/test_report_worker.py`

- [ ] **Step 1: Write failing timer/claim ownership tests**

Require:

```python
def test_review_retry_consumer_never_invokes_graph_directly(): ...
def test_retry_due_atomically_makes_job_claimable(): ...
def test_retry_before_due_is_discarded(): ...
def test_duplicate_retry_due_is_idempotent(): ...
def test_only_one_worker_claims_due_review_job(): ...
```

- [ ] **Step 2: Add database due time to Report Jobs**

Reuse an existing due column if semantically correct; otherwise add
`available_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`. Update `claim_next` so queued
or retrying work is claimable only when database time reaches the due value.
Persist the event's graph retry number in a dedicated nullable
`scheduled_attempt` field during the same scheduling transaction. Do not
overload the Report Job worker `attempt_count`; the two counters have different
lifecycles. Update `claim_next` to return `scheduled_attempt`, and index the claim
predicate without duplicating an existing index.

- [ ] **Step 3: Change timer-consumer responsibility**

The consumer validates engine, graph cursor, generation/report attempt, and
event identity, then marks the job due. It returns a stable scheduling outcome.
It does not obtain graph State after scheduling and does not call
`Command(resume=...)`.

- [ ] **Step 4: Resume under the Report Worker lease**

`run_claimed_job` inspects the graph snapshot under the Review thread lock. If
the next node is `wait_for_retry` and the expected attempt matches the claimed
job, it invokes `Command(resume=...)`; otherwise it starts, continues, or
discards according to the existing immutable assignment.

Make the three invocation branches explicit:

```python
snapshot = graph.get_state(config)
if snapshot.next == ("wait_for_retry",):
    expected = snapshot.values["expected_retry_attempt"]
    assert expected == claimed_job["scheduled_attempt"]
    result = graph.invoke(
        Command(resume={"next_attempt_number": expected}),
        config=config,
    )
elif snapshot.values:
    result = graph.invoke(None, config=config)
else:
    result = graph.invoke(initial_state, config=config)
```

`graph.invoke(None, ...)` must never be used to resume `wait_for_retry`. The
interrupt resume payload is what populates `retry_resume_attempt`; omitting it
would make a valid retry appear stale.

The `assert` above documents the branch invariant only. Production code must
perform an explicit comparison and return a stable stale/conflict outcome when
the values differ; correctness must not depend on Python assertions being
enabled.

If a worker owns the Report Job lease but cannot acquire the Review thread lock,
atomically return the job to `retrying`, set a short database-clock
`available_at`, and clear `lease_owner`, `lease_token`, and `lease_expires_at`.
Exit without waiting for the existing lease to expire. Fence this release with
the current lease token so a stale worker cannot reschedule a newer owner.

- [ ] **Step 5: Route Celery retry events correctly**

Add or update the dedicated Review workflow task. A `review_retry_due` event
must never be routed to the one-question round-review task.

- [ ] **Step 6: Verify and commit**

```powershell
python -m pytest -q tests/test_review_workflow_consumer.py tests/test_review_workflow.py tests/test_report_jobs.py tests/test_report_worker.py tests/test_event_publisher.py
git diff --check
git add app/services/review_workflow_consumer.py app/services/review_workflow.py app/services/review_workflow_store.py app/services/report_jobs.py app/services/report_worker.py app/services/runtime_outbox_dispatcher.py app/services/review_workflow_tasks.py app/services/celery_app.py tests/test_review_workflow_consumer.py tests/test_review_workflow.py tests/test_report_jobs.py tests/test_report_worker.py tests/test_event_publisher.py
git commit -m "feat: resume durable review through job claims"
```

---

## Task 9: Fence Review Execution and Atomic Final Commit

**Files:**

- Modify: `app/services/review_workflow.py`
- Modify: `app/services/review_workflow_store.py`
- Modify: `app/services/report_jobs.py`
- Modify: `app/services/runtime.py`
- Modify: `app/graphs/durable_review_graph.py`
- Modify: `tests/test_review_workflow.py`
- Modify: `tests/test_review_workflow_store.py`
- Create: `tests/test_review_fencing_postgres.py`
- Modify: `pytest.ini`

- [ ] **Step 1: Register `langgraph_fencing` and write failing tests**

Cover:

```python
def test_review_graph_requires_thread_lock_and_job_lease(): ...
def test_lease_loss_after_generation_prevents_commit(): ...
def test_old_worker_cannot_commit_after_reclaim(): ...
def test_identical_fenced_commit_is_idempotent(): ...
def test_different_digest_fails_closed(): ...
def test_partial_final_commit_rolls_back_all_tables(): ...
```

- [ ] **Step 2: Serialize Review graph invocation**

Use `review:{job_id}` with the advisory lock runtime. Acquire the job lease
first, then the thread lock. Keep this global acquisition order in every code
path to avoid deadlocks.

- [ ] **Step 3: Require lease context at side-effect boundaries**

Pass a runtime-only Review execution context containing worker ID and lease
token through LangGraph runtime context or dependency-bound invocation context,
not through `DurableReviewState`.

- [ ] **Step 4: Fence final commit**

Within one transaction:

1. lock the Report Job and Review Run;
2. verify engine, graph version, input digest, running status, owner, token, and
   unexpired lease;
3. load the immutable report artifact by ref and expected digest;
4. update Session review phase and public version;
5. update Report JSON/status;
6. mark Report Job completed and clear lease;
7. mark Review Run completed with result digest;
8. commit.

Any failed predicate raises `ReportLeaseLost`, `ReportCommitConflict`, or a
stable validation error and leaves all four projections unchanged.

- [ ] **Step 5: Recheck ownership after graph execution**

Call `heartbeat.ensure_owned()` after `graph.invoke()` returns and before any
workflow-level terminal action. The graph's final commit remains independently
fenced in SQL.

- [ ] **Step 6: Verify and commit**

```powershell
python -m pytest -q tests/test_review_workflow.py tests/test_review_workflow_store.py tests/test_review_fencing_postgres.py
git diff --check
git add app/services/review_workflow.py app/services/review_workflow_store.py app/services/report_jobs.py app/services/runtime.py app/graphs/durable_review_graph.py tests/test_review_workflow.py tests/test_review_workflow_store.py tests/test_review_fencing_postgres.py pytest.ini
git commit -m "feat: fence durable review commits"
```

---

## Task 10: Store Review Provider Effects as Write-Once Artifacts

**Files:**

- Modify: `app/services/review_workflow_store.py`
- Modify: `app/services/question_evaluations.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/services/runtime.py`
- Modify: `app/graphs/durable_review_graph.py`
- Modify: `tests/test_review_workflow_store.py`
- Modify: `tests/test_durable_review_recovery_postgres.py`
- Create: `tests/test_review_effect_replay_postgres.py`
- Modify: `pytest.ini`

- [ ] **Step 1: Register `langgraph_effect_replay` and write crash tests**

Use non-deterministic fake providers. First execution returns artifact A and
fails after the business write but before the node checkpoint. Recovery would
return B if called. Assert:

```text
provider_call_count = 1
artifact_insert_count = 1
stored output digest = digest(A)
final committed digest derives from A
```

Cover question evaluation, initial report generation, and each quality repair
operation.

Add a separate injected loss after the provider returns A but before the
completed artifact transaction commits. This window cannot promise exactly-once
external provider invocation: recovery may call the provider again. It must
still prove that only one logical effect reaches `completed`, no completed
artifact is overwritten, and all later projections derive from the winning
stored effect.

- [ ] **Step 2: Define stable operation keys**

Use:

```text
review-question:{job_id}:{question_id}:{question_input_sha256}:{attempt}
report-generation:{job_id}:{review_input_sha256}:{provider_attempt}:{repair_count}
```

Hash the canonical key for indexing if necessary, while retaining enough
structured columns for integrity checks. Do not derive identity from output.

- [ ] **Step 3: Add leased effect claims and immutable completed storage**

Create an explicit effect table or evolve the artifact schema so one operation
stores:

```text
operation_key primary key
job_id/session_id
effect_type
question_id nullable
graph_schema_version
input_sha256
output_sha256
artifact payload/reference
status = running | completed | failed
claim_owner
claim_token
claim_expires_at
fencing_version
created_at/completed_at
```

The table may store business payload JSON, but diagnostics and checkpoint State
must expose only refs and digests.

- [ ] **Step 4: Implement claim-before-provider and completed-effect reuse**

Execution algorithm:

1. read the operation;
2. if it is `completed` and input/version match, return the stored artifact;
3. if it is `running` under an unexpired foreign claim, do not call the
   provider and return the stable retryable outcome `review_effect_busy`;
4. if absent, insert a `running` claim with `ON CONFLICT DO NOTHING`;
5. if another writer wins that insert, reread and follow the completed, busy,
   or conflict path;
6. if a `running` claim is expired, reclaim it with a fresh token and incremented
   fencing version using database time and a compare-and-swap predicate;
7. only the code path holding the active claim token and fencing version may
   call the provider; heartbeat the claim independently during silent provider
   work, and surface claim loss cooperatively;
8. transition to `completed` with a compare-and-swap predicate over `running`
   status, claim token, fencing version, and lease validity; discard an output
   that returns after ownership was lost;
9. if a completed row has a different immutable input, graph version, or output
   digest than the operation being materialized, raise `ReviewEffectConflict`.

The Review thread lock is the primary guard against concurrent provider calls.
The effect claim lease is defence in depth and supplies crash ownership before
completion. Never use `ON CONFLICT DO UPDATE` to replace a completed effect.

Do not claim exactly-once provider invocation. A process can die after the
external provider returns but before PostgreSQL persists the completed artifact;
the reclaimed operation may therefore call the provider again. The promised
invariant is one immutable completed business effect and replay-safe projection,
not exactly one external side effect.

- [ ] **Step 5: Make question projection consume immutable effects**

Question evaluation projection may be idempotently materialized from the
write-once effect. A repeated projection with the same operation/output digest
is allowed. A different output for the same input fails closed.

- [ ] **Step 6: Strengthen existing recovery tests**

Replace set-only uniqueness assertions with explicit call counters. Preserve
the existing digest and privacy assertions.

- [ ] **Step 7: Verify and commit**

```powershell
python -m pytest -q tests/test_review_workflow_store.py tests/test_durable_review_recovery_postgres.py tests/test_review_effect_replay_postgres.py
git diff --check
git add app/services/review_workflow_store.py app/services/question_evaluations.py app/services/postgres_session.py app/services/runtime.py app/graphs/durable_review_graph.py tests/test_review_workflow_store.py tests/test_durable_review_recovery_postgres.py tests/test_review_effect_replay_postgres.py pytest.ini
git commit -m "feat: persist replay-safe review effects"
```

---

## Task 11: Reconcile Work Lost Before the First Checkpoint

**Files:**

- Modify: `app/services/interview_workflow.py`
- Modify: `app/services/interview_workflow_store.py`
- Modify: `app/services/review_workflow.py`
- Modify: `app/services/review_workflow_store.py`
- Modify: `scripts/runtime_recovery.py`
- Create: `tests/test_langgraph_cold_start_postgres.py`
- Modify: `tests/test_dual_langgraph_canary_postgres.py`

- [ ] **Step 1: Write the cold-start fault matrix**

Inject loss after:

```text
Interview durable shell insert
Interview graph input preparation
Interview first business projection
Review Job claim
Review Run initialize
Review graph input preparation
```

In each case the next valid owner must either rebuild one deterministic initial
checkpoint or fail closed on an immutable-input conflict.

- [ ] **Step 2: Add `ensure_interview_bootstrapped`**

Under the Interview thread lock:

1. load the immutable durable shell;
2. resolve its registered graph version;
3. canonicalize the stored plan together with the graph version and compute a
   deterministic `bootstrap_input_sha256`;
4. persist that digest in an existing immutable control row, or add a
   backward-compatible nullable column that is populated before the first graph
   invocation;
5. inspect the checkpoint;
6. if no checkpoint, the public version is zero, and the stored bootstrap
   digest and graph version match, rebuild the initial graph input and invoke
   once;
7. if a checkpoint exists and its bootstrap identity matches, do nothing;
8. if the bootstrap digest, graph version, or public version conflicts, raise a
   stable recovery error.

Use the same canonical serialization rules as other persisted digests. Do not
derive the bootstrap decision by comparing mutable deserialized plan or State
objects.

- [ ] **Step 3: Consolidate Review initialization**

Keep `initialize_run` idempotent, but define one bootstrap owner. Remove the
unexplained double initialization path only after tests prove process loss
before the first checkpoint remains recoverable.

- [ ] **Step 4: Add an operator recovery command**

The command accepts a workflow type and opaque identifier, obtains the same
thread lock, performs metadata-only diagnosis, and invokes deterministic
bootstrap when legal. It never prints raw state or provider content.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest -q tests/test_langgraph_cold_start_postgres.py tests/test_dual_langgraph_canary_postgres.py tests/test_langgraph_recovery_postgres.py tests/test_durable_review_recovery_postgres.py
git diff --check
git add app/services/interview_workflow.py app/services/interview_workflow_store.py app/services/review_workflow.py app/services/review_workflow_store.py scripts/runtime_recovery.py tests/test_langgraph_cold_start_postgres.py tests/test_dual_langgraph_canary_postgres.py
git commit -m "feat: reconcile langgraph cold starts"
```

---

## Task 12: Build the Combined Concurrency and Fault Matrix

**Files:**

- Create: `tests/test_langgraph_single_writer_postgres.py`
- Create: `tests/test_langgraph_fencing_postgres.py`
- Create: `tests/test_langgraph_effect_replay_postgres.py`
- Modify: `scripts/langgraph_dual_workflow_acceptance.py`
- Create: `scripts/langgraph_fencing_acceptance.py`
- Modify: `tests/test_langgraph_dual_workflow_acceptance.py`
- Create: `tests/test_langgraph_fencing_acceptance.py`

- [ ] **Step 1: Define the versioned acceptance check set**

Add a stable schema version such as:

```text
langgraph-fencing-acceptance-v1
```

The runner invokes focused unit tests, PostgreSQL single-writer tests,
generation fencing, Review fencing, effect replay, cold-start recovery,
preflight, privacy audit, and focused legacy compatibility.

- [ ] **Step 2: Implement deterministic concurrency barriers**

Use barriers/events so tests prove real overlap rather than hoping two fast
threads race. Provider fakes expose call counters and block until both workers
reach the intended fault point.

- [ ] **Step 3: Cover the matrix**

| Workflow | Fault or race | Required assertion |
| --- | --- | --- |
| Interview | Two command resumes | One active graph invocation |
| Interview | Command and retry overlap | Only legal cursor advances |
| Interview | Old generation token writes | Every write rejected |
| Interview | Projection ahead | Fail closed, no generation |
| Review | Two workers claim due retry | One fresh job lease |
| Review | Lease lost after generation | Old commit rejected |
| Review | Completed question effect then checkpoint loss | Provider call count remains one |
| Review | Provider return before effect commit then loss | Provider may repeat; one completed effect wins |
| Review | Report effect then loss | Original digest reused |
| Both | Different threads overlap | No global serialization |
| Both | Same raw ID, different namespace | No lock collision |

- [ ] **Step 4: Sanitize runner output**

Committed JSON and Markdown contain check name, status, duration, safe counts,
and short commit ID only. Add adversarial privacy tests for lease tokens, raw
thread IDs, checkpoint IDs, answers, reports, provider text, and DSNs.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest -q tests/test_langgraph_fencing_acceptance.py tests/test_langgraph_dual_workflow_acceptance.py
python -m scripts.langgraph_fencing_acceptance
git diff --check
git add tests/test_langgraph_single_writer_postgres.py tests/test_langgraph_fencing_postgres.py tests/test_langgraph_effect_replay_postgres.py scripts/langgraph_dual_workflow_acceptance.py scripts/langgraph_fencing_acceptance.py tests/test_langgraph_dual_workflow_acceptance.py tests/test_langgraph_fencing_acceptance.py
git commit -m "test: add langgraph fencing acceptance"
```

---

## Task 13: Extend Preflight, Canary, and Operator Documentation

**Files:**

- Modify: `scripts/runtime_preflight.py`
- Modify: `scripts/langgraph_canary.py`
- Modify: `app/services/langgraph_canary_status.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `docs/langgraph-interview-recovery-acceptance.md`
- Modify: `docs/langgraph-durable-review-acceptance.md`
- Modify: `docs/langgraph-dual-workflow-canary-acceptance.md`
- Modify: `docs/langgraph-stage46-acceptance.md`
- Modify: `tests/test_runtime_preflight.py`
- Modify: `tests/test_langgraph_canary_status.py`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Add preflight requirements**

Validate:

- advisory-lock capability;
- thread-lock runtime registration;
- generation lease token and fencing columns;
- Report Job due-time and lease-token columns/indexes;
- write-once effect table and unique operation constraint;
- both graph versions still registered;
- strict msgpack and PostgreSQL runtime;
- both rollout percentages remain within safe bounds;
- no positive rollout is accepted before Stage 46 repository status is ready.

- [ ] **Step 2: Add privacy-safe canary signals**

Expose aggregate counts/rates only:

```text
workflow_thread_busy_count
workflow_lock_wait_p95_ms
generation_lease_lost_count
generation_fencing_rejection_count
report_lease_lost_count
report_fencing_rejection_count
review_effect_reuse_count
review_effect_conflict_count
cold_start_reconciliation_count
projection_divergence_count
```

Counters use database time windows and stable error codes. They never enumerate
threads or artifacts.

- [ ] **Step 3: Document deployment and rollback**

The runbook includes:

1. isolated schema migration;
2. PostgreSQL marker tests;
3. preflight;
4. focused acceptance;
5. full regression;
6. runtime deployment with rollout zero;
7. observation of lock/fencing counters;
8. assignment-only rollback rules;
9. explicit statement that Stage 46 does not itself authorize 1% rollout.

- [ ] **Step 4: Update acceptance status only from evidence**

Move `docs/langgraph-stage46-acceptance.md` to
`READY_FOR_FENCING_CANARY` only after every repository gate passes. Do not alter
the historical PASS claims in older acceptance documents without rerunning their
named regressions.

- [ ] **Step 5: Verify and commit**

```powershell
python -m pytest -q tests/test_runtime_preflight.py tests/test_langgraph_canary_status.py tests/test_local_v1_docs.py
python -m scripts.runtime_preflight
git diff --check
git add scripts/runtime_preflight.py scripts/langgraph_canary.py app/services/langgraph_canary_status.py .env.example README.md docs/local-v1-runbook.md docs/langgraph-interview-recovery-acceptance.md docs/langgraph-durable-review-acceptance.md docs/langgraph-dual-workflow-canary-acceptance.md docs/langgraph-stage46-acceptance.md tests/test_runtime_preflight.py tests/test_langgraph_canary_status.py tests/test_local_v1_docs.py
git commit -m "docs: add langgraph fencing operations"
```

---

## Task 14: Run Final Repository Gates

**Files:**

- Modify: `docs/langgraph-stage46-acceptance.md`
- Modify: `reports/` only through an approved sanitized acceptance runner

- [ ] **Step 1: Run focused unit and compatibility gates**

```powershell
python -m pytest -q tests/test_workflow_thread_lock.py tests/test_runtime_work.py tests/test_interview_workflow_consumer.py tests/test_review_workflow_consumer.py tests/test_interview_generation_store.py tests/test_review_workflow_store.py tests/test_report_worker.py
```

- [ ] **Step 2: Run PostgreSQL single-writer and fencing gates**

```powershell
python -m pytest -q -m langgraph_single_writer
python -m pytest -q -m langgraph_fencing
python -m pytest -q -m langgraph_effect_replay
python -m pytest -q -m langgraph_recovery
python -m pytest -q -m langgraph_review_recovery
```

- [ ] **Step 3: Run combined acceptance and preflight**

```powershell
python -m scripts.runtime_preflight
python -m scripts.langgraph_fencing_acceptance
python -m scripts.langgraph_dual_workflow_acceptance
python -m scripts.langgraph_canary snapshot --window-minutes 60
```

The canary command is read-only and runs with rollout zero.

- [ ] **Step 4: Run full repository regression**

```powershell
python -m pytest -q
npm test
npm run build:prototype-css
npm run test:browser
git diff --check
```

Record counts and durations without raw subprocess output.

- [ ] **Step 5: Update the Stage 46 acceptance record**

If every gate passes, set:

```text
Status: READY_FOR_FENCING_CANARY
```

Do not mark `PASS`; no deployed canary is authorized or observed in this plan.
If any correctness, privacy, or atomicity invariant fails, leave the status
pending and keep rollout zero.

- [ ] **Step 6: Verify and commit**

```powershell
git diff --check
git add docs/langgraph-stage46-acceptance.md
git commit -m "docs: record stage 46 repository gates"
```

---

## Stop-Gate Checklist

Any checked item below blocks repository readiness and keeps both rollout
percentages at zero:

- [ ] Two executors enter the same Interview or Review thread concurrently.
- [ ] Lock contention marks a valid command conflict, applied, or failed.
- [ ] An advisory lock remains held after interrupt, exception, or connection
      close.
- [ ] Different workflow threads are globally serialized.
- [ ] Interview and Review identities with similar raw IDs contend.
- [ ] A stale generation token appends, heartbeats, fails, abandons, or completes
      an attempt.
- [ ] A stale Report Job lease commits a report or Review Run.
- [ ] A Review retry consumer invokes the graph without a fresh Report Job
      claim.
- [ ] A public projection silently fast-forwards over graph State.
- [ ] A repeated question/report operation calls the provider again after its
      first artifact committed.
- [ ] A completed effect is overwritten by a different output digest.
- [ ] Session, Report, Report Job, and Review Run disagree after final commit.
- [ ] Cold-start reconciliation creates more than one initial projection or
      Review Run.
- [ ] Existing `langgraph-v1` or `langgraph-review-v1` work cannot resume.
- [ ] Legacy behavior changes or an existing row is migrated.
- [ ] Diagnostics, logs, acceptance output, or canary status expose prohibited
      content.
- [ ] Any code, migration, test, or script changes rollout above zero.

## Final Repository Checklist

- [ ] The PostgreSQL optimization baseline and query-plan checks pass.
- [ ] Lock key derivation is stable and covered by fixed vectors.
- [ ] Every production graph invocation uses the advisory-lock helper.
- [ ] Locks release at interrupt and terminal boundaries.
- [ ] Projection divergence fails closed while legal same-digest replay remains
      idempotent.
- [ ] Generation attempts use unique tokens and monotonically ordered fencing.
- [ ] Every generation mutation validates current ownership.
- [ ] Silent providers are heartbeated independently of chunk flushes.
- [ ] Lease loss stops old executors without damaging new claims.
- [ ] Review timers return work to Report Job claiming.
- [ ] Review invocation requires both job lease and thread lock.
- [ ] Final report commit is lease-fenced and atomic.
- [ ] Question and report effects are write-once and replay-reusable.
- [ ] Recovery tests assert scenario-specific provider, projection, artifact,
      and commit call counts rather than set membership alone; the pre-artifact
      crash window explicitly permits a repeated provider call.
- [ ] Interview and Review cold starts reconcile deterministically.
- [ ] Preflight validates locks, fencing schema, effect uniqueness, versions,
      runtime settings, and rollout safety.
- [ ] Canary output is aggregate, read-only, database-clock based, and
      allowlisted.
- [ ] Focused, PostgreSQL, legacy compatibility, full Python, frontend, browser,
      privacy, and diff gates pass.
- [ ] `docs/langgraph-stage46-acceptance.md` is
      `READY_FOR_FENCING_CANARY`, not `PASS`.
- [ ] Both committed rollout defaults remain zero.

## Explicit Post-Stage-46 Backlog

These items remain intentionally unsolved:

- **Application PostgreSQL connection pool:** Store methods still open many
  independent connections. Migrate them through a shared connection provider in
  a separate infrastructure stage after execution ownership is proven.
- **Reference-only Interview messages:** Moving message text out of
  `DurableInterviewState` requires `langgraph-v2`, immutable message-log
  integrity, new privacy acceptance, and migration-free new assignment.
- **Question-level Review retry:** Independent per-question attempts and partial
  completion semantics require `langgraph-review-v2` State changes.
- **Fallback provenance:** Message-level provider/fallback source belongs to the
  next Interview State/message schema.
- **Checkpoint retention:** Completed-thread deletion requires a lifecycle
  owner, retention policy, legal/privacy approval, and active-thread safety.
- **Legacy retirement:** Legacy paths remain until a separately approved stage
  proves zero active ownership and historical read compatibility.
- **Production canary:** Moving from rollout zero to 1% requires separate
  operator authorization and deployed observation. Repository readiness alone
  is insufficient.

## Completion Definition

Stage 46 repository work is complete when:

1. existing PostgreSQL recovery remains green after the optimization batch;
2. same-thread graph invocation is serialized across processes;
3. stale generation and Report Job owners cannot write or commit;
4. projection divergence fails closed;
5. Review retries always reacquire Report Job ownership;
6. provider side effects are stable under replay and cannot be overwritten;
7. Interview and Review cold starts recover deterministically;
8. the combined concurrency/fault matrix passes with scenario-specific
   call-count assertions, including the explicitly repeatable pre-artifact
   provider window;
9. preflight, canary, privacy, full regression, and browser gates pass;
10. the Stage 46 acceptance record is `READY_FOR_FENCING_CANARY`;
11. committed rollout defaults are still zero.

An actual production canary is explicitly outside this implementation plan.
