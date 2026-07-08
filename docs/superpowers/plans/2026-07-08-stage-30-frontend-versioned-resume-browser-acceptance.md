# Stage 30 Frontend Versioned Resume And Browser Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Stage 29 versioned HTTP resume contract into the browser interview page and record a real Local V1 browser acceptance pass.

**Architecture:** Keep the existing four-page static frontend, FastAPI routes, SSE answer streaming, polling report flow, PostgreSQL runtime, and report worker unchanged. Add a small frontend command-envelope layer that remembers `state_version` from the session snapshot, sends `expected_version` plus a caller-generated `command_id` on mutating interview commands, and recovers from `409` conflicts by reloading `GET /api/interviews/{session_id}`. Stage 30 does not add WebSocket, Redis checkpoints, frontend redesign, or new backend state fields.

**Tech Stack:** Python 3.11, FastAPI, pytest static contract tests, vanilla browser JavaScript, existing SSE helper, existing Local V1 runbook.

---

## File Structure

- Modify: `app/static/api.js`
  - Add an exported `HttpError` class so frontend callers can distinguish `409` version conflicts from generic network or validation failures.

- Modify: `app/static/interview.js`
  - Track `latestStateVersion` from snapshots.
  - Build versioned command payloads for `answer/stream`, `skip`, and `finish`.
  - Generate stable per-request `command_id` values in the browser.
  - Reload the session snapshot and preserve the user's typed answer when a `409` conflict occurs.
  - Stop rendering partial `InterviewTurn` payloads as if they were full session snapshots after SSE completion.

- Modify: `tests/test_static_report_ui.py`
  - Add static regression tests for the `HttpError` shape and the interview page's Stage 30 command envelope.

- Modify: `docs/local-v1-runbook.md`
  - Add a Stage 30 verification note for frontend versioned commands and conflict recovery.

- Create: `docs/stage-30-browser-versioned-resume-acceptance.md`
  - Manual browser acceptance checklist and result log for the versioned resume behavior.

- Modify: `tests/test_local_v1_docs.py`
  - Assert the runbook and acceptance log describe Stage 30.

---

### Task 1: Preserve HTTP Status In Frontend API Errors

**Files:**
- Modify: `app/static/api.js`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write the failing static contract test**

Append to `tests/test_static_report_ui.py`:

```python
def test_api_js_exports_http_error_with_status_and_body():
    js = read_static_file("api.js")

    assert "export class HttpError extends Error" in js
    assert "this.status = status" in js
    assert "this.body = body" in js
    assert "throw new HttpError(" in js
    assert "response.status" in js
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_api_js_exports_http_error_with_status_and_body -q
```

Expected: FAIL because `api.js` currently throws plain `Error` instances without preserving `response.status`.

- [ ] **Step 3: Add the minimal HTTP error type**

Modify `app/static/api.js` so the top of the file contains:

```javascript
export class HttpError extends Error {
  constructor(message, { status, body } = {}) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.body = body || {};
  }
}

export function getSessionId() {
  return new URLSearchParams(window.location.search).get("session_id");
}
```

Then replace `parseJsonResponse(response)` with:

```javascript
export async function parseJsonResponse(response) {
  const body = await safeJson(response);
  if (!response.ok) {
    throw new HttpError(
      body.detail || response.statusText || `Request failed with ${response.status}`,
      { status: response.status, body },
    );
  }
  return body;
}
```

Replace the non-OK branch in `readSse(response, handlers)` with:

```javascript
  if (!response.ok) {
    const body = await safeJson(response);
    throw new HttpError(
      body.detail || response.statusText || `Request failed with ${response.status}`,
      { status: response.status, body },
    );
  }
```

Replace the non-OK branch in `downloadPdf(url, filename)` with:

```javascript
  if (!response.ok) {
    const body = await safeJson(response);
    throw new HttpError(
      body.detail || response.statusText || "PDF download failed",
      { status: response.status, body },
    );
  }
```

- [ ] **Step 4: Run the focused test and JS syntax check**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_api_js_exports_http_error_with_status_and_body -q
node --check app/static/api.js
```

Expected: PASS and `node --check` exits `0`.

- [ ] **Step 5: Commit**

```bash
git add app/static/api.js tests/test_static_report_ui.py
git commit -m "feat: expose http status to frontend callers"
```

---

### Task 2: Send Versioned Command Envelopes From The Interview Page

**Files:**
- Modify: `app/static/interview.js`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write the failing static contract test**

Append to `tests/test_static_report_ui.py`:

```python
def test_interview_page_sends_versioned_command_payloads():
    js = read_static_file("interview.js")

    assert "let latestStateVersion = null" in js
    assert "function rememberResumeMetadata(snapshot)" in js
    assert "function createCommandPayload(extra = {})" in js
    assert "expected_version" in js
    assert "command_id" in js
    assert "crypto.randomUUID" in js
    assert "JSON.stringify(createCommandPayload({ answer }))" in js
    assert "postJson(`/api/interviews/${sessionId}/skip`, createCommandPayload())" in js
    assert "postJson(`/api/interviews/${sessionId}/finish`, createCommandPayload())" in js
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_interview_page_sends_versioned_command_payloads -q
```

Expected: FAIL because `interview.js` currently submits unversioned `{ answer }`, `{}`, and `{}` payloads.

- [ ] **Step 3: Track resume metadata and build command payloads**

In `app/static/interview.js`, add this state near the existing constants:

```javascript
let latestStateVersion = null;
let commandSequence = 0;
```

Add these helpers after `hasSession()`:

```javascript
function rememberResumeMetadata(snapshot) {
  if (snapshot && Number.isInteger(snapshot.state_version)) {
    latestStateVersion = snapshot.state_version;
  }
}

function createCommandPayload(extra = {}) {
  const payload = {
    ...extra,
    command_id: createCommandId(),
  };
  if (Number.isInteger(latestStateVersion)) {
    payload.expected_version = latestStateVersion;
  }
  return payload;
}

function createCommandId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  commandSequence += 1;
  return `browser-command-${Date.now()}-${commandSequence}`;
}
```

At the start of `renderSnapshot(snapshot)`, add:

```javascript
  rememberResumeMetadata(snapshot);
```

Replace the streamed answer request body in `submitAnswer(event)`:

```javascript
      body: JSON.stringify(createCommandPayload({ answer })),
```

Replace `skipQuestion()` with:

```javascript
async function skipQuestion() {
  if (!hasSession()) return;
  await postJson(`/api/interviews/${sessionId}/skip`, createCommandPayload());
  await loadSnapshot();
}
```

Replace `finishInterview()` with:

```javascript
async function finishInterview() {
  if (!hasSession()) return;
  await postJson(`/api/interviews/${sessionId}/finish`, createCommandPayload());
  window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
}
```

Keep the extra `await loadSnapshot()` after successful `skip`; do not render the `InterviewTurn` return value as a local shortcut. The full session snapshot is the page state contract because it includes messages, question states, tags, and resume metadata that `InterviewTurn` does not include.

Do not add a separate `sendAnswerButton` click handler in this task. The current runtime contract is that the send button remains inside `answerForm` and uses native form submission, while Enter handling already delegates to `answerForm.requestSubmit()` with a button-click fallback.

- [ ] **Step 4: Run the focused test and JS syntax check**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_interview_page_sends_versioned_command_payloads -q
node --check app/static/interview.js
```

Expected: PASS and `node --check` exits `0`.

- [ ] **Step 5: Commit**

```bash
git add app/static/interview.js tests/test_static_report_ui.py
git commit -m "feat: send versioned interview commands"
```

---

### Task 3: Recover From Version Conflicts And Avoid Partial Turn Rendering

**Files:**
- Modify: `app/static/interview.js`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write the failing static contract test**

Append to `tests/test_static_report_ui.py`:

```python
def test_interview_page_recovers_from_version_conflicts():
    js = read_static_file("interview.js")

    assert "function isVersionConflict(error)" in js
    assert "error.status === 409" in js
    assert "async function recoverFromVersionConflict()" in js
    assert "await loadSnapshot()" in js
    assert "会话状态已刷新" in js
    assert "if (isVersionConflict(error))" in js
    assert "answerInput.value = answer" in js


def test_interview_page_does_not_render_partial_turn_payload_after_sse_done():
    js = read_static_file("interview.js")

    assert "renderSnapshot(data)" not in js
    assert "SSE done payload is an InterviewTurn" in js
    assert "await loadSnapshot();" in js
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_interview_page_recovers_from_version_conflicts tests/test_static_report_ui.py::test_interview_page_does_not_render_partial_turn_payload_after_sse_done -q
```

Expected: FAIL because `interview.js` does not yet handle status-specific conflicts and still calls `renderSnapshot(data)` with the SSE `done` turn payload.

- [ ] **Step 3: Add conflict detection and recovery helper**

In `app/static/interview.js`, add these helpers after `createCommandId()`:

```javascript
function isVersionConflict(error) {
  return error && error.status === 409;
}

async function recoverFromVersionConflict() {
  await loadSnapshot();
  showNotice(interviewNotice, "会话状态已刷新，请检查最新题目后继续。", "warning");
}
```

This recovery intentionally does not auto-retry `skip` or `finish` after a conflict. It refreshes the snapshot, updates `latestStateVersion`, and asks the user to retry so the page does not accidentally apply an action to a changed question.

Replace the SSE `done` handler inside `submitAnswer(event)` with:

```javascript
      done() {
        // The SSE done payload is an InterviewTurn, not a full session snapshot.
      },
```

Keep the existing `await loadSnapshot();` immediately after `readSse(...)`; the full snapshot remains the source of truth for messages, question states, tags, and resume metadata.

Replace the `catch` block inside `submitAnswer(event)` with:

```javascript
  } catch (error) {
    answerInput.value = answer;
    if (isVersionConflict(error)) {
      await recoverFromVersionConflict();
      return;
    }
    throw error;
  } finally {
```

Replace `skipQuestion()` with:

```javascript
async function skipQuestion() {
  if (!hasSession()) return;
  try {
    await postJson(`/api/interviews/${sessionId}/skip`, createCommandPayload());
  } catch (error) {
    if (isVersionConflict(error)) {
      await recoverFromVersionConflict();
      return;
    }
    throw error;
  }
  await loadSnapshot();
}
```

Replace `finishInterview()` with:

```javascript
async function finishInterview() {
  if (!hasSession()) return;
  try {
    await postJson(`/api/interviews/${sessionId}/finish`, createCommandPayload());
  } catch (error) {
    if (isVersionConflict(error)) {
      await recoverFromVersionConflict();
      return;
    }
    throw error;
  }
  window.location.href = `/report-processing?session_id=${encodeURIComponent(sessionId)}`;
}
```

- [ ] **Step 4: Run the focused tests and JS syntax check**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_interview_page_recovers_from_version_conflicts tests/test_static_report_ui.py::test_interview_page_does_not_render_partial_turn_payload_after_sse_done tests/test_static_report_ui.py::test_interview_page_sends_versioned_command_payloads -q
node --check app/static/interview.js
```

Expected: PASS and `node --check` exits `0`.

- [ ] **Step 5: Commit**

```bash
git add app/static/interview.js tests/test_static_report_ui.py
git commit -m "feat: recover interview page from version conflicts"
```

---

### Task 4: Document Stage 30 Verification And Browser Acceptance

**Files:**
- Modify: `docs/local-v1-runbook.md`
- Create: `docs/stage-30-browser-versioned-resume-acceptance.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write the failing docs regression**

Append to `tests/test_local_v1_docs.py`:

```python
def test_docs_describe_stage_30_frontend_versioned_resume_acceptance():
    runbook = read_text("docs/local-v1-runbook.md")
    acceptance = read_text("docs/stage-30-browser-versioned-resume-acceptance.md")

    expected = "Stage 30 wires the browser interview page into the versioned HTTP resume contract"
    assert expected in runbook
    assert expected in acceptance
    assert "expected_version" in acceptance
    assert "command_id" in acceptance
    assert "409" in acceptance
    assert "GET /api/interviews/{session_id}" in acceptance
```

- [ ] **Step 2: Run the docs test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_30_frontend_versioned_resume_acceptance -q
```

Expected: FAIL because the Stage 30 runbook wording and acceptance log do not exist yet.

- [ ] **Step 3: Update the local runbook**

Add this paragraph under `## 1.1 Architecture Position` in `docs/local-v1-runbook.md` after the Stage 29 paragraph:

```markdown
Stage 30 wires the browser interview page into the versioned HTTP resume contract. The frontend should read `state_version` from `GET /api/interviews/{session_id}`, send `expected_version` plus a browser-generated `command_id` on answer, skip, and finish commands, and recover from `409` conflicts by reloading the session snapshot instead of leaving stale UI state on screen.
```

Add this checklist near the browser acceptance section:

```markdown
Stage 30 versioned resume checks:

1. Open `/prep`, create an interview, and land on `/interview?session_id=...`.
2. Confirm `GET /api/interviews/{session_id}` returns `state_version`.
3. Submit a streamed answer and confirm the request payload includes `expected_version` and `command_id`.
4. Refresh `/interview?session_id=...` and confirm the latest messages and question state are restored.
5. Continue the interview after refresh and confirm the next mutating request uses the refreshed `state_version`.
6. Simulate or trigger a stale request that returns `409`, then confirm the page reloads `GET /api/interviews/{session_id}` and keeps the user's typed answer available for retry. Do not expect the page to auto-retry `skip` or `finish`; the intended behavior is refresh plus user retry.
```

- [ ] **Step 4: Create the Stage 30 acceptance log**

Create `docs/stage-30-browser-versioned-resume-acceptance.md`:

```markdown
# Stage 30 Browser Versioned Resume Acceptance

Stage 30 wires the browser interview page into the versioned HTTP resume contract.

## Scope

- Page: `/interview?session_id=...`
- Resume handshake: `GET /api/interviews/{session_id}`
- Mutating commands: answer stream, skip, finish
- Required command fields: `expected_version`, `command_id`
- Required recovery behavior: on `409`, reload `GET /api/interviews/{session_id}` and keep the user's answer available for retry. Skip and finish conflicts are not auto-retried; the user retries after the refreshed state is visible.

## Manual Verification Checklist

- [ ] Start from `/prep` and create a new interview.
- [ ] Confirm the first interview snapshot includes `state_version`.
- [ ] Submit a streamed answer.
- [ ] Confirm the streamed answer request payload contains `expected_version` and `command_id`.
- [ ] Refresh the interview page.
- [ ] Confirm the conversation, current question, question states, and tags are restored.
- [ ] Submit or skip after refresh.
- [ ] Confirm the next request uses the refreshed `state_version`.
- [ ] Trigger a stale command or simulate a `409` response.
- [ ] Confirm the page reloads the latest snapshot.
- [ ] Confirm the user's unsent or failed answer remains in the textarea.
- [ ] Confirm stale skip or finish does not auto-retry and succeeds after one manual retry.
- [ ] Finish the interview and continue to report processing.
- [ ] Confirm report detail still renders and PDF download remains available.

## Result

- Date: 2026-07-08
- Environment: Local V1 Windows runtime
- Browser:
- Session ID:
- Result:
- Notes:
```

- [ ] **Step 5: Run the docs test**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_30_frontend_versioned_resume_acceptance -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/local-v1-runbook.md docs/stage-30-browser-versioned-resume-acceptance.md tests/test_local_v1_docs.py
git commit -m "docs: describe stage 30 browser resume acceptance"
```

---

### Task 5: Run Stage 30 Verification Sweep

**Files:**
- Test: `tests/test_static_report_ui.py`
- Test: `tests/test_local_v1_docs.py`
- Test: full repository

- [ ] **Step 1: Run focused Stage 30 tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend syntax and CSS checks**

Run:

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

- All `node --check` commands exit `0`.
- CSS build exits `0`.
- Browserslist update notices are acceptable and do not fail the build.

- [ ] **Step 3: Run backend regression tests that cover the Stage 29 contract**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_boundary_api.py tests/test_session_service.py tests/test_api.py tests/test_session_serialization.py tests/test_orchestrator_graph.py -q
```

Expected: PASS.

- [ ] **Step 4: Run full repository tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: PASS, with PostgreSQL-specific tests allowed to skip when fixture prerequisites are unavailable.

- [ ] **Step 5: Perform manual browser acceptance**

Start the FastAPI web process:

```powershell
if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY must be set before browser acceptance" }
if (-not $env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL="https://api.deepseek.com" }
if (-not $env:OPENAI_MODEL) { $env:OPENAI_MODEL="deepseek-chat" }
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start the report worker in a second PowerShell window:

```powershell
if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY must be set before browser acceptance" }
if (-not $env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL="https://api.deepseek.com" }
if (-not $env:OPENAI_MODEL) { $env:OPENAI_MODEL="deepseek-chat" }
F:\python3.11\python.exe -m app.services.report_worker
```

Open:

```text
http://127.0.0.1:8000/prep
```

Run the checklist in `docs/stage-30-browser-versioned-resume-acceptance.md` and record the observed values in the `Result` section.

- [ ] **Step 6: Commit the filled acceptance result**

```bash
git add docs/stage-30-browser-versioned-resume-acceptance.md
git commit -m "test: record stage 30 browser resume acceptance"
```

---

## Verification Sweep

After all tasks are complete, run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py tests/test_local_v1_docs.py tests/test_runtime_boundary_api.py tests/test_session_service.py tests/test_api.py tests/test_session_serialization.py tests/test_orchestrator_graph.py -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected:

- Focused Stage 30 tests pass.
- Stage 29 backend contract tests remain green.
- Static JS syntax remains valid.
- CSS build remains green.
- Full repository tests remain green.
- Manual browser acceptance is recorded in `docs/stage-30-browser-versioned-resume-acceptance.md`.

## Self-Review

- Spec coverage: The plan covers frontend use of `state_version`, `expected_version`, and `command_id`; version-conflict recovery; refresh/resume behavior; docs; and browser acceptance. It intentionally excludes WebSocket, Redis checkpoints, and new backend orchestration fields.
- Placeholder scan: No unresolved placeholder instructions or unspecified implementation steps remain.
- Type consistency: The frontend command fields use the existing backend names `expected_version` and `command_id`; the snapshot field uses `state_version`; conflict detection uses HTTP `409`; recovery uses the existing `GET /api/interviews/{session_id}` resume handshake.
