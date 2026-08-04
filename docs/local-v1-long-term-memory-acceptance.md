# Local V1 long-term memory acceptance

**Status:** `LOCAL_MEMORY_ACCEPTANCE=PASS`

**Validated revision:** `3d4dccbb38afcf9792f368b0a2ff4a3146f0d1be`

**Date:** 2026-08-04

**Overall program state:** `LOCAL_V1_LONG_TERM_MEMORY=COMPLETE`

This record supersedes the pre-review Task 13 record. It proves the reviewed
repository implementation and Local V1 operational boundary on one clean
revision. The hardened candidate was pushed and read back from
`origin/codex/local-v1-long-term-memory` at the exact validated revision. It
does not authorize Hosted V2, production Shadows, a real-provider canary, or
real-candidate production processing.

## Executed evidence

| Evidence | Result | Scope |
|---|---:|---|
| Full Python regression with reachable PostgreSQL | 2123 passed, 1 skipped | all repository tests; PostgreSQL persistence, migrations, concurrency, restart, failure and restore paths executed |
| Skipped Python test | NOT_RUN | `tests/test_real_llm_eval.py`; requires explicit real-provider authorization and credentials, neither required nor authorized here |
| Frontend production build | PASS | Vite production build; 4591 modules transformed |
| Full Playwright matrix | 86 passed, 38 skipped | configured desktop/mobile projects; conditional non-applicable combinations and real-provider smoke remained skipped |
| Memory Center browser cases | 16 passed | eight cases on desktop Chromium and eight on mobile Chromium, including confirm, edit, session ignore/restore, stale recovery, keyboard, safe refs, responsive layout and reduced motion |
| Long-context quality and compression | 14 tests + 3 deterministic cases passed | hard-invariant pass rate 100%; budget, eligibility, gating, runner, validation, dataset and quality evaluation |
| Privacy, Prompt and Knowledge firewall | 75 passed | privacy, cross-Principal isolation, consumption isolation, Provider Context isolation, prep, scoring, report/PDF and Knowledge firewall |
| Review-fix focused regression | 125 passed | trusted-local API, exact Safe Ref lifecycle, PostgreSQL races, deletion fencing, append-only tombstones, external ledger, complete export, session cleanup and Memory Center contracts |
| Frontend/browser service cleanup | PASS | no listeners on the three test ports after the browser runner exited |
| PostgreSQL test cleanup | PASS | the final exact full run created 381 isolated test tables; all were removed in bounded transactions and final `test_*` relation residue is zero |
| Python compile check | PASS | `app`, `scripts` and `tests` compile successfully |
| Hosted disposition | PASS | public GitHub Issue #1 is `closed` with `state_reason=not_planned` |
| Main worktree protection | PASS | the same 14 pre-existing user-owned paths remain; no Local V1 task wrote to the main worktree |

The single Python warning is the existing Starlette/httpx deprecation warning.
It does not change test outcomes or Local Principal Memory behavior.

## Causal evidence boundary clarification

Local V1 is a trusted-local, default-off experiment. Principal Memory may
influence follow-up generation only. Score and report modules have no direct
Principal Memory dependency. No claim is made that changed interview
trajectories are causally equivalent.

The validated source and runtime firewalls show direct dependency isolation;
they do not show that later candidate answers, scores, or reports would remain
identical after a different follow-up. `learning_goal` and
`target_role_family` may change that trajectory. This acceptance therefore is
not evidence of fairness, candidate safety, production readiness, or Hosted
C1-A equivalence, and it does not authorize real-candidate production use.

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
| 11 | exclusive and same-value corrections are atomic | PASS | memory and PostgreSQL concurrent correction/confirmation tests plus the active-identity unique index |
| 12 | safe APIs reveal no internal locators | PASS | API, export, privacy and browser safe-ref tests |
| 13 | Read Shadow preserves Provider Context equality | PASS | prompt isolation and read-shadow matrix tests |
| 14 | Local Consume is bounded and follow-up-only | PASS | token/fact caps, graph integration and runtime tests |
| 15 | scoring, evidence, reports, PDFs, review, prep and Knowledge have no direct Principal Memory dependency | PASS | 75-test privacy/firewall/isolation focus plus full regression; no causal-equivalence claim |
| 16 | export is content-safe, complete and expires | PASS | 101-item complete export plus in-memory/PostgreSQL rights and API tests |
| 17 | deletion reaches zero residue or returns retryable failure | PASS | in-memory/PostgreSQL delete-vs-write races, durable fence checks and stage-by-stage failure injection |
| 18 | tombstone replay prevents backup resurrection | PASS | multi-cycle append-only tombstones, production ledger capture, protected-ledger integrity/import and live PostgreSQL restore replay |
| 19 | PostgreSQL upgrade and restart persistence pass | PASS | full PostgreSQL run, V12 integrity migration and restart suites |
| 20 | Memory Center lifecycle, keyboard and reduced-motion behavior pass | PASS | 16 executed desktop/mobile Memory Center cases |
| 21 | disabled mode performs zero Local Consume work | PASS | graph metric zero-activity and default-off config tests |
| 22 | telemetry is aggregate and content-free | PASS | metric schema denylist, output redaction and durable aggregate tests |
| 23 | review-fix Auto-review passed | PASS | all nine review findings plus the newly found tombstone replay construction defect were fixed; focused, full, browser, privacy, cleanup and remote-verification gates passed |
| 24 | full repository regression passed | PASS | 2123 Python tests plus production build and 86 browser tests |
| 25 | main-worktree user changes remain untouched | PASS | exact 14-path comparison after all Task 13 execution |
| 26 | RC branch is clean, pushed and reviewable | PASS | `origin/codex/local-v1-long-term-memory` was read back at exact hardened candidate `3d4dccbb38afcf9792f368b0a2ff4a3146f0d1be` |

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
