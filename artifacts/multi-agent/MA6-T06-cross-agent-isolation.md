# MA6-T06 — Cross-agent Isolation

## Implementation

Added one `InMemoryAgentMemoryStore` backing store for all logical namespaces.
The store issues `ScopedAgentMemory` port views bound to one complete
`AgentMemoryScope`.

Every `recall`, `remember`, and `delete_scope` call compares the requested
scope with the port's bound owner scope. A mismatch in any ownership dimension
fails closed with `AgentMemoryAccessDenied`:

```text
deployment_id
principal_id
session_id
agent_id
memory_type
```

Consequently, a Reviewer port cannot directly recall Examiner-private memory,
even though both logical namespaces share the same physical backing store.
Composition must inject only the scoped port view into an Agent, not the store
factory.

Cross-agent exchange continues through the neutral invocation contract, whose
typed result is `DomainArtifact`. Private-memory access is not a collaboration
channel.

## Verification

```text
Agent memory port and isolation contracts: 13 passed
typed invocation and neutral artifact regressions: 9 passed
Python compileall (domain memory, ports, memory adapters): PASS
isolation imports: PASS
git diff --check: PASS (only existing LF/CRLF conversion warnings)
```

## Acceptance

```text
Reviewer cannot recall Examiner private memory: PASS
all ownership dimensions enforced: PASS
foreign access fails closed: PASS
single physical backing store: PASS
cross-agent result boundary is DomainArtifact: PASS
```

```text
MA6-T06 = PASS
NEXT_TASK = NOT_STARTED
```

