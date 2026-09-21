# MA7-T03 — Production Entry Cutover

## Implementation

All new interview executions now enter through `SchedulerProductionEntry`:

```text
ordinary start          -> Scheduler
plan-revision start     -> Scheduler
prepared-plan launch    -> Scheduler
```

The entry deterministically maps `InterviewPlan`, `InterviewPlanV2`, or
`InterviewPlanV3` into one immutable `ExecutionPlan` and initial
`ExecutionState`. It claims the execution's immutable path as `NEW`, persists
the plan and state, and runs the bounded Scheduler bootstrap until
`WAIT_USER`, `COMPLETE`, or `FAILED`.

Replay and recomposition load the persisted Scheduler plan/state and call
`ensure_bootstrapped`; they do not reconstruct execution ownership from the
legacy workflow. Public snapshots expose `orchestration_path = NEW` and the
monotonic `scheduler_revision` while retaining the existing API state version
as a compatibility projection.

The Scheduler execution repository has memory and PostgreSQL adapters. Runtime
migration v32 adds `<prefix>_scheduler_executions`, stores the immutable plan
and revision-fenced state, rejects stale writes, and participates in session
deletion.

## Compatibility Boundary

The immutable `execution_path_binding` remains the source of truth:

```text
NEW binding -> Scheduler entry, snapshot, command, and stream fencing
OLD binding -> existing workflow continues to drain
unbound new execution -> claimed NEW before interview work
```

The legacy session store remains a compatibility projection for the current
API/SSE/event/report behavior. Removing its remaining business-routing role is
explicitly deferred to MA7-T05. MA7-T04 was not started in this Task.

## Verification

```text
production entry and API/compatibility regression suite: 119 passed
architecture full suite: 122 passed
PostgreSQL schema/migration contracts without real-DB markers: 71 passed, 6 deselected
all contract code tests from the Task run: 1043 passed, 3 skipped
all unit code tests from the Task run: 2662 passed
Python compileall: PASS
git diff --check: PASS (only LF/CRLF conversion warnings)
```

Real PostgreSQL scope tests were not run because only `POSTGRES_DSN` is present;
the required external approval id, approval receipt, approved fingerprint,
database allowlist, and expiry are absent. The repository intentionally fails
closed in that condition. No approval guard was bypassed.

Architecture inventories were regenerated after the cutover:

```text
app_files_scanned = 515
test_files_scanned = 460
top_level_symbols_scanned = 2925
parse_error_count = 0
cross_layer_violations = 0
```

## Acceptance

```text
ordinary new execution enters Scheduler: PASS
plan-revision new execution enters Scheduler: PASS
prepared-plan new execution enters Scheduler: PASS
new execution is immutably bound NEW: PASS
Scheduler bootstraps to WAIT_USER: PASS
persisted plan/state survive recomposition: PASS
NEW snapshot cannot call OLD workflow: PASS
answer/skip/finish and stream commands are Scheduler-fenced: PASS
existing OLD execution can continue to drain: PASS
OLD execution cannot be reclaimed by NEW: PASS
NEW execution cannot be reclaimed by OLD: PASS
production dual-path bypasses detected: 0
PostgreSQL v32 append-only migration contracts: PASS
```

```text
MA7-T03 = PASS
NEXT_TASK = NOT_STARTED
```
