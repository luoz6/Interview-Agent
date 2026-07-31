# Interview Agent Memory System Optimization Implementation Plan

**Plan revision:** v1.1, based on
`docs/interview-agent-memory-system-optimization-spec.md` v1.1.1-draft.

**v1.1 review amendments:** add Task 10 lazy-creation race coverage; define
Task 11 readiness behavior precisely; make Task 8 migration ordering explicit;
record the Task 1/Task 15 shared static-client edit; and add an automated check
that every requirement ID referenced by this plan exists in the pinned Spec.

> **Execution note:** This plan authorizes repository implementation work only
> when the user explicitly asks to execute it. Writing this plan does not
> authorize production rollout, real-provider compression calls, destructive
> cleanup, or migration of existing checkpoints. Implement tasks in order,
> begin every task with the stated failing or characterization test, preserve
> all unrelated tracked and untracked worktree changes, and keep rollout,
> enforcement, and compression-consumption defaults disabled.

**Goal:** Correct the current `langgraph-v2` dispatch and model-budget defects,
then replace unconditional full-history conversation compression with a
budget-authorized, fallback-safe, incrementally indexed Question Memory path.
Add configuration governance, knowledge-coverage gates, lifecycle deletion,
privacy-safe observability, multilingual budget evidence, frontend degradation
semantics, and an independent repository acceptance gate.

**Architecture:** Original session messages remain authoritative. The runtime
resolves one immutable effective memory configuration, dispatches legacy and
durable sessions through one shared predicate, resolves the actual model input
budget, performs deterministic selection, and only creates or retrieves
Question Memory when complete historical turns would otherwise be lost or
excessively truncated. Question Memory artifacts are immutable and owner-bound;
the business index keeps only bounded taxonomy metadata and opaque references.
Recoverable compression failures use deterministic context. Ownership,
fencing, identity, and scope failures remain fail-closed.

**Tech stack:** Python 3.11, FastAPI, Pydantic v2, LangGraph, LangChain
`ChatOpenAI`, PostgreSQL, psycopg2 connection domains, pgvector, React/Vite,
Playwright, pytest, the existing Context Artifact claim/lease/fencing runtime,
the existing Agent Run ledger, and runtime signal metrics.

**Repository baseline at plan authoring:**

- `HEAD` contains Stage 50 Context Artifact and Interview Graph v2 support.
- The working tree contains unrelated user changes and a new untracked memory
  optimization Spec. All implementation work must preserve them.
- `INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0` by committed default.
- Context Budget enforcement and Context Compression consumption are disabled
  by committed default.
- Stage 49 repository acceptance exists, but production observation is still
  `NOT_RUN`.
- The active Chinese v2 knowledge manifest reports 25 chunks and corpus version
  `stage44b1-zh-v2`; the implementation plan must not repeat an obsolete
  10-chunk assumption.
- `KNOWLEDGE_COVERED_TAGS` remains hard-coded in
  `app/services/knowledge_profile.py` and explicitly notes future manifest
  derivation.

---

## 1. Execution Preconditions

1. Preserve all user changes. Do not run destructive reset, checkout, clean,
   broad delete, or force-push commands.
2. Keep these committed deployment defaults unchanged until a later production
   observation task explicitly authorizes a change:

   ```dotenv
   INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
   REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
   INTERVIEW_LANGGRAPH_VERSION=langgraph-v1
   REPORT_LANGGRAPH_VERSION=langgraph-review-v1
   CONTEXT_BUDGET_SHADOW_ENABLED=false
   CONTEXT_BUDGET_PREP_ENFORCEMENT=false
   CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false
   CONTEXT_BUDGET_REVIEW_ENFORCEMENT=false
   CONTEXT_BUDGET_REPORT_ROUTING=false
   CONTEXT_COMPRESSION_SHADOW_ENABLED=false
   CONTEXT_COMPRESSION_PREP_ENABLED=false
   CONTEXT_COMPRESSION_INTERVIEW_ENABLED=false
   CONTEXT_COMPRESSION_EVIDENCE_ENABLED=false
   CONTEXT_COMPRESSION_REVIEW_ENABLED=false
   ```

3. Use deterministic fake compression providers in unit, integration, recovery,
   and repository acceptance tests. Do not call a real LLM during Tasks 1-18.
4. Run PostgreSQL tests only with an isolated table prefix against the approved
   local or test database.
5. Runtime constructors must use `schema_mode="validate"`; DDL remains owned by
   `app/services/postgres_runtime_migrations.py` and explicit migration entry
   points.
6. Do not rewrite historical LangGraph checkpoints. Existing v1 and v2
   checkpoints must remain readable.
7. Do not place prompts, answers, JD/Resume text, summaries, exact excerpts,
   Evidence content, artifact refs, source IDs, session IDs, credentials, or
   DSNs into telemetry, traces, canary JSON, exception messages, or acceptance
   artifacts.
8. Repository readiness does not authorize production consumption. Production
   rollout remains blocked until the plan reaches its explicit canary phase and
   receives a separate approval.

---

## 2. Scope

### 2.1 In Scope

- P0 durable HTTP dispatch correction.
- Model-resolved context budget propagation.
- Chinese, English, and mixed-language estimator evidence.
- Unified effective memory configuration and legacy environment adapter.
- Deterministic compression eligibility.
- Conversation artifact deterministic fallback.
- Versioned Question Memory contracts with exact-excerpt evidence.
- Canonical ordered source manifests and supersede semantics.
- PostgreSQL Question Memory index and migration.
- Immutable per-session memory-policy assignment for new sessions.
- Lazy, bounded, incremental Question Memory creation and retrieval.
- Knowledge coverage manifest derivation and minimum negative/boundary baseline.
- In-memory TTL and retention primitives.
- End-to-end session deletion job and API.
- Privacy-safe route, cost, latency, multilingual, storage, and deletion metrics.
- React candidate degradation experience and browser coverage.
- Full migration to `EffectiveMemoryConfig` after field stabilization.
- Stage 50 memory optimization acceptance runner and acceptance record.

### 2.2 Explicitly Out of Scope

- Automatic production rollout above zero.
- Real-provider production observation.
- Cross-session Principal Memory implementation.
- Public Knowledge Corpus auto-learning from candidate answers or scores.
- Large-scale corpus expansion beyond the P1 coverage baseline.
- Recursive summary trees, RAPTOR, or vectorized personal memory.
- Replacing original messages with summaries.
- Using summaries as exact scoring Evidence.
- Automatic migration of existing checkpoints into a new graph schema.
- Retiring legacy, `langgraph-v1`, `langgraph-v2`, or
  `langgraph-review-v1`.

P2 work remains documented in the Spec and requires a separate execution plan.

---

## 3. Fixed Implementation Decisions

### Decision 1: Keep `langgraph-v2`; add an immutable memory policy assignment

Do not introduce `langgraph-v3` solely for Question Memory. Add an immutable
`memory_policy_version` to the business session shell and bootstrap state:

```text
deterministic-v1
question-conversation-v1
question-memory-v1
```

Existing rows receive a migration-safe compatibility assignment. New sessions
receive `question-memory-v1` only when the new effective configuration and
rollout explicitly select it. This keeps existing v2 checkpoints readable and
prevents a global configuration change from silently changing an active
session's memory semantics.

### Decision 2: Generate Question Memory lazily under generation ownership

The first implementation does not create a separate asynchronous memory worker.
During a follow-up generation, the coordinator may create at most one missing
Question Memory artifact for the highest-ranked closed historical question.
It reuses existing active index entries for all other retrieved questions.
This preserves the existing generation parent-ownership and fencing model,
bounds provider calls, and avoids a new unowned effect queue.

### Decision 3: Controlled taxonomy is plaintext; candidate text is not

The Question Memory index may store versioned taxonomy values such as
`distributed_systems`, `idempotency`, and `missing_tradeoff`. It must not store
free-form candidate statements, project names, employer names, summaries, or
exact excerpts. Digests remain present for source integrity.

### Decision 4: Every LLM summary is non-authoritative

Programmable validation covers schema, source anchors, exact excerpts, numbers,
identifiers, and explicitly encoded relation fields. It does not prove full
natural-language semantic equivalence. Accepted Question Memory payloads carry
`authority="non_authoritative"` and cannot enter scoring Evidence.

### Decision 5: Knowledge baseline blocks consumption; corpus scale does not

Manifest-derived coverage tags and a minimum approved negative/boundary corpus
for supported core roles are P1. Broad role expansion and corpus scale remain
P2.

### Decision 6: Transparent artifact fallback is not a candidate error

If deterministic context still produces a normal follow-up, the candidate UI
does not show an error. A user notice is required only when the business
follow-up falls back to a basic template or another material capability loss is
visible.

---

## 4. Task Dependency Graph

```text
Task 0  Baseline and worktree audit
  ├─ Task 1  Durable HTTP dispatch
  ├─ Task 2  Resolved budget propagation
  │    └─ Task 3  Multilingual estimator evidence
  └─ Task 4  Effective memory config foundation

Task 2 + Task 4
  └─ Task 5  Compression eligibility
       └─ Task 6  Conversation fallback
            └─ Task 7  Question Memory contracts
                 └─ Task 8  Session memory-policy assignment
                      └─ Task 9  Question Memory index migration/store
                           └─ Task 10 Incremental coordinator and graph integration

Task 7 ────────────────┐
Task 10 ───────────────┼─ Task 11 Knowledge coverage baseline
Task 4 ────────────────┘

Task 4 ── Task 12 Retention and in-memory TTL
Task 9 + Task 12 ── Task 13 End-to-end purge

Task 1 through Task 13
  ├─ Task 14 Privacy-safe observability
  ├─ Task 15 Frontend degradation experience
  └─ Task 16 Complete config migration

Task 1 through Task 16
  └─ Task 17 Stage 50 memory acceptance
       └─ Task 18 Full regression and release record
```

Tasks may be implemented in parallel only where the graph shows independent
branches and only when the working-tree edits do not overlap.

---

## 5. Verification Conventions

Use the documented Python interpreter:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

The command above is the full-suite form. Every task below provides its exact
focused test command and file list; use that focused command before the full
suite.

Use these standard checks at task boundaries:

```powershell
& 'F:\python3.11\python.exe' -m compileall app scripts tests
git diff --check
```

PostgreSQL tests must carry the repository's existing markers and isolated
prefix setup. Browser verification uses the existing npm scripts and Playwright
configuration rather than a new browser harness.

Every task ends with one focused commit after its tests pass. Do not mix
unrelated user changes into those commits. Suggested commit messages are part
of the plan; an executor may adjust wording while preserving task boundaries.

---

## Task 0: Capture the Characterization Baseline

**Purpose:** Prove the current defects, freeze the safety defaults, and create a
repeatable baseline before changing source code.

**Files:**

- Modify: `tests/test_api.py`
- Modify: `tests/test_context_selection.py`
- Modify: `tests/test_context_compression_validation.py`
- Modify: `tests/test_local_v1_docs.py`
- Reference: `docs/interview-agent-memory-system-optimization-spec.md`

### Step 1: Add a failing v2 dispatch characterization

Add API-level tests that create or inject a session with:

```python
{
    "workflow_engine": "langgraph-v2",
    "graph_schema_version": "langgraph-v2",
}
```

Assert that snapshot and each mutation call the durable workflow service. The
current implementation should fail because routes compare only with
`"langgraph-v1"`.

### Step 2: Add a failing small-window selection characterization

Construct a model profile whose resolved available input is smaller than
`FOLLOWUP_CONTEXT_POLICY.input_cap_tokens`. Assert that the selected message
estimate is no greater than the resolved selectable budget. The current
implementation should fail because `build_interview_context()` uses the policy
cap.

### Step 3: Preserve the semantic-validation limitation as a characterization

Add a payload with valid source anchor and exact excerpt but an unsupported
free-language summary. Assert that the current programmable validator does not
claim semantic authority. Do not assert that the validator can automatically
detect every unsupported natural-language fact.

### Step 4: Freeze committed safe defaults

Extend the documentation/config contract test to assert that rollout,
enforcement, and compression consumption remain disabled by default.

### Step 5: Run the baseline tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_context_selection.py tests/test_context_compression_validation.py tests/test_local_v1_docs.py -q
```

Expected: the new v2 dispatch and resolved-budget tests fail for the intended
reasons; existing default-safety tests pass.

### Step 6: Record the failing evidence

Do not commit a permanently red branch. Keep the tests in the same working
sequence and proceed directly to Tasks 1 and 2, which make them pass. Record
the exact failing test names in the implementation log or commit body.

---

## Task 1: Unify Durable HTTP and Client Dispatch

**Spec coverage:** `MEM-DSP-001` through `MEM-DSP-010`.

**Files:**

- Modify: `app/api/routes.py`
- Modify: `app/graphs/interview_state.py`
- Modify: `app/static/interview.js`
- Modify: `tests/test_api.py`
- Modify: `tests/test_dual_langgraph_rollout.py`
- Modify: `tests/test_page_routes.py`
- Modify: `tests/browser_support_app.py`
- Modify: `tests/browser/langgraph-recovery.spec.js`

### Step 1: Centralize the durable predicate

Use `is_durable_interview_version()` as the only engine predicate. If API
imports would create a cycle, expose a thin helper in
`app/graphs/interview_state.py` or a dependency-neutral module. Do not copy a
set of version strings into routes or frontend code.

### Step 2: Replace all route string comparisons

Update snapshot, answer, answer stream, skip, and finish. Durable sessions must
call `InterviewWorkflowService`; legacy sessions must call the session store.

### Step 3: Correct the legacy static client compatibility check

`app/static/interview.js` currently treats only `langgraph-v1` as durable.
Change the client predicate to accept both durable versions without changing
the server-authoritative routing contract. Keep the React client behavior
characterized; update it only if tests show a version-specific assumption.

### Step 4: Extend the browser support app

Teach the fake support app to emit `langgraph-v2` snapshots and accepted command
responses. Add a browser test proving refresh and SSE resume use the durable
contract for v2.

### Step 5: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_dual_langgraph_rollout.py tests/test_page_routes.py -q
npm run test:browser -- --grep "langgraph-v2 durable dispatch"
```

Expected: all pass; no v2 mutation reaches a legacy store method.

### Step 6: Commit

```powershell
git add app/api/routes.py app/graphs/interview_state.py app/static/interview.js tests/test_api.py tests/test_dual_langgraph_rollout.py tests/test_page_routes.py tests/browser_support_app.py tests/browser/langgraph-recovery.spec.js
git commit -m "fix(memory): route all durable interview versions consistently"
```

---

## Task 2: Propagate the Resolved Model Input Budget

**Spec coverage:** `MEM-BUD-001` through `MEM-BUD-010`,
`MEM-SEL-001` through `MEM-SEL-010`.

**Files:**

- Modify: `app/services/context_budget.py`
- Modify: `app/services/context_selection.py`
- Modify: `app/graphs/interview_graph.py`
- Modify: `app/graphs/durable_interview_graph.py`
- Modify: `app/services/llm.py`
- Modify: `tests/test_context_budget.py`
- Modify: `tests/test_context_selection.py`
- Modify: `tests/test_context_enforcement.py`
- Modify: `tests/test_interview_graph.py`
- Modify: `tests/test_durable_interview_graph.py`

### Step 1: Introduce a resolved selection budget

Add an immutable value object that separates fixed prompt reserve from content
selection budget. A complete contract should include:

```python
@dataclass(frozen=True)
class ContextSelectionBudget:
    available_input_tokens: int
    fixed_prompt_reserve_tokens: int
    mandatory_content_floor_tokens: int

    @property
    def selectable_content_tokens(self) -> int:
        value = self.available_input_tokens - self.fixed_prompt_reserve_tokens
        return max(self.mandatory_content_floor_tokens, value)
```

Validate all fields and reject configurations whose fixed reserve leaves less
than the mandatory floor.

### Step 2: Change the selector API

Replace policy-cap-derived total allocation with the resolved selection budget.
Continue using policy values for per-message, per-Evidence, and item-count caps.
Calculate conversation and Evidence allocations from
`selectable_content_tokens`.

### Step 3: Update all follow-up call sites

Resolve `ContextBudget` from `ContextRuntime`, derive the selection budget, and
pass it to both legacy and durable graph context builders. Do not let call sites
reimplement reserve arithmetic.

### Step 4: Preserve final prompt enforcement

Keep `RenderedPromptGuard` authoritative. When enforcement is enabled and the
first render is over budget, apply the existing deterministic shrink strategy
or a new bounded re-selection pass before returning `ContextBudgetExceeded`.
Never call the provider with a known-over-budget prompt.

### Step 5: Add exact boundary tests

Cover:

- available budget smaller than operation cap;
- fixed reserve leaving exactly the mandatory floor;
- latest candidate answer retained under a tiny budget;
- Evidence allocation shrinking after conversation mandatory content;
- final prompt one token under, equal to, and one token over budget;
- enforcement disabled still publishing measurements.

### Step 6: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_context_budget.py tests/test_context_selection.py tests/test_context_enforcement.py tests/test_interview_graph.py tests/test_durable_interview_graph.py -q -m "not pg_runtime"
```

Expected: all pass; the Task 0 small-window test is green.

### Step 7: Commit

```powershell
git add app/services/context_budget.py app/services/context_selection.py app/graphs/interview_graph.py app/graphs/durable_interview_graph.py app/services/llm.py tests/test_context_budget.py tests/test_context_selection.py tests/test_context_enforcement.py tests/test_interview_graph.py tests/test_durable_interview_graph.py
git commit -m "fix(memory): select context from the resolved model budget"
```

---

## Task 3: Add Multilingual Estimator Evidence

**Spec coverage:** `MEM-BUD-011` through `MEM-BUD-014`,
`MEM-TST-020` through `MEM-TST-025`.

**Files:**

- Create: `app/services/context_language.py`
- Modify: `app/services/token_estimation.py`
- Modify: `app/services/provider_usage.py`
- Modify: `app/services/trace_sanitization.py`
- Create: `tests/test_context_language.py`
- Modify: `tests/test_context_selection.py`
- Modify: `tests/test_context_enforcement.py`
- Modify: `tests/test_utf8_text_contract.py`

### Step 1: Define a safe language bucket

Implement a deterministic, content-local classifier returning only:

```text
zh_hans
en
mixed
other
unknown
```

It must not persist samples or identifiers. Classification failure returns
`unknown` and never blocks the provider call.

### Step 2: Publish aggregate-safe metadata

Allow `language_bucket` only as a safe enum in provider context metadata. Add
numeric estimator error fields only when provider usage exists. Do not attach
the bucket to session-level trace output that permits individual correlation.

### Step 3: Add multilingual truncation tests

Use fixed Chinese, English, mixed technical, numeric, and code-containing
fixtures. Assert valid UTF-8, complete omission markers, mandatory-message
retention, and bounded estimates.

### Step 4: Add estimator comparison tests

For deterministic fake provider usage, calculate under- and over-estimation in
basis points per bucket. Assert that low sample counts produce an
`insufficient_sample` aggregate state in the metrics layer added later; at this
task, verify only the underlying normalized measurement contract.

### Step 5: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_context_language.py tests/test_context_selection.py tests/test_context_enforcement.py tests/test_utf8_text_contract.py -q
```

Expected: all pass with no raw fixture text in safe metadata snapshots.

### Step 6: Commit

```powershell
git add app/services/context_language.py app/services/token_estimation.py app/services/provider_usage.py app/services/trace_sanitization.py tests/test_context_language.py tests/test_context_selection.py tests/test_context_enforcement.py tests/test_utf8_text_contract.py
git commit -m "feat(memory): measure context estimates across language buckets"
```

---

## Task 4: Build the Effective Memory Configuration Foundation

**Spec coverage:** `MEM-CFG-001` through `MEM-CFG-010`, WP-8a.

**Files:**

- Create: `app/services/memory_config.py`
- Modify: `app/services/config.py`
- Modify: `app/services/runtime.py`
- Modify: `app/api/routes.py`
- Modify: `.env.example`
- Create: `tests/test_memory_config.py`
- Modify: `tests/test_runtime_boundary_api.py`
- Modify: `tests/test_local_v1_docs.py`

### Step 1: Write failing config precedence tests

Cover all four cases:

1. new structured value only;
2. legacy environment value only;
3. both present and equal after normalization;
4. both present and conflicting.

The fourth case must fail startup/config resolution. It must not silently choose
the more aggressive value.

### Step 2: Implement `EffectiveMemoryConfig`

Use nested frozen Pydantic models for interview graph, model budget,
enforcement, compression, selection, artifact, retention, and privacy. Include
a stable `schema_version` and a boolean indicating whether any legacy variable
was consumed.

### Step 3: Implement the legacy environment adapter

Map every variable listed in Spec section 11.4. Normalize booleans, percentages,
positive integers, enums, and deployment identifiers. Emit only variable names
in deprecation warnings, never secret or configured values.

### Step 4: Add preflight invariants

Reject:

- rollout above zero with runtime disabled;
- unsupported graph versions;
- compression consume without Interview budget enforcement;
- Evidence consumption without Interview/Review workflow compression;
- compression consume without an artifact store;
- unknown custom models without explicit context window;
- invalid retention, lease, or cleanup values.

### Step 5: Wire readiness without migrating every consumer

At WP-8a, runtime readiness and new memory components use
`EffectiveMemoryConfig`; old getters may delegate to the adapter. Do not yet
remove all legacy getters because Question Memory fields stabilize in later
tasks.

### Step 6: Update environment and runbook contracts

Document safe defaults, the new path names, conflict behavior, and the staged
deprecation policy. Keep legacy environment examples during the compatibility
window.

### Step 7: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_config.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py -q
```

Expected: all pass; invalid configurations fail before runtime services start.

### Step 8: Commit

```powershell
git add app/services/memory_config.py app/services/config.py app/services/runtime.py app/api/routes.py .env.example tests/test_memory_config.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py
git commit -m "feat(memory): centralize effective runtime configuration"
```

---

## Task 5: Implement Deterministic Compression Eligibility

**Spec coverage:** `MEM-ART-001` through `MEM-ART-007`.

**Files:**

- Create: `app/services/context_compression_eligibility.py`
- Modify: `app/services/context_selection.py`
- Modify: `app/services/interview_context_artifacts.py`
- Modify: `app/services/evidence_context_artifacts.py`
- Create: `tests/test_context_compression_eligibility.py`
- Create: `tests/test_interview_context_artifacts.py`
- Modify: `tests/test_evidence_context_artifacts.py`

### Step 1: Define stable reasons

Implement a closed enum for:

```text
older_complete_turn_would_drop
older_complete_turn_excessively_truncated
unresolved_topic_coverage_loss
evidence_representation_excessive_truncation
prep_section_coverage_loss
review_continuity_would_drop
```

Do not use free-form reason strings.

### Step 2: Define an immutable eligibility result

Include:

- eligible boolean;
- stable reason or null;
- source unit count;
- dropped/truncated counts;
- target artifact type;
- policy version;
- a safe source-manifest digest, used internally but not emitted to telemetry.

### Step 3: Evaluate after deterministic selection

Call eligibility only after the selector returns stats. Short contexts with no
drop or excessive truncation return `eligible=false` and route
`deterministic`. Shadow mode may create only when eligible; it may not restore
the old unconditional behavior.

### Step 4: Integrate Conversation and Evidence coordinators

Pass deterministic selection stats into coordinators. Evidence keeps its
independent gate. Existing direct tests that construct coordinators must inject
an eligibility policy or use a deterministic default.

### Step 5: Add no-call tests

Use a compressor fake that fails the test if called. Prove:

- short context does not call it;
- all messages fit does not call it;
- only mandatory current messages exist does not call it;
- stable dropped older turn calls it once when creation is enabled;
- shadow and consume derive the same eligibility decision.

### Step 6: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_context_compression_eligibility.py tests/test_interview_context_artifacts.py tests/test_evidence_context_artifacts.py tests/test_context_selection.py -q
```

Expected: all pass; compressor call count is zero for non-eligible contexts.

### Step 7: Commit

```powershell
git add app/services/context_compression_eligibility.py app/services/context_selection.py app/services/interview_context_artifacts.py app/services/evidence_context_artifacts.py tests/test_context_compression_eligibility.py tests/test_interview_context_artifacts.py tests/test_evidence_context_artifacts.py
git commit -m "feat(memory): gate compression on deterministic context loss"
```

---

## Task 6: Add Conversation Artifact Deterministic Fallback

**Spec coverage:** `MEM-ART-020` through `MEM-ART-029`.

**Files:**

- Modify: `app/services/interview_context_artifacts.py`
- Modify: `app/services/runtime_work.py`
- Modify: `app/graphs/durable_interview_state_v2.py`
- Modify: `app/graphs/durable_interview_graph.py`
- Modify after Task 5: `tests/test_interview_context_artifacts.py`
- Modify: `tests/test_runtime_work.py`
- Modify: `tests/test_durable_interview_graph.py`

### Step 1: Add failing recoverable-error tests

Parameterize Conversation artifact resolution with:

- `ContextArtifactBusy`;
- `ContextArtifactProviderFailed`;
- `ContextArtifactValidationFailed`.

Assert that each returns the original deterministic context and route
`artifact_fallback`.

### Step 2: Preserve fail-closed errors

Add separate tests proving these errors propagate:

- `ContextArtifactLeaseLost` when completed recovery is unavailable;
- `ContextArtifactConflict`;
- `GenerationLeaseLost` or parent ownership loss;
- owner-scope mismatch.

### Step 3: Implement the fallback boundary

Catch only recoverable artifact errors at the Conversation coordinator. Do not
catch broad `Exception`. Return no artifact ref on fallback and preserve the
deterministic context order and content.

### Step 4: Project the route safely

The graph may persist `context_route="artifact_fallback"` and stable safe code,
but it must not place exception messages, artifact IDs, source IDs, or prompt
content in checkpoint metadata.

### Step 5: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_context_artifacts.py tests/test_runtime_work.py tests/test_durable_interview_graph.py -q -m "not pg_runtime"
```

Expected: all pass; recoverable failures still complete the business follow-up
using deterministic context.

### Step 6: Commit

```powershell
git add app/services/interview_context_artifacts.py app/services/runtime_work.py app/graphs/durable_interview_state_v2.py app/graphs/durable_interview_graph.py tests/test_interview_context_artifacts.py tests/test_runtime_work.py tests/test_durable_interview_graph.py
git commit -m "fix(memory): fall back safely when conversation compression fails"
```

---

## Task 7: Add Versioned Question Memory Contracts

**Spec coverage:** `MEM-ART-010` through `MEM-ART-019`, `MEM-ART-030`,
`MEM-SUM-001` through `MEM-SUM-010`.

**Files:**

- Modify: `app/services/context_artifacts.py`
- Modify: `app/services/context_compression.py`
- Modify: `app/services/context_compression_validation.py`
- Modify: `app/agents/context_compressor.py`
- Modify: `app/ports/context_artifacts.py`
- Modify: `app/services/in_memory_context_artifact_store.py`
- Modify: `app/services/context_artifact_store.py`
- Modify: `tests/test_context_artifacts.py`
- Modify: `tests/test_context_artifact_contracts.py`
- Modify: `tests/test_context_compressor.py`
- Modify: `tests/test_context_compression_validation.py`
- Modify: `tests/test_context_compression_runner.py`
- Modify: `tests/test_in_memory_context_artifact_store.py`

### Step 1: Add `question_memory` without removing legacy types

Extend `ArtifactType`, schema inference, parser maps, purpose contracts, and
store checks to support `question_memory`. Keep `question_conversation`,
`evidence_compression`, and `prep_context` readable and executable.

### Step 2: Define the payload models

Add a versioned payload with these mandatory semantics:

```python
class QuestionMemoryClaim(BaseModel):
    claim_type: Literal[
        "decision",
        "tradeoff",
        "result",
        "skill",
        "constraint",
        "unresolved",
    ]
    summary: str
    polarity: Literal["positive", "negative", "uncertain", "mixed"]
    source_segment_sha256: list[str]
    supporting_excerpts: list[str] = Field(min_length=1)
    confidence: Literal["low", "medium", "high"]


class QuestionMemoryArtifact(BaseModel):
    schema_version: Literal["question-memory-v1"]
    authority: Literal["non_authoritative"]
    session_scope_sha256: str
    question_id_sha256: str
    question_focus_sha256: str
    source_manifest_sha256: str
    source_message_count: int
    claims: list[QuestionMemoryClaim]
    unresolved_topics: list[QuestionMemoryClaim]
```

The exact Pydantic base class must continue to reject unknown fields, NUL,
invalid UTF-8, invalid digests, duplicate anchors, and empty required strings.

### Step 3: Define the canonical ordered source manifest

Create a helper whose input is an ordered list of authoritative messages. Each
manifest item contains:

```python
{
    "sequence_no": 1,
    "role": "interviewer",
    "question_id_sha256": "a" * 64,
    "content_sha256": "b" * 64,
}
```

Sort by `sequence_no`, reject duplicate or non-positive sequence numbers, and
hash canonical JSON. Array order is part of the identity. The helper must not
return or log message content.

### Step 4: Enforce programmable validation

Validate:

- payload and policy schema versions;
- session, question, focus, and source-manifest digests;
- source message count;
- every claim has at least one source anchor and exact excerpt;
- every excerpt is a continuous substring of an anchored source;
- numeric and identifier subsets;
- polarity and claim type enums;
- output unit and token limits;
- `authority` is exactly `non_authoritative`.

Do not implement a rule that claims to prove unrestricted natural-language
semantic equivalence.

### Step 5: Add a semantic-boundary characterization

Use a valid anchored excerpt plus unsupported free-language prose. Assert that:

- the programmable validator does not label it authoritative;
- the payload authority remains `non_authoritative`;
- the payload cannot be converted into scoring Evidence by any adapter;
- offline quality evaluation, not the structural validator, owns new-fact-rate
  measurement.

### Step 6: Add compressor support

Add `QuestionMemoryArtifact` to the structured output map and create a dedicated
policy with new policy, prompt-contract, and output-schema versions. Require the
provider prompt to return exact excerpts and the fixed authority value.

### Step 7: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_context_artifacts.py tests/test_context_artifact_contracts.py tests/test_context_compressor.py tests/test_context_compression_validation.py tests/test_context_compression_runner.py tests/test_in_memory_context_artifact_store.py -q
```

Expected: all pass; legacy artifact fixtures remain valid.

### Step 8: Commit

```powershell
git add app/services/context_artifacts.py app/services/context_compression.py app/services/context_compression_validation.py app/agents/context_compressor.py app/ports/context_artifacts.py app/services/in_memory_context_artifact_store.py app/services/context_artifact_store.py tests/test_context_artifacts.py tests/test_context_artifact_contracts.py tests/test_context_compressor.py tests/test_context_compression_validation.py tests/test_context_compression_runner.py tests/test_in_memory_context_artifact_store.py
git commit -m "feat(memory): add non-authoritative question memory artifacts"
```

---

## Task 8: Persist an Immutable Session Memory Policy Assignment

**Purpose:** Prevent a global configuration change from changing memory
semantics for an already-created durable session.

**Files:**

- Modify: `app/graphs/interview_state.py`
- Modify: `app/graphs/durable_interview_state_v2.py`
- Modify: `app/services/session_serialization.py`
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/services/interview_workflow.py`
- Modify: `app/services/postgres_runtime_migrations.py`
- Modify: `app/services/runtime.py`
- Modify: `tests/test_session_serialization.py`
- Modify: `tests/test_session_service.py`
- Modify: `tests/test_postgres_session_store.py`
- Modify: `tests/test_postgres_runtime_migrations.py`
- Modify: `tests/test_durable_interview_state.py`
- Modify: `tests/test_dual_langgraph_rollout.py`

### Step 1: Define the assignment enum

Use a closed contract:

```text
deterministic-v1
question-conversation-v1
question-memory-v1
```

Add `memory_policy_version` to the business Interview State. For durable v2
state, make the field backward-readable: old checkpoints missing the field must
resolve to the migrated business assignment rather than failing deserialization.

### Step 2: Add migration-owned storage

Add a non-null session column with a safe migration sequence:

1. add nullable column;
2. backfill legacy and v1 rows to `deterministic-v1`;
3. backfill existing v2 rows to `question-conversation-v1`;
4. add allowed-value check constraint;
5. set non-null;
6. set default only if needed for migration compatibility, while application
   creation remains explicit.

Do not infer `question-memory-v1` for existing rows.

This migration is ordered after Task 1 and before deployment of the Task 8
application code. Backfilling `workflow_engine='langgraph-v2'` is valid even if
the target environment currently contains zero v2 rows; the update is
idempotent and may affect zero rows. The migration must not be applied from a
branch that lacks Task 1's durable-version handling and Task 8's backward-read
logic.

### Step 3: Assign policy only at session creation

`InterviewWorkflowService.start()` resolves the effective config once and
stores the assignment in the durable session shell and bootstrap input. Retry,
requeue, refresh, and global config changes must not alter it.

### Step 4: Project the assignment safely

Snapshots may return the stable policy version as safe metadata. Checkpoints
must not include policy payloads, config secrets, or environment values.

### Step 5: Add immutability tests

Cover:

- legacy, v1, existing v2, and new Question Memory assignments;
- serialization round-trip;
- migration backfill;
- global config change after session creation;
- duplicate start/bootstrap replay;
- unsupported stored value fails closed.

### Step 6: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_serialization.py tests/test_session_service.py tests/test_postgres_session_store.py tests/test_postgres_runtime_migrations.py tests/test_durable_interview_state.py tests/test_dual_langgraph_rollout.py -q -m "not pg_runtime"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py tests/test_postgres_runtime_migrations.py -q -m pg_runtime
```

Expected: all available environments pass; PostgreSQL-marked tests execute only
when the approved database is available.

### Step 7: Commit

```powershell
git add app/graphs/interview_state.py app/graphs/durable_interview_state_v2.py app/services/session_serialization.py app/services/session.py app/services/postgres_session.py app/services/interview_workflow.py app/services/postgres_runtime_migrations.py app/services/runtime.py tests/test_session_serialization.py tests/test_session_service.py tests/test_postgres_session_store.py tests/test_postgres_runtime_migrations.py tests/test_durable_interview_state.py tests/test_dual_langgraph_rollout.py
git commit -m "feat(memory): persist immutable session memory policy assignments"
```

---

## Task 9: Add the Question Memory Index Store and Migration

**Spec coverage:** Spec sections 8.3 and 10.1.

**Files:**

- Create: `app/ports/question_memory.py`
- Create: `app/services/question_memory_index.py`
- Create: `app/services/in_memory_question_memory_index.py`
- Create: `app/services/postgres_question_memory_index.py`
- Modify: `app/services/postgres_runtime_migrations.py`
- Modify: `app/services/runtime.py`
- Create: `tests/test_question_memory_index_contracts.py`
- Create: `tests/test_in_memory_question_memory_index.py`
- Create: `tests/test_postgres_question_memory_index.py`
- Modify: `tests/test_postgres_runtime_migrations.py`
- Modify: `tests/test_postgres_store_provider_injection.py`

### Step 1: Define the port

The port must support:

- atomically activating a new entry and superseding the previous active entry;
- loading the active entry for session/question/policy;
- listing bounded active entries for a session;
- loading a specific historical ref for checkpoint replay;
- marking all session entries deleted;
- deleting session index rows during purge.

It must not expose a method that mutates an existing artifact or summary.

### Step 2: Define the domain record

Include:

- session ID and question ID inside the business store only;
- question/focus digests;
- controlled `focus_tags`, `skill_tags`, and `unresolved_topic_codes`;
- corresponding source digests;
- artifact ref, digest, type, and policy version;
- source message count and maximum sequence number;
- status, timestamps, and `supersedes_artifact_ref`.

Validate taxonomy items against a versioned allowlist and reject free text.

### Step 3: Implement the in-memory store first

Use a lock and injectable clock. Atomically supersede the old active entry.
Prove there is at most one active entry per session/question/policy.

### Step 4: Add the PostgreSQL migration

Create the table and the partial unique index:

```sql
CREATE UNIQUE INDEX interview_question_memory_active_idx
ON interview_question_memory_refs (
    session_id,
    question_id,
    policy_version
)
WHERE status = 'active';
```

The migration implementation must derive both identifiers from the configured
runtime prefix using the repository's identifier helpers; the concrete SQL
above documents the default-prefix result.

Use prefixed identifiers, migration checksum, and validate mode. Add indexes for
bounded session retrieval and artifact-ref cleanup.

### Step 5: Implement the PostgreSQL store

Activation must lock the current active entry, mark it superseded, insert the
new entry, and commit in one transaction. A uniqueness race must produce a
stable conflict or retry path, not two active entries.

### Step 6: Add replay and deletion tests

Prove:

- ordinary retrieval ignores superseded and deleted entries;
- direct historical retrieval works for an authorized replay;
- a new source manifest supersedes the direct predecessor;
- session deletion prevents future ordinary retrieval;
- owner and artifact digests are preserved;
- connection-provider injection uses the business domain.

### Step 7: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_question_memory_index_contracts.py tests/test_in_memory_question_memory_index.py tests/test_postgres_question_memory_index.py tests/test_postgres_runtime_migrations.py tests/test_postgres_store_provider_injection.py -q -m "not pg_runtime"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_question_memory_index.py tests/test_postgres_runtime_migrations.py -q -m pg_runtime
```

Expected: one active entry is enforced in memory and PostgreSQL.

### Step 8: Commit

```powershell
git add app/ports/question_memory.py app/services/question_memory_index.py app/services/in_memory_question_memory_index.py app/services/postgres_question_memory_index.py app/services/postgres_runtime_migrations.py app/services/runtime.py tests/test_question_memory_index_contracts.py tests/test_in_memory_question_memory_index.py tests/test_postgres_question_memory_index.py tests/test_postgres_runtime_migrations.py tests/test_postgres_store_provider_injection.py
git commit -m "feat(memory): add superseding question memory index storage"
```

---

## Task 10: Integrate Incremental Question Memory into Interview v2

**Spec coverage:** WP-6, `MEM-ART-010` through `MEM-ART-019`, and
`MEM-ART-030`.

**Files:**

- Create: `app/services/question_memory.py`
- Create: `app/services/question_memory_retrieval.py`
- Modify: `app/services/interview_context_artifacts.py`
- Modify: `app/graphs/durable_interview_graph.py`
- Modify: `app/services/runtime.py`
- Modify after Task 5: `tests/test_interview_context_artifacts.py`
- Create: `tests/test_question_memory.py`
- Create: `tests/test_question_memory_retrieval.py`
- Create: `tests/test_question_memory_recovery.py`
- Modify: `tests/test_durable_interview_graph.py`
- Modify: `tests/test_dual_langgraph_rollout.py`

### Step 1: Identify closed historical questions deterministically

A question is eligible only when it is not the current question and its source
messages are authoritative in the session state. Skipped questions with no
candidate answer do not require a Question Memory artifact unless they contain
meaningful interviewer/candidate content under a future policy.

### Step 2: Build per-question source manifests

Enumerate the authoritative session message list to derive stable sequence
numbers. Group by explicit question ID, calculate message content digests, and
create the ordered manifest from Task 7. Do not use `messages[:-2]`.

### Step 3: Derive controlled retrieval taxonomy

Use plan question focus, kind, Prep Context topics, and a versioned allowlist.
Do not ask an LLM to generate index tags. Hash integrity fields separately from
the plaintext controlled taxonomy.

### Step 4: Retrieve before creating

Load active compatible entries for the session and rank them by:

1. exact focus tag overlap;
2. skill tag overlap;
3. unresolved topic code overlap;
4. recency by source maximum sequence number.

Limit count and total estimated tokens through the resolved selection budget.

### Step 5: Create at most one missing artifact per generation

When eligibility requires memory and the highest-ranked relevant closed
question has no active compatible entry, resolve one `question_memory` artifact
under the current generation's parent ownership. Activate the index only after
artifact completion and owner-ref validation.

### Step 6: Prove lazy-creation race and interruption safety

Add a dedicated recovery test module covering each boundary independently:

1. generation lease is lost after artifact claim but before the compressor
   provider call; the old worker must not call the provider or complete/index
   the artifact;
2. generation lease is lost after provider return but before artifact complete;
   stale completion must fail fencing and must not activate an index entry;
3. artifact complete succeeds and the process exits before owner-ref binding;
   replay must reuse the completed artifact, create one valid owner ref, and
   avoid a second provider call;
4. owner-ref binding succeeds and the process exits before index activation;
   replay must activate exactly one index entry;
5. two generation attempts concurrently target the same question manifest;
   artifact identity uniqueness and claim fencing must produce one completed
   artifact and at most one active index entry;
6. a stale attempt for an older manifest resumes after a corrected answer has
   activated a newer manifest; the stale attempt must not supersede the newer
   active entry.

Use barriers and deterministic fake stores/providers rather than timing sleeps.
Run the same critical cases against the PostgreSQL stores under the existing
`pg_runtime` marker.

### Step 7: Build the final context without overlap

The final context order is:

1. retrieved Question Memory summaries;
2. exact recent closed question if selected;
3. exact current-question messages;
4. exact or independently compressed Evidence.

Do not include a message in both a summary source representation and the exact
window unless a documented mandatory-current rule requires it. Add a duplicate
source detector in tests.

### Step 8: Preserve old session behavior

- `deterministic-v1`: deterministic selection only.
- `question-conversation-v1`: existing Stage 50 coordinator.
- `question-memory-v1`: new coordinator.

Global config must not change the route for an existing assignment.

### Step 9: Add replay and cost-bound tests

Prove:

- current question remains exact;
- only closed questions become memory sources;
- short contexts create no artifact;
- one missing memory creates one provider call;
- replay reuses the completed artifact;
- a corrected answer creates a new manifest and superseding entry;
- ordinary retrieval ignores the superseded entry;
- no generation creates more than one new Question Memory artifact;
- summary units never enter scoring Evidence.

### Step 10: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_question_memory.py tests/test_question_memory_retrieval.py tests/test_question_memory_recovery.py tests/test_interview_context_artifacts.py tests/test_durable_interview_graph.py tests/test_dual_langgraph_rollout.py -q -m "not pg_runtime"
& 'F:\python3.11\python.exe' -m pytest tests/test_question_memory_recovery.py tests/test_postgres_question_memory_index.py tests/test_context_artifact_store_postgres.py -q -m pg_runtime
```

Expected: all pass with deterministic fake compressor call counts.

### Step 11: Commit

```powershell
git add app/services/question_memory.py app/services/question_memory_retrieval.py app/services/interview_context_artifacts.py app/graphs/durable_interview_graph.py app/services/runtime.py tests/test_interview_context_artifacts.py tests/test_question_memory.py tests/test_question_memory_retrieval.py tests/test_question_memory_recovery.py tests/test_durable_interview_graph.py tests/test_dual_langgraph_rollout.py
git commit -m "feat(memory): add incremental question memory retrieval"
```

---

## Task 11: Establish the P1 Knowledge Coverage Baseline

**Spec coverage:** `MEM-KNW-003` through `MEM-KNW-009`, WP-10a.

**Files:**

- Modify: `app/services/knowledge_corpus_schema.py`
- Modify: `app/services/knowledge_profile.py`
- Modify: `app/services/knowledge_ingestion.py`
- Modify: `app/data/knowledge_v2/manifest.json`
- Modify: `scripts/build_knowledge_manifest_v2.py`
- Modify: `scripts/evaluate_knowledge_retrieval_v2.py`
- Modify: `tests/test_knowledge_manifest_v2.py`
- Modify: `tests/test_knowledge_profile.py`
- Modify: `tests/test_knowledge_ingestion.py`
- Modify: `tests/test_knowledge_eval_cli_v2.py`
- Modify: `tests/golden/knowledge_retrieval_v2_pilot.json`
- Add or modify approved files under: `app/data/knowledge_v2/`

### Step 1: Extend manifest schema with coverage metadata

Add a versioned coverage section containing controlled canonical tags, supported
role groups, and evidence-class counts for positive, negative, and boundary
examples. Coverage metadata must be included in the manifest checksum.

### Step 2: Derive runtime covered tags from the active manifest

Remove the hard-coded set as the runtime authority. Keep a compatibility
fallback only for legacy manifests if required, and make that fallback visible
as safe readiness metadata.

### Step 3: Define the minimum P1 evidence classes

For each currently declared core covered tag, require approved material that
supports:

- correct mechanism or practice;
- common failure or misuse;
- boundary, trade-off, or operational limitation.

Do not fabricate corpus content in tests. Add reviewed knowledge documents with
the repository's existing metadata and manifest-generation workflow.

### Step 4: Update retrieval evaluation

Add queries that distinguish correct practice from common misuse and boundary
conditions. Update the golden dataset only after inspecting deterministic
retrieval results and recording the corpus version.

### Step 5: Add a consumption preflight

Question Memory production consumption must refuse readiness when runtime
covered tags cannot be derived from the active manifest or the minimum approved
evidence-class coverage is absent. Use this precise behavior:

1. application startup and liveness remain successful because the deterministic
   interview path is still valid;
2. readiness metadata returns
   `memory_runtime.configuration_valid=false`,
   `memory_runtime.consumption_ready=false`, and stable reason
   `knowledge_coverage_unavailable`;
3. the effective runtime forces Question Memory consumption off and continues
   with deterministic context;
4. shadow artifact creation may continue only when explicitly configured and
   must not change candidate-visible context;
5. no request may bypass the readiness/config guard by directly invoking the
   Question Memory consumption coordinator.

Follow the repository's existing readiness HTTP-status convention; the new
memory fields must make the failure machine-readable without crashing the
application process.

### Step 6: Run focused tests and evaluation

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_manifest_v2.py tests/test_knowledge_profile.py tests/test_knowledge_ingestion.py tests/test_knowledge_eval_cli_v2.py -q
& 'F:\python3.11\python.exe' -m scripts.evaluate_knowledge_retrieval_v2 --help
```

Run the repository's documented v2 evaluation command with the deterministic
or approved local repository configuration. Expected: coverage manifest is
stable, runtime tags match it, and the updated golden evaluation passes.

### Step 7: Commit

```powershell
git add app/services/knowledge_corpus_schema.py app/services/knowledge_profile.py app/services/knowledge_ingestion.py app/data/knowledge_v2 scripts/build_knowledge_manifest_v2.py scripts/evaluate_knowledge_retrieval_v2.py tests/test_knowledge_manifest_v2.py tests/test_knowledge_profile.py tests/test_knowledge_ingestion.py tests/test_knowledge_eval_cli_v2.py tests/golden/knowledge_retrieval_v2_pilot.json
git commit -m "feat(knowledge): derive covered capabilities from the active corpus"
```

---

## Task 12: Add Retention Primitives and In-Memory TTL

**Spec coverage:** Spec section 12.4, WP-7a.

**Files:**

- Create: `app/services/memory_retention.py`
- Modify: `app/services/session.py`
- Modify: `app/services/durable_workflow_maintenance.py`
- Modify after Task 4: `app/services/memory_config.py`
- Modify: `app/services/runtime.py`
- Create: `tests/test_memory_retention.py`
- Modify: `tests/test_session_service.py`
- Modify: `tests/test_session_report_store.py`
- Modify: `tests/test_durable_workflow_maintenance.py`

### Step 1: Define one retention policy

Include max in-memory sessions, finished-session TTL, cleanup batch, business
session/report retention candidates, checkpoint days, and artifact windows.
Validate positive values and inject a clock.

### Step 2: Implement bounded in-memory cleanup

Evict only finished sessions. Keep sessions, reports, and question evaluations
consistent. When max capacity is reached and no finished session can be evicted,
return a stable capacity error rather than dropping an active session.

### Step 3: Add periodic cleanup integration

Reuse the maintenance scheduler. Keep cleanup idempotent and bounded. Memory
runtime cleanup must not change PostgreSQL persistence behavior.

### Step 4: Expose PostgreSQL retention candidates without deleting them

Add query/service boundaries that identify finished sessions older than policy.
Do not delete business sessions in this task; Task 13 owns deletion jobs and
complete purge semantics.

### Step 5: Add tests

Cover active preservation, finished TTL, max capacity, report/evaluation
co-eviction, injectable clock, cleanup batches, repeated cleanup, and empty
stores.

### Step 6: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_retention.py tests/test_session_service.py tests/test_session_report_store.py tests/test_durable_workflow_maintenance.py -q
```

Expected: all pass; no active session is evicted.

### Step 7: Commit

```powershell
git add app/services/memory_retention.py app/services/session.py app/services/durable_workflow_maintenance.py app/services/memory_config.py app/services/runtime.py tests/test_memory_retention.py tests/test_session_service.py tests/test_session_report_store.py tests/test_durable_workflow_maintenance.py
git commit -m "feat(memory): bound in-memory retention and cleanup"
```

---

## Task 13: Implement End-to-End Session Deletion

**Spec coverage:** `MEM-LCY-001` through `MEM-LCY-037`, WP-7b.

**Files:**

- Create: `app/ports/session_deletion.py`
- Create: `app/services/session_deletion.py`
- Create: `app/services/postgres_session_deletion.py`
- Create: `app/services/session_deletion_worker.py`
- Modify: `app/services/postgres_runtime_migrations.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/services/interview_workflow.py`
- Modify: `app/services/interview_workflow_store.py`
- Modify: `app/services/interview_generation_store.py`
- Modify: `app/services/context_artifact_store.py`
- Modify after Task 9: `app/services/postgres_question_memory_index.py`
- Modify: `app/services/runtime.py`
- Modify: `app/api/routes.py`
- Create: `tests/test_session_deletion.py`
- Create: `tests/test_postgres_session_deletion.py`
- Modify: `tests/test_postgres_runtime_migrations.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_context_artifact_store_postgres.py`
- Modify after Task 9: `tests/test_postgres_question_memory_index.py`

### Step 1: Define deletion job contracts

Add queued, running, completed, and failed states with attempt count, lease owner,
lease token, lease expiry, fencing version, stable error code, and timestamps.
Session ID is internal business data and must not enter aggregate telemetry.

### Step 2: Add migration-owned deletion storage

Create the prefixed deletion job table with one active logical job per session.
Add indexes for queued leasing and stale-lease reclaim. Runtime constructors
remain validate-only.

### Step 3: Add API contracts

Implement:

```http
DELETE /api/interviews/{session_id}
GET /api/interviews/{session_id}/deletion
```

Require the project's available authorization boundary. If current Local V1 has
no user identity, keep the endpoint disabled outside the explicitly trusted
local deployment profile and document the limitation. Repeated delete calls are
idempotent.

### Step 4: Revoke mutation before physical deletion

Mark the session deleting and make snapshot, answer, stream, skip, finish,
report, and SSE paths return a stable deleting/deleted contract. Do not report a
successful prior answer as lost.

### Step 5: Execute the purge under workflow ownership

In bounded, idempotent steps:

1. acquire the session workflow lock or deletion ownership;
2. stop or invalidate active generation ownership;
3. delete LangGraph checkpoints;
4. delete workflow control, command, retry, generation, and chunk rows;
5. delete Question Memory index rows;
6. delete Context Artifact owner refs for interview and review owners;
7. delete the business session and verified cascading rows;
8. schedule or perform bounded orphan artifact cleanup;
9. complete the deletion job with safe counts.

Each step must tolerate replay after process loss.

### Step 6: Add fault-injection tests

Inject loss after every numbered step. Prove reclaim resumes safely, no deleted
session is recreated, active generation cannot write after revocation, and
orphan cleanup does not delete artifacts still referenced elsewhere.

### Step 7: Add backup-boundary documentation

Update the Local V1 runbook with backup retention and the requirement to replay
completed deletion tombstones after restoring an older backup.

### Step 8: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_deletion.py tests/test_postgres_session_deletion.py tests/test_postgres_runtime_migrations.py tests/test_api.py tests/test_context_artifact_store_postgres.py tests/test_postgres_question_memory_index.py -q -m "not pg_runtime"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_deletion.py tests/test_context_artifact_store_postgres.py tests/test_postgres_question_memory_index.py -q -m pg_runtime
```

Expected: available tests pass; deletion replay is idempotent.

### Step 9: Commit

```powershell
git add app/ports/session_deletion.py app/services/session_deletion.py app/services/postgres_session_deletion.py app/services/session_deletion_worker.py app/services/postgres_runtime_migrations.py app/services/postgres_session.py app/services/interview_workflow.py app/services/interview_workflow_store.py app/services/interview_generation_store.py app/services/context_artifact_store.py app/services/postgres_question_memory_index.py app/services/runtime.py app/api/routes.py tests/test_session_deletion.py tests/test_postgres_session_deletion.py tests/test_postgres_runtime_migrations.py tests/test_api.py tests/test_context_artifact_store_postgres.py tests/test_postgres_question_memory_index.py docs/local-v1-runbook.md
git commit -m "feat(memory): add durable end-to-end session deletion"
```

---

## Task 14: Add Privacy-Safe Memory Observability

**Spec coverage:** `MEM-OBS-010` through `MEM-OBS-020`, WP-9a.

**Files:**

- Create: `app/services/memory_metrics.py`
- Modify: `app/services/runtime_signal_metrics.py`
- Modify: `app/services/provider_usage.py`
- Modify: `app/services/trace_sanitization.py`
- Modify after Task 5: `app/services/context_compression_eligibility.py`
- Modify after Task 10: `app/services/question_memory.py`
- Modify after Task 13: `app/services/session_deletion_worker.py`
- Modify: `app/api/routes.py`
- Create: `tests/test_memory_metrics.py`
- Modify: `tests/test_runtime_signal_metrics.py`
- Create: `tests/test_trace_sanitization.py`
- Modify: `tests/test_runtime_boundary_api.py`

### Step 1: Define a strict metric event model

Allow only stable route, eligibility reason, fallback code, policy/schema
version, language bucket, boolean mode flags, numeric counts, token usage,
latency, attempts, sizes, and utilization. Reject unknown keys and string values
that are not approved enums or versions.

### Step 2: Publish route and eligibility metrics

Record deterministic, shadow-created, created, reused, fallback, index-retrieved,
and index-empty counts. Keep source-manifest digests and artifact refs internal.

### Step 3: Publish cost and estimator aggregates

Measure compressor latency separately from business LLM latency. Aggregate
estimated and actual provider input by safe language bucket. Mark low-sample
buckets `insufficient_sample` without merging them into a misleading language
result.

### Step 4: Publish storage and deletion aggregates

Record checkpoint size distribution, active/superseded index counts, referenced
and orphan artifact counts, deletion queue age, outcome counts, and duration.
Do not expose per-session drill-down in the general metrics endpoint.

### Step 5: Add a restricted aggregate endpoint

Return time-windowed aggregate metrics only. Reuse the runtime boundary's
existing administrative trust model. Reject unsupported windows and limits.

### Step 6: Add privacy rejection tests

Attempt to publish prompt, answer, summary, excerpt, session ID, question ID,
Evidence ID, artifact ref, credential, and DSN fields. Every attempt must be
rejected or removed according to the established sanitizer contract; content
must not appear in captured logs or stored metric rows.

### Step 7: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_metrics.py tests/test_runtime_signal_metrics.py tests/test_trace_sanitization.py tests/test_runtime_boundary_api.py -q
```

Expected: all pass; privacy fixtures are absent from outputs.

### Step 8: Commit

```powershell
git add app/services/memory_metrics.py app/services/runtime_signal_metrics.py app/services/provider_usage.py app/services/trace_sanitization.py app/services/context_compression_eligibility.py app/services/question_memory.py app/services/session_deletion_worker.py app/api/routes.py tests/test_memory_metrics.py tests/test_runtime_signal_metrics.py tests/test_trace_sanitization.py tests/test_runtime_boundary_api.py
git commit -m "feat(memory): add privacy-safe runtime observability"
```

---

## Task 15: Add Candidate-Facing Degradation Semantics

**Spec coverage:** `MEM-UX-001` through `MEM-UX-008`, WP-9b.

**Files:**

- Modify: `app/services/session.py`
- Modify: `app/services/interview_workflow.py`
- Modify: `app/api/routes.py`
- Modify: `frontend/src/pages/InterviewPage.jsx`
- Modify: `frontend/src/components/UI.jsx`
- Modify: `frontend/src/styles/index.css`
- Modify: `app/static/interview.js`
- Modify: `tests/test_api.py`
- Modify: `tests/test_react_frontend.py`
- Modify: `tests/test_reference_ui_artifact.py`
- Modify: `tests/browser/langgraph-recovery.spec.js`
- Modify: `tests/browser/reference-ui.spec.js`

`app/static/interview.js` was already modified in Task 1 for durable-version
dispatch. Before editing it in this task, re-read and preserve Task 1's shared
durable predicate; Task 15 adds assistance-mode rendering only. Do not restore
the old `langgraph-v1`-only comparison while resolving the overlap.

### Step 1: Add snapshot fields

Return bounded memory metadata with:

```json
{
  "context_route": "artifact_fallback",
  "assistance_mode": "full",
  "user_notice_required": false,
  "policy_version": "question-memory-v1"
}
```

Do not return artifact refs, IDs, source digests, summaries, exact excerpts, or
technical exception text.

### Step 2: Define server-side mode mapping

- normal deterministic or artifact route: `full`;
- Knowledge unavailable but normal generated follow-up: `reduced`;
- template business follow-up fallback or material capability loss: `basic` and
  `user_notice_required=true`.

Artifact compression fallback alone does not require a user notice.

### Step 3: Implement the React notice

Show one non-blocking accessible notice only when required. Use the approved
Chinese copy from the Spec. Preserve the submitted-answer UI state and never
ask the candidate to resubmit a successfully persisted answer.

### Step 4: Keep static compatibility assets aligned

The legacy static prototype remains covered by repository tests. Apply the same
mode and notice semantics without creating a second server contract.

### Step 5: Add accessibility and refresh tests

Prove:

- transparent artifact fallback shows no candidate alert;
- template fallback shows one notice;
- notice contains no provider or internal error code;
- `aria-live` announces the material downgrade;
- refresh restores the mode without repeatedly announcing an acknowledged
  notice;
- reduced-motion behavior remains intact.

### Step 6: Run focused and browser tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_react_frontend.py tests/test_reference_ui_artifact.py -q
npm run test:browser -- --grep "memory assistance degradation"
```

Expected: all pass.

### Step 7: Build the React frontend

```powershell
npm run build:frontend
```

Expected: Vite production build succeeds.

### Step 8: Commit

```powershell
git add app/services/session.py app/services/interview_workflow.py app/api/routes.py frontend/src/pages/InterviewPage.jsx frontend/src/components/UI.jsx frontend/src/styles/index.css app/static/interview.js tests/test_api.py tests/test_react_frontend.py tests/test_reference_ui_artifact.py tests/browser/langgraph-recovery.spec.js tests/browser/reference-ui.spec.js
git commit -m "feat(memory): expose safe interview assistance degradation"
```

---

## Task 16: Complete Effective Configuration Migration

**Spec coverage:** WP-8b.

**Files:**

- Modify after Task 4: `app/services/memory_config.py`
- Modify: `app/services/config.py`
- Modify: `app/services/context_runtime.py`
- Modify: `app/services/context_compression_gating.py`
- Modify: `app/services/runtime.py`
- Modify: `app/api/routes.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify after Task 4: `tests/test_memory_config.py`
- Modify: `tests/test_context_runtime.py`
- Modify: `tests/test_context_compression_gating.py`
- Modify: `tests/test_local_v1_docs.py`

### Step 1: Inventory direct environment reads

Use `rg` to enumerate all memory, budget, compression, artifact, and interview
graph environment reads. Record the list in a test fixture or review note so the
task can prove it removed the intended direct reads.

### Step 2: Migrate runtime consumers

Make Context Runtime, compression gates, Interview Workflow construction,
artifact stores, maintenance, observability, and readiness consume the same
`EffectiveMemoryConfig` instance or an immutable derived sub-config.

### Step 3: Preserve compatibility getters temporarily

Old public getters may remain for one compatibility release, but they must
delegate to the adapter/effective config and emit only safe deprecation signals.
They must not independently parse environment variables.

### Step 4: Update documentation and examples

Document the structured paths, legacy mapping, conflict failure behavior,
readiness fields, and deprecation timeline. Keep production-safe default values.

### Step 5: Add source-audit tests

Assert that unauthorized modules do not directly reference the migrated legacy
environment variable names. Allow them only in `memory_config.py`, compatibility
tests, `.env.example`, and migration documentation.

### Step 6: Run focused tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_config.py tests/test_context_runtime.py tests/test_context_compression_gating.py tests/test_local_v1_docs.py -q
```

Expected: all pass; conflicting new/legacy values fail deterministically.

### Step 7: Commit

```powershell
git add app/services/memory_config.py app/services/config.py app/services/context_runtime.py app/services/context_compression_gating.py app/services/runtime.py app/api/routes.py .env.example README.md docs/local-v1-runbook.md tests/test_memory_config.py tests/test_context_runtime.py tests/test_context_compression_gating.py tests/test_local_v1_docs.py
git commit -m "refactor(memory): route runtime policy through effective config"
```

---

## Task 17: Add the Memory Optimization Repository Acceptance Gate

**Spec coverage:** WP-11 and Spec sections 17-21.

**Files:**

- Create: `scripts/memory_system_optimization_acceptance.py`
- Create: `docs/memory-system-optimization-acceptance.md`
- Create: `tests/test_memory_system_optimization_acceptance.py`
- Create: `tests/test_memory_system_artifact_audit.py`
- Create: `tests/test_memory_plan_traceability.py`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`

### Step 1: Define deterministic acceptance inputs

Use fake providers, isolated table prefixes, fixed multilingual long-context
fixtures, stable source manifests, and process-loss injection. Do not require a
real model or production credentials.

### Step 2: Assemble mandatory gates

The runner must execute and summarize:

- durable legacy/v1/v2 HTTP dispatch;
- resolved-budget and multilingual tests;
- config conflict/preflight tests;
- eligibility/no-call tests;
- Conversation fallback and fail-closed tests;
- Question Memory contract, manifest, supersede, and retrieval tests;
- PostgreSQL migrations and stores;
- knowledge coverage baseline;
- retention and deletion fault matrix;
- privacy and artifact audits;
- frontend degradation contract;
- compile and diff checks.

### Step 3: Enforce Spec/Plan requirement-ID integrity

Add a documentation contract test that:

1. reads the Plan's pinned Spec version and asserts it is
   `1.1.1-draft`;
2. extracts every `MEM-*` requirement ID referenced by the Plan and its
   traceability matrix;
3. extracts every normative requirement ID defined by the pinned Spec;
4. fails if the Plan references an ID absent from the Spec;
5. fails on duplicate normative IDs in the Spec;
6. permits a Spec requirement to be explicitly deferred only when the
   traceability matrix names the deferred range and reason.

This test must cover `MEM-ART-030`, `MEM-UX-001` through `MEM-UX-008`,
`MEM-TST-020` through `MEM-TST-025`, and `MEM-TST-030` through `MEM-TST-035`.
They are normative IDs already defined in Spec v1.1.1, not Plan-local temporary
requirements.

### Step 4: Add artifact privacy auditing

Scan generated acceptance artifacts, logs, JSON, Markdown, and trace fixtures
for blocked keys and known secret/content sentinels. The audit fails if any
prompt, answer, summary, excerpt, identifier, artifact ref, credential, or DSN
appears.

### Step 5: Emit the repository-only status

Successful output is exactly:

```text
READY_FOR_MEMORY_SYSTEM_SHADOW
PRODUCTION_OBSERVATION=NOT_RUN
```

The runner must not emit `PASS_FOR_PRODUCTION` or alter rollout defaults.

### Step 6: Write the acceptance record template

Document environment, commit, migration prefix, test counts, skipped tests,
privacy results, connection cleanup, process cleanup, and the distinction
between repository readiness and production observation.

### Step 7: Run the acceptance tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_system_optimization_acceptance.py tests/test_memory_system_artifact_audit.py tests/test_memory_plan_traceability.py -q
& 'F:\python3.11\python.exe' -m scripts.memory_system_optimization_acceptance
```

Expected output:

```text
READY_FOR_MEMORY_SYSTEM_SHADOW
PRODUCTION_OBSERVATION=NOT_RUN
```

### Step 8: Commit

```powershell
git add scripts/memory_system_optimization_acceptance.py docs/memory-system-optimization-acceptance.md tests/test_memory_system_optimization_acceptance.py tests/test_memory_system_artifact_audit.py tests/test_memory_plan_traceability.py README.md docs/local-v1-runbook.md
git commit -m "test(memory): add repository shadow readiness gate"
```

---

## Task 18: Run Full Regression and Produce the Release Record

**Purpose:** Close the repository implementation phase without authorizing
production consumption.

**Files:**

- Modify after Task 17: `docs/memory-system-optimization-acceptance.md`
- Modify only if evidence requires: `README.md`

### Step 1: Run the focused memory suite

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_memory_config.py tests/test_context_budget.py tests/test_context_selection.py tests/test_context_language.py tests/test_context_compression_eligibility.py tests/test_interview_context_artifacts.py tests/test_context_artifacts.py tests/test_context_compression_validation.py tests/test_context_compression_runner.py tests/test_question_memory_index_contracts.py tests/test_in_memory_question_memory_index.py tests/test_question_memory.py tests/test_question_memory_retrieval.py tests/test_session_deletion.py tests/test_memory_metrics.py -q
```

Expected: all pass.

### Step 2: Run PostgreSQL memory and recovery tests

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py tests/test_postgres_runtime_migrations.py tests/test_context_artifact_store_postgres.py tests/test_postgres_question_memory_index.py tests/test_postgres_session_deletion.py tests/test_durable_interview_graph.py tests/test_dual_langgraph_canary_postgres.py -q -m pg_runtime
```

Expected: all configured PostgreSQL tests execute and pass. If the approved
database is unavailable, the release record remains incomplete rather than
recording a pass from skipped tests.

### Step 3: Run the full Python regression

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: all tests pass, with only documented intentional skips and existing
reviewed warnings.

### Step 4: Run frontend and browser verification

```powershell
npm run build:frontend
npm run test:browser
```

Expected: build and tests pass; browser processes and ports are cleaned after
the run.

### Step 5: Run static checks and acceptance

```powershell
& 'F:\python3.11\python.exe' -m compileall app scripts tests
git diff --check
& 'F:\python3.11\python.exe' -m scripts.memory_system_optimization_acceptance
```

Expected acceptance status:

```text
READY_FOR_MEMORY_SYSTEM_SHADOW
PRODUCTION_OBSERVATION=NOT_RUN
```

### Step 6: Verify operational cleanup

Record:

- no acceptance-specific PostgreSQL tables remain;
- no test worker or browser process remains;
- no test listener remains;
- no real credential entered logs or artifacts;
- committed rollout and consumption defaults are still disabled.

### Step 7: Finalize the acceptance record

Write exact test counts, skips, warnings, environment, commit, migration prefix,
privacy result, cleanup result, and repository status. Do not claim production
observation.

### Step 8: Commit

```powershell
git add docs/memory-system-optimization-acceptance.md README.md
git commit -m "docs(memory): record repository shadow readiness"
```

---

## 6. Post-Implementation Shadow and Canary Sequence

The following steps are operational follow-up, not authorized by repository
implementation alone.

### Phase A: Budget Shadow

- keep Interview rollout and compression consumption at zero;
- enable approved aggregate measurements only;
- observe Chinese, English, mixed, other, and unknown estimator buckets;
- verify no provider context overflow and no sensitive telemetry.

### Phase B: Budget Enforcement Canary

- assign 1% of new approved sessions;
- keep Question Memory consumption disabled;
- stop automatically on follow-up failure regression, budget rejection surge,
  or mandatory-current-content loss.

### Phase C: Question Memory Shadow Creation

- create eligible artifacts without consuming summaries;
- compare deterministic context loss, excerpt coverage, latency, cost, and
  offline semantic-quality metrics;
- keep every accepted summary non-authoritative.

### Phase D: One-Percent Consumption

Prerequisites:

- WP-10a knowledge baseline accepted;
- frontend material-degradation notice accepted;
- deletion and privacy exercises passed;
- budget enforcement stable;
- explicit production approval recorded.

Assign only new `langgraph-v2` sessions with immutable
`memory_policy_version=question-memory-v1`.

### Phase E: Progressive Expansion

Use fixed gates:

```text
1% → 5% → 25% → 50% → 100%
```

Each increase requires a fixed observation window and explicit approval. A
rollback changes assignment for new sessions and sets consumption to shadow;
existing v2 sessions remain executable and use deterministic fallback.

---

## 7. Rollback Matrix

| Failure | Immediate action | Existing sessions | New sessions |
|---|---|---|---|
| v2 dispatch mismatch | rollout to 0; stop deploy | keep graph available; block unsafe mutation | assign legacy/v1 |
| budget regression | enforcement to shadow | deterministic selection with guard observation | no new enforcement |
| artifact validation surge | consumption to shadow | deterministic fallback | shadow creation or disabled |
| compressor latency surge | disable creation | existing refs remain readable for replay | deterministic only |
| knowledge coverage mismatch | block consumption readiness | deterministic or existing approved memory | no new consumption |
| index consistency conflict | stop Question Memory path | fail closed for conflicting ref; deterministic where safe | deterministic assignment |
| deletion defect | disable DELETE endpoint and worker leasing | preserve tombstones and data pending repair | no automatic retention deletion |
| privacy leak | stop affected telemetry/trace pipeline; rotate exposed secrets if applicable | preserve evidence for incident response under access control | disable affected path |

Never delete graph definitions, historical artifacts, or migrations as a
rollback mechanism while existing sessions reference them.

---

## 8. Risk Register

| Risk | Mitigation | Evidence required |
|---|---|---|
| Question Memory increases provider cost | eligibility, at most one new artifact per generation, reuse | call-count and token metrics |
| Free-language summary introduces unsupported meaning | non-authoritative contract, exact excerpts, no scoring use, offline evaluation | semantic evaluation record |
| New config changes active sessions | immutable `memory_policy_version` | config-change recovery test |
| Index races create multiple active entries | transaction plus partial unique index | PostgreSQL concurrency test |
| Chinese estimator undercounts | language-bucket evidence and enforcement stop gate | multilingual error report |
| Deletion races with generation | deleting status, workflow lock, generation revocation, fencing | purge fault matrix |
| Telemetry leaks identifiers | whitelist events and artifact audit | privacy acceptance output |
| Knowledge coverage claim exceeds corpus | manifest-derived tags and P1 evidence classes | coverage manifest and golden eval |
| Existing v2 checkpoints break | compatibility assignment and missing-field resolution | historical checkpoint recovery test |
| Frontend warns on transparent fallback | server mode mapping and browser tests | UI acceptance result |

---

## 9. Definition of Done

Repository implementation is complete only when all conditions hold:

1. Tasks 1-18 are complete in dependency order.
2. Legacy, v1, and v2 HTTP dispatch tests pass.
3. Context selection uses the resolved model budget.
4. Chinese, English, and mixed estimator evidence is recorded without content.
5. Short contexts make zero compressor calls.
6. Recoverable Conversation compression failures use deterministic fallback.
7. Question Memory uses canonical ordered manifests and immutable superseding
   artifacts.
8. Existing session memory policy assignments do not change with global config.
9. Ordinary retrieval returns only active compatible Question Memory entries.
10. Accepted summaries remain non-authoritative and never become scoring
    Evidence.
11. Knowledge covered tags derive from the active manifest and the P1 minimum
    evidence classes pass evaluation.
12. In-memory runtime is bounded without evicting active sessions.
13. Session deletion covers checkpoints, workflow rows, generations, index
    entries, owner refs, business rows, and orphan cleanup.
14. Observability contains only approved aggregate fields.
15. Candidate UI distinguishes transparent and material degradation.
16. Runtime memory consumers use `EffectiveMemoryConfig`.
17. PostgreSQL, full Python, frontend, browser, privacy, and cleanup gates pass.
18. Acceptance prints:

    ```text
    READY_FOR_MEMORY_SYSTEM_SHADOW
    PRODUCTION_OBSERVATION=NOT_RUN
    ```

19. Committed rollout, enforcement, and compression-consumption defaults remain
    disabled.
20. No production rollout claim is made without a separate operator record and
    approval.

---

## 10. Spec Traceability Matrix

The normative source for every ID in this table is
`docs/interview-agent-memory-system-optimization-spec.md` v1.1.1-draft. This
Plan does not define temporary `MEM-*` requirements. Task 17 adds an automated
contract test that fails if a future Plan revision references an ID absent from
the pinned Spec.

| Spec requirements | Implementation tasks |
|---|---|
| `MEM-ARCH-001` through `MEM-ARCH-009` | Tasks 1-18, fixed decisions, rollback matrix |
| `MEM-DSP-001` through `MEM-DSP-010` | Task 1, Task 8, Task 18 |
| `MEM-BUD-001` through `MEM-BUD-010` | Task 2, Task 4, Task 16 |
| `MEM-BUD-011` through `MEM-BUD-014` | Task 3, Task 14, Task 18 |
| `MEM-SEL-001` through `MEM-SEL-010` | Task 2, Task 5, Task 10 |
| `MEM-ART-001` through `MEM-ART-007` | Task 5 |
| `MEM-ART-010` through `MEM-ART-019`, `MEM-ART-030` | Tasks 7-10 |
| `MEM-ART-020` through `MEM-ART-029` | Task 6, Task 10 |
| `MEM-SUM-001` through `MEM-SUM-010` | Task 7, Task 10, Task 17 |
| `MEM-KNW-001` through `MEM-KNW-010` | Task 11; P2 scale remains a separate plan |
| `MEM-LCY-001` through `MEM-LCY-005` | Task 13 |
| `MEM-LCY-010` through `MEM-LCY-015` | Tasks 9, 12, 13 |
| `MEM-LCY-020` through `MEM-LCY-025` | Tasks 8, 9, 13 |
| `MEM-LCY-030` through `MEM-LCY-037` | Task 13, Task 18 |
| `MEM-CFG-001` through `MEM-CFG-010` | Tasks 4 and 16 |
| `MEM-OBS-001` through `MEM-OBS-005` | Task 15 |
| `MEM-OBS-010` through `MEM-OBS-020` | Task 14, Task 17 |
| `MEM-UX-001` through `MEM-UX-008` | Task 15 |
| `MEM-SEC-001` through `MEM-SEC-005` | Tasks 7, 9, 13, 14, 17 |
| `MEM-LTM-001` through `MEM-LTM-014` | Explicitly deferred to a separate P2 plan |
| `MEM-TST-001` through `MEM-TST-010` | Tasks 0-10 and Task 17 |
| `MEM-TST-020` through `MEM-TST-025` | Task 3 and Task 18 |
| `MEM-TST-030` through `MEM-TST-035` | Task 15 and Task 18 |

The only intentionally deferred normative range is `MEM-LTM-*`, because the
Spec requires separate product, privacy, identity, and legal approval before
Principal Memory implementation.
