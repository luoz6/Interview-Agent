# MA7-T04 — Compatibility Monitoring

## Scope

The cutover comparison reuses the frozen MA0 characterization baseline and the
MA3/MA4 Scheduler parity and recovery contracts. It does not execute OLD and
NEW orchestration against the same execution, preserving the immutable path
ownership rule established in MA7-T02.

The monitored dimensions are:

```text
behavioral invariants
errors
task outcomes
reports
recovery
```

## Comparison Matrix

| Dimension | OLD baseline | NEW cutover evidence | Result |
| --- | --- | --- | --- |
| Behavioral invariants | Frozen MA0 20-invariant characterization gate | Scheduler command, WAIT_USER, output compatibility, SSE, deletion, plan and memory contracts | PASS |
| Errors | Stable legacy version/conflict and public API errors | Neutral Agent errors, output rejection, command conflict, stale/wrong wait rejection | PASS |
| Task outcomes | Frozen question/answer/follow-up/finish transitions | Deterministic Scheduler phase order, adaptive legal outcomes, bounded loop and production entry contracts | PASS |
| Reports | One report job per finished session, retry and lease behavior | Scheduler completion-before-enqueue, retry/requeue recovery, API and SSE report acceptance | PASS |
| Recovery | Legacy/durable restart, retry, lease and tombstone behavior | Scheduler restart, crash redispatch, committed-receipt replay, stale-worker fencing and deletion | PASS |

## Detected Difference And Resolution

The first report-dimension run detected one real compatibility regression:

```text
repeat POST /api/interviews/{session_id}/finish
OLD expected: 200, same finished result, one report enqueue
NEW observed: 400, Scheduler had no WaitHandle after completion
```

`SchedulerProductionEntry.execute()` now preserves the frozen idempotent
`finish` behavior when Scheduler state is already `COMPLETED`. It delegates the
replay to the existing session projection without advancing Scheduler state.
Answers and skips still fail closed when no active `WaitHandle` exists.

The existing report API acceptance test now passes, and a focused Scheduler
contract proves that repeated finish returns the same terminal result, does not
change Scheduler state, and does not duplicate the closing message.

## Verification

```text
frozen MA0 behavioral compatibility gate: 298 passed, 5 deselected
NEW-path five-dimension focused matrix: 137 passed
core API/report/SSE acceptance: 102 passed
all contracts without approval-gated real PostgreSQL markers:
  1044 passed, 2 skipped, 4 deselected
architecture full suite: 122 passed
Python compileall: PASS
git diff --check: PASS (only LF/CRLF conversion warnings)
```

The deselected tests require external PostgreSQL scope approval. Only
`POSTGRES_DSN` is configured; approval id, receipt, target fingerprint,
database allowlist, and expiry are absent. No approval guard was bypassed.

Architecture inventories were regenerated after the fix:

```text
app_files_scanned = 515
test_files_scanned = 460
parse_error_count = 0
cross_layer_violations = 0
```

## Acceptance

```text
behavioral invariants parity: PASS
error semantics parity: PASS
task outcome parity: PASS
report lifecycle parity: PASS
recovery parity: PASS
unresolved compatibility differences: 0
same-execution dual execution: 0
```

```text
MA7-T04 = PASS
NEXT_TASK = NOT_STARTED
```
