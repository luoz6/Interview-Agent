# Stage 47.1 LangGraph Heartbeat Hardening Acceptance

Status: READY_FOR_OPERATOR_FENCING_CANARY

## Prerequisites and authority

Stage 46 single-writer/fencing and Stage 47 canary-gate hardening are required
prerequisites. This repository stage does not authorize a deployed rollout;
the production observation remains `NOT_RUN`, and both committed rollout
defaults remain zero.

## Narrow compatibility contract

Stage 47.1 covers `GenerationLeaseHeartbeat`, `ReportLeaseHeartbeat`, and
`ReviewEffectHeartbeat`. A renewal exception must fail closed before the next
guarded persistence operation. PostgreSQL lease and fencing predicates remain
authoritative.

`ReviewEffectLeaseLost` is a semantic subtype of `FencedWriteRejected`. It
distinguishes Effect claim-renewal loss while preserving the existing catch,
failure-code, Report Worker, and `langgraph-canary-v2` behavior. The
`fail_effect` mutation must gain the missing active Report lease, unexpired
Effect claim, matching token/version, and zero-row rejection.

The Durable Interview State schema, Durable Review State schema, Interview
Graph topology, Review Graph topology, interrupt payloads, graph versions,
signal allowlist, and canary artifact schema remain unchanged.

## Privacy and signal ownership

Only the first renewal exception is retained in process memory as an exception
cause. It is never serialized into State, checkpoint data, Outbox payloads,
signal buckets, or canary artifacts. Runtime outer boundaries remain the only
signal writers: the Outbox Dispatcher owns Interview incidents and the Report
Worker owns Review incidents.

## Gate status

- Generation heartbeat exception fail-closed: PASS
- Report heartbeat exception fail-closed: PASS
- Review Effect heartbeat exception fail-closed: PASS
- Review Effect failure-mutation fencing: PASS
- Stable failure classification: PASS
- Single signal ownership and privacy: PASS
- PostgreSQL stale-owner recovery: PASS
- Interview and Review recovery regression: PASS
- Stage 47 canary regression: PASS
- Full Python, CSS, and browser regression: PASS
- Compile, diff, rollout-zero, and resource cleanup: PASS

## Repository evidence

- Repository revision: `6ec932e`
- Stage 47 acceptance runner: four checks PASS
- Acceptance runner status: `READY_FOR_OPERATOR_FENCING_CANARY`
- Acceptance runner operator observation: `NOT_RUN`
- Acceptance runner rollout defaults changed: false
- PostgreSQL combined marker gate: 84 passed
- Heartbeat PostgreSQL recovery: 3 passed
- Full Python: 1165 passed, 1 skipped
- Python warning: one existing Starlette/httpx deprecation
- CSS production build: PASS
- Browser: 37 passed, 9 skipped
- `python -m compileall -q app scripts tests`: PASS
- `git diff --check`: PASS
- Privacy and closed signal allowlist: PASS
- Committed rollout defaults: `0/0`
- Port 8011 after browser cleanup: not listening
- Isolated heartbeat test tables after cleanup: zero
- Stage 47.1 browser artifacts created by this run: cleaned

The browser gate requires Python 3.11. The first local invocation inherited an
older Anaconda Python from the Node/webServer environment and failed before
application startup; rerunning with `STAGE41_PYTHON` set to the repository's
Python 3.11 executable completed the full deterministic browser suite. This
environment correction did not modify committed deployment configuration.

## Deferred boundaries

Connection pools, checkpoint retention, State v2, question-level Review retry,
provider response escrow, higher rollout, and Legacy retirement remain outside
Stage 47.1. Stage 51.1 owns provider idempotency/response escrow; an owner that
has lost its claim may not write an authoritative completed Effect.
