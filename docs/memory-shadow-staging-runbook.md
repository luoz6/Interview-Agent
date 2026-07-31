# Memory Shadow Isolated Staging Preflight Runbook

This how-to guide prepares and validates an isolated Staging target for the
Memory Shadow program. It is written for the operator who owns the validation
window and the separate rollback owner who can stop the exercise.

Completing this guide does not enable Budget Shadow, Principal Memory Write
Shadow, Principal Memory Read Shadow, compression consumption, Question Memory
consumption, or production observation.

## Outcome

A successful execution prints an aggregate JSON record with these fields:

~~~text
passed=true
migration_scope=isolated
database_fingerprint_matches=true
durable_metrics_validated=true
rollback_verified=true
cleanup_residue=0
all_memory_shadows_disabled=true
long_term_memory_consumption=BLOCKED
production_observation=NOT_RUN
~~~

The next allowed step is preparation for Budget Shadow. Do not enable it as
part of this runbook.

## Preconditions

Before starting, confirm all of the following:

- the validated application RC is `a982b1f`;
- `docs/memory-validation-operational-evidence.json` reports the RC gate as
  passed;
- the target is an isolated Staging environment;
- the data category is `synthetic`, unless a separate internal-data approval
  exists;
- a named operator role and a different rollback-owner role are assigned;
- no real Provider call is permitted;
- the database target and rollback window are approved;
- no test service is listening on the repository test ports;
- the user-owned Reports UI and design files remain outside this operation.

Use role identifiers such as `memory-shadow-operator`; do not put a person's
name, email address, hostname, DSN, Session ID, Principal ID or Fact ID in the
evidence artifact.

## Isolation choices

Use the strongest available boundary in this order:

1. a separate PostgreSQL instance;
2. a separate database;
3. a strict generated table prefix in a co-resident local database.

The third choice is allowed only for the current local or single-user Staging
exercise. It is a limited substitute for instance isolation, not a production
equivalent. When `strict_prefix` is selected, all four declarations are
mandatory:

- `co_resident_isolated_staging=true`;
- a dedicated connection scope;
- a dedicated worker/queue scope;
- a dedicated outbox/artifact owner scope.

The preflight fails closed if any declaration is missing. The current Task 2
exercise starts no worker and creates no lease; the dedicated worker and owner
declarations reserve the boundary for later tasks.

## Step 1: keep all Memory modes disabled

The effective configuration must resolve to:

~~~text
MEMORY_BUDGET_MODE=disabled
MEMORY_COMPRESSION_MODE=disabled
MEMORY_LONG_TERM_MODE=disabled
MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false
MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false
MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false
all budget enforcement gates=false
all compression consumption gates=false
~~~

Do not set `MEMORY_LONG_TERM_MODE=consume`. The real configuration loader must
reject it; the preflight treats failure to prove that rejection as a blocker.

## Step 2: inspect and approve the database fingerprint

Place the approved Staging connection in the process environment without
printing it. Then run the read-only inspection:

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.memory_shadow_staging_preflight `
  --inspect-database-fingerprint
~~~

The command prints an irreversible 16-character fingerprint and confirms that
the DSN was redacted. It rejects production-like database names. Register the
fingerprint in the operator's approved environment record; do not paste the
DSN, database name or fingerprint into general logs, tickets, or the
repository acceptance document.

For the execute step, provide the separately approved fingerprint as a shell
value:

~~~powershell
$approvedFingerprint = '<approved irreversible fingerprint>'
~~~

If the inspected and approved values differ, stop. Do not run a migration to
discover whether the target was intended.

## Step 3: run the static dry-run

Profile A is for stable Staging traffic and requires at least 168 observation
hours. Profile B is for low-traffic or single-user Staging and relies on the
synthetic coverage matrix in later tasks. Task 2 uses Profile B with a 24-hour
declared window; it does not claim that the later sample matrix is complete.

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.memory_shadow_staging_preflight `
  --validated-rc-revision a982b1f `
  --observation-profile B `
  --observation-hours 24 `
  --data-category synthetic `
  --operator-role memory-shadow-operator `
  --rollback-owner-role memory-shadow-rollback-owner `
  --retention-days 7 `
  --isolation-level strict_prefix `
  --co-resident-isolated-staging `
  --dedicated-connection-scope `
  --dedicated-worker-scope `
  --dedicated-owner-scope `
  --deterministic-path-verified
~~~

Expected dry-run state:

~~~text
mode=DRY_RUN
static_checks_passed=true
passed=false
gate_codes=LIVE_VALIDATION_NOT_RUN
configuration_changed=false
all_memory_shadows_disabled=true
~~~

`passed=false` is intentional: a static dry-run cannot claim that migration,
metrics and cleanup executed.

## Step 4: execute the isolated validation

Run the same declaration with `--execute` and the approved fingerprint:

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.memory_shadow_staging_preflight `
  --execute `
  --validated-rc-revision a982b1f `
  --observation-profile B `
  --observation-hours 24 `
  --data-category synthetic `
  --operator-role memory-shadow-operator `
  --rollback-owner-role memory-shadow-rollback-owner `
  --retention-days 7 `
  --isolation-level strict_prefix `
  --co-resident-isolated-staging `
  --dedicated-connection-scope `
  --dedicated-worker-scope `
  --dedicated-owner-scope `
  --deterministic-path-verified `
  --expected-database-fingerprint $approvedFingerprint
~~~

The executable preflight performs these operations inside one generated,
strictly validated test prefix:

1. validates the current migration registry through `principal_memory_v1`;
2. validates all Memory runtime stores;
3. writes one synthetic aggregate metric event;
4. proves durable aggregation and hour rollup;
5. exercises the bounded retention API;
6. drops only relations inside the validated prefix;
7. recounts the prefix and requires residue `0`.

The operation does not start a worker, acquire a business lease, create a real
Session or Principal, call a model, or modify the public Knowledge Corpus.

## Step 5: evaluate the result

Accept Task 2 only when `passed=true` and `gate_codes` is empty. Also require:

- `database_fingerprint_matches=true`;
- `prefix_valid=true`;
- `migration_validated=true`;
- `durable_metrics_validated=true`;
- `rollback_verified=true`;
- `cleanup_residue=0`;
- `worker_leasing_started=false`;
- `all_memory_shadows_disabled=true`.

Do not accept a record that contains both a failure gate and a READY state.

## Rollback procedure

Task 2 does not turn a Shadow on, so rollback means restoring the disabled
configuration boundary and removing isolated validation relations:

1. stop the preflight process if it is still running;
2. keep all Memory modes and gates disabled;
3. do not start or resume worker leasing;
4. do not delete committed migration definitions, graph definitions or
   immutable artifacts;
5. do not restore a terminal Principal Fact to `active`;
6. rerun the static dry-run;
7. confirm the isolated relation residue is `0`.

The implementation performs prefix cleanup in a `finally` boundary, including
after migration or metric failures. If cleanup itself fails, stop the program,
retain only aggregate failure evidence, and use the same strict prefix
validator before any manual cleanup.

## Stable failure gates

| Gate family | Operator action |
|---|---|
| RC or baseline mismatch | Stop and rebuild evidence from the committed RC |
| Database fingerprint mismatch | Stop; verify the approved target out of band |
| Production-like database | Stop; do not migrate |
| Prefix or co-resident scope failure | Stop; provide a stronger isolated boundary |
| Any Memory mode enabled | Restore disabled configuration and rerun dry-run |
| Migration validation failure | Stop; preserve schema and diagnose the isolated prefix |
| Durable metrics failure | Stop; do not proceed to Budget Shadow |
| Rollback or cleanup failure | Stop; block all later Shadow stages |
| Real Provider authorized | Remove authorization; Task 2 permits no real Provider |

## Evidence handling

Store only the aggregate execution result, exact pass counts, RC revision,
environment category, profile, declared window, gate codes, and residue count.
Do not store the DSN, database name, fingerprint, generated prefix, Prompt,
Answer, Resume, Excerpt, Source Manifest, provider payload, or subject-level
identifier.

## Task 2 exit state

~~~text
STAGING_PREFLIGHT=PASS
MIGRATION_SCOPE=ISOLATED
ROLLBACK_DRILL=PASS
ALL_MEMORY_SHADOWS=DISABLED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~
