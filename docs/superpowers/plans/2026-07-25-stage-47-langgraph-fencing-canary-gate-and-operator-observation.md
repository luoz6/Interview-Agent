# Stage 47 LangGraph Fencing Canary Gate and Operator Observation Plan

> **Execution note:** Implement the repository tasks in order and begin every
> task with the stated failing test. Keep both committed rollout percentages at
> zero. No code path, migration, test, preflight command, or canary CLI command
> may change a deployed rollout value. The operator observation section is a
> manual, separately authorized procedure, not an instruction for an
> implementation agent to mutate production.

**Goal:** Turn the Stage 46 fencing implementation into a trustworthy,
privacy-safe production canary gate, then provide a fixed operator procedure
that proves single-writer locking, lease fencing, replay-safe Review effects,
assignment-only rollback, and durable drain in a real multi-worker deployment.

**Architecture:** Preserve the existing `langgraph-v1` and
`langgraph-review-v1` graphs and their Stage 46 execution-authority model.
Correct the canary's PostgreSQL view of unfinished Outbox work, separate normal
command-version conflicts from true projection divergence, and classify
projection divergence with a stable non-retryable code. Persist transient
ownership signals in minute-level aggregate buckets containing no workflow
identifiers or payloads, because mutable `last_error_code` columns are cleared
after successful retries and cannot serve as a complete observation history.
Evaluate correctness conflicts as immediate rollback signals, ownership loss
as hold signals, and ordinary backlog/failure thresholds as operational hold
signals. After repository gates pass, an operator may run the fixed
`0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0` sequence with an explicit UTC
phase boundary, duration, sample requirement, decision, and drain proof at
every hold point.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, LangGraph 1.2,
`langgraph-checkpoint-postgres` 3.1, PostgreSQL 16, psycopg2, pytest,
Playwright, the existing runtime Outbox and Report Job stores, privacy-safe
runtime auditing, and the existing LangGraph canary CLI.

**Baseline:** Stage 46 is `READY_FOR_FENCING_CANARY`. Its recorded evidence is
31 `pg_runtime`, 17 `pg_control`, 10 `langgraph_recovery`, 6
`langgraph_review_recovery`, 70 focused PostgreSQL concurrency/recovery tests,
1098 passed and 1 skipped in the full Python suite, and 37 passed and 9 skipped
in the browser suite. Both committed rollout defaults remain zero. This plan
does not reinterpret that repository evidence as a deployed canary.

---

## Execution Preconditions

1. Preserve the complete Stage 46 working tree before editing Stage 47 files.
   Do not reset, delete, or rewrite unrelated tracked or untracked work.
2. Do not create a commit unless the user explicitly requests one.
3. Run schema and PostgreSQL tests against an isolated database or a unique
   `INTERVIEW_RUNTIME_TABLE_PREFIX`. Never test a first migration against
   production tables.
4. Keep these committed defaults unchanged:

   ```text
   INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
   REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
   INTERVIEW_LANGGRAPH_RUNTIME_ENABLED=true
   REPORT_LANGGRAPH_RUNTIME_ENABLED=true
   LANGGRAPH_STRICT_MSGPACK=true
   ```

5. Existing `langgraph-v1` Interview threads and
   `langgraph-review-v1` Review threads must remain resumable after every task.
6. Repository acceptance uses deterministic fake providers. Real candidate
   traffic and real provider calls belong only to an explicitly authorized
   deployed canary.
7. Canary evidence may contain UTC timestamps, rollout pairs, durations,
   aggregate counts/rates, stable reason codes, database major version, and a
   short deployment revision. It must not contain raw session, job, command,
   generation, attempt, effect, correlation, checkpoint, or thread IDs; lease
   tokens; fencing versions tied to an identity; DSNs; credentials; answers;
   resumes; evidence; chunks; feedback; reports; provider payloads; or hashes
   derived from business content.
8. A repository gate may end at `READY_FOR_OPERATOR_FENCING_CANARY`. Only a
   separately authorized and actually observed deployment may produce a scoped
   `PASS_FENCING_CANARY` result.

## Scope

This stage covers:

- versioning the canary snapshot contract so corrected field meanings do not
  silently alter `langgraph-canary-v1` artifacts;
- correcting Outbox backlog queries to use the real
  `pending/retrying/running` state model;
- separating stale command conflicts from true projection divergence;
- mapping `ProjectionConflict` to a stable, non-retryable runtime failure;
- recording transient ownership/fencing/effect signals in privacy-safe minute
  buckets without storing workflow identity or content;
- wiring exactly one outer execution boundary to record each signal;
- adding expired-lease grace semantics so a row crossing its lease timestamp
  during a snapshot does not create a false positive;
- evaluating correctness signals as `ROLL_BACK` and ownership/operational
  signals as `HOLD`;
- requiring independent Interview and Review sample sizes based on the current
  rollout pair rather than adding both samples together;
- adding explicit UTC phase boundaries to prevent one phase's rows from being
  counted in another phase;
- producing complete privacy-safe Markdown and JSON evidence for every phase;
- extending runtime preflight, PostgreSQL canary acceptance, runbooks, and
  release-contract tests;
- running a deterministic synthetic Stage 47 matrix at rollout zero;
- defining, but not automatically executing, the operator-authorized deployed
  canary sequence and assignment-only rollback/drain procedure.

## Non-Goals

- Do not change `DurableInterviewState` or `DurableReviewState`.
- Do not register `langgraph-v2` or `langgraph-review-v2`.
- Do not externalize Interview messages or `plan_snapshot`.
- Do not introduce question-level Review retry.
- Do not add Checkpointer, application SQL, or advisory-lock connection pools.
- Do not move `PostgresSaver.setup()` into migrations in this stage.
- Do not delete or retain-prune LangGraph checkpoints.
- Do not rename public session `checkpoint_version` fields.
- Do not retire or migrate Legacy Interview or Legacy Review ownership.
- Do not change SSE transport semantics.
- Do not merge Interview and Review into a parent graph.
- Do not automatically promote from 1% to 5%, 25%, 50%, or 100%.
- Do not automatically apply `1/0`, `0/1`, or `1/1` to any deployment.
- Do not claim exactly-once external provider invocation. Stage 46 guarantees a
  stable committed logical effect after the artifact exists; a process can
  still fail after a provider returns and before the first artifact commit.
- Do not treat a successful local synthetic canary as production evidence.

## Fixed Decisions

1. **Stage 47 validates Stage 46; it does not redesign it.** PostgreSQL advisory
   locks, Generation fencing, Report Job fencing, effect claims, and atomic
   Review commit remain unchanged unless a canary-gate test exposes a defect.
2. **The snapshot contract becomes `langgraph-canary-v2`.** Renaming the old
   `projection_conflict_count` and adding phase/ownership semantics is not a
   backward-compatible meaning change. Existing v1 artifacts remain historical
   records; the CLI emits v2 after this stage.
3. **Command conflict and projection divergence are different.** A command with
   a stale `expected_version` increments `command_conflict_count` and is not by
   itself a rollback signal. A version/digest mismatch in
   `project_state()` increments `projection_divergence_count` and is an
   immediate rollback signal.
4. **Projection divergence is non-retryable.** `ProjectionConflict` maps to
   `projection_conflict, retryable=False`. Repeating a provider call cannot
   repair divergent authoritative state.
5. **Unfinished Outbox work uses the real state model.** Backlog gauges include
   `pending`, `retrying`, and `running`. `processing` is a Report projection
   status, not an Outbox status, and must not appear in Outbox canary SQL. This
   correction applies independently to both the unfinished-row count query and
   the oldest-unfinished-age query; fixing only one leaves the snapshot
   internally inconsistent.
6. **Transient incident history cannot rely on mutable last-error columns.** A
   later successful claim clears `last_error_code`. Stage 47 therefore stores
   only aggregate minute buckets for stable runtime signal codes.
7. **Signal buckets contain no identity.** The key is
   `(bucket_start, workflow_type, signal_code)`. The value is a non-negative
   count. There is no session, job, command, generation, effect, thread,
   correlation, token, digest, payload, or free-form error column.
   `signal_code` is validated against a closed `Literal` allowlist; arbitrary
   exception text or caller-provided strings can never become metric keys.
8. **Signal recording is best-effort and non-authoritative.** Failing to record
   an observability bucket must never change a business outcome or hide the
   original exception. The boundary logs only a stable code and records a
   `canary_signal_write_failed` health signal when possible. Database health is
   still checked independently by preflight.
9. **One boundary owns each signal.** Interview command/retry execution signals
   are recorded by the runtime Outbox dispatch boundary. Durable Review
   execution signals are recorded by the Report Worker boundary. Store layers
   raise typed exceptions but do not independently increment the same bucket.
10. **Correctness conflicts roll back immediately.** Privacy failure, true
    projection divergence, Review effect identity conflict, Report final-commit
    conflict, acknowledged command loss, duplicate business projection, public
    version regression, and unknown graph fallback yield `ROLL_BACK`.
11. **Ownership anomalies hold.** Lock loss, Generation or Report lease loss,
    fenced-write rejection, and expired active ownership yield `HOLD`. A fenced
    rejection proves the guard worked, but also proves unexpected owner overlap
    occurred and requires investigation before promotion.
12. **Busy is not a correctness conflict.** Thread/effect busy means another
    live owner exists. The initial fencing canary uses a zero tolerance and
    holds for investigation; later rollout stages may explicitly approve a
    non-zero threshold without changing the rollback taxonomy.
13. **Expired gauges use a grace interval.** Count an active lease/effect claim
    as expired only when its expiry is older than the configured grace period.
    The default Stage 47 repository value is 30 seconds; an operator must record
    the deployed value.
14. **Samples are workflow-specific.** `1/0` requires the Interview minimum but
    not the Review minimum. `0/1` requires the Review minimum but not the
    Interview minimum. `1/1` requires both independently. `0/0` evaluates
    health and drain without manufacturing an assignment sample requirement.
15. **A phase has an explicit UTC start.** An operator evaluation must pass
    `--since-utc`. A rolling 60-minute window alone can mix `1/0`, `0/0`, and
    `0/1` rows and is not acceptable production evidence.
16. **Time and sample are separate gates.** The CLI evaluates sample counts;
    the observation record also requires an operator-approved minimum duration.
    Meeting one never implies the other.
17. **The CLI remains read-only.** Exit 0 means `ELIGIBLE_TO_CONTINUE`, exit 2
    means `HOLD`, and exit 3 means `ROLL_BACK`. No exit path changes environment
    variables, deployment configuration, database ownership, or graph version.
18. **Rollback is assignment-only.** Returning a rollout to zero affects only
    new assignment. Existing Durable sessions/jobs keep their engine and graph
    version and must drain with saver, workers, consumers, and graph registry
    still available.
19. **Every phase returns to a known hold point.** The first deployed sequence
    is fixed as `0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0`.
20. **Acceptance is scoped.** Repository readiness, local synthetic evidence,
    staging observation, and production observation are distinct claims. The
    environment and deployment revision must be named without exposing
    infrastructure secrets.

## Decision Taxonomy

| Signal | Source | Default decision | Reason |
| --- | --- | --- | --- |
| `privacy_audit_failed` | artifact/runtime audit | `ROLL_BACK` | Canary evidence crossed the privacy boundary. |
| `projection_conflict` | Interview projection exception | `ROLL_BACK` | Graph state and authoritative business projection diverged. |
| `review_effect_conflict` | Review effect identity exception | `ROLL_BACK` | One immutable operation identity mapped to conflicting data. |
| `report_commit_conflict` | Review Run/final commit | `ROLL_BACK` | Final business projections did not agree atomically. |
| external correctness stop codes | operator evidence | `ROLL_BACK` | A business invariant was observed to fail. |
| `workflow_thread_lock_lost` | Interview/Review outer boundary | `HOLD` | The session holding execution authority disappeared. |
| `generation_lease_lost` | Interview outer boundary | `HOLD` | Generation ownership changed during execution. |
| `report_lease_lost` | Report Worker boundary | `HOLD` | Review ownership changed during execution. |
| `fenced_write_rejected` | Interview/Review outer boundary | `HOLD` | A stale owner attempted a guarded mutation. |
| expired Generation/Report/effect ownership | PostgreSQL gauge with grace | `HOLD` | Work may be awaiting recovery or stuck. |
| `workflow_thread_busy` | outer boundary signal bucket | `HOLD` above configured threshold | Live-owner contention requires rate investigation. |
| `review_effect_busy` | Report Worker signal bucket | `HOLD` above configured threshold | A duplicate effect reached a live claim. |
| Outbox age/backlog | PostgreSQL gauge | `HOLD` | Runtime delivery is not draining within its SLO. |
| stale Interview/Review work | PostgreSQL gauge | `HOLD` | Durable work stopped making progress. |
| Review terminal failure rate | Report Jobs | `HOLD` | Quality/availability is outside the accepted envelope. |
| insufficient workflow-specific sample | assignment rows | `HOLD` | The phase has not observed enough work. |

## Canary v2 Snapshot Contract

The exact implementation may group fields in nested Pydantic models, but the
serialized contract must expose only the following safe categories:

```text
schema_version = langgraph-canary-v2
generated_at
observed_since
window_seconds
phase
rollout_pair

interview_assigned_count
interview_terminal_count
interview_retrying_count
review_assigned_count
review_terminal_count
review_failed_count
review_retrying_count

outbox_pending_count
outbox_retrying_count
outbox_running_count
oldest_unfinished_outbox_age_seconds
expired_running_outbox_lease_count

command_conflict_count
projection_divergence_count
report_commit_conflict_count

workflow_thread_busy_count
workflow_thread_lock_lost_count
generation_lease_lost_count
fenced_write_rejected_count
report_lease_lost_count
review_effect_busy_count
review_effect_conflict_count

expired_generation_lease_count
expired_report_lease_count
running_review_effect_count
expired_review_effect_claim_count

checkpoint_row_count
generation_chunk_row_count
review_artifact_row_count
review_effect_row_count

privacy_audit
recommendation
reasons
```

`running_review_effect_count` is informational. Only an effect claim older than
the grace interval is a hold signal.

## Release State Model

```text
Stage 46 = READY_FOR_FENCING_CANARY
        |
        v
Stage 47 repository gate hardening
        |
        v
Stage 47 repository acceptance = READY_FOR_OPERATOR_FENCING_CANARY
Operator observation = NOT_RUN
        |
        | explicit deployment authority
        v
0/0 baseline
  -> 1/0 Interview
  -> 0/0 Interview drain
  -> 0/1 Review
  -> 0/0 Review drain
  -> 1/1 joint
  -> 0/0 final drain
        |
        +-- correctness/privacy breach --> ROLLED_BACK
        +-- insufficient/operational issue --> HOLD
        +-- all phase gates pass --> PASS_FENCING_CANARY (scoped environment)
```

## File Map

| Area | Primary files |
| --- | --- |
| Failure taxonomy | `app/services/workflow_thread_lock.py`, `app/services/interview_workflow_store.py`, `app/services/runtime_work.py` |
| Signal buckets | `app/services/runtime_signal_metrics.py`, `app/services/runtime.py`, `app/services/runtime_outbox_dispatcher.py`, `app/services/report_worker.py` |
| Canary model/SQL | `app/services/langgraph_canary_status.py` |
| Canary CLI/artifacts | `scripts/langgraph_canary.py`, `scripts/audit_agent_runtime.py` |
| Schema/preflight/maintenance | `scripts/runtime_preflight.py`, `app/services/durable_workflow_maintenance.py`, `app/services/config.py`, `.env.example` |
| Release documentation | `docs/local-v1-runbook.md`, `README.md`, `docs/langgraph-stage46-acceptance.md`, new Stage 47 acceptance/observation records |
| Unit tests | `tests/test_runtime_work.py`, `tests/test_langgraph_canary_status.py`, `tests/test_langgraph_canary_cli.py`, `tests/test_runtime_signal_metrics.py` |
| PostgreSQL tests | `tests/test_langgraph_canary_status_postgres.py`, `tests/test_dual_langgraph_canary_postgres.py`, existing recovery markers |

---

## Task 1: Freeze the Stage 47 Release Contract

**Files:**

- Create: `docs/langgraph-stage47-fencing-canary-acceptance.md`
- Create: `docs/langgraph-stage47-fencing-canary-observation.md`
- Create: `tests/test_langgraph_stage47_release_contract.py`
- Modify: `docs/langgraph-stage46-acceptance.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write failing release-contract tests**

Require the repository record to begin with:

```text
Status: PENDING_GATE_HARDENING
```

Require the operator record to begin with:

```text
Status: NOT_RUN
```

The tests must require both documents to state:

- Stage 46 is the prerequisite;
- committed rollout defaults remain zero;
- repository readiness does not authorize production;
- the fixed seven-phase sequence;
- assignment-only rollback and durable drain;
- the v2 privacy boundary;
- exactly-once external provider invocation is not claimed;
- connection pools, State v2, retention, and Legacy retirement are deferred.

- [ ] **Step 2: Create the two records with safe placeholders**

The repository record contains named gates and pass/fail placeholders. The
operator record contains phase rows with no infrastructure secrets:

```text
phase | rollout | started_at | ended_at | samples | decision | reasons
```

Do not pre-populate production timestamps, samples, or a PASS decision.

- [ ] **Step 3: Link Stage 46 without changing its decision**

Append a short note that Stage 47 owns deployed fencing observation. Keep Stage
46 at `READY_FOR_FENCING_CANARY`.

- [ ] **Step 4: Run the focused contract gate**

```powershell
python -m pytest -q tests/test_langgraph_stage47_release_contract.py tests/test_local_v1_docs.py
```

Expected result: pass, with both rollout defaults still zero.

## Task 2: Give Projection Divergence a Stable Failure Identity

**Files:**

- Modify: `app/services/workflow_thread_lock.py`
- Modify: `app/services/interview_workflow_store.py`
- Modify: `app/services/runtime_work.py`
- Modify: `tests/test_runtime_work.py`
- Modify: `tests/test_interview_workflow_store.py`
- Modify: `tests/test_runtime_outbox_dispatcher.py`

- [ ] **Step 1: Write failing classifier tests**

Add exact assertions:

```python
ProjectionConflict -> RuntimeFailure("projection_conflict", False)
ReviewEffectConflict -> RuntimeFailure("review_effect_conflict", False)
FencedWriteRejected -> RuntimeFailure("fenced_write_rejected", False)
```

Also prove a stale command conflict remains a normal command outcome and does
not raise `ProjectionConflict`.

- [ ] **Step 2: Canonicalize the exception without breaking imports**

Define `ProjectionConflict` beside the other workflow ownership/fencing domain
exceptions in `workflow_thread_lock.py`. Import and re-export it from
`interview_workflow_store.py` so existing callers and tests that import from the
store keep working.

- [ ] **Step 3: Classify it as non-retryable**

Add the mapping before generic `RuntimeError`/unexpected handling. Preserve the
original typed exception for safe internal logging, but never include the
session ID in canary evidence.

- [ ] **Step 4: Prove the outer boundary persists the stable code**

Inject a projection conflict through the Interview event path and assert the
Outbox handling outcome uses `projection_conflict` and does not repeat provider
work.

- [ ] **Step 5: Run the focused gate**

```powershell
python -m pytest -q tests/test_runtime_work.py tests/test_interview_workflow_store.py tests/test_runtime_outbox_dispatcher.py
```

## Task 3: Define the Canary v2 Evaluation Contract

**Files:**

- Modify: `app/services/langgraph_canary_status.py`
- Modify: `tests/test_langgraph_canary_status.py`

- [ ] **Step 1: Replace aggregate-sample tests with phase-aware failures**

Add failing tests for:

```python
def test_interview_phase_requires_interview_sample_only(): ...
def test_review_phase_requires_review_sample_only(): ...
def test_joint_phase_requires_both_samples(): ...
def test_zero_zero_phase_evaluates_health_without_assignment_sample(): ...
def test_projection_divergence_rolls_back(): ...
def test_effect_conflict_rolls_back(): ...
def test_ownership_loss_holds(): ...
def test_expired_ownership_holds_after_grace(): ...
def test_running_nonexpired_effect_is_informational(): ...
def test_busy_above_threshold_holds_without_becoming_rollback(): ...
```

- [ ] **Step 2: Introduce `langgraph-canary-v2`**

Rename the old ambiguous field:

```text
projection_conflict_count -> command_conflict_count
```

Add a separate:

```text
projection_divergence_count
```

Do not deserialize a v1 payload as v2 by silently applying defaults.

- [ ] **Step 3: Model phase and explicit time boundary**

Add:

```python
phase: Literal["baseline", "interview", "interview_drain", "review", "review_drain", "joint", "final_drain"]
observed_since: str
window_seconds: int
```

Validate rollout pairs:

```text
baseline/interview_drain/review_drain/final_drain = 0/0
interview = 1/0
review = 0/1
joint = 1/1
```

The first Stage 47 canary does not accept other positive percentages.

- [ ] **Step 4: Separate immutable stop gates from operator thresholds**

Correctness/privacy thresholds are always zero and cannot be relaxed by CLI
arguments. Operational thresholds remain explicit fields, including:

```text
minimum_interview_sample
minimum_review_sample
max_oldest_outbox_age_seconds
max_review_failure_rate
max_workflow_thread_busy_count
max_review_effect_busy_count
lease_expiry_grace_seconds
```

- [ ] **Step 5: Preserve deterministic reason ordering**

Sort and deduplicate stable reason codes. One snapshot may contain both rollback
and hold conditions, but any rollback condition determines the final
recommendation.

- [ ] **Step 6: Run the unit gate**

```powershell
python -m pytest -q tests/test_langgraph_canary_status.py
```

## Task 4: Add Privacy-Safe Runtime Signal Buckets

**Files:**

- Create: `app/services/runtime_signal_metrics.py`
- Create: `tests/test_runtime_signal_metrics.py`
- Create: `tests/test_runtime_signal_metrics_postgres.py`
- Modify: `app/services/runtime.py`
- Modify: `app/services/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Write the pure contract test**

Require a typed API similar to:

```python
store.increment(workflow_type="interview", signal_code="workflow_thread_busy")
store.sum_since(observed_since, workflow_type=None)
store.cleanup_older_than(hours=...)
```

Define a closed `CanarySignalCode` allowlist for the Stage 47 codes. Reject
unknown workflow types, unknown or blank codes, free-form messages, payloads,
and identity fields. Tests must prove candidate/provider text cannot be passed
as a signal code and cannot create a high-cardinality bucket.

- [ ] **Step 2: Add an idempotent PostgreSQL schema**

Use a prefixed table with this logical shape:

```sql
CREATE TABLE runtime_signal_buckets (
    bucket_start TIMESTAMPTZ NOT NULL,
    workflow_type TEXT NOT NULL,
    signal_code TEXT NOT NULL,
    signal_count BIGINT NOT NULL CHECK (signal_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bucket_start, workflow_type, signal_code)
)
```

The database chooses `date_trunc('minute', NOW())`. Use an atomic upsert that
increments `signal_count`. Add only the index needed for bounded time-window
reads if the primary key cannot serve the query plan.

- [ ] **Step 3: Prove privacy structurally**

Catalog tests must assert the table has no columns matching identity, payload,
message, answer, report, token, hash, checkpoint, or error-detail categories.

- [ ] **Step 4: Add bounded retention**

Add:

```text
LANGGRAPH_CANARY_SIGNAL_RETENTION_HOURS=168
```

Validation requires a positive value. Cleanup uses database time, is bounded to
old buckets, and never touches business control or checkpoint tables.

- [ ] **Step 5: Prove concurrent increments**

Use independent PostgreSQL connections and a barrier. Exact final count must
equal the number of successful increments; no update may be lost.

- [ ] **Step 6: Run the focused gates**

```powershell
python -m pytest -q tests/test_runtime_signal_metrics.py
python -m pytest -q tests/test_runtime_signal_metrics_postgres.py -m pg_control
```

## Task 5: Instrument Exactly One Runtime Boundary per Signal

**Files:**

- Modify: `app/services/runtime_outbox_dispatcher.py`
- Modify: `app/services/report_worker.py`
- Modify: `app/services/runtime.py`
- Modify: `app/services/workflow_thread_lock.py`
- Modify: `tests/test_runtime_outbox_dispatcher.py`
- Modify: `tests/test_report_worker.py`
- Modify: `tests/test_workflow_thread_lock.py`

- [ ] **Step 1: Write exact-count tests**

For each injected exception, assert one and only one bucket increment:

```text
workflow_thread_busy
workflow_thread_lock_lost
generation_lease_lost
fenced_write_rejected
projection_conflict
report_lease_lost
review_effect_busy
review_effect_conflict
report_commit_conflict
```

The test must record a list/count, not a set, so duplicate instrumentation is
visible.

- [ ] **Step 2: Instrument Interview at the Outbox execution boundary**

Record classified Interview command/retry failures after classification and
before the Outbox transition. Do not record again inside graph nodes or Stores.

- [ ] **Step 3: Instrument Review at the Report Worker boundary**

Record typed Durable Review failures before the token-fenced release/get path.
Legacy report generation must not be mislabeled as a LangGraph fencing signal.

- [ ] **Step 4: Use lock `metric_callback` only for latency, not duplicate error counts**

The existing callback may report aggregate acquire/wait/release timings to the
deployed metrics system. Stable incident buckets remain owned by the outer
boundary.

- [ ] **Step 5: Make signal-write failure non-invasive**

If the metrics store fails, preserve the original business exception/outcome.
Log a stable code without identifiers or exception text. Unit tests must prove
the original retry/dead-letter decision is unchanged.

- [ ] **Step 6: Run the focused gate**

```powershell
python -m pytest -q tests/test_runtime_outbox_dispatcher.py tests/test_report_worker.py tests/test_workflow_thread_lock.py
```

## Task 6: Correct PostgreSQL Canary Snapshot Semantics

**Files:**

- Modify: `app/services/langgraph_canary_status.py`
- Create: `tests/test_langgraph_canary_status_postgres.py`
- Modify: `tests/test_dual_langgraph_canary_postgres.py`

- [ ] **Step 1: Write PostgreSQL rows that expose the old bug**

Insert one row in each Outbox state:

```text
pending
retrying
running
published
dead_letter
```

Assert unfinished counts include only the first three. There must be no
`processing` literal in the Outbox snapshot SQL. Cover both current call sites
with separate assertions:

1. the `outbox_pending_count`/unfinished-row count query;
2. the `oldest_outbox_age_seconds`/oldest-unfinished-age query.

The test must fail if either query is left on `('pending', 'processing')`, even
when the other query is correct.

- [ ] **Step 2: Use the explicit phase boundary in every windowed query**

Parse `--since-utc` once, bind it as a PostgreSQL timestamp parameter, and use
the same value for assignment, terminal, error, and signal-bucket queries. Do
not interpolate timestamps into SQL strings.

- [ ] **Step 3: Correct conflict sources**

Compute:

- `command_conflict_count` from command rows completed since the phase start;
- `projection_divergence_count` from the signal bucket;
- `report_commit_conflict_count` from the canonical Review signal/run source;
- effect conflict/busy and ownership-loss counts from the signal bucket.

Do not sum the same incident from both a mutable control row and its signal
bucket.

- [ ] **Step 4: Preserve read-only LangGraph checkpoint observation**

Keep `checkpoint_row_count` as an informational gauge by checking
`to_regclass('checkpoints')` and, when present, reading `COUNT(*)` from the
PostgresSaver-owned `checkpoints` table. The canary may observe this internal
table, but it must not write to it, delete from it, infer business ownership
from its rows, depend on undocumented content columns, or treat its existence
as authorization to manage checkpoint retention. `review_effect_row_count`
remains a separate application-owned informational gauge.

- [ ] **Step 5: Add lease grace to gauges**

Use a bound interval derived from validated integer seconds:

```sql
lease_expires_at <= NOW() - (%s * INTERVAL '1 second')
```

Apply the same rule to Generation Attempts, Report Jobs, running Outbox rows,
and Review effect claims.

- [ ] **Step 6: Add query-plan assertions**

Use catalog assertions and representative tables to prove:

- Outbox unfinished/lease queries use the recovery indexes;
- Generation and Report expired-running queries use appropriate indexes;
- signal-bucket window reads use their primary/time index;
- no query requires scanning provider payload or message columns.

Do not commit raw plans containing infrastructure identifiers.

- [ ] **Step 7: Run the PostgreSQL gate**

```powershell
python -m pytest -q tests/test_langgraph_canary_status_postgres.py tests/test_dual_langgraph_canary_postgres.py -m langgraph_dual_canary
```

## Task 7: Harden the Canary CLI and Evidence Artifacts

**Files:**

- Modify: `scripts/langgraph_canary.py`
- Modify: `tests/test_langgraph_canary_cli.py`
- Modify: `scripts/audit_agent_runtime.py`

- [ ] **Step 1: Add failing CLI parsing tests**

Require:

```text
--phase
--since-utc
--minimum-interview-sample
--minimum-review-sample
--max-oldest-outbox-age-seconds
--max-review-failure-rate
--max-workflow-thread-busy-count
--max-review-effect-busy-count
--lease-expiry-grace-seconds
```

Reject malformed/non-UTC timestamps, invalid phase/rollout pairs, negative
thresholds, and a production evaluation without an explicit phase start.

- [ ] **Step 2: Preserve the read-only exit contract**

Keep:

```text
ELIGIBLE_TO_CONTINUE -> 0
HOLD                 -> 2
ROLL_BACK            -> 3
```

Add a test that monkeypatches all configuration mutation facilities and proves
none are called.

- [ ] **Step 3: Write complete allowlisted evidence**

`result.json` contains the full v2 snapshot. `result.md` contains rollout pair,
phase, observed-since UTC, generated-at UTC, duration, workflow-specific
samples, all safe counts, recommendation, and sorted reason codes.

Artifacts must be written to a phase-specific empty directory or require an
explicit overwrite flag. One phase must never silently overwrite another.

- [ ] **Step 4: Extend the privacy adversarial test**

Reject nested or aliased forms of IDs, tokens, hashes, checkpoint metadata,
provider content, candidate content, DSNs, and credentials. Safe words in field
names are not sufficient; audit values as well.

- [ ] **Step 5: Run the CLI/privacy gate**

```powershell
python -m pytest -q tests/test_langgraph_canary_cli.py tests/test_agent_runtime_audit.py
```

## Task 8: Extend Preflight and Maintenance

**Files:**

- Modify: `scripts/runtime_preflight.py`
- Modify: `app/services/durable_workflow_maintenance.py`
- Modify: `app/services/runtime.py`
- Modify: `tests/test_runtime_preflight.py`
- Modify: `tests/test_durable_workflow_maintenance.py`

- [ ] **Step 1: Add failing preflight checks**

Preflight must verify:

- signal-bucket table and exact privacy-safe columns;
- bounded signal retention configuration;
- required time-window index;
- canary schema version v2;
- real Outbox status vocabulary;
- both graph registrations;
- Stage 46 lock/fencing/effect prerequisites;
- both rollout percentages remain zero for repository preflight.

- [ ] **Step 2: Add signal cleanup to the maintenance result**

Extend `MaintenanceResult` with `deleted_runtime_signal_buckets`. Preserve the
existing command-payload and generation-chunk cleanup behavior.

- [ ] **Step 3: Make cleanup safe under concurrent maintenance**

Keep the process-local run lock and use a bounded SQL deletion. Concurrent
application instances may race harmlessly; deletion count may be split, but no
recent bucket or business row may be deleted.

- [ ] **Step 4: Run focused operational tests**

```powershell
python -m pytest -q tests/test_runtime_preflight.py tests/test_durable_workflow_maintenance.py tests/test_runtime_lifecycle.py
```

## Task 9: Build the Stage 47 Synthetic Fault Matrix

**Files:**

- Create: `tests/test_langgraph_stage47_canary_postgres.py`
- Create: `scripts/langgraph_stage47_acceptance.py`
- Modify: `pytest.ini`

- [ ] **Step 1: Add a dedicated marker**

```ini
langgraph_fencing_canary: PostgreSQL Stage 47 fencing-canary gate tests
```

- [ ] **Step 2: Use barriers for real overlap**

The matrix must cover:

1. two Interview resumes contending for one thread;
2. Generation lease loss during a silent provider;
3. a stale Generation chunk/complete write;
4. Review thread lock contention after Job claim;
5. Report Job lease loss before final commit;
6. effect busy under a live claim;
7. expired effect reclaim and stale completion;
8. effect identity conflict;
9. projection digest divergence;
10. report final-commit conflict rollback;
11. Outbox pending/retrying/running backlog visibility;
12. successful retry clearing mutable `last_error_code` while the aggregate
    signal remains visible for the phase.

- [ ] **Step 3: Assert business outcomes and canary decisions together**

For each scenario assert:

- exact provider call count where deterministic;
- exact business projection/artifact count;
- stale owner cannot mutate;
- exact signal-bucket increment count;
- expected `HOLD` or `ROLL_BACK` result;
- no raw identifier or content in output.

- [ ] **Step 4: Prove healthy behavior is eligible**

Run deterministic `1/0`, `0/1`, and `1/1` synthetic phases with sufficient
workflow-specific samples and no injected faults. Each must be
`ELIGIBLE_TO_CONTINUE`, then return to an isolated `0/0` drain snapshot.

- [ ] **Step 5: Run the matrix**

```powershell
python -m pytest -q tests/test_langgraph_stage47_canary_postgres.py -m langgraph_fencing_canary
```

## Task 10: Update Operator Documentation

**Files:**

- Modify: `docs/local-v1-runbook.md`
- Modify: `README.md`
- Modify: `docs/langgraph-dual-workflow-canary-acceptance.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Document the v2 CLI without embedding environment values**

Show placeholders:

```powershell
python -m scripts.langgraph_canary evaluate `
  --phase interview `
  --since-utc <PHASE_START_UTC> `
  --minimum-interview-sample <APPROVED_SAMPLE> `
  --minimum-review-sample 0 `
  --output-dir <SANITIZED_PHASE_DIRECTORY>
```

Do not provide a command that changes rollout.

- [ ] **Step 2: Document the fixed operator sequence**

```text
baseline       0/0
interview      1/0
interview drain 0/0
review         0/1
review drain    0/0
joint          1/1
final drain    0/0
```

Require explicit approval before each positive phase and explicit
hold/rollback/continue recording after evaluation.

- [ ] **Step 3: Document rollback and drain**

Rollback changes new assignment only. It must not:

- disable the Interview or Review runtime;
- stop consumers or Report Workers;
- unregister either graph version;
- delete checkpoints, commands, attempts, chunks, effects, runs, or artifacts;
- rewrite existing engine/version ownership;
- reroute Durable work to Legacy.

Drain completes only when all work assigned before rollback reaches a safe
interrupt or terminal state and there are no expired active leases/effect
claims.

- [ ] **Step 4: Document human investigation for HOLD signals**

For each ownership signal, give a safe checklist based on aggregate state and
deployment health. Do not tell operators to log tokens or raw workflow IDs into
acceptance artifacts.

- [ ] **Step 5: Run documentation contract tests**

```powershell
python -m pytest -q tests/test_local_v1_docs.py tests/test_langgraph_stage47_release_contract.py
```

## Task 11: Run Final Repository Gates

**Files:**

- Modify: `docs/langgraph-stage47-fencing-canary-acceptance.md`

- [ ] **Step 1: Run focused unit and compatibility gates**

```powershell
python -m pytest -q tests/test_runtime_work.py tests/test_langgraph_canary_status.py tests/test_langgraph_canary_cli.py tests/test_runtime_signal_metrics.py tests/test_runtime_outbox_dispatcher.py tests/test_report_worker.py tests/test_runtime_preflight.py tests/test_durable_workflow_maintenance.py tests/test_local_v1_docs.py
```

- [ ] **Step 2: Run PostgreSQL baseline and Stage 46 recovery gates**

```powershell
python -m pytest -q -m pg_runtime
python -m pytest -q -m pg_control
python -m pytest -q -m langgraph_recovery
python -m pytest -q -m langgraph_review_recovery
```

- [ ] **Step 3: Run Stage 47 PostgreSQL gates**

```powershell
python -m pytest -q tests/test_runtime_signal_metrics_postgres.py tests/test_langgraph_canary_status_postgres.py tests/test_langgraph_stage47_canary_postgres.py
```

- [ ] **Step 4: Run runtime preflight against real isolated PostgreSQL**

Keep rollout at `0/0`. Record only named checks, PostgreSQL major version, safe
counts, and durations.

```powershell
python -m scripts.runtime_preflight --profile core
```

- [ ] **Step 5: Run full regression**

```powershell
python -m pytest -q
npm run build:prototype-css
npm run test:browser
```

Use the repository's established explicit hidden Uvicorn/reuse-existing-server
procedure if Playwright's self-owned server cleanup hangs. Clean only a verified
Stage 47 temporary directory inside the workspace.

- [ ] **Step 6: Run mechanical and privacy checks**

```powershell
python -m compileall -q app scripts tests
git diff --check
git status --short
```

Verify:

- no rollout default changed;
- no staged or committed production configuration was created;
- no test artifact contains prohibited data;
- unrelated user-owned files remain untouched;
- no temporary Stage 47 server or trace process remains.

- [ ] **Step 7: Set the repository decision**

Only after every repository gate passes, update:

```text
Status: READY_FOR_OPERATOR_FENCING_CANARY
```

Keep the operator observation record at `NOT_RUN`.

## Task 12: Operator-Authorized Fencing Canary Observation

**Files:**

- Modify only after actual observation:
  `docs/langgraph-stage47-fencing-canary-observation.md`

This task requires explicit authority to identify and mutate a deployed
environment. If that authority is absent, stop with repository status
`READY_FOR_OPERATOR_FENCING_CANARY`.

- [ ] **Step 1: Record change-control inputs**

Record only:

- environment category/name approved for evidence;
- deployment revision;
- operator/change reference;
- PostgreSQL major version;
- approved minimum duration and workflow-specific sample sizes;
- approved operational thresholds;
- UTC baseline start.

Do not record infrastructure secrets or raw business identifiers.

- [ ] **Step 2: Run `0/0` baseline**

Run core preflight and a v2 baseline snapshot. The baseline must have privacy
PASS, no correctness rollback signals, no expired active ownership after grace,
and an acceptable unfinished Outbox age.

- [ ] **Step 3: Run `1/0` Interview phase**

Change only the deployed Interview new-assignment percentage through the
approved deployment mechanism. Observe until both minimum duration and minimum
Interview sample are met. Evaluate using the exact phase-start UTC.

On `ROLL_BACK`, return new Interview assignment to zero immediately. On `HOLD`,
do not promote or begin another positive phase. On eligibility, still require
an explicit operator continue decision.

- [ ] **Step 4: Return to `0/0` and prove Interview drain**

New work returns to Legacy assignment, while existing Durable Interview work
continues. Prove it reaches safe interrupts/terminal states without changing
its stored engine/version and without expired ownership.

- [ ] **Step 5: Run `0/1` Review phase**

Repeat the same procedure for Review new assignment. Require a Review-specific
sample and prove Report Worker lease, effect reuse, final commit, retry timer,
and rollback drain behavior through aggregate evidence.

- [ ] **Step 6: Return to `0/0` and prove Review drain**

Already assigned Durable Review Jobs must finish under
`langgraph-review-v1`. Do not requeue them as Legacy.

- [ ] **Step 7: Run `1/1` joint phase**

Require independent Interview and Review minimum samples. Verify the durable
handoff produces one Report Job, one Review Run, and one final report per
logical flow without exposing identifiers in the observation record.

- [ ] **Step 8: Return to final `0/0` and drain**

The initial Stage 47 canary always ends at zero, even if every phase is
eligible. Expansion above 1% requires a new plan and approval.

- [ ] **Step 9: Record the scoped result**

Use exactly one terminal observation status:

```text
PASS_FENCING_CANARY
ROLLED_BACK
```

Use `HOLD` only while the observation remains open. A PASS is scoped to the
named environment, revision, thresholds, duration, and samples. It is not a
blanket authorization for higher rollout.

---

## Stage 47 Completion Definition

Repository work is complete when:

1. Stage 46 remains `READY_FOR_FENCING_CANARY` and all its recovery gates stay
   green;
2. canary output uses `langgraph-canary-v2` with an explicit phase and UTC
   boundary;
3. Outbox backlog uses `pending/retrying/running` and never `processing`;
4. command conflicts and projection divergence are distinct signals;
5. `ProjectionConflict` is stable, non-retryable, and forces rollback;
6. transient ownership signals survive later successful retries as privacy-safe
   aggregate buckets;
7. no signal bucket contains identity, content, token, digest, or free-form
   error data;
8. correctness signals roll back, ownership anomalies hold, and healthy phases
   can become eligible only with workflow-specific samples;
9. expired-ownership gauges use a documented grace interval;
10. CLI artifacts are phase-specific, allowlisted, read-only, and complete;
11. PostgreSQL query-plan, recovery, full Python, CSS, browser, preflight,
    privacy, compile, and diff gates pass;
12. committed rollout defaults remain zero;
13. repository acceptance is `READY_FOR_OPERATOR_FENCING_CANARY`;
14. operator observation remains `NOT_RUN` unless a deployed environment was
    explicitly authorized and actually observed.

An operator canary is separately complete only when all seven phases were
recorded, every positive phase met both time and sample requirements, every
hold point received an explicit decision, final assignment returned to `0/0`,
pre-existing Durable ownership drained safely, and the scoped observation is
`PASS_FENCING_CANARY` or `ROLLED_BACK`.

## Explicit Post-Stage-47 Backlog

- **Stage 48 — PostgreSQL connection ownership and capacity:** Separate
  Checkpointer, business SQL, and advisory-lock connection domains; introduce
  bounded pools; make Checkpointer startup thread-safe; move saver setup to
  migration/preflight; measure pool wait and connection budgets; validate the
  runtime table-prefix length against every derived PostgreSQL identifier so
  63-byte truncation cannot create table/index name collisions.
- **Stage 49 — Checkpoint lifecycle and privacy governance:** Add completed
  thread retention, active-thread safety, dry-run cleanup, backup/legal policy,
  capacity/age monitoring, and projection-versus-checkpoint metadata clarity.
- **Stage 50 — Interview `langgraph-v2`:** Move messages and plan data to
  immutable reference/digest form, add fallback provenance, and enforce
  question-count/recursion boundaries without mutating v1 checkpoints.
- **Stage 51 — Review `langgraph-review-v2`:** Add question-level attempts,
  partial progress, and independent retry semantics.
- **Legacy retirement:** Remains blocked on production ownership evidence,
  historical read compatibility, and separate approval.
- **Higher rollout:** 5%, 25%, 50%, and 100% require a new capacity-aware plan;
  Stage 47 authorizes no automatic expansion beyond its initial 1% matrix.
