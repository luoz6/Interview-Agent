# MA9-T06 Final Reviewer and ReportCoach

Date: 2026-09-22

```text
PHASE = MA9-T06
STATUS = COMPLETE
MA9_T06_FINAL_PIPELINE = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Changed Files

- `app/application/interview/scheduler_production_entry.py`
- `app/application/scheduling/scheduler.py`
- `app/domain/interview/scheduling/plan.py`
- `app/domain/interview/scheduling/state.py`
- `tests/contracts/test_ma9_t06_final_pipeline.py`
- `tests/contracts/test_ma9_t03_resolution_gate.py`

## Before Behavior

The static DAG contained `evaluate:interview` and `report:interview`, but the
production entry stopped after the final question resolution. The Final
Reviewer and ReportCoach were not dispatched, and their assembler inputs were
state/ref projections rather than durable transcript and evaluation payloads.

Follow-up pair registration consumed counters atomically, but production limits
and budget-exhaustion continuation were not wired into the MA9 path.

## After Behavior

After the last question resolution gate completes, the production path drives:

```text
evaluate:interview -> Final Reviewer
report:interview   -> ReportCoach
ReportArtifact durable
execution          -> COMPLETED
```

Final Reviewer receives the artifact-backed interview transcript. ReportCoach
receives the complete persisted final evaluation items. Execution becomes
`COMPLETED` only after the report task completes and its artifact ref is part of
ExecutionState.

ExecutionPlan now carries immutable follow-up/replan limits. If an insufficient
evaluation exhausts any applicable limit, one canonical transition records the
unresolved gap and completes the question resolution gate without registering
or charging another pair.

## Contract Changes

`ExecutionConstraints` now includes `max_followups_total`,
`max_followups_per_question`, and `max_replans_total`. `ExecutionState` owns
`unresolved_gaps` plus the usage counters introduced in T04.

`complete_resolution_with_gap()` atomically records the terminal gap and
completes the Scheduler-owned resolution gate. The next main question, or final
pipeline for the last question, then becomes dependency-ready.

## RED -> GREEN Evidence

```text
T06 RED:
  1 failed
  final question answer returned active; final pipeline was not dispatched

T06 GREEN:
  final pipeline E2E: 1 passed
  T02-T06 cross-stage: 18 passed
  budget exhaustion focus: 11 passed
  affected contract matrix: 32 passed
  final contracts excluding protected PostgreSQL approval suite:
    1075 passed, 2 skipped
```

Verified invocation order:

```text
Examiner main
Reviewer evaluate-answer
Final Reviewer evaluate-interview
ReportCoach generate-report
```

## Crash / Replay Evidence

Both final artifacts use the common idempotent ExecutionArtifactStore and are
attached by deterministic task/attempt refs. A missing referenced payload fails
closed during downstream request assembly or projection. Existing invocation
ledger recovery continues to replay committed task effects without re-invoking
an Agent. Full crash-window verification remains scheduled for T09.

## Production Path Evidence

The T06 E2E uses the real `SchedulerProductionEntry`, Scheduler policy, typed
requests, production artifact commit path, final dependency order, and
artifact-backed projection. It asserts final evaluation and report task states,
Agent invocation order, artifact refs, report payload retrieval, and final
snapshot status.

The exhaustion E2E verifies that a second insufficient result with a one-pair
limit creates no `followup:q1:2`, does not increment usage again, records the
gap, completes `resolve:q1`, and dispatches `main:q2`.

## Architecture Impact

No Final Reviewer workflow or report orchestration service was introduced; the
existing Scheduler and A2A capabilities remain the single path. Full
architecture tests report `125 passed, 4 failed`, with the same four stale
checked-in scanner artifacts present since T00. Excluding those four known
artifact-equality files, architecture rules pass: `105 passed`.

## LOC / Complexity Delta

Current principal module sizes are:

```text
scheduler_production_entry.py  714 lines
scheduler.py                  1528 lines
scheduler_projection.py        229 lines
T06 focused contract           192 lines
```

Final pipeline driving remains a bounded production-entry method. Payload
materialization stays in Scheduler request assembly, and read-model work stays
in the dedicated projection module.

## Remaining Risks

- Protected PostgreSQL behavior is unverified without
  `POSTGRES_TEST_APPROVAL_ID`; it is not reported as PASS.
- Full Agent artifact crash windows and duplicate outbox delivery are deferred
  to T09 as planned.
- Streaming lifecycle and question streaming remain T07/T08 work and are not
  authorized until the semantic gate receives human approval.
- Four checked-in architecture scanner artifacts remain stale by baseline
  decision and were not refreshed.

## Gate Decision

```text
FINAL_REVIEWER = PASS
REPORT_COACH = PASS
FINAL_ARTIFACT_PERSISTENCE = PASS
COMPLETE_ONLY_AFTER_REPORT = PASS
BUDGET_SCOPE_AND_ATOMICITY = PASS
MA9_T06_FINAL_PIPELINE = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
SEMANTIC_CUTOVER_GATE = READY_FOR_REVIEW
T07_ENTRY = BLOCKED_PENDING_HUMAN_REVIEW
```
