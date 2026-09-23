# MA9-CF00 Production Closure Baseline

Date: 2026-09-23
Baseline commit: `3a94b14`

## Current Streaming Call Path

`SchedulerProductionEntry` prepares a question boundary and
`runtime/scheduler_streaming.py` starts `AgentStreamInvocationWorker`, but its
`invoke()` callback calls `entry.dispatch_streaming_question()`, which uses
the synchronous `AgentInvocationPort.invoke()` path. The returned complete
question artifact is then yielded as one string, producing one `DELTA` event.
The provider-facing `InterviewLLM` exposes `stream_followup` but no
`stream_main_question`; the canonical invocation port exposes no
`invoke_stream`.

## Current Runtime Version Routing

All NEW production entry methods require the MA9 orchestration version. A
pre-MA9 NEW execution with `scheduler-pre-ma9` raises
`OrchestrationVersionMismatch`; there is no version-aware compatibility drain
router yet.

## Current PostgreSQL Commit Boundaries

The PostgreSQL execution artifact adapter opens its own connection and calls
`connection.commit()` after artifact insert. The scheduler execution
repository independently opens connections and commits state/plan writes.
The runtime control store has an outbox repository and transaction helpers,
but the MA9 answer and agent-result paths do not pass one shared unit of work
through command, artifact, ledger, state, and outbox writes.

```text
Command       = workflow command store transaction
Artifact      = execution_artifacts adapter transaction
Invocation    = invocation ledger transaction
ExecutionState= scheduler execution repository transaction
Outbox        = runtime control / outbox transaction
Ownership     = independent commits with replay recovery
POSTGRES_COMMIT_BASELINE = RECOVERABLE_MULTI_WRITE
```

## Current Budget Ownership

`ExecutionPlan.execution_constraints` currently owns follow-up and replan
limits plus scheduler/task limits, while `ExecutionState` owns follow-up and
replan usage. It does not yet own `max_agent_calls`, `max_retries`,
`execution_timeout_seconds`, `agent_calls_used`, `retries_used`, or a durable
execution start timestamp.

## Current DEGRADED Behavior

The scheduler commits a DEGRADED/UNDETERMINED reviewer artifact as a completed
task, but the production resolution path only recognizes SUFFICIENT,
INSUFFICIENT, and an undetermined fall-through. There is no canonical retry
attempt or retry budget transition before follow-up/continuation decisions.

## Current Finish Behavior

`SchedulerProductionEntry.execute(command_type="finish")` immediately calls
`ExecutionState.complete_by_user()` and projects a finished turn. It does not
terminalize unanswered branches into a completion request, drive Final
Reviewer, require ReportCoach, or enforce the ReportArtifact-before-COMPLETED
invariant.

## RED Test Results

```text
Expected RED contracts:
main provider streaming API: FAIL (missing stream_main_question)
canonical invoke_stream port: FAIL (missing invoke_stream)
pre-MA9 compatibility runtime: RED condition reproduced by mismatch path
complete budget ownership: RED (fields rejected)
transactional outbox ownership: RED baseline (independent commits)
```

## Gate

```text
CF00_BASELINE = PASS
CF00_PRODUCTION_GAPS_REPRODUCED = PASS
PRODUCTION_BEHAVIOR_CHANGED = NO
```
