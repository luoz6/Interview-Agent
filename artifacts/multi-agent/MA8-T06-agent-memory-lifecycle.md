# MA8-T06 — Agent Memory Lifecycle

## Lifecycle Matrix

| Required behavior | Executable evidence | Result |
| --- | --- | --- |
| write | every closed Agent namespace writes an `AgentMemoryRecord` through its scoped port | PASS |
| recall | every scoped port recalls exactly its own active record | PASS |
| isolation | a bound port rejects a foreign complete scope with `AgentMemoryAccessDenied` | PASS |
| expiry | records disappear from recall when `expires_at <= now`; invalid and overlong TTLs remain rejected | PASS |
| session deletion | the canonical `SessionDeletionWorker` purges every target-session namespace, retains other sessions, and tombstones recreation | PASS |

The dedicated lifecycle contract covers all five logical namespaces in the one
physical store:

```text
scheduler
knowledge
examiner
reviewer
report-coach
```

Ownership remains the complete immutable scope:

```text
deployment_id / principal_id / session_id / agent_id / memory_type
```

## Deletion Evidence

The matrix does not substitute a Protocol-shape check for behavior. It queues a
real in-memory deletion job and runs `SessionDeletionWorker` with the composed
Agent memory store contract. The completed job reports five deleted private
memory rows. Recall for the deleted session returns no records, all records for
the retained session remain visible, and a late write to the deleted session
fails closed.

The existing scheduler deletion contract additionally proves that Agent memory
is purged alongside scheduler execution state, A2A task history, invocation
ledger rows, artifact owner references, report history, and the business
session.

## Verification

```text
dedicated MA8 lifecycle matrix: 2 passed
MA6 memory + runtime composition + session deletion regression: 64 passed
all acceptance tests: 225 passed
architecture full suite: 125 passed
Python compileall (app + tests): PASS
```

Regenerated architecture evidence:

```text
app_files_scanned = 513
test_files_scanned = 463
parse_error_count = 0
unresolved_relative_import_count = 0
cross_layer_violations = 0
final_architecture_definition_satisfied = true
```

The reported Pydantic JSON-schema and Starlette/httpx warnings are pre-existing
dependency warnings and do not represent Agent memory lifecycle failures.

## Acceptance

```text
write: PASS
recall: PASS
isolation: PASS
expiry: PASS
session deletion: PASS
production implementation changes required: NONE
```

```text
MA8-T06 = PASS
NEXT_TASK = NOT_STARTED
```
