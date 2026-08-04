# Local V1 Long-term Memory Execution Baseline

**Frozen:** 2026-08-04
**Scope:** Local V1 single-user long-term memory only
**Execution branch:** `codex/local-v1-long-term-memory`

## Stable status

```text
LOCAL_MEMORY_BASELINE=FROZEN
HOSTED_V2=NO_GO_FOR_NOW
LOCAL_V1_LONG_TERM_MEMORY=IN_PROGRESS
LOCAL_MEMORY_DEFAULT=DISABLED
REAL_CANDIDATE_PRODUCTION_PROCESSING=PROHIBITED
```

GitHub Issue #1 was closed as `not_planned` with a public
`NO_GO_FOR_NOW` disposition. That disposition does not authorize production
processing and is not an external approval record.

## Revision binding

| Item | Value |
|---|---|
| Isolated worktree base revision | `a9982d54553a337cfd6858c737a146c8954eed84` |
| Branch | `codex/local-v1-long-term-memory` |
| `origin/master` at worktree creation | `6969efa119de0da33698f0de74f4fdeee502b375` |
| Base distance from `origin/master` | behind `0`, ahead `17` |
| Hosted review issue | GitHub Issue `#1`, closed `not_planned` |
| Deployed revision | `NOT_OBSERVED` |

The isolated worktree was clean before Task-owned plan and baseline files were
created.

## User-owned main-worktree paths

The following concurrent paths belong to the user and are outside this plan's
ownership. They must receive no reset, no restore, no clean, no staging, and no
commit from the Local V1 worktree:

```text
frontend/package-lock.json
frontend/package.json
frontend/src/App.jsx
frontend/src/pages/ReportProcessingPage.jsx
frontend/src/styles/report-processing-app.css
tests/browser/reference-ui.spec.js
tests/browser/reference-ui-geometry.js
tests/browser/report-processing-ui.spec.js
docs/superpowers/plans/2026-08-03-frontend-gsap-motion-optimization.md
docs/frontend-gsap-motion-v0.2-execution-evidence.md
frontend/eslint.config.js
frontend/src/components/RouteLoadBoundary.jsx
frontend/src/hooks/useReducedMotion.js
frontend/src/motion/
```

The Local V1 implementation may add files with different paths in its isolated
worktree. Any eventual integration conflict must be resolved without replacing
the user's versions.

## Test environment

The authoritative interpreter is:

```text
F:\python3.11\python.exe (Python 3.11.3)
```

The unqualified `python` command currently resolves to Python 3.8.3 and is not
a valid project test environment. Its collection failures are environment
errors, not a repository baseline.

The first Python 3.11 full run, including the new plan tests, produced:

```text
1821 passed, 166 skipped, 1 warning, 1 failed
```

The single failure proved that the old production-plan digest test hashed raw
checkout line endings. UTF-8/LF normalization yields the already approved
digest:

```text
DE0AFE41E815B8BEFBD56AE4ACDD5ED7E07540A0BAFFD3D06BDCA4E6542C3227
```

The baseline fix canonicalizes CRLF/CR to LF before hashing, matching the
decision-packet normalization contract. The post-fix full regression must be
green before Task 1 is committed. The verified post-fix result is:

```text
1827 passed, 166 skipped, 1 warning
```

The only expected warning is the existing Starlette `TestClient`/`httpx`
deprecation warning.

## Frozen boundaries

- all Local long-term-memory gates remain disabled by default;
- `MEMORY_LONG_TERM_MODE=consume` remains invalid;
- Hosted Tasks 3-34 remain unexecuted;
- no candidate, Principal, Session, fact, source, Prompt, answer, resume,
  report, Provider, DSN, secret, approval, or ticket data is stored here;
- every Local task requires an automatic review before the next task starts;
- only exact task paths may be staged and committed.

## Task 0 exit

```text
LOCAL_MEMORY_BASELINE=FROZEN
MAIN_USER_WORK=UNCHANGED
ISOLATED_WORKTREE=READY
```
