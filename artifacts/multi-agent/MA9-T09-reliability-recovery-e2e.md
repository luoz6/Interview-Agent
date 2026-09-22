# MA9-T09 Reliability, Recovery, and Full E2E

Date: 2026-09-22

```text
PHASE = MA9-T09
STATUS = COMPLETE
MA9_T09_RELIABILITY_RECOVERY_E2E = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Reliability Coverage

The local production-path suite covers the required Answer crash matrix:

- command persisted / artifact missing: replay materializes the deterministic
  AnswerArtifact and completes the state commit;
- artifact persisted / state missing: replay repairs the artifact ref, clears
  the wait, and advances exactly once;
- state persisted / outbox undelivered: redelivery observes the committed
  state and remains idempotent;
- duplicate delivery: the same command and payload replay without a second
  state transition or artifact.

Agent artifact recovery verifies both sides of the invocation commit boundary:

- an artifact saved before ledger commit can be recovered;
- a committed ledger entry can repair missing state without reinvocation;
- a committed ledger ref whose payload is absent fails closed before state
  mutation or task completion.

Dynamic follow-up task pairs and their budget counters are validated as one
state invariant. A partial pair or a pair/budget mismatch cannot be constructed
as a valid ExecutionState.

## Runtime Version and Idempotency

New MA9 executions bind the immutable orchestration version
`scheduler-ma9-v1`. A pre-MA9 NEW execution is rejected by the MA9 production
entry and cannot be taken over after deployment.

A2A idempotency keys include the frozen main-question identity and the answer
artifact identity. Distinct logical invocations therefore cannot collapse into
one cached Agent result.

## Production Wiring

PostgreSQL production composition now injects a container-owned
`PostgresSchedulerCommandAdapter` into Scheduler's `durable_command_port`.
The adapter reuses `PostgresInterviewWorkflowStore`, so durable command
acceptance and restart replay use the existing workflow command table rather
than a second command implementation.

This wiring is verified locally with composition contracts. It is not counted
as real PostgreSQL reliability verification.

## E2E Evidence

The mandatory flows are covered by the MA9 T02/T03/T06/T07/T08/T09 suites:

- A: sufficient answer resolves q1 and exposes the next main question;
- B: insufficient answer creates one follow-up, reevaluates, then resolves;
- C: the next main question stays blocked behind the resolution gate;
- D: exhausted follow-up budget records an unresolved gap and continues;
- E: all resolution gates lead to Final Reviewer, ReportCoach, and COMPLETE;
- F: streaming survives disconnect, commits the artifact, and replays the
  final snapshot on reconnect.

Current verification results:

```text
MA9 T02/T03/T06/T07/T08/T09 core: 41 passed
Contracts excluding protected PostgreSQL: 1107 passed, 2 skipped
Acceptance: 226 passed
Architecture: 129 passed
Python compileall: PASS
git diff --check: PASS
```

## PostgreSQL Boundary

`POSTGRES_TEST_APPROVAL_ID` is absent. The protected PostgreSQL reliability
suite was not run or bypassed.

```text
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Gate Decision

```text
ANSWER_ATOMIC_COMMIT = PASS
INVOCATION_COMMIT_RECOVERY = PASS
DYNAMIC_FOLLOWUP_PAIR = PASS
BUDGET_SCOPE_AND_ATOMICITY = PASS
IMMUTABLE_ORCHESTRATION_VERSION = PASS
STREAMING_LIFECYCLE = PASS
RECOVERY_GATE = PASS
LOCAL_REGRESSION_GATE = PASS
MA9_T09_RELIABILITY_RECOVERY_E2E = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```
