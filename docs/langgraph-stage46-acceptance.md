# Stage 46 LangGraph Fencing Acceptance

Status: READY_FOR_FENCING_CANARY

Post-review note: Workflow lock contexts are class based. Frozen domain
exceptions such as `SessionVersionConflict` now cross both the no-op and
PostgreSQL advisory-lock boundary without being replaced by
`FrozenInstanceError`; unlock/connection cleanup remains best effort when a
business exception is already active.

## Safety Boundary

- Interview LangGraph rollout remains zero by default.
- Review LangGraph rollout remains zero by default.
- This record must not contain DSNs, credentials, raw workflow identifiers,
  lease tokens, checkpoint identifiers, candidate answers, report content, or
  provider output.

## PostgreSQL Baseline

- PostgreSQL major version: 16
- Schema category: isolated test prefixes
- `pg_runtime`: 31 passed
- `pg_control`: 17 passed
- `langgraph_recovery`: 10 passed
- `langgraph_review_recovery`: 6 passed
- Baseline decision: PASS
- SSE tuple cursor expected index used: true
- Expired-running Outbox expected partial index used: true
- Representative row-count buckets: 10k-100k

Raw query plans were kept local and are not committed.

## Workflow Thread Lock

- Stable SHA-256/signed-bigint lock vectors: PASS
- Same-thread exclusion with independent PostgreSQL connections: PASS
- Different-thread concurrency: PASS
- Exception and connection-close release: PASS
- Bounded backoff without busy spinning: PASS

## Generation Fencing

- Unique UUID lease token per claim: PASS
- Monotonic fencing version per logical attempt: PASS
- Stale append, heartbeat, fail, abandon, and complete rejected: PASS
- Silent-provider heartbeat independent of chunk flush: PASS

## Review Fencing

- Retry timer schedules Report Job without invoking graph: PASS
- `available_at` and `scheduled_attempt` claim contract: PASS
- Retry resumes with `Command(resume=...)` under fresh lease: PASS
- Job lease acquired before Review thread lock: PASS
- Session, Report, Report Job, and Review Run final commit atomicity: PASS
- Stale final commit rollback: PASS

## Effect Replay

- Leased effect claim before provider invocation: PASS
- Completed question/report effect reuse: PASS
- Busy and immutable-input conflict outcomes: PASS
- Expired-claim reclaim and stale completion fencing: PASS
- Exactly-once external provider invocation is not claimed.

## Cold-Start Recovery

- Canonical Interview `bootstrap_input_sha256`: PASS
- Bootstrap digest write-once conflict handling: PASS
- Review initialization has one graph-owned bootstrap path: PASS
- Interview/Review saver restart and pre-first-checkpoint recovery: PASS

## Privacy

- Runtime preflight allowlist audit: PASS
- Browser agent-runtime correlation/privacy audit: PASS
- Canary additions contain aggregate counts only: PASS

## Full Regression

- Focused unit and compatibility: 79 passed, 1 skipped
- Focused PostgreSQL concurrency/recovery: 70 passed
- Full Python with PostgreSQL: 1098 passed, 1 skipped
- CSS production build: PASS
- Browser: 37 passed, 9 skipped
- `git diff --check`: PASS

## Release Decision

Repository decision: `READY_FOR_FENCING_CANARY`.

Both rollout defaults remain zero. This status does not authorize deployment or
production assignment. Moving either rollout above zero requires separate
operator authorization and deployed canary observation.

Stage 47 owns canary-gate hardening and any separately authorized deployed
fencing observation. That follow-on work does not change the Stage 46
repository decision or authorize rollout by itself.
