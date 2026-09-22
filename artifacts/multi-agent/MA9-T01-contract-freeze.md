# MA9-T01 Contract Freeze Proposal

Date: 2026-09-22

```text
PHASE = MA9-T01
STATUS = APPROVED
PRODUCTION_CUTOVER = AUTHORIZED_FOR_T02
MA9_CONTRACT_GATE = PASS
```

This document freezes the approved MA9 v1 semantics. The user authorized
continuation on 2026-09-22, satisfying the human Contract Gate for T02.

## 1. Artifact Store Contract

One neutral durable store owns all MA9 business artifacts:

```text
QuestionArtifact
AnswerArtifact
EvaluationArtifact
InterviewEvaluationArtifact
ReportArtifact
```

Required port operations:

```text
put_if_absent(artifact_ref, artifact) -> STORED | REPLAY
get_required(artifact_ref) -> DomainArtifact | ArtifactMissing
exists(artifact_ref) -> bool
```

`put_if_absent` is idempotent for a byte-equivalent canonical payload and raises
`ArtifactPayloadConflict` for the same ref with a different payload.
`get_required` always fails closed. ExecutionState stores refs and bounded
status/summary fields only, never complete answer, evaluation, transcript, or
report payloads.

## 2. AnswerArtifact v1

Exact schema:

```text
artifact_type = answer-artifact
schema_version = answer-artifact-v1
artifact_ref: str
execution_id: str
question_id: str
source_task_id: str
answer_kind: MAIN | FOLLOWUP
answer_text: str
command_id: str
wait_id: str
submitted_revision: int
submitted_at: RFC3339 UTC timestamp
```

`submitted_revision` is the accepted command's `expected_revision`, equal to
the active wait's `issued_revision`; it is not the post-commit state revision.
`submitted_at` is assigned when the durable command is first accepted and is
stored with that command. Recovery reuses the original timestamp and never
generates a new one.

Identity is deterministic:

```text
identity_input = canonical JSON UTF-8 array [execution_id, command_id]
artifact_ref = answer/sha256:{lowercase SHA-256 hex(identity_input)}
```

The same `(execution_id, command_id)` must resolve to the same ref. Reusing the
identity with a different answer payload is `command_payload_conflict` and must
not overwrite the artifact.

## 3. Answer Atomic Acceptance

The accepted command, AnswerArtifact persistence intent, state transition, and
outbox intent form one logical commit:

```text
durable UserCommand
+ AnswerArtifact or artifact persistence intent
+ ExecutionState.artifact_refs append
+ current_wait_handle clear
+ evaluation eligibility
+ revision increment
+ runtime_outbox effect
```

Production uses the existing `TRANSACTIONAL_OUTBOX/runtime_outbox` decision.
No MA9-specific transaction manager or commit service is allowed. In-memory
adapters provide the same logical atomicity under one lock/critical section.

Recovery rules:

| Crash window | Required recovery |
|---|---|
| command exists, answer missing | replay command, derive ref, materialize artifact, continue commit |
| answer exists, state missing ref | validate payload, attach ref, clear the matching wait, activate evaluation |
| state committed, event undelivered | outbox redelivery |
| event delivered twice | idempotent consumer by effect identity |

An ordinary `REPLAY` response is not sufficient while any required effect is
missing.

## 4. Agent Artifact Commit Extension

`CURRENT_INVOCATION_COMMIT_DECISION` remains authoritative:

```text
strategy = TRANSACTIONAL_OUTBOX
outbox_name = runtime_outbox
```

For production, the logical unit is:

```text
artifact persistence intent/payload
+ invocation-ledger commit
+ ExecutionState task COMPLETED
+ ExecutionState artifact ref
+ outbox effect
```

The effect identity is the existing logical invocation identity
`(execution_id, task_id, logical_attempt)`. Recovery rules are:

- artifact present / ledger uncommitted: validate deterministic ref, commit ledger, then state;
- ledger committed / state absent: require artifact payload, project state without reinvocation;
- state references missing payload: raise `ArtifactRecoveryRequired`, mark execution non-progressable, and never fabricate the payload;
- duplicate outbox delivery: no duplicate state transition or artifact.

## 5. EvaluationArtifact vNext

Exact schema version: `evaluation-artifact-v2`.

```text
question_id: str
answer_artifact_ref: str
question_artifact_ref: str
evaluation_status: EVALUATED | DEGRADED
evidence_status: SUFFICIENT | INSUFFICIENT | UNDETERMINED
score: int[0..100] | null
confidence: float[0..1] | null
gap: null | {
  gap_id: str,
  type: str,
  focus: str,
  reason: str
}
evidence_refs: tuple[str, ...]
summary: str
evaluation_policy_version: str
schema_version: evaluation-artifact-v2
```

Legal matrix:

| evaluation_status | evidence_status | score | Scheduler action |
|---|---|---|---|
| `EVALUATED` | `SUFFICIENT` | required | complete resolution gate |
| `EVALUATED` | `INSUFFICIENT` | null | register pair or exhaust budget |
| `DEGRADED` | `UNDETERMINED` | optional/null | retry Reviewer, then degraded resolution |

All other combinations are contract-invalid. Provider, timeout, adapter, and
parse failures are task/ledger failures and do not create a fake EvaluationArtifact.
`EVALUATED + INSUFFICIENT` requires a non-null gap; `EVALUATED + SUFFICIENT`
requires a null gap. The referenced question and answer artifacts must exist
before the evaluation artifact can be committed.

## 6. QuestionResolutionGate

`ExecutionTaskDefinition` gains a frozen `task_kind` discriminator:

```text
AGENT
QUESTION_RESOLUTION_GATE
```

For `AGENT`, capability/agent/skill contracts remain required. For a gate,
`agent_id`, `skill`, input contract, and output contract are absent and the
definition contains:

```text
task_id = resolve:{position}:{question_id}
task_kind = QUESTION_RESOLUTION_GATE
capability = scheduler.question-resolution
parameters = {
  question_id,
  position,
  resolution_policy_version: question-resolution-ma9-v1
}
```

A gate never invokes `AgentInvocationPort`, consumes agent-call budget, or
creates a ledger entry. Only the canonical Scheduler/domain policy can move it
to `COMPLETED`.

Static DAG:

```text
main:N:qN -> evaluate:N:qN -> resolve:N:qN -> main:N+1:qN+1
last resolve -> evaluate:interview -> report:interview
```

An evaluation task completing does not make the gate ready or complete. The
gate completes only for:

- EVALUATED + SUFFICIENT;
- EVALUATED + INSUFFICIENT with per-question or total follow-up budget exhausted, after recording an unresolved gap ref/marker;
- a follow-up evaluation returning EVALUATED + SUFFICIENT;
- DEGRADED + UNDETERMINED after Reviewer retries are exhausted, after recording `evaluation_degraded`.

While a gate is unresolved, all later main questions are non-dispatchable.

## 7. Dynamic Follow-up Pair

One insufficient evaluation proposes exactly one atomic pair:

```text
followup:{question_id}:{ordinal}
evaluate-followup:{question_id}:{ordinal}
```

Identity tuple:

```text
execution_id
parent_review_task_id
question_id
replan_ordinal
task_role = FOLLOWUP | FOLLOWUP_EVALUATION
```

Dependencies and routing:

```text
parent review -> followup
followup -> evaluate-followup
evaluate-followup -> resolution policy for resolve:{position}:{question_id}
```

Both definitions, both runtime states, and all budget increments are committed
in one `ExecutionState.register_followup_pair()` transition. Replaying the same
identity returns the existing pair without consuming budget. A partial pair or
counter mismatch is an invariant failure/recovery condition, never a new pair.

## 8. Budget Scope and Consumption

Immutable limits in `ExecutionPlan.execution_constraints`:

```text
max_followups_total
max_followups_per_question
max_replans_total
max_agent_calls
max_scheduler_steps
max_retries
execution_timeout_seconds
```

Mutable usage in `ExecutionState`:

```text
followups_total_used
followups_by_question: dict[question_id, int]
replans_used
agent_calls_used
scheduler_steps_used
retries_used
```

Consumption points:

- follow-up/replan: in the successful pair-registration transition;
- agent call: once after ledger prepare and lease acquisition grant execution authority;
- completed ledger replay: no new agent-call charge;
- scheduler step: once per accepted canonical transition, never for reads/previews.

## 9. Immutable Runtime Version

`ExecutionPlan` gains required immutable `orchestration_version`.

```text
new MA9 execution = scheduler-ma9-v1
pre-MA9 NEW execution = scheduler-v2-compat (migration/default on old rows only)
```

`execution_path` remains `OLD | NEW`. Admission flag `MA9_ADMISSION_ENABLED`
selects the version only when an execution is created. Turning the flag off
stops new MA9 admission but never changes or reroutes an existing execution.

## 10. NOOP and Boundary Semantics

`run_until_boundary()` is a bounded driver over the canonical Scheduler.
Externally visible normal boundaries are:

```text
WAIT_USER
PENDING
COMPLETE
FAILED
```

Reason handling:

| Reason | Contract |
|---|---|
| `awaiting_observation` | `PENDING`; no failure |
| `no_valid_next_action` | raise `SchedulerInvariantError` with plan/state/gate/ready diagnostics |
| `execution_identity_mismatch` | raise hard `SchedulerDispatchError` |
| internal step limit | raise/return `SCHEDULER_BOUNDARY_EXCEEDED` |

An answer while the question task is `RUNNING`, before durable question
artifact plus WAIT_USER, returns HTTP 409 with `QUESTION_NOT_READY`.

## 11. Session Projection Boundary

The eventual `_project_execution_to_session_view()` may read ExecutionState and
ArtifactReader and write the legacy read model. It may not skip tasks, complete
gates, select questions, evaluate answers, or finish the interview.

Final report assembly reads durable Question, Answer, and Evaluation artifacts.
It must not reconstruct the interview from `latest_observation`. Execution can
become `COMPLETED` only after durable ReportArtifact and completed report task.

## 12. Streaming Event Envelope

`AgentStreamEvent` schema version is `agent-stream-event-v1` with event types
`STARTED | DELTA | COMPLETED | FAILED`.

Common required fields:

```text
schema_version
event_type
execution_id
task_id
logical_attempt
agent_id
skill
stream_id
sequence
event_id
question_id
emitted_at
```

Conditional fields:

```text
DELTA: delta
COMPLETED: final_text + artifact_ref
FAILED: error_code + retryable
```

Identity and ordering:

```text
stream_id input = canonical JSON [execution_id, task_id, logical_attempt]
stream_id = stream/sha256:{lowercase SHA-256 hex(input)}
sequence = 1, 2, 3... within one stream
event_id = {stream_id}:{sequence}
```

`COMPLETED.final_text` equals the canonical durable artifact text.

## 13. Streaming Ownership

The Scheduler/runtime worker owns provider generation and final artifact commit.
SSE clients are observers only. Disconnect, slow consumption, queue overflow,
and observer callback failure may unsubscribe that observer but cannot cancel or
fail the invocation.

Each observer buffer is bounded to 128 events. Overflow closes that observer
with a reconnect signal; it does not block provider generation. MA9 v1 recovery
guarantees final artifact retrieval by snapshot after reconnect. Exact missing
token replay and durable token logs are explicit non-goals.

## Contract Review Checklist

```text
ANSWER_ARTIFACT_CONTRACT = PROPOSED_FROZEN
ANSWER_COMMIT_STRATEGY = PROPOSED_FROZEN
ARTIFACT_STORE_CONTRACT = PROPOSED_FROZEN
INVOCATION_COMMIT_EXTENSION = PROPOSED_FROZEN
EVALUATION_ARTIFACT_VNEXT = PROPOSED_FROZEN
EVALUATION_STATE_MATRIX = PROPOSED_FROZEN
QUESTION_RESOLUTION_GATE = PROPOSED_FROZEN
DYNAMIC_TASK_PAIR = PROPOSED_FROZEN
PAIR_IDEMPOTENCY = PROPOSED_FROZEN
BUDGET_SCOPE = PROPOSED_FROZEN
BUDGET_ATOMICITY = PROPOSED_FROZEN
ORCHESTRATION_VERSION_BINDING = PROPOSED_FROZEN
NOOP_SEMANTICS = PROPOSED_FROZEN
STREAM_EVENT_ENVELOPE = PROPOSED_FROZEN
STREAM_OWNERSHIP = PROPOSED_FROZEN
```

## Stage Report

Changed Files:

```text
artifacts/multi-agent/MA9-T01-contract-freeze.md
```

Before Behavior: MA9 contracts existed only as plan requirements with several
identity, port, recovery, and version details unresolved.

After Behavior: all 17 T01 contract areas have exact proposed semantics and
identities ready for review; production behavior is unchanged.

Contract Changes: proposed AnswerArtifact v1, EvaluationArtifact v2,
QuestionResolutionGate, pair registration, budgets, version binding, boundary
errors, artifact access, and stream envelope.

RED -> GREEN Evidence: T00 RED evidence is linked to the future contracts;
production GREEN work is intentionally blocked.

Crash / Replay Evidence: recovery behavior is frozen for answer, agent artifact,
outbox, and pair registration windows.

Production Path Evidence: no production path changed in T01.

Architecture Impact: proposed changes reuse Scheduler, ExecutionState,
runtime_outbox, invocation ledger, AgentInvocationPort, and one neutral artifact
store boundary. No second workflow engine or streaming orchestrator is proposed.

LOC / Complexity Delta: production modules `+0`.

Remaining Risks: Postgres atomic implementation, migrations, compatibility row
defaults, artifact payload storage, and detached streaming worker all remain for
later authorized phases. Protected PostgreSQL verification is deferred because
the required approval identifier is unavailable; four unrelated frozen
architecture scan artifacts are already stale against the current app tree.

Gate Decision:

```text
MA9_CONTRACT_GATE = PASS
T02_PRODUCTION_CUTOVER = AUTHORIZED
NEXT_REQUIRED_ACTION = EXECUTE_MA9_T02
POSTGRES_RELIABILITY_VERIFICATION = DEFERRED
```
