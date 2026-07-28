# Stage 7 Report Job Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace process-coupled report execution with a persistent PostgreSQL-backed job queue and worker that can recover report generation after API restarts or worker crashes.

**Architecture:** Persist report work separately from final report results. The API uses one transactional enqueue path to create or confirm both `interview_reports.processing` and `report_jobs.queued`, while a worker process claims jobs with leases, executes pure report generation, and persists both job state and report state back into PostgreSQL.

**Tech Stack:** Python 3.11, FastAPI, PostgreSQL, psycopg2-binary, pytest, current DeepSeek/OpenAI-compatible LLM integration.

---

## File Structure

- Create: `app/services/report_jobs.py`
  - PostgreSQL-backed report job store, transactional enqueue logic, and lease handling.

- Create: `app/services/report_worker.py`
  - Worker polling loop and execution adapter.

- Modify: `app/services/report_tasks.py`
  - Extract pure report execution from process-specific adapters.

- Modify: `app/api/routes.py`
  - Replace `BackgroundTasks.add_task(...)` enqueue logic with durable report job creation.

- Modify: `app/services/runtime.py`
  - Provide:
    - `build_report_job_store()`
    - `build_report_executor()`
    - optional `build_report_worker()`

- Modify: `pytest.ini`
  - Add `pg_jobs` marker.

- Create: `tests/test_report_jobs.py`
  - Unit and integration tests for enqueue/claim/retry/reclaim/orphan recovery.

- Create: `tests/test_report_worker.py`
  - Worker execution tests with fake evaluator or fake LLM.

- Modify: `tests/test_report_tasks.py`
  - Assert the pure execution function does not require `BackgroundTasks`.

- Modify: `tests/test_report_api.py`
  - Assert API enqueues work transactionally instead of binding to `BackgroundTasks`.

Unified test commands:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

PostgreSQL job tests:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_report_jobs.py tests/test_report_worker.py -q -m pg_jobs
```

---

### Task 1: Add Report Job Table, Transactional Enqueue, And Cleanup Fixture

**Files:**
- Modify: `pytest.ini`
- Create: `tests/test_report_jobs.py`
- Create: `app/services/report_jobs.py`

- [ ] **Step 1: Register pytest marker**

Update `pytest.ini`:

```ini
[pytest]
markers =
    pgvector: tests that require PostgreSQL with pgvector
    pg_runtime: tests that require PostgreSQL runtime persistence
    pg_jobs: tests that require PostgreSQL report job execution
```

- [ ] **Step 2: Write failing report job tests and cleanup fixture**

Create `tests/test_report_jobs.py`:

```python
import os
from uuid import uuid4

import pytest

from app.services.report_jobs import PostgresReportJobStore


pytestmark = pytest.mark.pg_jobs


def require_dsn():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for pg_jobs tests")
    return dsn


def make_prefix():
    return "test_jobs_" + uuid4().hex[:12]


@pytest.fixture
def job_store():
    store = PostgresReportJobStore(dsn=require_dsn(), table_prefix=make_prefix())
    yield store
    store.drop_tables()


def test_enqueue_report_request_creates_job_and_processing_report(job_store):
    created = job_store.enqueue_report_request(session_id="s1")

    job = job_store.get_job_by_session("s1")
    report = job_store.get_report_row("s1")

    assert created["session_id"] == "s1"
    assert job["status"] == "queued"
    assert report["status"] == "processing"


def test_enqueue_report_request_is_idempotent_for_same_session(job_store):
    first = job_store.enqueue_report_request(session_id="s1")
    second = job_store.enqueue_report_request(session_id="s1")

    assert first["job_id"] == second["job_id"]
    assert job_store.count_jobs() == 1
    assert job_store.count_reports() == 1


def test_claim_marks_job_running(job_store):
    job_store.enqueue_report_request(session_id="s1")

    claimed = job_store.claim_next(worker_id="worker-1")

    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["lease_owner"] == "worker-1"
```

- [ ] **Step 3: Run test to verify red**

Run:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_report_jobs.py -q -m pg_jobs
```

Expected: FAIL with missing module or missing implementation.

- [ ] **Step 4: Implement minimal PostgreSQL report job store**

Create `app/services/report_jobs.py` with:

- schema initialization for `report_jobs`
- helper methods:
  - `drop_tables()`
  - `count_jobs()`
  - `count_reports()`
  - `get_job_by_session(session_id)`
  - `get_report_row(session_id)`
- `enqueue_report_request(session_id)`
  - one PostgreSQL transaction
  - upsert `interview_reports.processing`
  - insert-or-reuse `report_jobs.queued`
- `claim_next(worker_id)`

- [ ] **Step 5: Run test to verify green**

Run the same command and expect PASS.

- [ ] **Step 6: Commit**

```powershell
git add pytest.ini app/services/report_jobs.py tests/test_report_jobs.py
git commit -m "feat: add transactional postgres report job store"
```

---

### Task 2: Add Explicit Retry, Reclaim, And Orphan Recovery

**Files:**
- Modify: `tests/test_report_jobs.py`
- Modify: `app/services/report_jobs.py`

- [ ] **Step 1: Write failing reclaim, retry, and orphan recovery tests**

Append:

```python
def test_expired_running_job_can_be_reclaimed(job_store):
    job_store.enqueue_report_request(session_id="s1")
    first = job_store.claim_next(worker_id="worker-1", lease_seconds=-1)

    reclaimed = job_store.claim_next(worker_id="worker-2")

    assert first is not None
    assert reclaimed is not None
    assert reclaimed["session_id"] == "s1"
    assert reclaimed["lease_owner"] == "worker-2"
    assert reclaimed["status"] == "running"


def test_retryable_failure_marks_retrying_until_max_attempts(job_store):
    created = job_store.enqueue_report_request(session_id="s1")

    job_store.mark_retryable_failure(created["job_id"], "transient error")
    current = job_store.get_job(created["job_id"])
    assert current["status"] == "retrying"

    job_store.mark_retryable_failure(created["job_id"], "transient error")
    job_store.mark_retryable_failure(created["job_id"], "transient error")
    terminal = job_store.get_job(created["job_id"])
    assert terminal["status"] == "failed"


def test_repair_orphan_processing_reports_enqueues_missing_job(job_store):
    job_store.insert_processing_report_only(session_id="s-orphan")

    repaired = job_store.repair_orphan_processing_reports()
    job = job_store.get_job_by_session("s-orphan")

    assert repaired == 1
    assert job is not None
    assert job["status"] == "queued"
```

- [ ] **Step 2: Run test to verify red**

Run the same `pytest tests/test_report_jobs.py -q -m pg_jobs` command.

Expected: FAIL due to missing reclaim/retry/orphan methods.

- [ ] **Step 3: Implement reclaim and retry methods**

Add:

- `get_job(job_id)`
- `mark_retryable_failure(job_id, error)`
- `mark_failed(job_id, error)`
- `insert_processing_report_only(session_id)` for test-only setup support
- `repair_orphan_processing_reports()`
- reclaim logic in `claim_next(...)`
  - claim `queued`
  - reclaim expired `running`
  - reclaim `retrying`
  - set status directly to `running`

- [ ] **Step 4: Run test to verify green**

Run the same command and expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_jobs.py tests/test_report_jobs.py
git commit -m "feat: add report job reclaim retry and orphan recovery"
```

---

### Task 3: Extract Pure Report Execution Function

**Files:**
- Modify: `tests/test_report_tasks.py`
- Modify: `app/services/report_tasks.py`

- [ ] **Step 1: Write failing task test**

Add a concrete test:

```python
def test_run_report_generation_returns_report_and_persists_side_effects():
    store = FakeStoreWithFinishedSession()
    llm = FakeReportLLM()
    vector_store = FakeVectorStore()

    report = run_report_generation(
        session_id="s1",
        store=store,
        llm=llm,
        vector_store=vector_store,
    )

    assert report.session_id == "s1"
    assert store.saved_report is report
    assert store.failed_error is None
```

This test must prove:

- no `BackgroundTasks` object is involved
- pure execution depends only on injected store/llm/vector_store
- final report is returned and persisted through store side effects

- [ ] **Step 2: Run test to verify red**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py -q
```

Expected: FAIL because `run_report_generation(...)` does not yet exist.

- [ ] **Step 3: Implement pure execution entrypoint**

Refactor `app/services/report_tasks.py` so that:

- `run_report_generation(session_id, store, llm, vector_store)` performs pure execution
- `generate_report_for_session(...)` becomes a thin compatibility wrapper if still needed

- [ ] **Step 4: Run test to verify green**

Run the same command and expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_tasks.py tests/test_report_tasks.py
git commit -m "refactor: extract pure report generation execution"
```

---

### Task 4: Expand Runtime Factories For API And Worker

**Files:**
- Create or Modify: `tests/test_runtime_provider.py`
- Modify: `app/services/runtime.py`

- [ ] **Step 1: Write failing runtime factory tests**

Add tests covering:

```python
def test_build_report_job_store_uses_postgres_dsn():
    ...


def test_build_report_executor_bundles_session_store_llm_and_vector_store():
    ...
```

Assertions must prove:

- `build_report_job_store()` uses `POSTGRES_DSN`
- `build_report_executor()` wires:
  - session store
  - llm
  - vector store

- [ ] **Step 2: Run test to verify red**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py -q
```

Expected: FAIL due to missing factory functions.

- [ ] **Step 3: Implement runtime factories**

Add to `runtime.py`:

- `build_report_job_store()`
- `build_report_executor()`
- optional `build_report_worker()`

Keep existing session store override behavior intact for tests.

- [ ] **Step 4: Run test to verify green**

Run the same command and expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/runtime.py tests/test_runtime_provider.py
git commit -m "feat: add runtime factories for report jobs and worker"
```

---

### Task 5: Replace API BackgroundTasks With Durable Enqueue

**Files:**
- Modify: `tests/test_report_api.py`
- Modify: `app/api/routes.py`
- Modify: `app/services/runtime.py`

- [ ] **Step 1: Write failing API enqueue test**

Add a test that finishes an interview and verifies:

- report record is marked `processing`
- report job store receives exactly one queued job
- request returns immediately
- no `BackgroundTasks.add_task(...)` path is required for correctness

- [ ] **Step 2: Run test to verify red**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py -q
```

Expected: FAIL because routes still depend on `BackgroundTasks`.

- [ ] **Step 3: Implement durable enqueue path**

Change:

- `_schedule_report_if_needed(...)` in `routes.py`
- runtime wiring in `runtime.py`

New behavior:

- `turn.status == finished`
- `report_job_store.enqueue_report_request(session_id)`

Do not separately call `store.mark_report_processing(session_id)` from the API route.

- [ ] **Step 4: Run test to verify green**

Run the same command and expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api/routes.py app/services/runtime.py tests/test_report_api.py
git commit -m "feat: enqueue durable report jobs from API"
```

---

### Task 6: Add Worker Loop And Completion Path

**Files:**
- Create: `tests/test_report_worker.py`
- Create: `app/services/report_worker.py`

- [ ] **Step 1: Write failing worker tests**

Create tests for:

- claiming one queued job
- running pure report execution
- marking job `completed`
- leaving `interview_reports` in `completed`

Example:

```python
def test_worker_completes_queued_report_job():
    ...
```

- [ ] **Step 2: Run test to verify red**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_worker.py -q
```

Expected: FAIL because worker module does not exist.

- [ ] **Step 3: Implement worker loop**

Add:

- `run_one_job(...)`
- optional `run_forever(...)`

Behavior:

- claim next job
- run pure report generation
- mark job completed / retrying / failed

- [ ] **Step 4: Run test to verify green**

Run the same command and expect PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_worker.py tests/test_report_worker.py
git commit -m "feat: add report worker execution loop"
```

---

### Task 7: Real PostgreSQL Job Flow Verification

**Files:**
- Modify as needed based on failures from verification.

- [ ] **Step 1: Run PostgreSQL job tests**

Run:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_report_jobs.py tests/test_report_worker.py -q -m pg_jobs
```

Expected: PASS.

- [ ] **Step 2: Run full suite**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Manual durable-mode smoke test**

Run API process:

```powershell
$env:INTERVIEW_RUNTIME_STORE='postgres'
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Run worker process:

```powershell
$env:INTERVIEW_RUNTIME_STORE='postgres'
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m app.services.report_worker
```

Expected:

- finish interview through HTTP
- `/report` returns `202`, then `200`
- stopping and restarting the API process does not lose queued report work
- stopping a worker mid-job allows stale lease reclaim

- [ ] **Step 4: Commit verification fixes if needed**

```powershell
git add app tests
git commit -m "test: verify durable report job execution"
```

---

## Self-Review

Spec coverage:

- transactional enqueue is covered in Task 1
- retry/reclaim/orphan recovery is covered in Task 2
- pure execution extraction is covered in Task 3
- runtime dependency factories are covered in Task 4
- API enqueue replacement is covered in Task 5
- worker runtime is covered in Task 6
- real PostgreSQL verification is covered in Task 7

Placeholder scan:

- removed the prior `...` placeholder from Task 3
- teardown strategy is explicit through `drop_tables()` fixture use

Type consistency:

- `report_jobs.py` owns job persistence and recovery
- `report_tasks.py` owns pure report execution
- `report_worker.py` owns worker lifecycle
- `runtime.py` owns dependency construction
- API layer only enqueues work
