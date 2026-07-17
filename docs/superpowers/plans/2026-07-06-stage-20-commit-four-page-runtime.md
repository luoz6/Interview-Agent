# Stage 20 Commit Four-Page Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the current uncommitted Stage 16-18 four-page runtime work into small, reviewable commits without mixing in IDE, cache, or unrelated local files.

**Architecture:** This stage does not add features. It audits the existing working tree, verifies that the already-created four-page frontend, build tooling, page routes, and report-task hardening are coherent, then commits them in dependency order. The final worktree may still contain unrelated local files such as `.idea/`, `.claude/`, historical untracked plans, and cache directories; those must remain uncommitted unless explicitly listed in this plan.

**Tech Stack:** FastAPI, vanilla ES modules, local Tailwind CSS build, pytest, Node syntax checks, Git.

---

## File Structure

Files to commit in this stage:

- Report task hardening:
  - `app/services/report_tasks.py`
  - `tests/test_report_tasks.py`
- Frontend build tooling:
  - `package.json`
  - `package-lock.json`
- Four-page runtime:
  - `app/main.py`
  - `app/test1.html`
  - `app/test2.html`
  - `app/test3.html`
  - `app/test4.html`
  - `app/static/api.js`
  - `app/static/shared-ui.js`
  - `app/static/prep.js`
  - `app/static/interview.js`
  - `app/static/report-processing.js`
  - `app/static/report-detail.js`
  - `app/static/prototype-source.css`
  - `app/static/prototype.css`
  - `app/static/index.html` deletion
  - `app/static/app.js` deletion
  - `app/static/styles.css` deletion
  - `tests/test_page_routes.py`
  - `tests/test_static_report_ui.py`
- Stage 16-18 handoff docs that directly describe the runtime work:
  - `docs/frontend-modification-guide.md`
  - `docs/stage-17-browser-smoke-test.md`
  - `docs/stage-18-acceptance-log.md`
  - `docs/superpowers/plans/2026-07-06-stage-16-four-page-frontend-runtime.md`
  - `docs/superpowers/plans/2026-07-06-stage-17-four-page-frontend-hardening.md`
  - `docs/superpowers/plans/2026-07-06-stage-18-browser-acceptance-and-polish.md`
  - `docs/superpowers/plans/2026-07-06-stage-20-commit-four-page-runtime.md`

Files to leave uncommitted in this stage:

- `.idea/**`
- `.claude/**`
- `.venv/**`
- `__pycache__/**`
- `tmp/**`
- `tmp-*.log`
- `tmp-*.pid`
- old historical untracked plans and specs not listed above, such as `docs/superpowers/plans/2026-07-01-*`, `docs/superpowers/plans/2026-07-02-*`, `docs/superpowers/plans/2026-07-03-*`, `docs/superpowers/plans/2026-07-04-*`, and `docs/superpowers/specs/**`

---

### Task 1: Baseline Audit And Verification

**Files:**
- Verify working tree only; do not modify files.

- [ ] **Step 1: Capture current worktree status**

Run:

```powershell
git status --short
git diff --stat
```

Expected:

- Modified files include `app/main.py`, `app/services/report_tasks.py`, and tests.
- Deleted old static files include `app/static/app.js`, `app/static/index.html`, and `app/static/styles.css`.
- Untracked four-page runtime files include `app/test1.html` through `app/test4.html` and `app/static/*.js`.
- Untracked noise may include `.idea/`, `.claude/`, cache directories, and historical plan/spec files.

- [ ] **Step 2: Verify no accidental secrets are present in files planned for commit**

Run:

```powershell
Select-String -Path `
  app/main.py, `
  app/services/report_tasks.py, `
  app/test1.html,app/test2.html,app/test3.html,app/test4.html, `
  app/static/api.js,app/static/shared-ui.js,app/static/prep.js,app/static/interview.js,app/static/report-processing.js,app/static/report-detail.js, `
  package.json,package-lock.json, `
  tests/test_page_routes.py,tests/test_static_report_ui.py,tests/test_report_tasks.py `
  -Pattern "sk-|OPENAI_API_KEY=.+|DEEPSEEK_API_KEY=.+|postgres:postgres@"
```

Expected:

- No real LLM API key appears.
- `postgres:postgres@` should not appear in runtime source or tests planned for frontend/report-task commits. If it appears only in already-committed docs such as `.env.example`, that is acceptable, but this command does not include those docs.

- [ ] **Step 3: Run baseline tests before committing**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_tasks.py tests/test_page_routes.py tests/test_static_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 4: Run JavaScript and CSS baseline checks**

Run each command separately in PowerShell:

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

- Each `node --check` command exits `0`.
- `npm run build:prototype-css` exits `0`.
- A Browserslist warning is acceptable.

---

### Task 2: Commit Report Task Failure Handling

**Files:**
- Modify: `app/services/report_tasks.py`
- Modify: `tests/test_report_tasks.py`

- [ ] **Step 1: Review report task diff**

Run:

```powershell
git diff -- app/services/report_tasks.py tests/test_report_tasks.py
```

Expected diff:

- `generate_report_for_session()` catches exceptions from `get_knowledge_store()`.
- On knowledge-store construction failure, it calls `store.fail_report(session_id, str(exc))` and returns.
- `tests/test_report_tasks.py` contains `test_generate_report_for_session_saves_failed_record_when_knowledge_store_is_unconfigured`.
- The test uses `monkeypatch.setattr(...)`, not direct global assignment.

- [ ] **Step 2: Run focused report task tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_tasks.py::test_generate_report_for_session_saves_failed_record_when_knowledge_store_is_unconfigured tests/test_report_tasks.py::test_generate_report_for_session_saves_failed_record_when_retrieval_is_unavailable -q
```

Expected: `2 passed`.

- [ ] **Step 3: Stage only report task files**

Run:

```powershell
git add app/services/report_tasks.py tests/test_report_tasks.py
git diff --cached -- app/services/report_tasks.py tests/test_report_tasks.py
```

Expected:

- Cached diff only includes the report task failure handling and its tests.

- [ ] **Step 4: Commit report task hardening**

Run:

```powershell
git commit -m "fix: record report failure when knowledge store is unavailable"
```

Expected: commit succeeds and includes exactly `app/services/report_tasks.py` and `tests/test_report_tasks.py`.

---

### Task 3: Commit Frontend Build Tooling

**Files:**
- Create: `package.json`
- Create: `package-lock.json`

- [ ] **Step 1: Review build tooling files**

Run:

```powershell
Get-Content -Encoding UTF8 -Path package.json
Test-Path package-lock.json
```

Expected `package.json` content:

```json
{
  "scripts": {
    "build:prototype-css": "tailwindcss -i ./app/static/prototype-source.css -o ./app/static/prototype.css --minify --content \"./app/test*.html\" \"./app/static/*.js\""
  },
  "devDependencies": {
    "tailwindcss": "^3.4.17"
  }
}
```

Expected: `Test-Path package-lock.json` prints `True`.

- [ ] **Step 2: Verify build command works**

Run:

```powershell
npm run build:prototype-css
```

Expected:

- Build exits `0`.
- `app/static/prototype.css` is produced or updated.
- A Browserslist warning is acceptable.

- [ ] **Step 3: Stage only build tooling files**

Run:

```powershell
git add package.json package-lock.json
git diff --cached -- package.json package-lock.json
```

Expected: cached diff creates only `package.json` and `package-lock.json`.

- [ ] **Step 4: Commit build tooling**

Run:

```powershell
git commit -m "build: add prototype css build tooling"
```

Expected: commit succeeds and includes exactly the two package files.

---

### Task 4: Commit Four-Page Frontend Runtime

**Files:**
- Modify: `app/main.py`
- Create: `app/test1.html`
- Create: `app/test2.html`
- Create: `app/test3.html`
- Create: `app/test4.html`
- Create: `app/static/api.js`
- Create: `app/static/shared-ui.js`
- Create: `app/static/prep.js`
- Create: `app/static/interview.js`
- Create: `app/static/report-processing.js`
- Create: `app/static/report-detail.js`
- Create: `app/static/prototype-source.css`
- Create: `app/static/prototype.css`
- Delete: `app/static/index.html`
- Delete: `app/static/app.js`
- Delete: `app/static/styles.css`
- Create: `tests/test_page_routes.py`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Review page routes**

Run:

```powershell
Get-Content -Encoding UTF8 -Path app/main.py
```

Expected:

- `/` and `/prep` return `app/test4.html`.
- `/interview` returns `app/test3.html`.
- `/report-processing` returns `app/test2.html`.
- `/report-detail` returns `app/test1.html`.
- `/static` remains mounted from `app/static`.

- [ ] **Step 2: Verify runtime HTML pages exist and use local CSS/modules**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_page_routes.py tests/test_static_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 3: Verify JS modules are syntactically valid**

Run each command separately:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: each command exits `0`.

- [ ] **Step 4: Rebuild prototype CSS**

Run:

```powershell
npm run build:prototype-css
```

Expected:

- Build exits `0`.
- `app/static/prototype.css` remains present.
- A Browserslist warning is acceptable.

- [ ] **Step 5: Stage only four-page runtime files**

Run:

```powershell
git add app/main.py `
  app/test1.html app/test2.html app/test3.html app/test4.html `
  app/static/api.js app/static/shared-ui.js app/static/prep.js app/static/interview.js app/static/report-processing.js app/static/report-detail.js `
  app/static/prototype-source.css app/static/prototype.css `
  tests/test_page_routes.py tests/test_static_report_ui.py
git add -u app/static/index.html app/static/app.js app/static/styles.css
git diff --cached --stat
```

Expected staged files:

- `app/main.py`
- `app/test1.html`
- `app/test2.html`
- `app/test3.html`
- `app/test4.html`
- seven static runtime files: `api.js`, `shared-ui.js`, `prep.js`, `interview.js`, `report-processing.js`, `report-detail.js`, `prototype-source.css`, `prototype.css`
- deleted old files: `app/static/index.html`, `app/static/app.js`, `app/static/styles.css`
- `tests/test_page_routes.py`
- `tests/test_static_report_ui.py`

Expected not staged:

- `.idea/**`
- `.claude/**`
- `docs/**`
- `package.json`
- `package-lock.json`
- `app/services/report_tasks.py`
- `tests/test_report_tasks.py`

- [ ] **Step 6: Inspect staged runtime diff**

Run:

```powershell
git diff --cached -- app/main.py tests/test_page_routes.py tests/test_static_report_ui.py
git diff --cached --name-status
```

Expected:

- Route tests match `app/main.py` routes.
- Static tests assert four runtime HTML pages, local CSS, JS modules, no CDN, old static asset deletion, reference `excerpt`, and empty/busy states.
- `git diff --cached --name-status` lists only files from Step 5 expected staged files.

- [ ] **Step 7: Commit four-page runtime**

Run:

```powershell
git commit -m "feat: switch to four page frontend runtime"
```

Expected: commit succeeds.

---

### Task 5: Commit Runtime Handoff Documentation

**Files:**
- Create: `docs/frontend-modification-guide.md`
- Create: `docs/stage-17-browser-smoke-test.md`
- Create: `docs/stage-18-acceptance-log.md`
- Create: `docs/superpowers/plans/2026-07-06-stage-16-four-page-frontend-runtime.md`
- Create: `docs/superpowers/plans/2026-07-06-stage-17-four-page-frontend-hardening.md`
- Create: `docs/superpowers/plans/2026-07-06-stage-18-browser-acceptance-and-polish.md`
- Create: `docs/superpowers/plans/2026-07-06-stage-20-commit-four-page-runtime.md`

- [ ] **Step 1: Review runtime handoff docs**

Run:

```powershell
Get-Content -Encoding UTF8 -Path docs/frontend-modification-guide.md -TotalCount 80
Get-Content -Encoding UTF8 -Path docs/stage-17-browser-smoke-test.md -TotalCount 80
Get-Content -Encoding UTF8 -Path docs/stage-18-acceptance-log.md -TotalCount 120
```

Expected:

- `frontend-modification-guide.md` describes the four-page frontend contract and API usage.
- `stage-17-browser-smoke-test.md` describes browser smoke procedure.
- `stage-18-acceptance-log.md` records automated and real LLM/RAG smoke results.

- [ ] **Step 2: Stage only Stage 16-18 runtime docs and this Stage 20 plan**

Run:

```powershell
git add docs/frontend-modification-guide.md `
  docs/stage-17-browser-smoke-test.md `
  docs/stage-18-acceptance-log.md `
  docs/superpowers/plans/2026-07-06-stage-16-four-page-frontend-runtime.md `
  docs/superpowers/plans/2026-07-06-stage-17-four-page-frontend-hardening.md `
  docs/superpowers/plans/2026-07-06-stage-18-browser-acceptance-and-polish.md `
  docs/superpowers/plans/2026-07-06-stage-20-commit-four-page-runtime.md
git diff --cached --name-status
```

Expected:

- Only the seven documentation files listed in this task are staged.
- Old historical plans/specs from July 1-4 are not staged.

- [ ] **Step 3: Commit runtime handoff docs**

Run:

```powershell
git commit -m "docs: archive four page runtime handoff"
```

Expected: commit succeeds.

---

### Task 6: Final Verification And Worktree Audit

**Files:**
- Verify repository after Tasks 1-5.

- [ ] **Step 1: Run focused runtime tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_tasks.py tests/test_page_routes.py tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run JS checks**

Run:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: all commands exit `0`.

- [ ] **Step 3: Rebuild CSS**

Run:

```powershell
npm run build:prototype-css
```

Expected: exits `0`; Browserslist warning is acceptable.

- [ ] **Step 4: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS. PostgreSQL tests may run when `POSTGRES_DSN` is configured; otherwise they skip.

- [ ] **Step 5: Confirm no intended Stage 16-20 files remain uncommitted**

Run:

```powershell
git status --short
```

Expected remaining untracked or modified files may include:

- `.idea/**`
- `.claude/**`
- old historical untracked plans/specs not listed in Task 5
- cache directories

Expected no remaining uncommitted files from:

- `app/main.py`
- `app/test1.html`
- `app/test2.html`
- `app/test3.html`
- `app/test4.html`
- `app/static/api.js`
- `app/static/shared-ui.js`
- `app/static/prep.js`
- `app/static/interview.js`
- `app/static/report-processing.js`
- `app/static/report-detail.js`
- `app/static/prototype-source.css`
- `app/static/prototype.css`
- `package.json`
- `package-lock.json`
- `app/services/report_tasks.py`
- `tests/test_page_routes.py`
- `tests/test_static_report_ui.py`
- `tests/test_report_tasks.py`
- `docs/frontend-modification-guide.md`
- `docs/stage-17-browser-smoke-test.md`
- `docs/stage-18-acceptance-log.md`

- [ ] **Step 6: If CSS rebuild changes `app/static/prototype.css`, commit it**

Run:

```powershell
git diff -- app/static/prototype.css
```

If there is no diff, do nothing.

If there is a diff, run:

```powershell
git add app/static/prototype.css
git commit -m "build: refresh prototype css"
```

Expected: only `app/static/prototype.css` is committed.

- [ ] **Step 7: Summarize final commits**

Run:

```powershell
git log --oneline -10
```

Expected recent commits include:

- `fix: record report failure when knowledge store is unavailable`
- `build: add prototype css build tooling`
- `feat: switch to four page frontend runtime`
- `docs: archive four page runtime handoff`
- optionally `build: refresh prototype css`

---

## Self-Review

**Spec coverage:** The plan covers the current uncommitted Stage 16-18 runtime work: report task hardening, frontend build tooling, four-page runtime files and route tests, old single-page asset deletion, runtime handoff docs, and final verification.

**Placeholder scan:** The plan contains no unresolved placeholders. Every command names exact files or explicitly states which intentionally untracked files remain out of scope.

**Type consistency:** File names match the current working tree: `report-processing.js`, `report-detail.js`, `prototype-source.css`, `tests/test_page_routes.py`, and the four `app/test*.html` runtime pages.
