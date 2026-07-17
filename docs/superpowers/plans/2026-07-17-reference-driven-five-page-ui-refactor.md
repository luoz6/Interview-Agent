# Reference-Driven Five-Page UI Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the five production desktop pages with real-data versions of the supplied single-file reference UI, including every visible control, safe report metadata, and failed-report requeue.

**Architecture:** Keep FastAPI `FileResponse` pages, native ES Modules, and the existing Tailwind build. Freeze the supplied hash-routed HTML as a design artifact, migrate one view at a time into the existing routed HTML files, keep page JavaScript responsible for real API state, and add only the bounded repository/API extensions required by report listing and requeue.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, psycopg2/PostgreSQL, HTML5, native ES Modules, Tailwind CSS 3, Playwright.

**Approved Design:** `docs/superpowers/specs/2026-07-17-reference-driven-five-page-ui-refactor-design.md`

**Execution Constraints:**

- Work on a feature branch or isolated worktree, not directly on `master`.
- Preserve unrelated untracked files under `docs/specs/` and historical `docs/superpowers/` paths.
- Do not start new PostgreSQL/Redis containers without approval; use the existing configured services for authenticated gates.
- Do not download or load a local embedding model. Deterministic browser support and fake-agent paths are sufficient for this UI plan.
- Use `F:\python3.11\python.exe` for pytest in this workspace.
- Treat 1280px as the minimum desktop width and 1440x1000 as the primary visual acceptance viewport.

---

### Task 1: Freeze the Reference Artifact and Baseline Contract

**Files:**
- Create: `tests/test_reference_ui_artifact.py`
- Create: `docs/prototypes/interview-agent-single-file.html` by moving `app/interview-agent-single-file.html`
- Create: `docs/reference-driven-five-page-ui-acceptance.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the failing artifact test**

```python
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "prototypes" / "interview-agent-single-file.html"
EXPECTED_SHA256 = "A4549DD6D1B0F37C4207338E1ABC33D00CD44453A7643FF2DF81F25F3D35E283"


def test_reference_ui_artifact_is_frozen():
    assert REFERENCE.exists()
    assert sha256(REFERENCE.read_bytes()).hexdigest().upper() == EXPECTED_SHA256


def test_reference_ui_is_not_served_from_app_root():
    assert not (ROOT / "app" / "interview-agent-single-file.html").exists()
```

- [ ] **Step 2: Run the test and verify the missing destination fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_reference_ui_artifact.py -q
```

Expected: FAIL because `docs/prototypes/interview-agent-single-file.html` does not exist.

- [ ] **Step 3: Freeze the reference without changing its bytes**

Run:

```powershell
New-Item -ItemType Directory -Force docs/prototypes | Out-Null
Move-Item -LiteralPath app/interview-agent-single-file.html -Destination docs/prototypes/interview-agent-single-file.html
Get-FileHash -Algorithm SHA256 -LiteralPath docs/prototypes/interview-agent-single-file.html
```

Expected hash: `A4549DD6D1B0F37C4207338E1ABC33D00CD44453A7643FF2DF81F25F3D35E283`.

- [ ] **Step 4: Ignore visual-companion session files**

Add this exact line to `.gitignore`:

```gitignore
.superpowers/
```

- [ ] **Step 5: Create the acceptance record in pending state**

```markdown
# Reference-Driven Five-Page UI Acceptance

Status: `PENDING`

Reference SHA-256: `A4549DD6D1B0F37C4207338E1ABC33D00CD44453A7643FF2DF81F25F3D35E283`

## Gates

| Gate | Result |
| --- | --- |
| Reference artifact frozen | PASS |
| Five production pages migrated | PENDING |
| Real controls and API bindings | PENDING |
| PostgreSQL report metadata/requeue | PENDING |
| Deterministic desktop Playwright | PENDING |
| Full Python regression | PENDING |
```

- [ ] **Step 6: Run the focused test**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_reference_ui_artifact.py -q
```

Expected: `2 passed`.

- [ ] **Step 7: Commit the frozen artifact**

```powershell
git add .gitignore docs/prototypes/interview-agent-single-file.html docs/reference-driven-five-page-ui-acceptance.md tests/test_reference_ui_artifact.py
git commit -m "docs: freeze five-page UI reference"
```

### Task 2: Establish the Shared Production Visual System

**Files:**
- Modify: `app/static/prototype-source.css`
- Modify: `app/static/prototype.css` through the existing build
- Modify: `app/static/shared-ui.js`
- Test: `tests/test_static_report_ui.py`

- [ ] **Step 1: Add failing visual-token and shared-component tests**

Append tests that read `prototype-source.css` and assert the literal production contract:

```python
def test_production_visual_tokens_replace_reference_decorations():
    css = read_static_file("prototype-source.css")
    for token in (
        "--color-primary: #2563eb",
        "--color-primary-hover: #1d4ed8",
        "--color-page: #f8fafc",
        "--color-line: #e2e8f0",
        "--radius-card: 8px",
        "--radius-control: 6px",
    ):
        assert token in css
    assert "linear-gradient" not in css


def test_shared_reference_components_are_declared():
    css = read_static_file("prototype-source.css")
    for selector in (
        ".app-topbar",
        ".app-brand",
        ".app-nav",
        ".workflow-shell",
        ".workflow-sidebar",
        ".workflow-step",
        ".ui-card",
        ".ui-button",
        ".ui-badge",
        ".ui-notice",
        ".progress-track",
    ):
        assert selector in css
```

Use the existing test helper that reads files; do not add a second path helper.

- [ ] **Step 2: Run the tests and verify the token test fails**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: FAIL because the semantic token/component layer is absent.

- [ ] **Step 3: Replace the base/token layer and add literal semantic components**

Use this exact token block in `prototype-source.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --color-primary: #2563eb;
    --color-primary-hover: #1d4ed8;
    --color-page: #f8fafc;
    --color-surface: #ffffff;
    --color-text: #1f2937;
    --color-muted: #64748b;
    --color-line: #e2e8f0;
    --color-success: #16a34a;
    --color-warning: #d97706;
    --color-danger: #dc2626;
    --radius-card: 8px;
    --radius-control: 6px;
    --shadow-card: 0 1px 2px rgba(15, 23, 42, 0.06);
  }

  body {
    min-width: 1280px;
    background: var(--color-page);
    color: var(--color-text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    letter-spacing: 0;
  }
}
```

Inside `@layer components`, add literal selectors for the shared shell. Use CSS properties or `@apply` with complete utility names; do not construct class fragments in JavaScript:

```css
.app-topbar { height: 4rem; display: flex; align-items: center; justify-content: space-between; padding: 0 1.5rem; background: var(--color-surface); border-bottom: 1px solid var(--color-line); }
.app-brand { display: inline-flex; align-items: center; gap: .625rem; color: var(--color-text); font-weight: 700; text-decoration: none; }
.app-nav { align-self: stretch; display: flex; align-items: center; gap: 1.5rem; }
.app-nav a { height: 4rem; display: inline-flex; align-items: center; color: var(--color-muted); border-bottom: 2px solid transparent; }
.app-nav a[aria-current="page"] { color: var(--color-primary); border-color: var(--color-primary); font-weight: 600; }
.workflow-shell { min-height: calc(100vh - 4rem); display: grid; grid-template-columns: 232px minmax(0, 1fr); }
.workflow-sidebar { background: var(--color-surface); border-right: 1px solid var(--color-line); padding: 1.25rem .875rem; }
.workflow-step { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: .625rem; padding: .625rem; border-radius: var(--radius-control); }
.workflow-step[aria-current="step"] { background: #eff6ff; color: #1e3a8a; }
.ui-card { border: 1px solid var(--color-line); border-radius: var(--radius-card); background: var(--color-surface); box-shadow: var(--shadow-card); }
.ui-button { min-height: 2.25rem; border: 1px solid #cbd5e1; border-radius: var(--radius-control); background: #fff; color: #334155; padding: .5rem .875rem; font-weight: 600; }
.ui-button-primary { border-color: var(--color-primary); background: var(--color-primary); color: #fff; }
.ui-button-primary:hover { background: var(--color-primary-hover); }
.ui-button:disabled { cursor: not-allowed; opacity: .55; }
.ui-badge { display: inline-flex; align-items: center; border: 1px solid #bfdbfe; border-radius: 9999px; background: #eff6ff; color: #1d4ed8; padding: .25rem .625rem; font-size: .75rem; font-weight: 600; }
.ui-notice { border-left: 3px solid var(--color-primary); background: #eff6ff; color: #1e40af; padding: .625rem .75rem; }
.ui-notice[data-type="danger"] { border-color: var(--color-danger); background: #fef2f2; color: #991b1b; }
.progress-track { height: .4375rem; overflow: hidden; border-radius: 4px; background: #e2e8f0; }
.progress-fill { height: 100%; background: var(--color-primary); }
```

Retain existing `.tag`, notice, icon fallback, question-state, `[hidden]`, and mobile compatibility behavior until all five pages migrate. The existing mobile evidence test must stay green, but this feature adds no new mobile reference-layout acceptance; the desktop shell may override `min-width` inside the existing narrow compatibility media rule solely to avoid overflow.

- [ ] **Step 4: Add safe shared formatting utilities**

Add to `shared-ui.js`:

```javascript
export function formatDuration(totalSeconds) {
  const safe = Math.max(0, Number(totalSeconds) || 0);
  const minutes = Math.floor(safe / 60);
  const seconds = Math.floor(safe % 60);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function setPressed(element, pressed) {
  if (!element) return;
  element.setAttribute("aria-pressed", pressed ? "true" : "false");
}
```

- [ ] **Step 5: Build CSS and run shared UI tests**

```powershell
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: CSS build succeeds and focused tests pass.

- [ ] **Step 6: Commit the shared visual system**

```powershell
git add app/static/prototype-source.css app/static/prototype.css app/static/shared-ui.js tests/test_static_report_ui.py
git commit -m "style: establish reference UI system"
```

### Task 3: Rebuild Interview Preparation and Implement File Import

**Files:**
- Modify: `app/test4.html`
- Modify: `app/static/prep.js`
- Modify: `app/static/prototype-source.css`
- Modify: `app/static/prototype.css` through build
- Test: `tests/test_static_report_ui.py`

- [ ] **Step 1: Add failing preparation DOM contract assertions**

Extend the existing preparation-page test with these required IDs:

```python
for element_id in (
    "jobDescription",
    "jobDescriptionFileInput",
    "jobDescriptionFileButton",
    "jobDescriptionFileMeta",
    "resumeText",
    "resumeFileInput",
    "resumeFileButton",
    "resumeFileMeta",
    "topicTags",
    "prepStatus",
    "planTitle",
    "planQuestionCount",
    "planDuration",
    "planQuestions",
    "prepKnowledgeStatus",
    "prepContextSummary",
    "prepContextTopics",
    "prepQuestionHints",
    "saveDraftButton",
    "restoreDraftButton",
    "prepButton",
    "startButton",
):
    assert f'id="{element_id}"' in html
assert 'accept=".txt,.md,text/plain,text/markdown"' in html
```

Also assert the page uses `app-topbar`, `workflow-shell`, and the real `/prep`, `/reports`, and `/help` links.

- [ ] **Step 2: Run the preparation contract test and verify failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: FAIL on the new file input/metadata/plan IDs.

- [ ] **Step 3: Replace `test4.html` with the reference preparation view**

Copy the `view-prep` structure from the frozen reference into the routed page, then make these production substitutions:

```html
<input id="jobDescriptionFileInput" type="file" accept=".txt,.md,text/plain,text/markdown" hidden>
<button id="jobDescriptionFileButton" class="ui-button" type="button">上传 JD 文件</button>
<span id="jobDescriptionFileMeta" class="field-meta">未选择文件</span>

<input id="resumeFileInput" type="file" accept=".txt,.md,text/plain,text/markdown" hidden>
<button id="resumeFileButton" class="ui-button" type="button">上传简历文件</button>
<span id="resumeFileMeta" class="field-meta">未选择文件</span>
```

Both textareas must start empty and use generic placeholders. Preserve every existing preparation ID listed above, include `<p id="prepStatus" class="ui-notice" role="status" aria-live="polite" hidden></p>`, and retain `<script type="module" src="/static/prep.js"></script>`.

Do not copy `data-view-target`, demo textarea values, demo tags, demo plan rows, inline `style`, or the reference hash router.

- [ ] **Step 4: Add deterministic text-file import behavior**

Add to `prep.js`:

```javascript
const MAX_TEXT_FILE_BYTES = 1024 * 1024;
const SUPPORTED_TEXT_EXTENSIONS = [".txt", ".md"];

function hasSupportedExtension(fileName) {
  const lower = String(fileName || "").toLowerCase();
  return SUPPORTED_TEXT_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

async function importTextFile(file, textarea, metadataNode, label) {
  if (!file) return;
  if (!hasSupportedExtension(file.name)) {
    throw new Error(`${label}仅支持 .txt 或 .md 文件`);
  }
  if (file.size > MAX_TEXT_FILE_BYTES) {
    throw new Error(`${label}文件不能超过 1 MiB`);
  }
  const content = await file.text();
  textarea.value = content;
  metadataNode.textContent = `${file.name} · ${content.length} 字符`;
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}
```

Bind each visible button to its hidden input and each input `change` to `importTextFile`. Route errors through `showNotice(prepStatus, error.message, "danger")`. Never send file bytes to the API.

- [ ] **Step 5: Render only real plan metrics**

Inside `renderPlan(plan)`, set:

```javascript
setText("planTitle", plan.title || "面试计划");
setText("planQuestionCount", `${(plan.questions || []).length} 题`);
setText(
  "planDuration",
  `${(plan.questions || []).length * 4}-${(plan.questions || []).length * 6} 分钟`,
);
```

Keep the existing safe tag, question, `PrepContext`, knowledge status, and evidence rendering. The idle state must clear these containers rather than render demo values.

- [ ] **Step 6: Add preparation-specific CSS and rebuild**

Add literal semantic selectors for `.prep-main`, `.prep-grid`, `.prep-fields`, `.prep-plan`, `.field-card`, `.field-header`, `.text-source`, `.field-actions`, `.plan-stats`, and `.plan-timeline`. Use the frozen reference grid proportions, mapped production tokens, 8px maximum card radius, and no gradient/inline style.

```powershell
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_page_routes.py -q
```

Expected: focused page tests pass.

- [ ] **Step 7: Commit the preparation page**

```powershell
git add app/test4.html app/static/prep.js app/static/prototype-source.css app/static/prototype.css tests/test_static_report_ui.py
git commit -m "feat: rebuild interview preparation UI"
```

### Task 4: Rebuild Live Interview with Focus, Drafts, and Review Status

**Files:**
- Modify: `app/test3.html`
- Modify: `app/static/interview.js`
- Modify: `app/static/prototype-source.css`
- Modify: `app/static/prototype.css` through build
- Test: `tests/test_static_report_ui.py`

- [ ] **Step 1: Add failing interview DOM and behavior contract tests**

Require the existing IDs plus:

```python
for element_id in (
    "focusModeButton",
    "answerCount",
    "answerDraftStatus",
    "elapsedTime",
    "estimatedRemainingTime",
    "roundReviewStatus",
):
    assert f'id="{element_id}"' in html
for marker in (
    "interviewAnswerDraft:",
    'document.body.classList.toggle("interview-focus-mode"',
    'event.key === "Escape"',
    "getQuestionEvaluations",
):
    assert marker in js
```

- [ ] **Step 2: Run the interview contract test and verify failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: FAIL because focus/draft/review elements and behavior are missing.

- [ ] **Step 3: Replace `test3.html` with the reference interview workbench**

Copy the `view-interview` layout into the routed page and preserve every existing ID consumed by `interview.js`. Add:

```html
<button id="focusModeButton" class="ui-button" type="button" aria-pressed="false">专注模式</button>
<span id="elapsedTime">0:00</span>
<span id="estimatedRemainingTime">--</span>
<span id="roundReviewStatus">等待逐题评审</span>
<span id="answerDraftStatus">尚未保存</span>
<span><span id="answerCount">0</span> / 5000</span>
```

Keep `currentQuestion`, `conversation`, `answerForm`, `answerInput`, all command buttons, question plan, tags, status, notice, and the existing module script. Remove all reference demo questions/messages/status values.

- [ ] **Step 4: Implement focus mode**

Import `setPressed` and add:

```javascript
const focusModeButton = byId("focusModeButton");

function setFocusMode(enabled) {
  document.body.classList.toggle("interview-focus-mode", enabled);
  setPressed(focusModeButton, enabled);
  focusModeButton.textContent = enabled ? "退出专注" : "专注模式";
}

focusModeButton.addEventListener("click", () => {
  setFocusMode(focusModeButton.getAttribute("aria-pressed") !== "true");
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setFocusMode(false);
});
```

CSS for `body.interview-focus-mode` must hide the two side columns and change the workbench to one `minmax(0, 1fr)` column without moving the answer editor.

- [ ] **Step 5: Implement per-question answer drafts**

Add state and exact key semantics:

```javascript
let currentQuestionId = null;
let draftTimer = null;

function answerDraftKey(questionId = currentQuestionId) {
  return questionId ? `interviewAnswerDraft:${sessionId}:${questionId}` : null;
}

function persistAnswerDraft() {
  const key = answerDraftKey();
  if (!key) return;
  localStorage.setItem(key, answerInput.value);
  setText("answerDraftStatus", "草稿已保存");
}

function restoreAnswerDraft(questionId) {
  const key = answerDraftKey(questionId);
  if (!key) return;
  const value = localStorage.getItem(key);
  if (value !== null && !answerInput.value) answerInput.value = value;
  setText("answerCount", String(answerInput.value.length));
}

function clearAnswerDraft(questionId) {
  const key = answerDraftKey(questionId);
  if (key) localStorage.removeItem(key);
}
```

On input, update `answerCount`, set status to `保存中`, and debounce `persistAnswerDraft` by 300ms. In `renderSnapshot`, update `currentQuestionId` and restore only the matching draft. Capture `submittedQuestionId` before commands; clear it only after successful answer, skip, or finish. Do not clear on SSE/network/version-conflict failure.

- [ ] **Step 6: Render real timing and review status**

Import `formatDuration` and `getQuestionEvaluations`. In `renderSnapshot`:

```javascript
setText("elapsedTime", formatDuration(snapshot.elapsed_seconds));
setText("estimatedRemainingTime", formatDuration(snapshot.estimated_remaining_seconds));
```

After each snapshot load and successful command, call the evaluations API and render `已评审 X / 已关闭 Y`, using completed/failed evaluation records and `snapshot.completed_questions`. When the evaluations call fails, show `逐题评审状态暂不可用` without blocking interview commands.

- [ ] **Step 7: Add interview CSS, build, and run focused tests**

Add literal selectors for `.interview-shell`, `.question-nav`, `.question-item`, `.interview-main`, `.question-banner`, `.conversation-panel`, `.message`, `.answer-panel`, `.interview-side`, `.context-section`, and `.interview-focus-mode` using the reference dimensions and production token map.

```powershell
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_page_routes.py -q
```

Expected: focused tests pass.

- [ ] **Step 8: Commit the interview workbench**

```powershell
git add app/test3.html app/static/interview.js app/static/prototype-source.css app/static/prototype.css tests/test_static_report_ui.py
git commit -m "feat: rebuild live interview workbench"
```

### Task 5: Rebuild Report Processing with Truthful Progress State

**Files:**
- Modify: `app/test2.html`
- Modify: `app/static/report-processing.js`
- Modify: `app/static/prototype-source.css`
- Modify: `app/static/prototype.css` through build
- Test: `tests/test_static_report_ui.py`

- [ ] **Step 1: Add failing processing DOM contract assertions**

Require:

```python
for element_id in (
    "reportProgressStatus",
    "reportProgressText",
    "reportProgressBar",
    "reportStageList",
    "reportEvents",
    "reportJobId",
    "reportPath",
    "reportMetrics",
    "reportRagSummary",
    "continueInBackgroundButton",
    "viewReportButton",
    "processingNotice",
):
    assert f'id="{element_id}"' in html
assert "setInterval" not in js
assert 'window.location.href = "/reports"' in js
```

- [ ] **Step 2: Run the processing test and verify failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: FAIL on the new stage/metric/background controls.

- [ ] **Step 3: Replace `test2.html` with the reference processing view**

Preserve the existing polling IDs and module entry. Replace demo values with empty/loading states:

```html
<div id="reportProgressText" class="processing-percent">0%</div>
<div class="progress-track"><div id="reportProgressBar" class="progress-fill" style="width:0%"></div></div>
<div id="reportStageList" class="processing-stages"></div>
<div id="reportEvents" class="processing-events"></div>
<span id="reportPath">Unavailable</span>
<div id="reportMetrics" class="processing-metrics"></div>
<button id="continueInBackgroundButton" class="ui-button" type="button">后台继续生成</button>
```

The only inline style allowed here is the dynamic initial width `0%`; JavaScript owns subsequent width updates. Do not copy the reference's static `68%`, job ID, timestamps, stages, or status claims.

- [ ] **Step 4: Replace demo stage rendering with progress-driven rendering**

Add:

```javascript
const stageLabels = {
  queued: "等待报告任务",
  retrieving: "读取会话与评审记录",
  evaluating: "补齐逐题评审",
  coaching: "汇总整体表现",
  completed: "报告已完成",
  failed: "报告生成失败",
};

function renderStageTimeline(progress) {
  clear(byId("reportStageList"));
  const events = Array.isArray(progress.events) ? progress.events : [];
  const items = events.length ? events : [{ stage: progress.stage, message: progress.message }];
  for (const event of items) {
    const row = createEl("section", "processing-stage");
    row.dataset.stage = event.stage || "queued";
    row.appendChild(createEl("strong", "", stageLabels[event.stage] || event.stage || "等待处理"));
    row.appendChild(createEl("p", "", event.message || ""));
    byId("reportStageList").appendChild(row);
  }
}
```

`renderProgress` must set `reportProgressText`, width, job ID, stage text, and safe metrics from the response. Map `report_path` only for `microbatch`, `full_session`, and `full_session_fallback`; otherwise show `Unavailable`. Render microbatch counts only when numeric fields exist.

- [ ] **Step 5: Bind real navigation and failure behavior**

```javascript
byId("continueInBackgroundButton").addEventListener("click", () => {
  window.location.href = "/reports";
});
```

On completed report response, navigate to encoded report detail as today. On failed progress/report status, stop polling, set the failure notice, enable background navigation, and do not claim that a worker/database is healthy. Delete any demo progress `setInterval`.

- [ ] **Step 6: Add processing CSS, build, test, and commit**

Add `.processing-grid`, `.processing-card`, `.processing-percent`, `.processing-stages`, `.processing-stage`, `.processing-metrics`, `.processing-events`, and `.processing-sidebar` with the reference layout and mapped tokens.

```powershell
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_report_api.py -q
git add app/test2.html app/static/report-processing.js app/static/prototype-source.css app/static/prototype.css tests/test_static_report_ui.py
git commit -m "feat: rebuild report processing UI"
```

Expected: focused tests pass before commit.

### Task 6: Return Safe Joined Report-List Metadata

**Files:**
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/api/routes.py`
- Test: `tests/test_session_report_store.py`
- Test: `tests/test_postgres_session_store.py`
- Test: `tests/test_report_api.py`

- [ ] **Step 1: Add failing in-memory metadata test**

Extend the existing report-list store test:

```python
item_state = store.get(session_id)
item = store.list_reports(limit=1)[0]
assert item["session_summary"] == {
    "job_title": item_state["plan"].title,
    "job_tags": item_state["job_tags"],
    "question_count": len(item_state["plan"].questions),
    "started_at": item_state["started_at"],
    "finished_at": item_state["finished_at"],
}
assert "job_description" not in item["session_summary"]
assert "resume_text" not in item["session_summary"]
assert "messages" not in item["session_summary"]
```

Use the session ID already created by the fixture; do not create a second test-only store API.

- [ ] **Step 2: Add failing PostgreSQL parity test**

In the existing pg-marked list test, assert the recovered item contains the same five allowlisted `session_summary` fields and no private fields. Also assert list operation succeeds after store reinstantiation.

- [ ] **Step 3: Add failing API field/path/duration tests**

For a finished session with a report progress metadata path, assert:

```python
item = client.get("/api/reports").json()["items"][0]
assert item["job_title"] == "Backend mock interview"
assert item["job_tags"]
assert item["question_count"] == 1
assert item["duration_seconds"] >= 0
assert item["report_path"] == "microbatch"
assert "job_description" not in item
assert "resume_text" not in item
assert "messages" not in item
```

Parameterize progress metadata with `microbatch`, `full_session`, `full_session_fallback`, `fallback_failed`, and no path. Expected public results are the first three literal values followed by `None`, `None`.

- [ ] **Step 4: Run tests and verify missing summary failures**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_report_store.py tests/test_report_api.py -q
```

Expected: FAIL because `session_summary` and safe API fields do not exist.

- [ ] **Step 5: Add the in-memory cross-reference**

In `InterviewSessionStore.list_reports`, add the allowlist while iterating selected records:

```python
state = self._states[session_id]
items.append(
    {
        "session_id": session_id,
        "record": record,
        "session_summary": {
            "job_title": state["plan"].title,
            "job_tags": list(state["job_tags"]),
            "question_count": len(state["plan"].questions),
            "started_at": state["started_at"],
            "finished_at": state["finished_at"],
        },
        "_index": index,
    }
)
```

Do not include the full state in the returned item.

- [ ] **Step 6: Replace the PostgreSQL report-only query with one JOIN**

Use aliases and prefixed identifiers:

```sql
SELECT reports.session_id, reports.status, reports.progress_json,
       reports.report_json, reports.error, reports.created_at,
       reports.completed_at, reports.failed_at,
       sessions.plan_json, sessions.job_tags,
       sessions.started_at, sessions.finished_at
FROM {reports} AS reports
JOIN {sessions} AS sessions
  ON sessions.session_id = reports.session_id
{where_clause}
ORDER BY reports.created_at DESC
LIMIT %s
```

Qualify the filter as `reports.status = %s`. Parse `row[8]` with `InterviewPlan.model_validate`, normalize timestamp rows with the existing `_iso_timestamp`, then build the same allowlisted `session_summary` from rows 8-11. Keep one connection, one cursor, and one query.

- [ ] **Step 7: Add route allowlisting and canonical path helpers**

Add:

```python
_PUBLIC_REPORT_PATHS = {
    "microbatch",
    "full_session",
    "full_session_fallback",
}


def _public_report_path(record) -> str | None:
    metadata = record.progress.metadata if record.progress is not None else {}
    value = metadata.get("report_path")
    return value if value in _PUBLIC_REPORT_PATHS else None


def _duration_seconds(summary: dict) -> int | None:
    started_at = summary.get("started_at")
    finished_at = summary.get("finished_at")
    if not started_at or not finished_at:
        return None
    started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
    return max(0, int((finished - started).total_seconds()))
```

Pass `session_summary` into `_report_summary_to_dict` and expose only the approved fields. Keep the current report URLs and old fields unchanged.

- [ ] **Step 8: Run memory/API and authenticated PostgreSQL tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_report_store.py tests/test_report_api.py -q
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py -q -m pg_runtime
```

Expected: focused suites pass. The PostgreSQL command uses the already-configured test DSN; if it is absent, record the gate as not run rather than starting a container.

- [ ] **Step 9: Commit the joined report summaries**

```powershell
git add app/services/session.py app/services/postgres_session.py app/api/routes.py tests/test_session_report_store.py tests/test_postgres_session_store.py tests/test_report_api.py
git commit -m "feat: expose safe report list metadata"
```

### Task 7: Expose Failed Report Requeue Through the Runtime Port and API

**Files:**
- Modify: `app/ports/runtime.py`
- Modify: `app/api/routes.py`
- Modify: `tests/browser_support_app.py`
- Test: `tests/test_runtime_ports.py`
- Test: `tests/test_report_api.py`
- Test: `tests/test_report_jobs.py`

- [ ] **Step 1: Add failing protocol and API tests**

Add the protocol assertion:

```python
def test_report_job_queue_requires_requeue_failed():
    assert hasattr(ReportJobQueue, "requeue_failed")
```

Add a fake queue with `get_job_by_session` and `requeue_failed`, then cover:

```python
response = client.post(f"/api/interviews/{session_id}/report/requeue")
assert response.status_code == 202
assert response.json() == {
    "session_id": session_id,
    "status": "queued",
    "report_progress_url": f"/api/interviews/{session_id}/report/progress",
}
```

Also assert exact details/statuses for missing session, missing job, queued/running/retrying, completed, status-race `ValueError`, and unavailable queue `RuntimeError` as specified by the design.

- [ ] **Step 2: Run tests and verify protocol/route failures**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_ports.py tests/test_report_api.py -q
```

Expected: FAIL because the port method and route do not exist.

- [ ] **Step 3: Extend the runtime port**

Add exactly:

```python
def requeue_failed(self, session_id: str) -> dict[str, Any]:
    ...
```

to `ReportJobQueue` after `get_job_by_session`.

- [ ] **Step 4: Add an injectable queue dependency**

In routes:

```python
def get_report_job_queue():
    try:
        return get_report_job_store()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="report queue is unavailable",
        ) from exc
```

Use this dependency only for the new endpoint. Update browser support to override `get_report_job_queue` with its deterministic job store.

- [ ] **Step 5: Implement stable prechecks and exception mapping**

```python
@router.post(
    "/interviews/{session_id}/report/requeue",
    status_code=202,
)
def requeue_failed_report(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
    queue=Depends(get_report_job_queue),
):
    try:
        store.get(session_id)
    except ValueError as exc:
        raise HTTPException(404, "interview session not found") from exc

    job = queue.get_job_by_session(session_id)
    if job is None:
        raise HTTPException(404, "report job not found")
    if job["status"] in {"queued", "retrying", "running"}:
        raise HTTPException(409, "report job is already queued or processing")
    if job["status"] == "completed":
        raise HTTPException(409, "completed report cannot be requeued")
    if job["status"] != "failed":
        raise HTTPException(409, "report job is not failed")

    try:
        queue.requeue_failed(session_id)
    except ValueError as exc:
        raise HTTPException(409, "report job is not failed") from exc
    return {
        "session_id": session_id,
        "status": "queued",
        "report_progress_url": f"/api/interviews/{session_id}/report/progress",
    }
```

- [ ] **Step 6: Align deterministic browser support**

Extend `BrowserReportJobStore` with `requeue_failed` using its existing in-memory job map and `get_job_by_session`. It must reject non-failed jobs with `ValueError("report job is not failed")`, call `store.mark_report_processing(session_id)`, set job status to queued, increment replay count, and return the updated dict. Do not change the production PostgreSQL implementation.

- [ ] **Step 7: Run focused and PostgreSQL report-job tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_ports.py tests/test_report_api.py tests/test_report_jobs.py -q
```

Expected: all focused tests pass, including existing atomic requeue tests.

- [ ] **Step 8: Commit the HTTP requeue surface**

```powershell
git add app/ports/runtime.py app/api/routes.py tests/browser_support_app.py tests/test_runtime_ports.py tests/test_report_api.py tests/test_report_jobs.py
git commit -m "feat: expose failed report requeue API"
```

### Task 8: Rebuild Report Detail and Render Safe Runtime Trace

**Files:**
- Modify: `app/test1.html`
- Modify: `app/static/report-detail.js`
- Modify: `app/static/shared-ui.js`
- Modify: `app/static/prototype-source.css`
- Modify: `app/static/prototype.css` through build
- Test: `tests/test_static_report_ui.py`
- Test: `tests/test_report_api.py`

- [ ] **Step 1: Add failing report-detail DOM/privacy tests**

Require section and navigation IDs:

```python
for element_id in (
    "reportOverview",
    "reportQuestionEvaluations",
    "reportImprovements",
    "reportRuntimeTrace",
    "reportHighScoreCount",
    "reportImprovementCount",
    "reportStrengths",
    "reportRisks",
    "agentRunList",
    "runtimeEventList",
    "runtimeTraceNotice",
):
    assert f'id="{element_id}"' in html
assert "超过候选人" not in html
assert "safe_metadata" not in js
assert "payload_json" not in js
```

Also assert navigation anchors point to the four IDs and the existing report/evaluation/PDF IDs remain.

- [ ] **Step 2: Run the detail test and verify failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: FAIL because reference detail sections and trace containers are absent.

- [ ] **Step 3: Replace `test1.html` with the reference detail view**

Copy the reference report-detail composition into the routed page. Preserve all current rendering IDs, add the IDs above, and leave every metric/list container empty or loading. Delete the percentile block, demo score/dimensions/quotes/evidence, inline styles, and demo runtime labels.

Use normal same-document anchors such as `<a href="#reportQuestionEvaluations">`; these are section anchors, not SPA view routing.

- [ ] **Step 4: Derive truthful summary metrics and improvement panels**

In `renderReport(report)`:

```javascript
const feedbacks = Array.isArray(report.feedbacks) ? report.feedbacks : [];
setText("reportHighScoreCount", String(feedbacks.filter((item) => Number(item.score) >= 80).length));
setText("reportImprovementCount", String(feedbacks.filter((item) => Number(item.score) < 80).length));
renderTextList(byId("reportStrengths"), report.highlights || [], "暂无优势总结");
const risks = [...feedbacks]
  .sort((left, right) => Number(left.score || 0) - Number(right.score || 0))
  .slice(0, 3)
  .map((item) => item.critique || item.better_answer)
  .filter(Boolean);
renderTextList(byId("reportRisks"), risks, "暂无重点改进项");
```

Add `renderTextList` to `shared-ui.js`. It must clear the container, create `<li>` nodes with `textContent`, and use the supplied empty message.

- [ ] **Step 5: Load only safe trace fields**

Add:

```javascript
async function loadRuntimeTrace() {
  try {
    const [runs, events] = await Promise.all([
      getJson(`/api/interviews/${sessionId}/agent-runs?limit=100`),
      getJson(`/api/interviews/${sessionId}/runtime-events?limit=100`),
    ]);
    renderAgentRuns(runs.items || []);
    renderRuntimeEvents(events.items || []);
  } catch (error) {
    showNotice(runtimeTraceNotice, "运行轨迹暂不可用，报告内容不受影响", "warning");
  }
}
```

`renderAgentRuns` may read only `run_id`, `correlation_id`, `agent`, `operation`, `status`, `latency_ms`, `error_code`, `started_at`, and `finished_at`. `renderRuntimeEvents` may read only identifiers, type, status, attempts, replay count, stable error code, and timestamps. Do not inspect arbitrary metadata or payload fields.

- [ ] **Step 6: Add report CSS, build, and run focused tests**

Add literal selectors for `.report-layout`, `.report-nav`, `.report-main`, `.score-card`, `.score-ring`, `.summary-metrics`, `.dimension-bars`, `.risk-grid`, `.evaluation-card`, `.evidence-grid`, and `.runtime-trace-grid`.

```powershell
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_report_api.py -q
```

Expected: focused tests pass.

- [ ] **Step 7: Commit report detail**

```powershell
git add app/test1.html app/static/report-detail.js app/static/shared-ui.js app/static/prototype-source.css app/static/prototype.css tests/test_static_report_ui.py tests/test_report_api.py
git commit -m "feat: rebuild report detail UI"
```

### Task 9: Rebuild Report Center with Search, Filters, Pagination, Download, and Requeue

**Files:**
- Modify: `app/test0.html`
- Modify: `app/static/report-center.js`
- Modify: `app/static/prototype-source.css`
- Modify: `app/static/prototype.css` through build
- Test: `tests/test_static_report_ui.py`
- Test: `tests/test_report_api.py`

- [ ] **Step 1: Add failing report-center contract tests**

Require:

```python
for element_id in (
    "reportOverviewTotal",
    "reportOverviewCompleted",
    "reportOverviewProcessing",
    "reportOverviewFailed",
    "reportSearch",
    "reportDateFilter",
    "reportsTableBody",
    "reportsEmptyState",
    "paginationPrevious",
    "paginationPages",
    "paginationNext",
    "reportsStatus",
    "refreshReportsButton",
    "startNewInterviewButton",
):
    assert f'id="{element_id}"' in html
for status in ("all", "completed", "processing", "failed"):
    assert f'data-report-status="{status}"' in html
for marker in ("pageSize: 5", "report/requeue", "downloadPdf", "created_at"):
    assert marker in js
```

- [ ] **Step 2: Run the center contract test and verify failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: FAIL because the reference table controls do not exist.

- [ ] **Step 3: Replace `test0.html` with the reference report-center view**

Copy the reference overview/sidebar/table composition into the routed page. Keep all overview values and the table body empty until JavaScript loads data. Use:

```html
<input id="reportSearch" class="report-search" type="search" placeholder="搜索岗位、会话、标签或状态">
<select id="reportDateFilter" class="report-select" aria-label="日期筛选">
  <option value="30">最近 30 天</option>
  <option value="all">全部日期</option>
</select>
<tbody id="reportsTableBody"></tbody>
<div id="reportsEmptyState" class="empty-state" hidden></div>
```

Status controls are buttons with `data-report-status`, `aria-pressed`, and real count nodes. Pagination buttons must be real buttons. Do not copy demo rows, counts, dates, scores, status, paths, or `data-toast`.

- [ ] **Step 4: Replace the renderer with explicit view state**

Use:

```javascript
const viewState = {
  items: [],
  query: "",
  status: "all",
  days: "30",
  page: 1,
  pageSize: 5,
};

function matchesQuery(item) {
  const haystack = [
    item.job_title,
    item.session_id,
    ...(item.job_tags || []),
    item.summary,
    item.status,
  ].filter(Boolean).join(" ").toLocaleLowerCase();
  return haystack.includes(viewState.query.toLocaleLowerCase());
}

function matchesDate(item) {
  if (viewState.days === "all") return true;
  const timestamp = Date.parse(item.finished_at || item.created_at || "");
  if (!Number.isFinite(timestamp)) return false;
  return timestamp >= Date.now() - Number(viewState.days) * 24 * 60 * 60 * 1000;
}

function filteredReports() {
  return viewState.items.filter((item) =>
    (viewState.status === "all" || item.status === viewState.status)
    && matchesDate(item)
    && matchesQuery(item)
  );
}
```

Every filter/search change sets `page = 1` and calls one `renderReportCenter()` that updates overview counts, pressed states, table rows, empty state, and pagination.

- [ ] **Step 5: Render rows with safe real actions**

Create every cell with `createEl`/`textContent`. Action rules:

- completed: report detail, ``downloadPdf(report.report_pdf_url, `interview-report-${report.session_id}.pdf`)``, interview again;
- processing: report-processing URL, interview again;
- failed: requeue button, interview again.

The requeue handler is:

```javascript
async function requeueReport(report, button) {
  setBusy([button], true);
  try {
    await postJson(`/api/interviews/${report.session_id}/report/requeue`);
    showNotice(reportsStatus, "报告已重新进入队列", "success");
    await loadReports();
  } catch (error) {
    showNotice(reportsStatus, error.message, "danger");
  } finally {
    setBusy([button], false);
  }
}
```

`loadReports()` requests `/api/reports?limit=100`, assigns `viewState.items`, and renders. Never display `fallback_failed`; map the three canonical paths to stable labels and null to `Unavailable`.

- [ ] **Step 6: Add report-center CSS, build, and run focused tests**

Add `.reports-main`, `.reports-overview`, `.reports-layout`, `.report-filters`, `.report-filter`, `.report-table-card`, `.report-tools`, `.report-search`, `.report-select`, `.report-table`, `.report-actions`, `.pagination`, and `.empty-state`.

```powershell
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_report_api.py -q
```

Expected: focused tests pass.

- [ ] **Step 7: Commit report center**

```powershell
git add app/test0.html app/static/report-center.js app/static/prototype-source.css app/static/prototype.css tests/test_static_report_ui.py tests/test_report_api.py
git commit -m "feat: rebuild report center UI"
```

### Task 10: Align Help, Global Navigation, and Remove Every Demo Runtime Value

**Files:**
- Modify: `app/test-help.html`
- Modify: `app/test0.html`
- Modify: `app/test1.html`
- Modify: `app/test2.html`
- Modify: `app/test3.html`
- Modify: `app/test4.html`
- Modify: `app/static/prototype-source.css`
- Modify: `app/static/prototype.css` through build
- Test: `tests/test_static_report_ui.py`
- Test: `tests/test_page_routes.py`
- Test: `tests/test_reference_ui_artifact.py`

- [ ] **Step 1: Add failing global navigation and demo-cleanup tests**

For every production HTML page, extract the top navigation and assert `/prep`, `/reports`, and `/help` are real hrefs. Add:

```python
def test_production_pages_do_not_copy_reference_demo_runtime():
    combined = "\n".join(read_app_file(name) for name in (
        "test0.html", "test1.html", "test2.html", "test3.html", "test4.html",
    ))
    for forbidden in (
        "data-view-target=",
        "location.hash",
        "超过候选人",
        "如何设计一个高并发缓存系统",
        "2026-07-17 16:25",
        "fallback_failed",
        "本地数据库已连接",
    ):
        assert forbidden not in combined
```

Also assert the frozen reference still has the approved SHA-256 so cleanup never edits the artifact.

- [ ] **Step 2: Run cleanup tests and verify remaining failures**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_reference_ui_artifact.py -q
```

Expected: FAIL until all page chrome/help links and forbidden demo blocks are aligned.

- [ ] **Step 3: Rebuild the help page with the shared shell**

Keep the existing help content and `/help` route. Use the same `app-topbar`, brand, navigation, buttons, tokens, and desktop width as the other pages. Mark Help with `aria-current="page"`. Do not add hash routing or simulated toast.

- [ ] **Step 4: Audit all production pages against Appendix A**

Run repository searches and resolve every match in production files:

```powershell
rg -n "data-view-target|showView\(|location\.hash|fallback_failed|超过候选人|2026-07-17 16:25|如何设计一个高并发缓存系统" app -g "test*.html" -g "*.js"
rg -n "linear-gradient|border-radius:\s*(1[0-9]|[2-9][0-9])px" app/static/prototype-source.css
```

Expected after cleanup: no production hash-router/demo/gradient match and no card/control radius above 8px. Canonical `full_session_fallback` matches are valid and must remain.

- [ ] **Step 5: Remove obsolete CSS only after consumer search**

For each old selector proposed for deletion, run `rg` over `app` with `-g "test*.html" -g "*.js"`. Delete it only when there is no consumer. Rebuild CSS after cleanup.

- [ ] **Step 6: Run focused tests and commit navigation cleanup**

```powershell
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_reference_ui_artifact.py -q
git add app/test-help.html app/test0.html app/test1.html app/test2.html app/test3.html app/test4.html app/static/prototype-source.css app/static/prototype.css tests/test_static_report_ui.py tests/test_page_routes.py tests/test_reference_ui_artifact.py
git commit -m "style: align five-page navigation and demo cleanup"
```

Expected: all focused tests pass before commit.

### Task 11: Add Deterministic Desktop Browser Acceptance

**Files:**
- Create: `tests/browser/reference-ui.spec.js`
- Modify: `tests/browser_support_app.py`
- Test: `tests/test_static_report_ui.py`

- [ ] **Step 1: Add deterministic report seed endpoints**

In browser support, add a helper that creates and finishes a session directly through the fake store, then creates one of two states:

```python
@app.post("/test-support/reports/{status}")
def seed_report_state(status: str):
    if status not in {"processing", "failed"}:
        raise HTTPException(422, "unsupported report seed status")
    plan = browser_llm.generate_plan("Backend engineer", "Redis project")
    turn = store.start(
        plan,
        job_description="Backend engineer",
        resume_text="Redis project",
        job_tags=["Redis", "Backend"],
    )
    store.finish(turn.session_id)
    store.mark_report_processing(turn.session_id)
    job_store.jobs[turn.session_id] = {
        "job_id": f"browser-job-{turn.session_id}",
        "session_id": turn.session_id,
        "status": status,
        "replay_count": 0,
    }
    if status == "failed":
        store.fail_report(turn.session_id, "provider_timeout")
    return {"session_id": turn.session_id, "status": status}
```

Ensure the fake queue's Task 7 requeue method updates this seeded failed job. These endpoints exist only in `tests/browser_support_app.py`.

At the top of `reference-ui.spec.js`, skip this feature-specific suite for the existing mobile project:

```javascript
test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "desktop-only UI refactor");
});
```

Do not remove the existing mobile project because it protects earlier Local V1 behavior.

- [ ] **Step 2: Write the failing preparation/file browser test**

```javascript
test("reference preparation imports text files and renders a real plan", async ({ page }) => {
  await page.goto("/prep");
  await page.locator("#jobDescriptionFileInput").setInputFiles({
    name: "role.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("Backend engineer with Redis and MySQL"),
  });
  await page.locator("#resumeFileInput").setInputFiles({
    name: "resume.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("Built cache-aside recovery workflows"),
  });
  await expect(page.locator("#jobDescription")).toContainText("Backend engineer");
  await page.locator("#prepButton").click();
  await expect(page.locator("#planQuestions li")).toHaveCount(3);
  await expect(page.locator("#prepKnowledgeStatus")).toBeVisible();
});
```

- [ ] **Step 3: Write the failing focus/draft browser test**

Start an interview through the deterministic prep flow, enter text, reload, and assert restoration. Toggle focus and assert both side columns are hidden, then press Escape and assert they return. Submit the restored answer and assert its localStorage key is removed only after success.

Use `page.evaluate` to read the exact `interviewAnswerDraft:<session>:<question>` key; do not rely only on visible status text.

- [ ] **Step 4: Write the failing report-center control/requeue test**

Seed processing and failed reports through the test-support endpoints, then assert:

```javascript
await page.goto("/reports");
await page.locator('[data-report-status="failed"]').click();
await expect(page.locator("#reportsTableBody tr")).toHaveCount(1);
await page.locator("#reportSearch").fill("Backend");
await page.getByRole("button", { name: "重新生成" }).click();
await expect(page.locator("#reportsStatus")).toContainText("重新进入队列");
```

Also cover date filter, five-row pagination, completed PDF action visibility, and processing progress navigation.

- [ ] **Step 5: Add five-page screenshot and geometry coverage**

Call `page.setViewportSize({ width: 1440, height: 1000 })` and save screenshots for prep, interview, processing, detail, and reports. Add a reusable geometry assertion:

```javascript
async function expectDesktopGeometry(page) {
  const metrics = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    buttons: [...document.querySelectorAll("button")].map((button) => {
      const rect = button.getBoundingClientRect();
      return { width: rect.width, height: rect.height, text: button.textContent.trim() };
    }),
  }));
  expect(metrics.document).toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.buttons.every((button) => button.width > 0 && button.height > 0)).toBe(true);
}
```

Repeat geometry-only coverage at 1280x800. Do not add mobile assertions.

- [ ] **Step 6: Run the deterministic browser suite**

```powershell
npm run test:browser
```

Expected: existing Local V1 tests and all new reference UI tests pass; real-model opt-in tests remain skipped.

- [ ] **Step 7: Commit browser acceptance**

```powershell
git add tests/browser/reference-ui.spec.js tests/browser_support_app.py tests/test_static_report_ui.py
git commit -m "test: cover reference-driven desktop UI"
```

### Task 12: Run Release Gates and Record Acceptance

**Files:**
- Modify: `docs/reference-driven-five-page-ui-acceptance.md`

- [ ] **Step 1: Build CSS and check JavaScript syntax**

```powershell
npm run build:prototype-css
Get-ChildItem app/static -Filter '*.js' | ForEach-Object {
  node --check $_.FullName
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Expected: build and all syntax checks pass.

- [ ] **Step 2: Run focused Python regression**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_reference_ui_artifact.py tests/test_page_routes.py tests/test_static_report_ui.py tests/test_session_report_store.py tests/test_report_api.py tests/test_report_jobs.py tests/test_runtime_ports.py -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Run authenticated PostgreSQL gates against the existing configured service**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py tests/test_report_jobs.py -q -m "pg_runtime or pg_jobs"
```

Expected: JOIN parity and atomic requeue tests pass. If the configured DSN is unavailable, stop and report the missing gate; do not create a new container automatically.

- [ ] **Step 4: Run deterministic Playwright and inspect screenshots**

```powershell
npm run test:browser
```

Expected: all deterministic tests pass and real-model tests remain explicit opt-in skips. Inspect the 1440x1000 screenshots for blank sections, clipped controls, overlapping columns, unexpected demo values, and failed assets.

- [ ] **Step 5: Run the full Python suite**

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: full suite passes with only documented skips/warnings.

- [ ] **Step 6: Audit privacy, routes, and reference integrity**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_runtime_boundary_api.py tests/test_reference_ui_artifact.py -q
rg -n "data-view-target|showView\(|location\.hash|fallback_failed|超过候选人|2026-07-17 16:25" app -g "test*.html" -g "*.js"
git diff --check
```

Expected: tests pass; the search has no invalid production match; diff check is clean.

- [ ] **Step 7: Update acceptance with exact observed results**

Change status to `PASS` only when every required gate ran successfully. Record exact test counts, PostgreSQL gate count, Playwright count, reference SHA-256, screenshot viewports, and privacy result. State explicitly that no local model was downloaded or loaded.

- [ ] **Step 8: Commit final acceptance**

```powershell
git add docs/reference-driven-five-page-ui-acceptance.md
git commit -m "test: accept reference-driven five-page UI"
```

- [ ] **Step 9: Verify final branch state**

```powershell
git status --short
git log --oneline -12
```

Expected: no uncommitted tracked changes. Preserve unrelated untracked historical documentation.
