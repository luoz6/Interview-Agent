# MA9-T02 Answer to Reviewer Production Cutover

Date: 2026-09-22

```text
PHASE = MA9-T02
STATUS = COMPLETE
MA9_T02_ANSWER_REVIEWER_CUTOVER = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```

## Changed Files

Production contracts and adapters:

- `app/domain/interview/scheduling/artifacts.py`
- `app/ports/execution_artifacts.py`
- `app/adapters/memory/execution_artifacts.py`
- `app/adapters/persistence/postgres/execution_artifacts.py`
- `app/adapters/persistence/postgres/runtime_control.py`
- `app/adapters/persistence/postgres/runtime_migrations.py`
- `app/adapters/postgres/identifiers.py`
- `app/adapters/postgres/schema_contract.py`
- `app/adapters/postgres/store_schema_adapter.py`
- `app/application/scheduling/scheduler.py`
- `app/application/interview/scheduler_production_entry.py`
- `app/application/interview/session_commands.py`
- `app/runtime/composition.py`
- `app/runtime/scheduler_composition.py`
- `app/domain/interview/scheduling/__init__.py`
- `app/ports/__init__.py`

Evidence and compatibility tests:

- `tests/contracts/test_ma9_t02_answer_reviewer_cutover.py`
- `tests/contracts/test_ma9_t00_current_gaps.py`
- PostgreSQL schema/migration and runtime-composition contract tests

## Before Behavior

The production answer path accepted a user command, copied the complete answer
into `ExecutionState.latest_observation`, and compatibility-skipped the matching
`evaluate:*` task. Reviewer invocation count was zero and an answer had no
durable business artifact payload behind its state ref.

The streaming path called `accept_answer()` but completed only the legacy
projection. It did not dispatch the Reviewer either.

## After Behavior

Both synchronous and streaming production paths now execute:

```text
fenced UserCommand
-> deterministic AnswerArtifact ref
-> durable ArtifactStore put_if_absent
-> ExecutionState answer ref + clear original wait
-> legacy SessionStore projection
-> dispatch only the matching evaluate:* task
-> Reviewer exactly once
-> evaluation task COMPLETED
-> compatibility projection creates the next wait
```

`ExecutionState` contains bounded answer metadata and `answer_artifact_ref`; it
does not contain `answer_text`. The temporary T02 Reviewer read-model bridge
loads the legacy session snapshot through `session_store.get`. T05 must remove
that bridge when artifact-only projection becomes authoritative.

## Contract Changes

`AnswerArtifact` implements the approved `answer-artifact-v1` schema. Its ref is
the lowercase SHA-256 of canonical JSON `[execution_id, command_id]`, prefixed
with `answer/sha256:`.

`ExecutionArtifactStore` freezes `put_if_absent`, `get_required`, and `exists`.
Same-ref/different-payload writes fail with `ArtifactPayloadConflict`. Both
memory and PostgreSQL adapters reject a key that differs from an artifact's
embedded ref.

PostgreSQL runtime schema V34 appends `_execution_artifacts`, owned by
`_scheduler_executions` through `execution_id ON DELETE CASCADE`. V1-V33
checksums are unchanged. Table creation orders the referenced Scheduler table
before the artifact table.

## RED -> GREEN Evidence

T00 RED characterization:

```text
Reviewer invocation count = 0
Evaluation task = SKIPPED (compatibility_projection)
Answer payload copied into ExecutionState
No durable AnswerArtifact
```

T02 GREEN result:

```text
Reviewer invocation count = 1
Evaluation task = COMPLETED
AnswerArtifact retrievable from ArtifactStore
ExecutionState contains ref and no answer text
Duplicate command keeps Reviewer invocation count = 1
```

The obsolete T00 answer assertions were converted to the GREEN behavior. The
remaining T00 tests continue to characterize gaps assigned to T03 and later.

## Crash / Replay Evidence

`test_replay_repairs_state_after_artifact_commit_before_state_commit` simulates
an artifact committed before its state ref and wait-clear effects. Replaying the
same command attaches the missing ref and clears only the original matching
wait.

If a later/different wait exists, replay preserves it. A replay with a different
answer or fence identity fails closed. Concurrent equivalent first writes reuse
the stored winner and its original `submitted_at`; payload mismatches remain
conflicts. A completed evaluation is never dispatched a second time.

## Production Path Evidence

`tests/contracts/test_ma9_t02_answer_reviewer_cutover.py` exercises the real
`SchedulerProductionEntry` and `StreamingTurnService`, with typed Scheduler
capabilities and artifacts. It proves synchronous and streaming Reviewer
dispatch rather than testing an isolated helper only.

Test results:

```text
T02 focused:                         4 passed
sync/stream/SSE regression:         12 passed
related Scheduler/schema contracts: 72 passed
all contracts excluding protected
PostgreSQL approval suite:           1067 passed, 2 skipped
```

Running all contracts without exclusion produced `1066 passed, 3 skipped,
3 failed`; all three failures require the absent `POSTGRES_TEST_APPROVAL_ID`
and match the T00 baseline.

## Architecture Impact

The dependency direction remains domain -> port -> adapter. Scheduler receives
the neutral artifact port and a temporary callable read-model bridge; it does
not import a PostgreSQL or A2A implementation. Runtime composition owns one
artifact-store instance and exposes it in `SchedulerRuntimeComposition`.

Architecture tests produced `125 passed, 4 failed`. The same four checked-in
dead-code, duplicate, final-report, and legacy scanner artifacts were already
stale at T00. T02 introduced no additional architecture failure.

## LOC / Complexity Delta

Tracked production diff is `+458/-41` lines. Four new production modules add
191 physical lines, for an approximate production net of `+608` lines.

The increase is concentrated in one artifact model, one neutral port, two
adapters, replay repair, production dispatch, and V34 schema ownership. No new
workflow engine, transport, or second Scheduler was introduced.

## Remaining Risks

- Protected PostgreSQL execution was not run because no
  `POSTGRES_TEST_APPROVAL_ID` was supplied.
- Command, artifact, Scheduler state, and legacy session writes remain separate
  physical transactions. T02 provides deterministic replay repair, not a
  cross-store ACID transaction.
- Reviewer input still uses the legacy session snapshot bridge until T05.
- T03 must add EvaluationArtifact v2 ingestion and a resolution gate before the
  next main task can be semantically released.
- T04 must create a dynamic follow-up plus follow-up-evaluation pair.

## Gate Decision

```text
ANSWER_ARTIFACT_DURABLE = PASS
ANSWER_STATE_REF_ONLY = PASS
REVIEWER_INVOCATION_COUNT_ONE = PASS
EVALUATION_TASK_NOT_SKIPPED = PASS
DUPLICATE_ANSWER_NO_DUPLICATE_REVIEWER = PASS
SYNC_AND_STREAMING_PRODUCTION_PATH = PASS
MA9_T02_ANSWER_REVIEWER_CUTOVER = PASS
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
T03_ENTRY = READY
```
