# Reference-Driven Five-Page UI Acceptance

Status: `PASS`

Date: 2026-07-18

Reference SHA-256: `A4549DD6D1B0F37C4207338E1ABC33D00CD44453A7643FF2DF81F25F3D35E283`

## Gate Results

| Gate | Result | Observed result |
| --- | --- | --- |
| Reference artifact frozen | PASS | Frozen artifact hash matches the recorded SHA-256. |
| Five production pages migrated | PASS | Static contracts and deterministic browser flows cover preparation, interview, report processing, report detail, and report center. |
| Real controls and API bindings | PASS | Focused Python regression: `118 passed, 10 skipped`; browser flows exercise file import, focus mode, draft recovery, report filtering, download, and requeue. |
| Frontend build and syntax | PASS | Tailwind build completed; all 7 files under `app/static/*.js` passed `node --check`. |
| PostgreSQL report metadata/requeue | PASS | `34 passed` against the existing PostgreSQL container using the isolated `interview_reference_ui_test` database. No container was created. |
| Deterministic Playwright | PASS | `13 passed, 7 skipped`; real-model cases remained explicit opt-in skips and reference-only desktop cases were intentionally skipped by the mobile project. |
| Screenshot and geometry audit | PASS | Five nonempty screenshots were captured at the `1440x1000` viewport; geometry also passed at `1280x800`. Sampled images contained 50-117 colors with luminance ranges of 215-232. |
| Privacy, routes, and reference integrity | PASS | `38 passed`; the production demo/hash-router search returned no matches; `git diff --check` passed. |
| Full Python regression | PASS | `660 passed, 53 skipped`; only the documented Starlette deprecation warning remained. |

## Full-Suite Resolution

The first full-suite run exposed a stale Stage 43B documentation contract:

The former Local V1 documentation source-string gate has been retired; current recovery behavior is owned by the Recovery Browser suite and structured contracts.

The test still expected `PENDING_RECOVERY_ACCEPTANCE`, while the Stage 43B
acceptance document was already `PASS`. Commit `73a7031` aligned that
contract with the completed Stage 43B gate. The complete Python suite was then
rerun successfully with `660 passed, 53 skipped`.

## Browser Evidence

The deterministic suite exercised `1440x1000` and `1280x800` desktop
viewports. Full-page screenshots were generated for all five production pages:

- `prep-1440x1000.png`: 1440x1027, 81,500 bytes
- `interview-1440x1000.png`: 1440x1000, 69,647 bytes
- `processing-1440x1000.png`: 1440x1000, 81,354 bytes
- `detail-1440x1000.png`: 1440x2419, 177,897 bytes
- `reports-1440x1000.png`: 1440x1037, 102,489 bytes

The screenshots were checked for nonblank output and pixel variation. Browser
geometry assertions covered blank sections, overflow, clipped controls, and
overlapping desktop columns.

## Model and Infrastructure Constraints

No local embedding model or language model was downloaded or loaded during
these gates. The PostgreSQL gate reused the user's existing `postgres`
container and the isolated `interview_reference_ui_test` database. No new
PostgreSQL or Redis container was created or started.
