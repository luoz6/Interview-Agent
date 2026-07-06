# Stage 22 Browser E2E Defect Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute real browser acceptance for the four-page local runtime, record the result, and close only defects discovered during that acceptance.

**Architecture:** This stage treats the existing four-page runtime as the release candidate. It does not add login, startup scripts, Playwright, Docker, or knowledge-base management UI. It first fixes documentation pointers to the current acceptance record, then runs manual browser E2E against the local FastAPI app and uses small tested commits for any real defects found.

**Tech Stack:** FastAPI, PostgreSQL/pgvector local runtime, DeepSeek-compatible OpenAI API, vanilla ES modules, pytest, Node syntax checks, Markdown acceptance docs.

---

## File Structure

- Modify: `README.md`
  - Point browser acceptance instructions to `docs/stage-21-browser-e2e-acceptance.md` instead of the old `docs/stage-19-local-e2e.md`.
- Modify: `docs/local-v1-runbook.md`
  - Point browser acceptance instructions to the current Stage 21 acceptance record.
- Modify: `tests/test_local_v1_docs.py`
  - Add regression coverage for the current acceptance record path.
- Modify: `docs/stage-21-browser-e2e-acceptance.md`
  - Replace `Pending` results with `Pass` or `Fail` after real browser execution.
  - Add Stage 22 execution notes and defect log.
- Potentially modify, only if a real browser defect is found:
  - `app/static/prep.js`
  - `app/static/interview.js`
  - `app/static/report-processing.js`
  - `app/static/report-detail.js`
  - `app/static/shared-ui.js`
  - `app/static/api.js`
  - `app/test1.html`
  - `app/test2.html`
  - `app/test3.html`
  - `app/test4.html`
  - Relevant backend file under `app/api/` or `app/services/`
  - Relevant test file under `tests/`

Do not create startup scripts. Do not add Playwright. Do not implement user login. Do not implement unrelated feature work.

---

### Task 1: Fix Acceptance Document References

**Files:**
- Modify: `tests/test_local_v1_docs.py`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`

- [ ] **Step 1: Add a failing doc regression test**

Append this test to `tests/test_local_v1_docs.py`:

```python
def test_readme_and_runbook_point_to_current_browser_acceptance_record():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")
    current_record = "docs/stage-21-browser-e2e-acceptance.md"
    old_record = "docs/stage-19-local-e2e.md"

    assert current_record in readme
    assert current_record in runbook
    assert old_record not in readme
    assert old_record not in runbook
```

- [ ] **Step 2: Run the new doc test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_readme_and_runbook_point_to_current_browser_acceptance_record -q
```

Expected: FAIL because `README.md` and `docs/local-v1-runbook.md` still mention `docs/stage-19-local-e2e.md`.

- [ ] **Step 3: Update `README.md` acceptance pointer**

Replace this sentence in `README.md`:

```markdown
Run real browser acceptance with `docs/local-v1-runbook.md` and record the result in `docs/stage-19-local-e2e.md`.
```

with:

```markdown
Run real browser acceptance with `docs/local-v1-runbook.md` and record the result in `docs/stage-21-browser-e2e-acceptance.md`.
```

- [ ] **Step 4: Update `docs/local-v1-runbook.md` acceptance pointer**

Replace this sentence in `docs/local-v1-runbook.md`:

```markdown
Record the result in `docs/stage-19-local-e2e.md`.
```

with:

```markdown
Record the result in `docs/stage-21-browser-e2e-acceptance.md`.
```

- [ ] **Step 5: Run doc tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit doc pointer fix**

Run:

```powershell
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git diff --cached --name-status
git commit -m "docs: point browser acceptance to current record"
```

Expected staged files:

```text
M	README.md
M	docs/local-v1-runbook.md
M	tests/test_local_v1_docs.py
```

---

### Task 2: Prepare Real Browser Acceptance Run

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Confirm local environment variables**

In the PowerShell session that will run the app, set:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:OPENAI_API_KEY="your-real-deepseek-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

Do not commit real API keys.

- [ ] **Step 2: Check PostgreSQL and pgvector readiness**

Run:

```powershell
@'
import os, psycopg2
conn = psycopg2.connect(os.environ["POSTGRES_DSN"])
cur = conn.cursor()
cur.execute("select current_database(), current_user")
print(cur.fetchone())
cur.execute("select extname from pg_extension where extname='vector'")
print(cur.fetchone())
cur.execute("select count(*) from knowledge_chunks")
print(cur.fetchone())
conn.close()
'@ | F:\python3.11\python.exe -
```

Expected:

```text
('interview', 'postgres')
('vector',)
(N,)
```

where `N` is greater than `0`.

- [ ] **Step 3: Load knowledge if the table is empty**

Run only if the previous step prints `(0,)` for `knowledge_chunks`:

```powershell
F:\python3.11\python.exe scripts/load_knowledge.py
```

Expected: command completes without exception and `knowledge_chunks` count becomes greater than `0`.

- [ ] **Step 4: Run automated checks before opening the browser**

Run each command separately in PowerShell:

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

- Pytest command passes.
- Each `node --check` exits `0`.
- CSS build passes. Browserslist warning is acceptable.

- [ ] **Step 5: Start the local FastAPI server**

Run in a dedicated PowerShell window:

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Expected: server logs show Uvicorn running on `http://127.0.0.1:8000`.

- [ ] **Step 6: Add Stage 22 execution notes skeleton**

In `docs/stage-21-browser-e2e-acceptance.md`, insert this section before `## Final Status` if it is not already present:

```markdown
## Stage 22 Execution Notes

| Item | Value |
| --- | --- |
| Execution date | 2026-07-06 |
| Browser | Pending |
| Server URL | `http://127.0.0.1:8000` |
| Runtime store | PostgreSQL |
| LLM provider | DeepSeek-compatible OpenAI API |
| Knowledge chunks | Pending |

## Defect Log

| ID | Severity | Page/API | Symptom | Fix commit | Verification |
| --- | --- | --- | --- | --- | --- |
| None | - | - | No browser defects recorded yet | - | - |
```

Do not commit this skeleton until the browser run has been executed and the table values are updated.

---

### Task 3: Execute Manual Browser E2E Checklist

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Open the prep page**

Open:

```text
http://127.0.0.1:8000/prep
```

Expected:

- Prep page renders.
- JD textarea is visible.
- Resume textarea is visible.
- Topic tags show the empty/default state rather than demo hard-coded tags.

Record result in `docs/stage-21-browser-e2e-acceptance.md`:

```markdown
| Open `/prep` | Prep page renders with JD/resume inputs and empty tags | Pass |  |
```

If it fails, record:

```markdown
| Open `/prep` | Prep page renders with JD/resume inputs and empty tags | Fail | Actual: <observed behavior> |
```

- [ ] **Step 2: Generate an interview plan**

Use this JD:

```text
后端开发工程师，负责核心交易系统和缓存平台建设。
要求熟悉 Python、FastAPI、Redis、PostgreSQL、消息队列、Docker 和 Linux。
需要具备高并发系统设计、接口幂等、缓存一致性、慢查询优化和工程化交付经验。
```

Use this resume:

```text
5 年 Python 后端开发经验，负责过电商订单、库存、支付对账和用户增长系统。
使用 FastAPI、PostgreSQL、Redis、Kafka、Docker 构建服务。
主导过 Redis 缓存改造、PostgreSQL 慢查询优化、消息队列削峰和接口幂等治理。
熟悉 Linux 部署、日志排障和服务监控。
```

Click `生成面试计划`.

Expected:

- `/api/prep` succeeds.
- Job tags render.
- Plan title renders.
- At least 3 questions render.

Record Pass or Fail in the `Generate plan` row.

- [ ] **Step 3: Save and restore draft**

Actions:

1. Click `保存草稿`.
2. Refresh the page.
3. Click `恢复草稿`.

Expected:

- Save shows success.
- `localStorage.interviewDraftId` exists in browser devtools.
- Restore fills JD and resume.
- Tags are restored if present.

Record Pass or Fail in the `Save draft` and `Restore draft` rows.

- [ ] **Step 4: Start interview**

Click `开始面试`.

Expected:

- Browser navigates to `/interview?session_id=...`.
- URL contains a non-empty `session_id`.
- Current question renders.
- Question navigation renders.

Record Pass or Fail in the `Start interview` row.

- [ ] **Step 5: Submit streamed answer**

Use this answer:

```text
我会优先用 cache-aside 模式。读请求先查 Redis，未命中再查 PostgreSQL 并回填缓存。
写请求先更新数据库，再删除缓存；对于强一致要求高的场景，会配合延迟双删或基于消息队列做缓存失效重试。
同时会给热点 key 加随机过期时间，避免缓存雪崩，并通过监控命中率和慢查询判断缓存效果。
```

Click submit.

Expected:

- No browser console error.
- SSE returns chunks or final done event.
- Conversation updates.
- Question navigation refreshes.

Record Pass or Fail in the `Submit streamed answer` row.

- [ ] **Step 6: Skip one question**

Click the skip/next-question control.

Expected:

- Current question changes, or if no question remains, session finishes.
- Question state shows skipped or finished state.
- No `/api/interviews/null/...` request appears in devtools network.

Record Pass or Fail in the `Skip question` row.

- [ ] **Step 7: Finish interview**

Click `结束面试`.

Expected:

- Browser navigates to `/report-processing?session_id=...`.
- URL preserves the same `session_id`.

Record Pass or Fail in the `Finish interview` row.

- [ ] **Step 8: Observe report processing**

Wait on the report processing page.

Expected:

- Progress status renders.
- Progress bar updates or reaches completed.
- RAG summary renders.
- Page eventually navigates to `/report-detail?session_id=...`, or the view-report button becomes usable when report is ready.

Record Pass or Fail in the `Report processing` row.

- [ ] **Step 9: Verify report detail**

On `/report-detail?session_id=...`, verify:

- Score renders.
- Summary renders.
- Five Chinese dimension labels render: `知识广度`、`技术深度`、`系统设计`、`工程实践`、`表达沟通`.
- Feedback rows render.
- Evidence excerpts render or the page clearly indicates no evidence.

Record Pass or Fail in the `Report detail` row.

- [ ] **Step 10: Download PDF**

Click PDF download.

Expected:

- A PDF file downloads.
- The visible report page remains rendered.
- If the download fails, only a notice appears and existing report content is not cleared.

Record Pass or Fail in the `PDF download` row.

- [ ] **Step 11: Verify error-state URLs**

Open these URLs manually:

```text
http://127.0.0.1:8000/interview
http://127.0.0.1:8000/report-processing
http://127.0.0.1:8000/report-detail
http://127.0.0.1:8000/report-detail?session_id=bad
```

Expected:

- `/interview` shows missing-session error and disables answer controls.
- `/report-processing` shows missing-session error and disables the view-report button.
- `/report-detail` shows missing-session error and disables PDF download.
- `/report-detail?session_id=bad` shows an API error without breaking the page shell.

Record Pass or Fail in the corresponding error-state rows.

- [ ] **Step 12: Update execution notes and final status**

In `docs/stage-21-browser-e2e-acceptance.md`:

- Replace `Browser | Pending` with the actual browser name, for example `Chrome`.
- Replace `Knowledge chunks | Pending` with the count from Task 2 Step 2.
- Replace automated verification `Pending` values with the actual results from Task 2 Step 4.
- If every manual and error-state row is `Pass`, replace:

```markdown
Pending manual browser execution.
```

with:

```markdown
Accepted for local four-page browser E2E. No blocking browser defects remain.
```

- If any row is `Fail`, replace the final status with:

```markdown
Browser E2E found defects. See Defect Log for details and fix commits.
```

---

### Task 4: Close Browser Defects With Minimal Tested Fixes

**Files:**
- Modify only files required by actual Fail rows.
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: If no checklist row is Fail, skip to Task 5**

Do not invent defects. If all rows are Pass, no code changes are required in this task.

- [ ] **Step 2: For each Fail row, create a defect log entry**

In `docs/stage-21-browser-e2e-acceptance.md`, replace the `None` defect row with one row per defect:

```markdown
| S22-1 | Medium | `/report-detail` | PDF download failure cleared rendered report content | Pending | Pending |
```

Use severity:

- `High`: Blocks full interview flow or report generation.
- `Medium`: Breaks one page feature but has a workaround.
- `Low`: Visual/text issue that does not block the flow.

- [ ] **Step 3: Add the smallest regression test for each defect**

Use the appropriate test target:

- Static frontend issue: add a focused assertion to `tests/test_static_report_ui.py`.
- Page route issue: add a focused assertion to `tests/test_page_routes.py`.
- API behavior issue: add a focused API/service test in the existing relevant test file.

Example for a frontend static issue:

```python
def test_report_detail_pdf_failure_keeps_rendered_report_visible():
    js = read_static_file("report-detail.js")

    assert "downloadPdf(" in js
    assert "showNotice(reportNotice, error.message, \"danger\")" in js
    assert "feedbackList.innerHTML = \"\"" not in js
```

Run the focused test and verify it fails before implementation:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_pdf_failure_keeps_rendered_report_visible -q
```

Expected: FAIL before the code fix, PASS after the code fix.

- [ ] **Step 4: Implement only the minimal fix for the defect**

Follow the failing test. Do not refactor unrelated modules. Do not change page design unless the defect is visual and the acceptance row documents it.

After implementation, run the focused test that failed in Step 3.

- [ ] **Step 5: Run the Stage 22 focused verification after each defect fix**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_local_v1_docs.py -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: all checks pass.

- [ ] **Step 6: Commit each defect fix separately**

For each defect, run:

```powershell
git add <changed-code-files> <changed-test-files>
git diff --cached --name-status
git commit -m "fix: resolve stage 22 browser defect S22-1"
```

Replace `S22-1` with the actual defect ID.

- [ ] **Step 7: Update defect log with fix commit and verification**

After each defect fix commit, update the defect row:

```markdown
| S22-1 | Medium | `/report-detail` | PDF download failure cleared rendered report content | `<commit-sha>` | Focused test and Stage 22 focused verification passed |
```

Do not commit the acceptance record until Task 5.

---

### Task 5: Commit Browser Acceptance Record

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Verify acceptance record has no `Pending` result for executed checks**

Run:

```powershell
Select-String -Path docs/stage-21-browser-e2e-acceptance.md -Pattern "Pending"
```

Expected:

- No `Pending` remains for manual rows that were actually executed.
- If a defect is intentionally unresolved, it must be represented as `Fail` with a Defect Log entry, not `Pending`.

- [ ] **Step 2: Commit the acceptance record**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record stage 22 browser acceptance"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 6: Final Verification And Worktree Audit

**Files:**
- Verify only.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run JavaScript syntax checks**

Run each command separately:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: all commands exit `0`.

- [ ] **Step 3: Rebuild local CSS**

Run:

```powershell
npm run build:prototype-css
```

Expected: PASS. Browserslist warning is acceptable.

- [ ] **Step 4: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Check generated CSS diff**

Run:

```powershell
git diff -- app/static/prototype.css
```

Expected: no output. If there is output, inspect it and commit only `app/static/prototype.css`:

```powershell
git add app/static/prototype.css
git commit -m "build: refresh prototype css"
```

- [ ] **Step 6: Audit worktree and recent commits**

Run:

```powershell
git status --short
git log --oneline -10
```

Expected:

- Recent commits include doc pointer fix, optional defect fix commits, and browser acceptance record commit.
- Remaining untracked files are only unrelated local files such as `.idea/`, `.claude/`, old historical plans/specs, or files explicitly excluded from this stage.

---

## Self-Review

- Spec coverage: The plan covers real browser E2E, acceptance record updates, and defect closure without adding login, startup scripts, Playwright, or new feature scope.
- Concrete known fix: README and local runbook currently point to the old Stage 19 record; Task 1 fixes and tests that.
- Defect workflow: Unknown browser defects cannot have prewritten code, so this plan requires a failing regression test, minimal fix, focused verification, and one commit per actual defect.
- Type consistency: Existing file names, routes, and acceptance document path match the current repository state.
