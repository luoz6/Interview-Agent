# LangGraph Interview Interruption Recovery Design

Status: APPROVED_FOR_SPEC_REVIEW

Date: 2026-07-22

## 1. Context

The current runtime uses LangGraph as a two-node phase router. The actual
interview loop, command idempotency, state persistence, version checks, and
HTTP resume behavior are implemented by InterviewGraphRunner and the memory or
PostgreSQL session stores. The application therefore has a versioned resume
contract, but it does not use LangGraph checkpoints, threads, interrupts, or
durable execution.

The first deeper LangGraph adoption will solve interview interruption recovery.
It will cover browser refresh, network loss, API process loss, graph worker
loss, Examiner failures, and interrupted follow-up streaming. It will not claim
that a provider stream can resume from an exact token after the provider
connection is lost.

## 2. Decisions

The approved design decisions are:

1. Recovery is message-level, not token-level. A lost provider stream is
   regenerated as one complete follow-up instead of being concatenated with a
   partial prior attempt.
2. Full recovery is a PostgreSQL production capability. The memory runtime is
   retained for local development and does not promise process-loss recovery.
3. Only sessions created after rollout are assigned to the new engine. Existing
   sessions are not migrated or backfilled into checkpoints.
4. New sessions use LangGraph as the workflow write model. Existing business
   tables become read projections and immutable content stores, not a second
   workflow authority.
5. Review generation remains on the current report job pipeline. This stage
   ends the graph after it durably emits the report request.
6. Streaming execution is detached from the SSE connection. Client disconnect
   cannot cancel the graph worker.

## 3. Goals

This work will:

1. Persist every accepted answer before acknowledging command durability.
2. Resume a new-engine interview from the last successful graph step after
   API or worker process loss.
3. Replay persisted streaming chunks after browser refresh or network loss.
4. Regenerate a complete follow-up after an unrecoverable provider-stream loss.
5. Preserve optimistic concurrency and command idempotency.
6. Make provider retry, degraded fallback, and generation replacement visible
   and auditable.
7. Keep legacy sessions operational throughout rollout and rollback.
8. Prevent raw JD, resume, answer, and evidence content from being copied into
   checkpoint metadata, runtime traces, or generation control records.

## 4. Non-Goals

This work will not:

- Migrate an active or completed legacy interview into LangGraph.
- Promise exact-token continuation after the provider stream is lost.
- Move final report evaluation or report jobs into LangGraph.
- Add dynamic Agent delegation, Agent debate, or parallel Reviewer graphs.
- Make the memory runtime durable across process restarts.
- Replace the current HTTP and SSE public transport with WebSocket.
- Claim exactly-once execution of an external model call.
- Adopt LangGraph Agent Server or change the deployment platform.

## 5. Considered Approaches

### 5.1 Checkpoint cursor beside the current Session Store

LangGraph would save only an execution cursor while the current Session Store
continued to own complete workflow state. This minimizes initial code changes,
but it creates two workflow authorities. A checkpoint could say that Examiner
generation is pending while the Session Store already contains a committed
follow-up. Recovery would require reconciliation rules for every step.

Rejected because it preserves the current architectural inconsistency and
creates unsafe dual writes.

### 5.2 LangGraph write model with business read projections

LangGraph checkpoints own the workflow state and execution position for new
sessions. Existing session, message, and report-facing tables expose query
projections and immutable content. Projection writes are idempotent and can be
replayed from graph state and content references.

Selected because it gives checkpoint recovery one source of workflow truth
while retaining the current API and report integration boundaries.

### 5.3 LangGraph Agent Server migration

Agent Server could own checkpoint and execution infrastructure. It would also
change deployment, operations, APIs, and observability at the same time.

Rejected for this stage because it expands far beyond interview interruption
recovery.

## 6. Architecture

The production command and streaming path is:

```text
HTTP Command API
      | persist command_id, expected_version, and answer reference
      v
Command Inbox and Runtime Outbox
      | leased background consumption
      v
LangGraph Interview Graph
      | thread_id = session_id
      | PostgreSQL checkpointer
      +----------------------> Session and Message Read Projection
      |
      +----------------------> Generation Attempts and Chunks
                                  |
                                  +----> replayable SSE
```

The API persists a command before scheduling graph execution. A process loss
between HTTP acceptance and graph invocation therefore cannot lose the answer.
The graph worker is independent of the request and SSE processes.

Every session stores an immutable engine selection:

| Field | Contract |
| --- | --- |
| workflow_engine | `legacy` or `langgraph-v1`. |
| graph_schema_version | Graph and state contract version for new-engine sessions. |
| session_id | Also used as the LangGraph thread ID. |

Routing uses `workflow_engine`, never a creation-time comparison. Disabling the
feature flag stops assigning new sessions to langgraph-v1 but does not reroute
existing langgraph-v1 threads to legacy code.

## 7. State Ownership and Schema

LangGraph owns transition state and the execution cursor. Business tables own
immutable text artifacts and query projections. Reading an immutable answer or
plan artifact from a business table does not make that table a workflow state
authority.

The checkpointed state is JSON serializable and contains references instead of
repeated sensitive text:

```text
InterviewGraphState
|-- identity
|   |-- session_id
|   |-- workflow_engine
|   `-- graph_schema_version
|-- interview
|   |-- plan_snapshot_id
|   |-- current_question_id
|   |-- message_ids
|   |-- skipped_question_ids
|   `-- interview_status
|-- execution
|   |-- pending_action
|   |-- generation_id
|   |-- generation_attempt
|   `-- last_error_code
`-- concurrency
    |-- state_version
    `-- last_command_id
```

JD, resume, plan, candidate answers, interviewer messages, and knowledge
evidence remain in access-controlled content storage. References include a
stable identifier and content hash. Nodes load only the content needed for the
current operation.

`state_version` remains the public optimistic-concurrency value. The LangGraph
checkpoint ID is an internal execution cursor and is not presented as the same
concept. The current behavior that mirrors `checkpoint_version` from
`state_version` is removed for new-engine sessions.

## 8. Graph Topology

The v1 graph is:

```text
START
  |
  v
initialize_session
  |
  v
project_state
  |
  v
wait_for_answer ------------------------------ interrupt
  |
  | Command(resume={command_id, expected_version, answer_ref})
  v
validate_command
  |-- duplicate -----------------------------> return_current_state
  |-- version conflict ----------------------> reject_command
  `-- accepted
        |
        v
append_candidate_answer
        |
        v
decide_next_action
  |-- follow_up
  |     |
  |     v
  |   prepare_generation
  |     |
  |     v checkpoint
  |   generate_followup
  |     |
  |     v
  |   classify_generation
  |     |-- completed ------------------------> commit_interviewer_message
  |     |-- retryable and under limit --------> prepare_retry --+
  |     `-- terminal or exhausted ------------> fallback_followup |
  |                                                              |
  |<-------------------------------------------------------------+
  |
  |-- next_question --------------------------> commit_next_question
  `-- finish_interview -----------------------> emit_report_event
                                                  |
                                                  v
                                                project_state
                                                  |-- active -> wait_for_answer
                                                  `-- finished -> END
```

`wait_for_answer` is a pure interrupt node. It performs no database write,
event publication, model call, or other side effect before `interrupt()`. On
resume, LangGraph restarts the node and returns the resume payload from the
interrupt call. All side effects occur in later idempotent nodes.

The resume payload contains an answer reference rather than raw answer text:

```json
{
  "command_id": "cmd-...",
  "expected_version": 4,
  "answer_ref": "answer-..."
}
```

## 9. Command Ingress

The API command transaction:

1. Validates the public request shape.
2. Inserts the answer into controlled content storage.
3. Inserts a command inbox record keyed by `(session_id, command_id)`.
4. Inserts the corresponding runtime outbox event in the same transaction.
5. Commits, then returns accepted command metadata.

The worker claims the outbox event with a lease and invokes the graph using the
session thread ID. At-least-once delivery is expected. Duplicate delivery sees
the same command inbox identity and returns the already applied state.

Two concurrent commands with the same expected version may both enter the
inbox, but only one can pass graph version validation. The other is marked as a
stable version conflict and is not appended to the conversation.

## 10. Generation Attempts and Streaming

Provider streaming is not tied to an HTTP generator. The graph worker writes
coalesced chunks to a generation store and SSE reads from that store.

Generation identity consists of:

| Field | Contract |
| --- | --- |
| generation_id | Stable identity for one logical follow-up. |
| attempt_number | Starts at 1 and increases for provider retry or regeneration. |
| sequence | Monotonic within one attempt. |
| status | pending, running, completed, failed, or abandoned. |
| lease fields | Worker identity, heartbeat, and expiry. |
| error_code | Stable classification; never raw exception text. |

Provider chunks are coalesced into approximately 100 to 250 millisecond
batches before persistence. This bounds database write volume while keeping
visible streaming latency low. A slow or disconnected SSE reader cannot apply
backpressure to the graph worker.

The client reconnects with generation ID, attempt number, and last sequence.
The server replays later chunks, then continues live delivery. If the provider
connection remains alive, browser refresh and network loss do not cause
regeneration.

If the worker or provider stream is lost, the active attempt lease expires. A
recovering worker marks that attempt abandoned, increments attempt_number, and
emits `generation_reset`. The client clears the partial prior attempt before
displaying replacement chunks. Chunks from different attempts are never
concatenated.

Only a completed full follow-up may advance to
`commit_interviewer_message`. The final message, decision, and next public
state version are therefore committed as one logical graph transition.

## 11. Failure Classification and Retry

Failures are classified at the node boundary:

| Class | Examples | Behavior |
| --- | --- | --- |
| Infrastructure interruption | Process exit, temporary database loss | Do not advance checkpoint; recover the step. |
| Retryable provider error | Timeout, rate limit, temporary 5xx | Record attempt and route to bounded retry. |
| Terminal provider error | Authentication, missing model, persistently invalid response | Route to fallback follow-up. |
| State or code invariant failure | Missing references, invalid hashes, unknown state | Stop the graph and alert. |

Provider generation permits at most three attempts. Retry availability is
stored as `available_at`; workers reschedule the graph instead of blocking a
worker thread with a long sleep. Exhausted or terminal provider failure routes
to the existing template follow-up and commits a degraded, auditable result.

Unexpected programming or state errors are not converted into a successful
template response. They leave the thread at its last checkpoint for operator
diagnosis and retry.

AgentExecutionRunner remains the observation boundary for one Agent attempt.
It does not immediately hide Examiner failures behind fallback in the new
engine. Graph routing owns retries and final fallback. Execution context gains
`parent_run_id` so nested Orchestrator and Examiner attempts form a real call
tree instead of sharing only one command causation ID.

## 12. Idempotent Side Effects

The required unique identities are:

| Side effect | Unique identity |
| --- | --- |
| User command | `(session_id, command_id)` |
| Generation attempt | `(generation_id, attempt_number)` |
| Stream chunk | `(generation_id, attempt_number, sequence)` |
| Message commit | `(session_id, source_command_id, role)` |
| State projection | `(session_id, state_version)` |
| Interview-finished event | `(session_id, finished_state_version)` |

The LangGraph checkpointer and application tables do not depend on one
distributed transaction. A node performs an idempotent side effect before it
returns its state update. If the side effect commits but checkpoint persistence
fails, rerunning the node reads and reuses the existing result through its
unique identity.

External provider invocation cannot be exactly once. A crash after provider
work but before durable result commit may repeat the call. It must not create a
second committed message or merge incompatible attempts.

## 13. Read Projection

The existing session API continues to read a session projection. The projection
contains the public interview status, current question, messages, pending
generation metadata, state version, and review status expected by current UI
and report code.

`project_state` is idempotent by `(session_id, state_version)`. If projection
succeeds but its graph checkpoint fails, rerunning the node performs no second
business transition. A projection lag may temporarily make GET return an older
state version, but it can be repaired from the graph state and immutable
content references.

Final report workers continue to consume the complete projected session. The
finish branch inserts the report request into the existing durable report job
boundary before the graph reaches END.

## 14. Version Compatibility

Paused threads contain node names and state schema. Graph changes therefore use
side-by-side versions instead of destructive in-place changes.

- Every new-engine session is pinned to `graph_schema_version`.
- The code can load langgraph-v1 for as long as an active v1 thread exists.
- A breaking graph change introduces langgraph-v2 and affects only sessions
  assigned to v2.
- Nodes referenced by active checkpoints are not renamed or removed.
- v1 code is removed only after there are no active or recoverable v1 threads
  within the retention window.
- Checkpoints use JSON-compatible serialization; pickle fallback is disabled.

The project will pin and test a compatible LangGraph v1 and PostgreSQL
checkpointer package combination instead of retaining an open-ended minimum
dependency range.

## 15. Privacy and Retention

Checkpoint state, command control records, generation metadata, chunks,
AgentRunRecord metadata, and application traces are subject to explicit privacy
tests.

- Checkpoints store identifiers, hashes, status, and counters, not raw JD,
  resume, answer, prompt, evidence, or provider response text.
- Generation chunks necessarily contain the follow-up being streamed. Their
  table uses the same access control as interview messages and is not exposed
  by runtime diagnostic APIs.
- Completed generation chunks are retained for 24 hours by default. The final
  follow-up remains in the message store.
- Session deletion cascades or explicitly deletes checkpoints, inbox commands,
  outbox work, attempts, chunks, projections, and content artifacts.
- Stable error codes are persisted; raw exception text remains restricted to
  protected application logs.

## 16. Recovery Semantics

| Interruption | Required behavior |
| --- | --- |
| Browser refresh | Load projected state and replay chunks after the last sequence. |
| Network or SSE loss | Continue worker execution and replay missed chunks on reconnect. |
| API loss after command commit | Inbox and outbox retain the answer command for later consumption. |
| Worker loss between nodes | Resume from the last successful PostgreSQL checkpoint. |
| Worker loss during provider stream | Abandon expired attempt, emit reset, and regenerate the full follow-up. |
| Temporary provider failure | Retry within the bounded attempt policy. |
| Persistent provider failure | Commit the degraded template follow-up. |
| Duplicate answer command | Return the existing command result without another message. |
| Concurrent answer commands | Accept one matching state version and reject the stale command. |

The user-visible guarantee is a complete final message and a non-duplicated
conversation. The system does not guarantee that a regenerated follow-up is
textually identical to the abandoned partial attempt. Regeneration pins the
model revision, prompt version, evidence snapshot, and low-temperature policy
to reduce variation and support fairness audits.

## 17. Testing Strategy

### 17.1 Unit tests

- Graph topology and every conditional edge.
- Pure `wait_for_answer` interrupt behavior.
- Command duplicate and version-conflict routing.
- Provider failure classification and the three-attempt cap.
- Generation reset and client attempt replacement rules.
- Every idempotency-key contract.
- Workflow engine and graph version routing.
- Checkpoint and trace privacy sanitization.

### 17.2 PostgreSQL integration tests

Use a real PostgreSQL checkpointer and application schema. Force process loss
at each boundary:

1. Command inbox committed before graph dispatch.
2. Candidate artifact written before node checkpoint.
3. Generation attempt prepared before provider invocation.
4. Partial chunks persisted during provider streaming.
5. Complete provider result saved before graph checkpoint.
6. Session projection committed before graph checkpoint.
7. Report event inserted before graph END.

After restarting a worker, every test verifies:

- No accepted answer is lost or duplicated.
- Exactly one final interviewer message is committed.
- Chunks from abandoned and replacement attempts are not concatenated.
- Public state_version increases monotonically.
- One effective report request exists.
- The thread resumes from the expected node.

### 17.3 Browser acceptance

- Refresh during streaming and verify replay plus continued output.
- Disconnect and reconnect SSE using the last sequence.
- Kill the generation worker and verify `generation_reset` clears partial UI.
- Repeat the same HTTP command and verify one candidate message.
- Submit from two pages and verify one explicit version conflict.
- Resume a legacy session after the new engine is deployed.

### 17.4 Regression gates

- Existing interview command and persistence tests.
- Existing report job and report generation tests.
- Agent trace and privacy tests.
- Knowledge evidence continuity and hash validation tests.
- Deterministic Playwright flow.
- Full Python suite and frontend static checks.

## 18. Acceptance Criteria

This design is accepted in implementation only when:

1. An answer acknowledged as durably accepted has RPO 0 under API or worker
   process loss.
2. Normal process restart resumes an active interview within 30 seconds.
3. All fault-injection cases produce zero duplicate committed messages.
4. SSE reconnect replays every persisted chunk after the client sequence.
5. A lost provider stream produces a reset and one complete replacement
   message, never concatenated output.
6. Permanent provider failure reaches template fallback after bounded retry.
7. State projection can be replayed without a second business transition.
8. Checkpoints and diagnostic metadata contain no raw JD, resume, candidate
   answer, knowledge evidence, or provider response.
9. Legacy sessions continue through the legacy engine without migration.
10. New session assignment can be disabled without stranding existing
    langgraph-v1 threads.

## 19. Rollout and Rollback

1. Deploy schema changes, the PostgreSQL checkpointer, v1 graph worker,
   cleanup jobs, and monitoring with no real session assignment.
2. Run the complete fault-injection suite and internal test interviews.
3. Assign a small feature-flagged percentage of new sessions to langgraph-v1.
4. Increase assignment while monitoring completion rate, recovery latency,
   provider fallback rate, abandoned attempts, projection lag, and duplicate
   constraint violations.
5. Stop creating legacy sessions only after the new engine meets its gates.
6. Retain legacy workers until all legacy sessions have completed or expired.

Rollback disables new langgraph-v1 assignment. It does not change the engine
of an existing session. The v1 graph and workers remain available until all v1
threads have completed or expired.

## 20. Risks and Controls

| Risk | Control |
| --- | --- |
| Dual workflow state | LangGraph is the write model; tables are projections and immutable content. |
| Lost accepted command | Command inbox and outbox commit before HTTP acceptance. |
| Duplicate model call | Expected after some crashes; attempt and message keys prevent duplicate business output. |
| Partial stream corruption | Attempt-scoped sequences and mandatory generation reset. |
| Checkpoint and table transaction gap | Every node side effect is idempotent and reusable. |
| Paused thread breaks after deploy | Pin graph version and retain old node definitions. |
| Checkpoint privacy growth | Reference-based state, JSON serialization, retention, and privacy tests. |
| Database load from chunks | Coalesce chunks and delete completed chunks after the replay window. |
| Rollback strands new sessions | Keep v1 workers running; rollback only assignment. |
| Scope expansion into report orchestration | End graph after durable report request emission. |

## 21. Implementation Boundary

The implementation plan will decompose this design into independently
reviewable stages:

1. Versioned engine routing and PostgreSQL checkpointer foundation.
2. Command inbox and durable graph resume.
3. Interview graph nodes and read projection.
4. Detached generation attempts, chunk replay, and SSE reconnection.
5. Failure routing, fallback, cleanup, and privacy enforcement.
6. Fault injection, browser acceptance, and gradual rollout controls.

No application implementation begins until this design has been reviewed and
a separate detailed implementation plan has been approved.

## 22. References

- LangGraph persistence:
  https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph interrupts:
  https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph subgraphs:
  https://docs.langchain.com/oss/python/langgraph/use-subgraphs
- LangGraph backward compatibility:
  https://docs.langchain.com/oss/python/langgraph/backward-compatibility
