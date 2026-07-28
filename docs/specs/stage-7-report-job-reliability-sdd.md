# Stage 7: Report Job Reliability SDD

## 1. Goal

Stage 7 upgrades report generation from process-coupled `BackgroundTasks` to a recoverable job execution model.

Stage 6 moved sessions, transcripts, and report records into PostgreSQL. That solved the state-loss problem for interview runtime and report storage. The next structural weakness is execution reliability: report generation still starts inside the API process through FastAPI `BackgroundTasks`. If the process exits, deploys, or restarts while a report is running, the persisted report row may remain in `processing` state without a worker that can resume or reclaim it.

The goal of Stage 7 is to make report generation operationally reliable:

- Persist report jobs separately from report results.
- Queue work outside the API request lifecycle.
- Allow workers to claim, execute, complete, fail, retry, and reclaim stale jobs.
- Keep the existing `/report` contract stable while improving the execution backend.

## 2. Scope

### 2.1 Included

- Introduce a persistent report job table in PostgreSQL.
- Replace direct `BackgroundTasks.add_task(...)` execution with enqueue semantics.
- Add a worker loop that polls and executes queued jobs.
- Add explicit job states, lease timestamps, retry counters, and stale-job reclaim rules.
- Keep `interview_reports` as the source of report result state shown to clients.
- Add tests for enqueueing, claiming, retrying, reclaiming, orphan recovery, and idempotent execution.
- Add a local multi-process run path, ideally via `docker-compose`.

### 2.2 Explicitly Excluded

- No user auth or per-user job isolation.
- No frontend history page in this stage.
- No queue migration for streaming answers.
- No LangGraph rewrite.
- No full observability stack.
- No distributed scheduler beyond a single worker role.

## 3. Current Code Constraints

Current execution flow:

```text
POST /api/interviews/{session_id}/answer
-> store.submit_answer(...)
-> turn.status == finished
-> store.mark_report_processing(session_id)
-> BackgroundTasks.add_task(generate_report_for_session, session_id, store)
-> API returns immediately
-> generate_report_for_session(...) runs inside API process
```

Relevant modules:

- [routes.py](F:/agent/Interview-Agent/app/api/routes.py:175)
  - `_schedule_report_if_needed()` uses `BackgroundTasks`.

- [report_tasks.py](F:/agent/Interview-Agent/app/services/report_tasks.py:7)
  - `generate_report_for_session()` performs the entire report generation side effect.

- [postgres_session.py](F:/agent/Interview-Agent/app/services/postgres_session.py:148)
  - `mark_report_processing()` and other report state operations already persist into PostgreSQL.

- [runtime.py](F:/agent/Interview-Agent/app/services/runtime.py)
  - currently provides session store construction
  - does not yet provide report job store or worker/executor factories

This means the current system already knows how to persist report results, but it does not persist the execution lifecycle of the work item itself.

## 4. Architectural Direction

### 4.1 Recommended Approach

Introduce a dedicated report job layer in PostgreSQL and a lightweight worker process.

Recommended runtime split:

- API process
  - Persists session state.
  - Does not run long report generation inline.
  - Enqueues durable report jobs transactionally.

- Worker process
  - Polls PostgreSQL for queued, retrying, or stale running jobs.
  - Claims a job lease.
  - Executes pure report generation logic.
  - Updates both job state and `interview_reports`.

### 4.2 Runtime Injection Chain

This stage must make the dependency graph explicit.

Worker execution needs all of:

- `PostgresReportJobStore`
- `InterviewSessionStore`
- `InterviewLLM`
- `PgVectorKnowledgeStore`

So `runtime.py` must be expanded to provide factories beyond `get_session_store()`:

- `build_session_store()`
- `build_report_job_store()`
- `build_report_executor()` or `build_report_worker()`

The recommendation is:

- `build_report_executor()`
  - returns a thin object or callable bundling:
    - session store
    - llm
    - vector store

- `build_report_worker()`
  - optional convenience wrapper that combines:
    - report job store
    - report executor

This keeps worker construction explicit and testable, instead of hiding runtime dependencies in module-level globals.

### 4.3 Why Not Celery First

Celery plus Redis is a valid eventual direction, but Stage 7 should first stabilize the execution model with minimal new infrastructure. The codebase already depends heavily on PostgreSQL and now persists runtime state there. A PostgreSQL-backed report job queue is enough to solve the most immediate reliability issue with smaller blast radius.

If Stage 7 is successful, a later stage can migrate the same job state machine to Redis/Celery without changing client contracts.

## 5. Data Model

### 5.1 New Table: `report_jobs`

```sql
CREATE TABLE IF NOT EXISTS report_jobs (
    job_id UUID PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES interview_sessions(session_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'retrying', 'completed', 'failed')),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_error TEXT,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Supporting indexes:

```sql
CREATE INDEX IF NOT EXISTS report_jobs_status_idx
ON report_jobs (status, queued_at);

CREATE INDEX IF NOT EXISTS report_jobs_lease_idx
ON report_jobs (status, lease_expires_at);
```

### 5.2 Relationship To `interview_reports`

`report_jobs` describes execution state.

`interview_reports` remains the persisted business result:

- `processing`
- `completed`
- `failed`

This split is important:

- Job state answers: "Is a worker running or should something be retried?"
- Report state answers: "What should the frontend show for this session?"

## 6. State Machine

### 6.1 Explicit Job State Machine

```text
queued -> running -> completed
queued -> running -> retrying
retrying -> running
queued/running/retrying -> failed

running with expired lease -> claim_next() may reassign directly to running
```

Notes:

- `completed` is terminal.
- `failed` is terminal.
- `retrying` is non-terminal.
- `retrying` jobs are eligible for the next claim cycle.
- stale `running` jobs are not required to transition back to `queued`; they may be reclaimed directly by `claim_next()`.

### 6.2 Execution Rules

When a session reaches `finished`, the API must not perform these two writes separately:

- `interview_reports.status = processing`
- `report_jobs.status = queued`

That creates a crash window where report state says `processing` but no job exists.

Instead, Stage 7 must introduce a single transactional enqueue entrypoint, for example:

```python
report_job_store.enqueue_report_request(session_id)
```

Inside one PostgreSQL transaction, it must:

1. ensure `interview_reports` is present in `processing`
2. ensure exactly one `report_jobs` row exists in `queued` or prior non-terminal state

The API should call this transactional method once, not two separate methods.

### 6.3 Orphan Recovery

The transactional enqueue path is the primary consistency mechanism.

Additionally, Stage 7 should add orphan recovery as a defensive repair mechanism:

- when worker starts or on periodic sweep
- find `interview_reports.status = 'processing'`
- where no corresponding `report_jobs` row exists
- enqueue a replacement job

This is a safety net, not the main write path.

## 7. Reclaim Rules

### 7.1 Lease Model

Each running job has:

- `lease_owner`
- `lease_expires_at`

If current time exceeds `lease_expires_at`, another worker may reclaim the job.

### 7.2 Reclaim Policy

For Stage 7:

- reclaim `running` jobs whose lease expired
- reclaim `retrying` jobs immediately

This avoids a separate scheduler service and keeps recovery logic simple.

## 8. Retry Policy

### 8.1 Retryable Errors

Retryable by default:

- network timeouts
- transient model provider errors
- transient pgvector/embedding availability errors

Non-retryable by default:

- malformed configuration
- invalid DSN
- invalid schema mismatch
- deterministic validation errors caused by code defects

### 8.2 Attempt Rules

Recommended defaults:

- `max_attempts = 3`
- `lease_duration = 5 minutes`

Stage 7 may use immediate retry on next poll cycle instead of delayed backoff. Delayed exponential backoff can be introduced later if needed.

## 9. Module Design

### 9.1 New Module: `app/services/report_jobs.py`

Responsibilities:

- initialize `report_jobs` schema
- transactionally enqueue report requests
- claim next job
- complete job
- fail job
- retry job
- reclaim stale jobs
- repair orphan report rows

### 9.2 New Module: `app/services/report_worker.py`

Responsibilities:

- worker loop
- claim job
- load dependencies via runtime factories
- execute pure report generation
- update job and report state

### 9.3 Existing Module Changes

- [routes.py](F:/agent/Interview-Agent/app/api/routes.py:175)
  - replace direct `BackgroundTasks.add_task(...)` with transactional enqueue call

- [report_tasks.py](F:/agent/Interview-Agent/app/services/report_tasks.py:7)
  - extract a pure report execution function
  - worker calls it directly
  - API does not call it

- [runtime.py](F:/agent/Interview-Agent/app/services/runtime.py)
  - add:
    - `build_report_job_store()`
    - `build_report_executor()`
    - optional `build_report_worker()`

## 10. API Behavior

Client-facing behavior should remain largely unchanged:

- `/answer` still returns immediately after interview completion
- `/report` still returns:
  - `202` while processing
  - `200` when completed
  - `500` when failed

The difference is that report execution is now durable and recoverable.

Stage 7 should avoid changing frontend contracts unless new job-state detail is absolutely necessary.

## 11. Local Run Model

Recommended local modes:

### 11.1 Demo Mode

- `INTERVIEW_RUNTIME_STORE=memory`
- no worker
- fast local development

### 11.2 Durable Mode

- `INTERVIEW_RUNTIME_STORE=postgres`
- PostgreSQL enabled
- report worker process enabled

Recommended `docker-compose` services:

- `postgres`
- `api`
- `report-worker`

Redis is not required in this stage if PostgreSQL-backed polling is used.

## 12. Testing Strategy

Stage 7 should stay TDD-first.

### 12.1 Unit Tests

- enqueue creates one job per session
- duplicate enqueue does not create duplicate jobs
- transactional enqueue creates both report row and job row
- lease claim marks `running`
- expired lease is reclaimable
- terminal failure marks job `failed`
- retryable failure marks job `retrying`
- orphan recovery re-enqueues processing reports with no job row

### 12.2 PostgreSQL Integration Tests

- enqueue and claim against real PostgreSQL
- worker completion persists both job and report state
- stale `running` job gets reclaimed by a new worker
- repeated worker execution does not duplicate final reports
- test tables are dropped in teardown

### 12.3 End-To-End Smoke Tests

- finish interview through HTTP
- confirm `report_jobs` row exists
- run worker
- confirm `/report` returns `200`

## 13. Acceptance Criteria

Stage 7 is complete when:

- finishing an interview enqueues a persistent report job transactionally
- no report generation depends on FastAPI `BackgroundTasks`
- a stopped API process does not lose queued report work
- a worker can reclaim stale running jobs
- orphan `processing` report rows are repairable
- the frontend report polling contract remains stable
- full test suite passes
- PostgreSQL job tests pass with configured DSN

## 14. Risks

- Polling workers can create duplicate execution if leases are wrong.
  - Mitigation: explicit lease fields and idempotent completion logic.

- Job state and report state can diverge.
  - Mitigation: use one transactional enqueue path plus orphan recovery.

- A PostgreSQL polling queue is simpler than Celery but less scalable.
  - Acceptable for this stage because the primary goal is durability, not horizontal throughput.
