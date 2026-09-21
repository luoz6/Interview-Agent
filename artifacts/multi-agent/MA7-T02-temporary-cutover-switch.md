# MA7-T02 — Temporary Cutover Switch

## Implementation

The temporary cutover now has one neutral, immutable execution ownership
contract:

```text
ExecutionPathBinding
execution_id + orchestration_path (OLD | NEW) + bound_at
schema_version = execution-path-binding-v1
```

`ExecutionPathBindingPort.bind()` is atomic and insert-once:

```text
first claim             -> persisted
same execution/path     -> idempotent replay
same execution/new path -> ExecutionPathConflict (fail closed)
```

The memory adapter serializes claims with an `RLock`. The PostgreSQL adapter
uses `execution_id` as the primary key plus `INSERT ... ON CONFLICT DO NOTHING`
and reads the winning row before accepting a claim. The port intentionally has
no delete or rebind operation, so session deletion retains the path tombstone.

Runtime Composition owns one binding store and one `ExecutionPathRouter`.
Legacy interview creation, bootstrap, resume, and command submission claim
`OLD`; Scheduler composition claims `NEW`. A claim conflict occurs before the
other orchestration path can perform interview work.

## Durable Upgrade

Runtime schema v31 adds:

```text
<prefix>_execution_path_bindings
```

The migration backfills every existing session as `OLD`, so pre-cutover
WAIT_USER and active sessions remain on their saved runtime. New legacy
sessions claim `OLD` before creation. PostgreSQL uniqueness serializes
cross-process OLD/NEW races.

Historical v1-v30 migration checksums remain unchanged. V31 also formally
registers the previously introduced Agent invocation ledger relation, which
had not yet been included in a new migration manifest.

## Temporary Switch Boundary

`INTERVIEW_ORCHESTRATION_PATH` accepts only `OLD` or `NEW` and defaults to
`OLD`. The switch is consulted only for an unbound execution through
`ExecutionPathRouter.bind_new_execution()`; it cannot mutate an existing
binding.

No production entry is routed to Scheduler in this Task. Setting the switch to
`NEW` does not by itself replace the current production start path; that is
reserved for MA7-T03.

## Verification

```text
cutover/composition/schema/legacy focused suite: 82 passed
legacy workflow and session deletion regressions: 86 passed
PostgreSQL migration fake/runtime contracts: 44 passed
all contract tests: 1038 passed, 3 skipped
external PostgreSQL scope tests: 3 blocked by missing approval environment
architecture full suite: 122 passed
refreshed architecture gate subset: 32 passed
Python compileall: PASS
public imports: PASS
git diff --check: PASS (only existing LF/CRLF conversion warnings)
```

The blocked real PostgreSQL tests require the repository's external scope
approval fields (`approval_id`, approval receipt, target fingerprint,
allowlist, and expiry). All migration tests that do not require that external
approval passed.

Architecture inventories after refresh:

```text
app_files_scanned = 510
test_files_scanned = 459
parse_error_count = 0
top_level_symbols_scanned = 2914
triple_zero_modules = 3
triple_zero_symbols = 31
cross_layer_violations = 0
```

## Acceptance

```text
OLD first claim: PASS
NEW first claim: PASS
same-path replay idempotent: PASS
OLD -> NEW conflict fail closed: PASS
NEW -> OLD conflict fail closed: PASS
concurrent OLD/NEW claim has one winning path: PASS
existing sessions backfilled OLD: PASS
switch change cannot migrate an existing execution: PASS
legacy entry rejects NEW-owned execution: PASS
Scheduler composition rejects OLD-owned execution: PASS
binding survives session deletion by contract: PASS
production entry remains OLD: PASS
```

```text
MA7-T02 = PASS
NEXT_TASK = NOT_STARTED
```
