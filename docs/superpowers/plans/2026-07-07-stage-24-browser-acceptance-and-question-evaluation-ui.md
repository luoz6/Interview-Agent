# Stage 24 Browser Acceptance And Question Evaluation UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the real browser acceptance gap and make Stage 23 question evaluations visible on the report detail page.

**Architecture:** Keep the existing four-page Local V1 runtime and FastAPI API contract. Add a small frontend API helper for `GET /api/interviews/{session_id}/question-evaluations`, render those records in a dedicated report-detail section, and update documentation so the agent/evaluation evidence chain is demonstrable in browser acceptance. Do not add new backend infrastructure, Playwright, login, Redis, Celery, WebSocket, or LangGraph in this stage.

**Tech Stack:** FastAPI, vanilla ES modules, static HTML, Tailwind-generated `prototype.css`, pytest static assertions, Node syntax checks, manual browser acceptance.

---

## Scope

This stage does:

- Execute and record Stage 21/24 real browser acceptance instead of leaving it as `Pending`.
- Add a frontend API helper for question evaluations.
- Add report-detail HTML hooks for a question-evaluation trace section.
- Render saved `QuestionEvaluationRecord` items on `/report-detail?session_id=...`.
- Keep the existing `feedbackList` and `evidenceList` rendering intact.
- Document the visible chain: `Report Worker -> ShadowReviewerAgent -> ReportCoachAgent -> QuestionEvaluationRecord -> Report Detail`.

This stage does not:

- Change the backend question-evaluation API added in Stage 23 unless a browser defect proves it is broken.
- Add automated browser tooling.
- Redesign the report page.
- Add login, report center accounts, Docker, Redis, Celery, WebSocket, or LangGraph.

## File Structure

- Modify: `app/static/api.js`
  - Add `getQuestionEvaluations(sessionId)` helper.
- Modify: `app/test1.html`
  - Add report-detail hooks: `questionEvaluationStatus`, `questionEvaluationList`.
- Modify: `app/static/report-detail.js`
  - Fetch and render question evaluations without blocking the base report render.
- Modify: `tests/test_static_report_ui.py`
  - Add static tests for API helper, HTML hooks, and report-detail rendering behavior.
- Modify: `tests/test_report_api.py`
  - Add an API regression test for missing-session behavior on question evaluations.
- Modify: `docs/stage-21-browser-e2e-acceptance.md`
  - Record real browser execution results and add Stage 24 question-evaluation UI acceptance rows.
- Modify: `README.md`
  - Mention that report detail shows per-question evaluation trace records.
- Modify: `docs/local-v1-runbook.md`
  - Add manual acceptance steps for checking question evaluations.
- Modify: `tests/test_local_v1_docs.py`
  - Add documentation regression coverage for the Stage 24 visible trace.

---

### Task 1: Add Question Evaluation API Helper

**Files:**
- Modify: `tests/test_static_report_ui.py`
- Modify: `app/static/api.js`

- [ ] **Step 1: Write the failing static API test**

Append this test to `tests/test_static_report_ui.py`:

```python
def test_api_js_exposes_question_evaluation_helper():
    js = read_static_file("api.js")

    assert "export function getQuestionEvaluations(sessionId)" in js
    assert "`/api/interviews/${sessionId}/question-evaluations`" in js
    assert "return getJson(" in js
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_api_js_exposes_question_evaluation_helper -q
```

Expected: FAIL because `getQuestionEvaluations()` does not exist yet.

- [ ] **Step 3: Implement the helper**

In `app/static/api.js`, add this function after `getJson()`:

```javascript
export function getQuestionEvaluations(sessionId) {
  return getJson(`/api/interviews/${sessionId}/question-evaluations`);
}
```

- [ ] **Step 4: Run the focused test and syntax check**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_api_js_exposes_question_evaluation_helper -q
node --check app/static/api.js
```

Expected:

- Pytest: PASS.
- Node syntax check exits `0`.

- [ ] **Step 5: Commit**

Run:

```powershell
git add app/static/api.js tests/test_static_report_ui.py
git diff --cached --name-status
git commit -m "feat: add question evaluation frontend API helper"
```

Expected staged files:

```text
M	app/static/api.js
M	tests/test_static_report_ui.py
```

---

### Task 2: Add Report Detail HTML Hooks

**Files:**
- Modify: `tests/test_static_report_ui.py`
- Modify: `app/test1.html`

- [ ] **Step 1: Write the failing HTML hook test**

Append this test to `tests/test_static_report_ui.py`:

```python
def test_report_detail_page_has_question_evaluation_hooks():
    html = read_app_file("test1.html")

    assert 'id="questionEvaluationStatus"' in html
    assert 'id="questionEvaluationList"' in html
    assert "逐题评估链路" in html
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_page_has_question_evaluation_hooks -q
```

Expected: FAIL because the hooks do not exist yet.

- [ ] **Step 3: Add the question-evaluation section**

In `app/test1.html`, add this section after the existing `逐题反馈` table block and before the `RAG 证据引用（部分）` block:

```html
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm mb-6 overflow-hidden">
                    <div class="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-bold text-gray-800">逐题评估链路</h3>
                            <p class="text-xs text-gray-400 mt-1">展示后台 QuestionEvaluationRecord，帮助追踪每题状态、评分依据与改进建议。</p>
                        </div>
                        <span id="questionEvaluationStatus" class="text-[12px] text-gray-400">等待加载</span>
                    </div>
                    <div id="questionEvaluationList" class="divide-y divide-gray-100"></div>
                </section>
```

- [ ] **Step 4: Run the focused test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_page_has_question_evaluation_hooks -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add app/test1.html tests/test_static_report_ui.py
git diff --cached --name-status
git commit -m "feat: add question evaluation report hooks"
```

Expected staged files:

```text
M	app/test1.html
M	tests/test_static_report_ui.py
```

---

### Task 3: Render Question Evaluations On Report Detail

**Files:**
- Modify: `tests/test_static_report_ui.py`
- Modify: `app/static/report-detail.js`

- [ ] **Step 1: Write the failing static render test**

Append this test to `tests/test_static_report_ui.py`:

```python
def test_report_detail_renders_question_evaluation_records():
    js = read_static_file("report-detail.js")

    assert 'import { downloadPdf, getQuestionEvaluations, getSessionId, parseJsonResponse } from "./api.js";' in js
    assert 'const questionEvaluationStatus = byId("questionEvaluationStatus")' in js
    assert 'const questionEvaluationList = byId("questionEvaluationList")' in js
    assert "function renderQuestionEvaluations(payload)" in js
    assert "record.answer_state" in js
    assert "feedback.better_answer" in js
    assert "getQuestionEvaluations(sessionId)" in js
    assert "if (!sessionId) return;" in js
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_renders_question_evaluation_records -q
```

Expected: FAIL because report detail does not fetch or render question evaluations.

- [ ] **Step 3: Update imports and DOM hooks**

In `app/static/report-detail.js`, replace:

```javascript
import { downloadPdf, getSessionId, parseJsonResponse } from "./api.js";
```

with:

```javascript
import { downloadPdf, getQuestionEvaluations, getSessionId, parseJsonResponse } from "./api.js";
```

Add these constants after `const evidenceList = byId("evidenceList");`:

```javascript
const questionEvaluationStatus = byId("questionEvaluationStatus");
const questionEvaluationList = byId("questionEvaluationList");
```

- [ ] **Step 4: Add answer-state label helper**

In `app/static/report-detail.js`, add this helper after `tableCell()`:

```javascript
function toAnswerStateLabel(state) {
  const labels = {
    answered: "已回答",
    skipped: "已跳过",
    unanswered: "未回答",
  };
  return labels[state] || state || "未知";
}
```

- [ ] **Step 5: Add renderer**

In `app/static/report-detail.js`, add this function after `renderReport(report)`:

```javascript
function renderQuestionEvaluations(payload) {
  clear(questionEvaluationList);
  const items = payload.items || [];
  setText("questionEvaluationStatus", `${items.length} 条记录`);
  if (!items.length) {
    renderEmptyState(questionEvaluationList, "暂无逐题评估链路。");
    return;
  }

  for (const record of items) {
    const feedback = record.feedback || {};
    const article = createEl("article", "p-5 grid grid-cols-[160px_1fr] gap-4");

    const meta = createEl("div", "text-xs text-gray-500 space-y-2");
    meta.appendChild(createEl("div", "font-bold text-gray-700", record.question_id || "题目"));
    meta.appendChild(createEl("div", "", toAnswerStateLabel(record.answer_state)));
    meta.appendChild(createEl("div", "", record.status || "unknown"));
    meta.appendChild(createEl("div", "text-blue-600 font-bold", `${feedback.score ?? ""}/100`));

    const body = createEl("div", "space-y-3 text-[13px] text-gray-600 leading-relaxed");
    body.appendChild(createEl("p", "font-medium text-gray-800", feedback.question_text || "未记录题目文本"));
    body.appendChild(createEl("p", "", feedback.rationale || "暂无评分依据。"));
    body.appendChild(createEl("p", "text-orange-600", feedback.critique || "暂无主要问题。"));
    body.appendChild(createEl("p", "text-green-700", feedback.better_answer || "暂无改进答案。"));

    article.appendChild(meta);
    article.appendChild(body);
    questionEvaluationList.appendChild(article);
  }
}
```

- [ ] **Step 6: Fetch question evaluations without blocking report rendering**

In `app/static/report-detail.js`, add this function after `loadReport()`:

```javascript
async function loadQuestionEvaluations() {
  if (!sessionId) return;
  try {
    const payload = await getQuestionEvaluations(sessionId);
    renderQuestionEvaluations(payload);
  } catch (error) {
    setText("questionEvaluationStatus", "加载失败");
    renderEmptyState(questionEvaluationList, error.message);
  }
}
```

At the bottom, replace:

```javascript
  loadReport().catch((error) => showNotice(reportNotice, error.message, "danger"));
```

with:

```javascript
  loadReport().catch((error) => showNotice(reportNotice, error.message, "danger"));
  loadQuestionEvaluations();
```

- [ ] **Step 7: Run focused static and syntax checks**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_renders_question_evaluation_records -q
node --check app/static/report-detail.js
```

Expected:

- Pytest: PASS.
- Node syntax check exits `0`.

- [ ] **Step 8: Run report-detail static tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```powershell
git add app/static/report-detail.js tests/test_static_report_ui.py
git diff --cached --name-status
git commit -m "feat: render question evaluation trace"
```

Expected staged files:

```text
M	app/static/report-detail.js
M	tests/test_static_report_ui.py
```

---

### Task 4: Add Question Evaluation API Regression

**Files:**
- Modify: `tests/test_report_api.py`

- [ ] **Step 1: Write the missing-session API regression test**

Append this test to `tests/test_report_api.py`:

```python
def test_question_evaluations_endpoint_returns_404_for_unknown_session():
    client, _, _, _ = make_client()

    response = client.get("/api/interviews/missing/question-evaluations")

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"
```

- [ ] **Step 2: Run the focused test**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_api.py::test_question_evaluations_endpoint_returns_404_for_unknown_session -q
```

Expected: PASS because Stage 23 already added the backend endpoint and `_raise_value_error()` handling.

- [ ] **Step 3: Run report API tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_api.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```powershell
git add tests/test_report_api.py
git diff --cached --name-status
git commit -m "test: cover question evaluation missing session"
```

Expected staged file:

```text
M	tests/test_report_api.py
```

---

### Task 5: Document Visible Question Evaluation Trace

**Files:**
- Modify: `tests/test_local_v1_docs.py`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`

- [ ] **Step 1: Write the failing docs test**

Append this test to `tests/test_local_v1_docs.py`:

```python
def test_docs_describe_visible_question_evaluation_trace():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    phrase = "Report Detail shows per-question evaluation trace records"
    chain = "Report Worker -> ShadowReviewerAgent -> ReportCoachAgent -> QuestionEvaluationRecord -> Report Detail"
    assert phrase in readme
    assert phrase in runbook
    assert chain in readme
    assert chain in runbook
```

- [ ] **Step 2: Run the focused docs test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_docs_describe_visible_question_evaluation_trace -q
```

Expected: FAIL because the docs do not yet describe the visible trace.

- [ ] **Step 3: Update `README.md`**

In `README.md`, under `## Current Architecture Position`, append this paragraph:

```markdown
Report Detail shows per-question evaluation trace records. The visible trace chain is: `Report Worker -> ShadowReviewerAgent -> ReportCoachAgent -> QuestionEvaluationRecord -> Report Detail`.
```

- [ ] **Step 4: Update `docs/local-v1-runbook.md`**

In `docs/local-v1-runbook.md`, under `## 1.1 Architecture Position`, append this paragraph:

```markdown
Report Detail shows per-question evaluation trace records. The visible trace chain is: `Report Worker -> ShadowReviewerAgent -> ReportCoachAgent -> QuestionEvaluationRecord -> Report Detail`.
```

In the manual browser acceptance section, after the report-detail check, add this numbered check:

```markdown
19. Confirm the `逐题评估链路` section renders at least one question evaluation record after report completion.
```

Insert the new check immediately before the existing PDF download check. Leave the list as Markdown ordered-list syntax so the renderer handles numbering.

- [ ] **Step 5: Run docs tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git diff --cached --name-status
git commit -m "docs: describe visible question evaluation trace"
```

Expected staged files:

```text
M	README.md
M	docs/local-v1-runbook.md
M	tests/test_local_v1_docs.py
```

---

### Task 6: Execute Real Browser Acceptance

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Verify local automated baseline before browser work**

Run:

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
- Each `node --check` exits `0`.
- CSS build exits `0`; the Browserslist warning is acceptable.

- [ ] **Step 2: Prepare real runtime environment**

In the server PowerShell session, set the local runtime variables. `OPENAI_API_KEY` must already contain a real DeepSeek-compatible key in the current shell before the server starts.

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY must be set in this shell before browser acceptance" }
```

Do not commit real API keys.

- [ ] **Step 3: Verify database and knowledge chunks**

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

If `N` is `0`, run:

```powershell
F:\python3.11\python.exe scripts/load_knowledge.py
```

- [ ] **Step 4: Start server and report worker**

Start the FastAPI process:

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start the report worker in a second PowerShell window with the same environment variables:

```powershell
F:\python3.11\python.exe -m app.services.report_worker
```

Expected:

- Server logs show `http://127.0.0.1:8000`.
- Worker stays running and can claim report jobs.

- [ ] **Step 5: Execute the four-page browser flow**

Open:

```text
http://127.0.0.1:8000/prep
```

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

Execute:

1. Generate plan.
2. Save draft.
3. Refresh and restore draft.
4. Start interview.
5. Submit one streamed answer.
6. Skip one question.
7. Finish interview.
8. Wait for report processing to complete.
9. Open report detail.
10. Confirm score, summary, dimensions, feedbacks, evidence, and `逐题评估链路`.
11. Download PDF.

- [ ] **Step 6: Record Stage 24 execution notes**

In `docs/stage-21-browser-e2e-acceptance.md`, add this section before `## Final Status` if it does not exist:

```markdown
## Stage 24 Execution Notes

| Item | Value |
| --- | --- |
| Execution date | 2026-07-07 |
| Browser | Chrome |
| Server URL | `http://127.0.0.1:8000` |
| Runtime store | PostgreSQL |
| LLM provider | DeepSeek-compatible OpenAI API |
| Knowledge chunks | Write the integer count printed by the database check |
| Question evaluation UI | Pass |

## Stage 24 Defect Log

| ID | Severity | Page/API | Symptom | Fix commit | Verification |
| --- | --- | --- | --- | --- | --- |
| None | - | - | No Stage 24 browser defects recorded | - | - |
```

Before committing, replace `Write the integer count printed by the database check` with the actual integer count from Step 3.

- [ ] **Step 7: Update checklist rows**

In `docs/stage-21-browser-e2e-acceptance.md`:

- Replace each executed manual browser row result with `Pass` or `Fail`.
- Add this row to the manual browser checklist if it does not already exist:

```markdown
| Question evaluation trace | `逐题评估链路` shows saved question evaluation records | Pass | Records loaded from `/api/interviews/{session_id}/question-evaluations` |
```

- Replace automated verification `Pending` values with actual `Pass` values for commands executed in Step 1.
- If every row passed, replace final status text with:

```markdown
Accepted for local four-page browser E2E with visible question evaluation trace. No blocking browser defects remain.
```

If any row failed, use:

```markdown
Browser E2E found defects. See Stage 24 Defect Log for details.
```

- [ ] **Step 8: Commit acceptance record**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record stage 24 browser acceptance"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 7: Final Verification And Worktree Audit

**Files:**
- Verify only.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_report_api.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run JavaScript syntax checks**

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

- [ ] **Step 4: Rebuild CSS**

Run:

```powershell
npm run build:prototype-css
```

Expected: PASS. Browserslist warning is acceptable.

- [ ] **Step 5: Check generated CSS diff**

Run:

```powershell
git diff -- app/static/prototype.css
```

Expected: no output. If there is output, inspect it and commit only if the diff is a deterministic Tailwind rebuild:

```powershell
git add app/static/prototype.css
git diff --cached --name-status
git commit -m "build: refresh prototype css"
```

- [ ] **Step 6: Audit worktree and recent commits**

Run:

```powershell
git status --short
git log --oneline -10
```

Expected:

- Recent commits include frontend API helper, report hooks, question-evaluation renderer, docs, and acceptance record.
- Remaining untracked files are only unrelated local files such as `.idea/`, `.claude/`, historical plans/specs, or files explicitly excluded from this stage.

---

## Self-Review

- Spec coverage: The plan covers real browser acceptance, frontend question-evaluation visibility, docs, and regression tests.
- Scope control: The plan does not add new backend infrastructure, automated browser tooling, login, or a report redesign.
- Red/green coverage: Tasks 1, 2, 3, and 5 start with failing tests. Task 4 is a backend regression expected to pass because Stage 23 already implemented the endpoint. Task 6 is real browser acceptance and must not be faked.
- Type consistency: The frontend uses the existing Stage 23 API path `/api/interviews/{session_id}/question-evaluations` and the existing `QuestionEvaluationRecord` fields `question_id`, `answer_state`, `status`, and `feedback`.
