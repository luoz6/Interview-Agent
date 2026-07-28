# Stage 47.2 Agent Runtime Telemetry Hardening Acceptance

Status: `READY_FOR_AGENT_TELEMETRY_CANARY`

Date: 2026-07-27

Production observation: `NOT_RUN`

## Post-review privacy hardening

The write-time metadata policy no longer treats a safe-looking key as proof
that its string value is safe. String metadata is accepted only for declared
machine fields such as `report_path`, `retrieval_path`, `knowledge_status` and
question-ID lists. Generic fields such as `value`, `debug` and `summary` cannot
carry free text, a DSN, bearer/JWT material or provider credentials into either
the file recorder or PostgreSQL ledger. Classifier callbacks are also runtime
validated: an invalid return value falls back to a completed telemetry outcome
without replacing a successful business result.

Committed rollout defaults: Interview `0%`, Review `0%`

## Scope

Stage 47.2 hardens `agent-runtime-v1` context snapshots, safe metadata,
telemetry-helper isolation, recorder failure observation, process-wide Runner
composition, Knowledge AgentRun ownership, and operation-level PostgreSQL
aggregates. It does not change Agent business output, either Durable State
schema, either LangGraph topology, provider retry/fallback policy, or
production rollout.

## Fixed Runtime Results

- `run()` captures a deep Context snapshot before provider execution.
- `stream()` captures Context at call time while latency starts at first
  iterator advancement.
- Metadata/classifier failures cannot replace a successful provider result.
- Every recorder receives the same sanitized bounded machine metadata.
- Top-level recorder failures remain best effort and emit a safe stable code.
- One runtime table prefix contributes one PostgreSQL AgentRun recorder.
- `prepare_interview()` remains the single production owner of the Knowledge
  AgentRun; retrieval traces remain separately correlated.
- Agent operation aggregates expose counts, rates, and latency percentiles
  without identity fields or `safe_metadata`.

## Gate Record

| Gate | Result |
| --- | --- |
| Baseline Agent Runtime | `PASS: 36 passed, 3 skipped` |
| Focused hardening and release contracts | `PASS: 57 passed` |
| Agent/Knowledge/Review/Report integration | `PASS: 110 passed, 5 skipped` |
| PostgreSQL Agent Ledger and aggregates | `PASS: 8 passed` |
| Full Python regression | `PASS: 1192 passed, 1 skipped, 1 existing warning` |
| LangGraph PostgreSQL recovery/fencing | `PASS: 24 passed, 1169 deselected` |
| CSS build | `PASS` |
| Playwright browser | `PASS: 38 passed, 8 opt-in/configuration skips, 0 failed; outer command timed out during WebServer teardown after all 46 results` |
| Compile/diff/privacy/cleanup | `PASS` |
| Acceptance runner | `PASS: READY_FOR_AGENT_TELEMETRY_CANARY` |

## Readiness Rule

All mandatory repository gates passed with PostgreSQL tests actually
executed. The browser harness enumerated all 46 configured tests as 38 passed
and 8 skipped with no failed test; its outer command timed out while waiting
for the already-complete WebServer teardown, and port/process cleanup was
verified separately. Repository readiness does not change production
observation from `NOT_RUN`.
