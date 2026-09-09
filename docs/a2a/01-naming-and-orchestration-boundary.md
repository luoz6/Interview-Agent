# A2A-V1 Naming & Orchestration Boundary

## Status

- Phase: `T-1B — Naming & Orchestration Boundary`
- Plan version: `v0.2`

## Frozen Terms

| Term | Definition |
|---|---|
| `Interview Lifecycle Coordinator` | Architectural role that coordinates `Prep -> Interview -> Review -> Report` |
| `Session Orchestrator` | Existing interview-session state/command orchestration |
| `Durable Interview Workflow` | LangGraph durable interview execution, checkpoint, resume, lease |
| `Durable Review Workflow` | LangGraph durable review execution |
| `A2A Agent` | Independent business capability with a stable card, task, artifact, and error contract |
| `Service` | Non-agent business/policy service such as follow-up decision |
| `Worker` | Background job executor with claim/lease/heartbeat |
| `Runtime` | Shared execution infrastructure such as `ContextRuntime` |
| `Platform` | Higher-level logical grouping above runtime/services |

## Coordinator Boundary

`Interview Lifecycle Coordinator` is currently an architectural role, not a
single existing Python class.

The current `OrchestratorAgent`/`InterviewGraphRunner` is a Session
Orchestrator. It must not be directly renamed into the Lifecycle Coordinator.

## Three-Layer Orchestration

```text
Lifecycle Coordinator        architectural role
          |
Session Orchestrator         current interview session orchestration
          |
Durable Workflow             execution/persistence runtime
```

These are different layers and must remain distinguishable in code and docs.

## Agents, Services, Workers

### A2A Agents

- Examiner Agent
- Knowledge & Grounding Agent
- Interview Reviewer Agent
- Report Coach Agent

### Services / Non-Agents

- Follow-up Decision Service
- Context Compression Service
- Context Runtime
- Knowledge retrieval/grounding services may be invoked by Knowledge Agent

### Workers / Infrastructure

- Report Worker
- Session Deletion Worker
- Runtime Outbox Worker

## Gate -1B

Status: `PARTIAL`

Agent rename, Agent Card implementation, and A2A SDK integration are not
authorized before this naming boundary is approved.
