# LangGraph Interview Interruption Recovery Design

Status: REVISED_FOR_SPEC_REVIEW

Date: 2026-07-22

Revised: 2026-07-23

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
4. New sessions use LangGraph as the workflow write model. The checkpoint
   inlines the bounded plan snapshot and conversation messages. Existing
   business tables become query projections, not a second workflow authority.
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
8. Keep raw JD, resume, and evidence content out of graph checkpoints, and keep
   all raw interview content out of runtime traces, generation control records,
   and diagnostic metadata. Checkpointed conversation text receives the same
   access control, deletion, and retention policy as interview messages.

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
projections. Projection writes are idempotent and can be replayed from graph
state.

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
      | persist command_id, expected_version, and answer text
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

LangGraph owns transition state and the execution cursor. Business tables are
query projections for API and report consumers. Command inbox rows temporarily
hold accepted request content until the graph validates and incorporates it.

The checkpointed state is JSON serializable and self-contained for the bounded
interview loop:

```text
InterviewGraphState
|-- identity
|   |-- session_id
|   |-- workflow_engine
|   `-- graph_schema_version
|-- interview
|   |-- plan_snapshot
|   |-- current_index
|   |-- messages
|   |-- skipped_question_ids
|   `-- interview_status
|-- execution
|   |-- generation_id
|   |-- generation_attempt
|   |-- expected_retry_attempt
|   |-- retry_resume_attempt
|   |-- next_retry_at
|   `-- last_error_code
`-- concurrency
    |-- state_version
    `-- last_command_id
```

The plan snapshot contains the ordered questions, focus metadata, evidence IDs,
and evidence hashes needed by the graph. It excludes raw JD, resume, and
knowledge evidence text. Messages are append-only and inline because one
interview has a small bounded conversation. This avoids loading the entire
message store in decide and Examiner nodes, keeps replay self-contained, and
accepts larger checkpoints as an explicit trade-off. Evidence content is still
loaded by ID and verified hash only when an Agent needs it.

`expected_retry_attempt` is durable retry-fencing state. It records the only
timer resume the paused thread may accept. `retry_resume_attempt` and the
command/generation outcome fields used by adjacent conditional edges are
transient routing values. `project_state` clears all such routing values after
the projection transaction has consumed them, so they cannot leak into a later
public transition.

There is no checkpointed `pending_action`. The graph cursor and interrupt
payload already encode pending execution. The API projection translates the
current StateSnapshot `next` nodes and interrupts into a public
`pending_action` value. This prevents a duplicated state field from drifting
away from the actual graph position.

`state_version` remains the public optimistic-concurrency value. The LangGraph
checkpoint ID is an internal execution cursor and is not presented as the same
concept. The current behavior that mirrors `checkpoint_version` from
`state_version` is removed for new-engine sessions.

Only `project_state` advances the public version. It deterministically
calculates `next_state_version = state.state_version + 1`, writes the projection
using `(session_id, next_state_version)` as its idempotency identity, and then
returns that value as the graph state update. If projection succeeds but
checkpoint persistence fails, the node restarts from the prior checkpoint,
calculates the same next version, reuses the existing projection row, and
returns the same update.

Public versions advance for session initialization, an accepted answer becoming
visible with its generation status, and a committed interviewer output or final
status. Provider retry nodes and chunk writes create operational checkpoints
but do not advance `state_version`. Duplicate and rejected commands also do not
advance it.

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
  | Command(resume={kind: answer_command, command_id})
  v
validate_command
  |-- duplicate ------------------------------> wait_for_answer
  |-- version conflict ------------------------> wait_for_answer
  `-- accepted
        |
        v
append_candidate_answer
        |
        v
decide_next_action
  |-- follow_up -> prepare_generation -> project_state
  |                                         |
  |                                         v
  |                                  generate_followup
  |                                         |
  |                                         v
  |                                  classify_generation
  |                                    |-- completed
  |                                    |      `-> commit_interviewer_message
  |                                    |                    |
  |                                    |                    v
  |                                    |              project_state
  |                                    |                    |
  |                                    |                    v
  |                                    |              wait_for_answer
  |                                    |
  |                                    |-- retryable and under limit
  |                                    |      `-> enqueue_retry
  |                                    |               |
  |                                    |               v
  |                                    |         wait_for_retry -- interrupt
  |                                    |               |
  |                                    |               | Command(resume={
  |                                    |               |   kind: retry_timer,
  |                                    |               |   generation_id,
  |                                    |               |   next_attempt_number
  |                                    |               | })
  |                                    |               v
  |                                    |         validate_retry
  |                                    |           |-- stale or mismatch
  |                                    |           |      `-> wait_for_retry
  |                                    |           `-- accepted
  |                                    |                  |
  |                                    |                  v
  |                                    |            prepare_retry
  |                                    |                  |
  |                                    |                  `-> generate_followup
  |                                    |
  |                                    `-- terminal or exhausted
  |                                           `-> fallback_followup
  |                                                     |
  |                                                     v
  |                                           commit_interviewer_message
  |                                                     |
  |                                                     v
  |                                               project_state
  |                                                     |
  |                                                     v
  |                                               wait_for_answer
  |
  |-- next_question -> commit_next_question -> project_state -> wait_for_answer
  `-- finish_interview -> project_state -> emit_report_event -> END
```

`wait_for_answer` is a pure interrupt node. It performs no database write,
event publication, model call, or other side effect before `interrupt()`. On
resume, LangGraph restarts the node and returns the resume payload from the
interrupt call. All side effects occur in later idempotent nodes.

The answer interrupt resumes with a command identity rather than raw answer
text. `validate_command` loads and validates the protected inbox row:

```json
{
  "kind": "answer_command",
  "command_id": "cmd-..."
}
```

`enqueue_retry` writes one idempotent retry outbox event with
`available_at`, calculated from PostgreSQL `NOW()`, and checkpoints its
`expected_retry_attempt`. `wait_for_retry` is a second pure interrupt node.
The leased outbox dispatcher polls due events and resumes that thread with a
`retry_timer` command. Before invoking the graph, the consumer rejects events
when the current cursor is not `wait_for_retry` or the expected attempt does
not match. `validate_retry` repeats that check inside the graph against
checkpointed state; it never trusts the resume payload to advance
`generation_attempt`. A stale or duplicate event returns to
`wait_for_retry` without changing generation state. LangGraph is not expected
to provide a sleep primitive, and graph workers never block while waiting for
backoff.

Every completed sequential node or superstep may write a PostgreSQL checkpoint.
A retry therefore creates checkpoints for failure classification, retry
scheduling, the timer interrupt, attempt preparation, and subsequent generation
work. Checkpoint counts represent durable node execution, not interview turns;
operations dashboards and retention estimates must use that interpretation.

`interview_status` is the only graph field used to distinguish active and
finished interviews; no parallel generic `status` field is introduced. The
conditional edge after `project_state` uses this fixed order:

1. `interview_status == "finished"` routes to `emit_report_event`.
2. An uncommitted `generation_id` routes to `generate_followup`.
3. Any other active state routes to `wait_for_answer`.

The commit and fallback nodes clear the uncommitted generation marker before
their final projection, so an active completed turn cannot loop back into
generation. `emit_report_event` routes directly to END.

## 9. Command Ingress

The API command transaction:

1. Validates the public request shape.
2. Inserts a command inbox record keyed by `(session_id, command_id)`.
3. Inserts the corresponding runtime outbox event in the same transaction.
4. Commits, then returns durable command metadata.

The inbox stores:

| Field | Contract |
| --- | --- |
| session_id, command_id | Composite command identity. |
| command_type | Initially answer, skip, or finish. |
| expected_version | Public version supplied by the client. |
| answer_text | Protected payload for answer commands; null otherwise. |
| payload_sha256 | Integrity and duplicate-payload audit. |
| status | pending, applied, conflict, or failed. |
| result_state_version | Set only after successful application. |
| error_code | Stable failure code, never raw exception text. |
| timestamps | Created, claimed, completed, and updated times. |

The outbox stores only scheduling data:

| Field | Contract |
| --- | --- |
| event_id, event_type | Work identity and command_ready or retry_due type. |
| session_id, work_ref | References command_id or generation_id. |
| status, attempt_count | Pending, running, retrying, completed, or dead-letter. |
| available_at | Earliest claim time; immediate for command work. |
| lease fields | Owner, heartbeat, and expiry. |
| timestamps | Created, claimed, completed, and updated times. |

Inbox and outbox rows are inserted in one PostgreSQL transaction. The outbox
never duplicates `answer_text`. After an answer is appended to checkpointed
messages and the message projection, the inbox payload may be cleared according
to the retention policy while its identity, hash, status, and result version
remain.

The worker claims the outbox event with a lease and invokes the graph using the
session thread ID. At-least-once delivery is expected. Duplicate delivery sees
the same command inbox identity and returns the already applied state.

The initial HTTP response acknowledges durable receipt, not successful graph
application. Normal clients observe applied or conflict status through the
command result and session stream. A projection precheck may reject an obviously
stale version early, but graph validation remains authoritative for races.

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
stored on a `retry_due` outbox row as `available_at`, derived from the
database clock. `enqueue_retry` inserts that row idempotently,
`wait_for_retry` interrupts the graph, and the existing leased dispatcher
resumes the thread only after the row becomes claimable. The checkpointed
expected attempt plus `validate_retry` fences duplicate, delayed, and
out-of-order retry events.
Exhausted or terminal provider failure routes to the existing template
follow-up and commits a degraded, auditable result.

Unexpected programming or state errors are not converted into a successful
template response. They leave the thread at its last checkpoint for operator
diagnosis and retry.

AgentExecutionRunner remains the observation boundary for one Agent attempt.
It does not immediately hide Examiner failures behind fallback in the new
engine. Graph routing owns retries and final fallback. Execution context gains
`parent_run_id` so nested Orchestrator and Examiner attempts form a real call
tree instead of sharing only one command causation ID.

This requires an explicit Examiner boundary that does not exist today.
`ExaminerAgent` adds `stream_followup_attempt` that invokes one provider
attempt through AgentExecutionRunner with no fallback callback. The
langgraph-v1 generation node always consumes this stream, persists chunks, and
joins them into the final text. No synchronous `generate_followup_attempt`
method is required in this design.
The existing `generate_followup` and `stream_followup` methods retain their
current immediate fallback semantics for legacy sessions. The graph fallback
node calls the shared deterministic template function directly; it must not
re-enter a legacy Examiner method that hides another provider call.

## 12. Idempotent Side Effects

The required unique identities are:

| Side effect | Unique identity |
| --- | --- |
| User command | `(session_id, command_id)` |
| Generation attempt | `(generation_id, attempt_number)` |
| Stream chunk | `(generation_id, attempt_number, sequence)` |
| Retry outbox event | `(generation_id, next_attempt_number)` |
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

`project_state` is idempotent by `(session_id, next_state_version)`, where
`next_state_version` is always calculated from the node's checkpointed input.
If projection succeeds but its graph checkpoint fails, rerunning the node
calculates and reuses the same version instead of incrementing again. The source
checkpoint ID may be recorded for audit but is not the version key. A projection
lag may temporarily make GET return an older state version, but it can be
repaired from graph state.

The current one-row-per-session projection uses a conditional upsert rather than
adding a second current-state row: insert the initial row, or update it only when
the stored version is lower than `next_state_version`. If the stored version is
already equal, `project_state` verifies the projection payload hash and reuses
the row. A greater stored version is a stale replay and performs no write.
Message projection rows retain their existing append identity and verify content
on an idempotent replay.

GET combines the business projection with `graph.get_state(config)` for
new-engine sessions. It maps snapshot `next` nodes and interrupt payloads to
the public `pending_action`; that derived field is not written back into graph
state.

Final report workers continue to consume the complete projected session. The
finish branch first commits the final `finished` projection and then inserts
the report request into the existing durable report job boundary before the
graph reaches END. These are separate node transactions, not a distributed
transaction: report enqueue is idempotent by session and replays independently
after failure. This ordering prevents a report worker from observing the job
before the final projection is readable.

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

- Checkpoints contain the bounded plan snapshot plus candidate and committed
  interviewer messages. They exclude raw JD, resume, knowledge evidence text,
  uncommitted provider payloads, credentials, and transport metadata.
- Checkpoint tables use a restricted database role, encrypted storage and
  backups, the interview retention window, and explicit session deletion.
- Raw conversation text is allowed only in protected checkpoints, command inbox
  rows awaiting incorporation, message projections, and generation chunks. It
  is excluded from Agent run metadata, runtime control APIs, logs, and traces.
- Generation chunks necessarily contain the follow-up being streamed. Their
  table uses the same access control as interview messages and is not exposed
  by runtime diagnostic APIs.
- Completed generation chunks are retained for 24 hours by default. The final
  follow-up remains in the message store.
- Session deletion cascades or explicitly deletes checkpoints, inbox commands,
  outbox work, attempts, chunks, and projections.
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
- Pure `wait_for_retry` interrupt and due-event resume behavior.
- Duplicate, stale, and mismatched retry events cannot advance an attempt.
- Command duplicate and version-conflict routing.
- Provider failure classification and the three-attempt cap.
- Retry-loop checkpoint accounting and retention estimates.
- Generation reset and client attempt replacement rules.
- Every idempotency-key contract.
- Workflow engine and graph version routing.
- StateSnapshot-to-pending_action projection mapping.
- Checkpoint schema allowlist and trace privacy sanitization.

### 17.2 PostgreSQL integration tests

Use a real PostgreSQL checkpointer and application schema. Force process loss
at each boundary:

1. Command inbox committed before graph dispatch.
2. Inbox answer appended to graph messages before node checkpoint.
3. Generation attempt prepared before provider invocation.
4. Partial chunks persisted during provider streaming.
5. Complete provider result saved before graph checkpoint.
6. Session projection committed before graph checkpoint; replay must reuse the
   same next_state_version.
7. Report event inserted before graph END.
8. Retry event not yet due, due, claimed, and redelivered after dispatcher loss.

After restarting a worker, every test verifies:

- No accepted answer is lost or duplicated.
- Exactly one final interviewer message is committed.
- Chunks from abandoned and replacement attempts are not concatenated.
- Public state_version increases monotonically.
- A project_state replay after checkpoint failure does not skip or duplicate a
  public version.
- Projection clears transient command, generation, and retry routing values.
- Outbox rows never contain answer_text.
- One effective report request exists.
- A report request is never visible before the final finished projection.
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
8. Checkpoints contain only the approved plan snapshot and conversation text;
   they contain no raw JD, resume, knowledge evidence, uncommitted provider
   payload, credential, or transport metadata.
9. Runtime traces, Agent run metadata, control APIs, and logs contain no raw
   interview text.
10. Legacy sessions continue through the legacy engine without migration.
11. New session assignment can be disabled without stranding existing
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
| Dual workflow state | LangGraph is the write model; business tables are query projections. |
| Lost accepted command | Command inbox and outbox commit before HTTP acceptance. |
| Duplicate model call | Expected after some crashes; attempt and message keys prevent duplicate business output. |
| Partial stream corruption | Attempt-scoped sequences and mandatory generation reset. |
| Checkpoint and table transaction gap | Every node side effect is idempotent and reusable. |
| Paused thread breaks after deploy | Pin graph version and retain old node definitions. |
| Checkpoint privacy growth | Bounded inline state, restricted storage, JSON serialization, retention, and privacy tests. |
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
