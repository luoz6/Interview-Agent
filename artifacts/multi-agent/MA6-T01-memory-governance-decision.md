# MA6-T01 — Agent Private Memory Governance Decision

## Decision

V1 Agent-private memory is governed as:

```text
SESSION_LOCAL_AGENT_MEMORY
```

The scope is the current `deployment_id + principal_id + session_id +
agent_id` ownership tuple. A private memory record is usable only during that
interview/session and must be removed as part of session deletion. It must not
be used to construct a cross-session user profile.

## In-scope guarantees

```text
Each Agent gets an independent logical memory scope: REQUIRED
Memory is session-local: REQUIRED
Session deletion removes the session-local Agent memory: REQUIRED
Agent memory is not copied into ExecutionState or DomainArtifact: REQUIRED
```

The physical implementation may use the existing memory infrastructure. This
decision does not authorize a second memory subsystem; the later MA6 tasks
define the minimal port, namespace, isolation, and context policy.

## Explicit non-goals for V1

The following are intentionally deferred and must not be introduced by MA6-T01:

```text
cross-session Agent memory
long-term user profiling
consent flows for cross-session Agent memory
long-term retention policy
user export of cross-session Agent memory
cross-session revocation/deletion semantics
```

If cross-session memory is ever proposed, it requires a separate governance
decision covering consent, principal ownership, tenant isolation, retention,
export, deletion, and revocation.

## Repository evidence and boundary

`artifacts/multi-agent/MA0-agent-inventory.md` records that no current Agent
has a private logical memory namespace with independent `recall`, `remember`,
or `delete_scope` behavior. Existing PrincipalMemory, question memory,
context artifacts, and session history are shared product/runtime facilities,
not Agent-private memory. Therefore this Task records the governing decision
only; implementation begins at MA6-T03 and later tasks.

The existing PrincipalMemory consent/export/retention surfaces remain outside
this Agent-private-memory decision. MA6-T01 neither enables nor changes those
principal-level features.

## Acceptance

```text
SESSION_LOCAL_AGENT_MEMORY selected: PASS
session deletion requirement recorded: PASS
cross-session profiling explicitly excluded: PASS
consent/retention/export complexity deferred: PASS
no implementation or second memory subsystem introduced: PASS
```

```text
MA6-T01 = PASS
NEXT_TASK = NOT_STARTED
```

