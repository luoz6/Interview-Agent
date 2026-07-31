# Memory Validation and Long-Term Memory Foundation Acceptance

Status: repository implementation and isolated operational validation complete.
This record is a repository and isolated test-environment gate only. It does
not authorize production rollout, real candidate data processing, long-term
memory consumption, or real-provider calls.

## Pinned inputs

- Plan:
  docs/superpowers/plans/2026-07-30-memory-validation-and-long-term-memory-foundation.md
  v1.1.
- Spec: docs/interview-agent-memory-system-optimization-spec.md
  v1.1.1-draft.
- Previous repository result:
  READY_FOR_MEMORY_SYSTEM_SHADOW.
- Previous production observation: NOT_RUN.

## Worktree ownership baseline

The implementation started from a heavily dirty worktree containing the
previous memory-system implementation and unrelated user changes. The executor
must preserve all existing tracked, untracked, and staged work. In particular:

- app/test0.html through app/test4.html and app/test-help.html were already
  deleted and must not be recreated by this phase;
- broad staging, reset, checkout, clean, destructive migration, and production
  rollout are prohibited;
- task changes must be reviewed by explicit file ownership rather than by
  assuming a clean repository.

The acceptance record stores only file classes and aggregate test evidence. It
must not copy prompts, answers, JD/resume content, summaries, excerpts,
session/principal/fact IDs, artifact refs, credentials, or DSNs.

## Initial characterization

- Full Python excluding the obsolete static-HTML baseline:
  1339 passed, 158 skipped, one existing deprecation warning.
- Obsolete static-HTML contract: known failing baseline; Task 1 must migrate
  useful assertions to React, route, compatibility-source, and Playwright
  contracts without restoring the deleted HTML.
- Full browser baseline: one known reference-UI timeout while waiting for
  .report-actions; Task 2 must isolate and remove the state-sharing failure.
- Live PostgreSQL pg_runtime: NOT_RUN.
- Deletion tombstone replay: NOT_RUN.
- Durable aggregate memory metrics: IMPLEMENTED; live final regression pending.
- Long-context quality gate: deterministic gate PASS.
- Principal Memory: write/read-shadow foundation IMPLEMENTED; consumption BLOCKED.

## Required safe defaults

~~~text
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false
CONTEXT_COMPRESSION_INTERVIEW_ENABLED=false
MEMORY_BUDGET_MODE=disabled
MEMORY_COMPRESSION_MODE=disabled
MEMORY_LONG_TERM_MODE=disabled
MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false
MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false
MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false
~~~

## Final isolated validation evidence

- Validated RC revision: `a982b1f`.
- Evidence binding: committed RC in a clean detached worktree. A later
  documentation-only evidence commit is not a replacement validation target.
- Executed at: `2026-07-31T14:21:05+08:00` (Asia/Hong_Kong).
- Toolchain: Python 3.11.3, Node 22.21.0, PostgreSQL 16.13, Playwright Chromium.
- Focused memory, context, deletion, knowledge, and trace suite:
  359 passed, 12 skipped, 0 failed during RC construction; the final focused
  release/acceptance/document contract suite added 62 passed, 0 failed.
- Live PostgreSQL `pg_runtime` suite: 43 passed, 1569 deselected, with the
  selected runtime tests genuinely executed rather than skipped.
- Latest isolated migration: `principal_memory_v1`; 28 relations validated in
  an isolated prefix and cleanup verified.
- Full Python regression: 1450 passed, 162 skipped, 0 failed, with one existing
  Starlette/httpx deprecation warning.
- Frontend production build: passed; Vite transformed 4587 modules.
- Full browser regression: 54 passed, 22 project-configured skips,
  0 failed; scope was the complete desktop/mobile project suite.
- Compile-all and whitespace/diff checks: passed.
- Root and frontend clean dependency installation: passed.

## Memory-system acceptance evidence

- Deletion restore/replay: passed, including six injected fault boundaries and
  simulated restoration of an old backup followed by tombstone replay.
- Durable aggregate metrics: passed using the PostgreSQL aggregate store;
  concurrent atomic aggregation, replayable rollup, bounded retention, and
  complete-data reporting were verified.
- Knowledge P1 coverage: ready on corpus `memory-p1-zh-v3`, 31 chunks, manifest
  SHA-256
  `d68eaa532f58d711686b5dc94d606faf7b5bd4ff6a03e264f67be4c78707c1d3`.
- Long-context deterministic quality: passed across synthetic Chinese,
  English, and mixed-language 20-turn cases. Hard-invariant pass rate,
  atomic-fact recall, and unresolved-topic recall were 1.0; unsupported atomic
  claim rate was 0.0; route/conclusion conflicts were 0.
- Budget Shadow preparation: validate-only preflight passed without changing
  configuration or enabling Shadow.
- Principal Memory contracts, explicit identity, operation-time consent,
  PostgreSQL fact storage, write-only proposal processing, lifecycle/deletion,
  bounded read-shadow selection, and Prompt isolation: passed.
- Privacy, cross-principal isolation, artifact audit, and the firewall between
  Principal Memory and the public knowledge corpus: passed.

## Cleanup and safety boundary

- Test listeners remaining on ports 4173, 8000, and 8011: 0.
- Strictly validated isolated-test PostgreSQL relation residue: 0.
- No DSN, database fingerprint, exact test prefix, candidate content, Prompt,
  answer, excerpt, or principal/fact/session locator is stored in this record
  or its JSON evidence.
- Repository defaults remain disabled for budget enforcement, compression,
  Principal Memory write shadow, Principal Memory read shadow, and the
  trusted-local Principal Memory API.
- `MEMORY_LONG_TERM_MODE=consume` remains an explicitly rejected
  configuration.
- No real provider call, real candidate-data exercise, production migration,
  production rollout, or production observation was performed.
- No Budget, compression, Question Memory, Write Shadow or Read Shadow mode was
  enabled while producing this evidence.

The machine-readable aggregate evidence is stored in
`docs/memory-validation-operational-evidence.json`.

## Acceptance result

~~~text
MEMORY_SHADOW_RC=REPRODUCIBLE
FOUNDATION_ACCEPTANCE=PASS
READY_FOR_MEMORY_VALIDATION_SHADOW
LONG_TERM_MEMORY_WRITE_SHADOW_READY
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~
