# Frontend Optimization Gate 0A Baseline

## Identity

- Frozen code baseline: `2f16aee` (`feat(frontend): preserve route loading and report motion baseline`)
- Parent baseline: `81ff57ce22842b2bc4fdb48274b4fa9952b29f0d`
- Branch: `codex/frontend-optimization-v031`
- Captured: 2026-08-05, America/New_York
- Plan: `C:\Users\admin\Downloads\2026-08-05-interview-agent-frontend-optimization-plan-v0.3.md`
- Plan revision: v0.3.1
- Plan SHA256: `5B85C27E1495F8337D7B67268D188ACFE8F9588D2DFFEA157CF8C04A94CCC198`
- Scope owner: Codex execution task for the user-requested frontend optimization plan

The protected commit contains one coherent candidate theme: React route lazy loading, route-load error recovery, ESLint flat configuration, GSAP report-progress motion, reduced-motion handling, browser contracts, and the prior execution evidence. No backend or unrelated user changes were mixed into the baseline commit.

## Environment

| Tool | Version / value |
| --- | --- |
| OS shell | Windows PowerShell |
| Node | `v22.21.0` |
| npm | `10.9.4` |
| Project Python | `F:\python3.11\python.exe` / Python 3.11, selected by `.python-version` |
| Default PATH Python | Python 3.8.3, incompatible with the current built-in generic syntax and not used for accepted browser evidence |
| Playwright | `1.61.1` |
| Vite | `6.4.3` |
| Git | `2.53.0.windows.3` |
| Browser | Playwright Chromium / Codex in-app Chromium for baseline captures |
| Browser backend | `tests.browser_support_app:app` on `127.0.0.1:8011` |
| Frontend target | Vite on `127.0.0.1:4173`, `VITE_API_TARGET=http://127.0.0.1:8011` |

The first browser-suite attempt inherited Python 3.8.3 and stopped before test execution while importing `tuple[...]`. The accepted run explicitly used `STAGE41_PYTHON=F:\python3.11\python.exe`; the environment failure is not counted as a product test failure or pass.

## Static check

Command:

```powershell
npm --prefix frontend run check
```

Result: exit 0, `0 errors / 4 warnings`.

| File | Warning count | Rule |
| --- | ---: | --- |
| `frontend/src/pages/InterviewPage.jsx` | 2 | `react-hooks/exhaustive-deps` |
| `frontend/src/pages/ReportDetailPage.jsx` | 1 | `react-hooks/exhaustive-deps` |
| `frontend/src/pages/ReportsPage.jsx` | 1 | `react-hooks/exhaustive-deps` |

These warnings are the frozen Phase 1 cleanup target. `--max-warnings 0` is not enabled until they are fixed.

## Production build and budgets

Command:

```powershell
npm run build:frontend
```

Result: exit 0; 4,601 modules transformed; build completed in 13.05 seconds.

### Main-entry budget

| Asset | Raw | Gzip | Frozen regression budget | Status |
| --- | ---: | ---: | ---: | --- |
| Main JS | 202.70 kB | 64.28 kB | 66 KiB gzip | within budget |
| Main CSS | 123.78 kB | 18.90 kB | 20 KiB gzip | within budget |

### Route and shared chunks

| Chunk | Raw | Gzip |
| --- | ---: | ---: |
| `StartPage` JS | 49.54 kB | 13.64 kB |
| `InterviewPage` JS | 41.50 kB | 12.08 kB |
| `ReportProcessingPage` JS | 110.12 kB | 40.56 kB |
| `ReportDetailPage` JS | 46.25 kB | 13.21 kB |
| `ReportsPage` JS | 26.01 kB | 7.35 kB |
| `HelpPage` JS | 27.79 kB | 8.27 kB |
| `InterviewPage` CSS | 31.60 kB | 4.63 kB |
| `ReportProcessingPage` CSS | 19.77 kB | 3.23 kB |
| `ReportDetailPage` CSS | 43.80 kB | 5.95 kB |
| `ReportsPage` CSS | 27.64 kB | 4.48 kB |
| `HelpPage` CSS | 15.47 kB | 2.69 kB |

The report-processing route owns the GSAP dependency and remains the largest route chunk. Browser coverage verifies that non-motion routes do not request the GSAP modules.

## Complete browser suite

Command:

```powershell
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm run test:browser
```

Result: exit 0 in 296.7 seconds.

| Total | Passed | Failed | Skipped |
| ---: | ---: | ---: | ---: |
| 138 | 93 | 0 | 45 |

### Skip audit

- 44 skips are the mobile project copies of tests intentionally owned by the desktop project because those specs run their own explicit viewport matrices: prep (1), help (3), interview (4), report processing (11), report detail (4), reports (5), and reference design acceptance (15), plus the mobile copy of the real-provider smoke (1).
- 1 additional skip is the desktop real-provider smoke. It requires `RUN_REAL_BROWSER_SMOKE=1` and real provider credentials, so it is intentionally excluded from the deterministic Local V1 baseline.
- Skipped tests are not counted as passed. Phase and Release Gates may retain provider-dependent skips only with an explicit reason; all newly added core PrepPlan, launch, reliability, navigation, and recovery tests must run with zero skips.

The accepted suite covers route chunk failure recovery, runtime Error Boundary behavior, reduced-motion behavior, report-processing GSAP retargeting/cleanup, non-motion route GSAP isolation, desktop/mobile flows, durable refresh recovery, duplicate command behavior, report retry states, PDF generation, and safe error rendering.

## Visual baseline assets

All captures are stored under `docs/baselines/frontend-optimization-2026-08-05/` and were taken against the frozen code baseline with the browser-support backend. The set records desktop `1440×900` and mobile `390×844` states for every formal product route, plus preparation empty state and report failure recovery.

| Route/state | Desktop | Mobile |
| --- | --- | --- |
| Preparation empty | `prep-empty-desktop-1440x900.png` | covered by plan-state mobile plus automated empty-state geometry |
| Preparation plan ready | `prep-plan-desktop-1440x900.png` | `prep-plan-mobile-390x844.png` |
| Interview running/recovered | `interview-running-desktop-1440x900.png` | `interview-running-mobile-390x844.png` |
| Report processing/retrieving | `report-processing-retrieving-desktop-1440x900.png` | `report-processing-retrieving-mobile-390x844.png` |
| Report processing/failed | `report-processing-failed-desktop-1440x900.png` | `report-processing-failed-mobile-390x844.png` |
| Report detail/complete | `report-detail-complete-desktop-1440x900.png` | `report-detail-complete-mobile-390x844.png` |
| Reports list | `reports-list-desktop-1440x900.png` | `reports-list-mobile-390x844.png` |
| Help guide | `help-guide-desktop-1440x900.png` | `help-guide-mobile-390x844.png` |

The captures are regression evidence, not approval that the current heavy workbench information architecture is the final target. The optimization plan intentionally simplifies several of these screens in later phases.

## Gate 0A review

| Requirement | Evidence | Verdict |
| --- | --- | --- |
| Candidate work is recoverable | Commit `2f16aee` on an isolated `codex/` branch | pass |
| Candidate scope is coherent | 15 protected paths, all route-loading/ESLint/GSAP/browser evidence | pass |
| Static check is reproducible | Exit 0, exact four-warning inventory | pass with frozen cleanup debt |
| Production build is reproducible | Exit 0, route chunks and gzip sizes recorded | pass |
| Browser suite is reproducible | Python 3.11 explicit; 93 passed, 0 failed, 45 audited skips | pass |
| Route-load fallback and Error Boundary | Browser suite coverage bound to the baseline | pass |
| Reduced motion and GSAP lifecycle | Dedicated browser tests pass | pass |
| Desktop/mobile visual baseline | Fifteen named PNG assets across six routes and key states | pass |
| Main bundle budget | JS 64.28 KiB gzip-equivalent output, CSS 18.90 kB gzip | pass |

Gate 0A is eligible to close after this evidence and the generated assets are committed and `git diff --check` is clean. Gate 0B remains required before PlanEditor, persistent stores, reliability UI, or practice-plan implementation begins.
