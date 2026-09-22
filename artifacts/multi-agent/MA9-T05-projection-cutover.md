# MA9-T05 SessionStore Projection Cutover

Date: 2026-09-22

```text
PHASE = MA9-T05
STATUS = COMPLETE
MA9_T05_PROJECTION_CUTOVER = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Changed Files

- `app/application/interview/scheduler_projection.py`
- `app/application/interview/scheduler_production_entry.py`
- `app/application/interview/session_commands.py`
- `app/application/scheduling/scheduler.py`
- `app/domain/interview/state.py`
- `app/domain/interview/scheduling/artifacts.py`
- `app/domain/interview/scheduling/state.py`
- `app/adapters/memory/session_store.py`
- `app/adapters/persistence/postgres/session_store.py`
- `app/adapters/persistence/postgres/execution_artifacts.py`
- `tests/contracts/test_ma9_t05_projection_cutover.py`
- `tests/contracts/test_production_entry_cutover_contract.py`

## Before Behavior

NEW executions still called legacy `SessionStore.start()` and
`SessionStore.submit_answer()`. `_advance_projection()` then changed canonical
Scheduler state by skipping tasks, creating waits, and marking executions
complete based on the legacy turn. Reviewer context also read the legacy
transcript.

## After Behavior

NEW executions create a metadata-only SessionStore shell. The Scheduler owns
question dispatch, answer acceptance, evaluation, resolution, next-question
selection, wait creation, and completion. `SchedulerSessionProjector` reads the
immutable plan, `ExecutionState`, and durable artifacts to produce the legacy
`InterviewTurn` and snapshot shapes without mutating execution state.

The temporary streaming facade also invokes the Scheduler path directly and
returns a done event without running the legacy answer state machine. T07/T08
will replace that non-streaming compatibility response with the durable stream
lifecycle.

## Contract Changes

All Agent results are persisted through the configured ExecutionArtifactStore
before their refs become projection inputs. Unknown Agent-owned artifact schemas
round-trip losslessly as `StoredExecutionArtifact`; AnswerArtifact retains its
typed v1 contract. Reviewer transcript assembly reads these artifact facts and
uses SessionStore only for immutable session metadata.

An explicit user finish is a canonical `ExecutionState.complete_by_user()`
transition; it no longer synthesizes a legacy closing message or lets a
SessionStore transition complete the execution.

## RED -> GREEN Evidence

```text
RED:
  2 failed
  both failures proved legacy SessionStore.start() still ran

GREEN:
  T05 focused: 2 passed
  T02-T05 compatibility: 42 passed
  full contracts excluding protected PostgreSQL approval suite:
    1073 passed, 2 skipped
```

The T05 tests use a SessionStore whose legacy `start`, `submit_answer`, `skip`,
and `finish` methods raise immediately. A complete q1 answer still reaches
Reviewer, resolves q1, dispatches q2, and produces the expected artifact-backed
snapshot.

## Crash / Replay Evidence

Snapshot reads leave `ExecutionState` byte-for-byte equal. Agent artifacts use
idempotent `put_if_absent`; referenced payload absence fails closed through
`get_required`. Explicit finish replay returns the same completed projection
without a second state transition or legacy message.

## Production Path Evidence

`SchedulerProductionEntry.start/execute/snapshot` now use the same configured
Scheduler and ExecutionArtifactStore as runtime composition. `SessionCommandService`
does not publish legacy transition events for NEW executions. The verified
artifact order is:

```text
main question -> answer -> evaluation -> next main question
```

Only user-visible question and answer artifacts become transcript messages;
evaluation artifacts remain business facts but are not rendered as dialogue.

## Architecture Impact

Projection logic was moved into a dedicated application module instead of
further expanding the production entry. No second scheduler or workflow engine
was introduced. Architecture tests reported `125 passed, 4 failed`; all four
are the unchanged stale checked-in scanner artifacts recorded at T00.

## LOC / Complexity Delta

`scheduler_projection.py` adds approximately 229 focused lines and the T05
contract adds approximately 185 lines. `_advance_projection()` and its legacy
state-control branches were deleted from the production entry. Static search
finds no production occurrence of `_advance_projection`,
`compatibility_projection`, or `compatibility-projection`.

## Remaining Risks

- T06 must dispatch Final Reviewer and ReportCoach after all resolution gates,
  persist both artifacts, and only then complete the execution.
- The streaming facade intentionally emits only a final done event until the
  T07/T08 lifecycle and streaming cutover stages.
- Protected PostgreSQL verification is deferred because
  `POSTGRES_TEST_APPROVAL_ID` is unavailable.
- The four checked-in architecture scanner artifacts remain stale by prior
  baseline decision.

## Gate Decision

```text
SESSIONSTORE_PROJECTION_ONLY = PASS
PROJECTION_MUTATES_EXECUTION_STATE = 0
LEGACY_BUSINESS_TRANSITIONS_ON_NEW_PATH = 0
ARTIFACT_BACKED_REVIEWER_CONTEXT = PASS
MA9_T05_PROJECTION_CUTOVER = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
T06_ENTRY = READY
```
