# ADR: Interview Plan Revision V2 and start API

- Status: Accepted for Phase 1 implementation
- Date: 2026-08-05
- Schema version: `interview-plan-v2`

## Decision

Preparation, editing, regeneration, and interview start share one immutable Plan
Revision chain. Phase 1 publishes the minimum V2 schema; Phase 5 may add configured
budgets and UX but must not introduce a second plan schema, editor, or start service.

### Records

`PlanSourceRecord` is the only family-level copy of sensitive source material:

```text
source_id, source_sha256, encrypted_payload_or_ref, retention_policy, created_at
```

`PlanConfigurationSnapshot` freezes:

```text
difficulty, target_duration_minutes, focus_preset, question_type_budget,
expected_followup_budget, max_followups_per_question=2, generator_version,
followup_policy_version
```

Every `InterviewPlanQuestionV2` contains:

```text
question_id, position, question_text, focus, question_type, difficulty,
expected_minutes, expected_followups, origin, replaces_question_id,
knowledge_binding
```

Every `InterviewPlanRevision` contains:

```text
plan_revision_id, plan_family_id, revision, parent_revision_id, source_kind,
source_id, source_sha256, configuration_snapshot_json, plan_json, plan_sha256,
generator_version, created_at, created_reason
```

IDs are stable opaque UUIDs. Position is independent from identity. Revisions are
append-only and `(plan_family_id, revision)` is unique. Canonical JSON follows the
algorithm frozen in the V1 contract ADR.

### Revision operations

- Initial generation creates revision 1.
- Edit, reorder, delete, restore, custom insertion, and one-question regeneration
  each create exactly one next revision and preserve the parent.
- Reorder preserves question IDs. Text edit preserves the edited question ID and
  records `origin=edited`. Regeneration creates a new question ID, sets
  `origin=regenerated`, and records `replaces_question_id`.
- `expected_revision` mismatch is HTTP 409 and creates no revision.
- Expiry prevents new edit/regenerate/start operations but does not delete readable
  revisions or session snapshots.

### API contract

```text
POST /api/prep
  input: source inputs + configuration
  output: plan_family_id, plan_revision_id, revision, plan_sha256, plan

PATCH /api/interview-plans/{plan_family_id}
  input: expected_revision, operations[]
  output: newly appended revision

POST /api/interview-plans/{plan_family_id}/questions/{question_id}/regenerate
  input: expected_revision, regeneration constraints
  output: newly appended revision

POST /api/interviews
  input: plan_revision_id, expected_revision, plan_sha256
  output/effect: verified immutable session snapshot
  Provider calls: exactly 0
```

Start locks and verifies the selected revision, revision number, and hash. It copies
the full plan and configuration snapshot into the session in the same logical commit.
It does not accept raw JD/resume as an alternative start path and never invokes plan
generation.

### Source retention and deletion

A revision stores only `source_id + source_sha256`, never a second raw resume or JD.
Draft, family revision, and session references are explicit. Deleting a Draft removes
only its reference. Session deletion cannot remove a source still referenced by
another session or family. Source deletion requires explicit ownership/reference
checks and an auditable tombstone that contains no original text.

After source payload deletion, existing Plan Revisions and session snapshots remain
readable. Regeneration fails closed with `plan_source_unavailable`; it must not infer
or reconstruct missing source. In-memory and PostgreSQL stores expose the same
conflict, retention, tombstone, and replay behavior.

## Phase 5 T50 amendment: generation configuration strategy

- Amendment status: Accepted
- Amendment date: 2026-08-06
- Policy manifest: config/interview_plan_generation_policy_v1.json
- Policy version: interview-plan-config-strategy-v1

This amendment does not add a schema, field, editor, revision path, or start path.
It freezes how the existing eight PlanConfigurationSnapshot fields are interpreted
and hashed.

### Allowed configuration

    difficulty                  foundation|intermediate|advanced
    target_duration_minutes     15|30|45|60
    focus_preset                technical_depth|system_design|project_review|balanced
    question_type_budget        exact generated main-question target by type
    expected_followup_budget    aggregate estimate, not a runtime quota
    max_followups_per_question  hard runtime ceiling 2
    generator_version           stable lowercase version identifier
    followup_policy_version     fixed_v1|adaptive_v1

question_type_budget is sparse: an omitted question type means zero. Counts must
be actual non-negative integers, Boolean/string coercion is prohibited, and the
total must be at least one. It records the generation target; later manual edits
may diverge and receive duration/budget warnings without rewriting history.

expected_followup_budget is the aggregate expected count used by the duration
estimate and generation hint. Adaptive runtime may use fewer. It is not a hard
quota. The only runtime hard limit remains two follow-ups per main question.

### Effect separation

- Difficulty changes requested depth/complexity, not passing thresholds.
- Target duration selects the T51 question-budget profile and duration estimate;
  it is not an exact-time SLA.
- Focus changes question-type allocation and prompt emphasis, not the rubric.
- Generator version identifies the prompt and deterministic budget-enforcement
  implementation.
- Follow-up policy selects runtime decision behavior from the immutable session
  snapshot.
- Every field appears in the configuration snapshot and canonical plan hash.
- Revision-level generator_version must equal the snapshot value.
- Configuration may never select a scoring rubric or change scoring strictness.

T50 freezes the four duration values and strategy semantics only. T51 owns the
question ranges and estimate formula; T52 owns Provider over/under-budget handling
and launch validation; T54 owns final user-facing warning interaction. The
existing 3–5 validation is intentionally not changed by T50.

### Hash and parser behavior

Configuration and plan hash helpers always serialize and revalidate a model
instance before hashing. A caller cannot use Pydantic model_copy to smuggle an
invalid policy or configuration value into a trusted hash.

The v1 parser remains available and does not call a Provider. Its compatibility
defaults remain intermediate difficulty, 30 minutes, balanced focus, fixed_v1,
and a hard per-question follow-up ceiling of two. Parsing v1 creates the existing
Schema v2 boundary object; it does not revive legacy IDs after the conversion.

## Rollback

Disable V2 write/read routing and return to legacy reads. Do not drop V2 tables,
source tombstones, revisions, or session snapshots. No rollback path re-enables
Provider generation during interview start.
