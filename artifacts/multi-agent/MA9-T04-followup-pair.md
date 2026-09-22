# MA9-T04 Dynamic Follow-up Pair and Re-evaluation

Date: 2026-09-22

```text
PHASE = MA9-T04
STATUS = COMPLETE
MA9_T04_FOLLOWUP_PAIR = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Changed Files

- `app/domain/interview/scheduling/state.py`
- `app/domain/interview/scheduling/decisions.py`
- `app/application/scheduling/scheduler.py`
- `app/application/interview/scheduler_production_entry.py`
- `tests/contracts/test_ma9_t03_resolution_gate.py`
- `tests/contracts/test_evidence_insufficient_replan_contract.py`
- `tests/contracts/test_adaptive_interview_e2e.py`

## Before Behavior

An insufficient answer could add a single follow-up task, but there was no
atomic registration of its matching Reviewer evaluation task. The legacy
adaptive E2E also treated the follow-up answer as interview completion instead
of requiring Reviewer re-evaluation through the question resolution gate.

## After Behavior

An insufficient evaluation now registers and executes the complete branch:

```text
evaluate:q1
  -> followup:q1:1
  -> WAIT_USER
  -> evaluate-followup:q1:1
  -> resolve:q1
  -> main:q2
```

`ExecutionState.register_followup_pair()` adds both dynamic task definitions
and both runtime task states in one immutable transition. Scheduler selection
skips the unresolved resolution gate and makes the dynamic follow-up READY
before dispatch. A sufficient follow-up evaluation completes the original gate;
only then can the next main question run.

## Contract Changes

`ExecutionState` now owns mutable `followups_total_used`,
`followups_by_question`, and `replans_used` counters. Their increment is part of
the same canonical transition as pair registration. Dynamic identities remain:

```text
followup:{question_id}:{ordinal}
evaluate-followup:{question_id}:{ordinal}
```

The follow-up evaluation uses the production Reviewer `evaluate-answer`
capability and the EvaluationArtifact v2 decision matrix introduced in T03.

## RED -> GREEN Evidence

```text
Before:
  insufficient -> one follow-up task
  follow-up answer -> COMPLETE

After:
  insufficient -> atomic follow-up plus evaluation pair
  follow-up answer -> Reviewer re-evaluation
  sufficient re-evaluation -> resolve gate -> next main
```

Verified invocation order:

```text
Examiner main q1
Reviewer evaluate q1
Examiner followup q1:1
Reviewer evaluate-followup q1:1
Examiner main q2
```

Focused production order tests: `4 passed`.
Focused compatibility tests: `23 passed`.
Final T04 compatibility rerun: `10 passed in 31.35s`.

## Crash / Replay Evidence

Re-registering an already complete pair returns the existing state without
adding definitions or consuming budget again. If only one member of the pair
exists, registration fails closed with an invariant error. Thus neither
`pair exists / budget not incremented` nor `budget incremented / pair missing`
is representable through the canonical transition.

## Production Path Evidence

The production entry consumes the persisted EvaluationArtifact v2. On
`EVALUATED + INSUFFICIENT`, it calls the canonical pair transition, persists
the updated execution state, dispatches the Examiner follow-up, enters
`WAIT_USER`, and later dispatches Reviewer for `evaluate-followup`.

```text
full contracts excluding protected PostgreSQL approval suite:
  1071 passed, 2 skipped
```

## Architecture Impact

No second scheduler, workflow engine, invocation path, or retry system was
added. Architecture tests reported `125 passed, 4 failed`; the four failures
are the same stale checked-in scanner artifacts recorded at T00. The artifacts
were intentionally not refreshed as part of this phase.

## LOC / Complexity Delta

Across the cumulative T02-T04 working tree, the main affected modules show:

```text
scheduler_production_entry.py  +318 / -30
scheduler.py                   +216 / -9
state.py                       +80 / -0
decisions.py                   +48 / -1
```

T04 keeps pair identity and budget mutation in one domain method. Production
entry orchestration remains the main complexity risk and is addressed next by
removing compatibility state transitions during T05.

## Remaining Risks

- SessionStore compatibility projection still performs business transitions;
  T05 must reduce it to a read-model projection only.
- Follow-up and replan limits are recorded atomically; complete exhaustion and
  degraded Reviewer policy remain part of the semantic cutover work.
- Protected PostgreSQL verification is deferred because
  `POSTGRES_TEST_APPROVAL_ID` is unavailable.
- The four checked-in architecture scanner artifacts remain stale by prior
  baseline decision.

## Gate Decision

```text
DYNAMIC_FOLLOWUP_PAIR = PASS
REVIEWER_REEVALUATION = PASS
BUDGET_ATOMICITY = PASS
INVOCATION_ORDER = PASS
MA9_T04_FOLLOWUP_PAIR = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
T05_ENTRY = READY
```
