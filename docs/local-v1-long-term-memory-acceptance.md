# Local V1 long-term memory acceptance

**Status:** `LOCAL_MEMORY_ACCEPTANCE=PASS`

**Validated revision:** `a96b52e796984a9d52c63f4e0ac13c5d51ad73a0`

**Date:** 2026-08-04

**Overall program state:** `LOCAL_V1_LONG_TERM_MEMORY=COMPLETE`

This record covers Task 13 of the Local V1 completion plan. It proves the
repository implementation and Local V1 operational boundary on one clean
revision. Task 14 subsequently published and verified the RC candidate at
`473f9e092f06aac346716fbfd7c466f7b50ef3ed`. It does not authorize Hosted V2,
production Shadows, a real-provider canary, or real-candidate production
processing.

## Executed evidence

| Evidence | Result | Scope |
|---|---:|---|
| Full Python regression with reachable PostgreSQL | 2097 passed, 1 skipped | all repository tests; PostgreSQL persistence, migrations, concurrency, restart, failure and restore paths executed |
| Skipped Python test | NOT_RUN | `tests/test_real_llm_eval.py`; requires explicit real-provider authorization and credentials, neither required nor authorized here |
| Frontend production build | PASS | Vite production build; 4591 modules transformed |
| Full Playwright matrix | 78 passed, 38 skipped | configured desktop/mobile projects; conditional non-applicable combinations and real-provider smoke remained skipped |
| Memory Center browser cases | 8 passed | four cases on desktop Chromium and four on mobile Chromium, including keyboard, safe refs, destructive confirmation, responsive layout and reduced motion |
| Long-context quality and compression | 42 passed | budget, eligibility, gating, runner, validation, dataset and quality evaluation |
| Privacy, Prompt and Knowledge firewall | 14 passed | privacy, cross-Principal isolation, consumption isolation, Provider Context isolation and Knowledge firewall |
| Local operations related regression | 188 passed, 4 skipped | configuration, metrics, lifecycle, consumption, API and PostgreSQL contracts; live PostgreSQL tests were separately included in the full run |
| Task 12 live PostgreSQL focus | 8 passed | expiry cleanup, bounded concurrent cleanup, migration drift, durable exports, safe refs and tombstone import/replay |
| Frontend/browser service cleanup | PASS | no listeners on the three test ports after the browser runner exited |
| PostgreSQL test cleanup | PASS | 867 strictly named historical/current test tables removed in bounded transactions; final `test_*` relation residue is zero |
| Python compile check | PASS | `app`, `scripts` and `tests` compile successfully |
| Hosted disposition | PASS | public GitHub Issue #1 is `closed` with `state_reason=not_planned` |
| Main worktree protection | PASS | the same 14 pre-existing user-owned paths remain; no Local V1 task wrote to the main worktree |

The single Python warning is the existing Starlette/httpx deprecation warning.
It does not change test outcomes or Local Principal Memory behavior.

## Requirement-to-evidence table

`PASS` below means the stated evidence was executed on the validated revision,
the exact RC candidate, or is an externally re-read disposition.

| DoD | Requirement | Status | Evidence |
|---:|---|---|---|
| 1 | Hosted Issue #1 remains NO_GO and no production authorization is claimed | PASS | GitHub public API returned `closed/not_planned`; plan and this record prohibit production authorization |
| 2 | Local identity is explicit, stable, local-only and default-off | PASS | identity/config/runtime suites in full regression |
| 3 | Null identity remains default | PASS | identity and runtime tests; default preflight returns disabled |
| 4 | legacy `consume` remains rejected | PASS | memory config and Local Consume runtime tests |
| 5 | `local_consume` requires every dedicated gate | PASS | config matrix plus fail-closed operations preflight truth table |
| 6 | Consent is purpose-specific and checked at operation time | PASS | Consent, control and consume-race suites |
| 7 | temporary disable and delete remain distinct | PASS | control, API, lifecycle and deletion suites |
| 8 | session ignore affects the next context assembly | PASS | control and consume graph tests |
| 9 | only canonical taxonomy facts can be stored | PASS | contracts, lifecycle negatives and API tests |
| 10 | user declarations may activate; model proposals never auto-activate | PASS | lifecycle, proposal and API tests |
| 11 | exclusive corrections are atomic | PASS | memory and PostgreSQL concurrent correction tests |
| 12 | safe APIs reveal no internal locators | PASS | API, export, privacy and browser safe-ref tests |
| 13 | Read Shadow preserves Provider Context equality | PASS | prompt isolation and read-shadow matrix tests |
| 14 | Local Consume is bounded and follow-up-only | PASS | token/fact caps, graph integration and runtime tests |
| 15 | scoring, evidence, reports, PDFs, review, prep and Knowledge are isolated | PASS | 14-test privacy/firewall/isolation focus plus full regression |
| 16 | export is content-safe and expires | PASS | in-memory/PostgreSQL rights and API tests |
| 17 | deletion reaches zero residue or returns retryable failure | PASS | stage-by-stage failure injection and PostgreSQL deletion tests |
| 18 | tombstone replay prevents backup resurrection | PASS | protected-ledger integrity/import and live PostgreSQL restore replay |
| 19 | PostgreSQL upgrade and restart persistence pass | PASS | full PostgreSQL run, V10-to-V11 upgrade and restart suites |
| 20 | Memory Center keyboard and reduced-motion behavior pass | PASS | 8 executed desktop/mobile Memory Center cases |
| 21 | disabled mode performs zero Local Consume work | PASS | graph metric zero-activity and default-off config tests |
| 22 | telemetry is aggregate and content-free | PASS | metric schema denylist, output redaction and durable aggregate tests |
| 23 | every Task Auto-review passed | PASS | Tasks 0–13 passed; Task 14 candidate passed exact Python/PostgreSQL, build, browser, privacy, cleanup and remote verification gates |
| 24 | full repository regression passed | PASS | 2097 Python tests plus production build and 78 browser tests |
| 25 | main-worktree user changes remain untouched | PASS | exact 14-path comparison after all Task 13 execution |
| 26 | RC branch is clean, pushed and reviewable | PASS | `origin/codex/local-v1-long-term-memory` was read back at exact candidate `473f9e092f06aac346716fbfd7c466f7b50ef3ed` and has a GitHub review URL |

## Explicit non-claims

- `REAL_PROVIDER_EVALUATION=NOT_RUN`
- `PRODUCTION_SHADOW=NOT_AUTHORIZED`
- `HOSTED_V2=NO_GO_FOR_NOW`
- `REAL_CANDIDATE_PRODUCTION_PROCESSING=PROHIBITED`
- `SCORING_AND_REPORT_USE=PROHIBITED`

The real-provider test is not required to prove deterministic Local V1 memory
contracts and was not enabled merely to eliminate a skip. Provider use needs a
separate authorization and evidence process.

## Final promotion decision

```text
LOCAL_MEMORY_ACCEPTANCE=PASS
LOCAL_V1_LONG_TERM_MEMORY=COMPLETE
LOCAL_MEMORY_DEFAULT=DISABLED
LOCAL_MEMORY_WRITE=USER_CONTROLLED
LOCAL_MEMORY_READ_SHADOW=AVAILABLE
LOCAL_MEMORY_CONSUMPTION=AVAILABLE_BUT_DEFAULT_OFF
SCORING_AND_REPORT_USE=PROHIBITED
REAL_CANDIDATE_PRODUCTION_PROCESSING=PROHIBITED
NEXT_REQUIRED_TASK=NONE
```
