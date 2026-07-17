# Stage 41 Local V1 RC Acceptance

Date: 2026-07-13; revalidated 2026-07-15

Result: `PASS`

## Scope

This record covers the Local V1 release-closure work: reproducible Python and
Node commands, PostgreSQL/pgvector initialization, Redis/Celery verification,
deterministic desktop and mobile browser regression, fresh provider quality
smoke, and a fresh provider browser flow. No API keys or database passwords are
stored in this record.

## Environment

- Python: `3.11.3`
- Node.js: `22.21.0`
- PostgreSQL: local `interview` database with `vector` extension
- Browser: Playwright Chromium
- Provider: DeepSeek-compatible OpenAI API, model recorded in the saved Stage 40 manifest

## Green Checks

| Gate | Result |
| --- | --- |
| Python/Node preflight | `PASS` (`core` and authenticated `celery` profile) |
| Dependency lock and install | `PASS` (`npm ci`, `.venv` `pip check`, `requirements.lock.txt`) |
| PostgreSQL/pgvector targeted suite | `42 passed` |
| Full pytest with PostgreSQL DSN | `515 passed, 1 skipped` |
| Skipped test | `tests/test_real_llm_eval.py` is opt-in only; the fresh run below passed it |
| Database init/check | `PASS`; five runtime tables, vector extension, knowledge table |
| Redis data path | `PASS`; authenticated ping, read/write, TTL and delete |
| Celery round review | `PASS`; real worker consumed `round_closed` and persisted `completed` evaluation |
| Deterministic browser regression | `4 passed`; desktop and mobile Chromium |
| Fresh provider quality smoke | `1 passed`; two real evaluation cases |
| Fresh provider browser smoke | `1 passed`; Prep, four streamed answers, report detail and PDF |
| Stage 40 evidence rescore | `PASS`; `40/40`, ranking `1.0`, grounding `1.0`, max score delta `0`, fallback `0` |
| Stage 40 artifact audit | `PASS`; 163 whitelisted files, 579047 bytes, no sensitive hit |

Deterministic browser command:

```powershell
$env:STAGE41_PYTHON="python"
npm run test:browser
```

Fresh provider quality command:

```powershell
$env:RUN_REAL_LLM_EVAL="1"
python -m pytest tests/test_real_llm_eval.py -q
```

Fresh provider browser command requires an isolated PostgreSQL table prefix,
the report worker, and explicit `RUN_REAL_BROWSER_SMOKE=1`; see
`playwright.real.config.js` and `tests/browser/real-model-smoke.spec.js`.

## Red-Green Fixes

1. The original real browser smoke clicked Start while the Prep request was
   still clearing its busy state. The test now waits for the enabled control.
2. Completed question records with empty applicable dimensions or evidence were
   incorrectly reused. They are now rerun before final aggregation.
3. A provider English-only summary could violate the runtime Chinese report
   contract. Microbatch finalization now uses a deterministic Chinese summary
   based only on the record count and backend aggregate score when needed.
4. Celery workers now explicitly include the round-review task module; without
   this, a worker could consume and discard the task as unregistered.
5. JetBrains `.idea` metadata is ignored and all previously tracked IDE files
   were removed from the Git index without deleting local settings.
6. The deterministic browser fixture now reuses one module-level test LLM for
   both Prep and session execution instead of creating a throwaway instance.
7. Missing answer payloads still retain the rubric v2 compatibility cap, but
   now emit a warning with the question id so abnormal callers are observable.

Rubric v2 intentionally pools provider evidence excerpts and computes dimension
scores from backend-owned applicability and quality signals. A future move to
provider-preserved dimension partitions must introduce a new rubric version and
rerun the full Stage 40 real-model acceptance; it is not folded into this RC.

## Release Notes

- The default `INTERVIEW_EVENT_BACKEND=local` remains usable without Redis.
- Redis/Celery is an optional profile and is claimable only after both the
  preflight and persisted-event acceptance pass.
- `reports/stage40-acceptance/20260710T124843Z/` is the official artifact root.
  Exploratory `stage40-group*` and `stage40-smoke*` directories are ignored by
  `.gitignore`; they were not deleted because they may be user-owned evidence.
- P0/P1 defects: none open. No known P2 release blocker remains; all accepted
  release-closure fixes are covered by regression tests. No new product
  capability was added.
- A release tag was not created automatically; the repository contains
  pre-existing Stage 40 worktree changes that should be reviewed before a
  maintainer commits or tags the complete release.
