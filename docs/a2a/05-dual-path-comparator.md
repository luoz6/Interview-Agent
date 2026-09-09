# A2A-V1 Dual-Path Comparator

## Status

- Phase: `T06 — Dual Path Semantic Comparator`
- Plan version: `v0.2`

## Purpose

No Agent may switch to A2A as the main business path until a machine-executable
comparator can compare:

```text
AGENT_TRANSPORT=local
```

against:

```text
AGENT_TRANSPORT=a2a
```

## Comparator Types

### Deterministic Comparator

For mocked/fixture providers:

- Domain artifact business payload must match.
- Ignore `task_id`, `message_id`, timestamps, and transport metadata.

### Semantic Comparator

For real LLM providers:

- Text does not need exact match.
- Business invariants must match.

## Comparison Scope

### Examiner

- question id consistent
- gap id consistent
- follow-up non-empty
- policy compliant
- no duplicate closed gap

### Knowledge

- role scope consistent
- question count matches config
- evidence scope legal
- grounding binding legal

### Reviewer

- evaluation dimensions complete
- score in range
- evidence references legal

### Report

- required sections complete
- evaluation consumption complete
- advice traceable
- action plan exists

## Gate F

Status: `PENDING_IMPLEMENTATION`
