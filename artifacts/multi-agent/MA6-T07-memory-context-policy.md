# MA6-T07 — Memory Context Policy

## Implementation

Added a frozen `AgentMemoryContextPolicy` that bounds Agent-private memory by:

```text
retrieval_limit
max_ttl_seconds
summary_mode = summary_only
max_context_tokens
```

`AgentMemoryRecord` requires a bounded summary, timezone-aware creation and
expiry timestamps, and optional artifact references. It rejects raw extra
payload fields, invalid lifetimes, and duplicate artifact references.

The shared memory store now:

- caps every recall at the configured retrieval limit;
- excludes expired records;
- rejects already-expired records and records beyond the maximum TTL;
- searches only bounded summaries.

`select_agent_memory_context` orders active records by recency, applies the
retrieval cap, renders summaries only, and enforces the context token budget.
It directly reuses the existing context subsystem's `TokenEstimator` and
`truncate_text_to_tokens`; no independent compression framework was created.

## Verification

```text
Agent memory policy/port/isolation contracts: 18 passed
existing context budget/selection and architecture regressions: 101 passed
Python compileall: PASS
public imports: PASS
git diff --check: PASS (only existing LF/CRLF conversion warnings)
```

## Acceptance

```text
retrieval limit: PASS
expiry and maximum TTL: PASS
summary-only context: PASS
context token budget: PASS
existing context/compression primitives reused: PASS
no new compression framework: PASS
```

```text
MA6-T07 = PASS
NEXT_TASK = NOT_STARTED
```

