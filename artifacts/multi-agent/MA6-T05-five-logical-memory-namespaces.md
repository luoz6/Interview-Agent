# MA6-T05 — Five Logical Memory Namespaces

## Implementation

`AgentMemoryScope.memory_type` is now restricted to exactly five logical
namespaces:

```text
scheduler
knowledge
examiner
reviewer
report-coach
```

The canonical `AgentMemoryType` literal and `AGENT_MEMORY_TYPES` tuple expose
the same closed set. Unknown namespace values fail model validation.

All five namespaces continue to use the single `AgentMemoryPort` contract and
the same complete ownership key. No namespace-specific port, adapter, table,
or physical memory subsystem was introduced.

## Verification

```text
Agent memory namespace/port contracts: 10 passed
all five namespace imports and exact ordering: PASS
unknown namespace rejection: PASS
Python compileall (domain memory and ports): PASS
git diff --check: PASS (only existing LF/CRLF conversion warnings)
```

## Acceptance

```text
scheduler namespace: PASS
knowledge namespace: PASS
examiner namespace: PASS
reviewer namespace: PASS
report-coach namespace: PASS
closed namespace set: PASS
single shared physical boundary: PASS
```

```text
MA6-T05 = PASS
NEXT_TASK = NOT_STARTED
```

