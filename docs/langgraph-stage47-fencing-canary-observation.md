# Stage 47 LangGraph Fencing Canary Operator Observation

Status: NOT_RUN

No deployed Stage 47 fencing canary is explicitly authorized or claimed by this record.
Repository acceptance, isolated PostgreSQL tests, and local synthetic probes do
not constitute a staging or production observation.

## Preconditions

- Stage 46: `READY_FOR_FENCING_CANARY`
- Stage 47 repository: `READY_FOR_OPERATOR_FENCING_CANARY`
- Committed Interview rollout default: zero
- Committed Review rollout default: zero
- Explicit environment/change authority: REQUIRED
- Approved minimum phase duration and workflow-specific samples: REQUIRED

## Fixed Sequence

```text
0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0
```

| Phase | Rollout | Started at UTC | Ended at UTC | Safe samples | Decision | Stable reasons |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline | `0/0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Interview | `1/0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Interview drain | `0/0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Review | `0/1` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Review drain | `0/0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Joint | `1/1` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Final drain | `0/0` | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |

Rollback is assignment-only. It never rewrites existing engine/version
ownership, reroutes Durable work to Legacy, disables runtime support, or
deletes active checkpoints/control/artifact rows. Already assigned work must
drain under its immutable engine and graph version.

Canary evidence contains aggregate counts and stable codes only. It never
contains workflow identifiers, tokens, content, provider output, DSNs, or
credentials. Exactly-once external provider invocation is not claimed.

Connection pools, State v2, checkpoint retention, question-level Review retry,
Legacy retirement, and rollout above 1% are deferred to separately approved
stages.
