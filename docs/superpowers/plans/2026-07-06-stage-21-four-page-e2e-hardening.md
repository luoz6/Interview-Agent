# Stage 21 Four-Page E2E Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the already-implemented four-page frontend runtime for local manual browser E2E acceptance, and align `docs/interface-requirements.md` with the current no-login, local single-user implementation.

**Architecture:** This stage does not add login, startup scripts, Playwright, or new product features. It tightens client-side error states around missing/invalid `session_id`, failed report status parsing, and PDF/report failures, then updates docs from “prototype/next-stage” wording to current runtime contract. Browser acceptance is recorded manually in a Markdown checklist.

**Tech Stack:** FastAPI page routes, vanilla ES modules, local Tailwind CSS build, pytest static UI tests, Node syntax checks, Markdown docs.

---

## File Structure

- Modify: `app/static/interview.js`
  - Disable all interview controls when `session_id` is missing.
  - Guard submit/skip/finish handlers so they never call `/api/interviews/null/...`.
- Modify: `app/static/report-processing.js`
  - Disable the report detail button when `session_id` is missing.
  - Reuse `safeJson()` for non-JSON report errors instead of direct `response.json()`.
  - Stop polling after terminal unavailable states.
- Modify: `app/static/report-detail.js`
  - Disable PDF download when `session_id` is missing.
  - Keep rendered report visible when PDF download fails.
- Modify: `tests/test_static_report_ui.py`
  - Add static tests for the above error-state guards.
- Modify: `docs/interface-requirements.md`
  - Remove stale “next stage/prototype replacement” wording where the feature is already implemented.
  - Keep explicit “no login, local single-user deployment” scope.
  - Document four HTML page routes as the current runtime contract.
- Modify: `tests/test_local_v1_docs.py`
  - Add regression checks that the interface doc no longer claims `/` serves the old single page.
- Create: `docs/stage-21-browser-e2e-acceptance.md`
  - Manual browser E2E checklist and acceptance log.

Do not create startup scripts. Do not add Playwright. Do not implement user login.

---

### Task 1: Lock Down Four-Page Error-State Expectations

**Files:**
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Add static regression tests for missing session guards and safe error parsing**

Append these tests to `tests/test_static_report_ui.py`:

```python
def test_interview_page_disables_all_controls_without_session_id():
    js = read_static_file("interview.js")

    assert 'const sendAnswerButton = byId("sendAnswerButton")' in js
    assert "function hasSession()" in js
    assert "showNotice(interviewNotice, \"缺少 session_id，请从准备页开始面试\", \"danger\")" in js
    assert "setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], true)" in js
    assert "if (!hasSession()) return;" in js


def test_report_processing_page_uses_safe_json_and_disables_view_without_session_id():
    js = read_static_file("report-processing.js")

    assert 'import { getJson, getSessionId, safeJson } from "./api.js";' in js
    assert "viewReportButton.disabled = true" in js
    assert "const body = await safeJson(reportResponse);" in js
    assert "window.clearTimeout(timer)" in js


def test_report_detail_page_disables_pdf_without_session_id_and_preserves_report_on_download_failure():
    js = read_static_file("report-detail.js")

    assert "downloadReportButton.disabled = true" in js
    assert "showNotice(reportNotice, error.message, \"danger\")" in js
    assert "renderReportError" not in js
```

- [ ] **Step 2: Run the new static tests and verify they fail before implementation**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_interview_page_disables_all_controls_without_session_id tests/test_static_report_ui.py::test_report_processing_page_uses_safe_json_and_disables_view_without_session_id tests/test_static_report_ui.py::test_report_detail_page_disables_pdf_without_session_id_and_preserves_report_on_download_failure -q
```

Expected: FAIL because `interview.js` does not yet bind `sendAnswerButton`, `report-processing.js` does not import `safeJson`, and report pages do not disable all invalid-session controls.

---

### Task 2: Harden Missing Session And Report Error States

**Files:**
- Modify: `app/static/interview.js`
- Modify: `app/static/report-processing.js`
- Modify: `app/static/report-detail.js`

- [ ] **Step 1: Update `app/static/interview.js` imports and button bindings**

Replace the top constants in `app/static/interview.js` with:

```js
const sessionId = getSessionId();
const conversation = byId("conversation");
const currentQuestion = byId("currentQuestion");
const answerForm = byId("answerForm");
const answerInput = byId("answerInput");
const sendAnswerButton = byId("sendAnswerButton");
const skipQuestionButton = byId("skipQuestionButton");
const finishInterviewButton = byId("finishInterviewButton");
const questionPlan = byId("questionPlan");
const topicTags = byId("topicTags");
const interviewNotice = byId("interviewNotice");
```

- [ ] **Step 2: Add a session guard helper in `app/static/interview.js`**

Replace the current top-level missing-session block:

```js
if (!sessionId) {
  showNotice(interviewNotice, "缺少 session_id，请从准备页开始面试", "danger");
  setBusy([answerInput, skipQuestionButton, finishInterviewButton], true);
}
```

with:

```js
function hasSession() {
  if (sessionId) return true;
  showNotice(interviewNotice, "缺少 session_id，请从准备页开始面试", "danger");
  setBusy([answerInput, sendAnswerButton, skipQuestionButton, finishInterviewButton], true);
  return false;
}
```

- [ ] **Step 3: Guard answer submit in `app/static/interview.js`**

At the start of `submitAnswer(event)`, immediately after `event.preventDefault();`, add:

```js
  if (!hasSession()) return;
```

The start of the function should become:

```js
async function submitAnswer(event) {
  event.preventDefault();
  if (!hasSession()) return;

  const answer = answerInput.value.trim();
  if (!answer) {
    showNotice(interviewNotice, "回答不能为空", "warning");
    return;
  }
```

- [ ] **Step 4: Guard skip and finish actions in `app/static/interview.js`**

Replace the existing `skipQuestion()` and `finishInterview()` functions with:

```js
async function skipQuestion() {
  if (!hasSession()) return;
  await postJson(`/api/interviews/${sessionId}/skip`, {});
  await loadSnapshot();
}

async function finishInterview() {
  if (!hasSession()) return;
  await postJson(`/api/interviews/${sessionId}/finish`, {});
  window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
}
```

- [ ] **Step 5: Initialize invalid-session state at the bottom of `app/static/interview.js`**

Replace:

```js
if (sessionId) {
  loadSnapshot().catch((error) => showNotice(interviewNotice, error.message, "danger"));
}
```

with:

```js
if (hasSession()) {
  loadSnapshot().catch((error) => showNotice(interviewNotice, error.message, "danger"));
}
```

- [ ] **Step 6: Update `app/static/report-processing.js` to use `safeJson`**

Replace the import line:

```js
import { getJson, getSessionId } from "./api.js";
```

with:

```js
import { getJson, getSessionId, safeJson } from "./api.js";
```

- [ ] **Step 7: Add terminal polling cleanup helper in `app/static/report-processing.js`**

After `let timer = null;`, add:

```js
function stopPolling() {
  if (timer) {
    window.clearTimeout(timer);
    timer = null;
  }
}
```

- [ ] **Step 8: Replace direct JSON parsing for report errors in `app/static/report-processing.js`**

Replace this block inside `poll()`:

```js
  if (reportResponse.status === 404 || reportResponse.status === 409 || reportResponse.status >= 500) {
    const body = await reportResponse.json().catch(() => ({}));
    showNotice(processingNotice, body.detail || "报告暂不可用，请稍后重试。", "danger");
    return;
  }
  if (reportResponse.status !== 202) {
    showNotice(processingNotice, "报告暂不可用，请稍后重试。", "danger");
    return;
  }
```

with:

```js
  if (reportResponse.status === 404 || reportResponse.status === 409 || reportResponse.status >= 500) {
    stopPolling();
    const body = await safeJson(reportResponse);
    showNotice(processingNotice, body.detail || "报告暂不可用，请稍后重试。", "danger");
    return;
  }
  if (reportResponse.status !== 202) {
    stopPolling();
    showNotice(processingNotice, "报告暂不可用，请稍后重试。", "danger");
    return;
  }
```

- [ ] **Step 9: Disable report navigation when `session_id` is missing in `app/static/report-processing.js`**

Replace the bottom missing-session block:

```js
if (!sessionId) {
  showNotice(processingNotice, "缺少 session_id，请从面试页进入", "danger");
} else {
  poll().catch((error) => showNotice(processingNotice, error.message, "danger"));
}
```

with:

```js
if (!sessionId) {
  viewReportButton.disabled = true;
  showNotice(processingNotice, "缺少 session_id，请从面试页进入", "danger");
} else {
  poll().catch((error) => showNotice(processingNotice, error.message, "danger"));
}
```

- [ ] **Step 10: Disable PDF download when `session_id` is missing in `app/static/report-detail.js`**

Replace the bottom block:

```js
if (!sessionId) {
  showNotice(reportNotice, "缺少 session_id，请从报告生成页进入", "danger");
} else {
  loadReport().catch((error) => showNotice(reportNotice, error.message, "danger"));
}
```

with:

```js
if (!sessionId) {
  downloadReportButton.disabled = true;
  showNotice(reportNotice, "缺少 session_id，请从报告生成页进入", "danger");
} else {
  loadReport().catch((error) => showNotice(reportNotice, error.message, "danger"));
}
```

Keep the existing PDF failure handler:

```js
downloadReportButton.addEventListener("click", () => {
  downloadPdf(
    `/api/interviews/${sessionId}/report.pdf`,
    `interview-report-${sessionId}.pdf`,
  ).catch((error) => showNotice(reportNotice, error.message, "danger"));
});
```

This is intentionally non-destructive: it shows a notice and does not clear the already rendered report.

- [ ] **Step 11: Run focused static tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 12: Run JS syntax checks**

Run these as separate PowerShell commands, not chained with `&&`:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: all commands exit `0`.

- [ ] **Step 13: Commit frontend hardening**

Run:

```powershell
git add app/static/interview.js app/static/report-processing.js app/static/report-detail.js tests/test_static_report_ui.py
git diff --cached --name-status
git commit -m "fix: harden four page runtime error states"
```

Expected staged files:

```text
M	app/static/interview.js
M	app/static/report-processing.js
M	app/static/report-detail.js
M	tests/test_static_report_ui.py
```

---

### Task 3: Align Interface Requirements With Current Four-Page Runtime

**Files:**
- Modify: `docs/interface-requirements.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Add doc regression tests**

Append this test to `tests/test_local_v1_docs.py`:

```python
def test_interface_requirements_describes_current_four_page_runtime_without_stale_next_stage_language():
    doc = read_text("docs/interface-requirements.md")

    assert "当前已实现的 HTML 页面路由" in doc
    assert "`GET` | `/` 或 `/prep`" in doc
    assert "`GET` | `/interview?session_id=...`" in doc
    assert "`GET` | `/report-processing?session_id=...`" in doc
    assert "`GET` | `/report-detail?session_id=...`" in doc
    assert "登录、用户隔离和跨设备同步不纳入本机部署范围" in doc
    assert "当前 FastAPI `/` 仍返回旧 `app/static/index.html`" not in doc
    assert "下一阶段用四个页面路由替代旧单页" not in doc
    assert "下一阶段前端目标是不再保留" not in doc
```

- [ ] **Step 2: Run the new doc regression test and verify it fails before doc edits**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_interface_requirements_describes_current_four_page_runtime_without_stale_next_stage_language -q
```

Expected: FAIL because the interface document still contains stale “下一阶段/旧单页” wording in the introduction and key-differences table.

- [ ] **Step 3: Update the introduction of `docs/interface-requirements.md`**

Replace this paragraph:

```markdown
本文档基于当前项目代码与 `app` 目录下 4 个 HTML 原型页生成，用于前后端联调、接口验收和前端页面替换排期。下一阶段前端目标是不再保留 `app/static/index.html` 作为运行入口，而是按四个原型页拆分为准备、面试、报告生成、报告详情四个页面。
```

with:

```markdown
本文档基于当前项目代码与 `app` 目录下 4 个 HTML 运行页生成，用于前后端联调、接口验收和本机部署验收。当前前端运行入口已经从旧 `app/static/index.html` 切换为四个页面：准备、面试、报告生成、报告详情。
```

- [ ] **Step 4: Update the source table descriptions in `docs/interface-requirements.md`**

Replace these five rows:

```markdown
| `app/test4.html` | 面试准备页原型，目标承载 JD/简历输入、草稿、标签、计划预览和开始面试 |
| `app/test3.html` | 模拟面试进行页原型，目标承载对话、流式追问、跳题、结束、题目导航和会话快照 |
| `app/test2.html` | 报告生成中页原型，目标承载报告进度、事件时间线、RAG 摘要和报告轮询 |
| `app/test1.html` | 结构化面评报告页原型，目标承载报告详情、维度分、逐题反馈、证据和 PDF 下载 |
| `app/static/index.html`、`app/static/app.js` | 旧单页运行界面；下一阶段应被四个原型页替换，不再作为目标前端入口 |
```

with:

```markdown
| `app/test4.html` | 面试准备运行页，承载 JD/简历输入、草稿、标签、计划预览和开始面试 |
| `app/test3.html` | 模拟面试运行页，承载对话、流式追问、跳题、结束、题目导航和会话快照 |
| `app/test2.html` | 报告生成运行页，承载报告进度、事件时间线、RAG 摘要和报告轮询 |
| `app/test1.html` | 结构化面评报告运行页，承载报告详情、维度分、逐题反馈、证据和 PDF 下载 |
| `app/static/*.js`、`app/static/prototype.css` | 四页运行时共享脚本和本地 CSS；旧 `index.html/app.js/styles.css` 已移除 |
```

- [ ] **Step 5: Replace stale key-differences table rows in `docs/interface-requirements.md`**

In section `## 3. 页面流程与接口关系`, replace the two rows:

```markdown
| 四个原型页分别作为运行入口 | 当前 FastAPI `/` 仍返回旧 `app/static/index.html` | 下一阶段用四个页面路由替代旧单页，不再保留 `app/static/index.html` 作为运行入口 |
| 报告页显示百分位、报告标签、完成时间、PDF 下载 | 当前 `InterviewReport` 不包含这些字段 | 扩展报告模型或新增报告元数据/PDF 接口 |
```

with:

```markdown
| 四个运行页分别作为入口 | FastAPI 已将 `/`、`/prep`、`/interview`、`/report-processing`、`/report-detail` 映射到四个 HTML 页面 | 页面路由是当前运行契约，不再依赖旧单页入口 |
| 报告页显示百分位、报告标签、完成时间、PDF 下载 | 当前 `InterviewReport` 不包含百分位和报告标签；PDF 下载已通过 `/api/interviews/{session_id}/report.pdf` 实现 | 前端隐藏后端未提供字段，不伪造百分位或完成时间 |
```

- [ ] **Step 6: Update “原型” wording in section 7 headings where it now describes implemented runtime**

Replace:

```markdown
## 7. HTML 原型驱动的补充接口需求
```

with:

```markdown
## 7. 四页运行时驱动的补充接口需求
```

Replace:

```markdown
### 7.2 Stage 10/11 已落地的原型接口

以下原型需求已实现，下一阶段需要接入四个原型运行页，接口详情见第 5 节：
```

with:

```markdown
### 7.2 Stage 10/11 已落地的运行时接口

以下页面需求已实现并已接入四个运行页，接口详情见第 5 节：
```

- [ ] **Step 7: Run doc tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit interface document alignment**

Run:

```powershell
git add docs/interface-requirements.md tests/test_local_v1_docs.py
git diff --cached --name-status
git commit -m "docs: align interface contract with four page runtime"
```

Expected staged files:

```text
M	docs/interface-requirements.md
M	tests/test_local_v1_docs.py
```

---

### Task 4: Add Manual Browser E2E Acceptance Record

**Files:**
- Create: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Create the Stage 21 manual acceptance document**

Create `docs/stage-21-browser-e2e-acceptance.md` with this content:

```markdown
# Stage 21 Browser E2E Acceptance

Date: 2026-07-06

## Scope

This acceptance record covers the four-page local runtime:

| Page | Route | Source |
| --- | --- | --- |
| Prep | `/` or `/prep` | `app/test4.html` |
| Interview | `/interview?session_id=...` | `app/test3.html` |
| Report processing | `/report-processing?session_id=...` | `app/test2.html` |
| Report detail | `/report-detail?session_id=...` | `app/test1.html` |

Out of scope: user login, account isolation, startup scripts, Playwright/browser automation.

## Environment

| Item | Value |
| --- | --- |
| Deployment mode | Local single-user |
| PostgreSQL | `postgresql://postgres:postgres@127.0.0.1:5432/interview` |
| Frontend | Four HTML pages served by FastAPI |
| LLM | DeepSeek-compatible OpenAI API through `OPENAI_BASE_URL` and `OPENAI_API_KEY` |

## Manual Browser Checklist

| Step | Expected result | Result | Notes |
| --- | --- | --- | --- |
| Open `/prep` | Prep page renders with JD/resume inputs and empty tags | Pending |  |
| Generate plan | `/api/prep` returns questions and tags render | Pending |  |
| Save draft | Draft saves and browser keeps `interviewDraftId` | Pending |  |
| Restore draft | JD/resume/tags restore from anonymous draft | Pending |  |
| Start interview | Browser navigates to `/interview?session_id=...` | Pending |  |
| Submit streamed answer | SSE chunks or final turn render; question navigation refreshes | Pending |  |
| Skip question | Question state changes to skipped or session finishes | Pending |  |
| Finish interview | Browser navigates to `/report-processing?session_id=...` | Pending |  |
| Report processing | Progress/status/RAG summary render until report is available | Pending |  |
| Report detail | Score, summary, five dimensions, feedbacks, evidence render | Pending |  |
| PDF download | PDF downloads and visible report content remains on screen | Pending |  |

## Error-State Checklist

| URL or action | Expected result | Result | Notes |
| --- | --- | --- | --- |
| `/interview` without `session_id` | Shows missing-session error and disables answer controls | Pending |  |
| `/report-processing` without `session_id` | Shows missing-session error and disables view-report button | Pending |  |
| `/report-detail` without `session_id` | Shows missing-session error and disables PDF button | Pending |  |
| `/report-detail?session_id=bad` | Shows API error without breaking page shell | Pending |  |
| PDF download failure | Shows local notice and does not clear rendered report | Pending |  |
| Report generation failure | Shows report unavailable/failure notice on processing page | Pending |  |

## Automated Verification

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_local_v1_docs.py -q` | Pending |
| `node --check app/static/api.js` | Pending |
| `node --check app/static/shared-ui.js` | Pending |
| `node --check app/static/prep.js` | Pending |
| `node --check app/static/interview.js` | Pending |
| `node --check app/static/report-processing.js` | Pending |
| `node --check app/static/report-detail.js` | Pending |
| `npm run build:prototype-css` | Pending |
| `F:\python3.11\python.exe -m pytest -q` | Pending |

## Final Status

Pending manual browser execution.
```

- [ ] **Step 2: Commit acceptance record**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: add stage 21 browser e2e checklist"
```

Expected staged file:

```text
A	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 5: Final Verification And Worktree Audit

**Files:**
- Verify only.

- [ ] **Step 1: Run focused Python verification**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run JavaScript syntax checks**

Run these as separate commands:

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

Expected: PASS. A Browserslist warning is acceptable.

- [ ] **Step 4: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Check whether CSS rebuild changed tracked output**

Run:

```powershell
git diff -- app/static/prototype.css
```

Expected: no output. If there is output, inspect it and commit only `app/static/prototype.css` with:

```powershell
git add app/static/prototype.css
git commit -m "build: refresh prototype css"
```

- [ ] **Step 6: Audit remaining worktree**

Run:

```powershell
git status --short
git log --oneline -8
```

Expected:

- Recent commits include the Stage 21 frontend hardening, interface doc alignment, and browser E2E checklist commits.
- Remaining untracked files, if any, are only intentionally excluded local files such as `.idea/`, `.claude/`, historical old plans/specs, or other files the user explicitly did not ask to commit.

---

## Self-Review

- Spec coverage: The plan includes four-page manual E2E acceptance, error-state hardening, interface document alignment, and final regression verification.
- Explicit exclusions: No startup script, no Playwright, no user login.
- Type and field consistency: The plan preserves `reference.excerpt`, uses existing `safeJson()`, existing `setBusy()`, existing `showNotice()`, and existing page hooks such as `sendAnswerButton`, `viewReportButton`, and `downloadReportButton`.
- No placeholder work remains: Every code/document change includes concrete text or commands.
