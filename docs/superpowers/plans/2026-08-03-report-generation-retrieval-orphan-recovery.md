# Report Generation Retrieval And Orphan Recovery Implementation Plan

> **Document type:** How-to implementation plan  
> **Audience:** Interview-Agent backend, frontend, data, and operations maintainers  
> **Status:** Implemented, committed, and running in preview; durable rollout remains pending  
> **Last updated:** 2026-08-03

**Goal:** Eliminate report jobs that remain indefinitely at knowledge retrieval, make report execution recoverable across API/worker failures, fail fast when embedding or pgvector is unavailable, and give the React UI truthful progress, failure, stall, and retry states.

**Architecture:** Preserve the existing PostgreSQL report-job, lease, worker, pgvector, and durable-review capabilities. Repair the composition boundaries around them: a durable runtime must enqueue a durable job and never fall back silently to a response-coupled task; a preview runtime must use an internally consistent memory/static stack. Every report attempt must reach one terminal or recoverable state, and every active job must have an identity, lease/heartbeat authority, or explicit in-process ownership.

**Tech stack:** Python 3.11, FastAPI, Pydantic v2, PostgreSQL, pgvector, psycopg2, the existing Report Worker and LangGraph review path, SiliconFlow `BAAI/bge-m3`, React/Vite, pytest, and the existing browser test stack.

---

## Execution Record — 2026-08-03

The implementation phase is complete. The durable response-coupled
`BackgroundTasks` fallback has been removed; coherent preview and durable
profiles, non-mutating preflight, a managed in-memory preview queue, static
preview knowledge, explicit enqueue outcomes, terminal-state protection,
independent heartbeat metadata, continuous Worker heartbeat, lease-token-fenced
terminal transitions, orphan detection/requeue, truthful progress fields, and
the React stalled/orphaned/retry experience are implemented.

Verification completed across isolated and restarted preview runtimes:

- Full Python suite: `1713 passed, 166 skipped`.
- Full Playwright browser matrix: `65 passed, 33 intentionally skipped`.
- Frontend production build: passed (`4590` modules transformed).
- Actual PostgreSQL report-job/Worker suite: `18 passed` using random test
  prefixes with cleanup.
- Actual pgvector suite: `9 passed` using isolated tables with cleanup.
- Isolated schema migration: `report_job_heartbeat_v1` applied successfully,
  proved idempotent, and verified both `heartbeat_at` and `lease_expires_at`;
  no `interview_*` production-like relation was migrated.
- Preview preflight: ready.
- Durable preflight: correctly blocked because embedding is disabled and no
  active corpus is available; PostgreSQL, report-job schema, and pgvector are
  reachable.
- `compileall` and `git diff --check`: passed.
- Live preview HTTP flow: a finished interview created a non-null
  `report_job_id`, published `heartbeat_at`, reached `completed / 100%` on its
  first attempt, and returned a readable report.

After explicit approval to restart, the old PID `348` was stopped and its
in-memory incident session was discarded as expected. Port `8000` now runs the
new code as a coherent preview profile using `InMemoryReportJobStore`, an
in-process Worker, and `StaticKnowledgeStore`; runtime configuration and report
runtime readiness both report `true`. The live validation report used session
`b95097bb-9301-404f-96e3-4b47d9487524` and job
`d5836e24-9895-44f8-88e0-55911ec5eb08`.

No production migration, credential change, active-corpus mutation, or durable
Worker rollout has been performed. Those actions remain the durable rollout
boundary and require a passing durable preflight.

---

## 1. Incident Summary

The affected report currently exposes:

```text
session_id=c015fc0f-e9f7-4cf6-bbd4-601d3081661c
report_job_id=null
status=processing
stage=retrieving
percent=20
matched_chunks=null
```

The active runtime reports:

```text
runtime_store=memory
session_store=InterviewSessionStore
report_job_store=PostgresReportJobStore
report_worker=external_process
event_backend=noop
```

The repository default remains `DEFAULT_RUNTIME_STORE = "postgres"`. The mixed
runtime in this incident was introduced by the local `INTERVIEW_RUNTIME_STORE`
environment override rather than by the repository default.

This is an invalid mixed composition:

```text
memory session
  + PostgreSQL report queue
  + external worker authority
  + PostgreSQL/pgvector knowledge store
  + no durable event authority
```

The visible `retrieving / 20%` value is not proof that a retrieval call is still running. `mark_report_processing()` writes that value before the report executor has proved that it owns a job or has started retrieval. When PostgreSQL enqueue fails, `enqueue_report_if_needed()` catches every exception, marks the in-memory report as processing, and schedules a FastAPI `BackgroundTask`. The fallback has no durable job ID, lease, heartbeat, or worker recovery path.

The launch environment also defaults to:

```text
EMBEDDING_PROVIDER=disabled
```

In that configuration, `PgVectorKnowledgeStore.search()` cannot produce a query vector and raises `EmbeddingConfigurationError("embedding provider is disabled")`.

Finally, `run_report_generation()` currently contains:

```python
except ValueError:
    return None
```

This can leave the last progress projection untouched rather than persisting a terminal failure.

### Verified active defects and existing capabilities

The following facts were rechecked against the current workspace after review:

- Both `InterviewSessionStore.mark_report_processing()` and
  `PostgresInterviewSessionStore.mark_report_processing()` write
  `retrieving / 20%` before report execution ownership is proven.
- Both in-memory and PostgreSQL `fail_report()` implementations write a failed
  record unconditionally. A stale failure can therefore overwrite a completed
  report and move the session review phase back to failed. This is a critical
  correctness bug, not only a future idempotency improvement.
- `PostgresReportJobStore` already implements durable enqueue, claim, lease
  renewal through `heartbeat()`, retry, requeue, failed-state handling, and
  orphan-processing repair. This plan must harden and compose those existing
  capabilities rather than rebuild them.
- The current report-job schema has `lease_expires_at` and `updated_at`, but no
  independent `heartbeat_at` column. `heartbeat()` currently extends the lease
  and updates `updated_at`. Adding a separately observable heartbeat therefore
  requires an explicit schema migration and query/model updates.
- The current public `events` array is not history. `_report_progress_detail()`
  synthesizes at most one item from the current projection, while
  `ReportProcessingPage.jsx` presents the array as an ordered event ledger.
  This is an active truthfulness bug.
- The existing retry operation is
  `POST /api/interviews/{session_id}/report/requeue`. The implementation plan
  extends this endpoint; it does not introduce a competing `/report/retry`
  endpoint. The UI may still label the action “重新尝试”.
- `ReportRecord` intentionally has no `report_job_id`. The progress route
  resolves the job independently by `session_id` and projects its ID. The
  required fix is to guarantee a resolvable job authority for every active
  report, not to duplicate job ownership inside `ReportRecord` without a
  separate schema decision.
- `frontend/src/api/client.js` exists and already exports `getJson()` and
  `postJson()`. Those helpers are sufficient for polling and requeue unless a
  later implementation proves that a dedicated helper removes real
  duplication.
- `tests/test_session_report_store.py`,
  `tests/test_postgres_session_store.py`, and
  `tests/test_embedding_config.py` all exist. They remain modify targets, not
  create targets. `tests/test_report_progress.py` also exists and is added to
  the progress regression work.
- The custom `<div role="progressbar">` in the React page supplies a name,
  minimum, maximum, current value, and value text. It is a valid ARIA
  progressbar; replacing it with native `<progress>` is optional and is not a
  prerequisite for this remediation.

### Confirmed failure chain

```text
Interview finishes
  -> PostgreSQL report enqueue fails
  -> the enqueue error is swallowed
  -> the report is projected as processing/retrieving/20%
  -> an in-process BackgroundTask is requested
  -> no durable job ID exists
  -> the task does not complete or exits outside a safe terminal boundary
  -> no worker can reclaim it
  -> the UI polls the unchanged projection forever
```

---

## 2. Scope

### In scope

- Runtime composition validation for session, report queue, worker, event, and knowledge-store backends.
- Durable report enqueue behavior and explicit preview-mode execution.
- Report task exception classification and terminal-state persistence.
- Job identity, attempt, lease, heartbeat, orphan detection, and controlled retry.
- Embedding, pgvector schema, corpus, and model-dimension preflight.
- A deterministic static knowledge store for explicit frontend preview mode.
- Report progress API fields needed to distinguish active, stalled, failed, retrying, and completed jobs.
- React report-processing states, error messages, polling policy, and retry affordances.
- Unit, API integration, PostgreSQL, worker-recovery, SSE, and browser tests.
- Controlled deployment, recovery, rollback, and acceptance evidence.

### Out of scope

- Redesigning report scoring rules or report content.
- Expanding the production knowledge corpus.
- Changing the six legacy static HTML pages.
- Replacing the existing Vite/React application.
- Automatically running PostgreSQL migrations during API startup.
- Exposing raw answers, resume text, provider payloads, API keys, DSNs, embeddings, or internal stack traces through public APIs.
- Using the current incident session as a reason to inject code into a running Python process.

---

## 3. Safety Constraints For The Current Session

The current backend uses an in-memory session store. Restarting it will lose the affected session.

- [ ] Do not restart the backend before the accessible session snapshot has been exported and reviewed.
- [ ] Do not run a PostgreSQL migration as part of incident inspection.
- [ ] Do not claim that starting the external worker can recover the current report; it has no `report_job_id`.
- [ ] Do not mutate the current process through debugger/injection techniques.
- [ ] Do not overwrite or delete the current in-memory report projection.
- [ ] Record whether the public snapshot contains enough information to recreate a report input after restart.
- [ ] If required internal input is unavailable, state explicitly that exact recovery is not guaranteed and use a new validation interview after deployment.

Export the non-secret public evidence before implementation deployment:

```powershell
Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8000/api/interviews/c015fc0f-e9f7-4cf6-bbd4-601d3081661c' `
  -Method Get

Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8000/api/interviews/c015fc0f-e9f7-4cf6-bbd4-601d3081661c/report/progress' `
  -Method Get

Invoke-RestMethod `
  -Uri 'http://127.0.0.1:8000/api/runtime' `
  -Method Get
```

Do not commit the exported session payload if it contains personal data or interview content.

---

## 4. Required Invariants

The implementation is complete only when every invariant below is enforced in code and tests.

### Runtime composition

- [ ] A PostgreSQL/external-worker runtime uses a durable PostgreSQL session store, report job store, and compatible worker authority.
- [ ] A memory preview runtime uses a memory job store, explicit in-process executor, and static knowledge store.
- [ ] No supported runtime advertises `external_process` while creating reports only through FastAPI `BackgroundTasks`.
- [ ] No supported runtime can expose `processing` without a non-null job/attempt identity or explicit in-process owner identity.

### State machine

- [ ] Every report moves through a legal state sequence.
- [ ] Every report attempt finishes as `completed`, `failed`, `cancelled`, or a recoverable `orphaned/queued` state.
- [ ] Progress is monotonic within one attempt.
- [ ] A completed report cannot return to processing.
- [ ] An old attempt cannot overwrite a newer attempt.
- [ ] Concurrent finish/retry requests create at most one active report job per session.

### Failure behavior

- [ ] Disabled embedding fails before report execution begins.
- [ ] Missing credentials, provider timeout, pgvector failure, schema mismatch, and missing corpus produce stable error codes.
- [ ] Unexpected `ValueError` is persisted as a report failure; it is not silently returned.
- [ ] Only an explicitly classified missing/deleted session may exit without updating a report.
- [ ] A failed status contains a safe public message, stable error code, retryability, timestamps, and attempt number.

### Recovery

- [ ] A worker crash is recoverable through lease expiry and reclaim.
- [ ] A stale processing projection with no job is detectable as an orphan.
- [ ] A retry creates or requeues one authoritative job and remains idempotent.
- [ ] Browser disconnect, SSE completion, refresh, or route navigation cannot decide whether a report job exists.

---

## 5. Supported Runtime Profiles

Implement two explicit profiles and reject mixed profiles.

### Durable report profile — recommended

```text
INTERVIEW_RUNTIME_STORE=postgres
REPORT_JOB_STORE=postgres
REPORT_WORKER=external_process
KNOWLEDGE_STORE=pgvector
EMBEDDING_PROVIDER=siliconflow
```

Properties:

- Sessions survive API restarts.
- Report jobs are transactional and lease-based.
- The external worker owns report execution.
- pgvector retrieval uses an enabled remote embedding provider.
- Production report generation fails closed when grounding is unavailable.

### Frontend preview profile

```text
INTERVIEW_RUNTIME_STORE=memory
REPORT_JOB_STORE=memory
REPORT_WORKER=in_process
KNOWLEDGE_STORE=static
EMBEDDING_PROVIDER=disabled
```

Properties:

- Intended only for local UI work.
- No PostgreSQL report queue or pgvector dependency is constructed.
- Every in-memory report still has a local job ID and attempt.
- Static references are deterministic and marked as preview evidence.
- `/api/runtime` exposes `preview=true` and `grounding_durable=false`.

### Invalid profile examples

Reject startup when any of the following are selected:

```text
memory session + postgres queue + external worker
memory session + pgvector + disabled embedding
postgres queue + in_process worker without an explicit supported adapter
external worker + memory-only job store
pgvector knowledge store + disabled embedding
```

---

## 6. File Map

Expected implementation surfaces:

- Modify: `app/services/report_enqueue.py`
  - Return an explicit enqueue outcome and remove silent durable-to-background fallback.

- Modify: `app/services/report_tasks.py`
  - Enclose dependency construction and execution in one terminal-state boundary; classify all errors.

- Modify: `app/services/report_jobs.py`
  - Add or harden job identity, attempt, heartbeat, lease, orphan detection, and idempotent repair/requeue behavior.

- Modify: `app/services/report_worker.py`
  - Publish heartbeat/progress and apply retry policy without creating a second authority.

- Modify: `app/services/session.py`
  - Make in-memory report transitions explicit and idempotent; prevent stale
    failures from overwriting completed reports.

- Modify: `app/services/postgres_session.py`
  - Remove pre-ownership processing projection and prevent stale failures from
    overwriting completed PostgreSQL reports.

- Modify: `app/services/runtime.py`
  - Build only coherent runtime profiles and expose their readiness.

- Modify: `app/services/config.py`
  - Add validated report/knowledge profile settings and timeouts.

- Modify: `app/services/embedding_providers.py`
  - Preserve stable configuration/provider errors and safe retryability.

- Modify: `app/services/vector_store.py`
  - Expose safe retrieval diagnostics and enforce corpus/dimension readiness.

- Modify: `app/api/routes.py`
  - Return truthful progress, expose controlled retry, and remove response-lifecycle authority.

- Create: `app/services/report_runtime_preflight.py`
  - Perform non-mutating runtime readiness checks.

- Create: `app/services/static_knowledge_store.py`
  - Provide deterministic development-only references.

- Create: `scripts/report_runtime_preflight.py`
  - Operator CLI for startup gates.

- Modify: `frontend/src/pages/ReportProcessingPage.jsx`
  - Render active, stalled, failed, retryable, orphaned, and completed states.

- Modify: `frontend/src/api/client.js` only if implementation evidence requires it
  - Prefer the existing `getJson()` and `postJson()` exports for progress and
    requeue; do not add a helper speculatively.

- Modify: `frontend/src/styles/report-processing-app.css`
  - Add accessible stalled/failure/retry presentation without changing the established visual system.

- Modify or create tests listed in the tasks below.

---

## 7. Task 1 — Lock The Failure With Regression Tests

**Files:**

- Modify: `tests/test_report_enqueue.py`
- Modify: `tests/test_report_tasks.py`
- Modify: `tests/test_report_worker.py`
- Modify: `tests/test_report_api.py`
- Modify: `tests/test_report_progress.py`
- Create: `tests/test_report_orphan_recovery.py`
- Create: `tests/test_report_runtime_preflight.py`
- Create: `tests/test_streaming_report_enqueue.py`

- [ ] **Step 1: Add an enqueue failure test**

Prove that a durable enqueue failure cannot create `processing` with a null job ID:

```python
def test_durable_enqueue_failure_does_not_create_orphan_processing_report():
    ...
```

Required assertions:

```text
enqueue result = failed
error_code = report_enqueue_unavailable
retryable = true
no FastAPI BackgroundTask is authoritative
no active report exists without a job ID
```

- [ ] **Step 2: Add an unexpected ValueError test**

Inject `ValueError` after the report enters retrieval and assert:

```text
record.status = failed
record.error_code = report_validation_failed
record.finished_at is not null
```

- [ ] **Step 3: Add a dependency-construction failure test**

Make each of these fail independently:

```text
get_knowledge_store()
resolve_runtime_llm()
get_agent_execution_runner()
```

Every failure must terminate the report safely.

- [ ] **Step 4: Add cancellation tests**

Cover task cancellation before retrieval, during retrieval, and after analysis. Cancellation must become either a retryable failure or a released/recoverable job; it must never remain active without ownership.

- [ ] **Step 5: Add orphan tests**

Cover:

```text
processing + job_id=null + stale updated_at
running job + expired lease
running job + stale heartbeat
processing report + completed job mismatch
completed report + running job mismatch
```

Also prove the ownership boundary explicitly:

```text
successful durable enqueue -> progress resolves the job ID from the job store
active report + unresolvable job authority -> orphan/configuration failure
ReportRecord remains the report-result projection, not the job owner
```

- [ ] **Step 6: Add SSE lifecycle tests**

Finish an interview through `/answer/stream` and simulate:

- normal EOF;
- disconnect immediately after the terminal event;
- browser refresh;
- duplicate command delivery.

The report job must already be authoritative before any terminal UI event is emitted.

- [ ] **Step 7: Run tests and verify they fail for the intended reasons**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_enqueue.py `
  tests/test_report_tasks.py `
  tests/test_report_worker.py `
  tests/test_report_api.py `
  tests/test_report_progress.py `
  tests/test_report_orphan_recovery.py `
  tests/test_report_runtime_preflight.py `
  tests/test_streaming_report_enqueue.py `
  -q
```

Expected: new tests fail without modifying external state.

---

## 8. Task 2 — Make Enqueue Outcomes Explicit

**Files:**

- Modify: `app/services/report_enqueue.py`
- Modify: `app/ports/runtime.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_report_enqueue.py`
- Modify: `tests/test_report_api.py`

- [ ] **Step 1: Introduce a bounded enqueue result**

Use a model equivalent to:

```python
class ReportEnqueueResult(BaseModel):
    status: Literal["queued", "already_exists", "failed"]
    job_id: str | None = None
    error_code: str | None = None
    retryable: bool = False
```

- [ ] **Step 2: Remove the broad silent fallback in durable mode**

Durable enqueue behavior:

```text
success       -> queued with non-null job_id
duplicate     -> already_exists with the existing job_id
store failure -> failed/report_enqueue_unavailable
```

Do not call `BackgroundTasks.add_task()` as the fallback for a durable profile.

- [ ] **Step 3: Preserve interview completion separately from report enqueue**

Finishing the interview must remain successful even if report enqueue fails. The response and report projection must expose that the interview is finished and report scheduling failed.

In the durable profile, do not call either session store's standalone
`mark_report_processing()` before `PostgresReportJobStore` has transactionally
created or confirmed the report row and job. Keep the standalone method only
for a proven compatible path or deprecate it after callers are migrated.

- [ ] **Step 4: Ensure the SSE terminal event contains the committed enqueue projection**

Before yielding `done`, the server must have one of:

```text
queued + job_id
failed + stable enqueue error
```

It must not emit a terminal interview event while the report is merely expected to be added later by the response background hook.

- [ ] **Step 5: Log safe enqueue diagnostics**

Record only:

```text
session_id
job_id when present
store kind
stable error code
latency
```

Do not log DSNs, SQL text with interview data, resume text, answers, or raw exception chains in public logs.

- [ ] **Step 6: Run focused tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_enqueue.py `
  tests/test_report_api.py `
  tests/test_streaming_report_enqueue.py `
  -q
```

Expected: PASS.

---

## 9. Task 3 — Close The Report Execution Error Boundary

**Files:**

- Modify: `app/services/report_tasks.py`
- Modify: `app/services/report.py`
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Modify: `tests/test_report_tasks.py`
- Modify: `tests/test_session_report_store.py`
- Modify: `tests/test_postgres_session_store.py`

- [ ] **Step 1: Replace the broad `except ValueError: return None`**

Only an explicitly classified missing/deleted session may exit silently. All other validation errors must write a terminal failure.

Suggested stable classifications:

```text
report_session_unavailable
report_validation_failed
report_provider_unavailable
knowledge_store_unavailable
report_task_cancelled
report_internal_error
```

- [ ] **Step 2: Enclose dependency construction and execution in one boundary**

The protected region must include:

```python
vector_store = get_knowledge_store()
llm = resolve_runtime_llm(store)
execution_runner = get_agent_execution_runner()
execute_report_generation(...)
```

- [ ] **Step 3: Normalize provider and retrieval errors**

Map embedding and knowledge exceptions into stable report errors without leaking provider bodies or secrets.

- [ ] **Step 4: Make `fail_report()` idempotent**

Required behavior:

- duplicate failure delivery retains one terminal result;
- a failure cannot overwrite a completed report;
- an old attempt cannot overwrite a newer attempt;
- a missing progress record does not prevent terminal failure persistence;
- `finished_at` and `updated_at` are always set.

Apply and test this behavior in both implementations. The current in-memory
and PostgreSQL methods are actively unsafe because both overwrite completed
state without a terminal-state guard. The PostgreSQL implementation must use
an atomic conditional update or row lock/version check; a read-then-write guard
alone is insufficient against a stale worker racing final completion.

- [ ] **Step 5: Handle cancellation deliberately**

Catch the runtime's cancellation type at the task/worker boundary. For a leased durable job, release or mark retryable according to job policy. For an explicit preview job, persist `cancelled` or a retryable failure.

- [ ] **Step 6: Preserve safe internal diagnostics**

Private logs may include an exception class and correlation identifier. Public report records use stable messages and codes only.

- [ ] **Step 7: Run focused tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_tasks.py `
  tests/test_session_report_store.py `
  tests/test_postgres_session_store.py `
  -q
```

Expected: PASS.

---

## 10. Task 4 — Enforce Coherent Runtime Profiles

**Files:**

- Modify: `app/services/config.py`
- Modify: `app/services/runtime.py`
- Modify: `app/api/routes.py` (`runtime_boundary()` owns `/api/runtime`)
- Modify: `.env.example`
- Create or modify: `tests/test_runtime_provider.py`
- Modify: `tests/test_runtime_boundary_api.py`

- [ ] **Step 1: Add explicit settings for each authority**

Validate settings equivalent to:

```text
INTERVIEW_RUNTIME_STORE
REPORT_JOB_STORE
REPORT_WORKER
KNOWLEDGE_STORE
EMBEDDING_PROVIDER
```

- [ ] **Step 2: Add a composition validator**

The validator returns a structured result for diagnostics and raises at startup for unsupported production combinations.

Treat `KNOWLEDGE_STORE=pgvector` with
`EMBEDDING_PROVIDER=disabled` as a current configuration defect to reject, not
as an optional future feature. At present this invalid combination survives
startup and fails only when `search()` first calls `embed_query()`.

- [ ] **Step 3: Build dependencies from one selected profile**

Do not independently default each store in a way that produces a mixed runtime. Resolve the profile first, then build all dependencies from it.

- [ ] **Step 4: Expand `/api/runtime`**

Expose safe fields:

```json
{
  "profile": "durable_report",
  "configuration_valid": true,
  "report_runtime_ready": true,
  "knowledge_runtime_ready": true,
  "session_store": "PostgresInterviewSessionStore",
  "report_job_store": "PostgresReportJobStore",
  "report_worker": "external_process",
  "knowledge_store": "PgVectorKnowledgeStore",
  "embedding_provider": "siliconflow",
  "preview": false,
  "warnings": []
}
```

Never expose credentials, base URLs containing credentials, DSNs, raw schema errors, or provider response bodies.

- [ ] **Step 5: Document `.env.example` profiles**

Keep credential values empty. Include one durable block and one preview block with comments that they must not be mixed.

- [ ] **Step 6: Run focused tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_runtime_provider.py `
  tests/test_runtime_boundary_api.py `
  -q
```

Expected: PASS.

---

## 11. Task 5 — Add Knowledge Runtime Preflight

**Files:**

- Create: `app/services/report_runtime_preflight.py`
- Create: `scripts/report_runtime_preflight.py`
- Modify: `app/services/embedding_providers.py`
- Modify: `app/services/vector_store.py`
- Modify: `app/services/runtime.py`
- Create: `tests/test_report_runtime_preflight.py`
- Modify: `tests/test_embedding_config.py`
- Modify: `tests/test_vector_store.py`

- [ ] **Step 1: Define non-mutating preflight checks**

Checks:

```text
runtime_profile_coherent
postgres_reachable
report_job_schema_compatible
pgvector_extension_available
knowledge_schema_compatible
active_corpus_available
embedding_provider_enabled
embedding_credentials_present
embedding_dimension_matches_corpus
report_llm_configured
worker_can_claim_without_mutating_business data
```

The preflight must not create/alter production schema or ingest a corpus.

- [ ] **Step 2: Fail before job claim when configuration is invalid**

An external worker must not claim jobs if embedding or knowledge readiness is false. The API may remain available for interviews, but report readiness must be visible.

- [ ] **Step 3: Use stable public error codes**

Required codes:

```text
embedding_provider_disabled
embedding_credentials_missing
embedding_provider_timeout
embedding_dimension_mismatch
knowledge_store_unavailable
knowledge_schema_invalid
knowledge_corpus_missing
report_llm_unconfigured
```

- [ ] **Step 4: Add the CLI**

```powershell
& 'F:\python3.11\python.exe' -m scripts.report_runtime_preflight
```

Example safe output:

```text
[PASS] runtime profile is coherent
[PASS] PostgreSQL is reachable
[PASS] report job schema is compatible
[PASS] pgvector extension is available
[PASS] active knowledge corpus exists
[PASS] embedding provider is enabled
[PASS] embedding dimension matches the active corpus
[PASS] report LLM is configured
```

- [ ] **Step 5: Prove secret redaction**

Test that the API key, Authorization header, resume text, answers, query text, DSN, and raw provider response never appear in preflight output or exceptions.

- [ ] **Step 6: Run focused tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_runtime_preflight.py `
  tests/test_embedding_config.py `
  tests/test_vector_store.py `
  -q
```

Expected: PASS without making a real provider call.

---

## 12. Task 6 — Harden Job Lease, Heartbeat, And Orphan Recovery

**Files:**

- Modify: `app/services/report_jobs.py`
- Modify: `app/services/report_worker.py`
- Add an explicit PostgreSQL schema migration using the repository's migration procedure
- Modify: `tests/test_report_jobs.py`
- Modify: `tests/test_report_worker.py`
- Create: `tests/test_report_orphan_recovery.py`

- [ ] **Step 1: Confirm or add required job metadata**

Each active job requires:

```text
job_id
session_id
status
attempt
max_attempts
created_at
started_at
updated_at
heartbeat_at
lease_owner
lease_expires_at
error_code
last_error
retryable
```

Existing durable fields include `lease_expires_at`, `updated_at`, attempt
counts, errors, and lease ownership. `heartbeat_at` does not exist. Add it as
an explicit nullable `TIMESTAMPTZ` migration, populate it on claim and every
successful heartbeat, return it from all job reads, and clear or retain it
according to the documented terminal-state policy. Do not rely on API startup
auto-migration.

- [ ] **Step 2: Define legal states**

Recommended job states:

```text
queued
running
waiting
retrying
completed
failed
cancelled
```

Recommended public report stages:

```text
queued
retrieving
analyzing
aggregating
completed
failed
orphaned
cancelled
```

- [ ] **Step 3: Publish heartbeat while work is active**

Recommended initial operational values:

```text
heartbeat interval = 10 seconds
lease duration = 45 seconds
orphan threshold = 90 seconds
max attempts = 3
```

Keep these validated and configurable.

`lease_expires_at` and `heartbeat_at` have different meanings:

```text
heartbeat_at     = when the worker last proved liveness
lease_expires_at = when another worker may legally reclaim ownership
updated_at       = when any job field last changed
```

Do not derive all three public concepts from `updated_at`.

- [ ] **Step 4: Recover expired jobs**

When a running/retrieving/analyzing/aggregating job has an expired lease and stale heartbeat:

```text
attempt < max_attempts -> release/requeue
attempt >= max_attempts -> failed/report_retry_exhausted
```

- [ ] **Step 5: Detect processing reports with no job**

Classify:

```text
processing + no job + stale updated_at -> orphaned/report_job_missing
```

Do not automatically recreate a job until session-store compatibility and idempotency have been proven.

- [ ] **Step 6: Reconcile contradictory terminal states**

Fail closed and alert when report/job projections disagree. Do not overwrite a completed report through automatic repair.

- [ ] **Step 7: Run focused tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_jobs.py `
  tests/test_report_worker.py `
  tests/test_report_orphan_recovery.py `
  -q
```

Expected: PASS.

---

## 13. Task 7 — Add An Explicit Preview Knowledge And Job Path

**Files:**

- Create: `app/services/static_knowledge_store.py`
- Create or modify: `app/services/memory_report_jobs.py`
- Modify: `app/services/runtime.py`
- Modify: `app/services/report_worker.py`
- Create: `tests/test_static_knowledge_store.py`
- Create: `tests/test_memory_report_jobs.py`
- Modify: `tests/test_runtime_provider.py`

- [ ] **Step 1: Add deterministic static references**

Static search must return bounded, deterministic `KnowledgeChunk` values suitable for UI preview. It must not call the embedding provider or PostgreSQL.

- [ ] **Step 2: Mark preview evidence truthfully**

Attach safe internal metadata equivalent to:

```text
retrieval_path=static_preview
preview=true
grounding_durable=false
```

- [ ] **Step 3: Add an in-memory job identity**

Preview reports still receive:

```text
job_id=memory-report-<uuid>
attempt=1
status=queued/running/completed/failed
```

- [ ] **Step 4: Use one managed in-process executor**

Do not make correctness depend on an arbitrary FastAPI response background hook. The preview executor must own jobs in an application-level registry and expose its active ownership in progress responses.

- [ ] **Step 5: Keep production fail-closed**

`KNOWLEDGE_STORE=static` must be rejected outside the explicit preview profile.

- [ ] **Step 6: Run focused tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_static_knowledge_store.py `
  tests/test_memory_report_jobs.py `
  tests/test_runtime_provider.py `
  -q
```

Expected: PASS.

---

## 14. Task 8 — Extend Controlled Requeue And Orphan Repair APIs

**Files:**

- Modify: `app/api/routes.py`
- Modify: `app/services/report_jobs.py`
- Modify: `app/ports/runtime.py`
- Modify: `tests/test_report_api.py`
- Modify: `tests/test_report_jobs.py`
- Modify: `tests/test_report_orphan_recovery.py`

- [ ] **Step 1: Extend the existing requeue contract**

Endpoint:

```http
POST /api/interviews/{session_id}/report/requeue
```

Keep the existing endpoint for backward compatibility. Do not add a second
`/report/retry` route unless a separately approved API-versioning decision
requires it.

Allowed:

```text
failed and retryable
orphaned
stale processing with no job after authoritative orphan classification
```

Rejected:

```text
completed
active job with a valid lease
unfinished interview
non-retryable configuration failure
```

- [ ] **Step 2: Return a new authoritative identity**

```json
{
  "session_id": "...",
  "report_job_id": "...",
  "status": "queued",
  "attempt": 2,
  "recovered_from": "orphaned",
  "retryable": true
}
```

- [ ] **Step 3: Enforce idempotency**

An idempotency/command key must make concurrent clicks and duplicate HTTP delivery return the same active job.

- [ ] **Step 4: Add trusted-local bulk repair**

If an operator endpoint is retained, it must be trusted-local, bounded, dry-run capable, and return counts only. It must not expose session content.

- [ ] **Step 5: Run focused tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_api.py `
  tests/test_report_jobs.py `
  tests/test_report_orphan_recovery.py `
  -q
```

Expected: PASS.

---

## 15. Task 9 — Make Progress And Errors Truthful

**Files:**

- Modify: `app/api/routes.py`
- Modify: `app/services/report.py`
- Modify: `tests/test_report_api.py`
- Modify: `tests/test_report_progress.py`
- Modify: `tests/test_runtime_boundary_api.py`

- [ ] **Step 1: Stop presenting a synthesized current state as event history**

If `events` remains public, populate it from persisted stage transitions. Otherwise rename/remove it and expose only current progress.

Treat the current behavior as an active bug: the backend manufactures one
current-stage item and the React page labels and renders it as an ordered
“运行事件” ledger. Until real history exists, the UI must call it current
status rather than imply a historical sequence.

- [ ] **Step 2: Add safe job health fields**

Recommended response:

```json
{
  "session_id": "...",
  "report_job_id": "...",
  "status": "retrieving",
  "stage": "retrieving",
  "percent": 24,
  "attempt": 1,
  "max_attempts": 3,
  "started_at": "...",
  "last_updated_at": "...",
  "heartbeat_at": "...",
  "stalled": false,
  "retryable": false,
  "current_question_id": "q1",
  "rag": {
    "provider": "siliconflow",
    "model": "BAAI/bge-m3",
    "corpus_version": "...",
    "top_k": 5,
    "matched_chunks": 5
  },
  "error": null
}
```

- [ ] **Step 3: Return structured terminal errors**

```json
{
  "status": "failed",
  "error": {
    "code": "embedding_provider_disabled",
    "message": "Knowledge retrieval is not configured.",
    "retryable": false
  }
}
```

- [ ] **Step 4: Make progress monotonic**

Suggested ranges:

```text
queued        0
retrieving   10-40
analyzing    40-75
aggregating  75-95
completed    100
```

Failure retains the last valid percentage and changes the status/stage to failed.

- [ ] **Step 5: Keep public diagnostics bounded**

Do not expose lease owner, thread/checkpoint IDs, hashes, raw SQL/provider errors, query text, answers, references content, or secrets.

- [ ] **Step 6: Run focused tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_api.py `
  tests/test_report_progress.py `
  tests/test_runtime_boundary_api.py `
  -q
```

Expected: PASS.

---

## 16. Task 10 — Update The React Processing Experience

**Files:**

- Modify: `frontend/src/pages/ReportProcessingPage.jsx`
- Modify: `frontend/src/styles/report-processing-app.css`
- Modify: `frontend/src/api/client.js` only if the existing `getJson()` and
  `postJson()` exports prove insufficient
- Modify or create: frontend unit/browser tests for report processing

- [ ] **Step 1: Add explicit view states**

Render:

```text
queued
active retrieval
active analysis
active aggregation
retrying
stalled
orphaned
failed retryable
failed non-retryable
completed
```

- [ ] **Step 2: Detect stall from server authority**

Prefer the API's `stalled` value. If a client-side fallback is necessary, calculate it from `last_updated_at`; never infer it only from an unchanged percentage.

- [ ] **Step 3: Map stable errors to Chinese guidance**

| Error code | User-facing message | Action |
|---|---|---|
| `report_enqueue_unavailable` | 报告任务暂时无法进入处理队列。 | 稍后重试 |
| `embedding_provider_disabled` | 知识检索服务尚未启用。 | 检查运行配置 |
| `embedding_credentials_missing` | 知识检索凭据尚未配置。 | 检查本地环境变量 |
| `embedding_provider_timeout` | 知识服务响应超时。 | 重新尝试 |
| `knowledge_corpus_missing` | 当前没有可用的岗位知识库。 | 返回准备页或联系维护人员 |
| `report_job_missing` | 报告任务已中断。 | 重新创建任务 |
| `report_retry_exhausted` | 多次尝试后仍未完成。 | 查看诊断信息 |

- [ ] **Step 4: Add a controlled retry action**

Show retry only when `retryable=true` or status is `orphaned`. Disable the button during the request and prevent duplicate submissions.

- [ ] **Step 5: Use adaptive polling**

Recommended initial policy:

```text
0-20 seconds: every 1 second
20-60 seconds: every 2 seconds
after 60 seconds: every 5 seconds
hidden tab: reduce frequency
visibility restored: poll immediately
terminal status: stop polling
```

- [ ] **Step 6: Preserve accessibility and motion preferences**

- Progress must use a valid `progressbar` name/value.
- Status changes should use a bounded `aria-live` region.
- Retry and navigation controls must remain keyboard accessible.
- Stalled/failure communication must not rely on color alone.
- Motion must respect `prefers-reduced-motion`.

The current ARIA `role="progressbar"` pattern is valid when its name and value
attributes remain synchronized. A native `<progress>` element may be adopted
for maintainability, but this remediation must not describe the present
CSS-variable implementation as non-standard solely because it is custom.

- [ ] **Step 7: Run frontend verification**

```powershell
Push-Location frontend
npm run build
npm test -- --run
Pop-Location
```

Use the repository's actual test script if it differs; do not add an unsupported command merely to satisfy this plan.

Expected: build and relevant tests PASS.

---

## 17. Task 11 — End-To-End Recovery And Fault Verification

**Files:**

- Create or modify PostgreSQL acceptance tests and scripts as required.
- Update the acceptance evidence document after successful execution.

- [ ] **Step 1: Run the focused Python suite**

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_enqueue.py `
  tests/test_report_tasks.py `
  tests/test_report_jobs.py `
  tests/test_report_worker.py `
  tests/test_report_api.py `
  tests/test_report_progress.py `
  tests/test_report_orphan_recovery.py `
  tests/test_report_runtime_preflight.py `
  tests/test_streaming_report_enqueue.py `
  -q
```

- [ ] **Step 2: Run PostgreSQL and pgvector tests**

Set `POSTGRES_DSN` only in the local process environment. Do not write credentials into this document or committed scripts.

```powershell
& 'F:\python3.11\python.exe' -m pytest `
  tests/test_report_jobs.py `
  tests/test_report_worker.py `
  tests/test_vector_store_pgvector.py `
  -q `
  -m 'pg_jobs or pgvector'
```

- [ ] **Step 3: Run the non-mutating preflight**

```powershell
& 'F:\python3.11\python.exe' -m scripts.report_runtime_preflight
```

Expected: every required durable-profile check reports PASS before API/worker smoke testing.

- [ ] **Step 4: Verify the normal flow**

```text
finish interview
  -> job ID returned
  -> queued
  -> worker claim
  -> retrieving with heartbeat
  -> analyzing
  -> aggregating
  -> completed
  -> report detail available
  -> PDF available
```

- [ ] **Step 5: Verify the provider-disabled flow**

In an isolated test process, use `EMBEDDING_PROVIDER=disabled` with the durable profile.

Expected:

```text
startup/preflight failure or immediate report failure
error_code=embedding_provider_disabled
no indefinite processing record
```

- [ ] **Step 6: Verify worker crash recovery**

Terminate an isolated test worker at each point:

```text
after claim
during retrieval
during analysis
during aggregation
after provider output but before final commit
```

Expected: lease expiry/reclaim produces one final report at most.

- [ ] **Step 7: Verify SSE/browser failure cases**

Cover disconnect after the interview terminal event, refresh on the processing page, duplicate retry click, API restart, and worker restart.

- [ ] **Step 8: Run the broader regression suite**

```powershell
& 'F:\python3.11\python.exe' -m pytest -q

Push-Location frontend
npm run build
Pop-Location
```

Run the repository's browser suite after confirming its documented prerequisites.

---

## 18. Deployment Sequence

Use this order to avoid creating another unrecoverable mixed runtime.

- [ ] Export and review the current incident snapshot.
- [ ] Merge regression tests and error-boundary fixes without restarting the current incident process.
- [ ] Merge explicit enqueue outcomes and remove the durable BackgroundTask fallback.
- [ ] Merge runtime profile validation.
- [ ] Merge preflight, heartbeat, orphan recovery, and retry behavior.
- [ ] Configure a compatible PostgreSQL schema through the project's explicit migration procedure; do not auto-migrate from API startup.
- [ ] Configure a fresh embedding credential only in the local/service environment.
- [ ] Activate or verify a compatible BGE-M3 corpus.
- [ ] Run report runtime preflight.
- [ ] Stop the old preview backend only after accepting that its in-memory session cannot survive restart.
- [ ] Start the durable API without `--reload` for the acceptance run.
- [ ] Start the external Report Worker.
- [ ] Start the independent Vite/React frontend.
- [ ] Create a new three-question validation interview.
- [ ] Verify the full report and PDF flow.
- [ ] Verify worker restart recovery.
- [ ] Verify the report-processing failure and retry UI.
- [ ] Record sanitized acceptance evidence.
- [ ] Remove or clearly rename the old mixed-runtime startup script.

Recommended process separation:

```powershell
# API
& 'F:\python3.11\python.exe' -m uvicorn app.main:app `
  --host 127.0.0.1 `
  --port 8000

# Worker
& 'F:\python3.11\python.exe' -m app.services.report_worker

# Frontend
Push-Location frontend
npm run dev
Pop-Location
```

Do not include secrets directly in command history. Supply them through an approved local environment mechanism.

---

## 19. Rollback Plan

Rollback must preserve already assigned durable jobs.

- [ ] Stop assigning new jobs to the new path by setting rollout/profile configuration back to the prior supported durable version.
- [ ] Keep the runtime required to resume already assigned job versions available.
- [ ] Do not convert active durable jobs into process-coupled BackgroundTasks.
- [ ] Do not delete report jobs, leases, checkpoints, or report projections during rollback.
- [ ] Keep the old and new public progress fields backward compatible for at least one deployment window.
- [ ] If embedding/provider readiness fails, stop new worker claims and expose maintenance/failure state; do not leave jobs running without heartbeat.
- [ ] Re-run runtime preflight after rollback.
- [ ] Confirm that completed reports and PDFs remain readable.

---

## 20. Acceptance Criteria

### Scheduling

- [ ] A finished interview produces a non-null `report_job_id` within one second in the durable profile.
- [ ] Durable enqueue failure produces a bounded failed/retryable projection instead of null-job processing.
- [ ] Duplicate finish commands reuse the same active job.

### Retrieval

- [ ] A valid pgvector/embedding configuration returns a non-null corpus version and truthful matched-chunk count.
- [ ] Disabled embedding fails within the configured preflight/task boundary and never remains at 20%.
- [ ] Provider credentials and query content never appear in logs or APIs.

### Execution and recovery

- [ ] Every active job has a valid owner/lease/heartbeat or explicit preview executor identity.
- [ ] A worker crash is reclaimed after lease expiry.
- [ ] An orphaned null-job processing report is detectable.
- [ ] Retry is idempotent and creates at most one active job.
- [ ] Maximum retry attempts are enforced.
- [ ] A completed report cannot be overwritten by an old worker.

### Progress API

- [ ] Progress is monotonic per attempt.
- [ ] `last_updated_at`, attempt, retryability, and structured error data are available.
- [ ] `events` is either true persisted history or no longer presented as history.
- [ ] No sensitive lease/checkpoint/provider data is exposed.

### Frontend

- [ ] The page distinguishes active, retrying, stalled, orphaned, failed, and completed states.
- [ ] Terminal states stop polling.
- [ ] Retry is shown only when authorized by the API.
- [ ] Status and errors remain accessible without color and respect reduced motion.

### Operations

- [ ] The runtime rejects unsupported mixed profiles.
- [ ] Preflight passes before the durable Worker starts.
- [ ] API, Worker, and Vite/React run as independent services.
- [ ] Acceptance includes worker crash, provider disabled, queue unavailable, and browser refresh cases.
- [ ] The full regression suite passes.

---

## 21. Recommended Execution Order

Implement in these batches:

### Batch A — Safe code fixes, no incident-process restart

1. Task 1: regression tests.
2. Task 2: explicit enqueue outcomes.
3. Task 3: terminal error boundary.
4. Task 4: runtime profile validation.

### Batch B — Runtime reliability

5. Task 5: knowledge/runtime preflight.
6. Task 6: heartbeat, lease, and orphan recovery.
7. Task 7: explicit preview path.
8. Task 8: controlled retry APIs.

### Batch C — Product feedback and release

9. Task 9: truthful progress API.
10. Task 10: React stalled/failure/retry states.
11. Task 11: PostgreSQL, provider, worker, SSE, and browser acceptance.
12. Controlled deployment and rollback rehearsal.

Do not begin the controlled restart until Batch A and Batch B tests are green and the target runtime profile passes preflight.

---

## 22. Definition Of Done

This remediation is done only when:

```text
No report can remain indefinitely at retrieving/20% without a live owner.
Every durable report has a job ID.
Every active job has a valid lease and heartbeat.
Every failure reaches a terminal, structured state.
Embedding and pgvector readiness are checked before work is claimed.
The worker can recover after process loss.
The React UI reports stalls and failures truthfully and offers only valid actions.
The durable and preview profiles are internally coherent and cannot be mixed.
The focused, PostgreSQL, browser, and full regression gates pass.
```
