# MA0-T05 - Reliability Asset Inventory

## Status

```text
TASK = MA0-T05
STATUS = COMPLETE
RELIABILITY_ASSETS = UNDERSTOOD
TASK_ACCEPTANCE = PASS
NEXT_TASK = NOT_STARTED
```

This inventory is frozen against repository commit
`ed088f0ad5091c042746ba8645b07a0c09e27c17` and the MA0-T03/T04 behavioral
baseline. It describes current authority and reuse boundaries; it does not claim
that a general Scheduler already exists.

## Classification

- **Direct reuse:** a current implementation can be consumed by the Scheduler
  without changing its ownership semantics.
- **Scheduler candidate:** the pattern or contract is useful, but needs a neutral
  Port/schema and migration before it becomes shared Scheduler infrastructure.
- **Domain-specific:** valid and reusable only inside its present interview,
  report, context, or deletion owner.
- **Process-local:** useful within one process but not restart-safe or a durable
  authority.

## Executive Matrix

| Required asset | Current authoritative implementations | Classification | Scheduler decision |
| --- | --- | --- | --- |
| Leases | Workflow advisory lock; generation/decision/context/report/review-effect/outbox/receipt/deletion claims | Mixed | Reuse advisory lock directly for single-writer exclusion; extract a neutral lease Port before sharing row leases |
| Fencing | Token/version/expiry predicates; report token fence; projection CAS/digest | Mostly domain-specific | Preserve at each effect owner; do not replace effect fencing with a Scheduler-only lock |
| Heartbeat | Generation, context artifact, report/review effect, outbox | Domain-specific mechanisms | Extract lifecycle helper only after ownership predicates are standardized |
| Idempotency | Command payload digest, event IDs, receipts, generation/effect identities, launch/report uniqueness | Mixed | Reuse identity rules in their owners; build a durable Scheduler invocation receipt rather than using A2A `_tasks` |
| Command queue | Interview command + outbox; report/deletion jobs; retry events | Domain-specific | No current generic Scheduler queue; outbox is the strongest extraction candidate |
| Checkpointer | Pooled PostgreSQL LangGraph saver + exact-version graph registry | Direct for LangGraph | Reuse for Scheduler graph state if Scheduler is LangGraph; never treat it as an invocation/effect ledger |
| Outbox | PostgreSQL runtime outbox + dispatcher + receipts | Scheduler candidate | Generalize envelope/sink dispatch; retain transactional enqueue and leased delivery |
| Report job | PostgreSQL job/lease/retry/orphan repair | Report-specific | Keep as report adapter; use as reference behavior, not Scheduler schema |
| Execution context | `AgentExecutionContext`, `InvocationContext`, agent-run recorders | Candidate/current-agent reuse | Generalize closed agent/phase literals before arbitrary capabilities use it |
| Correlation/causation | Runtime event envelope, agent context, A2A metadata | Candidate | Define one Scheduler propagation contract and bridge current fields explicitly |

## 1. Leases

### Workflow thread ownership

`PostgresWorkflowThreadLock` uses a stable SHA-256-derived PostgreSQL advisory
lock key, an exclusive connection, bounded exponential backoff, and connection
liveness checks. Interview and review identities use separate namespaces.

- **Authority:** live PostgreSQL session/advisory lock.
- **Classification:** **direct reuse** for single-writer workflow execution.
- **Limit:** it is not a recoverable work claim, has no durable lease row, and
  releases when the connection/process dies. `NoopWorkflowThreadLock` is test and
  isolated-caller compatibility only.

### Durable row-lease families

| Family | Identity/fence | Renewal | Reclaim | Classification |
| --- | --- | --- | --- | --- |
| Interview generation attempt | generation + attempt, owner, UUID token, fencing version, expiry | Follow-up heartbeat | Expired main question reclaimed in place; follow-up advances attempt | Domain-specific |
| Follow-up decision attempt | decision/attempt, token, fencing version, expiry | Store heartbeat | Attempt state machine | Domain-specific |
| Context artifact compression | artifact identity, claim token, fencing version, expiry | `ContextArtifactHeartbeat` | Expired claim reclaim | Domain-specific; strong pattern candidate |
| Context failure/probe state | scoped state version, fencing version, probe authorization | Artifact heartbeat also checks failure authorization | Explicit expired-probe reclaim | Domain-specific |
| Report job | job, owner, UUID token, expiry | `ReportLeaseHeartbeat` | `claim_next` takes expired running jobs | Report-specific |
| Durable review effect | operation key, owner/token, fencing version, expiry | `ReviewEffectHeartbeat` | Claim/reclaim by effect identity | Review-specific; strong pattern candidate |
| Runtime outbox | event, worker owner, expiry | Batch heartbeat | Expired rows return to retry | Scheduler candidate |
| Runtime receipt | event + consumer, worker owner, expiry | No general background heartbeat observed | Expired receipt can be reclaimed | Round-review-specific today |
| Session deletion job | session/job, owner/token, fencing version, expiry | No background heartbeat in worker | Expired job reclaim | Deletion-specific |

The schemas and method signatures differ. There is no current shared `LeasePort`
implemented by all of them. `LeaseToken`, `FencedMutation`, and
`IdempotencyReceipt` in `app/runtime/reliability.py` are generic-looking value
objects, but lease/mutation/receipt are only exercised by contract tests; current
production stores do not use them. They are **design candidates**, not a reusable
runtime implementation.

## 2. Fencing

Current fencing is deliberately owned at the mutation boundary:

- generation chunk/status/result writes require token + fencing version + live
  expiry;
- review effects require claim token + fencing version + live expiry, and report
  commit also verifies the active report lease transactionally;
- context artifact/failure-state writes require their current claim and versions;
- deletion completion/failure requires the claimed job token/version;
- report job terminal/retry mutations require owner + token + live expiry, but
  the active methods do not expose a general fencing-version contract;
- outbox/receipt transitions use status + worker ownership and expiry rather than
  UUID token/version fencing;
- durable session projection uses state-version CAS plus projection digest.

**Decision:** all are **domain-specific current authorities**. Their predicates
must remain in place even if a Scheduler owns dispatch. A future generic fenced
mutation abstraction is a **Scheduler candidate**, but it must represent the
actual differences instead of assuming every store already follows one model.

## 3. Heartbeat

Background heartbeat helpers exist for follow-up generation, context artifact
compression, report jobs, review effects, and batched outbox delivery. They run
in daemon threads, renew at roughly one third of the lease, and expose a
fail-closed `ensure_owned()` boundary.

Important exceptions:

- V3 main-question generation has bounded provider/total timeout and fenced final
  writes but does not start `GenerationLeaseHeartbeat`;
- session deletion has a fenced claim but no background renewal thread;
- event receipt processing has lease/reclaim behavior but no shared heartbeat
  helper in the round-review path;
- heartbeat threads themselves are **process-local**; correctness comes from the
  durable row predicate and failure on lost ownership.

**Decision:** the common control-flow shape is a **Scheduler candidate**. Current
heartbeat classes remain **domain-specific** until a common lease Port defines
renew, assert-owned, interval, shutdown, and failure precedence.

## 4. Idempotency

| Scope | Current key/rule | Durability | Reuse decision |
| --- | --- | --- | --- |
| Legacy command | last command ID only | Session store dependent | Interview compatibility only |
| Durable interview command | `(session_id, command_id)` + payload SHA-256 | PostgreSQL | Interview-only schema; preserve rule |
| Runtime event | unique `event_id` | PostgreSQL | Candidate shared event identity |
| Event consumption | `(event_id, consumer_name)` receipt | PostgreSQL | Candidate shared delivery receipt |
| Generation | stable generation identity/source command and immutable digests | PostgreSQL | Generation-specific |
| Review effect | unique operation key + immutable input identity | PostgreSQL | Strong generic effect-ledger reference |
| Interview launch | plan/command records and consumed-plan tombstone | PostgreSQL or in-memory | Launch-specific |
| Report enqueue | one job per session; stored idempotency key | PostgreSQL | Report-specific |
| Session deletion | one request/job per session plus tombstone | PostgreSQL or in-memory | Deletion-specific |
| Agent run telemetry | unique `run_id`, `ON CONFLICT DO NOTHING` | PostgreSQL/filesystem depending recorder | Observability only, not execution idempotency |
| A2A invocation | canonical capability-specific key searched in `_tasks` | **Process-local only** | Not reusable as durable idempotency |

`build_agent_idempotency_key()` produces stable keys for the current four Agent
families. That key builder can inform Scheduler identity design, but an identity
string without a persistent receipt/claim is not durable idempotency.

## 5. Command Queue

There is no neutral Scheduler command queue.

- Durable interview submission persists a domain command and an
  `interview_command_ready` outbox event transactionally.
- Retry timers are delayed outbox events.
- Report work uses the report-job table, due-time polling, leases, and retries.
- Session deletion uses its own job table and claim lifecycle.
- Celery is an optional delivery transport behind the outbox sink; it is not the
  system of record.
- Local background threads poll durable stores, but the threads themselves are
  not queue state.
- In-memory launch/report/deletion adapters are process-local compatibility paths.

**Decision:** the interview command and job stores stay **domain-specific**. A
generic Scheduler queue is a future **candidate** built around durable command
identity, due time, claim/lease, attempt policy, and result receipt. It does not
exist today.

## 6. Checkpointer

`PostgresCheckpointerRuntime` owns a bounded pooled LangGraph PostgreSQL saver,
schema validation, start/shutdown lifecycle, and thread deletion.
`VersionedGraphRegistry` requires an exact graph schema version and never falls
back across versions.

- **Classification:** **direct reuse** when the Scheduler itself is a LangGraph
  workflow and uses its own thread namespace/version.
- **Limits:** checkpoints store graph control state, not authoritative command
  payloads, leases, provider-effect receipts, or business projections. The
  interview and review workflows deliberately keep those in separate stores.
- **Process-local parts:** registry objects and runtime pool lifecycle are local;
  checkpoint rows are durable.

## 7. Outbox And Receipts

The PostgreSQL runtime outbox provides transactional insert, unique event ID,
due-time claim with `SKIP LOCKED`, attempt count, lease/heartbeat, bounded retry,
dead-letter, operator replay, and safe status queries. The dispatcher can deliver
to an in-process sink or Celery. Round-review receipts add per-consumer
idempotency and an atomic evaluation completion path.

- **Strength:** this is the closest existing asset to generic durable dispatch.
- **Current coupling:** `RuntimeEventEnvelope` requires `session_id`; event models
  and local/Celery sink routing enumerate interview/review/principal-memory event
  types; receipt completion is coupled to question evaluation.
- **Classification:** repository/dispatcher pattern is a **Scheduler candidate**,
  not direct generic reuse. Existing events remain directly reusable by their
  current owners.
- **Required extraction:** neutral subject/work identity, typed Scheduler event
  envelope, generic consumer receipt, token-strength decision, and port placement
  outside `runtime`/adapter dependencies.

## 8. Report Job

`PostgresReportJobStore` already supplies enqueue-or-get, persisted engine/version,
due-time claim with `SKIP LOCKED`, lease token/heartbeat/assertion, expired-claim
recovery, retry scheduling, max attempts, terminal status, report projection,
and orphan repair. Durable review adds checkpointed retry and separately fenced
provider effects.

- **Classification:** **report-specific**, directly reusable only by report and
  review execution.
- **Why not generic:** schema and transactions are tied to sessions, reports,
  review engine/version, report progress, and report artifact activation.
- **Scheduler value:** use its lifecycle as reference behavior for a neutral job
  contract; do not make the Scheduler depend on `ReportJobStore`.

## 9. Execution Context

`AgentExecutionContext` carries run, correlation, causation, parent-run, agent,
operation, phase, session/question/state/command identity, evidence IDs, and
attempt number. `AgentExecutionRunner` records completed/degraded/failed/cancelled
outcomes and sanitizes metadata. PostgreSQL and filesystem recorders exist.
`InvocationContext` transports a compatible subset through A2A.

- **Direct reuse:** current Agents and operations can keep using it unchanged.
- **Scheduler candidate:** the shape is suitable as the basis for dispatch
  context, but `AgentName` and `AgentPhase` are closed Literals and do not include
  Scheduler task/capability/version/lease identity.
- **Not authority:** agent-run records are telemetry. They do not prove a task was
  claimed, committed, or safe to replay.
- **Process-local fallback:** filesystem traces are local observability artifacts,
  not a cross-worker execution ledger.

## 10. Correlation And Causation

Current propagation is coherent inside each established path:

- runtime events default correlation to `session_id` and carry optional causation
  and state version;
- durable interview command events use command ID as causation;
- round-review execution uses event ID as causation;
- direct Agent calls commonly use prep run/session as correlation and command as
  causation;
- A2A `InvocationContext`, protocol task, official metadata bridge, and task log
  carry correlation, causation, parent run, command, and session identifiers.

The meanings are not fully uniform: correlation may be prep-run ID, session ID,
context ID, or a standalone generated ID; some optional call sites omit fields.

**Decision:** field names and propagation helpers are a **Scheduler candidate**.
The Scheduler needs one documented root-correlation/parent/causation rule and
must bridge old semantics explicitly rather than rewriting existing histories.

## A2A Task State: Explicit Boundary

```text
LocalA2AServer._tasks
= process-local Python dict
= in-process lifecycle and idempotency cache
!= durable invocation ledger
!= restart-safe queue
!= cross-process claim/fencing authority
```

`LocalA2AServer` linearly scans `_tasks` for the same idempotency key and can
deduplicate working/completed/terminal tasks or retry a retryable failure while
the process lives. It has no persistence adapter, transaction with callers,
lease, heartbeat, fencing token, due-time scheduler, recovery scan, or durable
receipt. A restart loses the task and its deduplication history.

The official A2A routes do not change this conclusion: each Agent route creates
an SDK `InMemoryTaskStore` and `InMemoryQueueManager`; cancellation IDs are held
in an in-memory set. The official transport is protocol-compatible, not durable.

**Scheduler rule:** A2A may remain an invocation transport, but the future
Scheduler must persist claim, attempt, idempotency, outcome, retry, and cancellation
authority outside A2A process memory.

## Reuse Plan

### Directly reusable now

1. `PostgresWorkflowThreadLock` for bounded single-writer execution.
2. `PostgresCheckpointerRuntime` and `VersionedGraphRegistry` for versioned
   LangGraph control state.
3. Runtime failure taxonomy/retry-delay behavior where its existing failure codes
   match the new work type.
4. Existing domain stores through their current interview/report/context/deletion
   owners during migration.
5. `AgentExecutionContext` for calls to the currently declared Agent set.

### Extract before Scheduler-wide reuse

1. A neutral durable work lease/claim Port and row model.
2. A generic fenced effect/receipt ledger, informed by review effects.
3. A neutral command/job queue, informed by outbox and report-job lifecycles.
4. A generic outbox envelope and consumer receipt independent of `session_id` and
   question evaluation.
5. An open capability execution context and one correlation/causation policy.
6. Shared heartbeat orchestration against the new lease Port.

### Keep domain-specific

1. Interview workflow commands, projection CAS, generation and decision stores.
2. Report job/review run/report artifact transactions.
3. Context artifact and compression failure-containment stores.
4. Session deletion jobs/tombstones and principal-memory deletion guards.

### Process-local only

1. `LocalA2AServer._tasks` and its idempotency lookup.
2. Official A2A `InMemoryTaskStore`, `InMemoryQueueManager`, and cancellation set.
3. In-memory session/report/launch/deletion stores.
4. Daemon heartbeat/polling threads and in-process graph registries as mechanisms;
   their durable backing rows, when present, remain the authority.
5. Filesystem agent traces as local observability, not execution state.

## Evidence Index

- Generic reliability values/failure taxonomy: `app/runtime/reliability.py`,
  `app/adapters/reliability/runtime_failure.py`
- Workflow lock: `app/domain/workflow_thread_lock.py`,
  `app/adapters/workflows/workflow_thread_lock.py`
- Generation/decision/context leases:
  `app/adapters/persistence/postgres/interview_generation_store.py`,
  `app/adapters/persistence/postgres/decision_store.py`,
  `app/adapters/postgres/context_artifacts.py`,
  `app/adapters/persistence/postgres/context_compression_failure_store.py`
- Report/review reliability:
  `app/adapters/persistence/postgres/report_job_store.py`,
  `app/adapters/persistence/postgres/review_workflow_store.py`,
  `app/runtime/review_workflow.py`
- Outbox/receipts: `app/adapters/postgres/runtime_outbox_repository.py`,
  `app/adapters/postgres/runtime_receipt_repository.py`,
  `app/adapters/persistence/postgres/runtime_control.py`,
  `app/runtime/outbox.py`
- Checkpointer: `app/runtime/langgraph_runtime.py`
- Deletion lease: `app/adapters/persistence/postgres/session_deletion.py`,
  `app/runtime/session_deletion_worker.py`
- Execution/tracing: `app/domain/agent_execution.py`,
  `app/runtime/agent_execution.py`, `app/runtime/agent_recorders.py`
- A2A: `app/a2a/server.py`, `app/a2a/official_server.py`,
  `app/a2a/idempotency.py`, `app/a2a/invocation/context.py`

## Verification

Executed on 2026-09-17:

```text
131 passed in 3.57s
```

The focused suite covered generic reliability contracts, LangGraph runtime
contracts, workflow locking, outbox dispatch/heartbeat/retry, A2A runtime and
task lifecycle, agent trace propagation/sanitization, generation storage, report
leases/recovery, review workflow, and deletion claim/fencing behavior.

## Acceptance Decision

```text
PASS
```

Reason: all ten required asset categories are mapped to concrete owners and
verified implementations; direct reuse, domain-only use, Scheduler candidates,
and process-local state are explicitly separated. The required A2A `_tasks`
boundary is unambiguous. No production or test behavior was changed. MA0-T06 has
not been started.
