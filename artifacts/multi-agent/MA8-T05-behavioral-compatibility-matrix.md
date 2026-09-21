# MA8-T05 — Behavioral Compatibility Matrix

## Baseline

This gate reruns the exact 23-module MA0-T04 characterization command with the
original `not pg_runtime` marker policy. It does not substitute broad test
counts for the frozen behavior evidence.

The current result is:

```text
298 passed
5 deselected
1 existing Starlette/httpx deprecation warning
```

The count matches the latest pre-closure MA7 compatibility run. The five
deselected tests are the original real-PostgreSQL characterization cases. They
remain protected by the external-scope approval guard; no approval metadata was
fabricated and no mock replaced them.

## Frozen MA0 Matrix

| # | Frozen behavior | Result |
| --- | --- | --- |
| 1 | state/version conflict | PASS |
| 2 | duplicate command handling | PASS |
| 3 | command-id semantics | PASS |
| 4 | expected-version semantics | PASS |
| 5 | WAIT_USER / interrupt behavior | PASS |
| 6 | late user answer rejection | PASS |
| 7 | generation lease | PASS |
| 8 | lease renewal / heartbeat | PASS |
| 9 | fencing / stale worker rejection | PASS |
| 10 | retry timer | PASS |
| 11 | JIT question identity | PASS |
| 12 | SSE reset / replay | PASS |
| 13 | report enqueue | PASS |
| 14 | report retry | PASS |
| 15 | session deletion | PASS |
| 16 | principal-memory deletion interaction | PASS |
| 17 | plan V2 compatibility | PASS |
| 18 | plan V3 compatibility | PASS |
| 19 | memory mode | PASS |
| 20 | restart / resume | PASS |

The MA0 version-only late-answer limitation remains accurately characterized
for retained durable graph versions. The NEW Scheduler deliberately strengthens
that boundary with persisted wait, task, question, command-kind, and revision
fences; it does not weaken or silently reinterpret the frozen behavior.

## NEW-Path Comparison

The focused current-path matrix verifies:

```text
Scheduler phase-order characterization
durable command and WAIT_USER fencing
output compatibility
SSE and report replay parity
production entry cutover
single production orchestration path
invocation crash recovery
```

Result:

```text
39 passed
unresolved compatibility differences = 0
same-execution OLD/NEW dual execution = 0
```

## Verification

```text
exact frozen MA0 characterization gate: 298 passed, 5 deselected
current Scheduler parity matrix: 39 passed
all acceptance tests: 225 passed
architecture full suite: 125 passed
Python compileall (app + tests): PASS
git diff --check: PASS (only LF/CRLF conversion warnings)
```

Architecture inventories remain valid from the final MA8-T04 refresh:

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
all 20 frozen MA0 behavioral invariants remain green: PASS
current NEW Scheduler behavior remains parity-safe: PASS
public acceptance behavior remains green: PASS
unresolved compatibility regression: NONE
production behavior changes required by MA8-T05: NONE
```

```text
MA8-T05 = PASS
NEXT_TASK = NOT_STARTED
```
