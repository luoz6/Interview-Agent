# Long-term Memory Production Execution Baseline

**Plan revision:** `v0.2-revised`
**Frozen at:** `2026-08-03T19:40:08+08:00`
**Purpose:** Task 0 evidence for the long-term-memory Hosted productization and bounded-promotion roadmap.

## Baseline result

```text
EXECUTION_BASELINE=FROZEN
HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED
PRODUCTION_DATA_USE_SPEC=NOT_APPROVED
PRODUCTION_BUDGET_SHADOW=NOT_RUN
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_MEMORY_C1A_SPEC=DRAFT
IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
```

Freezing this baseline does not authorize Hosted productization, production data processing, a production configuration change, or any Shadow/Canary window.

## Revisions and repository state

| Field | Frozen value |
|---|---|
| Execution inspection HEAD | `848699fd93ecfa8a55fe9e6b3f4bf7d06710e201` |
| Freeze source HEAD | `80936bbd73ce0199b33de5db93c13e1edcb81281` |
| Remote reference | `origin/master` |
| Remote HEAD at freeze | `6969efa119de0da33698f0de74f4fdeee502b375` |
| Freeze source divergence | behind `0`, ahead `5` |
| Deployed revision | `NOT_OBSERVED` |
| Historical Memory RC | `f5dce4206751775c1650a4fccbd5060625af523a` (`EVIDENCE_ONLY`) |
| Canonical plan | `docs/superpowers/plans/2026-08-03-long-term-memory-production-shadows-consumption-and-promotion.md` |
| Canonical plan SHA-256 | `DE0AFE41E815B8BEFBD56AE4ACDD5ED7E07540A0BAFFD3D06BDCA4E6542C3227` |

The execution inspection and freeze source differ because the user committed concurrent report-frontend work during the read-only baseline and regression run. The newer source HEAD was accepted without rewriting, resetting, or staging that work.

## Worktree ownership

The following paths were already modified by the user at execution inspection and were never staged or edited by this task:

```text
.hallmark/log.json
frontend/src/pages/ReportsPage.jsx
frontend/src/styles/reports-app.css
tests/browser/reports-ui.spec.js
```

`tests/browser/reference-ui.spec.js` appeared as concurrent user work before the freeze and was included in the user's `80936bb` commit. All five paths are outside Task 0 ownership.

Task 0 owns only:

```text
docs/superpowers/plans/2026-08-03-long-term-memory-production-shadows-consumption-and-promotion.md
docs/long-term-memory-production-execution-baseline.md
tests/test_long_term_memory_production_plan.py
tests/test_long_term_memory_execution_baseline.py
```

Safe handling rules remain: no broad staging, no reset, no restore, no clean, and no rewriting user commits.

## Safe configuration defaults

The repository example environment was inspected at freeze and still carries these disabled defaults:

```text
MEMORY_BUDGET_MODE=disabled
MEMORY_COMPRESSION_MODE=disabled
MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false
MEMORY_LONG_TERM_MODE=disabled
MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false
MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false
```

The current loader accepts only `disabled`, `write_shadow`, and `read_shadow`; unsupported consumption values remain fail-closed. The revised plan's `assist_c1a`, authenticated self-service, absolute-cap, and C1-A kill-switch keys are not added by Task 0. They remain gated behind the approved phase that owns their implementation.

No production environment, deployment, database, Provider, OIDC system, or approval system was contacted while freezing this baseline.

## Test baseline

Executed against freeze source HEAD plus the Task 0 plan/test changes:

```text
python -m pytest tests/test_long_term_memory_production_plan.py -q
13 passed in 0.18s

python -m pytest -q
1726 passed, 166 skipped, 1 warning in 28.82s
```

The warning is the existing Starlette `TestClient`/`httpx` deprecation warning. Skips include environment-dependent suites and are not interpreted as production evidence.

## Authorization boundary and next executable work

No external Productization ADR or Production Data-use approval record was supplied or discovered. Repository permission is not a substitute for Product, Privacy, Security, Legal, Fairness, Accessibility, Operations, or Change Owner approval.

The next safe work is limited to preparing reviewable Task 1 and Task 2 decision material. Per the revised roadmap, Tasks 4–34 must not be implemented until the Hosted Productization ADR is `GO` and the Production Data-use Spec is approved. Historical Principal Memory code remains present but does not change these production gates.

Task 0 exit is therefore satisfied while every production state remains unchanged.
