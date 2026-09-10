# A2A-V1 C09-C10 Task Lifecycle Semantic Closure

## Status

```text
C09 Cancellation: PASS
C10 Idempotency: PASS
A2A-V1: CODE_FROZEN
E2E: DEFERRED
V2 Implementation: AUTHORIZED
```

## Cancellation Contract

```text
submitted -> cancel -> canceled
working   -> cancel -> canceled
completed -> cancel -> completed (no mutation)
failed    -> cancel -> failed
canceled  -> cancel -> canceled (idempotent)
rejected  -> cancel -> rejected
unknown   -> cancel -> task_not_found
```

Late provider result after cancel must not transition to completed.

## Idempotency Contract

```text
completed replay same task/artifact, no provider re-execution
working returns existing task
failed retryable allows new attempt
failed terminal returns terminal failure
canceled/rejected require new command/key
```

Stable key:

```text
a2a-v1:<agent>:<skill>:<sha256(canonical identity)>
```

## Known Limitations

- PostgreSQL durable TaskStore still pending C13.
- Full business E2E still pending C11/C12.
