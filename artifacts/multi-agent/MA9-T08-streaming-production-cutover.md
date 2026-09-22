# MA9-T08 Streaming Production Cutover

Date: 2026-09-22

```text
PHASE = MA9-T08
STATUS = COMPLETE_WITH_T09_E2E_WORK_REMAINING
MA9_T08_STREAMING_PRODUCTION_CUTOVER = PASS
LEGACY_ACCEPTANCE_FIXTURE_MIGRATION = FAIL
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Changed Files

- `app/runtime/scheduler_streaming.py`
- `app/runtime/agent_streaming.py`
- `app/application/interview/scheduler_production_entry.py`
- `app/application/interview/session_commands.py`
- `app/application/interview/scheduler_projection.py`
- `app/api/interview/routes.py`
- `app/api/shared/dependencies.py`
- `tests/contracts/test_ma9_t08_streaming_production_cutover.py`
- `tests/contracts/test_ma9_t07_streaming_lifecycle.py`

## Before Behavior

The NEW Scheduler `/answer/stream` path executed the complete synchronous
answer pipeline and returned one legacy `done` event. Initial Scheduler V3
bootstrap also generated its first question synchronously before the client
opened `/bootstrap/stream`.

Neither route delivered the frozen `AgentStreamEvent` lifecycle from T07.

## After Behavior

The production answer stream advances the accepted answer through Reviewer and
question resolution, then stops at the next Examiner dispatch boundary. A
runtime-owned worker executes the existing canonical `scheduler.step()` and
delivers:

```text
STARTED -> DELTA* -> COMPLETED
```

for both next-main and dynamic follow-up questions. The same worker path serves
initial Scheduler V3 bootstrap. The Scheduler remains the only owner of Agent
invocation, artifact persistence, task completion, and `WAIT_USER`.

Externally, `COMPLETED` is yielded only after the canonical driver has durably
entered `WAIT_USER`, so an immediate answer cannot race the state transition.
On the final interview answer, where no Examiner question follows, the route
retains a normal `done` SSE boundary.

## Delivery and Recovery

SSE events use their frozen event types as the SSE event name and carry the
complete `AgentStreamEvent` envelope. A disconnected generator closes only its
subscription. The runtime worker continues, commits the question artifact,
enters `WAIT_USER`, and the public snapshot retrieves the final question.

No durable token log or exact token replay was added. The current Examiner A2A
port is synchronous for main questions, so the v1 adapter emits one canonical
text DELTA after the Scheduler invocation returns. This is truthful to the
current provider capability and preserves the frozen `DELTA*` contract.

## RED -> GREEN Evidence

```text
T08 RED:
  collection failed: app.runtime.scheduler_streaming did not exist

T08 GREEN:
  focused streaming production contracts: 6 passed
  T07 + T08 lifecycle/cutover contracts: 18 passed
  adjacent T02-T08, V3, API-router regression: 74 passed
  full contracts excluding protected PostgreSQL approval suite:
    1093 passed, 2 skipped
```

Focused coverage includes initial main question, next main question, dynamic
follow-up, completed/wait ordering, disconnect continuation and snapshot
recovery, canonical artifact text/ref, and final-answer compatibility.

## Architecture Evidence

```text
raw architecture: 125 passed, 4 failed
```

The four failures remain exactly the stale checked-in T00 scanner artifact
comparisons: dead-code, duplicate implementation, final architecture report,
and legacy-version removal. No architecture rule newly failed. The artifacts
were not refreshed or reverted.

## Acceptance Finding Carried To T09

The full legacy `tests/acceptance/test_api.py` run reported:

```text
13 failed, 26 passed
```

The first failure is the already-cut-over NEW production path invoking the real
Reviewer A2A adapter while the legacy acceptance fixture configures only its
old interview fake LLM. The remaining failures are primarily downstream state
failures from that setup mismatch. Reverting NEW executions to the legacy
state machine would invalidate T02-T08, so this suite/fixture migration is
explicit T09 full-E2E work and is not reported as PASS here.

## Static Verification

```text
relevant Python compile = PASS
git diff --check = PASS
```

`git diff --check` emitted only line-ending warnings.

## Deferred Verification

```text
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

The protected PostgreSQL suite cannot be authorized without
`POSTGRES_TEST_APPROVAL_ID`; no PostgreSQL reliability PASS is claimed.

## Gate Decision

```text
MAIN_QUESTION_STREAMING_CUTOVER = PASS
FOLLOWUP_STREAMING_CUTOVER = PASS
CANONICAL_ARTIFACT_TEXT_MATCH = PASS
COMPLETED_BEFORE_CLIENT_CAN_ANSWER = PASS
DISCONNECT_SNAPSHOT_RECOVERY = PASS
MA9_T08_STREAMING_PRODUCTION_CUTOVER = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
T09_ENTRY = READY
```
