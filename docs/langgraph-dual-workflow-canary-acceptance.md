# LangGraph Dual-Workflow Canary Acceptance

Status: PASS

This record separates deterministic repository acceptance and the explicitly
authorized Local V1 synthetic Canary from a production deployment. Neither
the repository gates nor the local observation changed committed rollout
defaults or observed production traffic.

## Supported Engine Matrix

| Interview engine | Review engine | Repository gate |
| --- | --- | --- |
| `legacy` | `legacy` | PASS |
| `langgraph-v1` | `legacy` | PASS |
| `legacy` | `langgraph-review-v1` | PASS |
| `langgraph-v1` | `langgraph-review-v1` | PASS |

Engine assignments are immutable. Requeue and rollout changes must preserve
the engine and graph version stored when the Session or Report Job was created.

## Repository Gates

- Deterministic assignment matrix: PASS.
- PostgreSQL joint handoff and restart recovery: PASS.
- Review cold start before the first checkpoint: PASS.
- Wrong-engine event discard: PASS.
- Browser refresh, reconnect, duplicate delivery, and joint handoff: PASS.
- Shared-saver namespace isolation: PASS.
- Runtime preflight at `0/0`, `1/0`, `0/1`, and `1/1`: PASS.
- Applied-command and completed-chunk maintenance: PASS.
- Privacy and diagnostic allowlists: PASS.
- Full Python and Playwright regressions: PASS.

Repository completion first moved this record to
`READY_FOR_OPERATOR_CANARY`. The later Local V1 synthetic observation moved
the record to `PASS` for the authorized local scope only. Neither step changed
either committed rollout default from zero.

## Repository Evidence

- Executed at: 2026-07-25T06:14:00Z
- Implementation base: `119d078`
- Combined focused acceptance: 134 passed, 0 skipped.
- PostgreSQL Interview recovery: 10 passed.
- Shared Durable Review regression: 98 passed.
- Cross-workflow focused gate: 49 passed.
- Full Python regression: 1055 passed, 1 skipped.
- Full Playwright regression: 37 passed, 9 skipped.
- Repository privacy: PASS.
- Rollout/preflight sequence: `0/0 -> 1/0 -> 0/1 -> 1/1 -> 0/0`, PASS.
- Local V1 synthetic Canary: PASS.
- Production Canary: NOT_RUN.

## Checkpoint Privacy Boundary

The Interview v1 checkpoint intentionally contains bounded conversation
`messages` so follow-up generation can resume without rebuilding context from
a mutable projection. It excludes job descriptions, resumes, evidence content,
provider payloads, credentials, leases, and internal operational metadata.
Message text must never be exported through diagnostics, logs, canary
snapshots, or acceptance artifacts.

Review checkpoints remain reference/hash only and contain no answer, feedback,
evidence, report, or provider text. Moving Interview messages to a
reference-only model requires a new graph version and is post-Stage-45 work.

## Operator Canary Sequence

The only approved initial sequence is:

```text
0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0
```

The first value is the percentage of newly created Interviews assigned to
`langgraph-v1`; the second is the percentage of newly created Report Jobs
assigned to `langgraph-review-v1`. Returning to zero changes new assignment
only. Both graph versions, the saver, and their consumers remain available for
already assigned work.

## Canary Stop Gates

Correctness or privacy failures require an assignment-only rollback:

- acknowledged command loss;
- duplicate Candidate or Interviewer projection;
- duplicate Report Job or final report;
- public state-version regression;
- stale retry cursor advancement;
- conflicting final report digest acceptance;
- unknown graph-version fallback;
- prohibited content in diagnostics or acceptance evidence.

Backlog, latency, retry, fallback, repair, or terminal-failure thresholds block
promotion and require an explicit hold-or-rollback decision. The canary status
tool is read-only and never changes deployment configuration.

## Operator Observation

Status: PASS_LOCAL_SYNTHETIC

- Environment: Local V1.
- Completed at: 2026-07-25T06:56:34Z.
- Implementation base: `45a672a`.
- Fixed phase sequence:
  `0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0`.
- Phase probes: 7 passed, 0 failed.
- Exact-ownership rollback/drain sample: 1 Interview and 1 Review Job.
- Stable business outcome: 1 Report Job, 1 Review Run, 1 final report; no
  duplicate Candidate or Interviewer projection.
- Post-rollback preflight: PASS at `0/0`.
- Post-rollback privacy snapshot: PASS; no pending outbox work, stale durable
  ownership, projection conflict, or report commit conflict.
- Production Canary: NOT_RUN.

The observation used isolated PostgreSQL test tables, deterministic synthetic
inputs, and fake provider dependencies. It resumed the exact durable ownership
created before rollback through newly constructed runtime objects after both
assignment percentages returned to zero. It did not call a model, use real
candidate traffic, modify committed defaults, or retain synthetic business
rows after verification.

No production Canary is authorized or claimed by this record. A production
operator must create a separate observation record and may move that record
through `CANARY_IN_PROGRESS` to `PASS` or `ROLLED_BACK` only after recording
sanitized UTC times, aggregate counts/rates, rollout pairs, stable reason
codes, and a deployment revision.

## Operator Runbook Contract

Before this record can move to `CANARY_IN_PROGRESS`, an operator must verify
repository acceptance, identify the target environment, supply infrastructure
configuration through secret management, and approve the fixed sequential
matrix. The read-only Canary CLI never applies rollout or rollback.

Rollback changes new assignment only. Both runtime-enabled settings, graph
registrations, consumers, and the shared saver remain available until all
assigned durable ownership has drained. No active durable control or content
rows are deleted during rollback.

## Deferred Versioned Work

- Reference-only or application-encrypted Interview message state requires
  `langgraph-v2` and new recovery acceptance.
- Cooperative SSE shutdown requires a separate transport-lifecycle change;
  Stage 45 proves durable reconnect after forced disconnect.
- Legacy retirement requires a separate approval and zero active ownership.
