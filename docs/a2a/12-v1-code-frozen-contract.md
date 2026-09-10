# A2A-V1 Frozen Contract

Status: `FROZEN_FOR_V2`

## Agent IDs

```text
interview-examiner
knowledge-and-grounding
interview-reviewer
report-coach
```

## Skills

```text
generate-followup
generate-interview-plan
evaluate-answer
evaluate-interview
generate-report
```

## Task States

```text
submitted
working
completed
failed
canceled
rejected
```

## Invariants

```text
completed => valid Artifact
canceled => late successful result cannot become completed
same logical command => stable idempotency key => no duplicate execution
retryable failure => new Attempt, not unrelated logical operation
```
