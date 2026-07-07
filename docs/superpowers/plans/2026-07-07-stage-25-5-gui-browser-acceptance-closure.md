# Stage 25.5 GUI Browser Acceptance Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining GUI browser acceptance blocker and mark Local V1 as RC accepted only after the real browser flow passes.

**Architecture:** Keep the current Local V1 architecture unchanged: FastAPI four-page frontend, built-in local PostgreSQL defaults, pgvector knowledge store, Postgres report worker, and Report Detail question-evaluation trace. This stage is an acceptance-closure stage, not an architecture or feature stage; it only cleans the local process environment, runs the manual browser checklist, records evidence, and fixes browser-discovered defects with targeted tests.

**Tech Stack:** FastAPI, vanilla ES modules, static HTML, Tailwind-generated `prototype.css`, PostgreSQL 5432, pgvector, DeepSeek/OpenAI-compatible LLM, pytest, Node syntax checks, manual GUI browser testing.

---

## Scope

This stage does:

- Resolve the stale `127.0.0.1:8000` listener noted in Stage 25.
- Start the current code on `http://127.0.0.1:8000`.
- Confirm `/openapi.json` includes `/api/interviews/{session_id}/question-evaluations`.
- Start a report worker in a separate PowerShell window with the same LLM environment variables.
- Run the real GUI browser path: `/prep` -> `/interview?session_id=...` -> `/report-processing?session_id=...` -> `/report-detail?session_id=...` -> PDF download.
- Verify browser-only behavior: localStorage draft restore, streamed answer/SSE UI, disabled controls on missing session pages, PDF download behavior, and visible question-evaluation trace.
- Update `docs/stage-21-browser-e2e-acceptance.md` from blocked/API-only evidence to real browser evidence.
- Fix only browser-discovered defects, each with a focused failing test first.

This stage does not:

- Add Playwright, Puppeteer, Selenium, or other browser automation.
- Add Redis, Celery, WebSocket, LangGraph, Docker, login, or multi-user isolation.
- Redesign the UI.
- Refactor report generation or worker internals unless a browser defect proves a small fix is required.
- Treat API-only checks as a replacement for GUI browser acceptance.

## File Structure

- Modify: `docs/stage-21-browser-e2e-acceptance.md`
  - Replace Stage 25 blocked/browser-not-run rows with real browser `Pass` or real `Fail`.
  - Replace final status with `Accepted as Local V1 RC` only if all blocking GUI rows pass.
- Potentially modify, only if a real browser defect is found:
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
  - Targeted tests matching the defect, usually `tests/test_static_report_ui.py`, `tests/test_page_routes.py`, `tests/test_report_api.py`, or `tests/test_local_v1_docs.py`.

---

### Task 1: Clean And Verify The Local Server Environment

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Identify anything listening on port 8000**

Run:

```powershell
netstat -ano | Select-String ':8000'
```

Expected:

- If there is no listener, continue to Step 3.
- If there is a listener, note the PID shown in the final column.

- [ ] **Step 2: Stop the stale 8000 listener if it is not the current Stage 25.5 server**

Inspect the process:

```powershell
$pidFromNetstat = 12345
Get-Process -Id $pidFromNetstat -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime,Path
```

If it is a stale local Python server from this project, stop it:

```powershell
Stop-Process -Id $pidFromNetstat -Force
```

Expected:

- `netstat -ano | Select-String ':8000'` no longer shows `LISTENING`.

If `Get-Process` cannot see the PID or `Stop-Process` cannot stop it, do not proceed as if 8000 is clean. Record this as `Fail` in the defect log and either resolve the OS-level process manually or use a clean terminal/session that can control the process.

- [ ] **Step 3: Confirm the LLM environment without printing secrets**

Run in the server PowerShell window:

```powershell
if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY must be set before Stage 25.5 browser acceptance" }
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

Expected: command completes without printing the key.

- [ ] **Step 4: Start FastAPI on port 8000**

Run in the same server PowerShell window:

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Expected:

- Server starts on `http://127.0.0.1:8000`.
- The same shell has `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` set.

- [ ] **Step 5: Verify health and current route registration**

Run in another PowerShell window:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health'
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/openapi.json' | ConvertTo-Json -Depth 8 | Select-String -Pattern 'question-evaluations'
```

Expected:

- Health returns `status: ok`.
- The OpenAPI output includes `question-evaluations`.

- [ ] **Step 6: Record environment cleanup evidence**

In `docs/stage-21-browser-e2e-acceptance.md`, update `Stage 25 RC Execution Notes`:

```markdown
| Server URL | `http://127.0.0.1:8000` |
```

In `Stage 25 RC Defect Log`, update or remove `S25-ENV-2`:

```markdown
| S25-ENV-2 | Medium | Local process environment | Port 8000 stale listener from Stage 25 was removed before Stage 25.5 browser acceptance | - | `/openapi.json` on 8000 includes `question-evaluations` |
```

If the stale listener could not be removed, keep `S25-ENV-2` as blocking and stop this plan until the process environment is clean.

- [ ] **Step 7: Commit environment cleanup evidence**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record stage 25.5 clean browser environment"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 2: Start The Report Worker For Browser Acceptance

**Files:**
- Verify only.

- [ ] **Step 1: Configure the worker PowerShell window**

Run in a second PowerShell window:

```powershell
if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY must be set in this worker window before Stage 25.5 browser acceptance" }
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

Expected: command completes without printing the key.

- [ ] **Step 2: Start the report worker**

Run in the same worker PowerShell window:

```powershell
F:\python3.11\python.exe -m app.services.report_worker
```

Expected:

- Worker remains running.
- Worker logs do not show startup errors.

- [ ] **Step 3: Warm up the default session store schema**

Before checking runtime tables, force the FastAPI process to instantiate `PostgresInterviewSessionStore`. The report worker initializes report job tables on startup, but session/message/question-evaluation tables are created when the session store is first constructed.

Run:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/reports'
```

Expected:

- The request returns a JSON object with `items` and `total`.
- This request initializes the default session store and creates `interview_sessions`, `interview_messages`, `interview_reports`, and `interview_question_evaluations` if they were not already present.

- [ ] **Step 4: Confirm worker and server can see the default runtime tables**

Run in a third PowerShell window:

```powershell
@'
import psycopg2
dsn = "postgresql://postgres:postgres@127.0.0.1:5432/interview"
with psycopg2.connect(dsn) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            select table_name
            from information_schema.tables
            where table_schema='public'
              and table_name in (
                'interview_sessions',
                'interview_messages',
                'interview_reports',
                'interview_report_jobs',
                'interview_question_evaluations'
              )
            order by table_name
        """)
        print([row[0] for row in cur.fetchall()])
'@ | F:\python3.11\python.exe -
```

Expected output includes:

```text
['interview_messages', 'interview_question_evaluations', 'interview_report_jobs', 'interview_reports', 'interview_sessions']
```

---

### Task 3: Execute The Main GUI Browser Flow

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Open the prep page in a real browser**

Open:

```text
http://127.0.0.1:8000/prep
```

Expected:

- Prep page renders.
- JD textarea is visible.
- Resume textarea is visible.
- Tags are empty/default before plan generation.
- Browser console has no error on initial load.

Record:

```markdown
| Open `/prep` | Prep page renders with JD/resume inputs and empty tags | Pass | Verified in Chrome or Edge |
```

- [ ] **Step 2: Generate a plan**

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

- Plan title renders.
- Job tags render.
- At least 3 questions render.
- Browser network shows `/api/prep` returning `200`.
- Browser console has no error.

Record `Generate plan` as `Pass` or `Fail`.

- [ ] **Step 3: Save and restore the draft**

Actions:

1. Click save draft.
2. Confirm success notice.
3. Open browser devtools and confirm `localStorage.interviewDraftId` exists.
4. Refresh the page.
5. Click restore draft.

Expected:

- JD and resume return.
- Tags return if generated.
- `interviewDraftId` remains in localStorage.

Record `Save draft` and `Restore draft` rows as `Pass` or `Fail`.

- [ ] **Step 4: Start the interview**

Click start interview.

Expected:

- Browser navigates to `/interview?session_id=...`.
- URL contains a non-empty `session_id`.
- First question renders.
- Question navigation renders.
- Browser console has no error.

Record `Start interview` as `Pass` or `Fail`, and copy the `session_id` into the notes if useful.

- [ ] **Step 5: Submit a streamed answer**

Use this answer:

```text
I would use cache-aside for most reads: read Redis first, fall back to PostgreSQL on miss, then refill the cache with a short TTL plus jitter. For writes I would update PostgreSQL first and then delete the Redis key. For stronger consistency I would add delayed double delete or an outbox-backed invalidation retry. I would track hit rate, slow queries, stale reads, and Redis latency so we can prove the cache is helping instead of hiding database problems.
```

Click submit.

Expected:

- Browser network shows `/api/interviews/{session_id}/answer/stream`.
- SSE chunks or the final done event update the UI.
- Conversation updates without manual refresh.
- Follow-up or next question appears.
- Question navigation refreshes.
- Browser console has no error.

Record `Submit streamed answer` as `Pass` or `Fail`.

- [ ] **Step 6: Skip one question**

Click the skip control.

Expected:

- Current question changes or the session reaches finished state.
- Skipped state is visible in the question/navigation UI.
- Browser network does not show `/api/interviews/null/...`.
- Browser console has no error.

Record `Skip question` as `Pass` or `Fail`.

- [ ] **Step 7: Finish the interview**

Click finish interview.

Expected:

- Browser navigates to `/report-processing?session_id=...`.
- The same session id is preserved.
- Browser console has no error.

Record `Finish interview` as `Pass` or `Fail`.

- [ ] **Step 8: Observe report processing**

Stay on `/report-processing?session_id=...`.

Expected:

- Status text renders.
- Progress UI updates.
- RAG/report progress summary renders when available.
- Page reaches completed state or enables view-report action.
- Browser console has no error.

Record `Report processing` as `Pass` or `Fail`.

- [ ] **Step 9: Verify report detail and question evaluation trace**

Open or navigate to:

```text
http://127.0.0.1:8000/report-detail?session_id=the-session-id-from-the-interview-url
```

Expected:

- Overall score renders.
- Summary renders.
- Five dimension scores render.
- Per-question feedback renders.
- Evidence section renders, even if evidence count is zero.
- `逐题评估链路` / question-evaluation section renders records.
- Browser network shows `/api/interviews/{session_id}/question-evaluations` returning `200` and `total > 0`.
- Browser console has no error.

Record `Report detail` and `Question evaluation trace` as `Pass` or `Fail`.

- [ ] **Step 10: Download PDF**

Click the PDF download control.

Expected:

- Browser downloads a file named like `interview-report-the-session-id.pdf`.
- Downloaded PDF opens.
- Visible report content remains on screen.
- Browser console has no error.

Record `PDF download` as `Pass` or `Fail`.

- [ ] **Step 11: Update Stage 25 RC execution notes**

In `docs/stage-21-browser-e2e-acceptance.md`, update the `Stage 25 RC Execution Notes` table so the browser block is closed explicitly during the main GUI run.

Use this shape, replacing `Chrome` with the actual browser if different:

```markdown
| Browser | Chrome |
| Server URL | `http://127.0.0.1:8000` |
| Runtime store | PostgreSQL with built-in local PostgreSQL defaults |
| Report worker | Pass via default worker process |
| Question evaluation trace | Pass via GUI and API: browser rendered records and `/question-evaluations` returned `total > 0` |
| PDF download | Pass via GUI download |
```

Do not leave the old value:

```markdown
| Browser | Blocked: no GUI browser/control available in this tool session |
```

- [ ] **Step 12: Commit main browser-flow evidence**

After updating the checklist rows in `docs/stage-21-browser-e2e-acceptance.md`, run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record stage 25.5 browser flow"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 4: Execute Browser Error-State Checks

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Check `/interview` without `session_id`**

Open:

```text
http://127.0.0.1:8000/interview
```

Expected:

- Page shell renders.
- Missing-session error is visible.
- Answer controls are disabled.
- Browser console has no error.

Record the row as `Pass` or `Fail`.

- [ ] **Step 2: Check `/report-processing` without `session_id`**

Open:

```text
http://127.0.0.1:8000/report-processing
```

Expected:

- Page shell renders.
- Missing-session error is visible.
- View-report button is disabled.
- Browser console has no error.

Record the row as `Pass` or `Fail`.

- [ ] **Step 3: Check `/report-detail` without `session_id`**

Open:

```text
http://127.0.0.1:8000/report-detail
```

Expected:

- Page shell renders.
- Missing-session error is visible.
- PDF button is disabled.
- Browser console has no error.

Record the row as `Pass` or `Fail`.

- [ ] **Step 4: Check `/report-detail?session_id=bad`**

Open:

```text
http://127.0.0.1:8000/report-detail?session_id=bad
```

Expected:

- Page shell renders.
- API error is visible.
- The page does not show stale report content.
- Browser console has no uncaught error.

Record the row as `Pass` or `Fail`.

- [ ] **Step 5: Check PDF failure behavior**

Use devtools Network controls or temporarily stop the server after report detail is loaded, then click PDF download.

Expected:

- A local notice/error is shown.
- Rendered report content remains on screen.
- Browser console has no uncaught error.

Record `PDF download failure` as `Pass` or `Fail`.

- [ ] **Step 6: Check report generation failure page behavior**

Use an intentionally bad session or temporarily force a failed report only if there is an existing safe way to do so without code changes. If there is no safe way, record:

```markdown
| Report generation failure | Shows report unavailable/failure notice on processing page | Not run | No safe manual failure injection path in Stage 25.5 |
```

Do not introduce a new failure-injection feature in this stage.

- [ ] **Step 7: Commit error-state evidence**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record stage 25.5 browser error states"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 5: Close Browser-Discovered Defects

**Files:**
- Modify only the smallest file set needed for the defect.
- Modify a matching test file.
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: If no browser defects were found, update the defect log**

If all blocking GUI browser rows passed, replace `S25-ENV-1` with:

```markdown
| S25-ENV-1 | Blocking | Browser acceptance environment | Closed in Stage 25.5: GUI browser acceptance completed manually | - | Browser checklist passed |
```

If `S25-ENV-2` was resolved, leave it as a closed environment note or remove it from blocking status:

```markdown
| S25-ENV-2 | Medium | Local process environment | Closed in Stage 25.5: stale 8000 listener removed before browser acceptance | - | 8000 OpenAPI included `question-evaluations` |
```

- [ ] **Step 2: For each real browser defect, write a failing regression test first**

Choose the most focused test:

- Static DOM/JS behavior: `tests/test_static_report_ui.py`
- Page route availability: `tests/test_page_routes.py`
- API contract: `tests/test_report_api.py`
- Docs acceptance regression: `tests/test_local_v1_docs.py`

Example for a missing question-evaluation trace hook:

```python
def test_report_detail_page_has_question_evaluation_hooks():
    html = read_app_file("test1.html")

    assert 'id="questionEvaluationStatus"' in html
    assert 'id="questionEvaluationList"' in html
```

- [ ] **Step 3: Run the failing test**

Run the exact focused test. Example:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_page_has_question_evaluation_hooks -q
```

Expected: FAIL before the fix.

- [ ] **Step 4: Implement the smallest fix**

Limit the change to the observed browser defect. Do not redesign the page or refactor unrelated code.

- [ ] **Step 5: Run focused verification**

Run the focused test and directly related syntax check. Example:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q
node --check app/static/report-detail.js
```

Expected: PASS.

- [ ] **Step 6: Re-run the failed browser step**

Repeat the exact browser action that failed.

Expected:

- The observed defect is fixed.
- Browser console has no uncaught error.

- [ ] **Step 7: Commit each defect fix separately**

Run:

```powershell
git add app/static/report-detail.js tests/test_static_report_ui.py docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "fix: resolve stage 25.5 browser defect S25-5-1"
```

Expected: one commit per defect. If the defect touches different files, stage only those files plus the matching test and acceptance record.

---

### Task 6: Final RC Acceptance And Verification

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Confirm no blocking browser rows remain**

Run:

```powershell
Select-String -Path docs/stage-21-browser-e2e-acceptance.md -Pattern "Blocked|Not accepted as Local V1 RC"
```

Expected:

- No `Blocked` rows remain.
- `Not accepted as Local V1 RC` is absent.

If output remains, do not mark RC accepted.

- [ ] **Step 2: Run final automated verification**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run JavaScript and CSS checks**

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

- [ ] **Step 4: Run real Postgres integration verification**

Run:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
F:\python3.11\python.exe -m pytest tests/test_postgres_session_store.py tests/test_report_jobs.py tests/test_vector_store.py tests/test_vector_store_pgvector.py -q
```

Expected: PASS.

- [ ] **Step 5: Set final acceptance status**

If all blocking GUI rows passed, replace the final status in `docs/stage-21-browser-e2e-acceptance.md` with:

```markdown
Accepted as Local V1 RC. The GUI browser flow, streamed answer UX, draft localStorage restore, report worker completion, service restart persistence, question evaluation trace, PDF download, and automated verification all passed. No blocking Stage 25.5 defects remain.
```

If any blocking GUI row failed, use:

```markdown
Not accepted as Local V1 RC. Blocking Stage 25.5 browser defects remain in the defect log.
```

- [ ] **Step 6: Commit final acceptance record**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: accept local v1 rc after browser validation"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

- [ ] **Step 7: Audit branch state**

Run:

```powershell
git status --short
git log --oneline -8
```

Expected:

- Only unrelated pre-existing files remain dirty or untracked.
- Recent commits include Stage 25.5 browser evidence and final RC acceptance.

---

## Self-Review

- Spec coverage: The plan directly closes the Stage 25 blocker: GUI browser acceptance. It includes 8000 cleanup, real browser main flow, SSE, localStorage, error states, PDF behavior, question-evaluation UI, defect closure, and final RC acceptance.
- Placeholder scan: The only dynamic values are browser name, session id, and defect details observed during manual execution. All commands and file paths are concrete.
- Scope control: The plan explicitly excludes Stage 26 architecture work, browser automation tooling, new infrastructure, and UI redesign.
- Type consistency: The plan uses existing routes and files: `/prep`, `/interview`, `/report-processing`, `/report-detail`, `/api/interviews/{session_id}/question-evaluations`, `docs/stage-21-browser-e2e-acceptance.md`, and the current static JS/HTML files.
