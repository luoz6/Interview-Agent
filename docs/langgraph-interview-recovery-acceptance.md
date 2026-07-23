# LangGraph Interview Recovery Acceptance

Status: PENDING_RECOVERY_ACCEPTANCE

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

The overall release status remains pending until the browser, compatibility,
operational, and full regression gates in Task 15 pass.
