# A2A-V1 Task / Context Mapping

## Status

- Phase: `T03 — A2A Task ↔ AgentExecutionContext Mapping`
- Plan version: `v0.2`

## Mapping

Existing `AgentExecutionContext` is not replaced. A2A metadata is mapped onto
the existing trace/runtime model.

| A2A concept | Existing concept |
|---|---|
| `contextId` | interview / prep / review workflow context |
| `taskId` | one professional Agent work lifecycle |
| `metadata.correlation_id` | `AgentExecutionContext.correlation_id` |
| `metadata.causation_id` | `AgentExecutionContext.causation_id` |
| `metadata.parent_run_id` | `AgentExecutionContext.parent_run_id` |
| `metadata.command_id` | `AgentExecutionContext.command_id` |

## Task Lifecycle

```text
submitted
working
input-required
completed
failed
canceled
rejected
```

### Semantics

- `completed` => a valid domain artifact exists, except for explicitly
  message-only skills.
- `working` => a valid execution attempt exists.
- `failed` => `error_code` and terminal reason exist.

## Constraint

A2A must not create a second trace system. It must reuse the existing
AgentExecutionContext / runtime trace records.
