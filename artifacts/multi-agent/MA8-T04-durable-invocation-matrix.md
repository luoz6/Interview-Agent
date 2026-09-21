# MA8-T04 — Durable Invocation Matrix

## Guarantee

The durable invocation boundary guarantees:

```text
one stable identity = (execution_id, task_id, logical_attempt)
one accepted committed Artifact per logical attempt
an active lease blocks another worker's dispatch
an expired lease may be reclaimed only with a newer fencing version
a stale worker cannot commit or publish Scheduler state
a committed receipt is re-observed without provider reinvocation
```

It intentionally does not claim external-provider exactly-once invocation. A
process can crash after receiving a provider result and before committing its
durable receipt; recovery may invoke the provider again, while still accepting
only one committed logical effect.

## Crash Matrix

| Crash window | Durable state | Recovery behavior | Verified result |
| --- | --- | --- | --- |
| after `PREPARED` | `PREPARED`, task still pending | redispatch same logical attempt | provider invoked once by recovery |
| after lease/`RUNNING`, before provider | `RUNNING`, fence 1 | live lease blocks; expiry permits reclaim | fence 2 commits |
| after provider result, before receipt | `RUNNING`, no artifact ref | live lease blocks duplicate; expiry permits reinvocation | exactly one artifact ref is committed |
| after receipt, before Scheduler observation | `COMPLETED`, durable artifact ref | observe committed receipt | provider is not reinvoked |

## Concurrency Matrix

```text
live foreign lease -> LeaseBusy, no duplicate provider dispatch
expired lease -> reclaim with strictly higher fencing version
old worker commit after reclaim -> LeaseLost
old worker Scheduler projection after reclaim -> suppressed
same artifact completion replay -> ALREADY_COMMITTED
different artifact for committed identity -> LogicalEffectConflict
completed invocation reacquire -> LeaseLost
```

## PostgreSQL Hardening

MA8-T04 found and closed a production-adapter race. PostgreSQL acquisition now
uses one conditional update fenced by:

```text
non-terminal status
current fencing_version
lease absent or expired
```

If the conditional update loses, the adapter reloads the winner and returns
`LeaseBusy` or `LeaseLost`; it cannot return a lease it did not persist.
`mark_running`, `commit`, and `mark_failed` require an unexpired lease at the
SQL write boundary. `renew` checks the update row count, and a `COMPLETED`
invocation can never be acquired again.

Real PostgreSQL scope tests were not run because the externally issued scope
approval metadata is absent. No approval value was fabricated. The durable SQL
shape, schema, adapter surface, terminal guard, CAS predicates, expiry fences,
and lost-race behavior are covered by executable adapter contracts.

## Verification

```text
core durable invocation matrix: 29 passed
expanded invocation / dispatch / stale-worker matrix: 35 passed
all acceptance tests: 225 passed
architecture full suite: 125 passed
Python compileall: PASS
git diff --check: PASS (only LF/CRLF conversion warnings)
```

Architecture inventories remain valid:

```text
app_files_scanned = 513
test_files_scanned = 462
parse_error_count = 0
unresolved_relative_import_count = 0
cross_layer_violations = 0
final_architecture_definition_satisfied = true
```

## Acceptance

```text
all required crash windows have explicit recovery evidence: PASS
lease expiry blocks early recovery: PASS
expired invocation is reclaimed with a newer fence: PASS
stale worker cannot commit or project state: PASS
live lease prevents duplicate dispatch: PASS
completion replay suppresses provider reinvocation: PASS
single committed logical effect is enforced: PASS
PostgreSQL acquire is terminal-safe and CAS-fenced: PASS
PostgreSQL lifecycle writes are expiry-fenced: PASS
```

```text
MA8-T04 = PASS
NEXT_TASK = NOT_STARTED
```
