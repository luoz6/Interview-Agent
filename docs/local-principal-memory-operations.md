# Local V1 Principal Memory operations

This guide is for the trusted operator of a single-user Local V1 deployment. It
explains how to prove readiness, run bounded expiry cleanup, and replay a
protected deletion ledger. It does not authorize Hosted V2, multi-user
identity, real-candidate production processing, or use of memory in scoring,
evaluation, reports, review, hiring decisions, or Knowledge retrieval.

## Safety model

Local Principal Memory remains disabled by default. `local_consume` is the only
supported consumption value; the older `consume` value remains invalid. A
Local Consume preflight succeeds only when all of the following are true:

- the deployment scope is exactly `single-tenant-local`;
- the interview runtime is PostgreSQL;
- the latest runtime migration ID and checksum are present;
- Local Principal, trusted-local API, Write Shadow, Read Shadow, Local Consume,
  and trusted-local metrics gates are explicitly enabled;
- the explicit identity resolver returns a `trusted_local` identity;
- durable aggregate metrics report complete PostgreSQL data.

The preflight never changes configuration, runs migrations, creates facts, or
calls a model. Unknown database, identity, or metrics state is a failure.

## Configure Local Consume

Keep the committed values in `.env.example` disabled. Set the following only in
the private Local V1 runtime environment:

```text
INTERVIEW_RUNTIME_STORE=postgres
MEMORY_PRIVACY_DEPLOYMENT_ID=single-tenant-local
MEMORY_TRUSTED_LOCAL_METRICS_ENABLED=true
MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=true
MEMORY_LOCAL_PRINCIPAL_ENABLED=true
MEMORY_LOCAL_PRINCIPAL_ID=local-owner
MEMORY_LONG_TERM_MODE=local_consume
MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=true
MEMORY_LONG_TERM_READ_SHADOW_ENABLED=true
MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED=true
```

Do not derive `MEMORY_LOCAL_PRINCIPAL_ID` from a name, résumé, answer, request,
device, network value, embedding, or model output. Do not put a PostgreSQL DSN,
credential, Principal ID, session ID, fact value, prompt, or answer in an
evidence file or command output.

## Run the fail-closed preflight

From the repository root, use the deployment's approved Python runtime:

```powershell
python -m scripts.local_principal_memory preflight
if ($LASTEXITCODE -ne 0) { throw 'Local Principal Memory preflight failed' }
```

The command prints one canonical JSON object. A successful decision has
`local_consume_ready=true`, `state="ready"`, and an empty `gate_codes` list. Do
not enable interview traffic when the exit code is non-zero, a gate code is
present, or output is missing.

### Readiness truth table

| Mode or condition | State | Local Consume ready | Required action |
|---|---|---:|---|
| committed defaults | `disabled` | no | leave disabled or complete every prerequisite |
| Write or Read Shadow | `blocked` | no | do not consume; use the applicable Shadow plan |
| Local Consume with memory runtime | `blocked` | no | migrate to the approved PostgreSQL runtime |
| Local Consume with stale/unknown migration | `blocked` | no | run the migration-owned deployment procedure, then retry |
| Local Consume with incomplete durable metrics | `blocked` | no | restore PostgreSQL telemetry and prove completeness |
| Local Consume with identity unavailable | `blocked` | no | restore the explicit trusted-local resolver |
| every prerequisite verified | `ready` | yes | Local V1 follow-up assistance may run, still default-off in Git |

### Stable preflight gate codes

| Gate code | Meaning |
|---|---|
| `CONFIGURATION_INVALID` | an environment value is absent, conflicting, or unsupported |
| `LOCAL_CONSUME_MODE_DISABLED` | long-term memory is at its safe default |
| `LOCAL_CONSUME_MODE_MISMATCH` | another repository mode is selected |
| `LOCAL_PRINCIPAL_GATE_DISABLED` | the explicit single-user identity gate is off |
| `TRUSTED_LOCAL_API_GATE_DISABLED` | the trusted-local management boundary is off |
| `WRITE_SHADOW_GATE_DISABLED` | the prerequisite write gate is off |
| `READ_SHADOW_GATE_DISABLED` | the prerequisite read gate is off |
| `LOCAL_CONSUMPTION_GATE_DISABLED` | the final Local Consume gate is off |
| `DEPLOYMENT_SCOPE_MISMATCH` | scope is not exactly `single-tenant-local` |
| `POSTGRES_RUNTIME_REQUIRED` | the runtime is not PostgreSQL |
| `POSTGRES_MIGRATION_NOT_CURRENT` | the latest migration marker/checksum is not verified |
| `DURABLE_METRICS_GATE_DISABLED` | trusted-local aggregate metrics are not enabled |
| `DURABLE_METRICS_INCOMPLETE` | metrics are unavailable or using the process-local fallback |
| `TRUSTED_LOCAL_IDENTITY_UNAVAILABLE` | explicit trusted-local identity cannot be resolved |
| `EXECUTION_NOT_AUTHORIZED` | a mutating operator command omitted `--execute` |
| `TOMBSTONE_LEDGER_INVALID` | a ledger is absent, empty, oversized, malformed, or fails schema validation |
| `OPERATION_FAILED` | a maintenance operation failed without exposing private details |

## Run bounded expiry cleanup

Cleanup can expire active facts at their recorded expiry, expire stale
proposals after the configured proposal retention, delete expired 24-hour
exports, delete expired 15-minute safe references, roll up aggregate metrics,
and apply the existing metric retention policy. It never deletes an active,
unexpired fact and never prints fact values or identifiers.

First run preflight. Then execute one bounded batch:

```powershell
python -m scripts.local_principal_memory cleanup --batch-size 200 --execute
if ($LASTEXITCODE -ne 0) { throw 'Local Principal Memory cleanup failed' }
```

The allowed batch range is 1 through 10,000. The result contains counts only:
`facts_expired`, `exports_deleted`, `safe_refs_deleted`, `metric_rollups`,
`metric_minute_deleted`, and `metric_hour_deleted`. Re-run bounded batches until
all expiry counts are zero. A command without `--execute` must fail with
`EXECUTION_NOT_AUTHORIZED`.

## Replay protected deletion tombstones after restore

Store the deletion tombstone ledger outside application backups and Git. Limit
access to the operator because each JSONL record contains deletion locators.
Never paste ledger content into tickets, logs, evidence, or chat.

Before enabling deletion, configure an absolute private destination outside the
repository and database backup boundary:

```text
MEMORY_PRINCIPAL_TOMBSTONE_LEDGER_PATH=C:\private\principal-memory-tombstones.jsonl
```

When this value is configured, the deletion endpoint fails closed if the
completed tombstone cannot be durably appended. The failure response contains
only the `operator_ledger` stage, never the path or protected locators. To
capture an already-completed tombstone explicitly, run:

```powershell
python -m scripts.local_principal_memory capture-tombstone-ledger --ledger '<absolute-private-ledger-path>.jsonl' --execute
if ($LASTEXITCODE -ne 0) { throw 'Principal Memory tombstone capture failed' }
```

Capture is append-only and idempotent by tombstone reference, uses a single
durable write plus file flush, rejects repository-relative destinations,
restricts file permissions where the host permits it, and emits aggregate
counts only. Repeat the capture after every completed deletion cycle and copy
the ledger to the operator-controlled restore location before rotating or
restoring application backups.

After restoring an older application backup:

1. Keep interview traffic closed and Local Consume disabled.
2. Restore the application database into the intended Local V1 scope.
3. Make the captured protected JSONL ledger available on the operator host.
4. Run the repository preflight and resolve database or migration failures.
5. Execute replay:

```powershell
python -m scripts.local_principal_memory replay-tombstones --ledger '<protected-ledger-path>' --execute
if ($LASTEXITCODE -ne 0) { throw 'Principal Memory tombstone replay failed' }
```

Replay validates each SHA-256 integrity digest before deleting anything. It
purges facts, Consent, controls, exports, and safe-reference cache entries, then
marks the tombstone replayed. Output contains only validated/replayed totals and
aggregate deletion counts. A restore is incomplete until replay succeeds and a
second replay reaches zero business-data residue.

## Observe Local Consume

The only new metric code is `principal_local_consume`. Its dimensions are fixed
to operation, outcome, reason, Shadow flag, and consumption flag. Values are
aggregate counts and token estimates. Approved reason values are `eligible`,
`no_eligible_fact`, `state_changed`, `token_cap`,
`current_candidate_missing`, and `runtime_failure`.

The schema rejects principal, session, question, fact, artifact, source digest,
prompt, answer, summary, excerpt, credential, and DSN fields. Disabled mode
publishes zero Local Consume events. Metrics failures never change the
deterministic interview path.

## Emergency disable and recovery

Set these private runtime values back to their safe state and restart the local
application:

```text
MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED=false
MEMORY_LONG_TERM_MODE=disabled
MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false
MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false
MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false
MEMORY_LOCAL_PRINCIPAL_ENABLED=false
MEMORY_TRUSTED_LOCAL_METRICS_ENABLED=false
```

Do not delete migrations, tombstones, or retained facts as a rollback action.
Investigate only aggregate gate codes and counts. Re-enable one private runtime
only after preflight is green and the underlying failure has a verified fix.

## Fixed product boundaries

- Local Consume may assist durable follow-up generation only.
- Current-session evidence always wins.
- Memory cannot affect prep, scoring, evaluation, review, report generation,
  PDF output, hiring decisions, or Knowledge retrieval.
- `confirmed_skill` and `accessibility_preference` never enter the follow-up
  prompt; accessibility values remain UI/interaction settings only.
- Local V1 does not establish a Hosted V2 authentication, tenancy, consent, or
  production authorization boundary.
- Real-candidate production processing remains prohibited.
