# Stage 47.2 Agent Runtime Telemetry Contract Hardening Plan

> **Execution note:** Implement this plan in task order and begin every task
> with the stated failing test. This stage hardens the Stage 43A/43B Agent
> Runtime without changing Agent business outputs, either Durable State
> schema, either LangGraph topology, `agent-runtime-v1`, or committed rollout
> defaults. Do not create a commit unless explicitly requested.

**Goal:** Convert the Agent Runtime's context-integrity, metadata-privacy,
recorder-observation, and production-composition conventions into enforced,
testable contracts while preserving completed/degraded/failed/cancelled
semantics and LangGraph retry/recovery ownership.

**Architecture:** `AgentExecutionRunner` remains a synchronous,
LangGraph-independent execution observer. At entry it captures a deep context
snapshot. Metadata extraction and outcome classification remain telemetry
helpers and cannot replace a successful provider result. Safe metadata is
normalized once before `AgentRunRecord` construction, so file and PostgreSQL
recorders receive the same validated payload. Production services continue
to receive the process-wide Runner from `runtime.py`; domain Agents do not
import that composition root. `prepare_interview()` continues to own one
Knowledge AgentRun, while `KnowledgeTraceRecorder` remains a separate
retrieval-diagnostic channel linked by the Prep correlation ID.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, LangGraph 1.2,
PostgreSQL 16, psycopg2, pytest, Playwright, `agent-runtime-v1`,
`CompositeAgentRunRecorder`, file Agent traces, PostgreSQL `agent_runs`, and
the existing privacy artifact audits.

**Baseline:** Stage 43A and Stage 43B acceptance are `PASS`. Stage 47.1
repository acceptance is `READY_FOR_OPERATOR_FENCING_CANARY`, production
operator observation remains `NOT_RUN`, and committed Interview/Review
rollout defaults remain `0/0`. Stage 48 remains reserved for PostgreSQL
connection ownership, pooling, and capacity.

---

## Why This Is the Next Step

The high-level separation is correct:

```text
AgentExecutionContext
        |
        v
AgentExecutionRunner
        |
        v
AgentRunRecorder Protocol
        |
        +-- AgentTraceRecorder
        +-- PostgresAgentRunRecorder
```

The remaining gaps are enforcement gaps:

1. `AgentExecutionContext` uses `extra="forbid"` but is mutable. `run()` and
   `stream()` serialize it only at final emission, so in-flight mutation can
   change historical context.
2. `safe_metadata` is `dict[str, Any]`. File traces use exact-key cleanup,
   while PostgreSQL currently stores callback output directly.
3. A successful provider result can be lost if `metadata(output)` or
   `classify(output)` raises before `_emit()` is entered.
4. `_emit()` silently suppresses a top-level recorder exception.
5. Production roots normally inject `get_agent_execution_runner()`, but an
   omitted dependency can still create a trace-only Runner.
6. Process-wide Runner initialization and recorder registration are not
   protected by one initialization lock.
7. Knowledge production calls are wrapped by `prepare_interview()`, while
   direct `KnowledgeAgent.generate_plan()` calls are not. The ownership is
   correct but implicit and vulnerable to future double wrapping.
8. `agent_runs` has session, correlation, and agent/status indexes but no
   operation-level aggregation contract.

Stage 47.2 closes these gaps before higher rollout or Agent telemetry SLOs.
Repository evidence does not become deployed canary evidence.

## Execution Preconditions

1. Preserve the complete dirty worktree. Do not reset, delete, rewrite,
   stage, commit, or clean unrelated tracked or untracked files.
2. Use the repository Python 3.11 interpreter for Python gates.
3. Run PostgreSQL tests only against the configured test database and unique
   safe table prefixes.
4. Do not call a real LLM. Use deterministic providers, injected callbacks,
   barriers, fake recorders, and isolated PostgreSQL tables.
5. Keep these committed values unchanged:

   ```text
   INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
   REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
   INTERVIEW_LANGGRAPH_RUNTIME_ENABLED=true
   REPORT_LANGGRAPH_RUNTIME_ENABLED=true
   LANGGRAPH_STRICT_MSGPACK=true
   ```

6. Preserve `agent-runtime-v1` and all current public AgentRun fields.
7. Preserve both Durable State schemas, graph versions, node names, edges,
   interrupt locations, thread identities, and checkpoint behavior.
8. Do not add raw provider, prompt, answer, resume, report, evidence content,
   exception message, DSN, token, SQL parameter, or absolute-path data to
   AgentRun records, logs, signals, artifacts, or browser output.
9. Treat earlier acceptance as historical evidence after the first source
   change. Restore readiness only after the complete Stage 47.2 gate.

## Scope

- Capture a deep `AgentExecutionContext` snapshot at Runner call time.
- Preserve it across normal, fallback, failed, and cancelled paths.
- Define one Agent safe-metadata policy for every recorder backend.
- Validate metadata before `AgentRunRecord` construction.
- Isolate metadata/classification helper failures from business results.
- Log recorder/helper failures with stable, privacy-safe codes.
- Preserve best-effort recorder behavior.
- Make Runner initialization and recorder registration thread-safe.
- Prove every official production Agent path receives the process Runner.
- Document and test single ownership of Knowledge AgentRun emission.
- Define operation-level AgentRun aggregates and supporting indexes.
- Extend unit, PostgreSQL, privacy, browser, and release-contract gates.

## Non-Goals

- Do not change LangGraph nodes, edges, State schemas, or interrupts.
- Do not add `agent-runtime-v2`.
- Do not change provider retry, fallback output, scoring, or report quality.
- Do not make Agent classes import `app.services.runtime`.
- Do not move retrieval details into `safe_metadata`.
- Do not remove or merge `KnowledgeTraceRecorder`.
- Do not add Agent roles or dynamic routing.
- Do not introduce an async telemetry daemon, fire-and-forget thread, Kafka
  topic, or new Outbox event.
- Do not add a PostgreSQL connection pool; Stage 48 owns that work.
- Do not add AgentRun retention or destructive cleanup.
- Do not expose `safe_metadata` through public APIs or browser pages.
- Do not change `langgraph-canary-v2` or ownership signal codes.
- Do not run a staging/production canary.
- Do not change committed rollout defaults from `0/0`.

## File Map

**Core runtime and privacy policy:**

- Modify: `app/services/agent_runtime.py`
- Modify: `app/services/trace_sanitization.py`
- Modify: `app/services/agent_recorders.py`
- Modify: `app/services/runtime.py`

**Production composition and ownership:**

- Modify only if failing tests prove it necessary: `app/services/prep.py`
- Modify only if failing tests prove it necessary: `app/agents/knowledge.py`
- Audit: `app/agents/examiner.py`
- Audit: `app/agents/orchestrator.py`
- Audit: `app/agents/shadow_reviewer.py`
- Audit: `app/agents/report_coach.py`
- Audit: `app/services/report_tasks.py`
- Audit: `app/services/report_microbatch.py`
- Audit: `app/services/round_review_runner.py`
- Audit: `app/services/runtime_event_consumer.py`

**PostgreSQL ledger and aggregation:**

- Modify: `app/services/postgres_runtime_control.py`
- Modify: `tests/test_agent_recorders.py`
- Create if Task 10 remains in this stage:
  `tests/test_agent_runtime_metrics_postgres.py`

**Tests and acceptance:**

- Modify: `tests/test_agent_runtime.py`
- Modify: `tests/test_agent_trace.py`
- Modify: `tests/test_agent_runtime_audit.py`
- Modify: `tests/test_agents.py`
- Modify: `tests/test_prep_service.py`
- Create: `tests/test_agent_runtime_hardening.py`
- Create: `tests/test_agent_runtime_composition.py`
- Create: `tests/test_agent_runtime_release_contract.py`
- Modify: `scripts/audit_agent_runtime.py`
- Create: `scripts/agent_runtime_stage47_2_acceptance.py`
- Modify: `docs/local-v1-runbook.md`
- Create: `docs/agent-runtime-telemetry-hardening-acceptance.md`

## Fixed Decisions

1. **The entry snapshot is authoritative.** `run()` snapshots before provider
   execution. `stream()` snapshots when the public method is called, not at
   the first `next()`.
2. **Deep snapshot provides correctness.** `frozen=True` alone cannot freeze
   nested lists. Stage 47.2 keeps v1 field types.
3. **Streaming uses an outer factory and inner generator.** A generator body
   is lazy, so the public method must snapshot before returning an iterator.
   The outer method does not start the persisted `started_at` or
   `perf_counter` clocks. The private generator starts both when it is first
   advanced, preserving the existing definition of stream latency as active
   iteration time and excluding idle time between `stream()` and `next()`.
4. **Metadata is normalized once.** Every recorder receives the same
   sanitized dictionary. For the `safe_metadata` subtree,
   `sanitize_agent_safe_metadata()` must be at least as strict as
   `sanitize_trace_payload(..., blocked_keys=AGENT_TRACE_BLOCKED_KEYS)`.
   Shared blocked-key predicates and parity tests enforce that relationship;
   the file sanitizer remains defense in depth for the complete record.
5. **Metadata is bounded machine data.** Allow bounded scalars and bounded
   collections of safe machine values; reject prose, paths, arbitrary
   objects, and sensitive key names.
6. **Sanitization fails closed for fields, not business execution.** Unsafe
   fields are omitted and a safe diagnostic is emitted.
7. **Rejected values never enter logs.** Diagnostics may contain run ID,
   Agent, operation, stable code, and rejected count only.
8. **Metadata callback failure is telemetry failure.** Use empty metadata and
   `agent_metadata_extraction_failed`; never replace a successful output.
9. **Classifier callback failure is telemetry failure.** Fall back to
   `completed` and `agent_outcome_classification_failed`; never invoke
   provider fallback or Graph retry.
10. **Provider/fallback semantics remain unchanged.**
    If a fallback is selected but its output iterator later fails, retain the
    original stable `fallback_reason`: it records why fallback was attempted,
    not proof that fallback delivery completed. `status="failed"` plus the
    iterator exception class in `error_code` records the failed delivery.
11. **Recorder failures remain best effort.** `_emit()` never replaces the
    business result but records `agent_run_emission_failed`.
12. **Composite owns child failures.** The Runner does not double-log a child
    failure that Composite absorbed.
13. **No exception message is logged.** Exception class may be recorded only
    as a bounded machine value.
14. **Production injection is explicit.** `runtime.py` remains the
    composition root; Agents do not lazily import it.
15. **Standalone behavior remains explicit.** Tests/CLI may use trace-only
    Runners, but official production paths must be enumerated.
16. **Knowledge has one AgentRun owner.** `prepare_interview()` owns
    `knowledge/generate_plan`; KnowledgeAgent owns plan/retrieval behavior;
    KnowledgeTrace owns detailed retrieval diagnostics.
17. **KnowledgeTrace remains separate.** Multiple retrieval records may link
    to one AgentRun through `prep_run_id == correlation_id`.
18. **Global initialization is atomic.** One lock protects Runner creation,
    Composite creation, and PostgreSQL recorder registration.
    The canonical Stage 47.2 PostgreSQL recorder identity is the non-sensitive
    runtime `table_prefix`. Test doubles without a table prefix may use
    object identity under a documented process-lifetime fallback. Multiple
    configured DSNs for the same table prefix are outside Stage 47.2 and must
    not be inferred by hashing or logging a DSN.
19. **No capacity claim.** Stage 47.2 may measure recorder latency but Stage
    48 owns pool and connection-budget readiness.
20. **Operation metrics are read models.** They do not enter Agent, Graph,
    Outbox, or public session State.
21. **Repository readiness is not production evidence.** Final repository
    status may be `READY_FOR_AGENT_TELEMETRY_CANARY`; production stays
    `NOT_RUN`.

## Required Semantics

### Synchronous execution

```text
run(context, invoke, metadata, classify)
        |
        +-- deep-copy context immediately
        |
        +-- invoke raises
        |      +-- no fallback -> emit failed -> re-raise provider error
        |      +-- fallback raises -> emit failed -> re-raise fallback error
        |      +-- fallback returns -> safe metadata -> emit degraded -> return
        |
        +-- invoke returns
               -> best-effort classify (completed on helper failure)
               -> best-effort metadata (empty on helper failure)
               -> sanitize -> emit -> return original output
```

### Streaming execution

```text
stream(context, ...)
        |
        +-- public call deep-copies context
        +-- returns private iterator
                +-- provider yields -> forward chunks
                +-- provider fails/no fallback -> failed
                +-- provider fails/fallback -> degraded while fallback yields
                +-- fallback iterator fails -> failed
                +-- GeneratorExit -> cancelled/client_disconnected
                +-- finally -> one best-effort record
```

### Metadata decision matrix

| Input | Persisted metadata | Business result | Diagnostic |
| --- | --- | --- | --- |
| Valid bounded machine values | Preserved | Unchanged | None |
| Blocked exact/sub-string key | Field omitted | Unchanged | Rejected count |
| Long prose or absolute path | Field omitted | Unchanged | Rejected count |
| Unsupported object | Field omitted | Unchanged | Rejected count |
| Callback raises | Empty metadata | Unchanged | Extraction failed |
| Recorder raises | Record may be absent | Unchanged | Emission failed |

---

## Task 1: Freeze the Stage 43 Runtime Baseline

**Files:** `tests/test_agent_runtime.py`, `tests/test_agent_recorders.py`,
`tests/test_agents.py`, `tests/test_prep_service.py`, and new
`tests/test_agent_runtime_release_contract.py`.

1. Add characterization tests for all four statuses, classifier degradation,
   provider/fallback failures, fallback error code, stream chunk counts,
   Composite continuation, PostgreSQL idempotency, public metadata exclusion,
   one Knowledge production record, and durable provider
   `fallback=None`.
2. Prefer behavioral tests over source-string checks.
3. Run:

   ```powershell
   python -m pytest -q `
     tests/test_agent_runtime.py `
     tests/test_agent_recorders.py `
     tests/test_agents.py `
     tests/test_prep_service.py `
     tests/test_agent_runtime_release_contract.py
   ```

4. The baseline must pass before source changes. Record counts in the future
   acceptance document but do not mark Stage 47.2 ready.

## Task 2: Capture an Authoritative Context Snapshot

**Files:** `app/services/agent_runtime.py`, `tests/test_agent_runtime.py`, and
new `tests/test_agent_runtime_hardening.py`.

1. Write a failing synchronous test whose provider changes `session_id` and
   appends an `evidence_id`. The final record must contain original values.
2. Write a failing stream call-time test:

   ```python
   iterator = runner.stream(context, provider)
   context.session_id = "mutated-before-next"
   first = next(iterator)
   ```

   The record must contain the value from the `stream()` call.
3. Add equivalent failed, fallback, and cancelled-path assertions.
4. Confirm failure:

   ```powershell
   python -m pytest -q tests/test_agent_runtime_hardening.py -k "snapshot or mutation"
   ```

5. Add `_snapshot_context(context)` using `model_copy(deep=True)`.
6. Refactor `stream()` into a non-generator public method returning a private
   generator so capture is not deferred to first iteration.
7. Preserve the existing latency contract explicitly:

   ```python
   def stream(self, context, invoke, *, fallback=None):
       snapshot = _snapshot_context(context)  # public call time
       return self._stream_impl(snapshot, invoke, fallback=fallback)

   def _stream_impl(self, snapshot, invoke, *, fallback=None):
       started_at = utc_now_iso()  # first next()
       started = perf_counter()    # first next()
       ...
   ```

   `started_at` and `latency_ms` continue to describe active stream
   consumption, not the idle interval after iterator construction. Do not add
   a persisted `observed_at` field to `agent-runtime-v1`. Add a deterministic
   fake-clock test proving an artificial delay between `stream()` and
   `next()` is excluded from `latency_ms`.
8. Evaluate `ConfigDict(frozen=True)` only after compatibility tests. Keep the
   deep snapshot regardless, and do not change `evidence_ids` to tuple in v1.
9. Run all Agent Runtime tests.

## Task 3: Define One Safe-Metadata Write Policy

**Files:** `app/services/trace_sanitization.py`,
`app/services/agent_runtime.py`, `tests/test_agent_trace.py`,
`tests/test_agent_runtime_audit.py`, and
`tests/test_agent_runtime_hardening.py`.

1. Inventory and characterize legitimate current metadata, including
   question/feedback counts, knowledge status, report path, quality-repair
   flags, emitted chunks, microbatch counts, and bounded question-ID arrays.
2. Write failing tests for exact and substring sensitive keys, nested payload,
   Windows/POSIX paths, long prose, unsupported objects, excessive depth,
   excessive key count, and excessive list length.
3. Add a dedicated `sanitize_agent_safe_metadata()` that returns sanitized
   JSON-compatible metadata plus rejected-field counts/categories.
4. Apply case-insensitive blocked keys/parts, maximum depth/items/string
   length, safe-machine-string validation, path rejection, and unsupported
   object rejection. Never call `str(object)` on a rejected object.
5. Define and test the relationship with `sanitize_trace_payload()`:

   - use the same `AGENT_TRACE_BLOCKED_KEYS` source for exact blocked keys;
   - for every key removed from the `safe_metadata` subtree by the existing
     Agent trace policy, the new sanitizer must also remove it;
   - the new sanitizer may be stricter through blocked-key substrings, value
     shape, depth, count, path, and machine-string constraints;
   - the full-record file sanitizer remains in place because it also protects
     fields outside `safe_metadata`;
   - add a parity/property matrix so future edits cannot make the write-time
     sanitizer weaker than the file defense.

6. Sanitize before `AgentRunRecord` construction. Keep file-recorder cleanup
   only as defense in depth.
7. Prove one malicious payload produces the same sanitized result in a
   capturing recorder, file JSON, and PostgreSQL row; public queries still
   expose none of it.
8. Do not change Knowledge trace behavior without its own characterization
   tests.
9. Run:

   ```powershell
   python -m pytest -q `
     tests/test_agent_runtime_hardening.py `
     tests/test_agent_trace.py `
     tests/test_agent_recorders.py `
     tests/test_agent_runtime_audit.py
   ```

## Task 4: Isolate Metadata and Outcome Helper Failures

**Files:** `app/services/agent_runtime.py`, `tests/test_agent_runtime.py`, and
`tests/test_agent_runtime_hardening.py`.

1. Write tests where provider succeeds but `metadata()` raises. Assert the
   original output, one record attempt, empty metadata, correct status, one
   `agent_metadata_extraction_failed` warning, and no exception text.
2. Repeat for successful fallback output.
3. Write tests where provider succeeds but `classify()` raises. Assert
   original output, `completed`, one
   `agent_outcome_classification_failed` warning, and independent metadata
   extraction.
4. Confirm current tests fail because helper exceptions escape.
5. Add internal `_resolve_outcome()`, `_resolve_safe_metadata()`, and
   `_warn_telemetry_failure()` boundaries.
6. Do not invoke provider fallback for helper failures and do not change
   LangGraph failure classification.
7. Rerun every existing failed/degraded test to prove error codes still come
   from provider or fallback failures.

## Task 5: Make Top-Level Emission Failure Observable

**Files:** `app/services/agent_runtime.py`,
`app/services/agent_recorders.py`, `tests/test_agent_runtime.py`,
`tests/test_agent_recorders.py`, and
`tests/test_agent_runtime_hardening.py`.

1. Inject a direct recorder whose exception message contains a fake DSN,
   token, prompt, answer, and absolute path.
2. Assert `run()` still returns and `stream()` still yields, while exactly one
   warning includes only run ID, Agent, operation, and
   `agent_run_emission_failed`.
3. Assert Composite child failure produces only its existing child warning,
   not a Runner duplicate.
4. Add a module logger and one centralized safe-warning helper. Do not use
   `logger.exception` or `str(exc)`.
5. Keep `_emit()` best effort and do not add retry inside the response path.
6. Run focused recorder and privacy tests.

## Task 6: Preserve and Simplify Streaming Semantics

**Files:** `app/services/agent_runtime.py`, `tests/test_agent_runtime.py`, and
`tests/test_agent_runtime_hardening.py`.

1. Complete a state matrix for: zero/multiple provider chunks; failure before
   and after partial chunks; fallback completion; fallback iterator failure;
   close during provider/fallback iteration; and recorder failure.
2. Assert status, fallback reason, error code, emitted chunks, propagation,
   and exactly one emission attempt for every row.
3. Refactor only after the matrix passes. Handle `GeneratorExit` directly;
   never classify it as provider `Exception`.
4. Preserve and document the current failed-fallback-iterator semantics:

   ```text
   status = failed
   fallback_reason = the stable reason that caused fallback selection
   error_code = the fallback iterator failure class
   ```

   `fallback_reason` answers "why was fallback attempted"; it does not claim
   that fallback delivery succeeded. Do not clear it and do not introduce a
   new compound reason such as `fallback_delivery_failed:<reason>` in
   `agent-runtime-v1`.
5. Verify Examiner standalone and durable `stream_followup_attempt()` paths.

## Task 7: Enforce the Production Runner Composition Contract

**Files:** new `tests/test_agent_runtime_composition.py`; modify production
boundaries only when failing tests prove it necessary.

1. Enumerate official paths: both Prep endpoints; legacy/durable Examiner;
   round Review; full-session/microbatch Report Coach; Report Worker retry;
   Local/Celery runtime-event consumers.
2. Inject an identifying Runner/Recorder and deterministic provider. Assert
   every official path emits through it.
3. A production path that constructs an unconfigured
   `AgentExecutionRunner()` must fail the contract.
4. Keep standalone Agent tests/CLI explicit and trace-only where intended.
5. Add a release assertion that `app/agents/` does not import
   `app.services.runtime`.
6. Do not mechanically remove every default Runner. Change only proven
   production boundaries.
7. Run API, Prep, round-review, report-task, and microbatch integration tests.

## Task 8: Make Runner Initialization and Recorder Registration Atomic

**Files:**

- Modify: `app/services/runtime.py`
- Modify: `tests/test_runtime_provider.py`
- Create/Modify: `tests/test_agent_runtime_composition.py`

### Step 1: Write real-overlap tests

Use `threading.Barrier`, not two fast uncoordinated calls, to prove concurrent
entry into:

- the first `get_agent_execution_runner()` call;
- the first call with a PostgreSQL control store;
- repeated calls with the same control store;
- calls with two Store objects using the same `table_prefix`;
- calls with Store objects using distinct table prefixes.

Required assertions:

- one `AgentExecutionRunner` instance is returned to all callers;
- one `CompositeAgentRunRecorder` is retained;
- one runtime table prefix contributes one PostgreSQL recorder even when
  represented by multiple Store objects;
- distinct, explicitly authorized table prefixes can each contribute one
  recorder;
- no caller observes a partially initialized global;
- reset helpers clear all registration state under the same lock.

### Step 2: Confirm the current race is observable

Introduce a test-only barrier/factory hook rather than using sleeps. The
current unlocked initialization should fail an exact construction-count
assertion.

### Step 3: Add one initialization lock

Protect Runner creation, Composite creation, registration lookup, recorder
addition, and registration update as one decision boundary.

Do not hold the lock during `record()` calls. `CompositeAgentRunRecorder`
continues to copy its recorder list under its own short-held lock.

### Step 4: Replace raw object-ID registration with table-prefix identity

Use `control_store.table_prefix` as the canonical Stage 47.2 recorder
identity. It is already available, stable for the logical runtime tables, and
does not expose connection credentials.

Required behavior:

- two Store objects for the same `table_prefix` register one PostgreSQL
  recorder;
- different table prefixes register distinct recorders;
- test doubles without `table_prefix` may fall back to `id(store)` under an
  explicit process-lifetime-only branch;
- reset clears both canonical and fallback identities;
- no DSN, DSN hash, credential-derived value, or exception text is used as an
  identity or logged.

Stage 47.2 assumes one configured runtime database per process. Supporting
multiple DSNs with the same table prefix belongs to the Stage 48 connection-
domain design; do not over-design that case here.

### Step 5: Run concurrency and reset tests

```powershell
python -m pytest -q `
  tests/test_agent_runtime_composition.py `
  tests/test_runtime_provider.py `
  -k "runner or recorder or concurrent or reset"
```

## Task 9: Make Knowledge AgentRun Ownership Explicit

**Files:**

- Modify: `tests/test_prep_service.py`
- Modify: `tests/test_agents.py`
- Modify: `tests/test_knowledge_trace.py`
- Modify only if required: `app/services/prep.py`
- Modify only if required: `app/agents/knowledge.py`
- Modify: `tests/test_agent_runtime_release_contract.py`

### Step 1: Write the single-owner call-count proof

For one production `prepare_interview()` execution, assert:

- one provider `generate_plan()` call;
- one `AgentRunRecord`;
- `agent == "knowledge"`;
- `operation == "generate_plan"`;
- AgentRun correlation ID equals the plan's `prep_run_id`;
- zero or more Knowledge retrieval trace rows/files use that same Prep ID;
- no second inner AgentRun is created.

Cover normal grounded, degraded grounding, provider fallback, and legacy
LLM-without-vector-store paths.

### Step 2: Characterize direct KnowledgeAgent usage

Direct `KnowledgeAgent.generate_plan()` is a domain/test API, not an official
production telemetry boundary. Add a test/documentation note so future code
does not infer that it automatically persists AgentRun records.

If direct production use is later required, it needs a separate ownership
change that removes the outer wrapper. Do not wrap both layers.

### Step 3: Keep retrieval details out of Agent metadata

Agent metadata may contain bounded aggregate fields such as:

```text
question_count
knowledge_status
retrieval_query_count
retrieval_hit_count
```

It must not contain query text, role profile prose, chunk content, raw scores
maps keyed by arbitrary input, resume signals, or provider context.

### Step 4: Run Knowledge continuity and privacy gates

```powershell
python -m pytest -q `
  tests/test_agents.py `
  tests/test_grounded_knowledge_agent.py `
  tests/test_knowledge_trace.py `
  tests/test_prep_service.py `
  tests/test_agent_runtime_release_contract.py
```

## Task 10: Add Operation-Level AgentRun Aggregation

**Files:**

- Modify: `app/services/postgres_runtime_control.py`
- Modify: `tests/test_agent_recorders.py`
- Create: `tests/test_agent_runtime_metrics_postgres.py`
- Modify if operator commands are added: `docs/local-v1-runbook.md`

### Step 1: Define the read contract before the index

Add an internal/operator-only query contract with:

- required bounded time window;
- optional Agent;
- optional operation;
- maximum result-group count;
- invocation count;
- completed/degraded/failed/cancelled counts and rates;
- fallback count/rate;
- P50/P95/P99 latency where PostgreSQL is available;
- minimum/maximum observed timestamp.

Never return `safe_metadata`, evidence IDs, session IDs, question IDs,
command IDs, or correlation IDs from aggregate rows.

### Step 2: Write isolated PostgreSQL tests

Insert deterministic AgentRun rows across:

- two Agents;
- two operations for one Agent;
- all four statuses;
- multiple attempts;
- rows inside and outside the requested time window.

Assert exact counts, rates, percentiles, filtering, ordering, and bounds.
Assert a zero-row window returns a stable empty result without division by
zero.

### Step 3: Add the supporting index

Make catalog structure the mandatory repository gate. Query `pg_indexes` or
the equivalent PostgreSQL catalogs to prove the indexed table, ordered
columns, access method, and absence of an accidental duplicate. Do not rely
only on an index name. The likely candidate is:

```sql
CREATE INDEX ... ON agent_runs (agent, operation, started_at)
```

If the final query always filters status, justify an alternative such as:

```sql
(agent, operation, status, started_at)
```

Aggregation correctness tests may use small isolated tables and must not
assert `Index Scan`: PostgreSQL correctly prefers a sequential scan for small
relations.

`EXPLAIN` planner-choice validation is optional diagnostic/capacity evidence,
not a Stage 47.2 release blocker. Run it only in a separately marked
integration/capacity test when:

- the isolated `agent_runs` table contains at least 10,000 representative
  rows, or an explicit capacity fixture provides an equivalent volume;
- PostgreSQL statistics have been refreshed with `ANALYZE`;
- the test records the row count and query shape without recording identity
  data.

Do not bulk-insert thousands of rows into every unit/PostgreSQL contract run
solely to force a planner choice. Stage 48 owns repeatable planner/capacity
evidence under production-like volumes.

### Step 4: Keep Stage 48 ownership clear

This task may add the read model and index, but it cannot:

- introduce a connection pool;
- declare connection capacity safe;
- raise rollout;
- add a background metrics scraper;
- claim P95 recorder overhead readiness.

If query/index verification exposes a material capacity dependency, retain
the tested query contract and defer the physical index/capacity acceptance to
Stage 48 with an explicit note.

### Step 5: Run PostgreSQL metrics tests

```powershell
python -m pytest -q `
  tests/test_agent_recorders.py `
  tests/test_agent_runtime_metrics_postgres.py
```

The mandatory PASS covers aggregate correctness and catalog index structure.
Report optional `EXPLAIN` diagnostics separately as `NOT_RUN` unless the
capacity fixture is explicitly enabled; optional planner evidence must not be
misreported as a skipped mandatory gate.

## Task 11: Strengthen Privacy Audit and Public Non-Exposure

**Files:**

- Modify: `scripts/audit_agent_runtime.py`
- Modify: `tests/test_agent_runtime_audit.py`
- Modify: `tests/test_agent_trace.py`
- Modify: `tests/test_agent_recorders.py`
- Modify: `tests/test_api.py`
- Modify only if coverage requires it:
  `tests/browser/reference-ui.spec.js`

### Step 1: Add an adversarial metadata matrix

Cover at least:

- `prompt`;
- `user_prompt`;
- `provider_response_debug`;
- `dsn_backup`;
- `token_value`;
- `answer_summary`;
- `resume_digest_source`;
- nested `payload/content/answer`;
- a long natural-language note;
- Windows and POSIX absolute paths;
- URL-like credential text;
- arbitrary Pydantic/domain objects;
- excessive nesting and collection sizes.

The audit fixture may contain fake secrets, but failure output and acceptance
artifacts must not echo them.

### Step 2: Align audit and write-time policy

The audit remains independent defense in depth. It must be at least as strict
as the write-time policy and detect a deliberately bypassed/hand-constructed
unsafe `AgentRunRecord`.

Do not weaken the audit merely because normal Runner writes are sanitized.

### Step 3: Prove public exclusion

Assert:

- `list_agent_runs()` omits `safe_metadata`;
- API runtime traces omit it;
- browser JavaScript does not render or stringify it;
- aggregate endpoints/read models expose only counts, rates, latency, Agent,
  operation, status, and bounded timestamps;
- logs contain stable codes but no raw rejected value.

### Step 4: Run audit gates

```powershell
python -m pytest -q `
  tests/test_agent_runtime_audit.py `
  tests/test_agent_trace.py `
  tests/test_agent_recorders.py `
  tests/test_api.py
```

If browser source coverage changes:

```powershell
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
```

## Task 12: Run the Combined Fault and Concurrency Matrix

**Files:**

- Create/Modify: `tests/test_agent_runtime_hardening.py`
- Create/Modify: `tests/test_agent_runtime_composition.py`
- Create/Modify: `tests/test_agent_runtime_metrics_postgres.py`

Use deterministic fakes and barriers. Do not use real providers or timing-only
races.

| Scenario | Required output | Required AgentRun/diagnostic |
| --- | --- | --- |
| Provider succeeds, recorder succeeds | Original output | One completed record |
| Provider succeeds, metadata raises | Original output | Completed, empty metadata, one safe warning |
| Provider succeeds, classifier raises | Original output | Completed, one safe warning |
| Provider succeeds, file recorder fails | Original output | Other recorder continues, one child warning |
| Provider succeeds, top recorder fails | Original output | One emission warning |
| Provider fails, fallback succeeds | Fallback output | One degraded record |
| Provider fails, fallback fails | Fallback exception | One failed record |
| Fallback iterator fails after selection | Iterator exception | Failed; original fallback reason retained; iterator error code |
| Stream consumer closes | `GeneratorExit`/closed iterator | One cancelled record |
| Original Context mutates | Normal behavior | Record uses entry snapshot |
| Iterator is idle before first `next()` | Normal behavior | Snapshot is call-time; latency starts at first `next()` |
| Two threads initialize Runner | Normal behavior | One Runner/Composite |
| Two Store objects share one table prefix | Normal behavior | One PostgreSQL recorder |
| Knowledge outer/inner overlap attempted | One plan result | One logical AgentRun |
| Metadata contains secrets/prose/path | Original output | Unsafe fields absent everywhere |
| PostgreSQL recorder unavailable | Original output after bounded failure | Safe recorder warning |
| Aggregate window empty | Empty aggregate | No divide-by-zero/data leak |

Additional assertions:

- exact provider call counts;
- exact fallback call counts;
- exact recorder call counts;
- no duplicate warnings;
- no exception message or payload in captured logs;
- no unbounded thread/process remains after tests;
- isolated PostgreSQL tables are removed in `finally`.

## Task 13: Add Acceptance Runner and Operator Documentation

**Files:**

- Create: `scripts/agent_runtime_stage47_2_acceptance.py`
- Create: `docs/agent-runtime-telemetry-hardening-acceptance.md`
- Modify: `docs/local-v1-runbook.md`
- Create/Modify: `tests/test_agent_runtime_release_contract.py`
- Modify: `tests/test_local_v1_docs.py`

### Step 1: Create a deterministic acceptance runner

The runner must execute or consume machine-readable results for:

- Agent Runtime unit/fault matrix;
- production composition contract;
- privacy audit;
- PostgreSQL Agent Ledger/aggregate tests;
- Stage 43A/43B regression;
- Stage 46/47/47.1 LangGraph regression subset;
- rollout-default contract.

It must not:

- call a real provider;
- print DSNs or environment secrets;
- mutate rollout defaults;
- claim deployed observation;
- silently treat skipped PostgreSQL gates as PASS.

Suggested output:

```json
{
  "status": "READY_FOR_AGENT_TELEMETRY_CANARY",
  "operator_observation": "NOT_RUN",
  "agent_runtime_schema": "agent-runtime-v1",
  "rollout_defaults_changed": false,
  "checks": {
    "runtime_unit": "PASS",
    "composition": "PASS",
    "privacy": "PASS",
    "postgres": "PASS",
    "langgraph_regression": "PASS"
  }
}
```

If PostgreSQL is unavailable, the runner must return a non-ready repository
status such as `BLOCKED_POSTGRES_GATE`, not fabricate readiness.

### Step 2: Write the acceptance record

Before gates finish:

```text
Status: IN_PROGRESS
Production observation: NOT_RUN
```

Only after every mandatory gate:

```text
Status: READY_FOR_AGENT_TELEMETRY_CANARY
Production observation: NOT_RUN
```

The record must distinguish:

- repository tests;
- local deterministic rehearsal;
- deployed canary observation.

### Step 3: Update the local runbook

Document:

- targeted and full gate commands;
- how to enable file traces safely;
- how to query operation aggregates;
- interpretation of completed/degraded/failed/cancelled;
- stable telemetry warning codes;
- metadata rejection semantics;
- how to confirm `safe_metadata` is not public;
- temporary table/trace cleanup;
- rollback conditions.

### Step 4: Add release-contract tests

Assert:

- acceptance status cannot be ready while a mandatory gate is skipped/failed;
- production observation remains `NOT_RUN`;
- schema remains `agent-runtime-v1`;
- rollout defaults remain `0/0`;
- no Agent imports the runtime composition root;
- Stage 48 remains the named connection-capacity owner.

## Task 14: Run Final Repository Gates

Run in order and stop on the first real failure.

### Gate 1: Focused Agent Runtime

```powershell
python -m pytest -q `
  tests/test_agent_runtime.py `
  tests/test_agent_runtime_hardening.py `
  tests/test_agent_trace.py `
  tests/test_agent_recorders.py `
  tests/test_agent_runtime_audit.py `
  tests/test_agent_runtime_composition.py `
  tests/test_agent_runtime_release_contract.py
```

### Gate 2: Agent integrations

```powershell
python -m pytest -q `
  tests/test_agents.py `
  tests/test_prep_service.py `
  tests/test_grounded_knowledge_agent.py `
  tests/test_knowledge_trace.py `
  tests/test_round_review.py `
  tests/test_report_tasks.py `
  tests/test_report_microbatch.py `
  tests/test_durable_interview_graph.py `
  tests/test_durable_review_graph.py
```

### Gate 3: PostgreSQL Agent Ledger

```powershell
python -m pytest -q `
  tests/test_agent_recorders.py `
  tests/test_agent_runtime_metrics_postgres.py
```

PostgreSQL tests must actually run. A skipped marker is not PASS.

### Gate 4: LangGraph recovery and fencing regression

Run the configured PostgreSQL marker set covering:

```text
langgraph_recovery
langgraph_review_recovery
langgraph_fencing_canary
langgraph_heartbeat_recovery
```

No Durable State or Graph topology changes are expected.

### Gate 5: Full Python

```powershell
python -m pytest -q
```

Only previously accepted environment-conditional skips are allowed. Record
the exact passed/skipped/warning counts.

### Gate 6: CSS and browser

```powershell
npm.cmd run build:prototype-css
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
```

Real-model smoke tests remain opt-in and skipped. Do not take screenshots
unless separately requested.

### Gate 7: Static and mechanical

```powershell
python -m compileall -q app scripts tests
git diff --check
rg -n "^INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=|^REPORT_LANGGRAPH_ROLLOUT_PERCENT=" .env.example
```

Expected rollout values:

```text
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
```

### Gate 8: Cleanup

Verify and remove only Stage 47.2-owned:

- isolated PostgreSQL `test_agent_*` tables;
- temporary trace directories;
- Playwright `test-results`;
- acceptance scratch artifacts;
- owned browser/web-server processes and ports.

Resolve and verify absolute paths before recursive deletion. Preserve all
pre-existing user files and dirty-worktree changes.

### Gate 9: Acceptance runner

```powershell
python -m scripts.agent_runtime_stage47_2_acceptance
```

Expected repository status:

```text
READY_FOR_AGENT_TELEMETRY_CANARY
```

Expected production status:

```text
NOT_RUN
```

## Acceptance Criteria

Stage 47.2 is complete only when all statements are true:

1. In-flight mutation of the original Context cannot alter any emitted
   AgentRun context field.
2. `stream()` captures Context at public call time, before first iteration.
3. Stream `started_at` and `latency_ms` still begin at first iterator
   advancement and exclude idle time after iterator construction.
4. Metadata callbacks cannot place blocked, prose, path, arbitrary-object, or
   unbounded data into capturing, file, or PostgreSQL recorders.
5. The write-time safe-metadata sanitizer is provably no weaker than the
   existing Agent file-trace blocked-key policy for the metadata subtree.
6. File and PostgreSQL recorders receive the same sanitized metadata.
7. Metadata callback failure does not change successful provider/fallback
   output.
8. Classifier callback failure does not change successful provider output or
   trigger fallback/retry.
9. Top-level recorder failure is observable through one privacy-safe warning
   and does not change business output.
10. Composite child failure remains independently observable without duplicate
   Runner warnings.
11. Completed/degraded/failed/cancelled semantics and stream chunk counts are
   unchanged.
12. A failed fallback iterator retains the stable reason that selected
    fallback while `status` and `error_code` identify delivery failure.
13. Every official production Agent path uses the process-wide injected
    Runner.
14. No Agent module imports `app.services.runtime`.
15. Concurrent first access produces one Runner and one Composite.
16. One runtime `table_prefix` contributes at most one PostgreSQL recorder.
17. One production Knowledge provider call produces one logical AgentRun and
    remains correlated with separate Knowledge retrieval traces.
18. Operation aggregates are bounded, privacy-safe, and correct across all
    four statuses and time windows.
19. Catalog tests prove the operation-level index structure; optional
    production-like `EXPLAIN` evidence is reported separately and is not
    fabricated from a tiny test table.
20. Public APIs/browser output still exclude `safe_metadata`.
21. `agent-runtime-v1` remains unchanged.
22. Both Durable State schemas and LangGraph topologies remain unchanged.
23. Stage 43, 46, 47, and 47.1 regressions pass.
24. Full Python, PostgreSQL, privacy, CSS, browser, compile, diff, and cleanup
    gates pass.
25. Committed rollout defaults remain `0/0`.
26. Repository status is `READY_FOR_AGENT_TELEMETRY_CANARY`.
27. Production observation remains `NOT_RUN` unless a deployed environment is
    separately authorized and actually observed.

## Rollback Triggers

Rollback the Stage 47.2 source changes, without resetting unrelated work, if
any of these occur:

- a successful provider result becomes failed because telemetry code raises;
- current fallback or Graph retry semantics change;
- a production Agent path stops emitting its existing record;
- one provider call starts producing duplicate logical AgentRuns;
- sanitized metadata differs between file and PostgreSQL backends;
- raw metadata, exception text, DSN, token, prompt, answer, resume, report
  content, or absolute path reaches logs or persisted records;
- Stream cancellation is classified as failed;
- initialization locking introduces deadlock or holds a lock during recorder
  I/O;
- PostgreSQL aggregation exposes identity-level fields;
- either Durable State schema or Graph topology changes;
- committed rollout defaults differ from `0/0`.

Rollback means reverting only the Stage 47.2-owned edits through a reviewed
patch or commit revert. Never use destructive workspace reset commands.

## Explicit Post-Stage-47.2 Backlog

- **Stage 48 — PostgreSQL connection ownership and capacity:** Separate
  bounded connection domains for Checkpointer, business SQL, advisory locks,
  and telemetry; add pools, timeouts, budgets, pool metrics, and recorder
  latency capacity evidence. Higher rollout still requires Stage 48.
- **Stage 49 — Checkpoint/AgentRun lifecycle and privacy governance:** Define
  retention, age/capacity metrics, dry-run cleanup, backup/restore, legal
  policy, and deletion safety for completed workflow and AgentRun data.
- **Agent Runtime v2:** Consider discriminated contexts or typed factory
  methods, immutable tuple fields, schema migration, and stronger
  agent/operation-specific required-field combinations. Do not retrofit these
  incompatibly into v1.
- **Asynchronous recorder delivery:** Evaluate a bounded durable telemetry
  Outbox or queue only with shutdown draining, backpressure, overflow policy,
  and loss accounting. Do not use untracked daemon fire-and-forget.
- **Telemetry SLOs:** Add deployed P50/P95/P99 dashboards, failure/degraded
  rates, recorder-loss alerts, and alert windows after Stage 48 capacity
  evidence.
- **Evidence duplicate strict mode:** If duplicate evidence IDs prove to be a
  quality signal, record only an aggregate duplicate count or introduce a
  validation mode. Do not log raw IDs by default.
- **Knowledge direct-call ownership:** If direct production KnowledgeAgent
  calls become required, redesign the single-owner boundary and remove the
  outer duplicate before moving Runner ownership into the Agent.
- **Production canary:** A deployed telemetry canary needs separate
  authorization, bounded traffic, privacy observation, recorder-latency
  observation, and an explicit recorded result. Repository readiness alone is
  not that evidence.
