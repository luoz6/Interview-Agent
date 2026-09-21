# MA7-T01 — Composition Consolidation

## Implementation

Runtime Composition now owns the complete dependency graph for the future
canonical Scheduler path:

```text
SchedulerApplicationCapability
A2A runtime and professional Agent adapters
AgentRegistry capability adapter
A2AAgentInvoker invocation adapter
Agent invocation ledger
Agent-private memory store
Scheduler execution-state store
LangGraph checkpointer and Scheduler graph
```

`get_a2a_runtime`, `get_scheduler_invocation_ledger`,
`get_scheduler_execution_state_store`, `get_scheduler_checkpointer`, and the
existing memory getter are all backed by the same `RuntimeContainer`.

`compose_scheduler_runtime` creates one plan-scoped
`SchedulerRuntimeComposition` bundle from those container-owned dependencies.
The Scheduler and its LangGraph graph share the same capability adapter,
invocation adapter, ledger, state store, and checkpointer. Conflicting initial
state for an already-composed execution fails closed.

The session deletion worker also reuses the composed Scheduler state, ledger,
A2A server (when initialized), and Agent memory instances rather than creating
parallel deletion dependencies.

## Scope Boundary

This Task consolidates construction only. It does not:

```text
add an OLD/NEW switch
assign an execution to a cutover path
route production interview entry to Scheduler
retire legacy orchestration
```

Those changes remain MA7-T02 and later.

## Verification

```text
composition and Scheduler/A2A/Port contracts: 29 passed
existing composition lifecycle and session deletion regressions: 32 passed
architecture gates: 20 passed
Python compileall: PASS
public imports: PASS
git diff --check: PASS (only existing LF/CRLF conversion warnings)
```

Architecture inventories after refresh:

```text
app_files_scanned = 505
test_files_scanned = 458
parse_error_count = 0
top_level_symbols_scanned = 2903
triple_zero_modules = 3
triple_zero_symbols = 31
```

## Acceptance

```text
Scheduler assembled by Runtime Composition: PASS
A2A assembled by Runtime Composition: PASS
Professional Agent adapters assembled by Runtime Composition: PASS
Capability and invocation adapters share one A2A runtime: PASS
durable ledger selected by runtime backend: PASS
Agent memory uses the container-owned store: PASS
LangGraph runtime/checkpointer assembled centrally: PASS
production routing unchanged: PASS
```

```text
MA7-T01 = PASS
NEXT_TASK = NOT_STARTED
```

