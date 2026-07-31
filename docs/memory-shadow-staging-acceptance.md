# Memory Shadow Isolated Staging Preflight Acceptance

Status: Task 2 isolated Staging preflight passed.

This reference record binds the Task 2 result to the committed preflight
implementation. It proves only that an isolated local Staging boundary can run
migration validation, durable aggregate metric checks, rollback cleanup and
safe configuration checks. It does not enable any Shadow or authorize a
production deployment.

## Pinned inputs

- Operational plan:
  `docs/superpowers/plans/2026-07-31-memory-operational-shadow-and-promotion-gates.md`,
  v1.1.
- Validated application RC revision: `a982b1f`.
- Task 1 evidence commit: `fb33894`.
- Validated Task 2 preflight implementation revision: `5280c9d`.
- Preflight schema: `memory-shadow-staging-preflight-v1`.
- Latest runtime migration: `principal_memory_v1`.

The later commit that stores this acceptance record is not the implementation
revision under test. The executable preflight, its tests and its runbook were
all read from the clean detached `5280c9d` worktree.

## Environment declaration

| Field | Accepted value |
|---|---|
| Environment category | isolated Staging |
| Isolation level | strict generated prefix |
| Co-resident local Staging | declared |
| Connection scope | dedicated |
| Worker/queue scope | dedicated and not started |
| Outbox/artifact owner scope | dedicated and not started |
| Observation profile | B |
| Declared window | 24 hours |
| Data category | synthetic |
| Retention declaration | 7 days |
| Backup/restore scope | isolated copy |
| Real Provider | not allowed and not called |
| Production observation | `NOT_RUN` |

The strict-prefix boundary is accepted only for the current local or
single-user Staging exercise. It is not equivalent to production instance or
database isolation and does not authorize production Shadow.

## Reproducibility evidence

- Clean detached worktree revision: `5280c9d`.
- Worktree modifications after validation: 0.
- Task 2 unit, document and dry-run contracts: 10 passed, 1 PostgreSQL test
  deselected, 0 failed.
- Focused configuration, migration, metric and Task 2 suite with PostgreSQL:
  42 passed, 0 failed.
- Compile check: passed. The temporary F-drive worktree required an alternate
  execution permission for Python cache creation; source compilation itself
  passed and the repository diff remained clean.
- Static dry-run: passed its static checks and correctly reported
  `LIVE_VALIDATION_NOT_RUN` instead of claiming operational PASS.
- Live executable preflight: passed with no gate codes.
- Test listeners on ports 4173, 8000 and 8011 after execution: 0.

The initial attempt to create a clean worktree under the long system temporary
path failed because historical report fixture paths exceeded the Windows path
limit. No validation ran in that incomplete directory. Repeating the checkout
at the short `F:\tmp\msp2` path produced a clean revision and the passing
results above.

## Database and migration evidence

- The target database passed the production-like-name rejection check.
- The irreversible database fingerprint matched the separately approved
  value; neither value nor the database name is stored in this record.
- The generated prefix passed the strict isolated-prefix validator; the exact
  prefix is not stored.
- All registered runtime migrations through `principal_memory_v1` were
  validated inside the isolated prefix.
- Runtime Session, workflow, artifact, Question Memory, deletion, durable
  metrics, Principal Memory consent and fact stores validated successfully.
- No production migration or business-prefix migration ran.

## Durable metrics evidence

The preflight wrote one synthetic aggregate-only metric event inside the
isolated prefix and proved:

- PostgreSQL aggregate store availability;
- `data_complete=true`;
- bounded 24-hour aggregation;
- hour rollup produces a durable bucket;
- retention cleanup API returns both minute and hour results;
- no subject identifier or free text is required by the metric event.

No Session, Principal, Fact, Question, Prompt, Answer, Resume, Excerpt, Source
Manifest or Provider payload was created or retained for this check.

## Rollback evidence

Cleanup ran in the executable preflight's `finally` boundary. It accepted only
relations inside the strict validated prefix and then recounted that boundary.

~~~text
rollback_verified=true
cleanup_residue=0
worker_leasing_started=false
configuration_changed=false
~~~

A separate post-run database query also reported zero remaining Task 2
validation relations. No migration definition, graph definition, immutable
artifact or terminal Principal Fact was changed as part of rollback.

## Configuration safety evidence

The effective runtime configuration resolved to:

~~~text
budget mode=disabled
budget shadow=false
budget enforcement=false
compression mode=disabled
Question Memory consumption=false
long-term mode=disabled
write shadow=false
read shadow=false
trusted-local Principal Memory API=false
~~~

The real configuration loader rejected `MEMORY_LONG_TERM_MODE=consume`; the
preflight did not downgrade it to a Shadow mode.

## Explicit limitations

This acceptance does not prove or authorize:

- a 24-hour observation actually occurred;
- the Profile B synthetic sample matrix is complete;
- Budget Shadow is enabled or accepted;
- Principal Memory Write or Read Shadow is enabled;
- worker leasing, proposal generation or would-select processing;
- real Provider behavior;
- backup restoration or operator tombstone replay;
- production migration, production Shadow or real candidate data;
- any long-term-memory consumption path.

Those gates remain assigned to later tasks in the operational plan.

## Acceptance result

~~~text
STAGING_PREFLIGHT=PASS
MIGRATION_SCOPE=ISOLATED
ROLLBACK_DRILL=PASS
ALL_MEMORY_SHADOWS=DISABLED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~
