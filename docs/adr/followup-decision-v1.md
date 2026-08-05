# ADR: Durable two-stage Followup Decision V1

- Status: Accepted for Phase 1 implementation
- Date: 2026-08-05
- Policy version: `adaptive_v1`

## Decision

An accepted answer first produces one immutable Followup Decision Artifact. Routing
then uses that persisted decision. Follow-up text generation is a separate durable
stage and occurs only for `action=follow_up`.

```text
answer command -> idempotent answer commit -> leased decision attempt
 -> persisted decision -> next_question
                    or -> leased generation -> persisted chunks/final text
                       -> idempotent interviewer-message commit
```

The Decision Artifact contains:

```text
decision_id, session_id, source_command_id, question_id, policy_version,
input_sha256, answer_state, gap_type, gap_summary, action, confidence,
reason_code, followup_count_before, closed_gap_ids, created_at, artifact_sha256
```

It deliberately excludes final question text and every scoring field.
`(session_id, source_command_id)` is unique. Client `command_id` remains the original
idempotency identity; policy version is data, not a suffix used to create duplicate
side effects.

## Lease, fencing, and attempts

Preparing a decision is idempotent. One worker acquires a lease and monotonically
increasing fencing token, heartbeats while active, and may make at most three attempts
for retryable failures. A stale worker cannot persist after a newer fence. Terminal
invalid output or exhausted attempts records a bounded failure/degradation reason and
routes according to the frozen fail-closed policy; it never retries forever.

Once the Artifact exists, Provider retries, checkpoint replay, duplicate commands,
process restart, and response loss return the same decision and hash. Crashes before
Artifact commit may retry under a new fence; crashes after commit must not recompute.

## Routing invariants

- `next_question` creates no generation identity and makes no Generation Provider
  call.
- `follow_up` creates exactly one `generation_id=hash(decision_id, generation
  version)` and the final text contains exactly one question.
- `followup_count_before >= 2` deterministically produces `next_question` without a
  Decision or Generation Provider call.
- Confidence is used for degradation and observation only. It never changes scores.
- Closed gaps cannot be reopened by replay without a new accepted answer command.

Legacy/durable entry points call the same policy and service. Legacy storage uses an
in-memory implementation of the same contract. A session created with `fixed_v1`
remains fixed for its lifetime and is never switched mid-session to `adaptive_v1`.

## Recovery truth table

| Crash point | Recovery result |
| --- | --- |
| Before answer commit | retry original command; no Decision exists |
| After answer commit, before decision lease | prepare/acquire same Decision identity |
| During decision attempt | expired lease may be reacquired with a higher fence |
| After Decision commit, before route | load and route the same Artifact |
| During follow-up generation | resume the one generation stream/identity |
| After message commit, before response | return the committed message idempotently |

Rollback selects `fixed_v1` only for newly created sessions. Existing adaptive
Artifacts remain readable and replayable.
