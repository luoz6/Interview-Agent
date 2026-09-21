# MA0-T07 - Execution State Persistence Ownership Decision

## Status

```text
TASK = MA0-T07
STATUS = COMPLETE
DECISION_STATUS = DECISION_PROPOSED
DECISION_LOCKED = false
SELECTED_OPTION = A
TASK_ACCEPTANCE = PASS
NEXT_TASK = NOT_STARTED
```

This proposal is based on repository commit
`ed088f0ad5091c042746ba8645b07a0c09e27c17` plus the MA0-T01 through T06
inventory and gates. It makes no Scheduler implementation change and does not
freeze the provisional MA1+ design.

## Proposed Decision

Choose option A:

```text
ExecutionStateRepository = canonical Scheduler runtime truth
LangGraph checkpoint     = graph cursor / technical recovery projection only
```

The future neutral `ExecutionStateRepository` must own the authoritative
Scheduler execution revision and lifecycle. A checkpoint may cache enough state
to resume graph mechanics, but it must not independently authorize a business
transition, task completion, accepted Artifact, retry, cancellation, or user
wait resolution.

The repository and checkpoint must never be presented as two equivalent truths.
On disagreement, the repository revision and its committed records win.

## Truth Boundary

| Concern | Canonical owner under this proposal | Checkpoint role |
| --- | --- | --- |
| Execution lifecycle and status | `ExecutionStateRepository` | Cached technical position |
| Monotonic execution revision | `ExecutionStateRepository` CAS | Carries observed committed revision |
| Ready/running/waiting task identity | Repository-owned execution/task records | Selects the next graph node |
| User wait and accepted command | Repository-owned wait/command records | Resume cursor only |
| Lease, fencing, retry and cancellation authority | Durable business records | No authority |
| Logical invocation completion | Durable invocation ledger | May cache the accepted outcome reference |
| Accepted Artifact identity/reference | Durable Artifact owner plus execution commit | No independent publication authority |
| Delivery/reconciliation work | Transactional outbox/receipt | Consumer of committed events |

The detailed Artifact owner matrix belongs to MA0-T08 and is not pre-empted by
this decision.

## Current Repository Findings

### 1. LangGraph PostgresSaver

`PostgresCheckpointerRuntime` constructs a dedicated psycopg3
`ConnectionPool`, configures it with `autocommit=True`, and passes that pool to
`PostgresSaver`. The pool has its own lifecycle, capacity, application name, and
schema validation. No application `UnitOfWorkPort`, business cursor, or borrowed
connection is exposed to Saver writes.

The current durable interview runtime uses checkpoint state for graph `.next`,
resume, recovery, and some public snapshot fields. The graph also writes a
separate CAS projection into the session repository. This is an observed hybrid
in the existing interview workflow, not the ownership model to copy into the
new Scheduler.

The schema contract repeatedly describes the boundary as
`transactional_with_idempotent_checkpointer_phase`, and the migration runbook
states that the LangGraph checkpointer phase is separately idempotent. Both are
consistent with separate commits rather than a shared transaction.

### 2. Workflow and Session Repositories

The business persistence path already has the primitives needed for canonical
execution state:

- `PostgresInterviewSessionStore` exposes a `PostgresUnitOfWork` over the
  business connection provider.
- Session writes support expected-version CAS, `state_version`,
  `checkpoint_version`, and a canonical projection digest.
- `PostgresInterviewWorkflowStore.project_state()` locks the session row,
  verifies the prior version, appends messages, advances the projection, marks
  the command applied, and enqueues a round-closed event in one business
  connection transaction.
- V3 launch inserts the session shell and bootstrap outbox event using the same
  caller-owned cursor before one explicit UoW commit.

These mechanisms are interview-specific today. They demonstrate transaction
capability; they are not themselves the future neutral Scheduler repository.

### 3. Command Persistence

Durable interview commands are persisted with `(session_id, command_id)`, an
immutable payload digest, expected version, status, result version, and error
code. Command insertion and the `interview_command_ready` outbox event share one
business transaction. Duplicate command IDs replay only when their payload
digest agrees.

This is a strong reference for future Scheduler commands, but the neutral model
must use execution/wait/task identity rather than importing interview ownership.

### 4. Lease and Fencing Persistence

Generation, follow-up decision, context, review effect, report, outbox, receipt,
and deletion work all persist their claims in business tables. The strongest
families check owner/token, fencing version, expiry, status, and immutable input
identity at the mutation boundary. Stale workers cannot rely on checkpoint state
to bypass those predicates.

This supports repository ownership: execution transitions can share the same
business transaction model and must preserve effect-owner fencing. A Scheduler
lock alone must not replace those fences.

### 5. Report, Artifact, and Outbox Boundaries

The runtime outbox repository accepts a caller-owned cursor. Session changes,
command changes, and their emitted events can therefore commit atomically.
Outbox claim, retry, dead-letter, replay, and receipt mutations use explicit
business UoWs.

Report jobs use the business connection provider for enqueue/claim/lease/retry
transitions. Durable review commit borrows the active business connection so the
immutable report Artifact/head publication, report compatibility projection,
lease validation, and review completion participate in one transaction.

These are concrete precedents for making execution revision, invocation
receipt, accepted Artifact metadata/reference, and outbox emission one explicit
consistency boundary where their stores share the business database.

### 6. PostgreSQL Connection and Transaction Ownership

`PostgresConnectionDomains` is the process-local owner of four distinct
connection domains:

| Domain | Driver/runtime | Transaction behavior |
| --- | --- | --- |
| business | psycopg2 pooled provider | Application transaction/UoW |
| telemetry | psycopg2 pooled provider | Independent telemetry commits |
| advisory lock | psycopg2 pooled provider | Exclusive lock connection |
| checkpointer | psycopg3 `ConnectionPool` | Saver-owned, autocommit enabled |

Sharing a DSN and PostgreSQL server does not create a shared transaction.
`PostgresUnitOfWork` owns one business connection/cursor and commits only after
an explicit decision. `PostgresSaver` obtains different connections through its
own pool and is not passed that cursor.

## Shared Transaction Finding

```text
CURRENTLY_SUPPORTED = false
```

The current LangGraph PostgresSaver cannot be assumed to share an atomic commit
with application persistence. No code path enlists Saver and business writes in
one connection or transaction, and the two paths use different driver/pool
ownership. Any future claim that they share a transaction requires a new,
explicitly tested adapter; it is not part of this proposal.

## Commit Protocol Required By Option A

For each Scheduler transition:

1. Open one business UoW and lock/read the execution at the expected revision.
2. Validate task state, command/wait identity, lease and fencing predicates.
3. Persist the next execution revision and all same-boundary task/invocation
   records.
4. Persist or reference the accepted Artifact according to the MA0-T08 ownership
   decision.
5. Insert the delivery/reconciliation event through the same cursor.
6. Commit the business transaction.
7. Allow LangGraph to advance an idempotent technical checkpoint carrying the
   committed execution revision.

Graph nodes must commit business truth before returning state that Saver may
checkpoint. Re-execution after a crash must use stable identities and CAS so a
node can observe the already committed revision and complete checkpoint repair
without duplicating the business result.

## Failure And Recovery Rules

| Failure window | Required outcome |
| --- | --- |
| Crash before business commit | No transition exists; retry from prior revision |
| Business commit succeeds, checkpoint write fails | Repository remains truth; replay/reconciler advances checkpoint idempotently |
| Checkpoint exists without matching committed revision | Checkpoint has no business authority; ignore/quarantine and reconcile |
| Stale worker returns after lease transfer | CAS/fencing rejects its state, Artifact, and completion writes |
| Outbox delivery fails after commit | Retry from durable outbox/receipt without rolling back canonical state |

Reads exposed to APIs, users, and downstream business services must use the
canonical repository. A graph may read its checkpoint to locate technical work,
but must validate the referenced repository revision before committing effects.

## Why Option B Is Not Selected

Making the checkpoint canonical would require every invocation-ledger,
Artifact, lease, command, report, and outbox transition to coordinate with a
Saver transaction that the current application cannot join. The alternative
would be a new outbox/reconciliation protocol for nearly every transition while
the repository tables still enforce business CAS and fencing.

That path adds a second consistency mechanism around infrastructure that already
supports explicit atomic business writes. It also risks formalizing the current
hybrid read model as permanent dual truth. No repository evidence shows a
benefit that outweighs this cost.

## Conditions Before Locking

This remains `DECISION_PROPOSED`, not `LOCKED`. MA1+ freeze must still resolve:

1. The exact neutral `ExecutionStateRepository` schema and Port contract.
2. The MA0-T08 Artifact owner/transaction matrix.
3. The MA0-T09 existing-execution drain and cutover policy.
4. The graph adapter's revision handshake and reconciliation records.
5. Crash-injection tests for every failure window above.
6. Database integration tests proving atomic execution/ledger/Artifact/outbox
   behavior for the selected physical stores.

None of these items permits falling back to implicit dual truth. A change from
option A would require a new explicit decision with evidence and review.

## Evidence Index

- Checkpointer pool and Saver construction: `app/runtime/langgraph_runtime.py`
- Connection-domain ownership: `app/runtime/postgres_connection_domains.py`
- psycopg2 providers and commit behavior:
  `app/adapters/postgres/connections.py`
- Explicit UoW: `app/ports/unit_of_work.py`,
  `app/adapters/postgres/unit_of_work.py`
- Session CAS and transactional outbox:
  `app/adapters/persistence/postgres/session_store.py`,
  `app/adapters/postgres/session_repository.py`
- Durable command/projection/outbox:
  `app/adapters/persistence/postgres/interview_workflow_store.py`
- Current checkpoint reads and resume:
  `app/runtime/interview_workflow.py`,
  `app/graphs/durable_interview_graph.py`
- Runtime outbox/UoW:
  `app/adapters/postgres/runtime_outbox_repository.py`,
  `app/adapters/persistence/postgres/runtime_control.py`
- Report and Artifact commit boundaries:
  `app/adapters/persistence/postgres/report_job_store.py`,
  `app/adapters/persistence/postgres/review_workflow_store.py`,
  `app/adapters/persistence/postgres/report_artifact_store.py`
- Schema transaction declaration: `app/adapters/postgres/schema_contract.py`
- Migration boundary statement:
  `docs/interview-quality-v1-t62-rollback-runbook.md`

## Verification

Executed on 2026-09-17:

```text
60 passed in 2.81s
```

The focused suite covered the LangGraph runtime contract, independent
PostgreSQL connection domains, explicit UoW commit/rollback, runtime outbox
atomicity, V3 bootstrap outbox behavior, immutable report Artifact/head
publication, and outbox dispatch/retry behavior.

## Acceptance Decision

```text
PASS
```

Reason: one canonical owner is explicitly proposed, the checkpoint role is
bounded, all required current persistence and transaction owners were inspected,
the lack of a shared Saver/application transaction is proven rather than
assumed, and the proposal defines failure/reconciliation rules without claiming
the decision is locked. MA0-T08 has not been started.
