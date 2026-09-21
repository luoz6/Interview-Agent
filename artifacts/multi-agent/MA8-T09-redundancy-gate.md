# MA8-T09 - Redundancy Gate

## Result

The dedicated AST Gate found one canonical responsibility owner and zero
second-owner violations in every required category.

| Required check | Canonical boundary | Candidates | Violations | Result |
| --- | --- | ---: | ---: | --- |
| duplicate scheduler | `SchedulerApplicationCapability` | 1 | 0 | PASS |
| duplicate workflow | Scheduler production entry plus explicit OLD drain boundary | 2 | 0 | PASS |
| duplicate execution state | `ExecutionState` | 1 | 0 | PASS |
| duplicate invocation port | `AgentInvocationPort` | 1 | 0 | PASS |
| duplicate agent registry | `AgentRegistry` | 1 | 0 | PASS |
| duplicate memory subsystem | `InMemoryAgentMemoryStore` physical Agent-private store | 1 | 0 | PASS |
| duplicate retry system | decision validation plus canonical task transition | 2 | 0 | PASS |
| duplicate task lifecycle | `TaskRuntimeState`, `transition_task_state`, and aggregate facade | 3 | 0 | PASS |

The candidate counts describe the expected implementation shape for each
responsibility. Multiple symbols in the retry and lifecycle rows are parts of
one Domain-owned state machine, not independent systems.

## Boundary Decisions

- `scheduler_graph.py` is a thin phase/checkpoint adapter over the Scheduler;
  it does not own policy or task truth.
- `SchedulerProductionEntry` is the only new-execution production entry. The
  retained `InterviewWorkflowService` handles explicit historical OLD drains,
  and `ReviewWorkflowService` owns the distinct report-review workflow.
- The A2A invocation module re-exports or implements the neutral Port; it does
  not define another invocation contract.
- In-memory and PostgreSQL execution/ledger adapters are expected Port
  polymorphism, not additional execution-state or lifecycle owners.
- Principal, question, report, context, and Agent-private memory have distinct
  ownership. The Agent-private memory Gate found one physical store serving
  the five logical namespaces through scoped views.
- Report, provider, ingestion, and historical workflow retry mechanisms serve
  separate concerns. Scheduler retry authority remains in scheduling decision
  validation and the canonical task transition.

## Detector Proof

`scan_multi_agent_redundancy.py` parses the complete application tree without
importing production modules. A synthetic fixture injects a second Scheduler,
command workflow, runtime state, invocation Protocol, Agent registry, physical
Agent memory store, Scheduler retry owner, task state model, and task transition
function. Every required category reports its corresponding violation.

The production Gate also requires every category to have at least one detected
candidate. Missing or unrecognized canonical implementations therefore cannot
pass as an empty zero-result scan.

## Repository Evidence

```text
application Python files scanned = 513
parse errors = 0
dedicated redundancy categories = 8
dedicated redundancy violations = 0

generic exact-AST implementation groups = 28
generic repository/store families = 27
generic runtime-wiring fingerprints = 0
```

The 28 generic exact-AST groups remain reviewed utility/error/DTO candidates;
none is a second owner in the eight orchestration categories. Repository/store
families are retained adapter polymorphism and are not counted as duplicate
subsystems.

## Verification

```text
dedicated Redundancy Gate and synthetic detector proof: 2 passed
architecture full suite: 129 passed
acceptance full suite: 225 passed
architecture + acceptance combined: 354 passed
```

The five warnings are existing Starlette deprecation and Pydantic JSON-schema
warnings. No external PostgreSQL scope was used or required for this Gate.

## Acceptance

```text
MA8-T09 = PASS
NEXT_TASK = NOT_STARTED
```
