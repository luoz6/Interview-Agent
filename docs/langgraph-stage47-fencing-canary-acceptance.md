# Stage 47 LangGraph Fencing Canary Gate Acceptance

Status: READY_FOR_OPERATOR_FENCING_CANARY

## Prerequisite

Stage 46 must remain `READY_FOR_FENCING_CANARY`, with its PostgreSQL recovery,
single-writer, lease-fencing, replay-safe-effect, cold-start, privacy, Python,
and browser gates green.

Both committed rollout defaults remain zero. Repository completion may move
this record to `READY_FOR_OPERATOR_FENCING_CANARY`; it does not authorize a
production rollout or claim that deployed traffic was observed.

## Repository Gates

- `langgraph-canary-v2` phase/time contract: PASS
- Outbox `pending/retrying/running` snapshot semantics: PASS
- Command conflict versus projection divergence taxonomy: PASS
- Privacy-safe runtime signal buckets: PASS
- Ownership/fencing HOLD gates: PASS
- Correctness/privacy ROLL_BACK gates: PASS
- Workflow-specific sample gates: PASS
- PostgreSQL signal/snapshot/query-plan gates: PASS
- Stage 46 recovery regression: PASS
- Runtime preflight and privacy audit: PASS
- Full Python, CSS, and browser regression: PASS
- Diff, compile, rollout-zero, and workspace checks: PASS

## Repository Evidence

- PostgreSQL major version: 16
- Schema category: isolated test prefixes
- Runtime preflight at `0/0`: PASS
- Workflow/observability tables checked: 8
- Recovery indexes checked: 6
- `pg_runtime`: 31 passed
- `pg_control`: 23 passed
- `langgraph_recovery`: 10 passed
- `langgraph_review_recovery`: 6 passed
- `langgraph_dual_canary`: 6 passed
- `langgraph_fencing_canary`: 5 passed
- Signal/snapshot/query-plan PostgreSQL file: 5 passed
- Focused runtime/graph compatibility: 89 passed, 23 skipped
- Full Python with PostgreSQL: 1140 passed, 1 skipped
- CSS production build: PASS
- Browser: 37 passed, 9 skipped
- Privacy audit: PASS
- `python -m compileall -q app scripts tests`: PASS
- `git diff --check`: PASS
- Port 8011 after browser cleanup: not listening
- Stage 47 trace directories after cleanup: zero

The signal-window query and Outbox status-count query used index plans on
representative 2,000-row isolated buckets. Raw plans were not committed.

## Non-Blocking Observation

An initially overlong isolated test prefix exposed PostgreSQL's 63-byte
identifier truncation: a derived Generation index name can collide with a
derived table name. The committed/default `interview` prefix is within the safe
length and all Stage 47 tests use a short isolated prefix. Prefix-length and
derived-identifier collision validation is recorded for Stage 48; Stage 47 did
not rename existing production tables or indexes.

## Fixed Operator Sequence

```text
0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0
```

Rollback is assignment-only. Returning a percentage to zero affects new
assignment, while saver, workers, consumers, graph registrations, and already
assigned Durable ownership remain available until drain completes.

## Privacy Boundary

Canary evidence may contain UTC times, rollout pairs, durations, aggregate
counts/rates, stable reason codes, database major version, and a short
deployment revision. It must not contain workflow identifiers, checkpoint
identifiers, lease tokens, business-content hashes, DSNs, credentials,
candidate content, evidence, feedback, reports, or provider payloads.

Exactly-once external provider invocation is not claimed. Stage 46 guarantees
stable committed logical effects after an artifact exists, not an atomic
transaction spanning an external provider and PostgreSQL.

## Deferred Boundaries

Connection pools, checkpoint retention, Interview/Review State v2, Legacy
retirement, question-level Review retry, and rollout above the initial 1%
matrix remain outside Stage 47.

## Operator Observation

Repository decision: `READY_FOR_OPERATOR_FENCING_CANARY`.

The separate Stage 47 observation record remains `NOT_RUN` until a deployed
environment is explicitly authorized and actually observed. Both committed
rollout defaults remain zero.

## Stage 47.1 follow-up

Stage 47.1 hardens background Generation, Report, and Review Effect heartbeat
renewal exceptions before any real operator fencing canary. The Stage 47
evidence above remains historical; Stage 47.1 must re-run the recovery,
privacy, and canary gates before restoring repository readiness. This note does
not change the production observation or committed rollout defaults.
