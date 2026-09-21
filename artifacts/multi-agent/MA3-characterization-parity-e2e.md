# MA3-T07 - Characterization Parity E2E

## Status

```text
TASK = MA3-T07
STATUS = COMPLETE
PARITY_GATE = PASS
NEXT_TASK = NOT_STARTED
```

The gate compares the deterministic Scheduler path with the frozen MA0
behavioral baseline. It does not claim that every lifecycle subsystem moved
inside the Scheduler: retry delivery, report job persistence, and session
deletion retain their existing durable owners and are verified as unchanged.

## Scheduler E2E

`tests/contracts/test_scheduler_characterization_parity_e2e.py` executes:

```text
Main Question
-> WAIT_USER
-> fenced Answer
-> Evaluation
-> Follow-up
-> WAIT_USER
-> fenced Answer
-> Final Evaluation
-> Report
-> Complete
```

Every Agent dispatch uses the durable invocation ledger and a fenced lease.
Both answers use durable command enqueue with `command_id` and
`expected_version` semantics.

## Parity Matrix

| Required behavior | Scheduler / retained-runtime evidence | Result |
| --- | --- | --- |
| duplicate command | `test_scheduler_durable_command_contract.py::test_durable_replay_is_detected_when_local_command_ledger_is_empty`; legacy/durable MA0 command tests | PASS |
| version conflict | `test_scheduler_wait_user_integration_contract.py::test_wait_user_rejects_stale_wrong_and_replays_commands_deterministically`; MA0 session/projection tests | PASS |
| late answer | Scheduler `WaitHandle` checks execution, wait, task, question, kind, and revision before enqueue; same integration contract above | PASS |
| retry timer | Existing interview/report retry timers remain authoritative; full MA0 non-PostgreSQL characterization suite is unchanged | PASS |
| generation lease | Scheduler invocation ledger lease/fencing contracts plus retained generation lease-loss and retry-CAS tests | PASS |
| JIT identity | Stable `(execution_id, task_id, logical_attempt)` Scheduler identity; retained V3 JIT generation source/replay tests | PASS |
| report enqueue | Scheduler E2E preserves Report-before-Complete; retained report enqueue tests preserve one-job-per-session durability | PASS |
| session delete | Existing claimed deletion job, purge order, fencing, and tombstone replay remain outside Scheduler and pass MA0 regression | PASS |
| plan compatibility | Scheduler validates execution identity and typed capability/request/output contracts; exact legacy graph-version registry tests remain green | PASS |

## Verification

```text
Scheduler full-path E2E: 1 passed
MA0 non-PostgreSQL characterization gate: 296 passed, 5 deselected
Combined MA0 + MA3 parity/architecture regression: 334 passed, 5 deselected
```

The five deselected tests are the frozen PostgreSQL-backed cases requiring the
repository's external-scope approval metadata. They are not replaced by mocks.
The first MA0 run found only generated legacy-version inventory drift caused by
new MA1-MA3 source/test files; the architecture artifacts were regenerated and
the gate was rerun.

## Acceptance Decision

```text
PASS
```

The deterministic Scheduler completes the required normal phase sequence with
durable commands and invocation leases. All frozen local MA0 behaviors remain
green under their established persistence owners, and no parity item regressed.
