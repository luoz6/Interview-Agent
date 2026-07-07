# Stage 25 Local V1 RC Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze Local V1 as a release candidate by running the full real-browser, real-Postgres, real-worker, real-LLM acceptance flow and closing only defects found during that acceptance.

**Architecture:** Keep the current four-page Local V1 runtime, default local Postgres runtime, pgvector knowledge store, Postgres report job worker, and visible question-evaluation trace. This stage does not add Redis, Celery, WebSocket, LangGraph, login, Docker, or automated browser tooling; it validates the current release candidate and records the evidence needed to move to Stage 26 architecture work.

**Tech Stack:** FastAPI, vanilla ES modules, static HTML, Tailwind-generated `prototype.css`, PostgreSQL 5432, pgvector, DeepSeek/OpenAI-compatible LLM, pytest, Node syntax checks, manual browser acceptance.

---

## Scope

This stage does:

- Verify the built-in local Postgres defaults with no `POSTGRES_DSN` environment variable.
- Verify local database health: database `interview`, user `postgres`, `vector` extension, and non-empty `knowledge_chunks`.
- Run the automated baseline before browser work.
- Run the real browser path: `/prep` -> `/interview` -> `/report-processing` -> `/report-detail` -> PDF download.
- Verify report worker behavior when it is delayed and then started.
- Verify persistence across FastAPI process restart.
- Verify Report Detail shows saved question-evaluation records.
- Record the RC acceptance result in `docs/stage-21-browser-e2e-acceptance.md`.
- Fix only blocking defects discovered during this acceptance.

This stage does not:

- Introduce new runtime infrastructure.
- Add Playwright or browser automation.
- Redesign the UI.
- Add account login, multi-user isolation, Docker deployment, Redis, Celery, WebSocket, or LangGraph.
- Refactor report generation unless a real acceptance defect requires a small targeted fix.

## File Structure

- Modify: `tests/test_local_v1_docs.py`
  - Add regression checks that README/runbook/acceptance docs describe Stage 25 RC acceptance and built-in Postgres defaults.
- Modify: `docs/stage-21-browser-e2e-acceptance.md`
  - Convert pending Stage 24 acceptance evidence into Stage 25 RC acceptance sections and final result.
- Modify: `docs/local-v1-runbook.md`
  - Add RC-specific checks for worker-delayed completion and service restart persistence.
- Modify: `README.md`
  - Mark Local V1 as RC-ready only after Stage 25 acceptance is recorded.
- Potentially modify, only if a real defect is found:
  - `app/static/api.js`
  - `app/static/prep.js`
  - `app/static/interview.js`
  - `app/static/report-processing.js`
  - `app/static/report-detail.js`
  - `app/test1.html`
  - `app/test2.html`
  - `app/test3.html`
  - `app/test4.html`
  - `app/api/routes.py`
  - `app/services/runtime.py`
  - `app/services/report_worker.py`
  - Targeted tests matching the defect.

---

### Task 1: Add Stage 25 Documentation Guardrails

**Files:**
- Modify: `tests/test_local_v1_docs.py`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`

- [ ] **Step 1: Write the failing documentation regression test**

Append this test to `tests/test_local_v1_docs.py`:

```python
def test_docs_describe_stage_25_local_v1_rc_acceptance():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected_phrases = (
        "Stage 25 Local V1 RC acceptance",
        "built-in local PostgreSQL defaults",
        "worker-delayed report completion",
        "service restart persistence",
        "question evaluation trace",
    )

    for phrase in expected_phrases:
        assert phrase in readme
        assert phrase in runbook
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_25_local_v1_rc_acceptance -q
```

Expected: FAIL because README and runbook do not yet contain all Stage 25 phrases.

- [ ] **Step 3: Update README with the Stage 25 RC acceptance position**

In `README.md`, under `## Current Architecture Position`, add this paragraph after the existing Report Detail trace paragraph:

```markdown
Stage 25 Local V1 RC acceptance is the release gate before Stage 26 architecture work. It verifies the built-in local PostgreSQL defaults, worker-delayed report completion, service restart persistence, and the Report Detail question evaluation trace with the real browser flow.
```

- [ ] **Step 4: Update runbook with the Stage 25 RC acceptance position**

In `docs/local-v1-runbook.md`, under `## 1.1 Architecture Position`, add this paragraph after the existing Report Detail trace paragraph:

```markdown
Stage 25 Local V1 RC acceptance is the release gate before Stage 26 architecture work. It verifies the built-in local PostgreSQL defaults, worker-delayed report completion, service restart persistence, and the Report Detail question evaluation trace with the real browser flow.
```

- [ ] **Step 5: Run the focused documentation test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_25_local_v1_rc_acceptance -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git diff --cached --name-status
git commit -m "docs: describe stage 25 rc acceptance gate"
```

Expected staged files:

```text
M	README.md
M	docs/local-v1-runbook.md
M	tests/test_local_v1_docs.py
```

---

### Task 2: Prepare The Stage 25 Acceptance Record

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write the failing acceptance-record regression test**

Append this test to `tests/test_local_v1_docs.py`:

```python
def test_stage_25_acceptance_record_has_rc_sections():
    record = read_text("docs/stage-21-browser-e2e-acceptance.md")

    assert "## Stage 25 RC Execution Notes" in record
    assert "## Stage 25 RC Resilience Checklist" in record
    assert "## Stage 25 RC Defect Log" in record
    assert "worker-delayed report completion" in record
    assert "service restart persistence" in record
    assert "built-in local PostgreSQL defaults" in record
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_stage_25_acceptance_record_has_rc_sections -q
```

Expected: FAIL because the Stage 25 RC sections do not exist yet.

- [ ] **Step 3: Insert the Stage 25 RC sections**

In `docs/stage-21-browser-e2e-acceptance.md`, insert this block before `## Final Status`:

```markdown
## Stage 25 RC Execution Notes

| Item | Value |
| --- | --- |
| Execution date | 2026-07-07 |
| Browser | Pending manual execution |
| Server URL | `http://127.0.0.1:8000` |
| Runtime store | PostgreSQL with built-in local PostgreSQL defaults |
| Database | `postgresql://postgres:postgres@127.0.0.1:5432/interview` |
| LLM provider | DeepSeek-compatible OpenAI API |
| Knowledge chunks | Pending database check |
| Report worker | Pending manual execution |
| Question evaluation trace | Pending manual execution |
| PDF download | Pending manual execution |

## Stage 25 RC Resilience Checklist

| Step | Expected result | Result | Notes |
| --- | --- | --- | --- |
| Built-in local PostgreSQL defaults | Clearing `POSTGRES_DSN`, `INTERVIEW_RUNTIME_STORE`, `INTERVIEW_RUNTIME_TABLE_PREFIX`, and `PGVECTOR_TABLE` still resolves runtime stores to `postgresql://postgres:postgres@127.0.0.1:5432/interview` | Pending |  |
| Worker-delayed report completion | Finishing an interview while the report worker is stopped leaves processing visible; starting the worker completes the report | Pending |  |
| Service restart persistence | Restarting FastAPI after report completion still loads `/report-detail?session_id=...` from PostgreSQL | Pending |  |
| Question evaluation trace | `/report-detail?session_id=...` shows saved question evaluation records loaded from `/api/interviews/{session_id}/question-evaluations` | Pending |  |

## Stage 25 RC Defect Log

| ID | Severity | Page/API | Symptom | Fix commit | Verification |
| --- | --- | --- | --- | --- | --- |
| None | - | - | No Stage 25 RC browser defects recorded yet | - | - |
```

- [ ] **Step 4: Run the focused test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_stage_25_acceptance_record_has_rc_sections -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md tests/test_local_v1_docs.py
git diff --cached --name-status
git commit -m "docs: prepare stage 25 rc acceptance record"
```

Expected staged files:

```text
M	docs/stage-21-browser-e2e-acceptance.md
M	tests/test_local_v1_docs.py
```

---

### Task 3: Verify Automated And Database Baseline

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Run the focused automated baseline**

Run each command separately:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_local_v1_docs.py -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

Expected:

- Pytest passes.
- Every `node --check` command exits `0`.
- CSS build exits `0`.
- A Browserslist stale-data warning is acceptable.

- [ ] **Step 2: Run the full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS with skipped integration tests allowed when their marker prerequisites are absent.

- [ ] **Step 3: Verify the database directly**

Run:

```powershell
@'
import psycopg2

dsn = "postgresql://postgres:postgres@127.0.0.1:5432/interview"
with psycopg2.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute("select current_database(), current_user")
        print("database_user=", cur.fetchone())
        cur.execute("select extname from pg_extension where extname='vector'")
        print("vector_extension=", cur.fetchone())
        cur.execute("select to_regclass('public.knowledge_chunks')")
        table = cur.fetchone()[0]
        print("knowledge_table=", table)
        if table:
            cur.execute("select count(*) from knowledge_chunks")
            print("knowledge_chunks_count=", cur.fetchone()[0])
'@ | F:\python3.11\python.exe -
```

Expected output shape:

```text
database_user= ('interview', 'postgres')
vector_extension= ('vector',)
knowledge_table= knowledge_chunks
knowledge_chunks_count= 10
```

The count may be greater than `10`; it must be greater than `0`. If the count is `0`, run:

```powershell
F:\python3.11\python.exe scripts/load_knowledge.py
```

- [ ] **Step 4: Verify built-in runtime defaults without environment variables**

Run:

```powershell
@'
import os
from app.services.runtime import DEFAULT_POSTGRES_DSN, build_report_job_store, build_session_store
from app.services.vector_store import PgVectorKnowledgeStore

for key in ("POSTGRES_DSN", "INTERVIEW_RUNTIME_STORE", "INTERVIEW_RUNTIME_TABLE_PREFIX", "INTERVIEW_TABLE_PREFIX", "PGVECTOR_TABLE"):
    os.environ.pop(key, None)

session_store = build_session_store()
job_store = build_report_job_store()
knowledge_store = PgVectorKnowledgeStore.from_env()

print("default_dsn=", DEFAULT_POSTGRES_DSN)
print("session_store_dsn=", session_store.dsn)
print("session_tables=", session_store.list_runtime_tables())
print("job_store_dsn=", job_store.dsn)
print("job_table_prefix=", job_store.table_prefix)
print("knowledge_store_dsn=", knowledge_store.dsn)
print("knowledge_table=", knowledge_store.table_name)
'@ | F:\python3.11\python.exe -
```

Expected output includes:

```text
default_dsn= postgresql://postgres:postgres@127.0.0.1:5432/interview
session_store_dsn= postgresql://postgres:postgres@127.0.0.1:5432/interview
job_store_dsn= postgresql://postgres:postgres@127.0.0.1:5432/interview
knowledge_store_dsn= postgresql://postgres:postgres@127.0.0.1:5432/interview
knowledge_table= knowledge_chunks
```

- [ ] **Step 5: Run real Postgres integration tests**

Run:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
F:\python3.11\python.exe -m pytest tests/test_postgres_session_store.py tests/test_report_jobs.py tests/test_vector_store.py tests/test_vector_store_pgvector.py -q
```

Expected: PASS.

- [ ] **Step 6: Record automated baseline results**

In `docs/stage-21-browser-e2e-acceptance.md`:

- Replace automated verification rows for commands executed in Steps 1 and 2 with `Pass`.
- In `Stage 25 RC Execution Notes`, replace `Knowledge chunks | Pending database check` with the integer printed by Step 3.
- In `Stage 25 RC Resilience Checklist`, replace the built-in defaults row result with `Pass`.

- [ ] **Step 7: Commit baseline evidence**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record stage 25 automated baseline"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 4: Run The Main Real Browser Flow

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Confirm the LLM key is present without printing it**

Run:

```powershell
if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY must be set before Stage 25 browser acceptance" }
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

Expected: command completes without printing the key.

- [ ] **Step 2: Start FastAPI**

Start the server in a dedicated PowerShell window:

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Expected:

- Server starts on `http://127.0.0.1:8000`.
- No startup exception.

- [ ] **Step 3: Start the report worker**

Start the worker in a second PowerShell window:

```powershell
F:\python3.11\python.exe -m app.services.report_worker
```

Expected:

- Worker stays running.
- Worker can claim report jobs when the browser flow finishes an interview.

- [ ] **Step 4: Open the prep page**

Open:

```text
http://127.0.0.1:8000/prep
```

Expected:

- Prep page renders.
- JD textarea is visible.
- Resume textarea is visible.
- No browser console error appears during initial load.

Record the `Open /prep` row as `Pass` or `Fail`.

- [ ] **Step 5: Generate a plan**

Use this JD:

```text
Backend engineer responsible for core transaction services and cache platform reliability. The role requires Python, FastAPI, Redis, PostgreSQL, message queues, Docker, and Linux. The interview should cover high-concurrency system design, API idempotency, cache consistency, slow-query optimization, observability, and production incident handling.
```

Use this resume:

```text
Five years of Python backend experience building order, inventory, payment reconciliation, and user-growth systems. Built FastAPI services with PostgreSQL, Redis, Kafka, Docker, and Linux deployment. Led Redis cache-aside improvements, PostgreSQL index tuning, queue backlog handling, API idempotency cleanup, logging, monitoring, and production troubleshooting.
```

Click the plan-generation button.

Expected:

- `/api/prep` succeeds.
- Plan title renders.
- Job tags render.
- At least 3 questions render.

Record the `Generate plan` row as `Pass` or `Fail`.

- [ ] **Step 6: Save and restore the draft**

Actions:

1. Save the draft.
2. Refresh the browser page.
3. Restore the draft.

Expected:

- Save action reports success.
- Browser local storage contains `interviewDraftId`.
- JD and resume content return after restore.
- Tags return after restore if they were generated.

Record `Save draft` and `Restore draft` rows as `Pass` or `Fail`.

- [ ] **Step 7: Start the interview**

Click the start-interview button.

Expected:

- Browser navigates to `/interview?session_id=...`.
- URL has a non-empty `session_id`.
- First question renders.
- Question navigation renders.

Record the `Start interview` row as `Pass` or `Fail`. Copy the `session_id` into the Stage 25 notes for later resilience checks.

- [ ] **Step 8: Submit a streamed answer**

Use this answer:

```text
I would use cache-aside for most reads: read Redis first, fall back to PostgreSQL on miss, then refill the cache with a short TTL plus jitter. For writes I would update PostgreSQL first and then delete the Redis key. For stronger consistency I would add delayed double delete or an outbox-backed invalidation retry. I would track hit rate, slow queries, stale reads, and Redis latency so we can prove the cache is helping instead of hiding database problems.
```

Expected:

- SSE chunks or final response render.
- Conversation updates.
- A follow-up or next question appears.
- No browser console error appears.

Record the `Submit streamed answer` row as `Pass` or `Fail`.

- [ ] **Step 9: Skip one question**

Click the skip control for one unanswered question.

Expected:

- The current question changes or the session finishes if no question remains.
- The skipped state is visible in the interview UI.
- Browser devtools network does not show a request to `/api/interviews/null/...`.

Record the `Skip question` row as `Pass` or `Fail`.

- [ ] **Step 10: Finish the interview**

Click the finish-interview control.

Expected:

- Browser navigates to `/report-processing?session_id=...`.
- The `session_id` is the same as the interview URL.

Record the `Finish interview` row as `Pass` or `Fail`.

- [ ] **Step 11: Wait for report processing**

Stay on `/report-processing?session_id=...`.

Expected:

- Status text renders.
- Progress UI updates.
- RAG or report progress summary renders when available.
- The page eventually reaches a completed state or offers a view-report action.

Record the `Report processing` row as `Pass` or `Fail`.

- [ ] **Step 12: Verify report detail and PDF**

Open `/report-detail?session_id=...`.

Expected:

- Overall score renders.
- Summary renders.
- Five dimensions render.
- Per-question feedback renders.
- Evidence excerpts render.
- Question evaluation trace section renders saved records.
- PDF download succeeds and does not clear the visible report.

Record the `Report detail`, `Question evaluation trace`, and `PDF download` rows as `Pass` or `Fail`.

- [ ] **Step 13: Record main browser-flow evidence**

In `docs/stage-21-browser-e2e-acceptance.md`:

- Update every executed manual checklist row to `Pass` or `Fail`.
- In `Stage 25 RC Execution Notes`, replace these rows:

```markdown
| Browser | Chrome or Edge |
| Report worker | Pass |
| Question evaluation trace | Pass |
| PDF download | Pass |
```

Use `Fail` only for rows that failed and add the observed symptom to the defect log in Task 6.

- [ ] **Step 14: Commit main-flow acceptance evidence**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record stage 25 main browser flow"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 5: Run Resilience Acceptance

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Test worker-delayed report completion**

Actions:

1. Stop the report worker process.
2. In the browser, create a second interview session through `/prep`.
3. Answer or skip enough questions to finish the interview.
4. Confirm the browser reaches `/report-processing?session_id=...`.
5. Wait 30 seconds with the worker stopped.
6. Start the worker again:

```powershell
F:\python3.11\python.exe -m app.services.report_worker
```

Expected:

- While the worker is stopped, the processing page stays in a processing state and does not show a false completed report.
- After the worker starts, the job completes.
- Report detail becomes available for the same `session_id`.

Record the `Worker-delayed report completion` resilience row as `Pass` or `Fail`.

- [ ] **Step 2: Test service restart persistence**

Actions:

1. Copy a completed report detail URL.
2. Stop the FastAPI process.
3. Start FastAPI again:

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

4. Open the copied `/report-detail?session_id=...` URL.

Expected:

- Report detail still loads from PostgreSQL.
- Score, summary, feedback, evidence, and question evaluation trace still render.

Record the `Service restart persistence` resilience row as `Pass` or `Fail`.

- [ ] **Step 3: Test missing-session error states**

Open these URLs:

```text
http://127.0.0.1:8000/interview
http://127.0.0.1:8000/report-processing
http://127.0.0.1:8000/report-detail
http://127.0.0.1:8000/report-detail?session_id=bad
```

Expected:

- Each page shell renders.
- Missing-session pages show a clear error state.
- Disabled controls remain disabled.
- Bad report-detail session shows an API error without breaking the page shell.

Record all corresponding error-state checklist rows as `Pass` or `Fail`.

- [ ] **Step 4: Record resilience evidence**

In `docs/stage-21-browser-e2e-acceptance.md`, update the `Stage 25 RC Resilience Checklist` rows:

```markdown
| Worker-delayed report completion | Finishing an interview while the report worker is stopped leaves processing visible; starting the worker completes the report | Pass | Verified with second session |
| Service restart persistence | Restarting FastAPI after report completion still loads `/report-detail?session_id=...` from PostgreSQL | Pass | Verified with completed report URL |
| Question evaluation trace | `/report-detail?session_id=...` shows saved question evaluation records loaded from `/api/interviews/{session_id}/question-evaluations` | Pass | Verified after report completion and after server restart |
```

Use `Fail` for rows that failed and add the observed symptom to the defect log in Task 6.

- [ ] **Step 5: Commit resilience evidence**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record stage 25 resilience acceptance"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 6: Close Any Acceptance Defects

**Files:**
- Modify only the smallest file set needed for each observed defect.
- Modify the matching test file for each defect.
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: If no defects were found, record the no-defect result**

If all browser and resilience rows passed, replace the current `Stage 25 RC Defect Log` body with:

```markdown
| None | - | - | No Stage 25 RC browser defects recorded | - | Automated baseline, browser flow, resilience checks |
```

Then skip to Task 7.

- [ ] **Step 2: For each defect, write a failing regression test first**

Use the most targeted test type:

- Static frontend defect: add a test to `tests/test_static_report_ui.py`.
- Page route defect: add a test to `tests/test_page_routes.py`.
- API contract defect: add a test to `tests/test_report_api.py` or `tests/test_api.py`.
- Postgres/report-worker defect: add a test to `tests/test_report_jobs.py`, `tests/test_report_worker.py`, or `tests/test_postgres_session_store.py`.

Example for a frontend null-session regression:

```python
def test_report_detail_defensively_skips_question_evaluations_without_session():
    js = read_static_file("report-detail.js")

    assert "if (!sessionId) return;" in js
    assert "/question-evaluations" in js
```

- [ ] **Step 3: Run the failing focused test**

Run the exact focused test command for the defect. Example:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_defensively_skips_question_evaluations_without_session -q
```

Expected: FAIL for the observed defect.

- [ ] **Step 4: Implement the smallest fix**

Keep the fix limited to the observed defect. Do not refactor surrounding code unless the focused test cannot be made meaningful without a small extraction.

- [ ] **Step 5: Run focused verification**

Run the focused test added in Step 2 plus any directly related test file. Example:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q
node --check app/static/report-detail.js
```

Expected: PASS.

- [ ] **Step 6: Update the defect log**

In `docs/stage-21-browser-e2e-acceptance.md`, add one row per fixed defect:

```markdown
| S25-1 | Blocking | Report Detail | Question evaluation request used a missing session id after direct page load | pending commit | Focused regression test and browser recheck passed |
```

Use the real page/API, symptom, and verification for the defect being fixed.

- [ ] **Step 7: Commit each defect fix separately**

Run:

```powershell
git add app/static/report-detail.js tests/test_static_report_ui.py docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "fix: resolve stage 25 acceptance defect S25-1"
```

Expected: one commit per defect, with the production fix, regression test, and defect-log update included in the same commit. If the defect touches different files, stage the exact files shown by `git status --short` for that defect only.

- [ ] **Step 8: Re-run the failed browser step**

After the fix commit, repeat the exact browser action that originally failed.

Expected:

- The previously failed row now passes.
- The defect log verification text matches the browser recheck.

---

### Task 7: Final RC Freeze Verification

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Confirm there are no Pending rows**

Run:

```powershell
Select-String -Path docs/stage-21-browser-e2e-acceptance.md -Pattern "Pending"
```

Expected: no output.

If output remains, finish the corresponding checklist item or mark it `Fail` with a defect-log row.

- [ ] **Step 2: Run the final automated verification suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run final JavaScript and CSS checks**

Run each command separately:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

Expected:

- Every Node syntax check exits `0`.
- CSS build exits `0`; Browserslist stale-data warning is acceptable.

- [ ] **Step 4: Run final real Postgres integration verification**

Run:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
F:\python3.11\python.exe -m pytest tests/test_postgres_session_store.py tests/test_report_jobs.py tests/test_vector_store.py tests/test_vector_store_pgvector.py -q
```

Expected: PASS.

- [ ] **Step 5: Set final acceptance status**

In `docs/stage-21-browser-e2e-acceptance.md`, replace the final status text with:

```markdown
Accepted as Local V1 RC. The real browser flow, built-in local PostgreSQL defaults, report worker completion, service restart persistence, question evaluation trace, PDF download, and automated verification all passed. No blocking Stage 25 defects remain.
```

If any blocking row still fails, use this text instead:

```markdown
Not accepted as Local V1 RC. Blocking Stage 25 defects remain in the defect log.
```

- [ ] **Step 6: Commit final RC acceptance**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: accept local v1 rc"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

- [ ] **Step 7: Audit the branch**

Run:

```powershell
git status --short
git log --oneline -8
```

Expected:

- Only unrelated pre-existing files remain dirty or untracked.
- Recent commits include Stage 25 docs guardrails, baseline evidence, browser-flow evidence, resilience evidence, optional defect fixes, and final RC acceptance.

---

## Self-Review

- Spec coverage: The plan covers Stage 25 as Local V1 RC acceptance, not architecture expansion. It includes built-in Postgres defaults, database health, automated checks, real browser E2E, worker-delayed completion, service restart persistence, question evaluation trace, PDF download, and defect closure.
- Placeholder scan: Dynamic values such as browser choice, knowledge count, and defect symptoms are recorded during acceptance, but every section has concrete commands, expected outputs, and exact file targets.
- Type consistency: The plan uses existing names: `POSTGRES_DSN`, `INTERVIEW_RUNTIME_STORE`, `INTERVIEW_RUNTIME_TABLE_PREFIX`, `PGVECTOR_TABLE`, `QuestionEvaluationRecord`, `/api/interviews/{session_id}/question-evaluations`, and `docs/stage-21-browser-e2e-acceptance.md`.
- Scope control: Redis, Celery, WebSocket, LangGraph, Playwright, Docker, login, and UI redesign are explicitly excluded from Stage 25.
