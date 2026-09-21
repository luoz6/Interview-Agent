# MA6-T03 — AgentMemoryPort

## Implementation

Added the neutral `app.ports.agent_memory.AgentMemoryPort` protocol and
re-exported it from `app.ports`. The minimal contract is:

```text
recall(*, scope, query=None, limit=None) -> tuple
remember(*, scope, memory) -> memory
delete_scope(*, scope) -> int
```

`delete_scope` is a required protocol member. A runtime adapter that exposes
only `recall` and `remember` does not conform to `AgentMemoryPort`.

The port remains transport- and storage-neutral. Scope ownership details are
intentionally deferred to MA6-T04, and no second memory subsystem or concrete
adapter was introduced.

## Verification

```text
AgentMemoryPort contract tests: 3 passed
runtime import: PASS
Python compileall (app/ports): PASS
git diff --check: PASS (only existing LF/CRLF conversion warnings)
```

## Acceptance

```text
recall present: PASS
remember present: PASS
delete_scope present and mandatory: PASS
transport-neutral port boundary: PASS
no second memory subsystem: PASS
```

```text
MA6-T03 = PASS
NEXT_TASK = NOT_STARTED
```

