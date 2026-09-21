# MA6-T04 — Agent Memory Namespace + Ownership

## Implementation

Added the frozen `app.domain.memory.agent.AgentMemoryScope` ownership value
object. Every Agent-private memory operation now receives a complete scope
with all five required dimensions:

```text
deployment_id
principal_id
session_id
agent_id
memory_type
```

The model rejects missing or extra dimensions and cannot be mutated after
construction. `AgentMemoryPort.recall`, `remember`, and `delete_scope` use this
scope type, preventing an adapter from treating `agent_id` alone as a memory
namespace.

Logical memory type values are intentionally not restricted here; MA6-T05
defines the five allowed logical namespaces.

## Verification

```text
AgentMemoryPort / ownership contract tests: 4 passed
Python compileall (app/domain/memory, app/ports): PASS
git diff --check: PASS (only existing LF/CRLF conversion warnings)
```

## Acceptance

```text
deployment_id bound: PASS
principal_id bound: PASS
session_id bound: PASS
agent_id bound: PASS
memory_type bound: PASS
agent-only namespace rejected by contract: PASS
immutable and extra-field-forbidden scope: PASS
```

```text
MA6-T04 = PASS
NEXT_TASK = NOT_STARTED
```

