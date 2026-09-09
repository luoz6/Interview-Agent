# A2A-V1 ContextPlatform / Worker Boundary Cleanup

## Status

- Phase: `T11 — ContextPlatform / Worker Boundary Cleanup`
- Plan version: `v0.2`

## Context Platform

Logical hierarchy:

```text
ContextPlatform
├── ContextRuntime
├── ContextCompressionService
├── ContextArtifact
├── Memory
└── Recovery
```

`ContextRuntime` keeps its current semantics. It is not expanded into a God
Object.

## Context Compressor

`ContextCompressorAgent` is treated as a service, not an A2A Agent. It does
not register an Agent Card and does not participate in A2A peer discovery.

## Workers

| Component | Classification |
|---|---|
| Report Worker | Worker |
| Session Deletion Worker | Worker / Data Lifecycle Infrastructure |
| Runtime Outbox Worker | Worker |

Workers execute tasks but are not A2A Agents.
