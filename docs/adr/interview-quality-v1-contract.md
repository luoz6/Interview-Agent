# ADR: Interview Quality V1 contract

- Status: Accepted for implementation
- Date: 2026-08-05
- Contract version: `interview-quality-v1-contract-v1`
- Scope: local V1 engineering and offline quality evaluation

## Decision

Interview Quality V1 uses independent state axes for work execution, generation,
evaluation, evidence, score publication, and coverage. No boolean such as
`is_fallback`, and no numeric sentinel such as `0`, may substitute for one of these
states.

### Frozen vocabulary

| Axis | Values | Meaning |
| --- | --- | --- |
| `job_status` | `queued`, `running`, `completed`, `failed` | Lifecycle of asynchronous work only |
| `generation_status` | `complete`, `degraded`, `failed` | Whether a lawful report body was generated |
| `score_status` | `scored`, `partial`, `unscored` | Whether numeric scores may be published |
| `coverage_status` | `complete`, `partial`, `none` | How much of the planned assessment was evaluated |
| `evaluation_status` | `evaluated`, `not_evaluated` | Whether an answer/dimension received an assessment |
| `evidence_status` | `sufficient`, `partial`, `insufficient`, `not_applicable` | Whether the claimed assessment has supporting session evidence |
| `answer_state` | `complete`, `partial`, `incorrect`, `off_topic`, `empty`, `skipped` | Classification of an accepted answer action |
| `report_path` | `microbatch`, `full_session`, `heuristic`, `legacy` | Engineering path only; never a trust level |

`job_status=failed` means that no new lawful Report Artifact was published by that
job. It does not mean `score_status=unscored`, because a failed job produces no
Artifact at all. `generation_status=degraded` means a valid Artifact exists with
explicit limitations; it is not inferred from the generation path.

### Numeric score publication

A numeric score is allowed only when all of the following are true:

1. the answer was presented and accepted as an attempt rather than skipped;
2. the item has `evaluation_status=evaluated`;
3. the score is recomputed by the versioned deterministic backend rubric;
4. the Artifact passes schema, evidence-reference, ownership, and runtime quality
   validation;
5. `score_status` is `scored` or `partial`;
6. the number belongs only to an evaluated item or dimension.

For `score_status=unscored`, overall, per-question, and per-dimension numeric fields
are `null`. For `score_status=partial`, only evaluated members contain numbers and
every aggregate displays its evaluated numerator and eligible denominator. An
unevaluated dimension is `not_evaluated`, is `null`, and is excluded from strongest,
weakest, average, and rank calculations.

### Answer-state semantics

| Answer state | Evaluation semantics | Numeric semantics |
| --- | --- | --- |
| `complete` | Addresses the material requirements with usable evidence | May be scored |
| `partial` | Addresses a strict subset of material requirements | May be scored with missing points recorded |
| `incorrect` | Makes a substantive but materially wrong attempt | May receive a low score, including zero when the rubric warrants it |
| `off_topic` | Substantive content does not answer the presented question | May receive a low score only when the rubric can deterministically evaluate the mismatch |
| `empty` | Submitted content has no substantive answer, including whitespace-only or explicit non-answer text | May receive zero only as evidence of an attempted non-answer; never used for a skipped question |
| `skipped` | User explicitly advances without answering | `not_evaluated`, numeric fields `null`, lowers coverage rather than capability score |

The classifier must persist the state and reason. “Meaningless” is not a hidden
seventh state: it maps to `empty` when no proposition is present, or `off_topic` when
substantive unrelated content is present.

### Follow-up boundary

Each main question permits zero, one, or two follow-ups. The persisted policy version
and `followup_count_before` decide the limit. Once two follow-ups have been committed,
the next action is deterministic `next_question`, with no Decision or Generation
Provider request. Each follow-up is one question, not a bundle of questions.

### Immutable Artifact boundaries

- A Plan Revision contains the complete immutable plan snapshot, its stable question
  IDs, source reference and hashes, configuration snapshot, generator version, and
  lineage. It never contains a Report or a Followup Decision.
- A Followup Decision Artifact contains the immutable action and reasoning metadata
  for one accepted answer command. It never contains final follow-up question text or
  scores.
- A Report Artifact contains one immutable, runtime-validated report revision and
  evidence references. Job state and active/latest pointers live outside its payload.

All three use frozen canonical UTF-8 JSON hashing: object keys sorted
lexicographically, no insignificant whitespace, Unicode encoded without ASCII
escaping, finite JSON numbers only, and a terminal byte sequence exactly equal to the
canonical JSON bytes (no BOM and no newline). Identity, revision, timestamp, and hash
fields are never silently regenerated during replay.

## Principal Memory boundary

Principal Memory is prohibited as an input to answer classification, follow-up
decisions, numeric scoring, strengths/weaknesses, report summaries, report claims,
evidence selection, and quality evaluation. It must not appear in Report evidence
references or Provider prompts for those operations.

Explicitly consented preferences may be used outside the assessment boundary for
non-evaluative presentation settings, such as language or accessibility preferences.
Those settings must be copied into a non-sensitive configuration snapshot and may not
change scores or report claims. Session-local answers and lawful session evidence are
not Principal Memory and remain eligible under their own contracts.

## Legacy compatibility

- A legacy session without a Plan Revision is read as
  `plan_origin=legacy_session_snapshot`; no historical revision is fabricated.
- A legacy report may be read through an adapter with `report_path=legacy` and
  explicit `legacy_unknown` reason codes. Missing states remain unknown/unscored;
  they are not inferred from `is_fallback` or a zero value.
- Existing valid reports remain readable while additive migrations run. New V2 jobs
  cannot overwrite legacy rows or immutable Artifacts.
- Fixed-policy legacy sessions remain `fixed_v1` for their lifetime. They do not
  switch to `adaptive_v1` after recovery.

## Consequences

Consumers must render state explicitly and tolerate `null` scores. Coverage and job
health may coexist with an active older report. Tests must exercise every state axis
independently and must reject attempts to derive trust from generation path,
fallback flags, or numeric sentinels.
