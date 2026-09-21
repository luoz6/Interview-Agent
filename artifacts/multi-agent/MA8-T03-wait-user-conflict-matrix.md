# MA8-T03 — WAIT_USER Conflict Matrix

## Scope

The closure matrix exercises the canonical Scheduler command boundary while an
execution is persisted in `WAIT_USER`. It distinguishes the two revision fences:

```text
stale:
command.expected_revision == wait.issued_revision
command.expected_revision != canonical state.revision

wrong revision:
command.expected_revision != wait.issued_revision
```

This distinction proves both checks are active rather than treating every
revision failure as the same test setup.

## Matrix

| Case | Input | Disposition | Reason | State / durable effect |
| --- | --- | --- | --- | --- |
| duplicate | same command id and payload | `REPLAY` | `idempotent_replay` | unchanged; original enqueue only |
| duplicate conflict | same command id, different payload | `CONFLICT` | `command_payload_conflict` | unchanged; no second enqueue |
| stale | valid wait revision after canonical revision advances | `STALE` | `stale_revision` | waiting state unchanged; no enqueue |
| late | old task while a newer wait is active | `REJECT` | `late_task` | waiting state unchanged; no enqueue |
| late after consumption | new command for a consumed wait | `REJECT` | `no_active_wait` | completed command state unchanged; no enqueue |
| wrong wait | mismatched wait id | `REJECT` | `wrong_wait` | waiting state unchanged; no enqueue |
| wrong question | mismatched question id | `REJECT` | `wrong_question` | waiting state unchanged; no enqueue |
| wrong revision | revision does not match the wait fence | `STALE` | `stale_revision` | waiting state unchanged; no enqueue |

The valid control command is durably enqueued before the canonical state
transition. The exact duplicate is accepted only as a replay and never applies
the answer twice.

## Restart Evidence

Existing Scheduler graph contracts additionally prove that:

```text
WaitHandle survives a LangGraph checkpoint restart
invalid resume returns to the same WAIT_USER node
invalid resume does not create a durable command record
an accepted durable command is replayed by a fresh process
resume uses a fenced UserCommand rather than an unfenced answer API
```

No production change was required for MA8-T03; the existing domain classifier,
Scheduler application boundary, durable command port, and graph restart path
satisfied the explicit closure matrix.

## Verification

```text
explicit MA8-T03 matrix: 6 passed
WAIT_USER / command / checkpoint matrix: 39 passed
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
duplicate command is idempotent and applies at most once: PASS
same command id with different payload conflicts: PASS
stale canonical revision is fenced: PASS
late answer cannot enter the active or consumed wait: PASS
wrong wait is rejected: PASS
wrong question is rejected: PASS
wrong revision is stale: PASS
all rejection paths preserve state and suppress durable enqueue: PASS
restart preserves the same conflict semantics: PASS
```

```text
MA8-T03 = PASS
NEXT_TASK = NOT_STARTED
```
