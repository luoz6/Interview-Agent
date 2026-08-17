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
  input: plan_revision_id, expected_revision, plan_sha256, request_id
  output/effect: verified immutable session snapshot
  Provider calls: exactly 0
```

Start locks and verifies the selected revision, revision number, and hash. It copies
the full plan and configuration snapshot into the session in the same logical commit.
It does not accept raw JD/resume as an alternative start path and never invokes plan
generation. `request_id` is a required idempotency identity scoped to the plan family:
an identical replay returns the existing session, while reuse with a different plan
revision, expected revision, or plan hash fails closed with
`session_start_request_conflict`.

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

## Phase 5 T51 amendment: duration budget and launch safety

- Amendment status: Accepted
- Amendment date: 2026-08-06
- Budget version: `interview-plan-duration-budget-v1`
- Formula version: `main-answer-plus-followups-plus-transitions-v1`
- Canonical policy SHA-256:
  `4f7213f4dd010032c75c61fa6ee7adf9941868912dbbf35f0deb75e4b3ca3b8b`

The target duration is an estimate, never an exact-time SLA. The frozen first
version profiles are:

| Target | Recommended main questions | Acceptable estimate |
|---:|---:|---:|
| 15 minutes | 3–4 | 12–20 minutes |
| 30 minutes | 5–6 | 24–36 minutes |
| 45 minutes | 7–8 | 36–54 minutes |
| 60 minutes | 9–10 | 48–72 minutes |

Each profile expects an average of zero to one follow-up per question. Runtime
may adapt below that estimate, while the immutable hard ceiling remains two
follow-ups per main question.

### Estimate and allocation

The service owns the only duration arithmetic source:

```text
estimated_minutes =
  sum(question.expected_minutes)
  + sum(question.expected_followups) * 2
  + max(0, question_count - 1) * 1
```

The two-minute follow-up budget and one-minute between-question transition are
part of the hashed budget policy. A policy/profile/formula change requires a new
version and canonical hash. Revision API responses contain the full server-side
`budget_assessment`, including formula inputs and result. Frontend code consumes
that response and must not reproduce the formula.

Generation allocates the aggregate expected-followup budget across questions
without exceeding two per question, then allocates at least one main-answer
minute per question. When feasible, deterministic allocation closes exactly to
the configured target duration. The expected-followup budget remains an estimate,
not a runtime quota.

### Warning and blocking truth table

| Condition | Result |
|---|---|
| Below or above the profile's recommended question range | warning; launch allowed |
| Below or above the acceptable estimated-duration range | warning; launch allowed |
| Manual question-type allocation differs from the generation snapshot | warning; launch allowed |
| Manual expected-followup total differs from the generation snapshot | warning; launch allowed |
| No valid main question remains | blocked |
| More than ten main questions | blocked |

Schema v2 and the editor therefore use the safe range 1–10. Deleting from the
recommended range down to one is permitted and recalculates warnings after every
revision; deleting the final question fails without creating a revision. Adding
through ten is permitted; the eleventh fails without creating a revision. There
is no unconditional 3–5 validator on configured Schema v2 plans. Legacy callers
that do not supply a configuration retain their 3–5 compatibility boundary until
they cross the configured V2 API.

Configured deterministic fallback respects the exact question-type budget,
difficulty, focus, and 1–10 safe range, including nine or ten questions for a
60-minute plan. T52 remains responsible for applying the same budget to Provider
prompts and enforcing Provider over/under-budget results.

## Phase 5 T52 amendment: configured generation enforcement

- Amendment status: Accepted
- Amendment date: 2026-08-06
- Generator version: `plan-generator-v2`

New `/api/prep` requests accept one nested `configuration` snapshot. When it is
omitted, the server supplies a 30-minute, intermediate, balanced, fixed-v1
configuration with five main questions: one project, two technical, one system
design, and one behavioral. The old three-question values remain parser defaults
for already-existing v1 plans only; they are not the default for new generation.

The client may choose an allowed duration, difficulty, focus, type budget,
expected-followup estimate, and follow-up policy. It may not claim a generator
implementation that is not deployed. A generator version other than
`plan-generator-v2`, a 0/>10 generation target, or an aggregate follow-up budget
above `question_count * 2` fails before any Provider call.

### Prompt and context contract

The Provider prompt contains the exact main-question count and per-type budget,
target duration, difficulty guidance, focus guidance, expected follow-up budget,
and the hard limit of two follow-ups. Its example JSON expands through the exact
requested `qN`; a ten-question configuration includes `q1..q10`. Difficulty and
focus affect requested content, never scoring strictness.

JD, resume, and safe grounding candidates continue through the shared plan
context selector. The selector reserves 20% of available input for fixed
instructions/schema/framing, then bounds JD, resume, and grounding independently.
The final rendered-prompt guard remains authoritative. A 60-minute ten-question
prompt must fit the same `knowledge.generate_plan` context policy; increasing the
question count does not increase the operation's input cap or bypass output-token,
timeout, retry, or concurrency safeguards. Starting with T52, plan generation
always runs both selection and final enforcement even when the staged context
rollout flag is disabled; flags for interview/review/report operations are
unchanged.

### Provider result enforcement

Provider output is untrusted and is revalidated at both the LLM and KnowledgeAgent
boundaries. The frozen rules are:

1. IDs must be unique, consecutive, and ordered `q1..qN`; focus and question text
   must be nonblank; retained question text must not duplicate after whitespace
   and case normalization.
2. Fewer questions than the configured exact target are rejected with
   `provider_question_count_under_budget`.
3. More than ten questions are rejected with
   `provider_question_count_above_safe_maximum`.
4. If `target < N <= 10`, only the consecutive first `target` questions may be
   retained. This deterministic prefix is accepted only when its type counts
   exactly equal `question_type_budget`; otherwise the response is rejected with
   `provider_question_type_budget_mismatch`.
5. Enforcement never reorders Provider questions, invents missing questions, or
   rewrites a mismatched type into another type.
6. A budget-validation failure after a successful structured response does not
   trigger a second raw-JSON Provider request.

Initial prep may publish the complete configured deterministic fallback and mark
the Agent run degraded with the precise enforcement code. Regeneration does not
use fallback: timeout or invalid/under/over-budget Provider output creates no new
revision and returns a structured failure code while the prior active revision
remains authoritative.

### Single revision and trace identity

After generation/grounding, the shared prepare service converts the accepted
legacy Provider boundary object to Schema v2 exactly once. It assigns opaque UUID
question identities, contiguous positions, configured difficulty, deterministic
minute/follow-up allocations, and the immutable configuration snapshot. The API
stores that already-bound object; it does not allocate a second set of IDs. Both
the top-level compatibility preview and nested `legacy_plan` are projected from
the saved revision, so neither exposes the Provider's temporary `q1..qN` IDs.
Regeneration revalidates the legacy projection but reuses an already-bound V2
result from the shared prepare service; it must not allocate new UUIDs after the
Agent trace hash has been emitted.

Agent safe metadata contains `configuration_sha256` and the exact `plan_sha256`
later persisted by the revision store, plus generator/budget versions, target
duration, Provider question count, retained count, and enforcement action. Hash
metadata is admitted only when it is lowercase 64-character hexadecimal; source
text, prompts, resumes, Provider responses, credentials, and raw content remain
blocked by trace sanitization.

## Phase 5 T54 amendment: revision audit and Knowledge Binding lifecycle

- Amendment status: Accepted
- Amendment date: 2026-08-06
- Audit schema: `plan-revision-audit-v1`
- Question binding schema: `plan-question-knowledge-binding-v1`

Every new revision persists one immutable audit object in the same revision row.
It records `created_reason`, `source_sha256`, parent/result plan hashes,
configuration field diffs, and one operation record per applied operation. An
operation records a bounded actor (`system|user|provider`), source/result question
UUIDs, optional target revision UUID, stable reason code, changed field names,
before/after field hashes, and the Knowledge Binding action/result. It never
stores source text, resume text, question text, focus text, Provider output, or a
raw field value. IDs are UUID-validated, hashes are canonical lowercase SHA-256,
and field/reason names use a restricted metadata alphabet.

`configuration_diff` is present even when empty. The current editor freezes the
configuration snapshot, so edit, move, delete, custom insertion, restore, and
regeneration produce an empty configuration diff. A future operation that is
authorized to change configuration must record per-field before/after hashes; it
must not place the raw configuration value in the audit.

Each V2 question carries a structured binding with status
`valid|unbound|invalidated`, evidence IDs, one content hash per evidence ID, an
optional corpus manifest hash, and a stable reason code. Valid bindings require
all IDs/hashes and a manifest. Unbound/invalidated bindings cannot claim any
evidence. The lifecycle is frozen as follows:

| Operation | Binding action |
|---|---|
| Initial grounded generation | Build and validate against hint/reference hashes and corpus manifest |
| Initial generation without grounded evidence | Explicit `unbound/no_grounded_evidence` |
| Edit question text or focus | Invalidate old evidence as `question_content_changed` |
| Move question | Preserve the exact binding and stable question ID |
| Delete question | Remove only that question/hint; preserve all remaining IDs and bindings |
| Add custom question | Force `unbound/custom_question`; client-supplied grounding is rejected |
| Regenerate one question | Allocate a replacement UUID, record lineage, rebuild and revalidate Provider binding |
| Restore revision | Restore and revalidate the target revision's complete binding snapshot |
| Regenerate all | Revalidate every question binding and preserve the frozen configuration |

Generation converts Provider `q1..qN` identities to opaque UUIDs once and remaps
the corresponding prep hints at the same boundary. The legacy runtime projection
receives a question-binding map keyed by those UUIDs, so runtime resolution no
longer searches for obsolete Provider IDs. An already-bound regeneration result
is rechecked through a temporary positional `q1..qN` projection, then reuses the
existing V2 IDs/hash instead of allocating another set.

Internal revisions and session snapshots retain evidence content hashes and the
corpus manifest needed for deterministic revalidation. Public prep, revision, and
session projections remove `evidence_content_sha256`,
`corpus_manifest_sha256`, `binding_snapshot`, and the internal binding map while
retaining safe binding status/reason/evidence IDs. The revision audit is safe to
return because it contains only validated IDs, hashes, reason codes, and field
names.

## Rollback

Disable V2 write/read routing and return to legacy reads. Do not drop V2 tables,
source tombstones, revisions, or session snapshots. No rollback path re-enables
Provider generation during interview start.

## U0 amendment: immutable interview knowledge scope

- Amendment status: Accepted
- Amendment date: 2026-08-15
- Scope schema: `interview-knowledge-scope-v1`
- Governing decision: `docs/adr/user-materials-rag-v1.md`

`InterviewPlanV2` contains one immutable `knowledge_scope` snapshot. The complete
snapshot participates in the existing canonical `plan_sha256`; Start validates and
copies it to the Session and does not accept a second temporary document list.

An existing serialized Plan without this field deterministically maps to system
knowledge enabled, no selected user documents, and a null compatibility timestamp.
It never derives a historical selection from the current materials library. Internal
selected-document records freeze Document ID, Revision ID, content hash, and allowed
usages. Owner identity remains outside the snapshot and is resolved from the existing
Plan Family/Session Principal boundary. The protected Plan Source and Session Binding
persist that server-side owner separately from the public snapshot. The first Start
revalidates every frozen Revision against that owner before copying the binding; a
successful retry or later replay reads the frozen binding deterministically before
consulting current library state. Start never accepts a client-supplied temporary
Scope or replacement document list.

The governing User Materials ADR owns all remaining lifecycle, retrieval, citation,
deletion, scoring, and capability decisions; this amendment does not duplicate or
weaken its S1-S9 contract.
