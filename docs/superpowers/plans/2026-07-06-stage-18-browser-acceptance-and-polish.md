# Stage 18 Browser Acceptance and Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the four-page local frontend in a real browser, fix only the defects found during the smoke run, and record a v1.0 local acceptance result.

**Architecture:** Keep Stage 16/17 architecture unchanged: FastAPI serves four HTML pages and local static ES modules/CSS. Stage 18 is acceptance-driven: run the actual flow, capture concrete defects, add targeted regression tests for each defect, implement minimal fixes, and update the smoke-test record. Do not add new product areas.

**Tech Stack:** FastAPI, vanilla JavaScript ES modules, local Tailwind-generated CSS, pytest, Node syntax checks, manual browser testing, optional real LLM configuration already supported by the project.

---

## Scope

Prerequisite: Stage 17 must be completed before starting Stage 18. Stage 18 assumes the four pages are already routed, CDN references are removed, local `prototype.css` exists, old single-page static assets are deleted, and Stage 17 automated tests pass.

In scope:

| Area | Work |
| --- | --- |
| Browser flow | Manually verify `/` -> `/interview` -> `/report-processing` -> `/report-detail` |
| Error-state pages | Verify missing/bad `session_id` URLs do not crash the page |
| Targeted fixes | Fix browser-discovered JS/runtime/layout defects only |
| Real-LLM sample | Run one sample JD/resume through the full flow if API keys/model configuration are available |
| Acceptance record | Convert `docs/stage-17-browser-smoke-test.md` into an actual dated acceptance log |
| Regression tests | Add tests for every defect fixed in this stage |

Out of scope:

| Area | Reason |
| --- | --- |
| Login and accounts | Local single-user deployment remains the target |
| Knowledge-base management UI | Separate backend and product scope |
| Docker deployment | Do after browser acceptance is stable |
| Report center redesign | Existing API remains, but four-page flow is the v1.0 acceptance target |
| Broad visual redesign | Only fix obvious layout breakage discovered in the browser |

## Current State

This state is valid only after Stage 17 has been executed.

| Area | Status |
| --- | --- |
| Four page routes | Implemented |
| CDN removal | Implemented |
| Old single-page assets | Removed |
| Automated tests | Passing in Stage 17 |
| Browser full-flow run | Not yet executed |
| Real-LLM sample run | Not yet executed |
| Acceptance log | Checklist exists, but no actual result record |

## File Structure

| File | Responsibility |
| --- | --- |
| `docs/stage-18-acceptance-log.md` | Dated acceptance result, defect log, real-LLM result, final sign-off status |
| `docs/stage-17-browser-smoke-test.md` | Keep as reusable checklist; optionally link to Stage 18 result |
| `app/static/*.js` | Only modify scripts for concrete defects found during browser run |
| `app/test1.html` to `app/test4.html` | Only modify HTML for concrete DOM/layout defects found during browser run |
| `app/static/prototype-source.css` and `app/static/prototype.css` | Only modify CSS for concrete layout defects found during browser run |
| `tests/test_static_report_ui.py` | Add static regression tests for fixed defects |
| `tests/test_page_routes.py` | Add route regression tests only if route behavior changes |

---

### Task 1: Create Acceptance Log Template

**Files:**

| Action | Path |
| --- | --- |
| Create | `docs/stage-18-acceptance-log.md` |
| Modify | `docs/stage-17-browser-smoke-test.md` |

- [ ] **Step 1: Create `docs/stage-18-acceptance-log.md`**

```markdown
# Stage 18 Acceptance Log

Date: 2026-07-06

## Environment

| Item | Value |
| --- | --- |
| OS | Windows local development |
| Python | `F:\python3.11\python.exe` |
| Server command | `F:\python3.11\python.exe -m uvicorn app.main:app --reload` |
| Browser | Not recorded yet |
| Backend storage | Not recorded yet |
| LLM mode | Not recorded yet |

## Automated Verification

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe -m pytest tests/test_page_routes.py tests/test_static_report_ui.py -q` | Not run |
| `node --check app/static/api.js ... app/static/report-detail.js` | Not run |
| `npm run build:prototype-css` | Not run |
| `F:\python3.11\python.exe -m pytest -q` | Not run |

## Browser Flow Result

| Step | Result | Notes |
| --- | --- | --- |
| Open `/` | Not run |  |
| Generate interview plan | Not run |  |
| Save draft | Not run |  |
| Start interview | Not run |  |
| Submit streamed answer | Not run |  |
| Skip question | Not run |  |
| Finish interview | Not run |  |
| Report processing progress | Not run |  |
| Report detail rendering | Not run |  |
| PDF download | Not run |  |

## Error-State Result

| URL | Result | Notes |
| --- | --- | --- |
| `/interview` | Not run |  |
| `/report-processing` | Not run |  |
| `/report-detail` | Not run |  |
| `/report-detail?session_id=bad` | Not run |  |

## Real-LLM Result

| Item | Result | Notes |
| --- | --- | --- |
| Plan quality | Not run |  |
| Follow-up quality | Not run |  |
| Report quality | Not run |  |
| Evidence quality | Not run |  |
| PDF quality | Not run |  |

## Defect Log

| ID | Severity | Page/File | Symptom | Fix | Regression Test |
| --- | --- | --- | --- | --- | --- |

## Final Status

Not accepted yet.
```

- [ ] **Step 2: Link Stage 17 checklist to Stage 18 log**

Append to `docs/stage-17-browser-smoke-test.md`:

```markdown
## Acceptance Result

Stage 18 records the executed result in `docs/stage-18-acceptance-log.md`.
```

- [ ] **Step 3: Commit**

```powershell
git add docs/stage-18-acceptance-log.md docs/stage-17-browser-smoke-test.md
git commit -m "docs: add stage 18 browser acceptance log"
```

---

### Task 2: Run Baseline Automated Checks

**Files:**

| Action | Path |
| --- | --- |
| Verify | Full repository |
| Modify | `docs/stage-18-acceptance-log.md` |

- [ ] **Step 1: Run page and static checks**

PowerShell note: run each command as a separate command. Do not join these commands with `&&` when using Windows PowerShell 5.1.

```powershell
F:\python3.11\python.exe -m pytest tests/test_page_routes.py tests/test_static_report_ui.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run JS syntax checks**

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: all commands exit successfully.

- [ ] **Step 3: Run CSS build**

```powershell
npm run build:prototype-css
```

Expected: `app/static/prototype.css` builds successfully.

- [ ] **Step 4: Run full test suite**

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: all non-skipped tests pass.

- [ ] **Step 5: Update acceptance log**

In `docs/stage-18-acceptance-log.md`, replace each `Not run` automated verification result with the exact pass/skip count or failure message.

- [ ] **Step 6: Commit**

```powershell
git add docs/stage-18-acceptance-log.md app/static/prototype.css
git commit -m "test: record stage 18 automated baseline"
```

---

### Task 3: Run Browser Four-Page Smoke Test

**Files:**

| Action | Path |
| --- | --- |
| Modify | `docs/stage-18-acceptance-log.md` |
| Modify if defects found | `app/static/*.js`, `app/test*.html`, `app/static/prototype-source.css`, `tests/test_static_report_ui.py` |

- [ ] **Step 1: Start local server**

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --reload
```

Expected: server starts on `http://127.0.0.1:8000`.

- [ ] **Step 2: Execute the normal flow**

Use the sample JD and resume from `docs/stage-17-browser-smoke-test.md`.

Checklist:

| Step | Expected |
| --- | --- |
| Open `http://127.0.0.1:8000/` | Prep page loads without console errors |
| Click `生成面试计划` | `topicTags`, `planTitle`, and `planQuestions` update |
| Click `保存草稿` | Save status appears; `localStorage.interviewDraftId` exists |
| Click `开始面试` | Browser navigates to `/interview?session_id=...` |
| Submit answer | Streamed follow-up or next turn appears; side panel refreshes |
| Click `下一题` | Question navigation state changes |
| Click `结束面试` | Browser navigates to `/report-processing?session_id=...` |
| Wait for report | Progress page updates; report eventually becomes available |
| Open report detail | Summary, score, five dimensions, feedback, and evidence render |
| Click PDF download | PDF downloads and page remains visible |

- [ ] **Step 3: Execute error-state URLs**

Open:

```text
http://127.0.0.1:8000/interview
http://127.0.0.1:8000/report-processing
http://127.0.0.1:8000/report-detail
http://127.0.0.1:8000/report-detail?session_id=bad
```

Expected: each page shows a visible error and no uncaught JS exception blocks rendering.

- [ ] **Step 4: Record browser results**

Update `docs/stage-18-acceptance-log.md`:

| Field | Required Content |
| --- | --- |
| Browser | Browser name and version if visible |
| Browser Flow Result | `Pass`, `Fail`, or `Blocked` for every row |
| Error-State Result | `Pass`, `Fail`, or `Blocked` for every row |
| Defect Log | Add one row per observed defect |

- [ ] **Step 5: If defects are found, fix them one at a time**

Known Stage 16/17 evidence-rendering pitfall: backend `FeedbackReference` uses `excerpt`, not `content`. If report evidence cards render titles but empty body text, first inspect `app/static/report-detail.js` and ensure it reads `reference.excerpt` and does not read `reference.content`.

For each defect:

1. Add a targeted automated regression test if the defect can be checked statically or by TestClient.
2. Apply the minimal code fix.
3. Run the focused test.
4. Record the defect ID, fix, and regression test in the log.

Example test for a missing page hook:

```python
def test_report_detail_page_has_retry_notice_hook():
    html = read_app_file("test1.html")

    assert 'id="reportNotice"' in html
```

Example test for a JS behavior string:

```python
def test_report_detail_handles_processing_response():
    js = read_static_file("report-detail.js")

    assert "response.status === 202" in js
    assert "报告仍在生成中" in js
```

- [ ] **Step 6: Commit fixes and log updates**

```powershell
git add docs/stage-18-acceptance-log.md app/static app/test1.html app/test2.html app/test3.html app/test4.html tests
git commit -m "fix: polish four-page browser smoke defects"
```

If no defects are found:

```powershell
git add docs/stage-18-acceptance-log.md
git commit -m "docs: record four-page browser smoke pass"
```

---

### Task 4: Run One Real-LLM Sample If Configured

**Files:**

| Action | Path |
| --- | --- |
| Modify | `docs/stage-18-acceptance-log.md` |

- [ ] **Step 1: Check whether real LLM is configured**

Inspect the local environment or project configuration used by this repo. Do not print secrets.

Run a safe check:

```powershell
if ($env:OPENAI_API_KEY -or $env:DEEPSEEK_API_KEY) { "LLM key present" } else { "No LLM key found" }
```

Expected:

| Output | Action |
| --- | --- |
| `LLM key present` | Continue to Step 2 |
| `No LLM key found` | Mark Real-LLM Result as `Blocked: no local LLM key configured` |

- [ ] **Step 2: Run sample JD/resume flow with real LLM**

Use the sample JD and resume from `docs/stage-17-browser-smoke-test.md`.

Acceptance criteria:

| Item | Pass Condition |
| --- | --- |
| Plan quality | Questions match backend/FastAPI/Redis/MySQL/system design topics |
| Follow-up quality | Follow-up asks for deeper technical detail, not generic encouragement |
| Report quality | Summary is a coherent evaluation paragraph, not only tags |
| Evidence quality | References render if available; unavailable state is explicit if RAG misses |
| PDF quality | PDF downloads and contains readable Chinese labels |

- [ ] **Step 3: Record result**

Update `docs/stage-18-acceptance-log.md` Real-LLM Result table with `Pass`, `Fail`, or `Blocked`.

If the run exposes a deterministic code defect, add it to Defect Log and fix it under Task 3 rules. If it exposes model-quality issues only, record them but do not change code in this stage unless the fix is a small prompt/normalization bug.

- [ ] **Step 4: Commit**

```powershell
git add docs/stage-18-acceptance-log.md
git commit -m "docs: record real llm smoke result"
```

---

### Task 5: Final Verification and Acceptance Status

**Files:**

| Action | Path |
| --- | --- |
| Verify | Full repository |
| Modify | `docs/stage-18-acceptance-log.md` |

- [ ] **Step 1: Re-run automated checks after any fixes**

PowerShell note: the following commands are listed together for convenience, but on Windows PowerShell 5.1 they should be run one at a time. Do not combine them with `&&`.

```powershell
F:\python3.11\python.exe -m pytest tests/test_page_routes.py tests/test_static_report_ui.py -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
F:\python3.11\python.exe -m pytest -q
```

Expected: all commands pass.

- [ ] **Step 2: Set final acceptance status**

In `docs/stage-18-acceptance-log.md`, update:

```markdown
## Final Status

Accepted for local v1.0 demo.
```

Use this instead if there are unresolved issues:

```markdown
## Final Status

Not accepted. Blocking issues:

| ID | Reason |
| --- | --- |
```

- [ ] **Step 3: Commit final status**

```powershell
git add docs/stage-18-acceptance-log.md app/static/prototype.css
git commit -m "docs: finalize stage 18 acceptance status"
```

---

## Self-Review

Spec coverage:

| Requirement | Covered By |
| --- | --- |
| Browser full-flow smoke test | Task 3 |
| Error-state verification | Task 3 |
| Targeted fixes only | Task 3 defect workflow |
| Real-LLM sample | Task 4 |
| Acceptance record | Task 1, Task 2, Task 3, Task 4, Task 5 |
| No login/knowledge-base/Docker scope creep | Scope section |

Execution notes:

| Note | Detail |
| --- | --- |
| Manual browser testing is required | Automated tests cannot prove CSS visual quality, PDF browser download behavior, or real SSE UX |
| Real-LLM may be blocked | If no local API key is configured, record `Blocked`, do not fabricate a result |
| Commit policy | If the working tree has unrelated untracked files, stage only files touched by this stage |
