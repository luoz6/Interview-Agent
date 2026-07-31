# Memory Shadow Release Candidate Record

Status: `TASK_0_READY_FOR_SCOPED_COMMIT`.

This record is a Release Candidate preparation artifact. It is not a production
release, a Staging deployment authorization, a Shadow activation record, or a
long-term-memory consumption approval.

## Current identity

- Base revision: `9132cf3`.
- Candidate revision: `TBD_AFTER_SCOPED_COMMITS`.
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
  corresponding newer worktree-only `ReportsPage.jsx` revision.

## Required verification after candidate construction

~~~powershell
& 'F:\python3.11\python.exe' -m compileall -q app scripts tests
& 'F:\python3.11\python.exe' -m pytest -q
& 'F:\python3.11\python.exe' -m pytest -q -m pg_runtime <approved list>
npm.cmd run build:frontend
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
git diff --check
& 'F:\python3.11\python.exe' -m scripts.memory_validation_foundation_acceptance
~~~

The RC revision and exact aggregate counts must then replace the current TBD
fields. No evidence artifact may contain a DSN, database fingerprint, test
prefix, Session/Principal/Fact identifier, Prompt, Answer, Excerpt or Provider
payload.

## Current blockers

- No scoped RC commit has been created yet.

The ownership and dependency review itself is complete:

- release preflight: passed with 264 classified paths and no blockers;
- isolated candidate compile: passed;
- isolated candidate focused suite: 359 passed, 12 skipped;
- isolated candidate frontend build: passed, 4587 modules;
- isolated candidate browser suite: 54 passed, 22 configured skips;
- seven user-owned design/repository-hygiene paths remain excluded.

Until these blockers are removed, the correct state is:

~~~text
MEMORY_SHADOW_RC=NOT_CREATED
STAGING_DEPLOYMENT=NOT_AUTHORIZED
ALL_MEMORY_SHADOWS=DISABLED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~
