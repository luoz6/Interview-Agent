# Stage 48 PostgreSQL Connection Ownership and Capacity Plan

> **Execution note:** Implement this plan in task order and begin every task
> with the stated failing test. Complete Batch A before changing any Store,
> complete each domain in Batch B before moving to the next domain, and do
> not declare capacity readiness until Batch C passes. Preserve the complete
> dirty worktree and do not create a commit unless explicitly requested.

**Goal:** Replace unbounded per-operation PostgreSQL connections with four
explicit, bounded connection domains; make Checkpointer and runtime database
lifecycle ownership atomic; move schema setup out of request/startup paths;
validate every derived PostgreSQL identifier against the 63-byte limit; and
produce privacy-safe, process-aware capacity evidence required before any
higher LangGraph rollout.

**Architecture:** The runtime composition root owns one
`PostgresConnectionDomains` object per process. It contains a psycopg3 pool
for LangGraph Checkpointer work and three bounded psycopg2 providers for
Business SQL, Advisory Locks, and Telemetry. Stores borrow connections through
injected providers and never close shared pools. Advisory-lock connections
remain session-exclusive for the complete lock ownership interval.
Checkpointer schema setup and business/telemetry DDL move into an explicit,
serialized migration command; runtime construction validates schema only.
An in-process metrics registry and deterministic capacity harness emit a
separate `postgres-capacity-v1` artifact without changing
`langgraph-canary-v2`.

**Tech Stack:** Python 3.11, PostgreSQL 16, psycopg2 2.9,
`psycopg_pool` 3.3, psycopg3, LangGraph 1.2,
`langgraph-checkpoint-postgres` 3.1, FastAPI lifespan, Celery, pytest,
Playwright, PostgreSQL catalogs, advisory locks, and the existing Stage
46/47/47.1/47.2 recovery, fencing, telemetry, and privacy gates.

**Baseline:** Stage 47.2 repository acceptance is
`READY_FOR_AGENT_TELEMETRY_CANARY`; production observation is `NOT_RUN`;
committed Interview/Review rollout defaults remain `0/0`. Full Python is
`1192 passed, 1 skipped`; Stage 47.2 PostgreSQL Agent Ledger is `8 passed`;
LangGraph recovery/fencing markers are `24 passed`; browser functionality is
`38 passed, 8 configured skips, 0 failed`. Stage 48 must preserve those
contracts. Repository capacity evidence is not deployed capacity evidence.

---

## Why This Is the Next Step

Stage 46 established single-writer ownership and SQL fencing. Stage 47 added
safe canary gates, Stage 47.1 made lease-renewal exceptions fail closed, and
Stage 47.2 hardened Agent telemetry. The remaining rollout blocker is
connection ownership and capacity.

Current production code opens new psycopg2 connections in Session, Runtime
Control, Generation, Report Jobs, Runtime Signals, Vector Store, Canary
status, and Advisory Lock paths. A single Interview or Review execution can
therefore create many TCP/authentication sessions, with no process-wide limit,
wait timeout, saturation metric, or total multi-process budget.

The LangGraph Checkpointer is also not pooled. `PostgresSaver.from_conn_string`
opens one psycopg3 connection, and `PostgresCheckpointerRuntime.start()` calls
`saver.setup()` without a lifecycle lock. Store constructors independently
run DDL. Concurrent process startup can repeat setup work and connection
bursts before the application serves useful traffic.

PostgreSQL silently truncates identifiers to 63 bytes. Runtime table prefixes
are currently composed into tables and indexes without a single registry that
proves every final identifier fits. A valid-looking prefix can therefore
produce colliding truncated index names.

Stage 48 addresses these infrastructure boundaries. It does not authorize
higher traffic by itself; it creates the evidence required for a separately
authorized capacity-aware canary.

## Execution Preconditions

1. Preserve all existing tracked and untracked user changes. Do not use
   destructive reset, checkout, clean, or broad delete commands.
2. Use the repository Python 3.11 interpreter.
3. Use only the configured local/test PostgreSQL database with isolated safe
   prefixes for repository tests.
4. Do not call a real LLM. Use deterministic providers and existing browser
   fakes.
5. Keep committed rollout defaults unchanged:

   ```text
   INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
   REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
   ```

6. Preserve `langgraph-v1`, `langgraph-review-v1`, `agent-runtime-v1`, both
   Durable State schemas, every Graph node/edge, interrupt location, thread
   identity, lease/fencing predicate, and retry taxonomy unless this plan
   explicitly adds the stable pool-exhaustion runtime error.
7. Confirm `psycopg_pool` and psycopg3 are installed before implementation.
8. Record a fresh baseline connection-count harness before replacing direct
   connections.
9. Never include DSNs, database users, hosts, SQL text, session/job/thread
   IDs, provider payloads, or credentials in pool metrics or artifacts.
10. After the first production source edit, previous readiness becomes
    historical evidence. Restore readiness only after all Stage 48 gates.

## Scope

Stage 48 covers:

- a shared connection-provider Protocol and explicit ownership contract;
- a bounded, timeout-aware, thread-safe psycopg2 pool wrapper;
- distinct Business SQL, Advisory Lock, and Telemetry psycopg2 domains;
- a bounded psycopg3 Checkpointer pool;
- atomic Checkpointer start/shutdown and failure recovery;
- Store injection of borrowed connection providers;
- transaction commit/rollback/reset and broken-connection discard;
- bounded pool acquisition and stable exhaustion classification;
- runtime composition, lazy domain creation, drain, and shutdown ordering;
- explicit schema migration and runtime schema validation;
- removal of `PostgresSaver.setup()` from normal runtime start;
- UTF-8 byte-length validation for all derived PostgreSQL identifiers;
- pool wait, use, timeout, discard, and peak-utilization metrics;
- multi-process connection-budget calculation;
- `postgres-capacity-v1` deterministic acceptance evidence;
- combined pool, fencing, recovery, telemetry, and browser regression.

## Non-Goals

- Do not change either LangGraph State schema or topology.
- Do not change checkpoint retention or delete existing checkpoints.
- Do not register Interview or Review v2 graphs.
- Do not add question-level Review retry.
- Do not change provider retry counts, backoff, lease duration, or heartbeat
  frequency.
- Do not convert all Business SQL from psycopg2 to psycopg3.
- Do not use one global pool for every domain.
- Do not replace session-level advisory locks with transaction-level locks.
- Do not hold a Business SQL transaction around provider work.
- Do not add an asynchronous Agent telemetry queue or Outbox.
- Do not expose pool internals through public session/browser payloads.
- Do not mutate `langgraph-canary-v2`; capacity uses a separate artifact.
- Do not automatically run a deployed migration, restart, canary, or rollout.
- Do not raise committed rollout percentages above zero.

## Batch Boundaries

### Batch A — Contracts and Safety Primitives

- Tasks 1–4.
- No production Store migration.
- Delivers baseline, identifier registry, connection-provider contract, and
  bounded psycopg2 pool primitive.

### Batch B — Domain Migration

- Tasks 5–9.
- Migrates Business, Telemetry, Advisory Lock, and Checkpointer domains one at
  a time.
- Each domain has its own focused rollback point and acceptance.

### Batch C — Migration, Capacity, and Release

- Tasks 10–14.
- Moves setup out of runtime, composes/shuts down domains, generates capacity
  evidence, and runs final repository gates.

## File Map

**New connection ownership and identifier modules:**

- Create: `app/services/postgres_connections.py`
- Create: `app/services/postgres_identifiers.py`
- Create: `app/services/postgres_capacity.py`
- Create: `app/services/postgres_runtime_migrations.py`

**Runtime composition and Checkpointer:**

- Modify: `app/services/runtime.py`
- Modify: `app/services/langgraph_runtime.py`
- Modify: `app/services/config.py`
- Modify: `app/services/runtime_work.py`
- Modify: `.env.example`

**Business SQL Stores:**

- Modify: `app/services/postgres_session.py`
- Modify: `app/services/postgres_runtime_control.py`
- Modify: `app/services/interview_generation_store.py`
- Modify: `app/services/interview_workflow_store.py`
- Modify: `app/services/report_jobs.py`
- Modify: `app/services/review_workflow_store.py`
- Modify: `app/services/vector_store.py`

**Telemetry and lock consumers:**

- Modify: `app/services/runtime_signal_metrics.py`
- Modify: `app/services/langgraph_canary_status.py`
- Modify: `app/services/agent_recorders.py`
- Modify: `app/services/workflow_thread_lock.py`
- Modify: `app/services/durable_workflow_maintenance.py`

**Migration, preflight, capacity, and documentation:**

- Create: `scripts/postgres_runtime_migrate.py`
- Create: `scripts/postgres_capacity_acceptance.py`
- Modify: `scripts/runtime_preflight.py`
- Modify: `docs/local-v1-runbook.md`
- Create: `docs/postgres-connection-capacity-acceptance.md`

**New tests:**

- Create: `tests/test_postgres_connections.py`
- Create: `tests/test_postgres_connections_postgres.py`
- Create: `tests/test_postgres_identifiers.py`
- Create: `tests/test_postgres_capacity.py`
- Create: `tests/test_postgres_capacity_postgres.py`
- Create: `tests/test_postgres_runtime_migrations.py`
- Create: `tests/test_postgres_runtime_migrations_postgres.py`
- Create: `tests/test_stage48_connection_composition.py`
- Create: `tests/test_stage48_release_contract.py`

**Existing tests to extend:**

- Modify: `tests/test_langgraph_runtime.py` or create it if absent
- Modify: `tests/test_runtime_provider.py`
- Modify: `tests/test_runtime_preflight.py`
- Modify: Store-specific PostgreSQL tests
- Modify: `tests/test_workflow_thread_lock.py`
- Modify: `tests/test_workflow_thread_lock_postgres.py`
- Modify: Stage 46/47/47.1/47.2 acceptance contracts

## Fixed Decisions

1. **Four domains, four budgets.** Checkpointer, Business SQL, Advisory Lock,
   and Telemetry never share one undifferentiated runtime pool.
2. **Business SQL remains psycopg2 in Stage 48.** Existing
   `psycopg2.sql.Composed` statements and transaction behavior remain valid.
   A driver migration is a separate stage.
3. **Checkpointer uses psycopg3 `ConnectionPool`.** Configure
   `autocommit=True`, `prepare_threshold=0`, and `dict_row`, then construct
   `PostgresSaver(pool)`. The currently installed
   `langgraph-checkpoint-postgres` 3.1 implementation explicitly accepts
   `psycopg_pool.ConnectionPool`, rejects only the pool-plus-pipeline
   combination, and checks out through its internal `get_connection()`.
   A behavioral compatibility test, not this source observation alone, is a
   mandatory release gate.
4. **Pools open explicitly.** Create with closed state, call `open()`/wait in
   runtime start or migration, and call bounded drain/close in shutdown.
5. **Store constructors borrow providers.** The composition root owns shared
   pools. Stores never close injected providers.
6. **Direct providers remain only for isolated compatibility tests and
   one-shot migration.** Production composition tests reject a Store that
   silently creates a direct connection.
7. **Acquire is bounded through an explicit Condition predicate.** The
   psycopg2 wrapper owns the available-capacity count. A waiter uses
   `Condition.wait_for(capacity_or_closed, timeout)`; it reserves capacity
   under the Condition before calling `ThreadedConnectionPool.getconn()`.
   Every pool has a finite wait timeout and raises `PostgresPoolExhausted`
   when it cannot prove capacity.
8. **Pool exhaustion is not a provider error.** Map it to stable
   `postgres_pool_exhausted`, retryable where the outer durable boundary can
   safely retry. Never label it `provider_unavailable`.
9. **Transactions are reset before reuse.** Success commits, exceptions roll
   back, dirty/failed transactions are cleared, and broken connections are
   discarded.
10. **Advisory-lock connections are session-exclusive.** A lock connection is
    never returned until unlock and session cleanup complete.
11. **Lost/unlock-failed lock connections are discarded.** They do not return
    to the lock pool.
12. **Checkpointer pool is a lifecycle/capacity boundary, not an unproven
    throughput claim.** Current synchronous `PostgresSaver` has its own lock.
13. **Checkpointer start is a state machine.** States are stopped, starting,
    started, stopping, and failed. One caller starts; concurrent callers wait
    or receive the same stable failure.
14. **Runtime start performs no schema mutation.** `PostgresSaver.setup()` and
    Store DDL run only in explicit migration.
15. **Runtime validates schema before serving.** Missing/incompatible schema
    raises a stable non-sensitive `PostgresSchemaNotReady`.
16. **Migration is serialized and phase-aware.** Use one dedicated one-shot
    migration connection and acquire one migration-specific session advisory
    lock for the complete migration. Under that outer lock, transaction-safe
    DDL runs in an explicit transaction; any future
    `CREATE INDEX CONCURRENTLY` or other transaction-forbidden operation runs
    in a separately declared autocommit phase. Never place `CONCURRENTLY`,
    `VACUUM`, or equivalent operations inside the transaction phase, and
    never use the workflow lock pool for DDL. A deployed migration requires
    application work admission to be drained first.
17. **Identifier limits use UTF-8 bytes.** Every table, index, constraint, and
    migration-table identifier must encode to at most 63 bytes.
18. **No silent truncation.** Reject a configured prefix before connecting if
    any derived identifier exceeds the limit or two names would collide after
    PostgreSQL truncation.
19. **Capacity metrics are aggregate and process-local.** They contain domain,
    configured bounds, counts, waits, and peaks only.
20. **Capacity evidence includes process multiplication.** Per-process pool
    maxima alone are insufficient. A conservative Checkpointer connection
    overhead/reserve is included and then validated against observed
    `pg_stat_activity` counts.
21. **Telemetry cannot starve Business SQL.** Saturating Telemetry must leave
    Business capacity available.
22. **Lock saturation and database-lock contention are distinct.** Emit
    different outcomes for pool acquire timeout and `pg_try_advisory_lock`
    busy timeout.
    `PostgresWorkflowThreadLock` accepts either `exclusive_provider` or the
    legacy unit-test `connect` callback, never both. Provider wins only when
    it is the sole configured connection source.
23. **Shutdown drains before close.** Stop work producers first, then
    Checkpointer/lock services, then close Telemetry and Business domains
    after leases return.
24. **Drain is bounded.** Timeout produces a stable safe diagnostic and
    force-closes only Stage 48-owned connections after work admission stops.
25. **No pool metric changes business results.** Metrics callbacks remain
    best effort and privacy-safe.
26. **Capacity uses `postgres-capacity-v1`.** Do not mutate
    `langgraph-canary-v2`.
27. **Repository readiness is not deployed evidence.** Final repository status
    may become `READY_FOR_CAPACITY_AWARE_FENCING_CANARY`; production
    observation remains `NOT_RUN`.

## Proposed Configuration

Committed defaults must be conservative and configurable:

```dotenv
POSTGRES_BUSINESS_POOL_MIN_SIZE=1
POSTGRES_BUSINESS_POOL_MAX_SIZE=12
POSTGRES_BUSINESS_POOL_ACQUIRE_TIMEOUT_SECONDS=2

POSTGRES_TELEMETRY_POOL_MIN_SIZE=1
POSTGRES_TELEMETRY_POOL_MAX_SIZE=4
POSTGRES_TELEMETRY_POOL_ACQUIRE_TIMEOUT_SECONDS=1

POSTGRES_LOCK_POOL_MIN_SIZE=1
POSTGRES_LOCK_POOL_MAX_SIZE=4
POSTGRES_LOCK_POOL_ACQUIRE_TIMEOUT_SECONDS=2

POSTGRES_CHECKPOINTER_POOL_MIN_SIZE=1
POSTGRES_CHECKPOINTER_POOL_MAX_SIZE=2
POSTGRES_CHECKPOINTER_POOL_ACQUIRE_TIMEOUT_SECONDS=2
POSTGRES_CHECKPOINTER_POOL_OVERHEAD=1

POSTGRES_CONNECT_TIMEOUT_SECONDS=3
POSTGRES_POOL_DRAIN_TIMEOUT_SECONDS=10
POSTGRES_POOL_MAX_LIFETIME_SECONDS=1800
POSTGRES_POOL_MAX_IDLE_SECONDS=300

POSTGRES_EXPECTED_API_PROCESSES=1
POSTGRES_EXPECTED_CELERY_PROCESSES=1
POSTGRES_EXPECTED_OUTBOX_PROCESSES=1
POSTGRES_EXTERNAL_CONNECTION_RESERVE=10
POSTGRES_CAPACITY_MAX_UTILIZATION=0.80

POSTGRES_RUNTIME_AUTO_MIGRATE=false
```

These are initial repository defaults, not production sizing conclusions.
The capacity harness must prove or revise them for each deployment role.

## Required Provider Semantics

### Business/Telemetry transaction checkout

```text
acquire semaphore with timeout
        |
        +-- timeout -> PostgresPoolExhausted
        |
        v
pool.getconn()
        |
        v
yield connection
        |
        +-- normal -> commit -> reset -> putconn
        |
        +-- error  -> rollback -> reset -> putconn -> re-raise
        |
        +-- broken/reset failure -> discard/close
```

### Advisory lock checkout

```text
acquire lock-domain capacity
        |
        v
dedicated connection, autocommit=True
        |
        v
pg_try_advisory_lock loop
        |
        +-- busy timeout -> unlock if needed -> reset -> return
        |
        +-- acquired -> hold for complete Graph/provider execution
                         |
                         +-- unlock true -> reset -> return
                         +-- lost/false/error -> discard
```

### Checkpointer lifecycle

```text
stopped
   |
   v
starting -- open pool / validate schema --> started
   |                                       |
   +-- failure -> cleanup -> failed        v
                                      stopping
                                           |
                                           v
                                        stopped
```

`start()` never runs DDL. A failed start may be retried only after its partial
pool/context is closed.

## Capacity Budget Formula

For each process role:

```text
role_budget =
    checkpointer_max_if_used
  + checkpointer_overhead_if_used
  + business_max_if_used
  + lock_max_if_used
  + telemetry_max_if_used
```

`POSTGRES_CHECKPOINTER_POOL_OVERHEAD` is a conservative budgeting margin, not
a claim that psycopg_pool always opens one extra database session. The real
PostgreSQL capacity test measures pool-owned sessions and fails if observed
Checkpointer connections exceed `max_size + overhead`. The artifact reports
configured overhead and observed peak separately.

Total application budget:

```text
total_app_budget =
    api_processes * api_role_budget
  + celery_processes * celery_role_budget
  + outbox_processes * outbox_role_budget
```

Available database connections:

```text
available =
    max_connections
  - superuser_reserved_connections
  - external_connection_reserve
```

Mandatory gate:

```text
total_app_budget <= floor(available * POSTGRES_CAPACITY_MAX_UTILIZATION)
```

The artifact must report the configured process counts and domain maxima, but
not DSN, host, username, database name, or process identifiers.

---

## Task 1: Freeze the Connection and Schema Baseline

**Files:**

- Create: `tests/test_stage48_connection_baseline.py`
- Modify: Store/runtime tests only to add non-invasive counters
- Create initial `docs/postgres-connection-capacity-acceptance.md` with
  `IN_PROGRESS`

### Step 1: Instrument direct connect calls in tests

Use injected connect factories or monkeypatch module-level
`psycopg2.connect`. Do not patch global driver state across parallel tests.
Record exact counts for:

- Session Store construction;
- one Interview start;
- one answer/followup command;
- one generation attempt with chunks;
- one Outbox claim/dispatch/receipt;
- one Report Job claim/heartbeat/finalize;
- one Review question/report effect;
- one AgentRun write;
- one Runtime Signal increment/read;
- one Canary snapshot;
- one advisory-lock hold;
- one Checkpointer start/invoke/shutdown.

### Step 2: Count schema setup calls

Prove current constructors call `_ensure_schema()` and Checkpointer `start()`
calls `setup()`. Record counts without changing behavior.

### Step 3: Run the baseline

```powershell
python -m pytest -q tests/test_stage48_connection_baseline.py
```

The test documents current counts; it must not assert the future lower counts
yet.

### Step 4: Preserve baseline evidence

Write only aggregate counts to the acceptance record. Do not record DSN,
table prefix, SQL, or object IDs.

## Task 2: Define and Validate Every PostgreSQL Identifier

**Files:**

- Create: `app/services/postgres_identifiers.py`
- Create: `tests/test_postgres_identifiers.py`
- Modify Store constructors to call validation before connecting only after
  the registry is complete

### Step 1: Build the identifier registry

Enumerate every prefix-derived name used by:

- Session tables/indexes;
- Runtime Outbox/receipt/AgentRun tables and indexes;
- Generation tables and partial/replay indexes;
- Interview workflow projection tables/indexes;
- Report Job tables/indexes;
- Review workflow/effect/artifact tables/indexes;
- Runtime signal buckets;
- Vector Store tables/indexes when they use the runtime prefix;
- migration/version tables.

LangGraph fixed Checkpointer table names are validated separately but are not
prefixed.

### Step 2: Write UTF-8 boundary tests

Test:

- default `interview`;
- empty/whitespace/unsafe prefix;
- ASCII name exactly 63 bytes;
- ASCII name at 64 bytes;
- Chinese/multibyte prefix whose character count is below 63 but byte count
  exceeds 63;
- the longest derived index;
- two names that PostgreSQL would truncate to the same 63 bytes;
- safe test prefixes;
- every current default identifier.

### Step 3: Implement fail-fast validation

Provide:

```python
validate_postgres_identifier(name)
derive_runtime_identifiers(prefix)
validate_runtime_table_prefix(prefix)
```

Raise stable `PostgresIdentifierInvalid` or
`PostgresIdentifierTooLong` without echoing credentials.

### Step 4: Prove no connection occurs before rejection

Inject a connect function that fails the test if called. An invalid prefix
must be rejected before schema setup or pool creation.

### Step 5: Add the gate to config/preflight

`get_runtime_table_prefix()` or the composition boundary validates once and
preflight prints only safe byte counts and PASS/FAIL.

## Task 3: Define Connection Provider, Metrics, and Error Contracts

**Files:**

- Create: `app/services/postgres_connections.py`
- Create: `tests/test_postgres_connections.py`
- Modify: `app/services/runtime_work.py`

### Step 1: Write contract tests

Define Protocols for:

```python
class ConnectionProvider(Protocol):
    @contextmanager
    def connection(self): ...

class ExclusiveConnectionProvider(Protocol):
    @contextmanager
    def exclusive_connection(self, *, autocommit: bool): ...
```

Define ownership:

- injected provider is borrowed;
- direct provider is stateless/no-op close;
- pooled provider is owned by `PostgresConnectionDomains`;
- Store close cannot close a borrowed provider.

### Step 2: Define stable errors

Add:

```text
PostgresPoolExhausted
PostgresPoolClosed
PostgresConnectionDiscarded
PostgresSchemaNotReady
PostgresPoolDrainTimeout
```

Only exhaustion/temporary connectivity is retryable at durable outer
boundaries. Schema-not-ready and identifier errors are startup/preflight
failures.

### Step 3: Define privacy-safe metrics

Per-domain snapshot:

```text
domain
min_size
max_size
leased
idle
waiting
peak_leased
acquire_count
acquire_timeout_count
discard_count
total_wait_ms
max_wait_ms
wait_samples
```

Compute P50/P95 only from bounded in-memory histogram buckets or bounded
samples. Never store connection objects, SQL, DSNs, or caller IDs.

### Step 4: Extend failure classification

Add stable `postgres_pool_exhausted` to the runtime classifier and exact tests
for retryability and public error-code privacy. Do not map it to a provider
code.

## Task 4: Implement the Bounded psycopg2 Pool Primitive

**Files:**

- Modify: `app/services/postgres_connections.py`
- Create: `tests/test_postgres_connections_postgres.py`
- Extend: `tests/test_postgres_connections.py`

### Step 1: Write deterministic unit tests

Use fake connections and barriers to cover:

- min/max validation;
- one checkout/return;
- max concurrent leases;
- a waiter succeeds after return;
- acquire timeout;
- commit on success;
- rollback on body exception;
- reset failure;
- closed connection;
- close rejects new work;
- bounded drain;
- drain timeout;
- metrics exactness;
- callback failure does not change connection behavior.

### Step 2: Implement a semaphore/condition wrapper

Wrap psycopg2 `ThreadedConnectionPool` with a bounded acquire primitive.
`ThreadedConnectionPool.getconn()` alone is not the public acquire contract
because it does not provide the required wait timeout semantics.

Use one `threading.Condition` and an explicit reserved/leased capacity count:

```python
with condition:
    available = condition.wait_for(
        lambda: closed or reserved_count < max_size,
        timeout=acquire_timeout,
    )
    if not available:
        raise PostgresPoolExhausted(...)
    if closed:
        raise PostgresPoolClosed(...)
    reserved_count += 1

try:
    connection = threaded_pool.getconn()
except Exception:
    with condition:
        reserved_count -= 1
        condition.notify()
    raise
```

Return/discard decrements the reserved count and calls `notify()`. Never call
`getconn()` before reserving capacity, never wait without a predicate loop,
and never hold the Condition while performing network connect, commit,
rollback, reset, or close I/O.

Use the pool's existing idle connection first; the explicit count protects
the maximum number of checked-out/growing physical connections. Spurious
wakeups and close-during-wait are mandatory tests.

### Step 3: Implement transaction cleanup

Before return:

- commit successful transaction;
- rollback failed/dirty transaction;
- restore required autocommit/session state;
- discard if `closed`, transaction status is unknown/in-error after reset, or
  reset raises.

### Step 4: Implement bounded shutdown

Track active leases with a Condition. `close(timeout)`:

1. marks the provider closed to new acquisitions;
2. waits for active leases;
3. closes idle/all owned connections;
4. raises/records `PostgresPoolDrainTimeout` if leases remain.

Do not call `closeall()` while new work can still enter.

### Step 5: Run real PostgreSQL proofs

Prove transaction reuse, rollback, discard/replacement, timeout, max
connection count from `pg_stat_activity`, and cleanup with an isolated
application name that contains only a stable domain label.

## Task 5: Migrate the Business SQL Domain

**Files:**

- Modify: `app/services/postgres_session.py`
- Modify: `app/services/postgres_runtime_control.py`
- Modify: `app/services/interview_generation_store.py`
- Modify: `app/services/interview_workflow_store.py`
- Modify: `app/services/report_jobs.py`
- Modify: `app/services/review_workflow_store.py`
- Modify: `app/services/vector_store.py`
- Extend Store-specific tests
- Create: `tests/test_stage48_business_pool_postgres.py`

### Step 1: Add optional provider injection without changing defaults

Each Store accepts a `connection_provider` parameter. During the migration
batch only, omission may use the direct compatibility provider so existing
isolated tests remain readable. Production composition is switched to the
pooled provider in Task 10 and release tests then reject direct production
connections.

Store rules:

- save `dsn` only where legacy/test APIs still require it;
- all normal connection acquisition goes through one private helper or the
  injected provider;
- a Store never closes an injected provider;
- nested Stores receive the same Business provider unless explicitly
  Telemetry-owned.

Use an explicit compatibility constructor contract:

```python
def __init__(
    self,
    *,
    table_prefix: str,
    dsn: str | None = None,
    connection_provider: ConnectionProvider | None = None,
):
    if connection_provider is None:
        if not dsn:
            raise ValueError("dsn or connection_provider is required")
        connection_provider = DirectPsycopg2ConnectionProvider(dsn)
        provider_is_owned = True
    else:
        provider_is_owned = False
    self._connection_provider = connection_provider
```

When both are supplied, `connection_provider` is the only runtime connection
source; `dsn` may remain as compatibility metadata for existing tests/helpers
but must never be used as a fallback connect path. Add a test that supplies a
provider plus a DSN whose `psycopg2.connect` would fail, and prove the DSN is
not used.

Explicit nested propagation chain:

```text
PostgresInterviewSessionStore
    -> PostgresRuntimeControlStore(business_provider)

PostgresInterviewWorkflowStore
    -> PostgresRuntimeControlStore(business_provider)

PostgresReviewWorkflowStore
    -> PostgresRuntimeControlStore(business_provider)

PostgresAgentRunRecorder / AgentRun facade
    -> telemetry_provider, not the parent Business provider
```

`PostgresReportJobStore` receives the Business provider directly; if a future
nested control Store is added, the same explicit propagation rule applies.
No nested Store may reconstruct a provider from `dsn`.

### Step 2: Migrate Runtime Control first

Replace `PostgresRuntimeControlStore.connection()` internals with the injected
provider. Preserve atomic methods that accept a caller-owned cursor; they must
not check out another connection inside an existing transaction.

Run:

```powershell
python -m pytest -q `
  tests/test_postgres_runtime_control.py `
  tests/test_event_publisher.py `
  tests/test_runtime_outbox_dispatcher.py
```

### Step 3: Migrate Session Store

Replace all direct `psycopg2.connect()` calls. Pass the same Business provider
to its nested Runtime Control Store. Prove:

- start + Outbox remains one transaction;
- answer/skip/finish behavior is unchanged;
- rollback leaves no partial Session or Outbox row;
- projection/recovery contracts remain unchanged.

Add the same provider-identity assertion to Interview and Review Workflow
Stores: `nested.control._connection_provider is parent._connection_provider`.

### Step 4: Migrate Generation and Interview Workflow Stores

Generation `_connection()` and workflow-control connections use Business
capacity. Do not hold a connection while waiting for LLM chunks except where
the existing chunk write transaction is active; each coalesced write checks
out only for its SQL boundary.

Prove lease/fencing predicates and replacement-owner recovery still pass.

### Step 5: Migrate Report Job and Review Workflow Stores

Preserve:

- `FOR UPDATE SKIP LOCKED`;
- Report lease token checks;
- Review Effect fencing;
- atomic final commit;
- write-once effect reuse;
- retry scheduling.

Nested Runtime Control instances receive the Business provider.

### Step 6: Migrate Vector Store

Move schema/query/write connections to the Business provider while preserving
pgvector behavior and transaction scope. Embedding/model work must occur
outside checked-out database connections.

### Step 7: Prove connection-count reduction

Repeat Task 1 workflows and assert:

- TCP connect calls are bounded by pool growth, not Store operation count;
- peak leased never exceeds configured Business max;
- sequential Store operations reuse connections;
- no Store silently bypasses the provider.

### Step 8: Batch B1 rollback point

Before Telemetry/Lock/Checkpointer migration, run full Business Store and
Stage 46 fencing tests. If any atomicity predicate changes, revert only Task 5
edits.

## Task 6: Isolate and Migrate the Telemetry Domain

**Files:**

- Modify: `app/services/runtime_signal_metrics.py`
- Modify: `app/services/langgraph_canary_status.py`
- Modify: `app/services/agent_recorders.py`
- Modify: `app/services/durable_workflow_maintenance.py`
- Modify: `app/services/postgres_runtime_control.py` only to support a
  Telemetry-backed AgentRun facade/provider
- Create: `tests/test_stage48_telemetry_pool_postgres.py`

### Step 1: Define Telemetry ownership

Telemetry includes:

- `PostgresAgentRunRecorder.record()`;
- Runtime Signal increment/sum/cleanup;
- Canary status read-only snapshots;
- pool/capacity metric persistence if any;
- maintenance metric reads.

Outbox claim/delivery, Session projection, Generation, Review Effect, and
Report Job mutations remain Business SQL.

### Step 2: Avoid duplicate Runtime Control schema owners

Do not create a second independently migrating Runtime Control Store merely
to write AgentRun rows. Use one of:

- a lightweight `PostgresAgentRunStore` with the Telemetry provider and
  existing table name; or
- an explicit AgentRun provider override on Runtime Control.

The migration plan remains the sole schema owner.

### Step 3: Prove isolation

Exhaust the Telemetry pool with barriers, then assert:

- Business Session read/write still acquires immediately;
- an AgentRun write fails best effort with the existing Stage 47.2 safe
  warning;
- Runtime Signal write failure does not change business outcome;
- no Telemetry timeout is mislabeled as a Business/provider failure.

### Step 4: Preserve public privacy

Pool metrics and Telemetry errors do not expose `safe_metadata`, SQL, DSNs,
identities, or exception messages. Stage 47.2 privacy/audit gates remain PASS.

### Step 5: Prove bounded overhead

Measure AgentRun write wait and total Recorder latency under available and
exhausted Telemetry capacity. This is repository evidence only; Stage 48
capacity acceptance later combines it with process budgets.

## Task 7: Implement the Advisory Lock Connection Domain

**Files:**

- Modify: `app/services/workflow_thread_lock.py`
- Modify: `tests/test_workflow_thread_lock.py`
- Modify: `tests/test_workflow_thread_lock_postgres.py`
- Create: `tests/test_stage48_lock_pool_postgres.py`

### Step 1: Inject an exclusive provider

`PostgresWorkflowThreadLock` receives a borrowed
`ExclusiveConnectionProvider`. Keep the existing `connect` injection only as
a compatibility adapter for focused unit tests, then deprecate it in
production composition.

Constructor compatibility is explicit:

```python
def __init__(
    ...,
    exclusive_provider: ExclusiveConnectionProvider | None = None,
    connect: Callable[..., Any] | None = None,
):
    if exclusive_provider is not None and connect is not None:
        raise ValueError(
            "exclusive_provider and connect are mutually exclusive"
        )
```

Resolution order:

1. `exclusive_provider` in production;
2. legacy `connect` callback only in isolated unit tests;
3. direct compatibility provider from DSN only while Batch B migration tests
   still require it.

The release contract rejects production composition that reaches paths 2 or
3. Existing fake-connection unit tests continue through the legacy callback
until converted; they must not instantiate a real pool.

### Step 2: Separate pool wait from PostgreSQL lock wait

Required outcomes:

```text
pool_exhausted
lock_busy
acquired
lock_lost
unlock_failed
released
```

Pool wait and `pg_try_advisory_lock` wait have separate durations and stable
metrics.

### Step 3: Prove session exclusivity

Use barriers with two lock identities and a small lock pool. While ownership
is yielded:

- the connection cannot be leased elsewhere;
- `ensure_owned()` uses the same session;
- provider/Graph work holds no Business transaction;
- another available lock-domain connection can serve a distinct identity up
  to pool max.

### Step 4: Prove cleanup and discard

Test:

- normal unlock true -> connection returns;
- body error -> unlock then return, original error wins;
- unlock false -> discard and raise if body succeeded;
- connection lost -> discard;
- cursor error -> discard;
- service shutdown -> no new hold;
- drain waits for active ownership;
- drain timeout is bounded.

### Step 5: Query PostgreSQL advisory lock catalog

After every real test, assert no Stage 48 test backend/key remains in
`pg_locks`. Do not record raw thread identities in acceptance output.

### Step 6: Preserve Stage 46/47 semantics

`WorkflowThreadBusy`, `WorkflowThreadLockLost`, lock ordering, fencing, and
outer signal ownership remain unchanged except for the new distinguishable
pool-exhaustion code.

## Task 8: Implement a Pooled, Atomic Checkpointer Runtime

**Files:**

- Modify: `app/services/langgraph_runtime.py`
- Create/Modify: `tests/test_langgraph_runtime.py`
- Create: `tests/test_stage48_checkpointer_postgres.py`
- Extend LangGraph recovery tests

### Step 0: Prove the installed PostgresSaver pool API

Before writing the runtime, add a release/API compatibility test against the
installed `langgraph-checkpoint-postgres>=3.1.0,<3.2`:

1. inspect/characterize `PostgresSaver.__init__(conn, pipe=None, serde=None)`;
2. assert it accepts the installed `psycopg_pool.ConnectionPool` without
   pipeline;
3. assert pool-plus-pipeline raises the library's expected `ValueError`;
4. open a real isolated pool, construct `PostgresSaver(pool)`, and execute a
   read such as `get_tuple()` for a missing test thread;
5. after explicit migration, execute a minimal put/get or graph checkpoint
   round trip and prove the Saver checks out/returns through the pool;
6. close the pool and prove no connection remains.

The current installed implementation is known to support this path and uses
its internal `get_connection(self.conn)`. The test makes that dependency
assumption executable.

If the compatibility test fails after a dependency change, stop Stage 48 with
`BLOCKED_CHECKPOINTER_POOL_API`. Do not pass a short-lived
`pool.connection()` connection into a long-lived Saver after returning it to
the pool. A fallback `PooledPostgresSaver` would have to wrap/override every
checkpoint operation so each operation owns its checkout; that is a separate
reviewed adapter design, not an automatic fallback inside this task.

### Step 1: Write lifecycle state-machine tests

Use fake pool/saver factories and barriers to cover:

- stopped -> starting -> started;
- concurrent start returns one saver;
- pool open failure cleans partial state;
- schema validation failure closes pool;
- a failed start can be retried explicitly;
- concurrent start/shutdown is deterministic;
- repeated shutdown is safe;
- saver access before start fails;
- no `setup()` call occurs in `start()`.

### Step 2: Construct psycopg3 pool explicitly

Use `ConnectionPool(open=False)` with:

```python
kwargs={
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}
```

Call `open(wait=True, timeout=...)` through a bounded lifecycle. Construct:

```python
PostgresSaver(pool)
```

Do not request pipeline mode with a pool; LangGraph rejects that combination.

Pin the compatibility behavior in `tests/test_stage48_release_contract.py`
alongside the dependency range. A dependency upgrade that changes the pool
contract must fail before runtime construction.

### Step 3: Validate Checkpointer schema

At start, query catalogs for required Checkpointer tables/migration level.
Validation is read-only. Missing schema raises `PostgresSchemaNotReady` and
closes the pool.

### Step 4: Acknowledge saver serialization

Add a characterization/capacity test that records the current synchronous
Saver lock behavior. Do not assert two checkpoint calls use two simultaneous
connections unless the installed LangGraph implementation proves it.

### Step 5: Recovery tests

Run Interview and Review process-loss recovery using the pool, including:

- runtime shutdown/restart;
- connection replacement;
- pool max of one and two;
- Checkpointer connection interruption;
- existing checkpoint resume;
- delete_thread cleanup.

### Step 6: Batch B rollback point

All LangGraph recovery and fencing markers must pass before removing runtime
schema setup in Task 9.

## Task 9: Define Pool Metrics and Stable Capacity Signals

**Files:**

- Modify: `app/services/postgres_connections.py`
- Create: `app/services/postgres_capacity.py`
- Create: `tests/test_postgres_capacity.py`
- Modify: `app/services/runtime_work.py`

### Step 1: Implement bounded wait histograms

Use fixed buckets such as:

```text
<=1ms, <=5ms, <=10ms, <=25ms, <=50ms,
<=100ms, <=250ms, <=500ms, <=1s, >1s
```

Avoid unbounded per-acquire samples.

### Step 2: Snapshot all four domains

Normalize psycopg2 and psycopg3 pool stats into one
`PostgresPoolDomainSnapshot`. Include configured min/max, current/peak leased,
waiting, counts, timeouts, discards, and wait histogram.

### Step 3: Add stable diagnostics

Pool exhaustion logs/signals include:

- domain;
- stable code;
- configured max;
- bounded wait bucket.

They exclude exception text, DSN, SQL, Store name where it could encode a
prefix, and request identity.

### Step 4: Keep metrics best effort

A metrics callback or snapshot failure does not change connection checkout,
transaction, lock, checkpoint, or business result.

---

## Task 10: Move Schema Setup into Explicit Migration and Preflight

**Files:**

- Create: `app/services/postgres_runtime_migrations.py`
- Create: `scripts/postgres_runtime_migrate.py`
- Create: `tests/test_postgres_runtime_migrations.py`
- Create: `tests/test_postgres_runtime_migrations_postgres.py`
- Modify every Store constructor/schema method
- Modify: `scripts/runtime_preflight.py`
- Modify: `tests/test_runtime_preflight.py`

### Step 1: Define schema modes

Use explicit modes:

```text
migrate   - one-shot operator/test fixture; may execute DDL
validate  - runtime; read-only catalog validation
```

Do not keep an ambiguous production `ensure=True` default.

Compatibility construction in unit tests must choose a mode explicitly after
their fixtures migrate.

### Step 2: Create migration registry

The registry covers:

- Session/Message/Report/Question Evaluation;
- Runtime Outbox/Receipt/AgentRun;
- Interview Workflow projections/commands/messages;
- Generation/generation attempts/chunks;
- Report Jobs;
- Review runs/effects/artifacts;
- Runtime Signals;
- Vector schema owned by this application;
- operation-level and partial indexes;
- LangGraph Checkpointer setup.

### Step 3: Serialize migration

Use one direct one-shot migration connection. Before any migration phase,
acquire:

```sql
SELECT pg_advisory_lock(<stable migration key>)
```

Hold that session lock across all phases and release it in `finally`; a lost
connection releases it automatically.

Each migration declares:

```text
transaction_mode = transactional | autocommit
```

Transactional phase allows only transaction-safe statements currently used
by the repository:

```text
CREATE TABLE IF NOT EXISTS
ALTER TABLE ... ADD COLUMN IF NOT EXISTS
CREATE INDEX IF NOT EXISTS
DROP INDEX IF EXISTS
catalog validation
migration-record insert
```

Autocommit phase is reserved for explicitly reviewed statements such as:

```text
CREATE INDEX CONCURRENTLY
DROP INDEX CONCURRENTLY
VACUUM
```

Do not add `CONCURRENTLY` merely because the migration framework supports it.
Current Stage 48 indexes remain ordinary transactional
`CREATE INDEX IF NOT EXISTS` unless a separately measured table-size/locking
review requires the autocommit phase.

For an autocommit migration:

1. record pending intent in a transaction;
2. execute the non-transactional statement while the session lock remains
   held;
3. validate the catalog result;
4. record completion/checksum in a new transaction.

On restart, pending intent is reconciled from PostgreSQL catalogs before
retry. The migration transaction/phase contains schema work only, never
provider/Graph work or `FOR UPDATE` business mutations.

Repository tests may migrate an isolated prefix while no application workers
use it. A deployed prefix requires an operator-approved drain/maintenance
window before `--apply`.

### Step 4: Add migration version record

Store applied runtime migration IDs/checksums in a prefix-derived migration
table validated by the identifier registry. Do not rewrite an applied
migration. Fail closed on checksum divergence.

### Step 5: Run LangGraph setup only here

The migration command creates a temporary correctly configured Checkpointer
connection/saver, calls `setup()`, verifies expected Checkpointer tables, and
closes it.

`PostgresCheckpointerRuntime.start()` contains no `setup()` call.

### Step 6: Runtime validation

Before serving, validate required business/telemetry/checkpointer schema and
index presence. Validation must not create, alter, or drop anything.

### Step 7: CLI safety

`scripts.postgres_runtime_migrate`:

- defaults to dry-run/plan display or requires an explicit `--apply`;
- prints migration IDs and safe identifier byte lengths only;
- never prints DSN;
- returns nonzero for missing config, invalid identifier, checksum conflict,
  or failed migration;
- supports isolated test prefix;
- is idempotent.

### Step 8: Fresh and existing database tests

Prove:

- fresh schema migrates then validates;
- rerun is idempotent;
- concurrent migration serializes;
- runtime without migration fails read-only;
- old Stage 47 schema upgrades without data loss;
- checkpoints remain readable;
- migration failure rolls back its transaction where PostgreSQL DDL allows.

## Task 11: Compose and Own All Connection Domains

**Files:**

- Modify: `app/services/runtime.py`
- Modify: `app/services/config.py`
- Modify: `.env.example`
- Create: `tests/test_stage48_connection_composition.py`
- Modify: `tests/test_runtime_provider.py`

### Step 1: Create `PostgresConnectionDomains`

It owns:

```text
business
telemetry
advisory_lock
checkpointer_runtime/pool
metrics_registry
```

Creation is thread-safe and process-local. It stores no public DSN-derived
identity.

### Step 2: Lazy role-aware creation

Do not open every pool in every process if that role does not use it.

Examples:

- API: Business, Telemetry, Lock, Checkpointer when durable graphs enabled;
- Celery Report worker: Business, Telemetry, Lock, Checkpointer;
- standalone Outbox worker: Business and Telemetry, Checkpointer only if its
  sink actually invokes graphs;
- migration CLI: one-shot migration connections, no runtime pools.

### Step 3: Inject domains everywhere

Production release tests monkeypatch direct `psycopg2.connect` to fail after
domain startup. Every production workflow must continue through providers.

### Step 4: Atomic startup

`start_runtime()`:

1. validates identifiers/config budget;
2. opens required pools;
3. validates schema;
4. starts Checkpointer;
5. builds workflow services;
6. starts maintenance/outbox.

Partial failure unwinds in reverse order.

### Step 5: Ordered bounded shutdown

Stop producers, wait for workers, close Checkpointer/lock admission, drain
leases, and close domains. Assert no pool worker thread, heartbeat, Outbox
thread, server, or connection remains.

### Step 6: Reset tests

`reset_runtime_for_tests()` closes Stage 48-owned pools and clears identities,
metrics, services, and startup state. Existing unrelated resources remain
untouched.

## Task 12: Build Multi-Process Capacity Evidence

**Files:**

- Modify: `app/services/postgres_capacity.py`
- Create: `scripts/postgres_capacity_acceptance.py`
- Create: `tests/test_postgres_capacity_postgres.py`
- Modify: `scripts/runtime_preflight.py`

### Step 1: Query safe PostgreSQL capacity settings

Read:

```text
max_connections
superuser_reserved_connections
current aggregate connection count
Stage 48 application_name aggregate count
```

Do not emit host, database, user, client address, PID, query, or application
request identifiers.

### Step 2: Calculate role budgets

Use configured API, Celery, and Outbox process counts and per-role used
domains. Reject zero/negative or implausibly large values before connecting.

For every role using Checkpointer, add:

```text
checkpointer_pool_max
+ POSTGRES_CHECKPOINTER_POOL_OVERHEAD
```

with repository default overhead `1`. Report this as conservative reserve,
not as an observed psycopg_pool maintenance connection.

The real overlap harness tags only Stage 48 Checkpointer connections with a
stable non-sensitive application name and queries their aggregate count.
Assert:

```text
observed_checkpointer_peak
    <= checkpointer_pool_max + configured_overhead
```

Also record whether observed peak was at or below `pool_max`; this lets a
deployment later reduce the conservative overhead only with evidence.

### Step 3: Run deterministic overlap load

Use barriers to hold real connections simultaneously up to each domain max.
Measure:

- peak leased;
- waiting;
- timeouts;
- wait histogram;
- connection reuse;
- database-observed max;
- recovery after release.

Do not use provider calls or arbitrary sleeps as the concurrency proof.

### Step 4: Define eligibility

Mandatory repository capacity conditions:

- identifier/schema/preflight PASS;
- total configured budget within allowed available fraction;
- zero unexpected acquire timeouts under target deterministic load;
- no domain exceeds configured max;
- observed Checkpointer sessions do not exceed max plus configured overhead;
- Telemetry saturation does not consume Business capacity;
- no leaked connections after harness;
- no leaked advisory locks;
- Checkpointer recovery PASS.

Wait P95 thresholds are recorded but deployment-specific thresholds require
operator approval.

### Step 5: Emit `postgres-capacity-v1`

Example shape:

```json
{
  "schema_version": "postgres-capacity-v1",
  "status": "ELIGIBLE_FOR_CAPACITY_CANARY",
  "process_budget": {
    "configured_total": 48,
    "available": 80,
    "allowed_at_utilization": 64
  },
  "domains": {
    "business": {
      "max_size": 12,
      "peak_leased": 12,
      "acquire_timeout_count": 0
    }
  },
  "privacy_violations": 0
}
```

Actual numbers come from configuration/evidence, not this example.

### Step 6: Distinguish evidence levels

Artifact status is one of:

```text
BLOCKED_CONFIG
BLOCKED_SCHEMA
BLOCKED_BUDGET
FAILED_LOAD
ELIGIBLE_FOR_CAPACITY_CANARY
```

It never claims deployed PASS.

## Task 13: Combined Connection, Recovery, and Fault Matrix

**Files:**

- Extend all new Stage 48 tests
- Extend Stage 46/47/47.1/47.2 PostgreSQL matrices

Use real overlaps and injected faults:

| Scenario | Required result |
| --- | --- |
| Business max reached | bounded waiter/timeout; no extra DB connection |
| Telemetry max reached | Business unaffected; telemetry best effort |
| Lock pool max reached | pool exhaustion distinct from lock busy |
| Same workflow lock contended | existing `WorkflowThreadBusy` |
| Lock connection lost | ownership lost; connection discarded |
| Unlock false | connection discarded; stable lock-lost result |
| Transaction body raises | rollback; next borrower sees clean session |
| Commit/reset fails | connection discarded |
| Pool closes with waiter | waiter gets stable closed error |
| Shutdown with active lease | bounded drain; no new admission |
| Checkpointer concurrent start | one pool/saver |
| Installed Saver pool API changes | release blocked before runtime construction |
| Checkpointer open fails | cleanup; explicit retry works |
| Schema missing | runtime fails read-only; no DDL |
| Concurrent migrations | one serialized application |
| Transaction-forbidden DDL declared transactional | rejected before execution |
| Autocommit migration interrupted | pending intent reconciled from catalogs |
| Identifier >63 bytes | rejected before connect |
| UTF-8 multibyte overflow | rejected before connect |
| Budget exceeds server allowance | preflight blocked |
| PostgreSQL interruption | pools recover or fail with stable code |
| Generation/Report lease loss | Stage 47.1 fail-closed unchanged |
| Agent Recorder exhausted | Stage 47.2 business result unchanged |
| Process replacement | existing checkpoints and projections recover |

Required exact assertions:

- physical connect count;
- peak concurrent connections per domain;
- pool wait/timeout/discard counts;
- provider/Graph call counts;
- SQL effect counts;
- no duplicate projection/report/effect;
- no leaked transaction, connection, lock, thread, table, or artifact;
- no sensitive log/artifact field.

## Task 14: Acceptance, Operator Documentation, and Final Gates

**Files:**

- Finalize: `docs/postgres-connection-capacity-acceptance.md`
- Modify: `docs/local-v1-runbook.md`
- Create: `tests/test_stage48_release_contract.py`
- Modify prior release contracts only for additive compatibility

### Step 1: Acceptance runner

`scripts.postgres_capacity_acceptance` runs:

- identifier/config validation;
- migration/schema validation;
- provider/pool unit tests;
- real PostgreSQL domain tests;
- deterministic capacity harness;
- Checkpointer recovery;
- Advisory Lock matrix;
- Stage 47.2 telemetry regression;
- rollout-default contract.

It suppresses DSNs and raw database errors.

### Step 2: Acceptance states

Before all gates:

```text
IN_PROGRESS
Production observation: NOT_RUN
```

After all repository gates:

```text
READY_FOR_CAPACITY_AWARE_FENCING_CANARY
Production observation: NOT_RUN
```

### Step 3: Focused unit gates

```powershell
python -m pytest -q `
  tests/test_postgres_identifiers.py `
  tests/test_postgres_connections.py `
  tests/test_postgres_capacity.py `
  tests/test_postgres_runtime_migrations.py `
  tests/test_stage48_connection_composition.py `
  tests/test_stage48_release_contract.py
```

### Step 4: Real PostgreSQL gates

```powershell
python -m pytest -q `
  tests/test_postgres_connections_postgres.py `
  tests/test_postgres_capacity_postgres.py `
  tests/test_postgres_runtime_migrations_postgres.py `
  tests/test_stage48_business_pool_postgres.py `
  tests/test_stage48_telemetry_pool_postgres.py `
  tests/test_stage48_lock_pool_postgres.py `
  tests/test_stage48_checkpointer_postgres.py
```

No mandatory PostgreSQL test may be skipped.

### Step 5: Existing Store and workflow regressions

Run Session, Runtime Control, Generation, Workflow, Report Job, Review Effect,
Vector Store, Agent Runtime, and all LangGraph recovery/fencing markers.

### Step 6: Full repository gates

```powershell
python -m pytest -q
npm.cmd run build:prototype-css
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
python -m compileall -q app scripts tests
git diff --check
```

Real-model browser tests remain opt-in. Do not take screenshots unless
separately requested.

### Step 7: Capacity and migration commands

```powershell
python -m scripts.postgres_runtime_migrate
python -m scripts.postgres_runtime_migrate --apply
python -m scripts.runtime_preflight --profile postgres-capacity
python -m scripts.postgres_capacity_acceptance
```

Run `--apply` only against an isolated test prefix during repository
acceptance. A deployed migration requires separate operator authorization.

### Step 8: Mechanical contracts

Assert:

- committed rollout is `0/0`;
- runtime start source contains no `saver.setup()`;
- production Store composition has no direct provider fallback;
- all derived identifiers are registered/validated;
- `langgraph-canary-v2` is unchanged;
- `postgres-capacity-v1` contains no sensitive fields;
- Stage 48 does not claim production observation.

### Step 9: Cleanup

Verify zero:

- Stage 48 test tables/migration tables;
- Stage 48 application-name connections;
- advisory locks;
- pool worker threads;
- Checkpointer contexts;
- Outbox/maintenance threads;
- browser/WebServer processes and port;
- Playwright results/traces;
- temporary capacity artifacts outside the accepted directory.

Resolve and validate filesystem paths before recursive deletion. Drop only
strictly validated isolated PostgreSQL prefixes.

## Acceptance Criteria

Stage 48 is complete only when:

1. Every configured runtime identifier and all derived identifiers fit in 63
   UTF-8 bytes and cannot collide through truncation.
2. Invalid identifiers fail before any connection or DDL.
3. Business, Telemetry, Lock, and Checkpointer domains have distinct bounded
   capacities.
4. Business Stores use the injected Business provider in production.
5. Telemetry saturation cannot consume Business pool capacity.
6. Advisory-lock connections remain session-exclusive until unlock.
7. Lost/unlock-failed lock connections are discarded.
8. Successful SQL commits and failed SQL rolls back before reuse.
9. Broken/reset-failed connections are discarded and replaced.
10. Pool acquire and drain waits are bounded.
11. Pool exhaustion has a stable privacy-safe classification.
12. Checkpointer uses an explicitly opened psycopg3 pool.
13. Concurrent Checkpointer start creates one pool/saver.
14. Checkpointer runtime start performs no schema setup.
15. Runtime Store construction performs validation, not DDL.
16. Explicit migration is serialized, idempotent, and checksum-safe.
17. Fresh and existing Stage 47 schemas migrate/validate without data loss.
18. Existing checkpoints remain recoverable after process replacement.
19. Pool metrics are exact, bounded-memory, and privacy-safe.
20. Multi-process configured connection budget fits the allowed PostgreSQL
    capacity fraction.
21. Deterministic overlap load stays within per-domain max and has no
    unexpected timeouts.
22. Repository capacity artifact is `postgres-capacity-v1`.
23. `langgraph-canary-v2` remains unchanged.
24. Stage 46 single-writer/fencing tests pass.
25. Stage 47 canary/recovery tests pass.
26. Stage 47.1 heartbeat fail-closed tests pass.
27. Stage 47.2 Agent telemetry/privacy tests pass.
28. Full Python, PostgreSQL, CSS, browser, compile, diff, privacy, and cleanup
    gates pass.
29. No connection, advisory lock, transaction, pool thread, runtime thread,
    test table, port, or temporary artifact remains.
30. Committed rollout defaults remain `0/0`.
31. Repository status is `READY_FOR_CAPACITY_AWARE_FENCING_CANARY`.
32. Production observation remains `NOT_RUN` unless separately authorized and
    actually observed.
33. A real behavioral compatibility test proves the installed
    `PostgresSaver` accepts and operates through `psycopg_pool.ConnectionPool`
    without pipeline.
34. psycopg2 wait timeout uses a Condition predicate/reservation and does not
    expose or call `getconn()` before capacity is reserved.
35. Session, Interview Workflow, and Review Workflow nested Runtime Control
    Stores receive the exact parent Business provider and never reconstruct
    one from DSN.
36. Migration statements are classified transactional/autocommit; forbidden
    statements cannot enter the wrong phase, and one session advisory lock
    serializes both phases.
37. Multi-process budget includes the configured Checkpointer overhead and
    real PostgreSQL observation stays within max plus overhead.
38. `PostgresWorkflowThreadLock` rejects simultaneous
    `exclusive_provider` and legacy `connect` configuration, while production
    composition uses only the exclusive provider.

## Rollback Triggers

Rollback the affected Stage 48 batch if:

- a Store transaction loses atomicity;
- a connection is reused with an open/failed transaction;
- a broken or lock-lost connection returns to a pool;
- Telemetry exhaustion blocks Business work;
- a lock connection is shared before unlock;
- Checkpointer startup creates multiple pools/savers;
- runtime performs DDL;
- migration changes existing checkpoint/business data unexpectedly;
- an identifier relies on PostgreSQL truncation;
- pool wait is unbounded;
- shutdown closes an actively admitted connection without first stopping
  admission and bounded drain;
- pool errors leak DSN/SQL/credentials;
- Stage 46/47/47.1/47.2 semantics regress;
- rollout defaults change.

Rollback only the owning batch through reviewed patches or commit reverts.
Never use destructive workspace resets.

## Explicit Post-Stage-48 Backlog

- **Deployed capacity canary:** Run migration and `postgres-capacity-v1`
  observation in an authorized environment with real API/Celery/Outbox
  process counts before any higher rollout.
- **Stage 49 — Checkpoint and AgentRun lifecycle/privacy governance:** Add
  retention, age/capacity monitoring, dry-run cleanup, backup/restore, legal
  policy, and active-thread safety.
- **psycopg3 Business migration:** Evaluate replacing psycopg2 after pooled
  behavior is stable; do not combine with Stage 48.
- **External metrics backend:** Export domain gauges/histograms to the chosen
  production metrics system after its privacy and cardinality contract is
  approved.
- **Adaptive pool sizing:** Consider only after deployed wait/utilization
  evidence. Static explicit budgets remain the Stage 48 safety baseline.
- **Checkpointer throughput redesign:** Current synchronous Saver lock may
  serialize calls even with a pool. Multiple saver instances or async Saver
  require a separate correctness/performance plan.
- **Stage 50/51 Graph v2:** State externalization and Review question retry
  remain blocked on separate versioned migration plans.
- **Legacy retirement:** Still requires deployed ownership and capacity
  evidence plus historical read compatibility.
