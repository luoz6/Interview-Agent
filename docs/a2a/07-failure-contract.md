# A2A-V1 Failure / Retry / Cancellation Contract

## Status

- Phase: `T12 — Failure / Retry / Cancellation Contract`
- Plan version: `v0.2`

## Error Taxonomy

```text
protocol_error
unsupported_skill
invalid_request
task_rejected
domain_validation_failed
provider_timeout
provider_unavailable
knowledge_unavailable
artifact_validation_failed
state_conflict
lease_lost
task_canceled
unexpected_error
```

Each error carries:

- `retryable`
- `terminal`
- `fallback_allowed`
- `public_message`
- `internal_reason`
- `observability_code`

## Retry Ownership

Retries are not allowed to stack across every layer. Ownership must be
explicitly chosen:

```text
A2A Client Retry
Agent Runtime Retry
Worker Retry
Provider Retry
```

Only one or two intentional layers should retry for a given operation.

## Cancellation

Supported states:

```text
Task Cancel Requested
Agent Stop
Worker Lease Release / Stop
Final Canceled State
```

Partial artifacts must have an explicit retention policy before cancellation.
