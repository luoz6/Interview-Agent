# ADR: Interview Quality V1 frontend testing stack

- Status: Accepted for T18 implementation
- Date: 2026-08-05

## Decision

T18 will add Vitest, React Testing Library, `@testing-library/user-event`, jsdom, and
coverage support to the existing Vite/React frontend. T09 freezes the decision only;
it does not pre-install dependencies or duplicate T18 implementation.

Unit/component tests will cover plan editing and optimistic conflicts, report state
rendering, null/unscored/partial semantics, end-interview confirmation, reducer/pure
function branches, loading/error/retry states, keyboard interaction, and accessible
names. Playwright remains the browser-level authority for routing, SSE recovery,
full workflows, PDF/download integration, responsive behavior, and browser-only
accessibility checks.

Pure functions and reducers added for this program require branch coverage at least
90%. The target does not apply retroactively to the whole frontend. Coverage must be
reported by file and cannot be met by excluding the changed logic. Browser and unit
tests must not assert the same implementation detail when one contract-level assertion
is sufficient.

## Reproducibility and isolation

Dependencies and scripts are committed through `frontend/package.json` and its
lockfile; `npm ci` is the clean-install acceptance path on Windows and Ubuntu. Tests
use deterministic clocks/IDs and local fixtures. They do not make real Provider calls
or use real candidate data. Existing Playwright configuration remains intact unless
T18 demonstrates a required compatibility change.

## Failure policy

New unit/component failures block the owning engineering task. Browser-only failures
block browser acceptance. Missing infrastructure is not converted to a skip. T18 will
record exact commands and versions in the execution manifest.
