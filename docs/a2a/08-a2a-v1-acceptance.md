# A2A-V1 Acceptance Review

## Status

- Phase: `T14 — Real Business E2E / Parity Acceptance`
- Plan version: `v0.2`
- Review date: `2026-09-09`

## Gate Summary

| Gate | Status | Evidence |
|---|---|---|
| Gate -1A Baseline Identity Frozen | `PARTIAL` | `00-runtime-baseline.md` |
| Gate -1B Naming Frozen | `PARTIAL` | `01-naming-and-orchestration-boundary.md` |
| Gate A Semantic Baseline Frozen | `PARTIAL` | `02-semantic-baseline.md`, `tests/a2a_baseline/test_artifact_contract_guard.py` |
| Gate B Contract Frozen | `PASS` | `03-agent-boundary-contract.md`, `app/a2a/contracts/` |
| Gate C Examiner A2A | `PASS_LOCAL` | local A2A server/client + `FollowupArtifactPayload` + `ExaminerAgentBridge` |
| Gate D Interview-side A2A | `PARTIAL` | examiner and knowledge bridges are available under `AGENT_TRANSPORT=a2a`; real provider parity not executed |
| Gate E Review-side A2A | `PASS_LOCAL` | reviewer and report coach run through A2A invoker under `AGENT_TRANSPORT=a2a`; real provider parity not executed |
| Gate F Parity | `PASS_LOCAL` | deterministic comparator and DualPathRunner implemented |
| Gate G A2A-V1 Acceptance | `BLOCKED_EXTERNAL` | real LLM/provider E2E requires external authorization |

## Official A2A SDK

The official `a2a-sdk==1.1.2` is installed and pinned in
`requirements-a2a.txt`, kept separate from the base reproducibility-locked
requirements.
`app/a2a/official.py` converts internal Agent Cards into official A2A 1.0
AgentCard protobufs. Full official server/client transport is not yet wired
into the main FastAPI runtime.

A local A2A card discovery endpoint is available:

```text
GET /api/a2a/agents
GET /api/a2a/agents/{agent_id}/card
POST /api/a2a/agents/{agent_id}/tasks
```

## Local Real E2E Evidence

A real provider preview E2E was executed with `AGENT_TRANSPORT=a2a`:

```text
Prep -> A2A Knowledge -> InterviewPlan
Interview -> A2A Examiner
Review -> legacy microbatch
Report -> A2A Report Coach shadow
```

Report job terminal state:

```text
status: completed
stage: completed
percent: 100
```

## PostgreSQL Durable Evidence

Local PostgreSQL `127.0.0.1:5432/interview` is reachable and initialized:

```text
scripts.init_local_runtime --check -> status ok
```

Protected PostgreSQL tests executed:

```text
tests/contracts/test_owned_postgres_scope_postgres.py -> 3 passed
tests/integration/postgres/test_postgres_runtime_control.py
tests/integration/postgres/test_postgres_runtime_migrations.py -m pg_runtime -> 6 passed
```

## Executed Verification

```text
python -m compileall -q app/a2a
```

```text
pytest tests/unit/test_a2a_runtime.py
pytest tests/unit/test_report_tasks.py
pytest tests/unit/test_report_coverage.py
pytest tests/unit/test_report_tasks_microbatch.py
```

Result:

```text
118 passed
```

## Remaining External Gate

Real business E2E cannot be claimed because:

- Real provider calls require explicit authorization and can incur cost.
- PostgreSQL durable review/report integration is not active in the local
  preview profile used for this run.

The local A2A protocol, artifact, card, invocation, and comparator layers are
implemented and verified.
