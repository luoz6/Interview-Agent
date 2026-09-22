# MA9-T00 Baseline and RED Evidence

Date: 2026-09-22

```text
PHASE = MA9-T00
STATUS = COMPLETE
PRODUCTION_CODE_CHANGED = NO
T01_ENTRY = READY
T02_ENTRY = BLOCKED
```

## Scope

This baseline records the production behavior at commit `1e7a955` before the
MA9 semantic cutover. The RED tests are characterization tests: they pass while
the documented defect exists. Reversing each assertion gives the future GREEN
acceptance assertion.

## Current Answer Path

Exact synchronous path:

```text
POST /api/interviews/{session_id}/answer
  -> app.api.interview.routes.submit_answer
  -> InterviewApplicationService.execute
  -> SchedulerProductionEntry.execute
  -> SchedulerProductionEntry.accept_answer
  -> SchedulerApplicationCapability.accept_user_command
  -> SchedulerApplicationCapability.apply_user_command
  -> SchedulerCommandPort.enqueue (when configured)
  -> ExecutionState.latest_observation = ANSWER_RECEIVED + full payload
  -> ExecutionState.current_wait_handle = None
  -> SessionStore.submit_answer
  -> SchedulerProductionEntry._advance_projection
  -> evaluate:{position}:{question_id} = SKIPPED
     reason_code = compatibility_projection
```

The streaming answer route also calls `SchedulerProductionEntry.accept_answer`
first, but then the HTTP-owned legacy generator drives follow-up generation and
calls `complete_projected_turn()` only after the generator finishes.

Primary source inventory:

| Concern | File / symbol |
|---|---|
| Production entry and DAG | `app/application/interview/scheduler_production_entry.py::build_interview_execution`, `SchedulerProductionEntry` |
| Scheduler execution and command acceptance | `app/application/scheduling/scheduler.py::SchedulerApplicationCapability` |
| Deterministic task selection | `app/application/scheduling/policy.py::DeterministicSchedulerPolicy` |
| Plan and state truth | `app/domain/interview/scheduling/plan.py`, `state.py` |
| Adaptive replan | `app/domain/interview/scheduling/decisions.py::derive_adaptive_followup_decision`, `execute_evidence_insufficient_replan` |
| Commit decision | `app/domain/interview/scheduling/commit.py::CURRENT_INVOCATION_COMMIT_DECISION` |
| Runtime composition | `app/runtime/composition.py::build_scheduler_production_entry`, `compose_scheduler_runtime` |
| HTTP-owned stream | `app/application/interview/session_commands.py::StreamingTurnService`, `app/runtime/agent_execution.py::AgentExecutionRunner._stream` |

## Current Reviewer Behavior

`build_interview_execution()` declares `evaluate-answer` tasks assigned to
`interview-reviewer`. The production answer path does not call
`SchedulerApplicationCapability.step()` after accepting the answer. Instead,
`_advance_projection()` changes the matching evaluation task from `PENDING` or
`READY` to `SKIPPED` with `compatibility_projection`.

Observed production result:

```text
Reviewer invocation count = 0
Evaluation task = SKIPPED
```

The real Reviewer capability exists in the A2A runtime, but production answer
handling does not dispatch it.

## Current Static DAG

For each question, `build_interview_execution()` creates:

```text
main:{position}:{question_id}
  -> evaluate:{position}:{question_id}
  -> main:{next_position}:{next_question_id}
```

The final tail is:

```text
evaluate:{last_position}:{last_question_id}
  -> evaluate:interview
  -> report:interview
```

There is no `resolve:{position}:{question_id}` task. Therefore completion of an
evaluation directly satisfies the dependency for the next main question.

## Current Task Selection Ordering

`DeterministicSchedulerPolicy.decide()` iterates in this order:

1. Active wait handle.
2. Any `WAITING` task.
3. Any `RUNNING` task, returned as `NOOP/awaiting_observation`.
4. Latest question/follow-up artifact, returned as `WAIT_USER`.
5. Static plan tasks followed by dynamic tasks, selecting the first dependency-ready task.
6. All terminal, returned as `COMPLETE`.
7. Otherwise `NOOP/no_valid_next_action`.

The policy has no question-resolution priority and treats both
`execution_identity_mismatch` and `no_valid_next_action` as ordinary `NOOP`
decisions.

## Current Artifact Ownership

Agent results are returned as `DomainArtifact`, but the Scheduler only:

- deterministically computes an artifact ref;
- commits that ref to the invocation ledger;
- appends `ExecutionArtifactRef` to `ExecutionState`;
- copies the full result into `ExecutionState.latest_observation.artifact`.

There is no neutral durable business `ArtifactStore`/`ArtifactReader` used by
the Scheduler. An artifact ref can therefore exist without a durable payload.

Answers are not artifacts. The full answer remains in
`latest_observation.payload.answer_text` and in the legacy SessionStore.

## Current Commit Contract

`CURRENT_INVOCATION_COMMIT_DECISION` is:

```text
strategy = TRANSACTIONAL_OUTBOX
state_and_ledger_same_store = false
artifact_metadata_same_store = false
outbox_name = runtime_outbox
logical_effect_guarantee = true
external_provider_exactly_once = false
```

Current agent commit order is:

```text
ledger.prepare
-> lease acquire / mark running
-> ExecutionState task RUNNING
-> AgentInvocationPort.invoke
-> derive artifact_ref (payload is not durably stored here)
-> ledger.commit(artifact_ref)
-> ExecutionState task COMPLETED + artifact ref + payload observation
```

The committed-ledger/state-missing window is recoverable without reinvocation.
The artifact-saved/ledger-missing window is not meaningful yet because this path
does not save a durable artifact payload.

Current answer commit order is:

```text
durable command enqueue
-> ExecutionState clear wait + ANSWER_RECEIVED payload
-> SessionStore mutation
```

These writes are not one MA9 atomic business commit unit.

## Current Budget Contracts

Immutable `ExecutionConstraints` currently contains only:

```text
max_scheduler_steps
max_tasks
max_concurrency
max_wall_time_seconds
```

`SchedulerBudget` exposes remaining counters for agent calls, replans, retries,
follow-ups, questions, tokens, and elapsed time, but `ExecutionState` persists
only `scheduler_step_count`. It has no canonical usage counters and no
`followups_by_question` scope. Dynamic task registration and budget consumption
are not one state transition.

## Current Runtime Binding

`ExecutionPathRouter` durably binds only `OLD` or `NEW`.
`SchedulerProductionEntry.start()` claims `NEW`, then stores the plan/state.
`ExecutionPlan` has no `orchestration_version`; the API reports the descriptive
string `scheduler-v1`, but it is not an immutable execution contract. A
pre-MA9 NEW execution therefore cannot be distinguished from a future MA9 NEW
execution by the canonical plan.

## Current Streaming Ownership

The legacy answer stream is owned by the HTTP response generator:

```text
StreamingTurnService.iter_events
-> SessionStore.stream_followup
-> ExaminerAgent.stream_followup
-> AgentExecutionRunner.stream
-> provider iterator
```

Closing the consumer raises `GeneratorExit` into `AgentExecutionRunner._stream`,
which records:

```text
status = cancelled
fallback_reason = client_disconnected
```

The durable legacy event stream is a database-polling observer, but the MA9
Scheduler Examiner invocation does not yet own a detached stream lifecycle.

## RED Evidence

Executable evidence is in
`tests/contracts/test_ma9_t00_current_gaps.py`:

| Test | Current evidence | Future GREEN condition |
|---|---|---|
| `test_current_answer_does_not_dispatch_reviewer` | Reviewer count is zero; evaluation is compatibility-skipped | Reviewer count is one; evaluation is not skipped |
| `test_current_evaluation_completion_can_unblock_next_main_too_early` | `main:2:q2` is immediately dispatchable | unresolved `resolve:1:q1` blocks it |
| `test_current_answer_is_not_recoverable_as_durable_artifact` | no answer ref; payload copied to latest observation | deterministic durable AnswerArtifact ref |
| `test_current_dynamic_task_does_not_create_followup_evaluation_pair` | one follow-up task only | atomic follow-up/evaluation pair |
| `test_current_stream_disconnect_cancels_or_terminates_generation` | disconnect marks invocation cancelled | disconnect only unsubscribes observer |

Verification:

```text
python -m pytest tests/contracts/test_ma9_t00_current_gaps.py -q
5 passed

python -m pytest \
  tests/contracts/test_production_entry_cutover_contract.py \
  tests/contracts/test_scheduler_wait_user_integration_contract.py \
  tests/contracts/test_adaptive_interview_e2e.py \
  tests/contracts/test_atomic_commit_decision_contract.py \
  tests/unit/test_agent_runtime.py -q
30 passed
```

Broader baseline runs:

```text
python -m pytest tests/contracts -q
1062 passed, 3 skipped, 3 failed

Failures: protected PostgreSQL tests require missing POSTGRES_TEST_APPROVAL_ID.

python -m pytest tests/architecture -q
125 passed, 4 failed

Failures: four checked-in scan/report artifacts do not match the current app
tree metadata/LOC. The MA9-T00 change does not modify app/ and did not regenerate
unrelated frozen architecture baselines.
```

The repository `.venv` does not contain pytest; verification used the system
Python 3.11 environment already configured for this workspace.

## Stage Report

Changed Files:

```text
tests/contracts/test_ma9_t00_current_gaps.py
artifacts/multi-agent/MA9-T00-baseline.md
```

Before Behavior: undocumented production gaps.

After Behavior: unchanged production behavior with executable defect evidence.

Contract Changes: none.

RED -> GREEN Evidence: five current-gap assertions established; GREEN is
reserved for T02/T03/T04/T07.

Crash / Replay Evidence: existing command and invocation replay contracts were
inspected; missing answer/artifact payload recovery is recorded above.

Production Path Evidence: `SchedulerProductionEntry` behavior is exercised,
including the compatibility skip.

Architecture Impact: test and documentation only.

LOC / Complexity Delta: production modules `+0`; `scheduler.py` remains 1229
LOC, `runtime/composition.py` remains 2882 LOC.

Remaining Risks: all MA9 semantic, artifact, versioning, and streaming gaps are
still open by design. The protected PostgreSQL gate is not authorized in this
environment, and pre-existing architecture scan artifacts are stale.

Gate Decision:

```text
MA9_T00_BASELINE = PASS
MA9_T01_CONTRACT_FREEZE = READY_FOR_REVIEW
MA9_T02_PRODUCTION_CUTOVER = BLOCKED
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```
