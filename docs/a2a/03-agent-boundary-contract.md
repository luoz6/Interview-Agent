# A2A-V1 Agent Boundary Contract

## Status

- Phase: `T01 — Agent Boundary Contract`
- Plan version: `v0.2`

## Frozen Agents

Only four professional capabilities are A2A Agents in V1:

1. Examiner Agent
2. Knowledge & Grounding Agent
3. Interview Reviewer Agent
4. Report Coach Agent

The Lifecycle Coordinator is an architectural role, not yet a concrete A2A
Agent.

## Shared Contract Shape

Each agent must define:

- `Agent Name`
- `Business Goal`
- `Responsibilities`
- `Non-Responsibilities`
- `Skills`
- `Input`
- `Output`
- `Artifact`
- `Required Context`
- `Owned State`
- `Read-only State`
- `Tools`
- `Services`
- `Failure Semantics`
- `Timeout`
- `Retry`
- `Idempotency`
- `Security Scope`

## Examiner Agent

| Field | Value |
|---|---|
| Name | `interview-examiner` |
| Business Goal | Generate a grounded, policy-compliant follow-up question for the current interview question |
| Responsibilities | Generate follow-up text; probe one open answer gap |
| Non-Responsibilities | Final follow-up policy; numeric scoring; report narrative |
| Skills | `generate-followup`, `probe-answer-gap` |
| Input | current question, candidate answer, selected gap |
| Output | `FollowupArtifact` |
| Required Context | current question, answer, gap, follow-up policy |
| Owned State | none |
| Read-only State | session plan, question, gap |
| Failure Semantics | provider timeout/unavailable; invalid follow-up output |
| Timeout | provider and stream timeouts inherited from runtime |
| Retry | bounded by provider/worker retry policy |
| Idempotency | `session_id + question_id + answer_revision + gap_id + policy_version` |
| Security Scope | no access to full private user materials unless bound evidence is supplied |

## Knowledge & Grounding Agent

| Field | Value |
|---|---|
| Name | `knowledge-and-grounding` |
| Business Goal | Turn role/profile context into retrievable, grounded interview-plan inputs |
| Responsibilities | role analysis; knowledge query generation; RAG retrieval; grounding; evidence binding |
| Non-Responsibilities | interview workflow control; final report narrative |
| Skills | `analyze-role`, `generate-interview-plan`, `retrieve-grounding`, `ground-question` |
| Input | JD, resume, scope, current question/intent |
| Output | `GroundingArtifact` |
| Required Context | source scope, retrieval profile, target question/plan |
| Failure Semantics | knowledge unavailable; evidence insufficient; embedding disabled |
| Security Scope | must preserve existing Knowledge Scope, Evidence Scope, Memory Scope |

## Interview Reviewer Agent

| Field | Value |
|---|---|
| Name | `interview-reviewer` |
| Business Goal | Produce evidence-bound question/session evaluations |
| Responsibilities | evaluate answer/question; identify gaps; score competency; bind evidence |
| Non-Responsibilities | final report narrative; changing coach decisions |
| Skills | `evaluate-answer`, `evaluate-question`, `evaluate-interview` |
| Input | session state/question, candidate answer, evidence references |
| Output | `EvaluationArtifact` |
| Required Context | question, answer, evidence, review policy |
| Failure Semantics | insufficient evidence; provider failure; artifact validation failure |
| Idempotency | session/question/answer/evaluation policy |
| Security Scope | minimum required evidence only |

## Report Coach Agent

| Field | Value |
|---|---|
| Name | `report-coach` |
| Business Goal | Aggregate evaluations into a final coaching report |
| Responsibilities | generate report; generate coaching advice; generate action plan; repair report |
| Non-Responsibilities | overwriting Reviewer scores or evidence |
| Skills | `generate-report`, `repair-report`, `generate-action-plan` |
| Input | `EvaluationArtifact[]` |
| Output | `ReportArtifact` |
| Required Context | evaluations, session plan, report policy |
| Failure Semantics | provider failure; report output invalid; report quality failure |
| Security Scope | no raw provider prompt/chain-of-thought exposure |

## Explicitly Non-A2A

The following are not A2A Agents:

- Follow-up Decision Service
- Context Compression Service
- Context Runtime
- Report Worker
- Session Deletion Worker

## Gate B

Status: `PENDING_REVIEW`

Artifact implementation must match this boundary.
