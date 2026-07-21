# Five-Page UI Systematic Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the completed five-page runtime UI into conformance with the approved precision-workbench color, elevation, navigation, motion, and accessibility contracts.

**Architecture:** Keep the independent HTML routes and existing ES modules. Consolidate visual behavior in `prototype-source.css`, add only the minimal DOM attributes and report scrollspy behavior required for accessibility, rebuild generated CSS, and preserve every API and storage contract.

**Tech Stack:** Static HTML, CSS through Tailwind 3 build layers, browser-native ES modules, Python static-contract tests, Playwright.

---

### Task 1: Lock the Target Visual Contracts

**Files:**
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Replace old palette assertions and add failing component contracts**

Assert the nine target color values, semantic subtle/focus/control tokens, `--motion-fast`, `--motion-state`, `.ui-card-elevated`, `.ui-button-danger`, and `@media (prefers-reduced-motion: reduce)`. Assert that the base `.ui-card` block does not contain `box-shadow`.

- [ ] **Step 2: Add failing navigation accessibility contracts**

Assert that `report-detail.js` uses `IntersectionObserver`, maintains `aria-current` with value `location`, and has a hash fallback. Assert that interview question rendering sets `aria-current="step"` only for the current question and that `finishInterviewButton` uses `.ui-button-danger`.

- [ ] **Step 3: Run focused tests and verify failure**

Run: `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q`
Expected: FAIL only on the newly specified target contracts.

- [ ] **Step 4: Commit the failing contracts**

```powershell
git add tests/test_static_report_ui.py
git commit -m "test: define systematic UI polish contracts"
```

### Task 2: Migrate Shared Tokens, Typography, Elevation, and Commands

**Files:**
- Modify: `app/static/prototype-source.css`
- Modify: `app/test3.html`
- Generate: `app/static/prototype.css`

- [ ] **Step 1: Install the approved token layer**

Set `--color-text: #172033`, `--color-muted: #647084`, `--color-page: #f4f6f9`, `--color-line: #dce2ea`, `--color-primary: #2457d6`, `--color-primary-hover: #1d47b3`, `--color-success: #17845e`, `--color-warning: #b7791f`, and `--color-danger: #c2413b`. Add semantic tokens for strong ink, control borders, selected/focus and semantic subtle backgrounds so repeated literal colors no longer encode component roles.

- [ ] **Step 2: Flatten base surfaces and add explicit elevation**

Remove the default shadow from `.ui-card`. Add `.ui-card-elevated` using the existing low-elevation shadow token, with no hover elevation for static cards.

- [ ] **Step 3: Add shared command and motion primitives**

Add `.ui-button-danger` with danger border/text and a subtle hover surface. Add stable 160ms interaction transitions to buttons and selectable navigation/filter controls using `--motion-fast`; use `--motion-state: 200ms` for state and progress changes. Change progress width transition from 240ms to the state token.

- [ ] **Step 4: Normalize page titles and body line-height**

Set all six page-heading `h1` selectors to 1.5rem/1.3 without changing compact panel typography. Normalize paragraph, input, table, and message body copy to the 1.55 body rhythm while preserving intentionally compact metadata.

- [ ] **Step 5: Apply the destructive class and rebuild CSS**

Add `ui-button-danger` to `#finishInterviewButton`, then run `npm run build:prototype-css`.

- [ ] **Step 6: Run focused static tests**

Run: `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q`
Expected: remaining failures are limited to navigation/scrollspy contracts in Task 3.

- [ ] **Step 7: Commit the shared visual system**

```powershell
git add app/static/prototype-source.css app/static/prototype.css app/test3.html
git commit -m "style: implement precision workbench visual tokens"
```

### Task 3: Unify Context Navigation and Report Scrollspy

**Files:**
- Modify: `app/static/prototype-source.css`
- Modify: `app/static/interview.js`
- Modify: `app/static/report-detail.js`
- Modify: `app/test1.html`
- Generate: `app/static/prototype.css`

- [ ] **Step 1: Add stable report navigation hooks**

Add `data-report-section-link` to each report-section anchor and set the overview link's initial `aria-current="location"` so the server-rendered page has one valid current location before JavaScript runs.

- [ ] **Step 2: Implement progressive report scrollspy**

Create `setupReportSectionNavigation()` in `report-detail.js`. Map links by hash, centralize selection in `setCurrentReportSection(id)`, observe report sections with `IntersectionObserver`, update the URL hash on anchor clicks, and fall back to `location.hash` or the overview section when observation is unavailable. Never hide or block report content.

- [ ] **Step 3: Expose current interview question semantics**

When rendering question-plan items, set `aria-current="step"` on exactly the current question and remove it from all other items. Keep the existing `.question-current` class for compatibility.

- [ ] **Step 4: Apply the three-pixel active spine**

Use a reserved transparent 3px left border for workflow steps, question-plan rows, and report-section links. Switch it to cobalt for `aria-current`. Keep report-center filters on their independent `aria-pressed` selected-control treatment.

- [ ] **Step 5: Rebuild and verify static/JS contracts**

Run: `npm run build:prototype-css`
Run: `Get-ChildItem app/static/*.js | ForEach-Object { node --check $_.FullName }`
Run: `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q`
Expected: PASS.

- [ ] **Step 6: Commit navigation behavior**

```powershell
git add app/test1.html app/static/interview.js app/static/report-detail.js app/static/prototype-source.css app/static/prototype.css
git commit -m "feat: add accessible contextual navigation state"
```

### Task 4: Complete Reduced-Motion Behavior

**Files:**
- Modify: `app/static/prototype-source.css`
- Generate: `app/static/prototype.css`
- Test: `tests/test_static_report_ui.py`

- [ ] **Step 1: Add the single page-shell reveal**

Add one restrained opacity/translate reveal to the main page shell using the state token and a non-bouncy easing curve. Do not animate messages, errors, or layout dimensions.

- [ ] **Step 2: Add reduced-motion overrides**

Under `@media (prefers-reduced-motion: reduce)`, disable page entry and transforms, set interaction and progress transition duration effectively to zero, and retain immediate visible state changes.

- [ ] **Step 3: Rebuild and run static tests**

Run: `npm run build:prototype-css`
Run: `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q`
Expected: PASS.

- [ ] **Step 4: Commit motion accessibility**

```powershell
git add app/static/prototype-source.css app/static/prototype.css tests/test_static_report_ui.py
git commit -m "style: honor reduced motion preferences"
```

### Task 5: Add Browser Acceptance for New Interaction Contracts

**Files:**
- Modify: `tests/browser/reference-ui.spec.js`

- [ ] **Step 1: Add report scrollspy acceptance**

Open `/report`, assert exactly one report navigation link has `aria-current="location"`, scroll to `#reportQuestionEvaluations`, and assert the current location moves without exposing private runtime fields.

- [ ] **Step 2: Add reduced-motion and geometry acceptance**

Emulate reduced motion, verify the page-shell animation resolves immediately, then retain the existing 1280x800 and 1440x1000 document-overflow assertions.

- [ ] **Step 3: Run deterministic browser tests**

Run: `npm run test:browser`
Expected: all deterministic flows pass; real-model opt-in tests remain skipped.

- [ ] **Step 4: Commit browser acceptance**

```powershell
git add tests/browser/reference-ui.spec.js
git commit -m "test: cover polished UI accessibility states"
```

### Task 6: Run Release Gates and Record Completion

**Files:**
- Modify: `docs/superpowers/plans/2026-07-21-five-page-ui-systematic-polish.md`

- [ ] **Step 1: Run build, syntax, static, and browser gates**

Run the CSS build, every JavaScript syntax check, `tests/test_static_report_ui.py`, and deterministic Playwright suite.

- [ ] **Step 2: Run full Python regression**

Run: `F:\python3.11\python.exe -m pytest -q`
Expected: the full deterministic suite passes with environment-dependent tests skipped.

- [ ] **Step 3: Inspect screenshots and geometry**

Review 1280x800 and 1440x1000 screenshots for all five production pages plus help. Confirm no document-level horizontal overflow, clipping, incoherent overlap, or unreadable status contrast.

- [ ] **Step 4: Run repository hygiene checks**

Run: `git diff --check`
Run: `git status --short`
Confirm unrelated untracked files remain untouched and no `.superpowers` visual-companion artifact is staged.

- [ ] **Step 5: Record exact gate results and commit**

Update this plan with a completion section containing command results and any intentional skips.

```powershell
git add docs/superpowers/plans/2026-07-21-five-page-ui-systematic-polish.md
git commit -m "docs: record five-page UI polish acceptance"
```
