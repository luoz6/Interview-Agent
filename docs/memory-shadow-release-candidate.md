# Memory Shadow Release Candidate Record

> Historical release-candidate snapshot: the `memory-p1-zh-v3` corpus below is
> superseded by the active `memory-p1-zh-v4` RocketMQ corpus. No v4 production
> promotion is implied by this historical record.

Status: `MEMORY_SHADOW_RC=REPRODUCIBLE`.

This record is a Release Candidate preparation artifact. It is not a production
release, a Staging deployment authorization, a Shadow activation record, or a
long-term-memory consumption approval.

## Current identity

- Base revision: `9132cf3`.
- Validated RC revision: `a982b1f`.
- RC construction commits:
  `57ef9a0`, `d80150b`, and `a982b1f`.
- Candidate label: `memory-shadow-rc1` (reserved, not created).
- Latest runtime migration: `principal_memory_v1`.
- Knowledge corpus: `memory-p1-zh-v3`, 31 chunks.
- Foundation acceptance: passed before RC commit construction.
- Production observation: `NOT_RUN`.

## Proposed dependency-coherent commit groups

1. `memory_foundation_core`
   - structured config, context/question memory, deletion, migrations, durable
     metrics and central runtime integration;
   - must compile and pass the affected memory/runtime/PostgreSQL tests.
2. `knowledge_p1_coverage`
   - reviewed corpus additions, manifest, retrieval fixtures and coverage
     contracts;
   - must reproduce both the active 31-chunk corpus and historical 25-chunk
     Stage 44B1 baseline.
3. `principal_memory_foundation`
   - identity, consent, facts, proposals, lifecycle, deletion, read-shadow,
     privacy and firewall;
   - may be merged with `memory_foundation_core` if shared config/runtime types
     prevent a valid intermediate revision.
4. `memory_verification_tests`
   - quality, PostgreSQL, deletion replay, privacy and acceptance tooling/tests;
   - may be paired with the implementation commit whose contract it proves.
5. `frontend_contract_migration`
   - React application, API-only FastAPI boundary, browser runner, browser
     specs, static compatibility behavior and retired HTML deletion;
   - shared review required; full build/browser validation is mandatory.
6. `memory_verification_docs`
   - Spec, plans, runbooks, threat model, operational evidence and acceptance
     records.

## Excluded from the candidate

- `.hallmark/**`;
- `DESIGN.md`;
- prototype visual-system CSS;
- `.gitattributes`, whose only rule targets a prototype artifact outside the
  memory RC.
- the post-candidate `frontend/src/styles/reports-app.css` user asset and the
  corresponding newer worktree-only `ReportsPage.jsx` and Reports browser-spec
  revisions.

## Verification completed from the committed RC

~~~powershell
& 'F:\python3.11\python.exe' -m compileall -q app scripts tests
& 'F:\python3.11\python.exe' -m pytest -q
& 'F:\python3.11\python.exe' -m pytest -q -m pg_runtime <approved list>
npm.cmd run build:frontend
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
git diff --check
$env:OPERATIONAL_INPUT_REVISION='<validated revision>'
$env:EVIDENCE_HMAC_KEY_ID='<external key id>'
$env:EVIDENCE_HMAC_SECRET_B64='<external base64 secret>'
& 'F:\python3.11\python.exe' -m scripts.memory_validation_foundation_acceptance `
  --evidence reports/memory/operational-rc-evidence-v1.json
~~~

The validation ran in a clean detached worktree created from `a982b1f`, with
fresh root and frontend dependency installation completed before the final
regression. Aggregate results were:

- full Python: 1450 passed, 162 skipped, 0 failed;
- live PostgreSQL `pg_runtime`: 43 passed, 1569 deselected, 0 failed;
- frontend production build: passed, 4587 modules transformed;
- complete browser project: 54 passed, 22 configured skips, 0 failed;
- focused release/acceptance/document contracts: 62 passed, 0 failed;
- compile-all, whitespace/diff check and foundation acceptance: passed;
- remaining test listeners: 0;
- strictly validated isolated PostgreSQL relation residue: 0.

The evidence intentionally uses `validated_rc_revision=a982b1f`. The later
documentation-only evidence commit cannot be its own validation target and
must not replace this field with a self-referential hash. No evidence artifact
contains a DSN, database fingerprint, test prefix, Session/Principal/Fact
identifier, Prompt, Answer, Excerpt or Provider payload.

## Ownership result

The ownership and dependency review is complete:

- release preflight: passed with 264 classified paths and no blockers;
- isolated candidate compile: passed;
- isolated candidate focused suite: 359 passed, 12 skipped;
- isolated candidate frontend build: passed, 4587 modules;
- isolated candidate browser suite: 54 passed, 22 configured skips;
- nine user-owned design, Reports UI, and repository-hygiene paths remain
  excluded from the validated RC and were not staged by the evidence commit.

The correct Task 1 exit state is:

~~~text
MEMORY_SHADOW_RC=REPRODUCIBLE
FOUNDATION_ACCEPTANCE=PASS
STAGING_PREFLIGHT=NOT_RUN
ALL_MEMORY_SHADOWS=DISABLED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~
