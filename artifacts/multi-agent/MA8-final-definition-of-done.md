# Multi-Agent Scheduler - Final Definition of Done

## Decision

```text
FINAL_DOD = PASS
CHECKS_PASSED = 32 / 32
NEXT_TASK = NOT_STARTED
```

This Gate evaluates the final checklist after MA8-T10. It does not authorize
deleting the historical OLD-execution drain runtime without the zero-active-
execution evidence required by the V2.1 drain policy.

## Canonical Path

| Check | Evidence | Result |
| --- | --- | --- |
| production orchestration path = 1 | New starts require `SchedulerProductionEntry`; temporary selector and OLD fallback are absent | PASS |

Existing OLD-bound executions remain immutable and drain through their saved
runtime version. That compatibility obligation cannot be selected for a new
execution and is not a second production path.

## Runtime Truth

| Check | Evidence | Result |
| --- | --- | --- |
| ExecutionPlan = definition | Frozen plan contract excludes runtime status, attempts, and artifacts | PASS |
| ExecutionState = single runtime truth | One canonical aggregate and repository revision/CAS boundary | PASS |
| LangGraph does not maintain duplicate task truth | Scheduler graph checkpoint contains only serialized `ExecutionState` and delegates decisions/transitions | PASS |

## User Command Safety

| Check | Evidence | Result |
| --- | --- | --- |
| WaitHandle exists | Typed immutable wait identity contract | PASS |
| UserCommand exists | Typed command with wait/question/revision identity | PASS |
| revision fencing exists | Wait-issued and canonical-state revision checks | PASS |
| duplicate/stale/late answers handled | Full duplicate, payload-conflict, stale, late, wrong-wait, wrong-question, and wrong-revision matrix | PASS |

## Invocation Reliability

| Check | Evidence | Result |
| --- | --- | --- |
| stable logical invocation identity | `(execution_id, task_id, logical_attempt)` | PASS |
| durable ledger | Neutral Port plus memory and PostgreSQL adapters | PASS |
| lease | Acquire, renew, expiry, and reclaim contracts | PASS |
| fencing | Monotonic fencing version and stale-owner rejection | PASS |
| crash recovery | PREPARED, pre-provider, post-provider/pre-receipt, and post-receipt windows | PASS |
| single committed logical effect | Same-artifact replay accepted; conflicting artifact rejected | PASS |

The guarantee is deliberately one committed logical effect, not external
provider exactly-once execution.

## Capability Safety

| Check | Evidence | Result |
| --- | --- | --- |
| typed requests | Closed request models at the core boundary | PASS |
| typed outputs | Neutral `DomainArtifact` outputs | PASS |
| schema/version compatibility | Capability and output compatibility validators | PASS |
| deterministic request assembly | Plan task plus canonical state deterministically creates the request | PASS |
| LLM cannot construct arbitrary Agent request | Structured decision schema has no arbitrary request/payload channel | PASS |

## Scheduler

| Check | Evidence | Result |
| --- | --- | --- |
| deterministic parity | Frozen characterization and replay/SSE/report parity | PASS |
| adaptive scheduling | Evidence-sensitive ADD_TASK/DISPATCH E2E | PASS |
| bounded replanning | Step, call, replan, retry, follow-up, question, and timeout limits | PASS |
| WAIT_USER | Durable interrupt, restart, and fenced-resume integration | PASS |
| recovery | Ledger replay, lease reclaim, restart, and stale-worker suppression | PASS |

## Memory

| Check | Evidence | Result |
| --- | --- | --- |
| five logical private namespaces | scheduler, knowledge, examiner, reviewer, report-coach | PASS |
| existing physical infrastructure reused | One `InMemoryAgentMemoryStore` with scoped views | PASS |
| principal/session ownership | Complete immutable `AgentMemoryScope` | PASS |
| session deletion | All target-session namespaces deleted and tombstoned; other sessions retained | PASS |
| no cross-agent private reads | Foreign scope fails closed with `AgentMemoryAccessDenied` | PASS |

No professional Agent persists private records without a proven business use
case, and ExecutionState/Artifact/Event payload duplication is prohibited.

## Architecture

| Check | Evidence | Result |
| --- | --- | --- |
| P0-P9 dependency rules preserved | Five final zero-tolerance boundaries; no parse or unresolved-relative-import errors | PASS |
| no agents/ as services 2.0 | `app/agents` contains only the four professional Agents and context-compression capability; no Scheduler/orchestration owner | PASS |
| no new duplicated orchestration framework | MA8-T09 eight-category Gate reports zero violations | PASS |

## Verification

The final focused matrix maps directly to every checklist section:

```text
final DoD focused contracts and architecture gates: 172 passed
full architecture suite: 129 passed
full acceptance suite: 225 passed
MA8 redundancy categories: 8, violations: 0
new dependency cycles: 0
Python compileall: PASS
git diff --check: PASS (existing line-ending warnings only)
```

The real external PostgreSQL scope remains approval-gated. No approval metadata
was fabricated and no external database was touched. PostgreSQL durability is
covered here by schema, SQL-shape, CAS/fencing, adapter, and lost-race
contracts; live-target execution remains subject to its separate authorization
boundary.

## Final Acceptance

All 32 explicit Definition-of-Done checks have executable or structural
evidence, the production path is singular, and the known historical drain is
bounded by immutable execution ownership.

```text
MULTI_AGENT_PLAN_V2_FINAL_GATE = PASS
NEXT_TASK = NOT_STARTED
```
