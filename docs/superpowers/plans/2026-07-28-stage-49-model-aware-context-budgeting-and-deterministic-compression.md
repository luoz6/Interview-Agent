# Stage 49 Model-Aware Context Budgeting and Deterministic Compression Plan

**Plan revision:** v1.1, incorporating the tokenizer-resolution, legacy-message,
validated-excerpt, checkpoint-recovery, telemetry-schema, and prompt-caching
review amendments.

> **Execution note:** Implement this plan in task order and begin each task
> with the stated failing or characterization test. Do not add LLM-generated
> semantic summaries, Context Artifact persistence, or new LangGraph State
> payloads in Stage 49. Preserve the complete dirty worktree and do not create
> a commit unless explicitly requested.

**Goal:** Introduce an explicit, model-aware authorization layer before every
LLM provider invocation. Every operation must know its context window, output
reserve, protocol/schema reserve, operation input cap, deterministic selection
policy, final rendered-prompt estimate, and privacy-safe usage evidence before
it can call the provider.

**Architecture:** Raw business inputs remain immutable sources of truth.
Operation-specific context builders derive bounded provider inputs by removing
duplicate representations, selecting complete semantic units, extracting
relevant paragraphs, and deterministically truncating oversized content. A
model capability registry and token-estimator chain calculate an input budget.
The final rendered prompt is measured again immediately before invocation.
Provider-reported usage is captured after the call when available and is kept
distinct from pre-call estimates. Full-session Review requests that cannot fit
are routed to the existing Microbatch map-reduce path before any provider call.

**Tech Stack:** Python 3.11, LangGraph, LangChain `ChatOpenAI`, OpenAI-compatible
providers, Pydantic v2, PostgreSQL runtime signals and Agent Run ledger, pytest,
and the existing Stage 46-48 ownership, fencing, recovery, telemetry, privacy,
and connection-capacity contracts.

**Baseline:** Stage 46-48 established single-writer execution authority,
lease/fencing safety, fail-closed heartbeat behavior, privacy-safe Agent
telemetry, and bounded PostgreSQL connection domains. Production observation
remains `NOT_RUN`; committed Interview and Report rollout defaults remain
`0/0`. Stage 49 must preserve those contracts. Repository acceptance is not
production context-budget evidence.

---

## Why This Is the Next Step

The runtime currently has no systematic awareness of provider context windows.
Interview follow-up uses a fixed last-four-message slice, bound Knowledge
Evidence is injected without a token budget, Prep can concatenate complete job
descriptions and resumes, and Full-Session Report can serialize all questions,
messages, and references into one request. Microbatch Review is the correct
map-reduce direction, but its reduce payload still duplicates answers,
rationales, critiques, and references.

These behaviors are functionally usable but cannot prove that a provider
request fits its model window, cannot distinguish estimated usage from actual
provider usage, and cannot safely plan cost or latency. A tokenizer-only patch
is insufficient: custom OpenAI-compatible endpoints may not share OpenAI token
semantics, structured output adds hidden schema/protocol overhead, and output
capacity is not currently reserved and enforced as a single contract.

Stage 49 creates the deterministic foundation. Stage 50 will add durable,
write-once semantic compression artifacts only after Stage 49 proves that
budgeting, routing, telemetry, privacy, and replay determinism are correct.

## Execution Preconditions

1. Preserve all existing tracked and untracked user changes. Do not use
   destructive reset, checkout, clean, or broad delete commands.
2. Use the repository Python 3.11 interpreter and deterministic fake providers
   for repository tests. Do not call a real LLM.
3. Keep committed rollout defaults unchanged:

   ```text
   INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
   REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
   ```

4. Keep context enforcement feature flags disabled by default.
5. Preserve existing Graph node/edge topology, interrupt locations, thread
   identity, lease/fencing predicates, Review Effect semantics, and checkpoint
   schemas unless this plan explicitly states otherwise.
6. Do not put prompts, answers, resumes, job descriptions, Evidence content,
   or compression payloads into LangGraph State, runtime signals, Agent Run
   metadata, canary snapshots, logs, or exception messages.
7. Do not add a Context Artifact table or LLM-generated summary in this stage.
8. Treat explicit custom-provider configuration as authoritative. Do not infer
   a production context window from a model-name substring.
9. After the first production source edit, previous readiness is historical
   evidence only. Restore readiness after all Stage 49 gates pass.
10. Production observation remains `NOT_RUN` until separately authorized real
    traffic is observed.

## Scope

### In Scope

- Model runtime profiles and explicit context-window configuration.
- Operation-level input caps and output limits.
- Token estimator abstraction with conservative fallback.
- Exact final rendered-prompt validation.
- Token-aware Interview conversation selection.
- Per-item and total Knowledge Evidence budgets.
- Deterministic Prep selection for job descriptions, resumes, and Knowledge
  candidate metadata.
- Per-question Review context budgets.
- Pre-call Full-Session-to-Microbatch routing.
- Microbatch Reduce payload deduplication.
- Provider usage capture when available.
- Privacy-safe context metrics, runtime signals, canary evaluation, preflight,
  acceptance automation, and operator documentation.

### Explicitly Out of Scope

- LLM-generated conversation, Evidence, Resume, or job-description summaries.
- `workflow_context_artifacts` or equivalent persistence.
- Context compression claim, heartbeat, lease, or fencing.
- Context Artifact retention or cleanup.
- Automatic model switching.
- Cross-session memory.
- Vectorized or RAPTOR-style hierarchical summaries.
- Silent provider-side truncation.
- Context payloads in LangGraph State.
- Graph schema migration for compression references.

## Fixed Decisions

### Fixed Decision 1: Raw Business Inputs Remain Immutable

Context budgeting must never mutate `state["messages"]`, `InterviewPlan`,
`EvaluationChunk`, Knowledge repository chunks, Resume text, job-description
text, or `QuestionEvaluationRecord`. Provider inputs are derived values.

### Fixed Decision 2: Stage 49 Compression Is Deterministic

Allowed transformations are structural deduplication, complete-turn selection,
token-bounded selection, stable paragraph/sentence extraction, stable
head/relevant-middle/tail truncation, and map-reduce. No provider-generated
summary may participate in Stage 49.

### Fixed Decision 3: Provider Usage Is Authoritative Only After the Call

Pre-call `estimated_input_tokens` and post-call provider-reported input/output
usage are separate fields. If a provider does not return usage, actual usage
remains absent; the estimate must not be copied into an actual-usage field.

### Fixed Decision 4: `tiktoken` Is an Estimator, Not Universal Truth

Known model-specific tokenizers are preferred. Tokenizer resolution follows an
explicit fail-closed chain:

```text
exact model tokenizer
    -> explicitly configured tokenizer family
    -> tested model-family mapping
    -> conservative deterministic UTF-8 estimator
    -> ContextEstimatorUnavailable
```

Unknown DeepSeek, proxy, or custom OpenAI-compatible models must not silently
select `cl100k_base` or another optimistic OpenAI encoding after
`encoding_for_model()` raises. `cl100k_base` is allowed only when it is an
explicit operator configuration or a repository-tested model-family mapping.
The default unknown-model fallback remains conservative.

The final rendered-prompt check removes component/rendering estimation drift,
but it cannot eliminate differences between a local estimator and the
provider's actual tokenizer. Safety margin, protocol/schema reserve,
post-call usage reconciliation, and provider-overflow canary signals remain
mandatory.

### Fixed Decision 5: Unknown Production Models Require an Explicit Window

An unknown model or custom base URL without an explicit context-window value
fails preflight. Stage 49 must not assume 128K based on a model name.

### Fixed Decision 6: Reserved Output Must Be Enforced

Every operation has an explicit maximum output. The provider call must receive
the corresponding output limit where the provider contract supports it. An
input-budget subtraction without an actual provider output cap is incomplete.

### Fixed Decision 7: Final Rendered Prompts Are the Hard Boundary

Component estimates guide selection, but the complete rendered prompt is
measured immediately before invocation. Structured-output protocol/schema
overhead is represented by a configured reserve unless the exact serialized
request can be measured.

### Fixed Decision 8: Provider-Side Silent Truncation Is Forbidden

An oversized final request is deterministically shrunk, routed to a bounded
alternative, or rejected before invocation. It is never sent in the hope that
the provider will truncate it safely.

### Fixed Decision 9: Current Interview Content Has Mandatory Representation

The current question and latest candidate answer cannot be completely dropped.
An abnormally long answer may be represented by deterministic, bounded
head/relevant-middle/tail excerpts. If even the minimum representation cannot
fit, the provider is not called and `ContextBudgetExceeded` is raised.

### Fixed Decision 10: Evidence Identity Is Independent of Representation

Evidence ID, source type, content digest, and corpus-manifest checks remain
unchanged. Stage 49 may bound the provider representation but cannot rewrite
or replace the repository source.

### Fixed Decision 11: Full-Session Overflow Routes Before Invocation

A Full-Session Report that exceeds its operation budget is routed to the
existing Microbatch path before any Full-Session provider request. Later
questions must never be silently deleted to make the request fit.

### Fixed Decision 12: Scoring Evidence Must Be Traceable to Raw Answers

`dimension_evidence.observed` must be copied from original candidate content or
validated deterministic excerpts. Knowledge content, evaluator rationale, and
truncation markers cannot be treated as candidate evidence.

### Fixed Decision 13: Context Construction Is Replay-Deterministic

The same source inputs, model profile, operation policy, and policy version
must produce the same selected units, truncation result, route, prompt digest,
estimate, and aggregate statistics. Selection cannot depend on random values,
wall-clock time, set ordering, or concurrent completion order.

### Fixed Decision 14: Context Payloads Never Enter Telemetry

Only counts, numeric usage, utilization, stable booleans, safe route enums,
policy versions, and approved digests may enter telemetry. Prompt fragments,
message IDs, Evidence IDs, answers, Resume/JD content, and Evidence text are
forbidden.

### Fixed Decision 15: Telemetry Is Best Effort

Usage collection, Agent Run recording, signal recording, and canary aggregation
must never replace a successful business output. Failures produce only
privacy-safe warnings and aggregate incidents.

## Target Architecture

Add the following modules:

```text
app/services/model_capabilities.py
app/services/token_estimation.py
app/services/context_budget.py
app/services/context_selection.py
```

The target call flow is:

```text
Raw Business Context
        |
        v
OperationContextPolicy
        |
        v
ModelRuntimeProfile + TokenEstimator
        |
        v
Deterministic Context Selector
        |
        v
Existing Prompt Builder
        |
        v
RenderedPromptGuard
        |
        v
Provider Invocation
        |
        v
Provider Usage Collector
        |
        v
Privacy-Safe Agent/Signal/Canary Metrics
```

## Core Contracts

### Model Runtime Profile

```python
@dataclass(frozen=True)
class ModelRuntimeProfile:
    provider: str
    model: str
    context_window_tokens: int
    protocol_reserve_tokens: int
    structured_output_reserve_tokens: int
    safety_margin_tokens: int
```

### Operation Context Policy

```python
@dataclass(frozen=True)
class OperationContextPolicy:
    operation: str
    input_cap_tokens: int
    max_output_tokens: int
    mandatory_content_floor_tokens: int
    max_single_message_tokens: int
    max_evidence_item_tokens: int
    max_total_evidence_tokens: int
    max_evidence_items: int
    context_policy_version: str
```

### Context Budget

```python
@dataclass(frozen=True)
class ContextBudget:
    operation: str
    model: str
    context_window_tokens: int
    operation_input_cap_tokens: int
    max_output_tokens: int
    protocol_reserve_tokens: int
    structured_output_reserve_tokens: int
    safety_margin_tokens: int
    available_input_tokens: int
```

Budget calculation:

```python
model_available_input = (
    context_window_tokens
    - max_output_tokens
    - protocol_reserve_tokens
    - structured_output_reserve_tokens
    - safety_margin_tokens
)

available_input_tokens = min(
    model_available_input,
    operation_input_cap_tokens,
)
```

### Context Selection Statistics

```python
@dataclass(frozen=True)
class ContextSelectionStats:
    source_message_count: int = 0
    selected_message_count: int = 0
    dropped_message_count: int = 0
    truncated_message_count: int = 0
    source_evidence_count: int = 0
    selected_evidence_count: int = 0
    dropped_evidence_count: int = 0
    truncated_evidence_count: int = 0
    estimated_input_tokens: int = 0
    available_input_tokens: int = 0
    budget_utilization_basis_points: int = 0
    estimator_fallback_used: bool = False
    deterministic_shrink_used: bool = False
```

### Context Build Result

```python
@dataclass(frozen=True)
class ContextBuildResult:
    prompt: str
    prompt_sha256: str
    policy_version: str
    stats: ContextSelectionStats
```

`prompt` is process-local and must never be serialized by recorders. Only the
digest, version, and approved numeric statistics may leave the call boundary.

---

## Task 1: Establish Characterization Tests

### Files

Create or extend targeted tests for:

```text
app/services/llm.py
app/services/knowledge_binding.py
app/graphs/interview_graph.py
app/graphs/durable_interview_graph.py
app/services/evaluator.py
app/services/report_microbatch.py
```

### Test First

Capture synthetic provider inputs and prove the current behavior:

1. Prep includes the complete synthetic JD and Resume.
2. Interview selects the last four messages.
3. Bound Knowledge Evidence includes complete synthetic chunk content.
4. Full-Session Report includes every question and evaluation item.
5. Microbatch Reduce duplicates answer/rationale/critique in `messages`.
6. Identical references are duplicated across scoring and answer fields.

Use only artificial markers. Never include real candidate or credential data.

### Completion

Characterization tests document the starting behavior. Later tasks replace
their old-output assertions with the new budget contracts.

## Task 2: Implement Model Capability Resolution

### Files

Create:

```text
app/services/model_capabilities.py
```

Modify:

```text
app/services/llm.py
```

### Test First

Cover known models, explicit overrides, unknown models, custom base URLs,
invalid windows, negative reserves, and impossible window/output combinations.

### Implementation

Add `ModelCapabilityRegistry` and stable configuration errors. Resolution order
is explicit configuration, tested known-model registry, then failure. A custom
base URL requires an explicit window unless a separately tested provider
profile is configured.

Extend `LLMConfig` with:

```text
context_window_tokens
protocol_reserve_tokens
structured_output_reserve_tokens
context_safety_margin_tokens
```

Resolve and retain configuration once:

```python
resolved_config = config or LLMConfig.from_env()
self.config = resolved_config
self.chat_model = chat_model or self._build_chat_model(resolved_config)
```

When a fake `chat_model` is injected, tests and production composition must
also inject or resolve an explicit model profile rather than inspecting an
arbitrary model object.

## Task 3: Implement the Token Estimator Chain

### Files

Create:

```text
app/services/token_estimation.py
```

### Test First

Test empty, Chinese, English, mixed language, code, JSON, UUID-heavy text,
Markdown, emoji, unknown models, estimator exceptions, fallback success, total
failure, deterministic repetition, and privacy-safe errors. Explicitly test
exact-model, configured-family, tested-family, and conservative-fallback paths.

### Implementation

Define:

```python
class TokenEstimator(Protocol):
    def estimate_text(self, text: str, *, model: str) -> int:
        ...

    def estimate_messages(self, messages, *, model: str) -> int:
        ...
```

Provide at least:

```text
TikTokenEstimator
ConservativeUtf8TokenEstimator
CompositeTokenEstimator
FakeTokenEstimator for tests
```

Resolve estimators in this order:

```text
1. Exact provider/model tokenizer.
2. Explicitly configured tokenizer family, such as cl100k_base.
3. A model-family mapping that has dedicated repository tests.
4. ConservativeUtf8TokenEstimator.
5. ContextEstimatorUnavailable if every path fails.
```

Return resolution metadata without source text:

```python
@dataclass(frozen=True)
class TokenEstimatorResolution:
    estimator: TokenEstimator
    estimator_path: Literal[
        "exact_model",
        "configured_family",
        "tested_family_mapping",
        "conservative_utf8",
    ]
    fallback_used: bool
```

An environment setting such as `LLM_TOKENIZER_FAMILY=cl100k_base` may select a
compatible family explicitly. A `KeyError` from `encoding_for_model()` must not
implicitly make that selection.

The conservative fallback intentionally overestimates unknown-model input. It
must return a stable non-negative integer and a flag that the fallback path was
used. It must never log the input text.

If every estimator fails, raise `ContextEstimatorUnavailable`; do not classify
the condition as a provider outage.

## Task 4: Implement Budget Resolution and Final Prompt Guard

### Files

Create:

```text
app/services/context_budget.py
```

### Test First

Test exact boundary, one-token overflow, 4K/8K/128K profiles, operation caps,
output reserve, protocol reserve, structured-output reserve, invalid minimum
budgets, fallback estimator use, and privacy-safe exceptions.

### Implementation

Add:

```text
ContextBudgetResolver
RenderedPromptGuard
ContextBudgetExceeded
ContextConfigurationError
ContextEstimatorUnavailable
```

`RenderedPromptGuard` measures the complete final prompt, calculates basis-point
utilization, computes a deterministic digest, and rejects an oversized request
before provider invocation. Exceptions include only safe machine fields and
numeric counts.

## Task 5: Implement Deterministic Selection Primitives

### Files

Create:

```text
app/services/context_selection.py
```

### Test First

Test complete-turn grouping, question grouping, newest-first selection followed
by chronological output, paragraph and sentence boundaries, code blocks,
head/relevant-middle/tail truncation, input immutability, stable ordering, and
identical result digests across runs.

### Implementation

Add reusable helpers for:

```text
group_messages_by_question
group_messages_into_turns
select_newest_complete_turns
truncate_text_by_token_budget
truncate_by_paragraph_boundary
truncate_by_sentence_boundary
select_evidence_under_budget
select_document_sections_under_budget
calculate_context_stats
```

Use a stable omission marker. Character limits are only a final emergency
guard; token budgets and semantic boundaries are the primary mechanism.

## Task 6: Budget Prep Plan Inputs

### Files

Modify:

```text
app/services/llm.py
```

Optionally create a focused helper module if required by existing conventions:

```text
app/services/prep_context.py
```

### Test First

Cover short and oversized JD/Resume inputs, Chinese/English/mixed text, many
Resume projects, promotional JD sections, many Knowledge candidates, final
prompt boundaries, deterministic output, and no content in telemetry.

### Implementation

Split JD and Resume into stable sections. Prioritize required skills,
responsibilities, projects, experience, and technologies. Give each major
Resume section a bounded representation before lower-value repeated prose.
Bound Knowledge candidate metadata separately. Build the normal plan prompt,
then apply the final rendered-prompt guard.

Stage 49 does not summarize Resume or JD with an LLM. If the deterministic
minimum cannot fit, raise `ContextBudgetExceeded` without a provider call.

## Task 7: Replace Interview's Fixed Message Window

### Files

Modify:

```text
app/graphs/interview_graph.py
app/graphs/durable_interview_graph.py
```

### Test First

Cover short histories, histories crossing question boundaries, multi-turn
current questions, very long latest answers, Chinese/code/JSON answers,
mandatory current-question retention, complete-turn selection, chronological
output, Legacy/Durable parity, state immutability, partially tagged legacy
histories, completely untagged histories, repeated roles, skipped questions,
and replay/retry message sequences.

### Implementation

Replace `messages[-4:]` with a shared selector. Priority order is:

```text
P0 current question
P0 bounded representation of latest candidate answer
P0 latest interviewer message for the current question
P1 earlier complete turns for the current question
P2 limited previous-question continuity
P3 older questions
```

Select from newest to oldest, but emit in chronological order. The latest
answer must have a representation. If the minimum mandatory representation
cannot fit, reject before invoking the Examiner provider.

Legacy and Durable graphs must share the same algorithm rather than copying
independent implementations. Preserve the Durable Graph's injectable
`context_builder` boundary for isolation tests, policy injection, and fault
tests; its default implementation and the Legacy path must delegate to the
same shared builder.

Messages with explicit `question_id` use that field as the authoritative
grouping key. Historical messages without `question_id` use a conservative
compatibility path:

```text
explicit_question_id
    -> authoritative question group

legacy_role_pair
    -> only adjacent, unambiguous interviewer/candidate pairs

legacy_unscoped
    -> standalone low-priority continuity message
```

Do not infer a historical question ID by fuzzy-matching message content to a
Plan question. Consecutive same-role messages, retry duplicates, skipped
questions, or otherwise ambiguous messages remain `legacy_unscoped`.
`legacy_unscoped` content may provide low-priority continuity but cannot be
treated as current-question candidate Evidence.

Represent grouping explicitly:

```python
@dataclass(frozen=True)
class ConversationUnit:
    question_id: str | None
    messages: tuple[dict[str, str], ...]
    grouping_path: Literal[
        "explicit_question_id",
        "legacy_role_pair",
        "legacy_unscoped",
    ]
    is_complete_turn: bool
```

## Task 8: Budget Bound Knowledge Evidence

### Files

Modify:

```text
app/services/knowledge_binding.py
app/graphs/interview_graph.py
app/graphs/durable_interview_graph.py
```

### Test First

Cover no Evidence, one short item, one oversized item, many items, item and
total limits, stable ID order, retained source headers, manifest/version/missing
behavior, repository failures, input immutability, and telemetry privacy.

### Implementation

Keep `resolve_bound_evidence()` as the complete source-resolution contract.
Add a separate budgeted representation builder that consumes a successful
resolution. It enforces maximum item count, per-item token budget, and total
Evidence budget while retaining Evidence identity and source information.

The required ordering is:

```text
1. Read Evidence IDs from the current question.
2. Resolve by ID and validate expected hashes/manifest/version/missing state.
3. Produce a complete KnowledgeBindingResolution.
4. Apply the Context Budget to the provider representation.
```

Budget selection must not run before identity and corpus validation, because it
must not hide existing Knowledge degradation or conflict semantics. A Legacy
path without a repository passes `evidence_resolution=None` to the same shared
Interview builder rather than using a second selector implementation.

Question-aware paragraph selection may use deterministic lexical scoring over
the question prompt/focus. Selected paragraphs are reordered into original
source order. Repository chunks are never rewritten.

Existing degraded Knowledge behavior remains unchanged.

## Task 9: Budget Per-Question Review Inputs

### Files

Modify the actual per-question provider boundary, expected among:

```text
app/services/evaluator.py
app/services/evaluator_ext.py
app/services/report_microbatch.py
app/agents/shadow_reviewer.py
```

### Test First

Cover short and very long answers, multi-turn answers, skipped/unanswered
questions, large and duplicate references, exact excerpt provenance, truncation
markers, Knowledge/candidate role separation, and final prompt boundaries.

### Implementation

Keep `EvaluationChunk` complete as a business object. Derive a bounded
`QuestionReviewInput` immediately before provider invocation. Priority order is
fixed instructions/schema, question/focus, bounded candidate answer, applicable
dimensions, scoring references, answer references, and low-value repeated
interviewer text.

Reference item count, item tokens, and total reference tokens are bounded.
Identical scoring and answer reference payloads are not serialized twice.
Candidate observations must remain traceable to original messages.

## Task 10: Route Oversized Full-Session Reports to Microbatch

### Files

Modify:

```text
app/services/evaluator.py
app/services/evaluator_ext.py
app/services/llm.py
app/services/runtime.py
```

### Test First

Cover Full-Session under budget, exact boundary, one-token overflow, zero
Full-Session provider calls after overflow, all-question Microbatch coverage,
stable route decisions, replay determinism, and privacy-safe route metadata.

### Implementation

Build and measure the Full-Session candidate request before invocation. Use the
Full-Session path only if it fits. Otherwise route to Microbatch with a stable
route enum:

```text
full_session
microbatch_configured
microbatch_context_budget
```

Do not delete later questions or send a known-oversized Full-Session request.

## Task 11: Deduplicate Microbatch Reduce Payloads

### Files

Modify:

```text
app/services/report_microbatch.py
app/services/llm.py
```

### Test First

Prove that answers, rationales, critiques, question text, and identical
references are not serialized twice; prove that Report assembly, scoring,
observed Evidence, reference IDs, skipped/unanswered behavior, and fallback
semantics remain unchanged. Prove that every Reduce-stage observed excerpt was
validated against its source candidate message before a reusable
`QuestionEvaluationRecord` was completed.

### Implementation

Introduce a bounded Reduce DTO containing question identity/kind/state, score,
dimension scores, applicable dimensions, dimension Evidence, rationale,
critique, better answer, and reference chunk IDs. Remove reconstructed
`messages` and complete `user_answer` where verified observed excerpts already
provide the required source material.

Map/per-question Review is the provenance boundary. It must validate that each
`dimension_evidence.observed` value is a continuous excerpt of an original
candidate message before the Question Evaluation becomes completed/reusable.
A provenance-aware shape is recommended:

```python
class ValidatedObservedExcerpt(BaseModel):
    text: str
    source_message_sha256: str
    source_message_index: int | None = None
    verified_against_source: Literal[True] = True


class ReportReduceDimensionEvidence(BaseModel):
    dimension: str
    observed: list[ValidatedObservedExcerpt]
    missing: list[str]
    quality_signals: list[str]
```

Internal source digests support backend verification and need not be exposed to
the Report provider unless required by its contract. The Reduce provider may
organize rationale, critique, and `better_answer`, but it must not invent or
rewrite observed candidate excerpts. `better_answer` is coaching output and is
not required to appear in the original candidate answer.

If compatibility consumers require the old shape, introduce a source-specific
adapter first and remove duplication only for
`source="question_evaluation_record"` until tests prove safe migration.

## Task 12: Capture Provider Usage

### Files

Modify:

```text
app/services/llm.py
app/services/agent_runtime.py
```

Optionally create:

```text
app/services/provider_usage.py
```

### Test First

Cover `usage_metadata`, provider-specific response metadata, missing usage,
stream-final usage, streams without usage, structured output, callback-based
capture, structured-first failure plus raw fallback, multiple provider attempts,
collector exceptions, and payload privacy.

### Implementation

Use provider/LangChain callbacks or a model wrapper for structured output where
the parsed Pydantic result hides the original AI message. Aggregate attempts
without losing the cost of a failed structured attempt. Do not fabricate actual
usage when it is unavailable. Usage-collection failure is best effort and does
not affect business output.

## Task 13: Extend Privacy-Safe Agent Metadata

### Files

Modify:

```text
app/services/trace_sanitization.py
app/services/agent_runtime.py
```

### Test First

Prove that exact numeric token-usage fields pass, string values under the same
keys fail, secret-bearing token keys remain blocked, prompt/answer/Resume/JD/
Evidence content remains blocked, and both metadata sanitization layers apply
the same rule. Prove that Context numeric, boolean, and string fields are
accepted only through their respective exact schema allowlists.

### Implementation

The existing blocked-key policy treats `token` as sensitive. Add an exact,
numeric-only allowlist for approved usage keys, for example:

```text
estimated_input_tokens
provider_input_tokens
provider_output_tokens
provider_total_tokens
available_input_tokens
context_window_tokens
budget_utilization_basis_points
```

Only non-negative finite numbers are accepted. Strings, lists, and dictionaries
under these keys are rejected. All other keys containing `token`, including
`api_token`, `access_token`, and `authorization_token`, remain blocked.

Define a complete Context Telemetry Schema rather than allowing arbitrary new
machine fields:

```python
AGENT_CONTEXT_NUMERIC_METADATA_KEYS = frozenset(
    {
        "estimated_input_tokens",
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_total_tokens",
        "available_input_tokens",
        "context_window_tokens",
        "budget_utilization_basis_points",
        "source_message_count",
        "selected_message_count",
        "dropped_message_count",
        "truncated_message_count",
        "source_evidence_count",
        "selected_evidence_count",
        "dropped_evidence_count",
        "truncated_evidence_count",
        "provider_attempt_count",
    }
)

AGENT_CONTEXT_BOOLEAN_METADATA_KEYS = frozenset(
    {
        "estimator_fallback_used",
        "deterministic_shrink_used",
        "provider_usage_available",
    }
)

AGENT_CONTEXT_STRING_METADATA_KEYS = frozenset(
    {
        "context_policy_version",
        "estimator_path",
        "report_path",
    }
)
```

Numeric keys accept only non-negative finite values, boolean keys accept only
actual booleans, and string keys continue to use the existing bounded machine
string rules. The token-name exception applies only to exact numeric usage
keys. Other token-bearing names remain blocked.

## Task 14: Add Runtime Failure Classification and Signals

### Files

Modify the existing runtime classification, dispatcher, worker, and signal
boundaries, including:

```text
app/services/runtime_work.py
app/services/runtime_outbox_dispatcher.py
app/services/report_worker.py
```

### Test First

Test classification and single-signal ownership for budget exceeded,
configuration error, estimator unavailable, estimator fallback, deterministic
shrink, Evidence/message truncation, budget-based Microbatch routing, missing
provider usage, and provider context overflow.

### Implementation

Add stable failures:

| Exception | Code | Retryable |
| --- | --- | ---: |
| `ContextBudgetExceeded` | `context_budget_exceeded` | No |
| `ContextConfigurationError` | `context_configuration_error` | No |
| `ContextEstimatorUnavailable` | `context_estimator_unavailable` | No |

Add aggregate signals:

```text
context_budget_exceeded
context_mandatory_content_overflow
context_estimator_fallback
context_estimator_unavailable
context_deterministic_shrink
context_evidence_truncated
context_message_truncated
report_microbatch_budget_route
provider_usage_missing
provider_context_overflow
```

Signals contain counts only. No IDs, payloads, prompts, or exception messages
are stored.

## Task 15: Extend Context Canary Evaluation

### Files

Modify:

```text
app/services/langgraph_canary_status.py
```

and its snapshot/evaluation/script tests.

### Test First

Test snapshot defaults, populated aggregate counts, privacy, hard failures,
HOLD conditions, healthy decisions, insufficient samples, and backward-safe
handling of absent historical metrics.

### Implementation

Add aggregate fields for budget exceeded, mandatory overflow, estimator
fallback/unavailability, deterministic shrink, message/Evidence truncation,
budget-based Microbatch routing, missing provider usage, and provider overflow.

ROLL_BACK conditions include invalid context configuration, provider context
overflow, privacy failures, determinism failures, or proven mandatory-content
drop. HOLD conditions include budget failures, estimator unavailability,
excessive missing usage, insufficient samples, abnormal truncation, or excessive
estimate/actual error. Expected deterministic truncation alone is not a hard
failure.

Percentiles may be added only if the current aggregate infrastructure can prove
them correctly. Otherwise record count/sum/max/sample count and leave percentile
work explicit for a follow-up.

## Task 16: Add Preflight, Acceptance, and Operator Documentation

### Files

Create:

```text
scripts/langgraph_stage49_acceptance.py
docs/langgraph-stage49-context-budget-acceptance.md
```

Update relevant environment examples and operator runbooks.

### Test First

Test invalid and valid configurations, unknown/custom models, impossible
budgets, estimator availability, numeric-only token metadata, blocked secrets,
feature-flag defaults, and privacy-safe artifact output.

### Implementation

Preflight verifies:

1. Context window resolution succeeds.
2. Unknown/custom providers have explicit windows.
3. Every operation has positive input and output limits.
4. All reserve values are valid.
5. The available input budget is positive and above the mandatory floor.
6. Primary and conservative estimators run.
7. Numeric usage metadata passes sanitization.
8. Secret-bearing token metadata remains blocked.
9. Every context policy has a version.
10. Context enforcement flags default to false.
11. Interview and Report rollout defaults remain zero.
12. Production observation remains `NOT_RUN`.

Recommended feature flags:

```dotenv
CONTEXT_BUDGET_SHADOW_ENABLED=false
CONTEXT_BUDGET_PREP_ENFORCEMENT=false
CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false
CONTEXT_BUDGET_REVIEW_ENFORCEMENT=false
CONTEXT_BUDGET_REPORT_ROUTING=false
```

---

## Initial Operation Policy Guidance

These values are starting points for shadow measurement, not universal model
truth. Effective input is always the minimum of model availability and the
operation cap.

### Prep Plan

```text
operation_input_cap_tokens = 24000
max_output_tokens = 4096
max_single_document_section_tokens = 3000
knowledge_metadata_total_tokens = 2000
```

### Interview Follow-up

```text
operation_input_cap_tokens = 12000
max_output_tokens = 512
max_single_message_tokens = 5000
max_total_evidence_tokens = 3500
max_evidence_item_tokens = 1200
max_evidence_items = 5
```

### Per-Question Review

```text
operation_input_cap_tokens = 16000
max_output_tokens = 2500
max_candidate_context_tokens = 7000
max_total_reference_tokens = 5000
max_reference_item_tokens = 1200
```

### Full-Session Report

```text
operation_input_cap_tokens = 24000
max_output_tokens = 4096
overflow_path = microbatch
```

### Report Reduce

```text
operation_input_cap_tokens = 20000
max_output_tokens = 4096
raw_messages = excluded_by_default
raw_evidence_payloads = excluded_by_default
```

Operation caps remain useful even for 128K models because they control cost,
latency, and unnecessary context dilution.

## Required Test Matrix

### Token and Budget Tests

- Exact budget boundary and one-token overflow.
- 4K, 8K, and 128K profiles.
- Chinese, English, code, JSON, UUID, Markdown, and emoji input.
- Unknown tokenizer and conservative fallback.
- Every estimator unavailable.
- Output reserve greater than or equal to the context window.
- Structured-output reserve and protocol reserve.
- Model input availability versus operation cap.

### Interview Tests

- Histories shorter and longer than four messages.
- Last four messages crossing question boundaries.
- Multi-turn current questions.
- Very long latest Chinese, code, and JSON answers.
- Current question and latest answer always represented.
- Complete turns retained and output chronology preserved.
- Older questions cannot displace mandatory current context.
- Legacy and Durable paths produce identical selections.
- Source State is not mutated.
- Repeated builds produce identical digest/stats.

### Knowledge Tests

- No Evidence, one short item, one oversized item, many small items.
- Per-item, item-count, and total-token limits.
- Stable Evidence order and retained source headers.
- Existing version, manifest, missing, and unavailable degradation paths.
- Repository content remains unchanged.
- No Evidence content in telemetry or exceptions.

### Prep Tests

- Short and oversized JD/Resume.
- Chinese, English, and mixed-language documents.
- Multiple projects and experience sections.
- Promotional/low-value JD sections.
- Many Knowledge candidates.
- Every major Resume section has bounded representation.
- Final prompt fits or fails before provider invocation.
- No source content in telemetry.

### Review and Report Tests

- Short/long/multi-turn/skipped/unanswered per-question inputs.
- Large, duplicate, and missing references.
- Exact candidate Evidence provenance.
- Truncation markers excluded from scoring Evidence.
- Knowledge content never counted as candidate Evidence.
- Full-Session under budget, exact boundary, and one-token overflow.
- Oversized Full-Session provider call count equals zero.
- Microbatch receives every question after routing.
- Reduce payloads no longer duplicate answer/rationale/critique/references.
- Final Report semantics and fallback behavior remain unchanged.

### Provider Usage Tests

- Usage in `usage_metadata` and provider response metadata.
- Missing usage.
- Streaming final usage and streams without usage.
- Structured output and callback capture.
- Structured-first failure plus raw fallback.
- Multiple attempts aggregated without losing failed-attempt cost.
- Usage collector failure does not affect business output.

### Privacy Tests

- Exact numeric usage keys accepted.
- Strings under numeric usage keys rejected.
- `api_token`, `access_token`, authorization, DSN, prompt, answer, Resume,
  JD, and Evidence content rejected.
- Context exceptions contain only safe machine fields.
- Runtime signals and canary output contain no IDs or payloads.
- File and PostgreSQL Agent Run recorders apply equivalent sanitization.

## Combined Context and Fault Matrix

| Scenario | Expected Result | Provider Calls |
| --- | --- | ---: |
| Short Interview context | Bounded original context | 1 |
| Long Interview history | Old turns dropped, latest answer retained | 1 |
| Oversized latest answer | Stable bounded representation | 1 |
| Mandatory minimum cannot fit | `ContextBudgetExceeded` | 0 |
| Oversized Evidence item | Stable item truncation | 1 |
| Excessive Evidence total | Stable subset/representation | 1 |
| Unknown tokenizer | Conservative fallback | 1 |
| All estimators fail | `ContextEstimatorUnavailable` | 0 |
| Full-Session fits | Full-Session path | 1 |
| Full-Session overflows | Microbatch path | Full-Session 0 |
| Structured Report fails, raw succeeds | Two separately measured attempts | 2 |
| Provider usage missing | Business result unchanged, signal recorded | Unchanged |
| Telemetry fails | Business result unchanged | Unchanged |
| Invalid context config | Preflight/config failure | 0 |
| Replay receives identical input | Identical route/digest/stats | Identical |
| Provider reports context overflow | Stable hard signal and failure | 1 if already called |

## Rollout Plan

### Phase 0: Disabled Defaults

All Stage 49 enforcement and shadow flags remain false. Existing Interview and
Report rollout percentages remain zero.

### Phase 1: Shadow Measurement

Enable only `CONTEXT_BUDGET_SHADOW_ENABLED`. Continue sending the legacy
provider input while computing the proposed selection and metrics. Do not log
or persist old/new prompts and do not call an additional LLM.

Observe would-truncate count, would-route count, estimated usage, utilization,
and estimator fallback rate.

### Phase 2: Prep Enforcement

Enable bounded Prep first because frequency is lower and plan quality can be
compared directly. Observe Plan quality, source-section coverage, latency,
estimated/actual usage, and fallback/error rate.

### Phase 3: Interview Enforcement

Enable a small Interview percentage. Observe follow-up relevance, latest-answer
retention, truncation, generation retry/failure, latency, provider usage, and
budget incidents.

### Phase 4: Per-Question Review Enforcement

Observe scoring drift, exact Evidence integrity, references, per-question
usage, provider timeout, and Report failure rate.

### Phase 5: Report Routing

Enable budget-based Full-Session-to-Microbatch routing. Observe route rate,
total provider attempts, question reuse, completion latency, and final-report
quality.

### Phase 6: Joint Context Canary

Run Interview and Review together through the required observation and drain
windows. Repository readiness alone does not authorize production rollout.

## Final Repository Gates

Run in this order:

1. Model capability, estimator, budget, selector, and sanitization unit tests.
2. Prep, Interview, and Knowledge targeted tests.
3. Per-question Review, Full-Session, Microbatch, Report Reduce, and provider
   usage targeted tests.
4. PostgreSQL Agent Run/signal/canary tests if aggregate queries change.
5. Stage 46-48 ownership, recovery, fencing, heartbeat, telemetry, privacy, and
   connection-capacity regressions.
6. Combined context/fault matrix.
7. Full Python regression.
8. Existing browser smoke/E2E gates. Stage 49 should not require screenshots.
9. Privacy scan and `git diff --check`.
10. `python -m scripts.langgraph_stage49_acceptance`.

The acceptance command must report:

```text
READY_FOR_CONTEXT_BUDGET_CANARY
PRODUCTION_OBSERVATION=NOT_RUN
```

It must not report production `PASS` without separately authorized observation.

## Definition of Done

Stage 49 is complete only when all of the following are true:

1. Every major LLM operation has an explicit versioned Context Policy.
2. Every production model has a resolvable explicit context window.
3. Unknown/custom providers cannot silently assume a window.
4. Every operation has an enforced output maximum.
5. Every final rendered prompt is measured before invocation.
6. No known-oversized request is sent to a provider.
7. Fixed `messages[-4:]` is no longer the Interview safety mechanism.
8. Current question and latest candidate answer always have a representation.
9. Knowledge Evidence has item-count, per-item, and total budgets.
10. Prep JD/Resume inputs are deterministically bounded.
11. Per-question Review has an independent budget.
12. Oversized Full-Session Review routes to Microbatch before invocation.
13. Microbatch Reduce no longer duplicates the same text/references.
14. Estimated usage and provider-reported actual usage remain distinct.
15. Missing provider usage does not fabricate actual values.
16. Numeric usage telemetry passes exact numeric-only privacy rules.
17. Context signals and canary output contain no source payloads or IDs.
18. Legacy messages without `question_id` follow the conservative, tested
    grouping contract and never become unverified current-question Evidence.
19. Reduce-stage observed excerpts carry verified source provenance.
20. Replay produces identical selection, route, digest, and statistics.
21. Stage 46-48 regressions pass.
22. Committed rollout defaults remain zero.
23. Production observation remains `NOT_RUN`.
24. Repository status is `READY_FOR_CONTEXT_BUDGET_CANARY`.

## Post-Stage-49 Backlog

The next stage is:

```text
Stage 50 - Durable Context Compression Artifacts
```

It will add:

- `QuestionConversationArtifact`.
- `EvidenceCompressionArtifact`.
- `PrepContextArtifact`.
- Stable source/policy/model/target-budget identities.
- Write-once compression effects.
- Claim tokens, leases, heartbeats, and fencing versions.
- Process-loss recovery and replay reuse.
- Artifact conflict detection.
- Context Artifact retention and cleanup.
- State references/digests without raw compression payloads.

The Stage 50 plan must include an explicit LangGraph checkpoint/effect recovery
matrix:

### Artifact Complete Before Checkpoint

```text
claim artifact
-> compression provider succeeds
-> artifact completes durably
-> process dies before LangGraph checkpoint
-> replay resolves the same artifact identity
-> completed artifact digest is verified and reused
-> compression provider total call count remains 1
```

### Checkpoint Complete Before the Next Node

```text
artifact completes
-> checkpoint stores artifact ref + digest
-> process dies
-> recovery loads the checkpoint
-> next node verifies and uses the completed artifact
-> no compression replay
```

### Provider Returned Before Artifact Completion

```text
provider has returned and charged
-> process dies before artifact complete is durable
-> lease expires and a new owner reclaims
-> provider may be called again
```

Stage 50 must not claim exactly-once external provider invocation for this
boundary unless the provider supports a durable idempotency key or the raw
response is durably captured before process loss. Write-once artifacts guarantee
reuse only after completion is durable.

### Stale Owner After Reclaim

```text
owner A holds fencing version 7
-> owner B reclaims version 8 and completes
-> owner A resumes
-> version-7 complete/update is rejected
-> owner A must not emit a stale LangGraph state patch
```

Before returning an artifact ref/digest state patch, the current owner must
either prove ownership or re-read and verify the authoritative completed
artifact identity and digest.

### Missing or Conflicting Checkpoint Reference

If a checkpoint references a missing artifact, raise a stable
`ContextArtifactMissing` recovery error unless an explicit rebuild policy
authorizes a new identity. If the checkpoint digest and artifact digest differ,
raise non-retryable `ContextArtifactConflict`. Neither case may silently
regenerate and pretend to be the same artifact.

Stage 50 call-count tests must prove:

```text
completed artifact before checkpoint loss -> total provider calls = 1
completed artifact replay -> replay provider calls = 0
provider returned but completion was not durable -> calls may be 2
stale owner completion -> SQL update count = 0 and no stale state patch
```

Stage 50 must not begin until Stage 49 deterministic budgeting and Context
Canary evidence are complete.

## Post-Stage-50 Backlog

The following optimization is intentionally not a Stage 49 correctness
dependency:

```text
Stage 51 - Provider Prompt Caching and Token Cost Optimization
```

Stage 49 should preserve stable prompt prefixes, deterministic ordering,
canonical JSON serialization, stable policy text, and clear fixed/dynamic
boundaries so later provider caching remains possible. It does not claim cache
hits because provider behavior may depend on role separation, model/settings,
tool or schema definitions, byte-identical prefixes, cache-control APIs,
minimum token thresholds, TTL, and account scope.

Stage 51 should add provider cache capability profiles and distinguish:

```text
provider_input_tokens
provider_cached_input_tokens
provider_cache_write_tokens
provider_output_tokens
cache_hit_count
cache_miss_count
```

Cached usage remains provider-reported actual usage and must not be inferred
from prompt similarity alone.
