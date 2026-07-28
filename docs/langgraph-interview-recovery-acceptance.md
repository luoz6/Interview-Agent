# LangGraph Interview Recovery Acceptance

Status: PASS

This record tracks the release gates for the versioned interview workflow.
Legacy sessions remain on the legacy engine; only sessions assigned after
rollout are eligible for `langgraph-v1`.

## Required Gates

- PostgreSQL saver setup and restart recovery.
- Duplicate command and retry-event idempotency.
- Public state version monotonicity after projection replay.
- Attempt-scoped chunk replay with reset before replacement output.
- Browser refresh during an active generation.
- Browser reconnect with `Last-Event-ID`.
- Runtime diagnostics privacy audit.
- Full Python, static JavaScript, and Playwright suites.

## Operational Rules

- Keep rollout at zero until every gate passes.
- Roll back assignment for new sessions only; keep v1 graph definitions and
  workers available for existing v1 threads.
- Retain completed generation chunks for 24 hours, then delete them.
- Never place answer text, provider payloads, leases, checkpoint IDs, or raw
  evidence content in diagnostics.
- A replacement attempt emits `generation_reset`; clients clear abandoned
  partial text before rendering new chunks.

## PostgreSQL Recovery Gate

Task 14 status: PASS

- Executed at: 2026-07-23T11:06:02Z
- Implementation base: `136e6bd`
- Recovery checks: 10 passed
- Focused recovery contracts: 12 passed
- Recovery duration: 12.501 seconds
- Acknowledged command RPO: zero
- Replacement attempts observed: 1
- Retry timer checks: 1
- Duplicate committed messages: 0
- Duplicate report jobs: 0
- Privacy allowlist: PASS

The PostgreSQL recovery gate remains valid and was rerun during Task 15.

## Release Decision

Task 14 recovery evidence remains valid. Task 15 must be rerun after the shared
runtime integration of `langgraph-review-v1`, including focused compatibility,
browser recovery, operational privacy, full regression, and deterministic
rollout/rollback checks.

Every Task 15 repository gate has completed. This record is `Status: PASS` and
makes the workflow eligible for an operator canary; it does not authorize or
perform an operator rollout.

The Interview v1 checkpoint intentionally retains bounded conversation
`messages` required to resume follow-up generation. Job-description, resume,
evidence, provider, credential, lease, and internal operational content remains
outside the checkpoint, and no message text may enter diagnostics or acceptance
artifacts. Reference-only Interview messages require a future graph version.

## Task 15 Final Repository Gate

- Executed at: 2026-07-25T06:14:00Z
- Implementation base: `119d078`
- Focused Interview contracts: 76 passed.
- PostgreSQL Interview recovery: 10 passed.
- Recovery acceptance checks: 10 passed in 16.642 seconds.
- Shared Durable Review regression: 98 passed.
- Cross-workflow, privacy, maintenance, and preflight contracts: 49 passed.
- Combined focused acceptance: 134 passed, 0 skipped.
- Full Python regression: 1055 passed, 1 skipped.
- Full Playwright regression: 37 passed, 9 skipped.
- Static JavaScript checks: PASS.
- Tailwind CSS build: PASS.
- Runtime preflight `0/0`: PASS.
- Runtime preflight `1/0`: PASS.
- Runtime preflight `0/1`: PASS.
- Runtime preflight `1/1`: PASS.
- Assignment rollback to `0/0`: PASS.
- Privacy allowlist: PASS.
- Acknowledged command RPO: zero.
- Duplicate Candidate/Interviewer messages: zero.
- Duplicate logical Report Jobs/reports: zero.

Both committed rollout defaults remain zero. Existing `langgraph-v1` threads
remain resumable when new Interview assignment is disabled.
