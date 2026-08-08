# Adaptive Task-Aware Context Compression Optimization Plan

**Plan revision:** v1.2.1.

**Revision focus:** Preserve the v1.1 task ordering and v1.2 architecture while
closing its remaining execution ambiguities. Define byte-compatible Artifact
identity v0/v1 persistence, correct the Follow-up selectable-budget example,
replace the deduplication boolean with an explicit disabled/shadow/enforce mode,
distinguish authoritative raw storage from deterministically bounded Provider
representations, narrow Interview Semantic Status to fields with declared
authority, persist provider circuits and validation quarantines in one fenced
owner-scoped runtime store, define exact pre-loss measurement equations, route
dynamic target tiers through one resolved request contract, and include every
new test in repository acceptance. The v1.2.1 clarification makes deduplication
shadow measurements non-authoritative for business eligibility, uses integer
cross-multiplication for threshold decisions, and scopes the 11,360/9,088
example to a resolved 12,000-token Follow-up fixture.

**Related baseline:**

- `docs/interview-agent-memory-system-optimization-spec.md`
- `docs/superpowers/plans/2026-07-28-stage-49-model-aware-context-budgeting-and-deterministic-compression.md`
- `docs/superpowers/plans/2026-07-28-stage-50-durable-context-compression-artifacts-and-interview-graph-v2.md`
- `docs/superpowers/plans/2026-07-30-interview-agent-memory-system-optimization.md`

> **Execution note:** This document is an implementation plan, not deployment
> authorization. Do not change production rollout, enable real-provider
> compression consumption, migrate existing checkpoints, or delete existing
> artifacts unless the user separately authorizes execution. Begin each task
> with the stated failing or characterization test. Preserve all unrelated
> worktree changes, especially the existing frontend and browser-test edits.
> Keep budget enforcement, compression consumption, task-aware projection, and
> any new circuit-breaker behavior disabled by committed default.

**Goal:** Evolve the current loss-triggered, query-independent compression
path into a measured, task-aware, tiered context-management system without
weakening source authority, deterministic fallback, artifact durability,
checkpoint compatibility, or scoring provenance. Wire the existing 80%
utilization and exact-recent-question configuration, make compression intent
semantically visible to the compressor, add deterministic deduplication and a
bounded interview status projection, prevent repeated provider failures, and
define evidence-based promotion from disabled to shadow to low-percentage
consumption.

**Architecture:** Raw candidate messages and the original bound Evidence remain
authoritative in storage for business facts, scoring, provenance, replay, and
deletion. Provider-facing working context uses deterministically token-bounded
raw representations and may use a validated non-authoritative Evidence
projection only where an explicit workflow policy allows it; neither a bounded
representation nor a projection replaces the authoritative source record or
becomes scoring provenance. Every business operation first resolves a
model-aware budget, classifies mandatory non-semantically-compressed units,
establishes canonical source identities, performs or shadows exact
deduplication, and measures **pre-loss context demand** before any truncation,
omission, or semantic compression.
Only then may proactive compression become eligible. Completed questions may
produce reusable, non-authoritative Question Memory artifacts. Per-operation
compression receives a versioned `CompressionIntent` containing actual semantic
focus and preservation rules, not only identity digests. The runtime consumes
only validated artifacts and keeps current-question content, the latest
candidate answer, and configured exact-recent questions non-semantically-
compressed and deterministically bounded in Provider context. A small
deterministic **Interview Semantic Status** projection exposes only business
progress and advisory unresolved coverage; runtime control data such as artifact
route or memory-unit count remains telemetry/checkpoint metadata and is not
injected into the LLM. Provider failures use a durable owner-scoped circuit;
repeatable source/intent validation failures use a separately keyed quarantine
path. Both are persisted in a versioned, fenced, owner-scoped failure-
containment store. Full-session LLM compaction remains out of scope until shadow
data proves it is necessary.

**Tech stack:** Python 3.11, Pydantic v2, FastAPI, LangGraph, LangChain
`ChatOpenAI`, PostgreSQL, the existing Context Runtime and Context Artifact
claim/lease/fencing implementation, pytest, and the existing privacy-safe
memory metric store.

---

## 1. Baseline Findings

The plan is grounded in the current repository behavior, not only the desired
architecture.

1. Compression modes are `disabled`, `shadow`, and `consume`; all committed
   consumption defaults are disabled.
2. `QUESTION_CONVERSATION_COMPRESSION_POLICY` and
   `QUESTION_MEMORY_COMPRESSION_POLICY` use bounded structured output and exact
   source excerpts.
3. `ContextCompressionEligibilityPolicy` currently becomes eligible after
   deterministic selection reports dropped or truncated content. It does not
   proactively use operation-budget utilization.
4. `SelectionMemoryConfig.eligibility_utilization_basis_points` defaults to
   `8000`, but no runtime consumer currently reads it.
5. `SelectionMemoryConfig.exact_recent_questions` defaults to `1`, but no
   runtime consumer currently reads it.
6. The compressor prompt receives semantic-focus digests but not the actual
   question focus, consumer operation, phase, or preservation contract.
7. Question Memory retrieval is task-aware at the artifact-ranking level, but
   individual compression is not explicitly task-aware.
8. Evidence and messages have deterministic token caps, but there is no
   explicit canonical deduplication stage before truncation.
9. Provider, validation, or busy failures fall back deterministically, but
   repeated compression failures do not open a durable owner-scoped circuit.
10. The graph persists phase and checkpoint metadata, but no concise,
    deterministic interview-progress projection is injected as a dedicated
    model context unit.
11. Question Memory currently removes summarized raw messages by comparing
    message `content`. Identical text in two different questions can therefore
    cause the wrong raw message to be removed. Projection/subtraction must use a
    canonical source identity, not content equality.
12. Context Artifact identity, owner binding, validation, replay reuse,
    privacy scope, lease, heartbeat, and fencing are already strong and must
    not be replaced.
13. Repository readiness and production observation are separate; production
    compression evidence remains required before any consume promotion.

---

## 2. Objectives and Non-Objectives

### 2.1 Objectives

- Trigger compression before destructive loss when an operation-specific
  utilization threshold is crossed and compressible historical units exist.
- Keep short contexts on the deterministic path with zero compressor calls.
- Pass real semantic intent to the compressor while hashing the same canonical
  intent into Artifact identity.
- Preserve current questions, a configurable number of recent completed
  questions, latest candidate answers, and the authoritative raw Evidence used
  for scoring/provenance. Provider-facing raw units may be deterministically
  token-bounded but are never LLM-summarized while mandatory. Any compressed
  Evidence projection is explicitly non-authoritative and traceable to its raw
  source.
- Deduplicate exact/repeated context before semantic compression.
- Maintain reusable, query-independent Question Memory while making
  conversation compression and retrieval projections task-aware.
- Add a deterministic status projection for authoritative progress and clearly
  labeled advisory unresolved-topic codes.
- Bound compression output dynamically under existing hard policy caps.
- Stop repeated compression calls after a stable failure threshold and recover
  automatically after an explicit scope boundary or cooldown.
- Measure relevance, preservation, fallback, cost, latency, compression ratio,
  and provider estimate error without logging candidate or document content.
- Define repository, shadow, canary, promotion, hold, rollback, and deletion
  criteria.

### 2.2 Explicitly Out of Scope

- Replacing original candidate messages or authoritative raw Evidence records
  used for scoring/provenance. A provider-facing compressed Evidence projection
  is allowed only behind an explicit workflow policy and never changes source
  authority.
- Using compressed summaries as authoritative scoring evidence.
- Enabling compression consumption by committed default.
- Automatically moving existing sessions to a different graph or memory
  policy.
- Recursive summary trees, RAPTOR, cross-session personal memory, or public
  knowledge learning from candidate answers.
- Generic browser/search tool-result compaction; Interview-Agent is not an
  unrestricted tool-loop agent.
- Provider-native Context Editing APIs unless a separately approved provider
  compatibility plan establishes stable support.
- A generic full-session LLM summary. This remains a later decision gate, not
  an implementation task in this plan.
- Frontend redesign. Existing candidate-visible degradation semantics remain
  unchanged unless a later task proves a new notice is necessary.

---

## 3. Target Context Hierarchy

```text
Tier 0 — Authoritative sources and bounded raw Provider representations
  current question source + bounded raw representation
  latest candidate answer source + bounded raw representation
  configured exact recent questions + bounded raw representations
  original bound/scoring Evidence remains authoritative
  immutable plan and rubric constraints
        |
        v
Tier 1 — Deterministic context hygiene
  operation-aware token budget
  complete-turn grouping
  mandatory-raw classification
  canonical source identity
  exact digest/source deduplication
  pre-loss context-demand measurement
  stable source ordering
  bounded head/tail truncation
  low-priority omission
        |
        v
Tier 2 — Reusable archival memory
  one non-authoritative Question Memory artifact per closed question version
  exact source anchors and excerpts
  unresolved-topic metadata
  immutable identity and replay reuse
        |
        v
Tier 3 — Task-aware projection
  current operation and semantic focus
  ranked relevant Question Memory units
  deterministic Interview Status projection
  current raw turn retained
  raw Evidence remains authoritative; provider projection may be compressed
        |
        v
Tier 4 — Failure containment
  deterministic fallback
  owner-scoped compression failure circuit
  no repeated provider spend while the circuit is open
```

The tiers are sequential. Semantic compression cannot bypass deterministic
selection or the rendered-prompt guard.

---

## 4. Fixed Design Decisions

### Decision 1: Keep raw sources authoritative and distinguish Provider representations

Question Memory, conversation summaries, and provider-facing compressed Evidence
remain `non_authoritative`. They may guide follow-up selection and continuity but
cannot replace original candidate messages or raw Evidence, become exact
candidate quotes, become authoritative scoring Evidence, or become final score
provenance. Raw Evidence must remain retrievable by its original identity and
content digest for review/report scoring.

`raw` has two explicit meanings:

- **authoritative raw source:** the complete persisted candidate message or
  Evidence record;
- **bounded raw Provider representation:** source text that has not been
  semantically summarized but may be deterministically head/tail bounded by the
  operation policy.

The plan never promises that an arbitrarily long raw source is sent verbatim to
the Provider. When mandatory bounded-raw representations still exceed the selection
budget, the rendered-prompt guard raises the existing stable budget error or the
business path uses its documented fallback; semantic compression cannot bypass
that guard.

### Decision 2: Separate reusable archival memory from current-task projection

`question_memory` remains query-independent with respect to future questions.
It summarizes one closed question in the semantic context of that source
question. Current-question relevance is applied during retrieval and context
projection. Do not regenerate every historical memory artifact for every new
question.

### Decision 3: Pass semantic intent and hash the same canonical intent

Digests alone are insufficient for model behavior. Compression receives a
bounded, structured intent payload containing actual allowed semantic text.
The canonical JSON representation is hashed into the Artifact identity. A
digest mismatch fails closed.

Proposed contract:

```python
class CompressionIntent(BaseModel):
    schema_version: Literal["compression-intent-v1"]
    consumer_operation: Literal[
        "followup", "question_review", "report", "prep"
    ]
    phase: Literal["prep", "interview", "review", "report"]
    source_focus: str | None
    current_focus: str | None
    preserve: list[Literal[
        "candidate_claims",
        "numbers",
        "identifiers",
        "tradeoffs",
        "failure_boundaries",
        "unresolved_topics",
        "evidence_provenance",
    ]]
    authority: Literal["non_authoritative"]
    prohibited_authority_upgrades: list[Literal[
        "candidate_exact_quote",
        "authoritative_scoring_evidence",
        "new_fact",
        "identity_inference",
    ]]
```

Intent fields must be bounded, normalized, unique where order is not semantic,
and sourced from trusted plan or workflow state. Candidate text must not be
copied into `current_focus`. `prohibited_authority_upgrades` constrains
downstream use of summaries; it does not forbid the compressor from returning
exact supporting excerpts required by validation.

### Decision 4: Use pre-loss demand over the operation budget for the adaptive threshold

The utilization denominator is the resolved operation selection budget after
fixed prompt reserve. When resolved Follow-up `available_input_tokens` reaches
the operation's 12,000-token cap, subtracting the 640-token fixed prompt reserve
yields 11,360 selectable content tokens; 80% is therefore 9,088 tokens. This is
a fixed test/example fixture, not a promise that every model resolves to 12,000
available input tokens. If model availability is lower, the denominator is
`resolved available_input_tokens - fixed_prompt_reserve_tokens`.

The business numerator is **not** the already-selected token count and is
mode-aware. It is deterministic pre-loss required context demand measured after
mandatory classification but before head/tail truncation, drop, omission, or
semantic compression. Enforced exact deduplication may reduce the business
numerator; hypothetical shadow deduplication may not. The advertised 128K model
window is irrelevant except through the resolved operation budget.

Required internal measurements are at least:

```text
source_demand_tokens
duplicate_removed_tokens
post_dedup_demand_tokens
mandatory_bounded_raw_tokens
compressible_history_tokens
pre_dedup_required_tokens
post_dedup_required_tokens
business_pre_loss_required_tokens
shadow_post_dedup_required_tokens
selectable_content_tokens
business_utilization_basis_points
shadow_post_dedup_utilization_basis_points
```

Required equations and estimator path:

```text
source_demand_tokens
  = pre_dedup_required_tokens

pre_dedup_required_tokens
  = estimate_messages(
      pre-dedup mandatory bounded-raw units
      + pre-dedup compressible historical units
      + pre-dedup eligible exact Evidence units
    )

post_dedup_required_tokens
  = estimate_messages(identity-deduplicated form of the same units)

duplicate_removed_tokens
  = max(0, pre_dedup_required_tokens - post_dedup_required_tokens)

post_dedup_demand_tokens
  = post_dedup_required_tokens

business_pre_loss_required_tokens
  = pre_dedup_required_tokens
      when exact_deduplication_mode is disabled or shadow
  = post_dedup_required_tokens
      when exact_deduplication_mode is enforce

shadow_post_dedup_required_tokens
  = post_dedup_required_tokens
      when exact_deduplication_mode is shadow
  = absent otherwise

business_utilization_basis_points
  = round(
      business_pre_loss_required_tokens
      * 10_000
      / selectable_content_tokens
    )

shadow_post_dedup_utilization_basis_points
  = round(
      shadow_post_dedup_required_tokens
      * 10_000
      / selectable_content_tokens
    )
    when shadow_post_dedup_required_tokens is present

business_eligible_at_threshold
  = (
      business_pre_loss_required_tokens * 10_000
      >= selectable_content_tokens * eligibility_utilization_basis_points
    )
```

`estimate_messages` must use the same model/tokenizer resolution and message
framing path used by final Provider-input measurement. The implementation must
not approximate pre-loss demand by summing plain-text token counts.
`post_dedup_required_tokens`, `post_dedup_demand_tokens`, and
`duplicate_removed_tokens` are absent when deduplication is `disabled` and are
computed only in `shadow` or `enforce`. Absence must not be serialized as a
business deduplication result.
`business_utilization_basis_points` and
`shadow_post_dedup_utilization_basis_points` are telemetry/display values only;
rounded basis points must never decide eligibility. The cross-multiplication
predicate is the normative threshold contract.

In `enforce`, this ordering prevents duplicate-heavy inputs from triggering
unnecessary compressor calls. In `shadow`, it records the same counterfactual
without changing eligibility, target resolution, source segments, Artifact
identity, compressor input, deterministic selection, or final business-provider
input. In every mode, the business 80% signal is observed before destructive
loss.

### Decision 5: Threshold eligibility still requires compressible history

Crossing 80% is not sufficient by itself. The selector must identify at least
one complete historical unit outside the mandatory bounded-raw set. Current-
question content, the latest candidate answer, and configured exact recent
questions cannot become semantic-compression sources. They may still be
deterministically token-bounded under the operation policy. If the mandatory
bounded-raw set cannot satisfy the mandatory floor, the existing stable budget
failure/fallback path applies.

### Decision 6: Canonical source identity and mode-aware exact deduplication precede utilization and truncation

Define a stable source identity before deduplication or Question Memory
subtraction. For conversation messages it must distinguish at least
`question_id`, authoritative `sequence_no`, `role`, and `content_sha256` within
the session/owner scope. For Evidence it must use the existing authoritative
Evidence/chunk identity plus content digest where available.

Start with deterministic digest/source-manifest deduplication. Do not introduce
embedding- or LLM-based semantic deletion in the first release. Exact
candidate text repeated in two different questions or sequence positions is not
implicitly the same source unit and must not be deleted merely because the text
matches. Deduplication is explainable, replay-stable, and runs before utilization
is calculated and before truncation.

### Decision 7: Dynamic output sizing uses deterministic discrete tiers through one resolved request

The hard maximum stays 2,000 tokens for question conversation and Question
Memory unless a policy version explicitly changes. A deterministic allocator
computes a required target from source size, configured ratio, floor, and
remaining business budget, then rounds it to an allowed target tier such as
`256 / 512 / 1024 / 1536 / 2000`. Identity includes the resolved tier.

Do not create near-arbitrary targets such as 783, 791, and 812 tokens: they
fragment Artifact identity with little quality benefit and reduce replay reuse.

One immutable request contract is authoritative for the resolved target:

```python
@dataclass(frozen=True)
class ResolvedCompressionRequest:
    policy: ContextCompressionPolicy
    intent: CompressionIntent
    source_segments: tuple[CompressionSourceSegment, ...]
    resolved_target_output_tokens: int
```

Artifact identity, compressor prompt, Provider `max_tokens`, output validation,
and prompt measurement must all read `resolved_target_output_tokens` from this
request. No component may silently fall back to the policy hard cap after a
lower tier is resolved.

### Decision 8: Separate Interview Semantic Status from Context Runtime Status

The LLM-facing **Interview Semantic Status** is code-maintained and contains only
business-semantic state with a declared source and authority: authoritative
progress counters from session/review records, controlled active-focus tags
from the plan, and optional `advisory_unresolved_topic_codes` from validated
non-authoritative Question Memory. Do not label Provider-generated memory as
`verified`, do not ask an LLM to infer counters or completion state, and do not
inject free-form candidate evaluation.

`memory_route`, Artifact refs, memory-unit counts, dedup counts, exact-recent
counts, circuit state, and similar control-plane data belong to **Context Runtime
Status** in checkpoints/metrics/traces. They are not injected into the Examiner
or Reviewer prompt merely because they exist.

### Decision 9: Provider circuit and validation quarantine share one fenced store but separate scopes

An in-process retry counter is insufficient. Provider/network failures and
repeatable validation failures have different causes and must not share one
undifferentiated streak.

- **Owner provider circuit** key: privacy scope + owner type + owner-key digest +
  provider + model + artifact class + policy version. It covers timeout,
  connection, and provider-availability failures and bounds per-owner spend.
- **Validation quarantine** key: privacy scope + owner type + owner-key digest +
  artifact class + source manifest + compression intent + prompt contract +
  output schema + policy version + provider + model. It covers repeatable
  invalid-schema/grounding failures for one semantic input without affecting
  another owner or source.

Persist both kinds in a dedicated, versioned
`{prefix}_context_compression_failure_states` table with an in-memory test/local
implementation. Rows contain a hashed canonical state key, kind, permitted scope
digests and versions, consecutive failure count, open-until timestamp,
half-open-probe ownership, fencing version, and timestamps. Atomic compare-and-
set/fencing operations own increment, open, half-open claim, reset, and expiry.
Session deletion removes matching owner-scoped rows; retention removes expired
unreferenced rows. Checkpoints may carry only a bounded state digest/status for
diagnostics, not the authoritative counter. Existing checkpoints therefore need
no circuit-state migration.

This plan does not introduce a deployment-global provider health circuit. That
is infrastructure-level load-shedding work and requires a separate capacity
plan. Busy contention, parent lease loss, identity conflicts, privacy/scope
faults, and cancellation do not increment either owner-scoped state. No raw
error text is persisted.

### Decision 10: Do not add full-session compaction without evidence

Operation caps, deterministic selection, Question Memory, and per-question
review microbatches already bound the primary domain flows. A full-session LLM
summary would add compounded-loss and scoring-provenance risk. Reconsider it
only if shadow evidence shows unresolved overflow or continuity failures after
this plan is complete.

### Decision 11: Raw-message subtraction is identity-based, never content-based

When a Question Memory artifact represents historical source messages, remove or
replace only the exact source units referenced by canonical source identity. Do
not subtract deterministic context by comparing `message.content`. Repeated text
such as `"I don't know."` in two different questions must remain distinguishable.

### Decision 12: Artifact identity v0 remains byte-compatible; v1 persists intent explicitly

Existing completed Artifacts keep the exact current v0 canonical payload and
hash algorithm. Loading a v0 row must not add an `intent-none-v0` field and
recompute its key. New intent-aware Artifacts use `identity-v1`, whose canonical
payload includes `identity_schema_version="identity-v1"` and
`compression_intent_sha256`.

Because `PostgresContextArtifactStore` reconstructs identity material from
stored columns before validating `artifact_key`, the Stage migration adds
nullable `identity_schema_version` and `compression_intent_sha256` columns.
Existing rows remain null and select the byte-identical v0 serializer. New v1
rows require both values. In-memory and PostgreSQL stores, joined-ref loaders,
schema contracts, migrations, and full-identity validation use the same
versioned serializer. Completed v0 rows are never rewritten as v1.

---

## 5. Configuration Contract

Reuse the existing immutable `memory-runtime-config-v1` model. Do not add a
second configuration loader.

### Existing fields to wire

```text
memory.selection.exact_recent_questions = 1
memory.selection.max_memory_units = 4
memory.selection.max_memory_tokens = 2500
memory.selection.eligibility_utilization_basis_points = 8000
```

### Proposed new fields

```text
memory.compression.task_intent_enabled = false
memory.compression.status_projection_enabled = false
memory.compression.provider_circuit_threshold = 3
memory.compression.provider_circuit_cooldown_seconds = 300
memory.compression.validation_quarantine_threshold = 2
memory.compression.validation_quarantine_cooldown_seconds = 3600
memory.compression.failure_state_lease_seconds = 60
memory.selection.exact_deduplication_mode = disabled
memory.selection.dynamic_target_floor_tokens = 256
memory.selection.dynamic_target_source_ratio_basis_points = 2500
memory.selection.dynamic_target_allowed_tokens = [256, 512, 1024, 1536, 2000]
```

Rules:

1. New feature booleans default `false`.
   `exact_deduplication_mode` is the explicit enum
   `disabled | shadow | enforce` and defaults `disabled`.
   - `disabled`: do not compute a deduplicated business candidate.
   - `shadow`: compute identity-safe hypothetical deduplication and metrics, but
     keep business eligibility, target, source segments, Artifact identity,
     compressor input, selection, and final business-provider input equivalent
     to the non-deduplicated path.
   - `enforce`: use the deduplicated candidate in pre-loss eligibility,
     deterministic selection, and business-provider input.
   Promotion to `enforce` requires accepted shadow equivalence and identity-safe
   removal evidence.
2. Task intent cannot be consumed unless compression mode is `consume`, the
   workflow gate is enabled, and operation budget enforcement is active.
3. Compression shadow mode may compute intent, eligibility, dynamic targets,
   status projections, and artifacts but must not change business-provider
   input. Deduplication shadow/enforcement is controlled independently by
   `exact_deduplication_mode`.
4. Provider-circuit and validation-quarantine thresholds/cooldowns are separate,
   positive, and bounded. The failure-state lease is shorter than both
   cooldowns and follows the runtime heartbeat/fencing contract.
5. Dynamic target tiers must be strictly increasing, contain no duplicates,
   include the configured
   floor, and never exceed the policy hard cap.
6. Legacy environment aliases are optional. If introduced, conflicting
   normalized old/new values fail preflight.
7. Readiness returns only modes, versions, numeric limits, and booleans. It
   never returns focus text, candidate content, artifact refs, or source IDs.

---

## 6. Task Dependency Graph

```text
Task 0  Baseline, characterization, and requirement update
  |
  +--> Task 1  Wire existing selection configuration
  |      |
  |      +--> Task 5  Canonical source identity + deterministic deduplication
  |              |
  |              +--> Task 6  Exact-recent preservation + identity-safe memory projection
  |                      |
  |                      +--> Task 4  Pre-loss adaptive eligibility + tiered targets
  |                      +--> Task 7  Interview Semantic Status projection
  |
  +--> Task 2  CompressionIntent contract and identity
         |
         +--> Task 3  Task-aware compressor prompts and validation

Tasks 3 + 4 + 6 + 7
  +--> Task 8  Durable provider circuit + validation quarantine
  +--> Task 9  Privacy-safe observability and evaluation dataset

Tasks 1 through 9
  +--> Task 10 Repository acceptance and recovery matrix
        |
        +--> READY_FOR_SHADOW
              |
              +--> Task 11 Deployed shadow observation (separate authorization)
                    |
                    +--> Task 12 Low-percentage consume canary (separate authorization)
```

Tasks may run in parallel only where the graph permits and only when file edits
do not overlap. The existing dirty frontend files are outside this plan.

---

## 7. Implementation Tasks

## Task 0: Pin requirements and characterize the current path

**Requirements:** `MEM-CTX-PLAN-001` and the complete v1.2.1 requirement set
introduced by this task.

**Purpose:** Prevent the optimization from silently changing current short-
context behavior or contradicting the existing memory specification.

**Modify:**

- `docs/interview-agent-memory-system-optimization-spec.md`
- `docs/superpowers/plans/2026-08-07-adaptive-task-aware-context-compression-optimization.md`

**Add or modify tests:**

- `tests/test_context_compression_eligibility.py`
- `tests/test_context_selection.py`
- `tests/test_question_memory.py`
- `tests/test_durable_interview_graph.py`

**Steps:**

1. Add every `MEM-CTX-*` ID referenced by Tasks 0-12 to the pinned Spec with one
   normative statement and one verification mapping per ID. Add an automated
   plan/spec check that fails when an ID is missing, duplicated, or unreferenced.
2. Characterize current provider input for short, medium, and lossy contexts.
3. Characterize current compression-call count for disabled, shadow, and
   consume gates.
4. Characterize v1 and existing v2 checkpoint recovery.
5. Record the current dead-config finding for
   `exact_recent_questions` and
   `eligibility_utilization_basis_points`.

**Acceptance:**

- Existing short-context provider input is pinned.
- Every new plan task references at least one requirement ID, and the automated
  plan/spec check passes.
- No production source behavior changes in this task.

---

## Task 1: Wire existing selection configuration end to end

**Requirements:** `MEM-CTX-CFG-001`, `MEM-CTX-CFG-002`.

**Purpose:** Make the already-declared selection policy authoritative instead
of leaving it as documentation-only configuration.

**Modify:**

- `app/services/memory_config.py`
- `app/services/runtime.py`
- `app/services/question_memory.py`
- `app/services/context_compression_eligibility.py`

**Tests:**

- `tests/test_memory_config.py`
- `tests/test_question_memory.py`
- `tests/test_context_compression_eligibility.py`
- `tests/test_agent_runtime_composition.py`

**Steps:**

1. Inject `max_memory_units` and `max_memory_tokens` exclusively from
   `EffectiveMemoryConfig.selection`; remove duplicated constructor defaults
   from runtime composition where safe.
2. Pass `exact_recent_questions` to deterministic selection and Question
   Memory coordination.
3. Pass `eligibility_utilization_basis_points` to the eligibility policy.
4. Validate all values during effective-config loading.
5. Add the v1.2.1 configuration fields from Section 5, including the
   deduplication mode enum, separate provider/quarantine thresholds, failure-
   state lease, and discrete target tiers. Do not add legacy aliases unless an
   approved deployment requires them.
6. Add readiness fields for the numeric/mode policy without exposing content.

**Acceptance:**

- Changing each field changes exactly one intended behavior in tests.
- Invalid or conflicting values fail before serving traffic.
- Existing defaults preserve current behavior until later tasks explicitly
  consume the newly wired fields.
- Invalid deduplication modes, duplicate/unsorted target tiers, and lease/
  cooldown combinations fail preflight.

---

## Task 2: Add a versioned CompressionIntent contract

**Requirements:** `MEM-CTX-INTENT-001`, `MEM-CTX-ID-001`,
`MEM-CTX-ID-002`.

**Purpose:** Give the compressor real semantic task information and make it
part of immutable Artifact identity.

**Create:**

- `app/services/context_compression_intent.py`

**Modify:**

- `app/services/context_artifacts.py`
- `app/services/interview_context_artifacts.py`
- `app/services/evidence_context_artifacts.py`
- `app/services/question_memory.py`
- `app/services/context_compression.py`

**Tests:**

- `tests/test_context_artifact_contracts.py`
- `tests/test_context_compressor.py`
- `tests/test_interview_context_artifacts.py`
- `tests/test_evidence_context_artifacts.py`
- `tests/test_question_memory.py`

**Steps:**

1. Implement bounded Pydantic models for intent and preservation fields.
2. Normalize whitespace, list ordering, enum ordering, and Unicode before
   canonical serialization.
3. Compute `compression_intent_sha256` from canonical JSON.
4. Split canonical identity serialization into byte-compatible
   `identity-v0` and intent-aware `identity-v1`. V0 serializes exactly the
   current dataclass fields and does not inject a compatibility sentinel.
5. Add nullable `identity_schema_version` and
   `compression_intent_sha256` columns through the migration owner. Existing
   rows remain null and load through v0; new intent-aware rows require
   `identity-v1` plus a valid intent digest.
6. Update PostgreSQL insert/select/join reconstruction, in-memory storage,
   required-column contracts, full identity checks, and artifact-key validation
   to dispatch by identity version.
7. Build intent only from trusted plan/workflow metadata. Do not place resume,
   JD, candidate answer, or Evidence body text into intent metadata.
8. Never reinterpret, re-key, or rewrite a completed v0 Artifact as v1.

**Acceptance:**

- Same semantic intent produces the same digest.
- Different focus or preservation rules produce a different Artifact key.
- Intent digest mismatch fails closed.
- Every pre-existing v0 fixture retains its exact Artifact key after the v1
  code and migration are installed.
- A PostgreSQL v1 row reload reconstructs the exact same full identity material
  and key.
- No raw intent content is emitted to telemetry or checkpoints.

---

## Task 3: Make compressor prompts task-aware without weakening grounding

**Requirements:** `MEM-CTX-INTENT-002`, `MEM-CTX-AUTH-001`.

**Purpose:** Improve information selection during compression while preserving
the existing exact-source validation contract.

**Modify:**

- `app/services/context_compression.py`
- `app/services/context_compression_validation.py`
- `app/agents/context_compressor.py`

**Tests:**

- `tests/test_context_compressor.py`
- `tests/test_context_compression_validation.py`
- `tests/test_context_compression_runner.py`

**Steps:**

1. Add canonical intent JSON to the compressor prompt; do not pass only its
   digest.
2. For Question Memory, pass the closed source question's focus and require
   preservation of candidate claims, numbers, identifiers, trade-offs,
   failure boundaries, and unresolved topics.
3. For conversation compression, pass the current question focus and consumer
   operation so older context can be selected for the active follow-up.
4. For Evidence compression, pass the allowed semantic purpose while retaining
   the rule that summaries cannot become exact scoring Evidence.
5. Add explicit instructions that preservation priorities do not authorize new
   facts or inferred candidate ability.
6. Express exact-quote and scoring restrictions as downstream authority
   prohibitions; retain exact continuous supporting excerpts required by the
   existing validation contract.
7. Update prompt-contract versions. Old completed Artifacts remain immutable
   and are not overwritten.
8. Add adversarial fake-provider fixtures that omit a number, change an
   identifier, invent a conclusion, or cite a non-source excerpt.

**Acceptance:**

- Task-aware summaries retain focus-relevant grounded units in golden tests.
- Fabricated or altered facts remain rejected.
- Current question and candidate answer remain authoritative in storage and use
  non-semantically-compressed, deterministically bounded Provider
  representations.
- Any compressed Evidence consumed by a provider is explicitly
  non-authoritative and traceable to raw Evidence that remains available for
  scoring/provenance.

---

## Task 4: Add pre-loss adaptive eligibility and tiered dynamic target allocation

**Requirements:** `MEM-CTX-ELIG-001`, `MEM-CTX-TARGET-001`,
`MEM-CTX-BUD-001`.

**Depends on:** Tasks 1, 5, and 6.

**Purpose:** Start compression before destructive loss while avoiding eager
compression of short or duplicate-heavy contexts.

**Modify:**

- `app/services/context_compression_eligibility.py`
- `app/services/context_budget.py`
- `app/services/context_selection.py`
- `app/services/interview_context_artifacts.py`
- `app/services/evidence_context_artifacts.py`

**Tests:**

- `tests/test_context_budget.py`
- `tests/test_context_selection.py`
- `tests/test_context_compression_eligibility.py`
- `tests/test_interview_context_artifacts.py`

**Steps:**

1. Add stable reason `approaching_operation_budget`.
2. Extend deterministic selection measurements with
   `source_demand_tokens`, `duplicate_removed_tokens`,
   `post_dedup_demand_tokens`, `mandatory_bounded_raw_tokens`,
   `compressible_history_tokens`, `pre_dedup_required_tokens`,
   `post_dedup_required_tokens`, `business_pre_loss_required_tokens`, and the
   optional `shadow_post_dedup_required_tokens`.
3. Compute utilization with the Decision 4 equations and the same
   `estimate_messages` framing path used by final Provider-input measurement.
   With deduplication `disabled` or `shadow`, business eligibility uses
   pre-dedup demand. Only `enforce` may use post-dedup demand for the business
   numerator. Shadow post-dedup demand is counterfactual telemetry only.
4. Decide the configured utilization threshold with integer
   cross-multiplication:
   `business_pre_loss_required_tokens * 10_000 >=
   selectable_content_tokens * eligibility_utilization_basis_points`.
   Rounded basis points are telemetry only. Also require at least one
   non-mandatory complete historical unit.
5. Preserve existing drop/truncate reasons as stronger compatibility reasons;
   proactive eligibility must not depend on first observing those losses.
6. Resolve a required target from source tokens, configured ratio, target
   floor, policy hard cap, and final operation budget; round it
   deterministically to `dynamic_target_allowed_tokens`.
7. Build one `ResolvedCompressionRequest` and use its target tier for Artifact
   identity, compressor prompt, Provider `max_tokens`, validation, and prompt
   measurement.
8. Compression shadow records a hypothetical compression route and target but
   leaves business-provider input unchanged. Deduplication shadow independently
   records a hypothetical post-dedup demand/utilization comparison and cannot
   change the business compression route or target.

**Acceptance:**

- In a fixture where resolved Follow-up `available_input_tokens = 12,000`, the
  640-token reserve yields 11,360 selectable tokens. At an 8,000-basis-point
  threshold, 9,087 remains below threshold and makes zero proactive compressor
  calls; 9,088 is exactly eligible when compressible history exists.
- A smaller-window fixture uses its resolved availability rather than the
  12,000 operation cap; for example, 9,000 available minus the 640 reserve
  yields an 8,360-token denominator and a 6,688-token 80% boundary.
- A non-divisible boundary fixture proves rounded telemetry cannot promote an
  ineligible request: 8,003 of 10,004 selectable tokens may display as 8,000
  basis points after rounding, but remains below threshold by integer
  cross-multiplication.
- 80% pre-loss utilization with compressible historical turns is eligible even
  though no message has yet been dropped or truncated.
- With deduplication `enforce`, duplicate-heavy input that falls below 80% after
  identity-safe exact deduplication is not proactively eligible.
- With deduplication `shadow`, the same hypothetical post-dedup result is
  recorded but cannot suppress or trigger business compression eligibility,
  change the resolved target, or alter either Provider input.
- 80% utilization containing only mandatory bounded-raw content is not
  proactively eligible for semantic compression.
- Drop/truncate compatibility eligibility remains deterministic.
- Dynamic targets are always one configured allowed tier, never exceed policy
   hard caps, and never fall below the configured floor when compression is
   attempted.
- Prompt, Provider binding, validation, and Artifact identity use the same
  resolved target tier in every test.

---

## Task 5: Add canonical source identity and deterministic exact deduplication

**Requirements:** `MEM-CTX-SOURCE-001`, `MEM-CTX-DEDUP-001`,
`MEM-CTX-DEDUP-002`.

**Purpose:** Establish identity-safe context units, measure true duplicates in
`shadow`, and remove them before business utilization/truncation only in
`enforce`, without paying for semantic summary or risking false-positive
deletion.

**Create or modify:**

- `app/services/context_source_identity.py`
- `app/services/context_selection.py`
- `app/services/evidence_context_artifacts.py`
- `app/services/question_memory.py`

**Tests:**

- `tests/test_context_source_identity.py`
- `tests/test_context_selection.py`
- `tests/test_evidence_context_artifacts.py`
- `tests/test_question_memory.py`

**Steps:**

1. Define a canonical conversation source identity containing at least
   `question_id`, a versioned authoritative or deterministically reconstructed
   `sequence_no`, `role`, and `content_sha256` within owner/session scope. Do
   not use content text as identity.
2. Deduplicate Evidence by authoritative source/chunk identity and content
   digest; preserve separate Evidence units when provenance differs.
3. Deduplicate historical message representations only when canonical source
   identity or an explicitly equivalent replay identity proves they represent
   the same source unit. If duplicate representations cross mandatory and
   non-mandatory classes, the mandatory bounded-raw representation wins.
4. Keep the newest representation only when ordering/provenance semantics are
   explicitly equivalent and doing so cannot replace a mandatory bounded-raw
   unit.
5. Never deduplicate two candidate statements solely because normalized or raw
   text matches. The same sentence in different questions or sequence numbers
   remains distinct.
6. Publish only aggregate duplicate counts.
7. In `shadow` mode, compute canonical identities, duplicate candidates,
   `duplicate_removed_tokens`, and hypothetical post-dedup pre-loss demand, but
   do not use them for business eligibility, target resolution, source
   segments, Artifact identity, compressor input, deterministic selection, or
   final Provider input. In `enforce` mode, use the deduplicated candidate
   before business utilization and selection.
8. Record business and hypothetical shadow measurements separately so telemetry
   never implies that counterfactual duplicate removal changed the consumed
   route.

**Acceptance:**

- In `enforce`, duplicate representations of the same Evidence source consume
  business budget once. In `shadow`, the hypothetical candidate counts them
  once while the business path remains unchanged.
- Identical candidate text from different questions/sequence numbers remains
  distinct unless source identity proves it is a replay duplicate.
- Similar but non-identical candidate claims both remain.
- Replay produces identical ordering and digests.
- Shadow comparison proves both Provider inputs are unchanged with or without
  duplicates; enforce mode removes only identity-proven duplicate
  representations.
- When compression is `consume` and deduplication is `shadow`, business
  eligibility and the resolved compression request remain byte-/identity-
  equivalent to the non-deduplicated path; only shadow metrics differ.

---

## Task 6: Preserve exact recent questions and make Question Memory projection identity-safe

**Requirements:** `MEM-CTX-RAW-001`, `MEM-CTX-MEMORY-001`.

**Purpose:** Turn the configured exact-recent policy into a hard preservation
rule, fix content-equality subtraction, and improve current-question relevance
without repeatedly recompressing raw history.

**Modify:**

- `app/services/context_selection.py`
- `app/services/question_memory.py`
- `app/services/question_memory_retrieval.py`
- `app/graphs/durable_interview_graph.py`

**Tests:**

- `tests/test_context_selection.py`
- `tests/test_question_memory_retrieval.py`
- `tests/test_question_memory.py`
- `tests/test_durable_interview_graph.py`

**Steps:**

1. Mark the configured number of most recently completed questions as mandatory
   non-semantically-compressed units before memory selection or proactive
   eligibility. Their Provider representations remain subject to deterministic
   per-message and operation token bounds.
2. Exclude those units from semantic-compression sources unless a later,
   separately versioned emergency policy explicitly authorizes it. Define the
   stable budget failure/business fallback used when mandatory bounded-raw
   representations cannot satisfy the mandatory floor.
3. Replace Question Memory subtraction based on `message.content` equality with
   subtraction by canonical source identity from Task 5.
4. Add a regression where identical candidate text appears in two different
   questions; summarizing the older question must not remove the newer raw
   message.
5. Rank older Question Memory using current focus tags, skill tags, unresolved
   topic codes, recency, and source completeness.
6. Rank individual claims/unresolved units deterministically where schema
   metadata permits; do not ask a second LLM to summarize summaries.
7. Enforce `max_memory_units` and `max_memory_tokens` after ranking.
8. Preserve stable original ordering in final projected context.

**Acceptance:**

- The newest configured completed questions remain authoritative in storage and
  non-semantically-compressed in Provider context; any deterministic bounding is
  visible in selection stats.
- Oversized mandatory sources cannot bypass the rendered-prompt guard.
- Repeated identical text in different source units is not accidentally removed.
- A Question Memory artifact replaces only the exact source identities it
  represents.
- Older relevant Question Memory outranks irrelevant recent memory only under
  an explicit, tested scoring rule.
- The same state and configuration produce the same projection.
- No summary-of-summary provider call is introduced.

---

## Task 7: Add a deterministic Interview Semantic Status projection

**Requirements:** `MEM-CTX-STATUS-001`, `MEM-CTX-AUTH-002`.

**Purpose:** Give Examiner and Review consumers a compact, reliable
business-semantic status unit instead of forcing them to infer progress
repeatedly from long history, while keeping runtime-control metadata out of the
prompt.

**Create:**

- `app/services/interview_status_projection.py`

**Modify:**

- `app/graphs/durable_interview_graph.py`
- `app/graphs/durable_interview_state_v2.py`
- `app/services/question_memory.py`

**Tests:**

- `tests/test_durable_interview_state.py`
- `tests/test_durable_interview_graph.py`
- `tests/test_question_memory.py`
- `tests/test_interview_status_projection.py`

**Projection fields:**

```text
schema_version
plan_question_count
current_question_index
completed_question_count
reviewed_question_count
active_focus_tags
advisory_unresolved_topic_codes
```

**Steps:**

1. Derive progress counters only from authoritative session and completed
   review records. Derive active focus tags only from the immutable plan.
2. Optional `advisory_unresolved_topic_codes` may come only from a validated
   non-authoritative Question Memory Artifact and must remain explicitly
   advisory in the rendered schema. Do not emit `verified_coverage_codes` or
   `required_next_check_codes` until a separate versioned producer/provenance
   contract exists.
3. Use controlled taxonomy codes, not free-form candidate evaluation.
4. Keep `memory_route`, `memory_unit_count`, exact-recent counts, Artifact refs,
   circuit/quarantine state, dedup counts, and similar runtime-control fields in
   metrics/checkpoint metadata only; do not render them into the semantic
   status message.
5. Render one bounded structured semantic context message with a stable role
   and schema.
6. Place it after the stable system/tool prefix and before variable historical
   summaries according to the existing provider prompt contract.
7. In shadow mode, render and measure it without changing provider input.
8. Keep state compatibility defaults for existing checkpoints.

**Acceptance:**

- Projection counters/codes match authoritative records.
- Advisory codes are labeled advisory and never reported as verified coverage.
- No candidate answer, score rationale, session ID, artifact ref, memory route,
  memory-unit count, or circuit metadata appears.
- Projection size is bounded and deterministic.
- Disabling the gate restores byte-equivalent prior provider input.

---

## Task 8: Add durable provider circuit and validation quarantine

**Requirements:** `MEM-CTX-FAIL-001`, `MEM-CTX-FAIL-002`,
`MEM-CTX-PRIV-001`.

**Purpose:** Prevent repeated provider spend and latency while keeping the
business operation available, without allowing one deterministically difficult
source to disable compression for unrelated sources.

**Modify or create:**

- `app/services/context_compression_failure_containment.py`
- `app/services/context_compression_failure_store.py`
- `app/services/in_memory_context_compression_failure_store.py`
- `app/services/postgres_runtime_migrations.py`
- `app/services/postgres_schema_contract.py`
- `app/services/session_deletion_worker.py`
- `app/services/runtime.py`
- `app/graphs/durable_interview_state_v2.py`
- `app/graphs/durable_interview_graph.py`
- `app/services/context_compression_runner.py`
- This task integrates Interview v2 only. Review keeps its existing deterministic
  fallback; a Review failure-containment integration requires a separate plan
  if it cannot reuse this store without changing Review Effect ownership.

**Tests:**

- `tests/test_context_compression_failure_containment.py`
- `tests/test_context_compression_failure_store_postgres.py`
- `tests/test_context_compression_runner.py`
- `tests/test_durable_interview_state.py`
- `tests/test_durable_interview_graph.py`
- `tests/test_question_memory_recovery.py`
- `tests/test_session_deletion_worker.py`

**Steps:**

1. Maintain two independent failure-containment states:
   - owner provider circuit keyed by privacy scope + owner type + owner-key
     digest + provider + model + artifact class + policy version;
   - validation quarantine keyed by privacy scope + owner type + owner-key
     digest + artifact class + source manifest + intent digest + prompt
     contract + output schema + policy version + provider + model.
2. Provider circuit counts only stable timeout/connection/provider-availability
   failures. Validation quarantine counts repeatable schema/grounding failures
   for the same source/intent identity.
3. Do not count busy contention, parent lease loss, identity conflict,
   privacy/scope failure, cancellation, or stale ownership as either streak.
4. Persist both kinds in the dedicated failure-state store defined by Decision
   9. Add migration-owned schema, in-memory parity, required-column validation,
   owner-scoped deletion, and expired-row retention.
5. Use atomic state-version/fencing predicates for increment, open, half-open
   probe claim, reset, and expiry. Only one worker may own a half-open probe.
6. Open each state after its separately configured consecutive threshold.
7. While a matching state is open, issue zero matching compressor calls and use
   deterministic context. A quarantined source must not block unrelated source
   identities.
8. Close after cooldown plus one half-open probe, a versioned policy/prompt
   change, or the documented owner/session boundary as applicable.
9. Persist only stable codes, digests already permitted by runtime contracts,
   counts, timestamps, and state version; never raw provider/validation text.
10. Checkpoint replay and worker replacement query the authoritative store and
    preserve both behaviors; checkpoints do not own the counters.

**Acceptance:**

- Three configured consecutive provider failures result in no fourth matching
  immediate provider call.
- Repeatable validation failure quarantines only the matching
  source/intent/prompt/policy identity.
- The same source text in a different owner/privacy scope is not quarantined.
- A successful half-open probe resets the relevant streak.
- Concurrent half-open probes result in at most one Provider call.
- Session deletion removes its owner-scoped failure states; retention never
  removes a live half-open lease.
- Provider success followed by business-operation failure does not corrupt the
  completed Artifact.
- Identity, privacy, and ownership violations remain fail-closed and are not
  hidden by either mechanism.

---

## Task 9: Add privacy-safe compression quality and cost observability

**Requirements:** `MEM-CTX-OBS-001`, `MEM-CTX-EVAL-001`.

**Purpose:** Make promotion depend on evidence rather than repository tests
alone.

**Modify:**

- `app/services/memory_metrics.py`
- `app/services/provider_usage.py`
- `app/services/context_compression_eligibility.py`
- `app/services/context_compression_runner.py`
- `app/services/question_memory.py`

**Add:**

- `tests/golden/context_compression_task_aware_v1.json`
- `scripts/context_compression_shadow_acceptance.py`
- `tests/test_context_compression_shadow_acceptance.py`

**Required aggregate metrics:**

```text
operation
workflow
policy_version
intent_schema_version
eligibility_reason
route
source_token_bucket
target_token_bucket
result_token_bucket
compression_ratio_bucket
estimated_input_tokens
provider_input_tokens_when_available
estimator_error_basis_points
source_demand_token_bucket
duplicate_removed_token_bucket
post_dedup_demand_token_bucket
mandatory_bounded_raw_token_bucket
pre_dedup_required_token_bucket
post_dedup_required_token_bucket
business_pre_loss_required_token_bucket
shadow_post_dedup_required_token_bucket
business_utilization_basis_points
shadow_post_dedup_utilization_basis_points
selected_unit_count
dropped_unit_count
truncated_unit_count
deduplicated_unit_count
exact_recent_preserved
current_answer_preserved
validation_outcome
fallback_outcome
provider_circuit_state
validation_quarantine_state
failure_state_store_outcome
latency_bucket
```

**Quality dataset cases:**

- Chinese, English, and mixed-language answers.
- Numbers, dates, percentages, code identifiers, and product names.
- Repeated Evidence and near-duplicate non-identical Evidence.
- Identical candidate text repeated in different question/sequence identities.
- Relevant old trade-off versus irrelevant recent detail.
- Unresolved failure boundary needed by a later question.
- Adversarial prompt injection inside candidate text or Evidence.
- Provider timeout, invalid JSON, unsupported source excerpt, and lease loss.

**Privacy rules:**

- No prompt, answer, resume, JD, Evidence body, summary, excerpt, focus text,
  source ID, artifact ref, session ID, or raw error message in metrics.
- Owner keys are represented only by approved privacy/owner scope buckets or
  irreversible digests used for runtime correctness, never exported as metric
  dimensions.
- Golden datasets use synthetic fixtures only.

**Acceptance:**

- Metrics can explain why compression was eligible, created, consumed,
  bypassed, or circuit-blocked without exposing content.
- Estimated/provider token error distributions are available by language and
  operation.
- Repository acceptance performs zero real-provider calls.

---

## Task 10: Build the repository acceptance and recovery matrix

**Requirements:** `MEM-CTX-ACCEPT-001`, `MEM-CTX-RECOVERY-001`.

**Purpose:** Prove the combined system before any deployed shadow execution.

**Add or modify:**

- `scripts/memory_system_optimization_acceptance.py`
- `tests/test_memory_system_optimization_acceptance.py`
- `docs/context-compression-optimization-acceptance.md`

**Required matrix:**

| Scenario | Business input | Artifact | Compressor calls |
|---|---|---|---:|
| All gates disabled | Prior deterministic path | None | 0 |
| Short shadow context | Unchanged | None | 0 |
| Resolved Follow-up availability is 9,000 and demand is 6,687 | Uses the 8,360-token denominator and remains below 80% | None | 0 |
| Rounded utilization displays 8,000 bp but cross-product is below threshold | Prior deterministic path | None | 0 |
| 80% pre-loss shadow with old turns | Unchanged | Created or reused | <= 1 |
| Dedup shadow with duplicates | Prior byte-equivalent input | Hypothetical only | 0 |
| Consume above 80% pre-dedup demand but below 80% hypothetical post-dedup demand while dedup is shadow | Non-deduplicated business route remains eligible; separate lower shadow metric | Business Artifact identity uses non-deduplicated source segments | <= 1 |
| Dedup enforce with identity-proven duplicates | Deduplicated deterministic input | None required | 0 |
| Consume with valid artifact | Task-aware projection | Completed | <= 1 |
| Retry after completion | Same projection | Reused | 0 |
| Invalid compression | Deterministic fallback | Failed | 1 |
| Provider circuit open | Deterministic fallback | Unchanged | 0 |
| Validation source quarantined | Deterministic fallback | Unchanged | 0 |
| Same text in two question identities | Both bounded-raw units preserved as required | No accidental subtraction | <= 1 |
| Oversized mandatory bounded-raw set | Stable budget failure/business fallback | None | 0 |
| Existing identity-v0 Artifact after migration | Byte-identical key and successful reload | Reused | 0 |
| New identity-v1 Artifact reload | Full intent identity reconstructed | Reused | 0 |
| Quarantined source under another owner | Other owner remains independently eligible | Separate scope | <= 1 |
| Concurrent half-open probes | One authoritative probe | Fenced state | <= 1 |
| Parent lease lost | No stale business patch | May complete safely | <= 1 |
| Digest conflict | Fail closed | Unchanged | 0 |
| Existing v1 checkpoint | v1 behavior | No new requirement | 0 |
| Existing v2 compatibility checkpoint | Compatibility behavior | Old refs readable | 0 |
| Session deletion | No later context access | Refs removed | 0 |

**Verification suites:**

```powershell
python -m pytest `
  tests/test_memory_config.py `
  tests/test_agent_runtime_composition.py `
  tests/test_context_budget.py `
  tests/test_context_selection.py `
  tests/test_context_source_identity.py `
  tests/test_context_compression_eligibility.py `
  tests/test_context_compressor.py `
  tests/test_context_compression_validation.py `
  tests/test_context_compression_runner.py `
  tests/test_context_artifacts.py `
  tests/test_context_artifact_contracts.py `
  tests/test_context_artifact_store_postgres.py `
  tests/test_interview_context_artifacts.py `
  tests/test_evidence_context_artifacts.py `
  tests/test_question_memory.py `
  tests/test_question_memory_retrieval.py `
  tests/test_question_memory_recovery.py `
  tests/test_interview_status_projection.py `
  tests/test_context_compression_failure_containment.py `
  tests/test_context_compression_failure_store_postgres.py `
  tests/test_context_compression_shadow_acceptance.py `
  tests/test_durable_interview_state.py `
  tests/test_durable_interview_graph.py `
  tests/test_session_deletion_worker.py `
  tests/test_memory_metrics.py `
  tests/test_memory_system_optimization_acceptance.py -q
```

Then run the repository acceptance entry point with fake providers.
The acceptance runner also validates that every test module declared by Tasks
0-10 is executed by this fixed suite or listed in an explicit reviewed
exemption manifest.

**Acceptance:**

- Fixed-time and fixed-input runs are reproducible.
- Existing v1/v2 recovery remains supported.
- No new schema is created by runtime constructors.
- Repository result states `READY_FOR_SHADOW`, never production `PASS`.

---

## Task 11: Run bounded deployed shadow observation (deployment evolution)

**Requirements:** `MEM-CTX-SHADOW-001`.

**Purpose:** Measure eligibility, quality, latency, and cost in a real deployed
workload without changing business-provider input. This task is **not part of
repository `READY_FOR_SHADOW` Definition of Done** and requires separate
operator authorization and sufficient workload volume.

**Preconditions:**

- Task 10 accepted.
- Migration and preflight succeed.
- Privacy review accepts metric dimensions.
- Explicit authorization for real-provider shadow calls and cost budget.

**Configuration:**

```text
budget.mode = shadow or enforce according to the accepted prior gate
compression.mode = shadow
task_intent_enabled = true
status_projection_enabled = true
exact_deduplication_mode = shadow
workflow consumption gates = false
```

**Minimum observation:**

- When real deployed volume exists, target at least 200 eligible interview
  generations or a separately approved statistically justified sample. Local
  single-user development is not required to fabricate 200 production cases to
  satisfy repository readiness.
- Chinese, English, and mixed-language buckets represented where workload
  supports them.
- Short, medium, and high-utilization buckets represented.
- At least one controlled provider failure drill and one replay drill.
- Synthetic/adversarial repository suites remain the authority for deterministic
  correctness invariants; production shadow is for distribution, latency, cost,
  provider behavior, and real-language quality.

**Exit criteria:**

- No observed current-answer preservation failures.
- No observed exact-recent-question preservation failures.
- No observed unsupported source-excerpt acceptance.
- No observed new-number/new-identifier acceptance.
- These zero-observation statements are not treated as proof of a true 0% defect
  rate; deterministic/property/adversarial tests remain the release authority
  for correctness invariants.
- Validation failure: < 1%.
- Artifact fallback: < 5% over the observation window.
- Provider-circuit and validation-quarantine rates: explained and within the
  approved thresholds.
- P95 compression latency within the product budget.
- Task-aware golden relevance improves over query-independent baseline.
- No privacy or identity violations.

**Output:** A signed observation packet with aggregate metrics, sample coverage,
known limitations, cost, latency, and a `HOLD`, `ROLL_BACK`, or
`READY_FOR_LOW_PERCENT_CONSUME` recommendation.

---

## Task 12: Low-percentage consumption canary and promotion packet (deployment evolution)

**Requirements:** `MEM-CTX-CANARY-001`.

**Purpose:** Prove that validated task-aware context improves continuity without
reducing business success or scoring trust in a deployment that actually has
sufficient new-session volume. This task is outside repository completion and
requires separate authorization.

**Preconditions:**

- Task 11 result is `READY_FOR_LOW_PERCENT_CONSUME`.
- Separate user/operator authorization.
- Interview budget enforcement active.
- Artifact store, deletion, retention, and rollback preflight green.
- Task-aware semantic-compression consumption and deterministic deduplication
  enforcement have separate assignment keys and can be rolled back
  independently.
- Deduplication may remain `shadow` during the semantic-compression canary. It
  may enter `enforce` only after its own shadow/equivalence packet is accepted.

**Canary sequence:**

```text
0% task-aware consume baseline; exact_deduplication_mode = shadow
  -> 1% new-session assignment
  -> hold and evaluate
  -> 5% only after explicit review
  -> hold and evaluate
  -> no further promotion in this plan

optional, separately accepted deduplication canary:
exact_deduplication_mode: shadow
  -> enforce for 1% new-session assignment
  -> hold and evaluate before any broader enforcement
```

Existing sessions keep their immutable graph and memory-policy assignments.

**Promotion metrics:**

- Follow-up business success rate.
- Context overflow count.
- Basic-template fallback rate.
- Relevant unresolved-topic carry-forward.
- Duplicate follow-up rate.
- Current-answer and exact-recent preservation.
- Provider token input and cached-token behavior when available.
- End-to-end latency and compression-provider cost.
- Artifact reuse rate.
- Validation, fallback, lease-loss, and circuit-open rates.
- Report scoring provenance invariants.

**Immediate rollback conditions:**

- Any current-answer or exact Evidence replacement.
- Any compressed summary treated as authoritative scoring Evidence.
- Any identity, scope, digest, or owner-binding violation.
- Any stale worker successfully patching business state.
- Any existing v1/v2 checkpoint recovery regression.
- Any privacy-sensitive metric or trace payload.
- Context overflow above the accepted baseline.
- Validation failures >= 1% or fallback >= 5% for 15 minutes.
- Material business success regression.

**Rollback order:**

```text
task-aware consume -> compression shadow
compression shadow -> disabled if provider cost/failure remains unsafe
exact_deduplication_mode: enforce -> shadow -> disabled
new v2/question-memory assignment -> 0%
existing assigned sessions -> keep compatible graph/runtime available
do not delete schema or completed artifacts during incident rollback
```

Task-aware semantic-compression consumption and deterministic deduplication
enforcement are independent rollback dimensions. An incident in one must not
require enabling, retaining, or broadening the other.

**Output:** A promotion packet. This plan does not authorize promotion above
5% or committed-default changes.

---

## 8. Evaluation Method

### 8.1 Offline paired evaluation

For each synthetic or approved redacted case, compare:

```text
A: current deterministic selection
B: query-independent Question Memory
C: task-aware intent + Question Memory projection
```

Use identical source messages, question focus, model configuration, and token
budget. Judge:

- grounded fact retention;
- required number/identifier retention;
- unresolved-topic retention;
- irrelevant-detail removal;
- duplicate removal;
- current-answer preservation;
- token count;
- follow-up relevance;
- repeatability;
- provider-call count.

Human review must be blind to route for the quality subset. Automated checks
remain authoritative for exact excerpts, numbers, identifiers, identity, and
schema constraints.

### 8.2 No single compression-ratio target

Do not optimize for the smallest output. A lower token count is a failure when
it drops required trade-offs, failure boundaries, or provenance. Promotion
requires a Pareto improvement or an explicitly accepted trade-off across:

- task success;
- grounded retention;
- token cost;
- latency;
- failure rate.

### 8.3 Language coverage

Maintain separate measurements for Chinese, English, and mixed-language
contexts because fallback token estimation and identifier segmentation may
behave differently.

### 8.4 Separate correctness proof from observational statistics

Deterministic invariants such as current-answer preservation, exact-recent
preservation, source identity, exact excerpt grounding, number/identifier
constraints, owner binding, and schema validity are proved by unit/property/
adversarial/replay tests. Shadow/canary samples measure real distribution,
latency, cost, estimator error, fallback frequency, and semantic relevance.

Do not interpret `0` failures in 200 observations as proof of a true 0% failure
rate. For intuition, the rule-of-three gives an approximate 95% upper bound of
`3 / n` when zero failures are observed; with `n=200`, that is about 1.5%. This
is why zero-tolerance correctness properties remain enforced in code/tests
rather than inferred from operational samples.

---

## 9. Privacy, Security, and Authority Invariants

1. Candidate messages remain session-scoped and authoritative.
2. Compression intent may contain plan-derived focus text but never candidate
   free text, Resume/JD bodies, or Evidence bodies.
3. Prompt injection inside candidate or Evidence content is data, not an
   instruction. The compressor system contract remains higher authority.
4. Provider-facing bounded-raw representations remain linked to the complete
   authoritative source identity. Deterministic bounding never changes the
   authority of stored raw data and never bypasses `RenderedPromptGuard`.
5. Artifacts retain privacy-scope identity and owner refs. Existing v0 identity
   material remains byte-compatible; only new intent-aware Artifacts use the
   fully persisted `identity-v1` material.
6. Telemetry contains only stable codes, booleans, versions, counts, buckets,
   and timings.
7. Deduplication `shadow` preserves non-deduplicated business eligibility,
   target, Artifact identity, compressor input, selection, and final
   business-provider input while emitting only privacy-safe hypothetical
   counts/buckets. `enforce` removes only canonical identity-proven duplicates
   and remains separately gated.
8. Checkpoints contain refs/digests and bounded failure-state metadata, never
   compressed payloads or intent text.
9. The authoritative failure-state store is privacy- and owner-scoped, uses
   hashed canonical keys and fencing, persists no raw error/content text, and is
   included in session deletion. Retention never removes a live half-open lease.
10. Summary claims and provider-facing compressed Evidence remain
    non-authoritative and cannot replace or enter authoritative raw scoring
    Evidence. Raw Evidence identity/content remains retrievable for scoring and
    provenance.
11. Interview Semantic Status counters and active focus tags come from declared
    authoritative producers. `advisory_unresolved_topic_codes` remains visibly
    advisory and never upgrades Provider-generated memory to verified truth.
12. Session deletion removes Question Memory index entries, Artifact owner refs,
    and owner-scoped failure-state rows before orphan cleanup.
13. Artifact reuse requires complete version-appropriate identity equality,
    including intent and resolved target budget for `identity-v1`.
14. Owner/privacy scope is part of Artifact, quarantine, and circuit identity;
    equal source text never authorizes cross-owner reuse or blocking.
15. Unknown or unsupported model context windows fail preflight.

---

## 10. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Task-aware intent reduces Artifact reuse | Keep Question Memory archival artifacts future-query-independent; use current intent only where identity already depends on current focus |
| Compression drops subtle candidate caveats | Preserve exact recent authoritative sources and bounded-raw Provider representations, validate source excerpts, use non-authoritative summaries, evaluate failure-boundary cases |
| Summary-of-summary compounds loss | Rank existing units deterministically; do not add a second LLM projection in this plan |
| 80% threshold creates eager provider cost | Require compressible non-mandatory history, shadow first, dynamic target, cost cap |
| Rounded telemetry changes threshold behavior | Use integer cross-multiplication for eligibility and reserve rounded basis points for display/metrics |
| Operation cap is mistaken for resolved model availability | Derive selectable content from resolved `available_input_tokens`; scope 11,360/9,088 to the explicit 12,000-token fixture |
| Deduplication removes meaningful repetition | Exact canonical equality only; preserve role/question/source scope |
| Deduplication shadow accidentally changes business behavior | Assert unchanged eligibility, target, Artifact identity, compressor input, selection, and final Provider messages in `shadow`; gate `enforce` separately and provide `enforce -> shadow -> disabled` rollback |
| Mandatory bounded-raw content alone exceeds budget | Keep deterministic bounds, fail through the stable budget contract or documented business fallback, and issue zero compressor calls |
| Identity migration changes existing Artifact keys | Preserve byte-exact v0 canonical fixtures, use nullable version columns, dispatch reconstruction by identity version, and never rewrite completed v0 rows |
| Identity-v1 reload loses intent material | Persist the intent digest and identity version explicitly; require in-memory and PostgreSQL full-identity reload tests |
| Failure-state races allow multiple half-open calls | Use atomic compare-and-set, fencing versions, probe leases, and concurrent-probe acceptance tests |
| Failure containment hides permanent correctness faults | Ownership/identity/privacy failures remain fail-closed; provider circuit and source/intent validation quarantine are separate and owner-scoped |
| Status projection becomes an alternative truth | Derive counters/focus only from authoritative state/records; keep unresolved-topic codes advisory; use controlled codes and no free-form evaluation |
| More configuration becomes unmanageable | Reuse one immutable config, validate combinations, expose readiness, avoid duplicate loaders |
| Canary changes scoring behavior | Summaries never become scoring Evidence; lock per-question provenance and compare report invariants |

---

## 11. Definition of Done

### 11.1 Repository `READY_FOR_SHADOW`

Repository implementation is complete only when all of the following are true:

1. The existing 80% utilization and exact-recent configuration fields have
   tested runtime consumers.
2. Every `MEM-CTX-*` requirement referenced by Tasks 0-12 exists exactly once in
   the pinned Spec, and automation fails on missing, duplicate, or unreferenced
   IDs.
3. CompressionIntent v1 is bounded, canonical, hashed into identity, and
   semantically visible to the compressor through one immutable
   `ResolvedCompressionRequest`.
4. Byte-compatible identity-v0 fixtures retain their exact existing Artifact
   keys, and identity-v1 rows reload the complete intent-aware identity through
   both in-memory and PostgreSQL stores.
5. Short contexts cause zero compressor calls.
6. Threshold eligibility uses the declared mode-aware pre-loss equations and
   Provider message-framing estimator before destructive loss, only when
   historical compressible units exist. The decision uses integer
   cross-multiplication; rounded basis points are telemetry only. The denominator
   is resolved `available_input_tokens - fixed_prompt_reserve_tokens`, not the
   static operation cap.
7. `exact_deduplication_mode` defaults to `disabled`; `shadow` preserves
   non-deduplicated business eligibility, target, identity, compressor input,
   selection, and final Provider input while recording separate hypothetical
   metrics; `enforce` runs canonical identity-based deduplication before
   proactive utilization and truncation only after its separate equivalence gate
   is accepted.
8. Current questions, latest candidate answers, and configured recent questions
   remain authoritative in storage and non-semantically-compressed in bounded
   Provider representations; authoritative raw Evidence remains stored and
   retrievable for scoring/provenance even when an explicitly allowed Provider-
   facing Evidence projection is compressed.
9. Oversized mandatory bounded-raw input takes the stable budget error or
   documented business fallback, never semantic compression, and results in zero
   compressor calls.
10. Question Memory stays non-authoritative, reusable, owner-bound, and
   source-verifiable.
11. The Interview Semantic Status projection is deterministic, bounded, free of
    candidate content, and excludes runtime-control metadata; progress/focus
    fields have authoritative producers and unresolved-topic codes remain
    explicitly advisory.
12. Provider failures open a durable provider circuit; repeatable
    source/intent validation failures open only a matching validation quarantine.
    Both states use the owner-scoped fenced failure-state store, survive worker
    replacement, isolate different owners/sources, allow at most one half-open
    probe, and are removed by owner/session deletion. Neither mechanism blocks
    the business operation.
13. Existing v1 and v2 checkpoints recover without migration.
14. The fixed Task 10 suite covers every test module declared by Tasks 0-10 or a
    reviewed exemption; it passes with fake Providers, makes zero real-provider
    calls, and reports only `READY_FOR_SHADOW`.
15. Task-aware consumption and deduplication enforcement remain independently
    reversible and disabled by committed default.
16. No full-session LLM compaction is added without a new evidence-backed plan.
17. All unrelated worktree changes are preserved.

### 11.2 Deployment promotion (not repository completion)

Tasks 11-12 begin only after `READY_FOR_SHADOW`, separate operator authorization,
and an environment with sufficient workload. Deployed shadow must satisfy its
quality/privacy/latency/failure criteria before any consume canary. A 1%/5%
new-session rollout is not required to declare the v1.2.1 repository
implementation complete.

---

## 12. Recommended Execution Slices

For reviewable changes, implement in these slices:

1. **Slice A — Policy wiring:** Tasks 0-1.
2. **Slice B — Deterministic context correctness:** Tasks 5-6 (canonical source
   identity, exact dedup shadow path, exact-recent preservation, identity-safe
   Question Memory subtraction).
3. **Slice C — Pre-loss adaptive eligibility:** Task 4 after Slice B.
4. **Slice D — Task-aware semantic compression:** Tasks 2-3; may be developed in
   parallel with Slice B/C after Task 0, but must merge before repository
   acceptance.
5. **Slice E — Semantic status and failure containment:** Tasks 7-8.
6. **Slice F — Evidence and repository gate:** Tasks 9-10.
7. **Slice G — Deployment evolution:** Tasks 11-12, only with separate
   authorization and sufficient workload.

Each slice should end with its focused tests, full affected regression, a
privacy review of new metadata, and a diff audit proving that committed rollout
and consumption defaults remain disabled.
