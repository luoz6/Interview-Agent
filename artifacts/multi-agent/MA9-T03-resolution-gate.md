# MA9-T03 Evidence Decision and Resolution Gate

Date: 2026-09-22

```text
PHASE = MA9-T03
STATUS = COMPLETE
MA9_T03_RESOLUTION_GATE = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Changed Files

- `app/a2a/contracts/evaluation.py`
- `app/a2a/adapters.py`
- `app/a2a/registry.py`
- `app/domain/interview/scheduling/requests.py`
- `app/domain/interview/scheduling/assembler.py`
- `app/domain/interview/scheduling/plan.py`
- `app/domain/interview/scheduling/state.py`
- `app/application/scheduling/policy.py`
- `app/application/scheduling/scheduler.py`
- `app/application/interview/scheduler_production_entry.py`
- `tests/contracts/test_ma9_t03_resolution_gate.py`
- updated T00 and production-entry characterization tests

## Before Behavior

The static DAG linked `evaluate:q1` directly to `main:q2`. Completing the
evaluation therefore made the next main question dispatchable without a
question-level semantic resolution decision.

## After Behavior

Every question now has a non-Agent `QUESTION_RESOLUTION_GATE` task:

```text
main:N:qN -> evaluate:N:qN -> resolve:N:qN -> main:N+1:qN+1
```

An `EVALUATED + SUFFICIENT` v2 evaluation completes the gate through
`ExecutionState.complete_resolution_gate()`. An `EVALUATED + INSUFFICIENT`
evaluation leaves it pending for T04 pair registration. Gate tasks never enter
the capability registry, invocation port, or invocation ledger.

## Contract Changes

Production `evaluate-answer` advertises and returns
`evaluation-artifact-v2`. The contract includes question/answer artifact refs,
the frozen evaluation/evidence status matrix, optional score/confidence, typed
gap, evidence refs, summary, and policy version. Invalid matrix combinations
fail Pydantic validation.

`ExecutionTaskDefinition.task_kind` defaults to `AGENT` for compatibility and
permits `QUESTION_RESOLUTION_GATE` only without Agent contracts and with
`scheduler.question-resolution` capability.

## RED -> GREEN Evidence

```text
Before:
  evaluate:q1 COMPLETED -> policy DISPATCH main:q2

After (insufficient):
  evaluate:q1 = COMPLETED
  resolve:q1  = PENDING
  main:q2     = PENDING
  policy      = NOOP / no_valid_next_action

After (sufficient):
  evaluate:q1 = COMPLETED
  resolve:q1  = COMPLETED
  resolution Agent invocations = 0
```

## Crash / Replay Evidence

T02 answer and Reviewer idempotency remains authoritative. Evaluation replay
does not redispatch a completed task. Gate completion is idempotently ignored
when already completed; an unresolved gate remains the canonical blocker.

## Production Path Evidence

`tests/contracts/test_ma9_t03_resolution_gate.py` runs the real
`SchedulerProductionEntry`, artifact-backed answer request assembly, Reviewer
dispatch, v2 artifact result, gate policy, and repository state.

```text
T03 focused and compatibility tests: 30 passed
full contracts excluding protected PostgreSQL approval suite:
  1070 passed, 2 skipped
```

## Architecture Impact

Resolution remains a pure domain/Scheduler transition. No resolution Agent,
transport handler, or second orchestration path was added. Architecture tests
reported `125 passed, 4 failed`; all four failures are the same stale checked-in
scanner artifacts present at T00 and T02.

## LOC / Complexity Delta

T03 adds approximately 190 production lines and one focused contract module.
Complexity is limited to one task discriminator, one v2 artifact model, one
domain gate transition, and one production evaluation-to-resolution decision.

## Remaining Risks

- T04 must atomically register the follow-up plus follow-up-evaluation pair for
  insufficient evidence; T03 intentionally leaves that gate unresolved.
- Degraded/undetermined retry exhaustion is modeled by v2 but not yet wired to
  the T06 budget policy.
- Evaluation payload durability in the common ArtifactStore remains part of
  the later projection/commit cutover.
- Protected PostgreSQL verification is deferred without an approval id.

## Gate Decision

```text
EVALUATION_ARTIFACT_V2 = PASS
QUESTION_RESOLUTION_GATE = PASS
INSUFFICIENT_BLOCKS_NEXT_MAIN = PASS
SUFFICIENT_COMPLETES_GATE = PASS
RESOLUTION_AGENT_INVOCATION_COUNT = 0
MA9_T03_RESOLUTION_GATE = PASS
T04_ENTRY = READY
```
