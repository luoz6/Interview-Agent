# Stage 47.1 LangGraph Heartbeat Fail-Closed Hardening Plan

> **Execution note:** Implement this plan in task order and begin every task
> with the stated failing test. This stage is a narrow hardening patch on top
> of Stage 47. It must not change either Durable State schema, either Graph
> topology, the `langgraph-canary-v2` artifact contract, or any committed
> rollout default. Do not create a commit unless explicitly requested.

**Goal:** Make Generation, Report Job, and Review Effect heartbeat failures
fail closed when a background renewal call raises, while preserving the
existing lease/fencing model, retry taxonomy, privacy boundary, and outer
signal ownership.

**Architecture:** Each existing heartbeat keeps its current lifecycle and
public interface. Its background thread records the first renewal exception
in process memory, sets the existing lost-ownership event, and terminates.
The next `ensure_owned()` call raises the stable typed ownership exception
with the renewal exception as its cause. Review Effect renewal loss uses a
new semantic subtype of the existing fenced-write exception so current catch,
classification, and signal behavior remain compatible. Final SQL writes and
failure mutations remain authoritative and continue to require an unexpired
lease or claim, matching token, and matching fencing version. The Runtime
Outbox Dispatcher remains the only Interview
signal boundary, and the Report Worker remains the only Review signal
boundary. No raw exception detail is stored in LangGraph State, business
tables, signal buckets, or canary artifacts.

**Tech Stack:** Python 3.11, LangGraph 1.2, PostgreSQL 16, psycopg2, Pydantic
v2, pytest, the existing PostgresSaver runtime, Generation/Report/Review
Effect leases, PostgreSQL fencing predicates, privacy-safe runtime signal
buckets, and the Stage 46/47 recovery and canary gates.

**Baseline:** Stage 46 single-writer and lease fencing is implemented. Stage
47 repository acceptance is `READY_FOR_OPERATOR_FENCING_CANARY`; its local
operator procedure rehearsal is `PASS_LOCAL_OPERATOR_REHEARSAL`; production
operator observation is still `NOT_RUN`; and committed Interview/Review
rollout defaults remain `0/0`. Stage 47.1 does not reinterpret local evidence
as deployed canary evidence.

---

## Why This Is the Next Step

The three heartbeat implementations correctly treat a `False` renewal result
as ownership loss, but their background `_run()` methods do not catch an
exception raised by the renewal store call:

- `GenerationLeaseHeartbeat` in
  `app/graphs/durable_interview_graph.py`;
- `ReportLeaseHeartbeat` in `app/services/review_workflow.py`;
- `ReviewEffectHeartbeat` in
  `app/services/review_workflow_store.py`.

If PostgreSQL connectivity fails inside one of those background threads, the
thread exits without necessarily setting the heartbeat's lost event.
Generation and Review Effect `ensure_owned()` currently inspect only that
event, so they cannot immediately distinguish a healthy heartbeat from a
thread that died with an exception. Report `ensure_owned()` performs an
additional synchronous lease assertion, but an exception from that assertion
is not normalized to `ReportLeaseLost`.

This is not an unfenced-write vulnerability. The existing Generation and
Review Effect completion SQL still requires an unexpired lease plus the
active token and fencing version. Report final commit still verifies the
Report Job lease inside its fenced transaction. A stale or expired owner is
therefore rejected. The gap is earlier fail-closed detection, stable error
classification, and deterministic observation of a renewal failure.

Stage 47.1 closes that gap before a real fencing canary. Connection pooling,
checkpoint retention, Graph v2, and question-level Review retry remain
separate later stages.

## Execution Preconditions

1. Preserve the complete dirty worktree. Do not reset, delete, rewrite, stage,
   or commit unrelated tracked or untracked files.
2. Run PostgreSQL tests only against the configured local/test database and a
   unique safe test table prefix.
3. Keep these committed values unchanged:

   ```text
   INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
   REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
   INTERVIEW_LANGGRAPH_RUNTIME_ENABLED=true
   REPORT_LANGGRAPH_RUNTIME_ENABLED=true
   LANGGRAPH_STRICT_MSGPACK=true
   ```

4. Preserve every existing Graph node name, edge, interrupt location, thread
   identity, workflow engine value, and graph schema version.
5. Existing `langgraph-v1` Interview threads and
   `langgraph-review-v1` Review jobs must remain resumable across process loss.
6. Do not call a real LLM in repository tests. Use deterministic providers,
   barriers, injected renewal failures, and real PostgreSQL fencing rows.
7. Do not add raw session, job, generation, effect, checkpoint, token, fencing,
   payload, answer, report, or exception-message data to runtime signals or
   acceptance artifacts.
8. After any implementation change, treat previous repository acceptance as
   historical evidence. Re-run the complete Stage 47.1 gate before restoring
   `READY_FOR_OPERATOR_FENCING_CANARY`.

## Scope

This stage covers:

- catching renewal exceptions in all three background heartbeat threads;
- recording the first exception only in process memory;
- setting lost ownership before the thread terminates;
- raising the stable typed ownership exception from `ensure_owned()`;
- normalizing synchronous Report lease assertion errors;
- fencing Review Effect failure mutations as well as completions;
- proving that no final write occurs after heartbeat ownership becomes
  unverifiable;
- proving that existing and strengthened fencing predicates reject stale
  owners;
- proving that outer boundaries record exactly one existing privacy-safe
  signal code;
- documenting that an inability to renew is treated as an inability to prove
  ownership;
- extending Stage 47 acceptance so this behavior cannot regress.

## Non-Goals

- Do not change `DurableInterviewState` or `DurableReviewState`.
- Do not add, remove, or rename LangGraph nodes or edges.
- Do not add `langgraph-v2` or `langgraph-review-v2`.
- Do not add automatic checkpoint migration.
- Do not add question-level Review retry.
- Do not refactor `generate_followup()` beyond the minimum heartbeat wiring
  needed for this stage.
- Do not create a shared generic heartbeat framework or base class.
- Do not add PostgreSQL connection pools.
- Do not change heartbeat intervals or lease durations.
- Do not change provider retry counts or backoff schedules.
- Do not add provider cancellation as a correctness dependency.
- Do not change the `langgraph-canary-v2` schema or add new signal codes.
- Do not change correctness conflicts from `ROLL_BACK` or ownership anomalies
  from `HOLD`.
- Do not automatically run or claim a staging/production canary.
- Do not change the final committed rollout pair from `0/0`.

## Fixed Decisions

1. **Unable to renew means unable to prove ownership.** A renewal store call
   that raises is treated as ownership loss for the current execution. The
   worker must stop before its next guarded persistence operation.
2. **SQL fencing remains authoritative.** The heartbeat is an early detector,
   not the final source of truth. Generation chunk/complete/fail writes,
   Review Effect completion/failure, and Report final commit must require an
   active parent lease or claim plus the matching token/fencing identity.
3. **Give Review Effect lease loss a compatible semantic type.** Generation
   raises `GenerationLeaseLost`, Report raises `ReportLeaseLost`, and Review
   Effect raises a new `ReviewEffectLeaseLost` subclass of
   `FencedWriteRejected`. The subclass preserves every existing
   `isinstance(..., FencedWriteRejected)` catch and the current stable failure
   mapping while distinguishing claim-renewal loss from an SQL statement that
   actually rejected a stale write.
4. **Use existing stable signal codes.** The corresponding outer-boundary
   codes remain `generation_lease_lost`, `report_lease_lost`, and
   `fenced_write_rejected`. This keeps `langgraph-canary-v2` stable.
5. **The first renewal failure wins.** Store only the first background
   exception. Later shutdown races or repeated checks must not replace it.
6. **The exception cause is process-local.** The original exception may be
   chained with `raise ... from failure` for diagnostics. It must never be
   serialized to State, written to a signal bucket, placed in an Outbox
   payload, or included in canary evidence.
7. **Outer boundaries own observation.** Heartbeats do not increment runtime
   signal buckets. Interview signals are recorded by the Runtime Outbox
   Dispatcher; Review signals are recorded by the Report Worker. This avoids
   double counting.
8. **Signal write failure never changes the business outcome.** Existing
   best-effort signal behavior remains unchanged.
9. **No retry-policy expansion.** This patch only normalizes ownership
   failures. `ReviewEffectLeaseLost` intentionally inherits the current
   non-retryable `FencedWriteRejected` classification in Stage 47.1. It does
   not introduce Review question retry or reinterpret ownership failure as a
   provider failure. The Effect Store can prove reclaim independently while
   the parent Job stays active, but the complete Review v1 Graph still enters
   `fail_review`. Stage 51 owns that Job-level policy redesign.
10. **No Graph-boundary refactor.** The heartbeat objects keep `__enter__`,
    `__exit__`, `ensure_owned`, and `_run`. This makes the patch small and
    preserves injected heartbeat factories used by tests.
11. **Bounded shutdown remains.** Context exit sets the stop event and joins
    the daemon thread with the current bounded timeout. Shutdown must not hold
    a SQL transaction open.
12. **A successful committed result remains successful.** A late heartbeat
    shutdown race after an authoritative final commit must not rewrite the
    business result. Replay continues to rely on existing idempotency and
    completed-effect reuse.
13. **No new sensitive logs.** A boundary may log a stable code and exception
    class. It must not log the exception message if that message can contain a
    DSN, identifier, provider content, or database statement parameter.
14. **Production observation stays `NOT_RUN`.** Repository tests and a local
    procedure rehearsal do not create deployed canary evidence.

## Required Runtime Semantics

For each heartbeat, the required event sequence is:

```text
background renewal call
        |
        +-- returns True  -> continue
        |
        +-- returns False -> mark lost -> stop thread
        |
        +-- raises error  -> remember first cause -> mark lost -> stop thread

provider/graph execution continues until next guarded boundary
        |
        v
ensure_owned()
        |
        +-- healthy -> continue to fenced SQL
        |
        +-- lost    -> raise stable typed ownership exception
```

Required exception mapping:

| Component | Typed exception | Runtime failure code | Retryable today | Canary decision |
| --- | --- | --- | --- | --- |
| Generation attempt | `GenerationLeaseLost` | `generation_lease_lost` | yes | HOLD |
| Report Job | `ReportLeaseLost` | `report_lease_lost` | yes | HOLD |
| Review Effect renewal/claim loss | `ReviewEffectLeaseLost(FencedWriteRejected)` | `fenced_write_rejected` | no | HOLD |
| Review Effect stale SQL write | `FencedWriteRejected` | `fenced_write_rejected` | no | HOLD |

The semantic subtype deliberately retains `fenced_write_rejected,
retryable=False` in Stage 47.1 because current Review v1 has no independent
question retry and because this patch must not change the existing effect
ambiguity policy. This is a documented compatibility compromise rather than a
claim that renewal failure and stale-write rejection are identical. Stage 51
owns the retry redesign and may introduce a distinct stable signal contract.

---

## Task 1: Freeze the Stage 47.1 Release Contract

**Files:**

- Create: `docs/langgraph-stage47-1-heartbeat-hardening-acceptance.md`
- Create: `tests/test_langgraph_stage47_1_release_contract.py`
- Modify: `docs/langgraph-stage47-fencing-canary-acceptance.md`
- Modify: `docs/langgraph-stage47-fencing-canary-observation.md` only to add a
  historical note if needed; keep its status `NOT_RUN`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write the failing release-contract test**

Require the new acceptance record to begin with:

```text
Status: PENDING_HEARTBEAT_HARDENING
```

Require it to state:

- Stage 46 and Stage 47 are prerequisites;
- all three heartbeat implementations are in scope;
- renewal exceptions fail closed;
- existing lease/fencing SQL remains authoritative and `fail_effect()` gains
  the missing active Report lease, unexpired claim, and zero-row rejection;
- `ReviewEffectLeaseLost` is a semantic subtype of
  `FencedWriteRejected`, preserving current catch and classifier behavior;
- no State or Graph topology changes are allowed;
- existing signal codes and `langgraph-canary-v2` remain unchanged;
- committed rollout defaults remain zero;
- local evidence is not deployed canary evidence;
- Stage 48 connection pooling and Stage 51 question retry are deferred.

- [ ] **Step 2: Create the acceptance record with placeholders**

Include named gates for:

```text
generation heartbeat unit
report heartbeat unit
review effect heartbeat unit
outer-boundary signal ownership
PostgreSQL stale-owner fencing
Interview recovery
Review recovery
Stage 47 canary regression
full Python
browser
privacy
mechanical checks
```

- [ ] **Step 3: Link Stage 47 without changing its decision**

Add a note that Stage 47.1 hardens background renewal failures before any real
operator fencing canary. Keep the Stage 47 acceptance decision unchanged as
historical evidence until Stage 47.1 gates finish.

- [ ] **Step 4: Run the contract gate**

```powershell
python -m pytest -q `
  tests/test_langgraph_stage47_1_release_contract.py `
  tests/test_langgraph_stage47_release_contract.py `
  tests/test_local_v1_docs.py
```

Expected: pass, with production observation still `NOT_RUN` and both rollout
defaults still zero.

## Task 2: Capture Generation Heartbeat Renewal Exceptions

**Files:**

- Modify: `app/graphs/durable_interview_graph.py`
- Modify: `tests/test_durable_interview_graph.py`
- Modify: `tests/test_runtime_work.py`

- [ ] **Step 1: Add a failing direct heartbeat test**

Use a fake Generation Store whose `heartbeat_attempt()` raises a deterministic
exception after the heartbeat thread starts. Use a short injected interval or
event coordination; do not use a long sleep.

Assert:

```text
background thread terminates
_lost becomes set
ensure_owned raises GenerationLeaseLost
GenerationLeaseLost.__cause__ is the injected exception
```

The test must not inspect or persist a lease token beyond the fake object's
local assertions.

- [ ] **Step 2: Add a first-failure-wins test**

Coordinate two attempts to mark the heartbeat lost and assert that the first
stored cause is preserved. A later false return or shutdown path must not
replace it.

- [ ] **Step 3: Add a generation-node safety test**

Use a provider that blocks on a barrier until the heartbeat failure has
occurred, then yields or returns its next chunk. Assert:

```text
generate_followup raises GenerationLeaseLost
append_chunk is not called after the failure is visible
complete_attempt is not called
fail_attempt is not called by the stale execution
```

The existing rule that `generation_lease_lost` is re-raised rather than
converted to provider failure must remain intact.

- [ ] **Step 4: Implement exception capture**

Add process-local first-failure storage to `GenerationLeaseHeartbeat`.
`_run()` catches `Exception`, records the first cause, sets the lost event, and
returns. `ensure_owned()` raises `GenerationLeaseLost` from the stored cause.

Do not catch `BaseException`. Interpreter termination and test interrupts are
not normal renewal failures.

- [ ] **Step 5: Preserve clean shutdown**

Keep the stop event and bounded join behavior. Extend the clean-shutdown test
to prove that a healthy heartbeat thread exits and no cause is recorded.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest -q `
  tests/test_durable_interview_graph.py `
  tests/test_runtime_work.py
```

Expected: pass with the Graph node/edge contract unchanged.

## Task 3: Normalize Report Heartbeat Renewal and Assertion Errors

**Files:**

- Modify: `app/services/review_workflow.py`
- Modify: `tests/test_review_workflow.py`
- Modify: `tests/test_report_worker.py`

- [ ] **Step 1: Add a failing background-renewal test**

Use a fake Report Job Store whose `heartbeat()` raises. Assert:

```text
background thread terminates
lost event is set
ensure_owned raises ReportLeaseLost
the injected exception is preserved as __cause__
```

- [ ] **Step 2: Add a failing synchronous-assertion test**

Use a Store whose `assert_lease()` raises during `__enter__()` or a later
`ensure_owned()`. Assert that the public exception is `ReportLeaseLost`, not a
raw psycopg2/database exception.

- [ ] **Step 3: Add a claimed-job mutation test**

Run a Durable Review job with an injected heartbeat failure while the Graph is
still executing. Assert that the stale worker does not:

- mark the Report Job completed;
- mark the Report Job failed;
- schedule a retry under a replacement token;
- overwrite the Review Run or final report.

The Report Worker should return the current authoritative job row, matching
the existing ownership-failure branch.

- [ ] **Step 4: Implement Report normalization**

Apply the same first-failure semantics to `ReportLeaseHeartbeat._run()`.
Wrap exceptions from the synchronous `assert_lease()` call, record the first
cause, set lost, and raise `ReportLeaseLost` from that cause.

Keep the `False` and exception branches distinct. The implementation should
follow this control flow:

```python
def ensure_owned(self) -> None:
    if self._lost.is_set():
        raise ReportLeaseLost(
            "report job lease is no longer owned"
        ) from self._first_failure
    try:
        owned = self.job_store.assert_lease(
            self.job_id,
            worker_id=self.worker_id,
            lease_token=self.lease_token,
        )
    except Exception as exc:
        self._mark_lost(exc)
        raise ReportLeaseLost(
            "report job lease ownership could not be verified"
        ) from exc
    if not owned:
        self._mark_lost()
        raise ReportLeaseLost(
            "report job lease is no longer owned"
        )
```

The exact private helper and field names may differ, but a raised assertion
error must never escape as raw psycopg2/database failure.

- [ ] **Step 5: Preserve existing false-result behavior**

A `False` heartbeat or assertion result still raises `ReportLeaseLost` with no
fabricated exception cause.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest -q `
  tests/test_review_workflow.py `
  tests/test_report_worker.py `
  tests/test_runtime_work.py
```

Expected: pass, with `ReportLeaseLost` still classified as
`report_lease_lost, retryable=True`.

## Task 4: Capture Review Effect Heartbeat Renewal Exceptions

**Files:**

- Modify: `app/services/workflow_thread_lock.py`
- Modify: `app/services/review_workflow_store.py`
- Modify: `tests/test_workflow_thread_lock.py`
- Modify: `tests/test_review_workflow_store.py`
- Modify: `tests/test_durable_review_graph.py`
- Modify: `tests/test_report_worker.py`
- Modify: `tests/test_runtime_work.py`

- [ ] **Step 1: Add a failing direct heartbeat test**

Use a fake Review Workflow Store whose `heartbeat_effect()` raises. Assert:

```text
background thread terminates
lost event is set
ensure_owned raises ReviewEffectLeaseLost
the exception remains an isinstance of FencedWriteRejected
the injected exception is preserved as __cause__
```

- [ ] **Step 2: Add a provider-return safety test**

Coordinate a provider that returns only after the Review Effect heartbeat has
failed. Assert that `complete_effect()` is not called by `run_effect()`.

The provider call may already have occurred. Stage 47.1 must not claim exactly
once external provider invocation. It guarantees only that the execution
without provable ownership cannot commit the effect.

- [ ] **Step 3: Add provider-failure ownership-precedence tests**

Cover both failure branches in `run_effect()`:

```text
provider raises
provider returns a non-dict payload
```

For each branch, coordinate the heartbeat renewal failure before the provider
branch attempts `fail_effect()`. Assert:

```text
heartbeat.ensure_owned runs before fail_effect
fail_effect is not called by the unowned execution
ReviewEffectLeaseLost takes precedence over provider/validation failure
the original heartbeat renewal error remains the ownership exception cause
```

If ownership is still healthy, preserve the existing behavior:

```text
provider/validation failure
-> fenced fail_effect succeeds
-> original provider/validation exception is re-raised
```

- [ ] **Step 4: Fence `fail_effect()` itself**

Write failing PostgreSQL Store tests proving `fail_effect()` is an
authoritative mutation rather than best-effort cleanup. It must validate the
active parent Report Job lease in the same transaction and update only when:

```sql
effects.status = 'running'
AND effects.claim_token = active claim token
AND effects.fencing_version = active fencing version
AND effects.claim_expires_at > NOW()
```

If the parent Report lease is not active, preserve the existing
`ReportLeaseLost` result from `_assert_active_lease()`. If the effect predicate
updates zero rows, raise `ReviewEffectLeaseLost`; do not silently return.

Assert that a stale owner cannot mark an expired-but-not-yet-reclaimed effect
as failed, and cannot mask ownership loss by re-raising only the provider
exception.

- [ ] **Step 5: Add a Graph outcome test**

Verify the current Review v1 behavior remains explicit:

```text
Review Effect heartbeat failure
-> ReviewEffectLeaseLost (compatible FencedWriteRejected subtype)
-> question outcome error_code fenced_write_rejected
-> fail_review under the current v1 policy
```

Do not add independent question retry in this task.

- [ ] **Step 6: Implement the semantic subtype and exception capture**

Add:

```python
class ReviewEffectLeaseLost(FencedWriteRejected):
    """The active Review Effect claim is no longer provably owned."""
```

Place the base class before the subtype. Do not add a special classifier
branch in Stage 47.1: inheritance intentionally preserves
`fenced_write_rejected, retryable=False` and existing Report Worker catches.

Apply first-failure storage and exception chaining to
`ReviewEffectHeartbeat`. Update `run_effect()` so both provider-failure paths
prove heartbeat ownership before calling the newly fenced `fail_effect()`.

- [ ] **Step 7: Preserve the initial-claim boundary deliberately**

`claim_effect()` already verifies the parent Report lease and returns a fresh
or reclaimed active claim inside a transaction, immediately before entering
`ReviewEffectHeartbeat`. Stage 47.1 therefore does not add a redundant
`__enter__()` database round trip. Add a code comment or contract test that
identifies `claim_effect()` as the initial ownership proof; periodic renewal
and every effect mutation remain fenced after that point.

The future shared-heartbeat lifecycle may add an explicit initial assertion if
the gap between claim and provider execution grows or becomes asynchronous.

- [ ] **Step 8: Run focused tests**

```powershell
python -m pytest -q `
  tests/test_workflow_thread_lock.py `
  tests/test_review_workflow_store.py `
  tests/test_durable_review_graph.py `
  tests/test_report_worker.py `
  tests/test_runtime_work.py
```

Expected: pass with Review v1 retry semantics unchanged.

## Task 5: Prove Stable Classification and Single Signal Ownership

**Files:**

- Modify only if a test exposes a gap:
  `app/services/runtime_work.py`
- Modify only if a test exposes a gap:
  `app/services/runtime_outbox_dispatcher.py`
- Modify only if a test exposes a gap:
  `app/services/report_worker.py`
- Modify: `tests/test_runtime_work.py`
- Modify: `tests/test_runtime_outbox_dispatcher.py`
- Modify: `tests/test_report_worker.py`
- Modify: `tests/test_runtime_signal_metrics.py`

- [ ] **Step 1: Freeze existing classifier outputs**

Assert exact mappings:

```text
GenerationLeaseLost -> generation_lease_lost, retryable=True
ReportLeaseLost     -> report_lease_lost, retryable=True
FencedWriteRejected -> fenced_write_rejected, retryable=False
ReviewEffectLeaseLost (subclass)
                     -> fenced_write_rejected, retryable=False
```

Also assert that exception chaining does not change classification: the outer
typed exception, not its psycopg2 cause, determines the stable code. Assert
that `ReviewEffectLeaseLost` remains catch-compatible with
`FencedWriteRejected`.

- [ ] **Step 2: Prove Interview signal ownership**

Inject a `GenerationLeaseLost` caused by a renewal exception through the
Interview event-consumption path. Assert exactly one signal increment:

```text
(workflow_type="interview", signal_code="generation_lease_lost")
```

The heartbeat and Store must not increment the bucket themselves.

- [ ] **Step 3: Prove Review Report signal ownership**

Inject a caused `ReportLeaseLost` through `run_one_job()`. Assert exactly one:

```text
(workflow_type="review", signal_code="report_lease_lost")
```

- [ ] **Step 4: Prove Review Effect signal ownership**

Return the current v1 terminal outcome with
`error_code="fenced_write_rejected"` and assert the Report Worker records
exactly one existing fenced-write signal.

- [ ] **Step 5: Prove privacy**

Use adversarial causes containing strings that resemble:

```text
DSN
lease token
session identifier
provider payload
raw SQL parameter
```

Assert those strings do not appear in:

- signal store arguments;
- canary snapshot payload;
- JSON/Markdown acceptance artifacts;
- structured log `extra` fields owned by this patch.

- [ ] **Step 6: Keep the signal allowlist unchanged**

Assert `CANARY_SIGNAL_CODES` has no new Stage 47.1 code and that the signal
table schema remains the five-column privacy contract.

- [ ] **Step 7: Run boundary tests**

```powershell
python -m pytest -q `
  tests/test_runtime_work.py `
  tests/test_runtime_outbox_dispatcher.py `
  tests/test_report_worker.py `
  tests/test_runtime_signal_metrics.py `
  tests/test_langgraph_canary_status.py
```

Expected: pass with no double count and no canary schema change.

## Task 6: Add the PostgreSQL Stale-Owner Fault Matrix

**Files:**

- Create: `tests/test_langgraph_heartbeat_recovery_postgres.py`
- Modify: `pytest.ini`
- Modify: `scripts/langgraph_stage47_acceptance.py`

Use a dedicated marker such as:

```text
langgraph_heartbeat_recovery
```

Every test must use a unique, length-safe table prefix and deterministic
barriers. Do not depend on timing races alone.

- [ ] **Step 1: Generation renewal exception before persistence**

Sequence:

```text
Worker A owns Generation attempt
Worker A provider blocks
Worker A heartbeat raises
Worker A observes GenerationLeaseLost
lease expires or is deliberately expired in the isolated test
Worker B reclaims with a higher fencing version
Worker B completes
Worker A stale append/complete is rejected
```

Assert:

```text
one completed generation
one final text
no stale chunk after ownership loss
replacement fencing version is higher
```

- [ ] **Step 2: Report renewal exception before final commit**

Sequence:

```text
Worker A owns Report Job
Worker A heartbeat raises during Graph execution
Worker A cannot commit or mutate replacement ownership
Worker B reclaims after expiry
Worker B resumes the stored Graph version
Worker B commits report atomically
```

Assert:

```text
one Report Job
one Review Run
one report projection
terminal status completed
```

- [ ] **Step 3: Review Effect renewal exception before effect completion**

Sequence:

```text
Worker A claims effect
provider returns a deterministic payload
heartbeat raises before complete_effect
Worker A is fenced
claim expires
the parent Report Job remains active in this Store-level fault test
Worker B obtains the active Report Job lease and reclaims the Effect with a
higher fencing version
Worker B commits the winner
Worker A cannot overwrite it
```

Assert:

```text
one completed effect row
winner digest is stable
loser cannot update payload or digest
```

Repeat the ownership-loss portion with a provider exception instead of a
successful payload. Assert Worker A cannot mark the expired/reclaimed effect
failed, `ReviewEffectLeaseLost` takes precedence, and Worker B can still
reclaim and commit the winner.

These are Store-level authority tests. In the complete
`langgraph-review-v1` Graph, `review_one_question()` catches the subtype as a
`FencedWriteRejected`, produces `error_code="fenced_write_rejected"`, and the
current v1 policy terminates the Review Job. Do not claim Job-level automatic
recovery for Review Effect renewal loss in Stage 47.1; Stage 51 owns that
retry-policy change.

- [ ] **Step 4: Recovery after process replacement**

For Interview and Review, construct a fresh runtime/service instance after
the first worker loses ownership. Do not reuse the first Graph service object.
This proves recovery across process-local heartbeat state.

- [ ] **Step 5: Real overlap proof**

Each concurrency test uses barriers/events to prove the first provider call is
in flight before ownership changes. A test that merely starts two fast threads
without a barrier is insufficient.

- [ ] **Step 6: Cleanup proof**

In every `finally` block:

- stop heartbeat threads;
- shut down PostgresSaver runtimes;
- delete only known test thread IDs;
- drop only validated test-prefix tables;
- assert no test heartbeat thread remains alive.

- [ ] **Step 7: Run the PostgreSQL matrix**

```powershell
$env:POSTGRES_DSN='<approved test DSN>'
python -m pytest -q -m langgraph_heartbeat_recovery
```

Expected: pass with no real provider calls.

## Task 7: Re-run the Combined Recovery and Canary Gates

**Files:**

- Modify: `scripts/langgraph_stage47_acceptance.py`
- Modify: `docs/langgraph-stage47-1-heartbeat-hardening-acceptance.md`

`scripts/langgraph_stage47_acceptance.py` already exists in the Stage 47
baseline. Extend it; do not create a second acceptance runner with overlapping
repository/operator status semantics.

- [ ] **Step 1: Extend the Stage 47 acceptance runner**

Add named checks without changing its operator status semantics:

```text
generation_heartbeat_exception_fail_closed
report_heartbeat_exception_fail_closed
review_effect_heartbeat_exception_fail_closed
heartbeat_signal_single_owner
heartbeat_postgres_stale_owner_rejected
```

The runner must still emit:

```text
operator_observation: NOT_RUN
rollout_defaults_changed: false
```

- [ ] **Step 2: Run focused unit gates**

```powershell
python -m pytest -q `
  tests/test_durable_interview_graph.py `
  tests/test_review_workflow.py `
  tests/test_review_workflow_store.py `
  tests/test_durable_review_graph.py `
  tests/test_runtime_work.py `
  tests/test_runtime_outbox_dispatcher.py `
  tests/test_report_worker.py `
  tests/test_runtime_signal_metrics.py `
  tests/test_langgraph_canary_status.py `
  tests/test_langgraph_canary_cli.py
```

- [ ] **Step 3: Run PostgreSQL runtime/control gates**

```powershell
python -m pytest -q -m "pg_runtime or pg_control"
```

- [ ] **Step 4: Run Interview recovery**

```powershell
python -m pytest -q -m langgraph_recovery
```

- [ ] **Step 5: Run Review recovery**

```powershell
python -m pytest -q -m langgraph_review_recovery
```

- [ ] **Step 6: Run dual and fencing canary matrices**

```powershell
python -m pytest -q `
  -m "langgraph_dual_canary or langgraph_fencing_canary or langgraph_heartbeat_recovery"
```

- [ ] **Step 7: Run the Stage 47 acceptance runner**

```powershell
$env:POSTGRES_DSN='<approved test DSN>'
python -m scripts.langgraph_stage47_acceptance
```

Expected repository status:

```text
READY_FOR_OPERATOR_FENCING_CANARY
```

Expected operator status:

```text
NOT_RUN
```

## Task 8: Update Operator Documentation Without Claiming Production Evidence

**Files:**

- Modify: `docs/local-v1-runbook.md`
- Modify: `docs/langgraph-stage47-1-heartbeat-hardening-acceptance.md`
- Modify: `docs/langgraph-stage47-local-operator-rehearsal.md`
  only if a new local rehearsal is actually run
- Do not change the status in:
  `docs/langgraph-stage47-fencing-canary-observation.md`

- [ ] **Step 1: Document operator meaning**

Add a concise entry:

```text
generation_lease_lost / report_lease_lost / fenced_write_rejected
may mean the active owner changed or that renewal could no longer prove
ownership. In both cases promotion holds until database health and worker
overlap are investigated.
```

- [ ] **Step 2: Document investigation order**

For a heartbeat ownership incident, check only safe operational data:

1. PostgreSQL availability and connection saturation;
2. worker restart/replacement events;
3. expired lease counts after grace;
4. runtime signal bucket counts;
5. Outbox/Report Job backlog and age;
6. fencing rejection counts;
7. active worker topology.

Do not paste raw lease tokens, job IDs, provider payloads, checkpoint contents,
or DSNs into the observation record.

- [ ] **Step 3: Re-run the local procedure rehearsal only after all code gates**

Use the existing seven-phase local sequence with isolated PostgreSQL tables
and deterministic providers. Its maximum result remains:

```text
PASS_LOCAL_OPERATOR_REHEARSAL
```

Production observation remains:

```text
NOT_RUN
```

- [ ] **Step 4: Update the acceptance decision**

Only after every repository gate passes, change the Stage 47.1 acceptance
record from:

```text
PENDING_HEARTBEAT_HARDENING
```

to:

```text
READY_FOR_OPERATOR_FENCING_CANARY
```

Record exact test counts and the short repository revision. Do not include a
DSN or test table prefix.

## Task 9: Run Final Repository Gates

**Files:**

- Modify only the Stage 47.1 acceptance record with final evidence

- [ ] **Step 1: Compile Python**

```powershell
python -m compileall -q app scripts tests
```

- [ ] **Step 2: Run the full Python suite**

```powershell
python -m pytest -q
```

Expected: all deterministic tests pass; explicit opt-in real-model tests may
remain skipped according to the existing contract.

- [ ] **Step 3: Run CSS regression**

```powershell
npm run build:prototype-css
```

- [ ] **Step 4: Run browser regression**

```powershell
npm run test:browser
```

- [ ] **Step 5: Audit generated acceptance artifacts**

Run the existing privacy audit over all newly generated JSON artifacts.
Expected:

```text
privacy_status: PASS
privacy_violation_count: 0
```

- [ ] **Step 6: Check rollout defaults**

```powershell
rg -n "^INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=|^REPORT_LANGGRAPH_ROLLOUT_PERCENT=" .env.example
```

Expected:

```text
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
```

- [ ] **Step 7: Run mechanical diff checks**

```powershell
git diff --check
git status --short
```

Review the complete diff. Preserve unrelated dirty-worktree changes and do not
stage or commit without explicit approval.

- [ ] **Step 8: Confirm resource cleanup**

Confirm:

- no Stage 47.1 test server remains listening;
- no heartbeat test thread remains alive;
- no temporary PostgresSaver runtime remains open;
- no isolated test-prefix table remains;
- only privacy-safe artifacts intentionally retained in `tmp/` remain.

- [ ] **Step 9: Record final status**

The only successful repository terminal status for this stage is:

```text
READY_FOR_OPERATOR_FENCING_CANARY
```

The production observation remains:

```text
NOT_RUN
```

Do not record `PASS_FENCING_CANARY` without an explicitly authorized and
actually observed deployed environment.

---

## Combined Fault Matrix

| Scenario | Expected typed result | Final stale write | Replacement recovery | Signal owner | Signal count |
| --- | --- | --- | --- | --- | ---: |
| Generation renewal returns false | `GenerationLeaseLost` | rejected/not attempted | yes | Outbox Dispatcher | 1 |
| Generation renewal raises | `GenerationLeaseLost` caused by renewal error | rejected/not attempted | yes | Outbox Dispatcher | 1 |
| Report renewal returns false | `ReportLeaseLost` | no stale Report mutation | yes | Report Worker | 1 |
| Report renewal raises | `ReportLeaseLost` caused by renewal error | no stale Report mutation | yes | Report Worker | 1 |
| Report synchronous assert raises | `ReportLeaseLost` caused by assertion error | no stale Report mutation | yes | Report Worker | 1 |
| Effect renewal returns false | `ReviewEffectLeaseLost` (`FencedWriteRejected` subtype) | effect completion/failure rejected or not attempted | Store-level reclaim if parent Job stays active; full v1 Job fails | Report Worker | 1 |
| Effect renewal raises | `ReviewEffectLeaseLost` caused by renewal error | effect completion/failure rejected or not attempted | Store-level reclaim if parent Job stays active; full v1 Job fails | Report Worker | 1 |
| Provider raises after Effect renewal loss | `ReviewEffectLeaseLost` takes precedence | stale `fail_effect` not attempted | Store-level reclaim only in Stage 47.1 | Report Worker | 1 |
| Invalid provider payload after Effect renewal loss | `ReviewEffectLeaseLost` takes precedence | stale `fail_effect` not attempted | Store-level reclaim only in Stage 47.1 | Report Worker | 1 |
| `fail_effect` loses parent Report lease | `ReportLeaseLost` | failure mutation rejected | yes | Report Worker | 1 |
| `fail_effect` loses Effect claim | `ReviewEffectLeaseLost` | zero-row failure mutation raises | yes after claim expiry | Report Worker | 1 |
| Signal bucket write raises | original business result preserved | unchanged | unchanged | outer boundary | best effort |
| Renewal fails after authoritative commit | completed result remains authoritative | not applicable | replay reuses completion | outer boundary if surfaced | at most 1 |
| Process dies without Python cleanup | lease/claim expires | stale token fenced | replacement reclaims | canary expired gauge | aggregate |

## Compatibility Matrix

| Contract | Before Stage 47.1 | After Stage 47.1 |
| --- | --- | --- |
| Interview Graph schema | `langgraph-v1` | unchanged |
| Review Graph schema | `langgraph-review-v1` | unchanged |
| Interview node/edge topology | Stage 47 topology | unchanged |
| Review node/edge topology | Stage 47 topology | unchanged |
| Interrupt payloads | Stage 47 payloads | unchanged |
| Checkpoint serialization | strict msgpack v1 State | unchanged |
| Generation fencing SQL | token + version + unexpired lease | unchanged |
| Report fencing SQL | active Report lease | unchanged |
| Effect completion fencing SQL | token + version + unexpired claim | unchanged |
| Effect failure fencing SQL | token + version only; zero rows ignored | active Report lease + token + version + unexpired claim; zero rows raise |
| Runtime failure codes | Stage 47 codes | unchanged |
| Canary schema | `langgraph-canary-v2` | unchanged |
| Signal bucket schema | five-column aggregate | unchanged |
| Committed rollout defaults | `0/0` | `0/0` |
| Production observation | `NOT_RUN` | `NOT_RUN` until separately executed |

## Stage 47.1 Completion Definition

Stage 47.1 is complete only when all of the following are true:

1. Generation, Report, and Review Effect heartbeat renewal exceptions set
   lost ownership and terminate their background thread.
2. The first background exception is preserved only as an in-process cause.
3. `ensure_owned()` raises the stable typed ownership exception, including the
   catch-compatible `ReviewEffectLeaseLost` subtype for Effect claim loss.
4. No raw renewal exception crosses into State, Outbox payloads, signal rows,
   or canary artifacts.
5. Existing `False` renewal semantics remain unchanged.
6. No stale worker can append/complete a Generation, commit a Report, or
   complete/fail a Review Effect after ownership is lost or unprovable.
7. A replacement worker can reclaim and finish through a fresh runtime.
8. Existing failure classifications and retry flags are unchanged.
9. Interview and Review outer boundaries each record exactly one existing
   signal for the surfaced incident.
10. The `langgraph-canary-v2` schema and signal allowlist are unchanged.
11. Both Durable State schemas and both Graph topologies are unchanged.
12. Stage 46 recovery, Stage 47 canary, full Python, CSS, browser, privacy,
    compile, and mechanical gates pass.
13. Committed rollout defaults remain `0/0`.
14. The Stage 47.1 repository status is
    `READY_FOR_OPERATOR_FENCING_CANARY`.
15. Production operator observation remains `NOT_RUN` unless a deployed
    environment is separately authorized and actually observed.

## Explicit Post-Stage-47.1 Backlog

- **Stage 48 — PostgreSQL connection ownership and capacity:** Introduce
  separate bounded connection domains for Checkpointer, business SQL, and
  advisory locks; make Checkpointer startup thread-safe; move saver setup to
  migration/preflight; add pool metrics and connection budgets; validate all
  derived identifiers against PostgreSQL's 63-byte limit.
- **Stage 49 — Checkpoint lifecycle and privacy governance:** Add capacity and
  age metrics, completed-thread retention, active-thread safety, dry-run
  cleanup, backup/restore procedure, and legal/privacy policy.
- **Stage 50 — Interview `langgraph-v2`:** Register v1 and v2 together, assign
  only new work to v2, keep per-version State `Literal` types, externalize
  messages and plan data to references/digests, add fallback provenance, and
  refactor Generation execution without changing v1 checkpoint boundaries.
- **Stage 51 — Review `langgraph-review-v2`:** Add question-level attempts,
  retry only failed questions, persist partial progress, separate question and
  report attempts, bound question count/Graph steps, and prevent batch outcome
  accumulation.
- **Stage 51.1 — Provider idempotency and response escrow:** Close the cost gap
  between a provider returning and the authoritative Effect commit. Prefer a
  provider-native idempotency key or a separately designed non-authoritative
  response escrow that a new owner can validate and adopt. Never let an owner
  that has lost its claim directly write an authoritative completed Effect.
  Evaluate a shared heartbeat lifecycle with an explicit initial ownership
  assertion when this work changes the claim-to-provider gap.
- **Higher rollout:** Any rollout above the initial 1% fencing canary requires
  Stage 48 capacity evidence and separate authorization.
- **Legacy retirement:** Remains blocked on real ownership evidence,
  historical read compatibility, and a separate migration/retirement plan.
