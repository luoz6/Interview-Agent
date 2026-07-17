# Stage 16 Four-Page Frontend Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old `app/static/index.html` runtime entry with the four prototype pages `app/test4.html`, `app/test3.html`, `app/test2.html`, and `app/test1.html`, wired to the already implemented API contract.

**Architecture:** Keep FastAPI as the HTML host and API server. Serve four HTML pages directly from `app/`, move runtime behavior into small page-specific ES modules under `app/static`, and keep shared fetch/render helpers in focused shared modules. Do not add login, multi-user isolation, or knowledge-base management in this stage.

**Tech Stack:** FastAPI, vanilla JavaScript ES modules, HTML, CSS, pytest, FastAPI TestClient, optional `node --check` for JavaScript syntax.

---

## Scope

This stage implements the frontend plan described by `docs/interface-requirements.md` and `docs/frontend-modification-guide.md`.

Current code audit:

| Area | Current State | Stage 16 Target |
| --- | --- | --- |
| `app/main.py` | Only `GET /` exists and returns `app/static/index.html` | `/` and `/prep` return `app/test4.html`; `/interview`, `/report-processing`, `/report-detail` return `app/test3.html`, `app/test2.html`, `app/test1.html` |
| `tests/test_static_report_ui.py` | Asserts prototype files are not shipped | Must assert the four prototype files exist and expose runtime hooks |
| `app/test4.html` | Static prep prototype without required runtime ids | Add `jobDescription`, `resumeText`, `saveDraftButton`, `restoreDraftButton`, `prepButton`, `startButton`, `topicTags`, `planTitle`, `planQuestions`, `prepStatus` |
| `app/test3.html` | Static interview prototype without required runtime ids | Add `conversation`, `currentQuestion`, `answerForm`, `answerInput`, `sendAnswerButton`, `skipQuestionButton`, `finishInterviewButton`, `questionPlan`, `topicTags`, `sessionStatus`, `interviewNotice` |
| `app/test2.html` | Static processing prototype without required runtime ids | Add `reportProgressBar`, `reportProgressStatus`, `reportEvents`, `reportRagSummary`, `reportJobId`, `viewReportButton`, `processingNotice` |
| `app/test1.html` | Static report prototype uses four-dimension Chart.js radar | Ignore or remove the four-dimension radar and render backend five-dimension `overall_dimension_scores` |
| `app/static` | Contains old `index.html`, `app.js`, `styles.css` only | Add six ES modules: `api.js`, `shared-ui.js`, `prep.js`, `interview.js`, `report-processing.js`, `report-detail.js` |
| `tests/test_page_routes.py` | Does not exist | Add route tests for all five page routes |

In scope:

| Area | Scope |
| --- | --- |
| Page routes | `/`, `/prep`, `/interview`, `/report-processing`, `/report-detail` |
| Runtime pages | Use `app/test4.html`, `app/test3.html`, `app/test2.html`, `app/test1.html` as real pages |
| Frontend state | `session_id` query parameter, `localStorage.interviewDraftId`, page-local JS state |
| API wiring | Prep, drafts, session snapshot, stream answer, skip, finish, report progress, report detail, PDF |
| Tests | Route tests, static contract tests, JS syntax tests where Node is available |

Out of scope:

| Area | Reason |
| --- | --- |
| Login and accounts | Project is local single-user deployment |
| Knowledge-base management UI | Requires upload, chunking, embedding, indexing, deletion, and retrieval preview APIs |
| Report center redesign | Existing `/api/reports` remains available; a dedicated report center page can be a later stage |
| Full visual redesign | Preserve prototype structure first; polish after the four-page flow works |

Execution constraints:

| Constraint | Reason |
| --- | --- |
| Do not keep `app/static/index.html` as a runtime entry | It contradicts the updated interface document |
| Do not directly reuse old `app/static/app.js` | It is built around the old single-page architecture; this stage needs page-specific modules |
| Do not display fake prototype-only data | Percentiles, Worker names, station notifications, and share links are not returned by current APIs |
| Do not change API contracts unless a page cannot work without it | Current backend API already supports the four-page interview flow |
| Treat CDN dependencies as a known risk, not a blocker | Tailwind, FontAwesome, and Chart.js localizing can be a follow-up after runtime wiring |

## File Structure

| File | Responsibility |
| --- | --- |
| `app/main.py` | Serve four HTML page routes and keep `/static` mounted |
| `app/test4.html` | Runtime prep page |
| `app/test3.html` | Runtime interview page |
| `app/test2.html` | Runtime report-processing page |
| `app/test1.html` | Runtime report-detail page |
| `app/static/api.js` | Shared JSON fetch, POST, SSE parsing, PDF blob download |
| `app/static/shared-ui.js` | Shared DOM helpers, tag rendering, question states, dimension labels |
| `app/static/prep.js` | Prep page API integration |
| `app/static/interview.js` | Interview page API integration |
| `app/static/report-processing.js` | Report-processing page polling |
| `app/static/report-detail.js` | Report-detail page rendering and PDF download |
| `tests/test_page_routes.py` | FastAPI HTML route tests |
| `tests/test_static_report_ui.py` | Rewrite static frontend contract tests from old single-page assumptions to four-page assumptions |

---

### Task 1: Add Four HTML Page Routes

**Files:**

| Action | Path |
| --- | --- |
| Modify | `app/main.py` |
| Create | `tests/test_page_routes.py` |

- [ ] **Step 1: Write failing route tests**

Create `tests/test_page_routes.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root_serves_prep_page():
    response = client.get("/")

    assert response.status_code == 200
    assert "开始一次模拟面试" in response.text


def test_prep_route_serves_prep_page():
    response = client.get("/prep")

    assert response.status_code == 200
    assert "开始一次模拟面试" in response.text


def test_interview_route_serves_interview_page():
    response = client.get("/interview?session_id=session-1")

    assert response.status_code == 200
    assert "模拟面试进行中" in response.text


def test_report_processing_route_serves_processing_page():
    response = client.get("/report-processing?session_id=session-1")

    assert response.status_code == 200
    assert "面评报告生成中" in response.text


def test_report_detail_route_serves_report_page():
    response = client.get("/report-detail?session_id=session-1")

    assert response.status_code == 200
    assert "结构化面评报告" in response.text
```

- [ ] **Step 2: Run route tests and verify failure**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_page_routes.py -q
```

Expected: `/interview`, `/report-processing`, and `/report-detail` fail with `404`; `/` still returns old `app/static/index.html`.

- [ ] **Step 3: Implement routes in `app/main.py`**

Replace `app/main.py` with:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Interview Agent MVP")
app.include_router(router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _html_page(filename: str) -> FileResponse:
    return FileResponse(BASE_DIR / filename)


@app.get("/")
def root():
    return _html_page("test4.html")


@app.get("/prep")
def prep_page():
    return _html_page("test4.html")


@app.get("/interview")
def interview_page():
    return _html_page("test3.html")


@app.get("/report-processing")
def report_processing_page():
    return _html_page("test2.html")


@app.get("/report-detail")
def report_detail_page():
    return _html_page("test1.html")
```

- [ ] **Step 4: Run route tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_page_routes.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/main.py tests/test_page_routes.py
git commit -m "feat: serve four prototype runtime pages"
```

---

### Task 2: Replace Old Single-Page Static Tests

**Files:**

| Action | Path |
| --- | --- |
| Modify | `tests/test_static_report_ui.py` |

- [ ] **Step 1: Replace old single-page assumptions with four-page static contracts**

Rewrite `tests/test_static_report_ui.py` so it no longer asserts that prototype files are absent and no longer depends on `app/static/index.html`.

Use this content:

```python
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
STATIC_DIR = APP_DIR / "static"


def read_app_file(name: str) -> str:
    return (APP_DIR / name).read_text(encoding="utf-8")


def read_static_file(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_four_runtime_html_pages_exist():
    assert (APP_DIR / "test4.html").exists()
    assert (APP_DIR / "test3.html").exists()
    assert (APP_DIR / "test2.html").exists()
    assert (APP_DIR / "test1.html").exists()


def test_old_static_index_is_not_the_runtime_contract():
    html = read_static_file("index.html") if (STATIC_DIR / "index.html").exists() else ""

    assert "开始一次模拟面试" not in html


def test_prep_page_has_runtime_hooks():
    html = read_app_file("test4.html")

    for element_id in (
        "jobDescription",
        "resumeText",
        "saveDraftButton",
        "restoreDraftButton",
        "prepButton",
        "startButton",
        "topicTags",
        "planTitle",
        "planQuestions",
        "prepStatus",
    ):
        assert f'id="{element_id}"' in html
    assert '/static/prep.js' in html


def test_interview_page_has_runtime_hooks():
    html = read_app_file("test3.html")

    for element_id in (
        "conversation",
        "currentQuestion",
        "answerForm",
        "answerInput",
        "sendAnswerButton",
        "skipQuestionButton",
        "finishInterviewButton",
        "questionPlan",
        "topicTags",
        "sessionStatus",
    ):
        assert f'id="{element_id}"' in html
    assert '/static/interview.js' in html


def test_report_processing_page_has_runtime_hooks():
    html = read_app_file("test2.html")

    for element_id in (
        "reportProgressBar",
        "reportProgressStatus",
        "reportEvents",
        "reportRagSummary",
        "reportJobId",
        "viewReportButton",
    ):
        assert f'id="{element_id}"' in html
    assert '/static/report-processing.js' in html


def test_report_detail_page_has_runtime_hooks():
    html = read_app_file("test1.html")

    for element_id in (
        "reportStatus",
        "reportScore",
        "reportSummary",
        "dimensionScores",
        "reportHighlights",
        "feedbackList",
        "evidenceList",
        "downloadReportButton",
        "reportNotice",
    ):
        assert f'id="{element_id}"' in html
    assert '/static/report-detail.js' in html


def test_shared_ui_maps_dimensions_to_chinese():
    js = read_static_file("shared-ui.js")

    assert "dimensionLabels" in js
    assert "知识广度" in js
    assert "技术深度" in js
    assert "系统设计" in js
    assert "工程实践" in js
    assert "表达沟通" in js


def test_page_scripts_use_real_api_endpoints():
    combined = "\n".join(
        read_static_file(name)
        for name in (
            "prep.js",
            "interview.js",
            "report-processing.js",
            "report-detail.js",
        )
    )

    assert "/api/prep" in combined
    assert "/api/interview-drafts" in combined
    assert "/api/interviews/" in combined
    assert "/answer/stream" in combined
    assert "/skip" in combined
    assert "/finish" in combined
    assert "/report/progress" in combined
    assert "/report.pdf" in combined
```

- [ ] **Step 2: Run static tests and verify failure**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q
```

Expected: fail because the four HTML pages do not yet have stable ids and the new JS modules do not exist.

- [ ] **Step 3: Commit failing tests only if using TDD checkpoints**

If the project standard allows committing failing tests, commit them. Otherwise keep them uncommitted until Task 3 and Task 4 make them pass.

```powershell
git add tests/test_static_report_ui.py
git commit -m "test: define four-page frontend contract"
```

---

### Task 3: Add Shared Frontend Modules

**Files:**

| Action | Path |
| --- | --- |
| Create | `app/static/api.js` |
| Create | `app/static/shared-ui.js` |

- [ ] **Step 1: Create `app/static/api.js`**

```js
export function getSessionId() {
  return new URLSearchParams(window.location.search).get("session_id");
}

export async function getJson(url) {
  const response = await fetch(url);
  return parseJsonResponse(response);
}

export async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse(response);
}

export async function parseJsonResponse(response) {
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(body.detail || `Request failed with ${response.status}`);
  }
  return body;
}

export async function readSse(response, handlers) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed with ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const rawEvent of events) {
      const event = parseSseEvent(rawEvent);
      if (event && handlers[event.event]) {
        handlers[event.event](event.data);
      }
    }
  }
}

function parseSseEvent(rawEvent) {
  const event = { event: "message", data: {} };
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) {
      event.event = line.slice("event:".length).trim();
    }
    if (line.startsWith("data:")) {
      event.data = JSON.parse(line.slice("data:".length).trim());
    }
  }
  return event;
}

export async function downloadPdf(url, filename) {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "PDF download failed");
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
```

- [ ] **Step 2: Create `app/static/shared-ui.js`**

```js
export const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};

export const questionStateLabels = {
  current: "当前题",
  answered: "已回答",
  skipped: "已跳过",
  unanswered: "未回答",
  pending: "待进行",
};

export function byId(id) {
  return document.getElementById(id);
}

export function setText(id, value) {
  const node = byId(id);
  if (node) {
    node.textContent = value ?? "";
  }
}

export function clear(node) {
  if (node) {
    node.innerHTML = "";
  }
}

export function createEl(tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function renderTags(container, tags) {
  clear(container);
  const safeTags = Array.isArray(tags) ? tags : [];
  if (!safeTags.length) {
    container.appendChild(createEl("span", "tag muted", "等待识别岗位标签"));
    return;
  }
  for (const tag of safeTags) {
    container.appendChild(createEl("span", "tag", tag));
  }
}

export function toDimensionLabel(name) {
  return dimensionLabels[name] || name;
}

export function showNotice(node, message, type = "info") {
  if (!node) return;
  node.textContent = message || "";
  node.dataset.type = type;
  node.hidden = !message;
}

export function formatPercent(value) {
  if (value === null || value === undefined) return "0%";
  return `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
}
```

- [ ] **Step 3: Run static tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_shared_ui_maps_dimensions_to_chinese -q
```

Expected: shared dimension test passes.

- [ ] **Step 4: Run JS syntax checks**

Run:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
```

Expected: both commands exit successfully.

- [ ] **Step 5: Commit**

```powershell
git add app/static/api.js app/static/shared-ui.js tests/test_static_report_ui.py
git commit -m "feat: add shared frontend runtime helpers"
```

---

### Task 4: Wire `app/test4.html` Prep Page

**Files:**

| Action | Path |
| --- | --- |
| Modify | `app/test4.html` |
| Create | `app/static/prep.js` |

- [ ] **Step 1: Add stable DOM hooks and module script**

In `app/test4.html`, add or update these elements:

```html
<textarea id="jobDescription"></textarea>
<textarea id="resumeText"></textarea>
<div id="topicTags"></div>
<p id="prepStatus" hidden></p>
<button id="saveDraftButton" type="button">保存草稿</button>
<button id="restoreDraftButton" type="button">恢复草稿</button>
<button id="prepButton" type="button">生成面试计划</button>
<button id="startButton" type="button">开始面试</button>
<h2 id="planTitle"></h2>
<div id="planQuestions"></div>
<script type="module" src="/static/prep.js"></script>
```

Keep the existing prototype layout, but make sure each runtime id appears exactly once.

- [ ] **Step 2: Create `app/static/prep.js`**

```js
import { getJson, postJson } from "./api.js";
import { byId, clear, createEl, renderTags, showNotice, setText } from "./shared-ui.js";

const jobDescription = byId("jobDescription");
const resumeText = byId("resumeText");
const saveDraftButton = byId("saveDraftButton");
const restoreDraftButton = byId("restoreDraftButton");
const prepButton = byId("prepButton");
const startButton = byId("startButton");
const topicTags = byId("topicTags");
const planQuestions = byId("planQuestions");
const prepStatus = byId("prepStatus");

let currentTags = [];
let latestPlan = null;
let draftId = localStorage.getItem("interviewDraftId");

function payload() {
  return {
    job_description: jobDescription.value.trim(),
    resume_text: resumeText.value.trim(),
  };
}

function setCurrentTags(tags) {
  currentTags = Array.isArray(tags) ? tags : [];
  renderTags(topicTags, currentTags);
}

function renderPlan(plan) {
  latestPlan = plan;
  setText("planTitle", plan.title || "面试计划");
  clear(planQuestions);
  for (const question of plan.questions || []) {
    const card = createEl("article", "question-card");
    card.appendChild(createEl("h3", "", question.prompt));
    card.appendChild(createEl("p", "", question.focus));
    planQuestions.appendChild(card);
  }
  setCurrentTags(plan.job_tags || []);
}

async function saveDraft() {
  const body = {
    ...payload(),
    draft_id: draftId,
    title: latestPlan ? latestPlan.title : null,
    job_tags: currentTags.length ? currentTags : null,
  };
  const draft = await postJson("/api/interview-drafts", body);
  draftId = draft.draft_id;
  localStorage.setItem("interviewDraftId", draft.draft_id);
  showNotice(prepStatus, "草稿已保存", "success");
}

async function restoreDraft() {
  if (!draftId) {
    showNotice(prepStatus, "没有可恢复的草稿", "warning");
    return;
  }
  try {
    const draft = await getJson(`/api/interview-drafts/${draftId}`);
    jobDescription.value = draft.job_description || "";
    resumeText.value = draft.resume_text || "";
    setCurrentTags(draft.job_tags || []);
    showNotice(prepStatus, "草稿已恢复", "success");
  } catch (error) {
    localStorage.removeItem("interviewDraftId");
    draftId = null;
    showNotice(prepStatus, error.message, "danger");
  }
}

async function generatePlan() {
  const plan = await postJson("/api/prep", payload());
  renderPlan(plan);
  showNotice(prepStatus, "面试计划已生成", "success");
}

async function startInterview() {
  const turn = await postJson("/api/interviews", payload());
  window.location.href = `/interview?session_id=${encodeURIComponent(turn.session_id)}`;
}

saveDraftButton.addEventListener("click", () => saveDraft().catch((error) => showNotice(prepStatus, error.message, "danger")));
restoreDraftButton.addEventListener("click", () => restoreDraft().catch((error) => showNotice(prepStatus, error.message, "danger")));
prepButton.addEventListener("click", () => generatePlan().catch((error) => showNotice(prepStatus, error.message, "danger")));
startButton.addEventListener("click", () => startInterview().catch((error) => showNotice(prepStatus, error.message, "danger")));

setCurrentTags([]);
```

- [ ] **Step 3: Run focused tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_prep_page_has_runtime_hooks tests/test_static_report_ui.py::test_page_scripts_use_real_api_endpoints -q
node --check app/static/prep.js
```

Expected: prep hooks and JS syntax pass.

- [ ] **Step 4: Commit**

```powershell
git add app/test4.html app/static/prep.js tests/test_static_report_ui.py
git commit -m "feat: wire prep prototype page"
```

---

### Task 5: Wire `app/test3.html` Interview Page

**Files:**

| Action | Path |
| --- | --- |
| Modify | `app/test3.html` |
| Create | `app/static/interview.js` |

- [ ] **Step 1: Add stable DOM hooks and module script**

In `app/test3.html`, add or update these runtime hooks:

```html
<section id="conversation"></section>
<section id="currentQuestion"></section>
<form id="answerForm">
  <textarea id="answerInput"></textarea>
  <button id="sendAnswerButton" type="submit">提交回答</button>
</form>
<button id="skipQuestionButton" type="button">下一题</button>
<button id="finishInterviewButton" type="button">结束面试</button>
<div id="questionPlan"></div>
<div id="topicTags"></div>
<p id="sessionStatus"></p>
<p id="interviewNotice" hidden></p>
<script type="module" src="/static/interview.js"></script>
```

- [ ] **Step 2: Create `app/static/interview.js`**

```js
import { getJson, getSessionId, postJson, readSse } from "./api.js";
import { byId, clear, createEl, renderTags, showNotice, setText, questionStateLabels } from "./shared-ui.js";

const sessionId = getSessionId();
const conversation = byId("conversation");
const currentQuestion = byId("currentQuestion");
const answerForm = byId("answerForm");
const answerInput = byId("answerInput");
const skipQuestionButton = byId("skipQuestionButton");
const finishInterviewButton = byId("finishInterviewButton");
const questionPlan = byId("questionPlan");
const topicTags = byId("topicTags");
const interviewNotice = byId("interviewNotice");

if (!sessionId) {
  showNotice(interviewNotice, "缺少 session_id，请从准备页开始面试", "danger");
}

function renderMessages(messages) {
  clear(conversation);
  for (const message of messages || []) {
    const item = createEl("article", `message ${message.role || message.speaker || "system"}`);
    item.appendChild(createEl("p", "", message.content || message.text || ""));
    conversation.appendChild(item);
  }
}

function renderCurrentQuestion(question) {
  clear(currentQuestion);
  if (!question) {
    currentQuestion.appendChild(createEl("p", "", "当前没有待回答题目"));
    return;
  }
  currentQuestion.appendChild(createEl("h2", "", question.prompt));
  currentQuestion.appendChild(createEl("p", "", question.focus));
}

function renderQuestions(questions) {
  clear(questionPlan);
  for (const question of questions || []) {
    const state = question.state || "pending";
    const item = createEl("div", `question-item question-${state}`);
    item.appendChild(createEl("strong", "", question.prompt || question.id));
    item.appendChild(createEl("span", "", questionStateLabels[state] || state));
    questionPlan.appendChild(item);
  }
}

function renderSnapshot(snapshot) {
  setText("sessionStatus", `状态：${snapshot.status} / ${snapshot.completed_questions || 0}/${snapshot.total_questions || 0}`);
  renderTags(topicTags, snapshot.job_tags || []);
  renderMessages(snapshot.messages || []);
  renderCurrentQuestion(snapshot.current_question);
  renderQuestions(snapshot.questions || []);
  if (snapshot.status === "finished") {
    window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
  }
}

async function loadSnapshot() {
  const snapshot = await getJson(`/api/interviews/${sessionId}`);
  renderSnapshot(snapshot);
}

async function submitAnswer(event) {
  event.preventDefault();
  const answer = answerInput.value.trim();
  if (!answer) {
    showNotice(interviewNotice, "回答不能为空", "warning");
    return;
  }
  const response = await fetch(`/api/interviews/${sessionId}/answer/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answer }),
  });
  let streamedText = "";
  await readSse(response, {
    chunk(data) {
      streamedText += data.delta || "";
      showNotice(interviewNotice, streamedText, "info");
    },
    done(data) {
      answerInput.value = "";
      renderSnapshot(data);
    },
    error(data) {
      showNotice(interviewNotice, data.detail || "提交失败", "danger");
    },
  });
  await loadSnapshot();
}

async function skipQuestion() {
  await postJson(`/api/interviews/${sessionId}/skip`, {});
  await loadSnapshot();
}

async function finishInterview() {
  await postJson(`/api/interviews/${sessionId}/finish`, {});
  window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
}

answerForm.addEventListener("submit", (event) => submitAnswer(event).catch((error) => showNotice(interviewNotice, error.message, "danger")));
skipQuestionButton.addEventListener("click", () => skipQuestion().catch((error) => showNotice(interviewNotice, error.message, "danger")));
finishInterviewButton.addEventListener("click", () => finishInterview().catch((error) => showNotice(interviewNotice, error.message, "danger")));

if (sessionId) {
  loadSnapshot().catch((error) => showNotice(interviewNotice, error.message, "danger"));
}
```

- [ ] **Step 3: Run focused tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_interview_page_has_runtime_hooks tests/test_static_report_ui.py::test_page_scripts_use_real_api_endpoints -q
node --check app/static/interview.js
```

Expected: interview hooks and JS syntax pass.

- [ ] **Step 4: Commit**

```powershell
git add app/test3.html app/static/interview.js
git commit -m "feat: wire interview prototype page"
```

---

### Task 6: Wire `app/test2.html` Report Processing Page

**Files:**

| Action | Path |
| --- | --- |
| Modify | `app/test2.html` |
| Create | `app/static/report-processing.js` |

- [ ] **Step 1: Add stable DOM hooks and module script**

In `app/test2.html`, add or update:

```html
<div id="reportProgressBar"></div>
<p id="reportProgressStatus"></p>
<div id="reportEvents"></div>
<div id="reportRagSummary"></div>
<span id="reportJobId"></span>
<button id="viewReportButton" type="button" disabled>查看报告</button>
<p id="processingNotice" hidden></p>
<script type="module" src="/static/report-processing.js"></script>
```

- [ ] **Step 2: Create `app/static/report-processing.js`**

```js
import { getJson, getSessionId } from "./api.js";
import { byId, clear, createEl, formatPercent, showNotice, setText } from "./shared-ui.js";

const sessionId = getSessionId();
const reportProgressBar = byId("reportProgressBar");
const reportEvents = byId("reportEvents");
const reportRagSummary = byId("reportRagSummary");
const viewReportButton = byId("viewReportButton");
const processingNotice = byId("processingNotice");

let timer = null;

function renderProgress(progress) {
  const percent = progress.percent ?? 0;
  reportProgressBar.style.width = formatPercent(percent);
  setText("reportProgressStatus", `${progress.stage || "queued"} · ${percent}% · ${progress.message || ""}`);
  setText("reportJobId", progress.report_job_id || "暂无任务 ID");

  clear(reportEvents);
  for (const event of progress.events || []) {
    reportEvents.appendChild(createEl("p", "", `${event.stage}: ${event.message}`));
  }

  clear(reportRagSummary);
  const rag = progress.rag || {};
  reportRagSummary.appendChild(createEl("p", "", `top_k: ${rag.top_k ?? "未返回"}`));
  reportRagSummary.appendChild(createEl("p", "", `matched_chunks: ${rag.matched_chunks ?? "未返回"}`));
}

async function poll() {
  const progress = await getJson(`/api/interviews/${sessionId}/report/progress`);
  renderProgress(progress);

  const reportResponse = await fetch(`/api/interviews/${sessionId}/report`);
  if (reportResponse.status === 200) {
    viewReportButton.disabled = false;
    window.location.href = `/report-detail?session_id=${encodeURIComponent(sessionId)}`;
    return;
  }
  if (reportResponse.status !== 202) {
    const body = await reportResponse.json().catch(() => ({}));
    showNotice(processingNotice, body.detail || "报告生成失败", "danger");
    return;
  }
  timer = window.setTimeout(() => poll().catch((error) => showNotice(processingNotice, error.message, "danger")), 3000);
}

viewReportButton.addEventListener("click", () => {
  window.location.href = `/report-detail?session_id=${encodeURIComponent(sessionId)}`;
});

if (!sessionId) {
  showNotice(processingNotice, "缺少 session_id，请从面试页进入", "danger");
} else {
  poll().catch((error) => showNotice(processingNotice, error.message, "danger"));
}

window.addEventListener("beforeunload", () => {
  if (timer) window.clearTimeout(timer);
});
```

- [ ] **Step 3: Run focused tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_processing_page_has_runtime_hooks tests/test_static_report_ui.py::test_page_scripts_use_real_api_endpoints -q
node --check app/static/report-processing.js
```

Expected: report-processing hooks and JS syntax pass.

- [ ] **Step 4: Commit**

```powershell
git add app/test2.html app/static/report-processing.js
git commit -m "feat: wire report processing prototype page"
```

---

### Task 7: Wire `app/test1.html` Report Detail Page

**Files:**

| Action | Path |
| --- | --- |
| Modify | `app/test1.html` |
| Create | `app/static/report-detail.js` |

- [ ] **Step 1: Add stable DOM hooks and module script**

In `app/test1.html`, add or update:

```html
<p id="reportStatus"></p>
<strong id="reportScore"></strong>
<p id="reportSummary"></p>
<div id="dimensionScores"></div>
<div id="reportHighlights"></div>
<div id="feedbackList"></div>
<div id="evidenceList"></div>
<button id="downloadReportButton" type="button">下载报告 (PDF)</button>
<p id="reportNotice" hidden></p>
<script type="module" src="/static/report-detail.js"></script>
```

Remove or ignore the prototype four-dimension radar chart. The runtime page must render `overall_dimension_scores` from the backend five-dimension model.

- [ ] **Step 2: Create `app/static/report-detail.js`**

```js
import { downloadPdf, getJson, getSessionId } from "./api.js";
import { byId, clear, createEl, showNotice, setText, toDimensionLabel } from "./shared-ui.js";

const sessionId = getSessionId();
const dimensionScores = byId("dimensionScores");
const reportHighlights = byId("reportHighlights");
const feedbackList = byId("feedbackList");
const evidenceList = byId("evidenceList");
const downloadReportButton = byId("downloadReportButton");
const reportNotice = byId("reportNotice");

function renderDimensions(scores) {
  clear(dimensionScores);
  for (const [name, value] of Object.entries(scores || {})) {
    const item = createEl("div", "dimension-row");
    item.appendChild(createEl("span", "", toDimensionLabel(name)));
    item.appendChild(createEl("strong", "", String(value)));
    dimensionScores.appendChild(item);
  }
}

function renderHighlights(highlights) {
  clear(reportHighlights);
  for (const highlight of highlights || []) {
    reportHighlights.appendChild(createEl("p", "", highlight));
  }
}

function renderFeedbacks(feedbacks) {
  clear(feedbackList);
  clear(evidenceList);
  const seenReferences = new Set();

  for (const feedback of feedbacks || []) {
    const card = createEl("article", "feedback-card");
    card.appendChild(createEl("h3", "", feedback.question || feedback.question_id || "题目反馈"));
    card.appendChild(createEl("p", "", `得分：${feedback.score}`));
    card.appendChild(createEl("p", "", feedback.rationale || ""));
    card.appendChild(createEl("p", "", feedback.better_answer || ""));
    feedbackList.appendChild(card);

    for (const reference of feedback.references || []) {
      const key = `${reference.source_type}:${reference.title}:${reference.content}`;
      if (seenReferences.has(key)) continue;
      seenReferences.add(key);
      const evidence = createEl("article", "evidence-card");
      evidence.appendChild(createEl("strong", "", reference.title || reference.source_type || "参考证据"));
      evidence.appendChild(createEl("p", "", reference.content || ""));
      evidenceList.appendChild(evidence);
    }
  }
}

function renderReport(report) {
  setText("reportStatus", report.is_fallback ? "兜底报告" : "报告已完成");
  setText("reportScore", String(report.overall_score ?? ""));
  setText("reportSummary", report.summary || "");
  renderDimensions(report.overall_dimension_scores || {});
  renderHighlights(report.highlights || []);
  renderFeedbacks(report.feedbacks || []);
}

async function loadReport() {
  const report = await getJson(`/api/interviews/${sessionId}/report`);
  renderReport(report);
}

downloadReportButton.addEventListener("click", () => {
  downloadPdf(
    `/api/interviews/${sessionId}/report.pdf`,
    `interview-report-${sessionId}.pdf`,
  ).catch((error) => showNotice(reportNotice, error.message, "danger"));
});

if (!sessionId) {
  showNotice(reportNotice, "缺少 session_id，请从报告生成页进入", "danger");
} else {
  loadReport().catch((error) => showNotice(reportNotice, error.message, "danger"));
}
```

- [ ] **Step 3: Run focused tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_detail_page_has_runtime_hooks tests/test_static_report_ui.py::test_shared_ui_maps_dimensions_to_chinese tests/test_static_report_ui.py::test_page_scripts_use_real_api_endpoints -q
node --check app/static/report-detail.js
```

Expected: report-detail hooks and JS syntax pass.

- [ ] **Step 4: Commit**

```powershell
git add app/test1.html app/static/report-detail.js
git commit -m "feat: wire report detail prototype page"
```

---

### Task 8: Full Verification and Documentation Cleanup

**Files:**

| Action | Path |
| --- | --- |
| Modify | `docs/interface-requirements.md` if implementation deviates from route names |
| Modify | `docs/frontend-modification-guide.md` if implementation deviates from file names |

- [ ] **Step 1: Run frontend static contract tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_page_routes.py tests/test_static_report_ui.py -q
```

Expected: all route and static frontend tests pass.

- [ ] **Step 2: Run API regression tests most affected by this stage**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_api.py tests/test_drafts.py tests/test_report_api.py tests/test_report_pdf.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: all tests pass. If PostgreSQL integration tests require a DSN and are skipped locally, record the skip count in the final summary.

- [ ] **Step 4: Manual local smoke test**

Run the server:

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Manual acceptance:

| Step | Expected |
| --- | --- |
| Enter JD and resume | Inputs accept text |
| Click generate plan | `job_tags` and questions render from `/api/prep` |
| Click start interview | Browser navigates to `/interview?session_id=...` |
| Submit answer | Streamed follow-up appears and session snapshot refreshes |
| Click next question | Question state refreshes |
| Click finish interview | Browser navigates to `/report-processing?session_id=...` |
| Report completes | Browser navigates to `/report-detail?session_id=...` |
| Click PDF download | PDF downloads without clearing the report view |

- [ ] **Step 5: Commit verification/doc cleanup**

```powershell
git add docs/interface-requirements.md docs/frontend-modification-guide.md tests/test_page_routes.py tests/test_static_report_ui.py
git commit -m "test: verify four-page frontend runtime"
```

---

## Self-Review

Spec coverage:

| Requirement | Covered by |
| --- | --- |
| Do not keep `app/static/index.html` as runtime entry | Task 1, Task 2 |
| Use four prototype pages as runtime pages | Task 1, Task 4, Task 5, Task 6, Task 7 |
| Prep page supports draft, prep, tags, start | Task 4 |
| Interview page supports snapshot, stream answer, skip, finish | Task 5 |
| Report processing page supports progress and completion polling | Task 6 |
| Report detail page supports report rendering and PDF blob download | Task 7 |
| No login or multi-user scope | Scope section |
| Use backend five dimensions | Task 3, Task 7 |

Known follow-up after this plan:

| Follow-up | Reason |
| --- | --- |
| Remove or localize CDN dependencies from prototype pages | This plan prioritizes runtime wiring; offline-perfect visual parity can be a separate cleanup if Tailwind replacement becomes large |
| Dedicated report center page | Existing API exists, but four-page interview flow should land first |
| Knowledge-base management UI | Requires separate backend capability and product design |
