# Stage 50 Durable Context Compression Artifacts and Interview Graph v2 Plan

**Plan revision:** v1.1, incorporating the Artifact identity-material,
Review Effect ownership, full ref-identity validation, independent Evidence
gating, prefixed foreign-key, cleanup protocol, v2 dispatch, privacy-scope,
compressor-configuration, terminal-row, and synchronous parent-ownership
review amendments.

> **Execution note:** Do not begin production source changes until the Stage 49
> Context Budget canary has been observed and accepted. Implement this plan in
> task order, start each task with the stated failing or characterization test,
> preserve all unrelated worktree changes, and keep every new rollout and
> enforcement default disabled. Repository readiness is not production
> observation.

**Goal:** Add durable, write-once, semantically compressed Context Artifacts
that can be safely reused across process loss, LangGraph replay, provider
retry, and worker replacement without placing compressed payloads in
checkpoints or telemetry. Introduce an Interview `langgraph-v2` that references
artifacts by opaque ref and digest while preserving `langgraph-v1` recovery
unchanged.

**Architecture:** Stage 49 remains the authorization and deterministic
selection layer. Stage 50 activates semantic compression only after the
deterministic selector identifies eligible low-priority material that would
otherwise be dropped or excessively truncated. A dedicated compression agent
produces a versioned structured payload. A PostgreSQL Context Artifact Store
claims the immutable artifact identity, renews a lease, validates the output,
and completes it with claim-token and fencing predicates. LangGraph State may
store only an opaque artifact reference, output digest, artifact type, and
policy version. Provider-generated compressed text remains in the business
database and is loaded only at the provider-input boundary.

**Tech Stack:** Python 3.11, LangGraph, LangChain `ChatOpenAI`, Pydantic v2,
PostgreSQL, psycopg2 business-domain connection pools, the Stage 46-48
single-writer/lease/fencing/heartbeat contracts, the Stage 49 model-aware
Context Runtime, pytest, and the existing runtime signal and Agent Run ledger.

**Baseline:** Commit `bc9f3e9` completed the Stage 49.1 Context Runtime
hardening. Stage 49 repository acceptance reports
`READY_FOR_CONTEXT_BUDGET_CANARY`, but production observation remains
`NOT_RUN`. Committed Interview and Review rollout defaults remain `0/0`, and
all Context enforcement flags remain false. Those defaults and all Stage
46-49 privacy and recovery contracts must remain intact.

---

## Why This Is the Next Step

Stage 49 can deterministically select, deduplicate, truncate, route, and reject
provider inputs. It deliberately does not create LLM summaries. This avoids
unbounded requests, but long sessions still lose older semantic continuity,
large Evidence can be reduced to narrow excerpts, and complex Prep inputs may
lose useful low-priority relationships.

A plain `summarize(text)` helper would create new correctness problems:

- the same source may be summarized repeatedly after replay;
- a worker can die after the provider charges but before the result is saved;
- two workers can race and publish different summaries;
- a stale worker can overwrite a replacement worker;
- a checkpoint can reference an artifact that was deleted or changed;
- compressed candidate content can be mistaken for exact scoring Evidence;
- summaries, answers, Resume data, and Evidence can leak into traces or logs;
- an extra heartbeat per compression can exceed the Stage 48 connection
  budget under parallel Review execution.

Stage 50 therefore treats semantic compression as a durable external Effect,
not as a prompt-building utility. It reuses the existing claim, lease,
heartbeat, fencing, write-once, recovery, privacy, migration, and connection
ownership principles rather than introducing an unowned cache.

## Execution Preconditions

1. Stage 49 production shadow measurement and phased enforcement evidence must
   be reviewed and explicitly accepted. The current `NOT_RUN` state blocks
   Stage 50 implementation rollout.
2. Preserve all tracked and untracked user work. Do not use destructive reset,
   checkout, clean, broad delete, or force-push operations.
3. Use deterministic fake compression providers for repository tests. Do not
   call a real LLM during implementation or repository acceptance.
4. Keep these committed values unchanged:

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

5. Keep `langgraph-v1` and `langgraph-review-v1` readable and executable for
   existing sessions and jobs.
6. Do not mutate an existing checkpoint into v2. Only newly created work may
   be assigned to `langgraph-v2`.
7. Do not put compression prompts, source payloads, summaries, excerpts,
   answers, Resume/JD text, Evidence content, artifact refs, artifact IDs, or
   source IDs into Agent metadata, runtime signals, canary JSON, logs, or
   exception messages.
8. Run PostgreSQL tests only against an isolated table prefix and the
   configured local/test database.
9. Schema creation remains migration-owned. Runtime constructors use
   `schema_mode="validate"` and must not run DDL.
10. Production observation remains `NOT_RUN` until separately authorized
    Stage 50 shadow and canary phases complete.

## Scope

### In Scope

- A versioned Context Artifact domain model and structured payload schemas.
- `QuestionConversationArtifact`, `EvidenceCompressionArtifact`, and
  `PrepContextArtifact`.
- Immutable source/policy/model/prompt/target-budget identities.
- A PostgreSQL Context Artifact Store and owner-reference table.
- Claim tokens, leases, heartbeats, fencing versions, write-once completion,
  failed-claim reclaim, and conflict detection.
- Compression output validation and deterministic fallback.
- Process-loss and LangGraph replay reuse.
- Interview `langgraph-v2`, registered beside v1 and assigned only to new
  sessions when explicitly configured.
- Prep, Interview, Evidence, and Review integration behind independent flags.
- Reference-aware retention, explicit purge integration, and orphan cleanup.
- Privacy-safe aggregate signals and canary metrics.
- Stage 48 connection-capacity recalculation for Artifact heartbeats.
- Preflight, migration, acceptance automation, operator documentation, and a
  combined checkpoint/effect fault matrix.

### Explicitly Out of Scope

- Cross-session conversational memory or user profiles.
- Vectorized, RAPTOR-style, recursive, or multi-level semantic summaries.
- Replacing current-question content or the latest candidate answer with a
  summary.
- Treating semantic summaries as exact candidate scoring Evidence.
- Automatic model switching or provider selection.
- Provider prompt caching and cached-token accounting.
- Provider-native idempotency or response escrow.
- Exactly-once external provider invocation claims.
- Review `langgraph-review-v2` question-attempt redesign.
- Retiring Legacy, Interview v1, or Review v1.
- Automatic migration of existing checkpoints.
- General checkpoint retention/backup policy beyond Artifact reference safety.
- Persisting raw compression prompts or provider request/response envelopes.

## Fixed Decisions

### Fixed Decision 1: Stage 49 Remains the Authorization Layer

Semantic compression cannot bypass Stage 49 budgets. The compressor request
and the final business-operation prompt are independently measured and
authorized by `ContextRuntime`, `ContextBudgetResolver`, and
`RenderedPromptGuard`.

### Fixed Decision 2: Compression Is Conditional, Not the Default Input Path

Short or already well-bounded input uses the deterministic Stage 49 path.
Compression is eligible only when a versioned policy reports a stable reason,
for example:

```text
older_conversation_would_drop
evidence_representation_excessive_truncation
prep_section_coverage_loss
review_continuity_would_drop
```

The same source and policy must make the same eligibility decision.

### Fixed Decision 3: Raw Business Sources Remain Authoritative

Artifacts are derived representations. Session messages, Plan data, Resume/JD
text, Knowledge chunks, and Question Evaluation records remain the sources of
truth. Completing an Artifact never mutates or replaces a source row.

### Fixed Decision 4: Artifact Payloads Are Sensitive Business Data

Compressed text is not telemetry-safe merely because it is shorter. Artifact
payloads use the business PostgreSQL domain, inherit database access controls,
and never enter checkpoints, signals, Agent metadata, logs, or canary files.

### Fixed Decision 5: Checkpoints Store References, Not Payloads

When a later node needs an Artifact, State may contain only:

```text
artifact_ref
artifact_sha256
artifact_type
compression_policy_version
```

The payload is reloaded and digest-verified at the consumption boundary.

### Fixed Decision 6: Identity Material and Derived Key Are Separate

Canonical identity material includes the privacy scope, Artifact type, source
snapshot digest, source manifest digest when applicable, compression policy
version, output schema version, compressor provider/model/settings version,
prompt contract version, and target output budget. `artifact_key` is derived
from the canonical material and never participates in its own hash input.

Attempt numbers, worker IDs, lease tokens, wall-clock timestamps, random values,
`artifact_id`, and `artifact_key` are excluded from canonical identity material.

### Fixed Decision 7: Provider Attempts Reuse the Same Artifact Identity

Interview generation retries and Review provider attempts must reuse a
completed Artifact when the source and policy are unchanged. The Artifact key
must not include `generation_attempt`, `provider_attempt`, or Review Effect
operation attempt suffixes.

### Fixed Decision 8: Completed Artifacts Are Write Once

Once status is `completed`, payload and output digest cannot be overwritten.
An identical claim reads and reuses the completed row. A conflicting immutable
identity raises `ContextArtifactConflict`.

### Fixed Decision 9: External Provider Invocation Is Not Exactly Once

If the provider returned but the process died before Artifact completion was
durable, a replacement owner may call the provider again. Stage 50 guarantees
reuse only after durable completion. Provider-native idempotency or response
escrow remains a later stage.

### Fixed Decision 10: Artifact and Parent Workflow Ownership Are Separate

Artifact claim ownership authorizes the Artifact write. Interview Generation
lease or Review Report Job/Effect ownership authorizes the Graph/business state
patch. A caller must prove both authorities before publishing a result that
advances its workflow.

### Fixed Decision 11: Interview Compression Runs Inside Generation Heartbeat

Current v1 code constructs context after acquiring a Generation attempt but
before starting its heartbeat. Interview v2 must start the Generation heartbeat
before Artifact resolution or compression. A long compression call must not
silently outlive the parent Generation lease.

### Fixed Decision 12: Artifact Heartbeats Fail Closed

`ContextArtifactHeartbeat.__enter__()` performs an initial ownership check.
`False` and exceptions from heartbeat renewal both mark the claim lost. The
first renewal exception remains the cause of `ContextArtifactLeaseLost`.

### Fixed Decision 13: Stale Owners Cannot Complete or Patch State

Every Artifact completion/failure update includes status, claim token, fencing
version, and unexpired lease predicates. Before returning a ref/digest patch, a
caller must either still own the claim or re-read and verify the authoritative
completed row. A stale owner never emits a state patch based on its private
provider result.

### Fixed Decision 14: Missing and Conflicting References Fail Explicitly

A checkpoint reference to a missing Artifact raises
`ContextArtifactMissing`. A ref/digest/identity mismatch raises
`ContextArtifactConflict`. Neither condition silently regenerates under the
same reference.

### Fixed Decision 15: Mandatory Current Content Stays Deterministic

The current question, latest candidate answer, current answer state, and other
Stage 49 mandatory content retain a raw bounded representation. Semantic
compression may represent only eligible older or lower-priority material.

### Fixed Decision 16: Semantic Output Is Never Exact Scoring Evidence

Conversation, Prep, and Evidence summaries may provide advisory context. They
cannot populate `dimension_evidence.observed`, replace exact candidate
excerpts, or satisfy Knowledge content-hash validation. Review scoring keeps
the Stage 49 provenance boundary.

### Fixed Decision 17: Every Compressed Unit Has Source Anchors

Provider output is structured as bounded units with source segment digests and,
where exact quotation matters, a continuous supporting excerpt. The validator
rejects unknown anchors, non-continuous excerpts, fabricated IDs, and newly
introduced numeric/identifier tokens that are absent from supporting source.

### Fixed Decision 18: Validation Failure Uses a Deterministic Fallback

An invalid Artifact is not completed. The current owner records a stable safe
error code and the business operation falls back to the Stage 49 deterministic
representation when that representation fits. If mandatory input still cannot
fit, the existing Context Budget error remains authoritative.

### Fixed Decision 19: No Recursive Semantic Compression

Compression source input is itself deterministically selected under a
compressor operation budget. Artifact payloads are not fed into another
compression pass in Stage 50.

### Fixed Decision 20: v1 Is Frozen and v2 Is Side by Side

Create separate Interview v2 State and Graph modules. Register v1 and v2
together. Existing v1 checkpoint topology, State `Literal` values, interrupt
locations, and recovery behavior remain unchanged.

### Fixed Decision 21: Review v1 Integrates Without a State Schema Change

Review compression is nested within the existing Question Review Effect. A
completed Artifact is reused across Review attempts by identity. Review v2,
question-level attempt state, and batch-outcome cleanup remain Stage 51 work.

### Fixed Decision 22: Artifact Retention Is Reference Aware

An Artifact with a live owner reference cannot be age-deleted. Explicit session
or Review job purge deletes owner refs first. Maintenance deletes only
unreferenced completed/failed Artifacts past retention and expired unattached
Prep refs.

### Fixed Decision 23: Connection Capacity Is a Release Gate

Artifact heartbeat traffic uses the Stage 48 business domain. The plan must
recalculate worst-case Interview and parallel Review heartbeat demand and prove
that Artifact saturation cannot consume advisory-lock, Checkpointer, or
telemetry capacity.

### Fixed Decision 24: Defaults Stay Disabled

Artifact creation, Artifact consumption, Interview v2 assignment, and Review
compression are separate flags. No repository acceptance command changes a
rollout or enforcement default.

### Fixed Decision 25: Privacy Scope Is a Resolved Security-Domain Contract

Stage 50 does not infer privacy scope from random run IDs and does not use an
unqualified global constant. A `ContextArtifactPrivacyScopeResolver` returns a
stable security-domain scope for Prep, Interview, and Review.

The initial repository/runtime profile is explicitly single-tenant per
deployment. Its scope is a stable deployment identifier supplied by trusted
configuration and hashed before entering identity material. If multiple
tenants can share a business database, Stage 50 rollout is blocked until a
trusted tenant/account ID is carried to all three boundaries. For policies
that forbid cross-session reuse, the stable session identity is additionally
included before hashing the scope.

The initial policy is concrete:

```text
PrepContextArtifact
    deployment/tenant scope
    (never random prep_run_id)

QuestionConversationArtifact
    deployment/tenant scope + stable session_id
    (never shared across sessions)

EvidenceCompressionArtifact for Interview/Review
    deployment/tenant scope + corpus manifest identity
    + session_id when policy is question/session-specific

Review non-Evidence context
    deployment/tenant scope + stable session_id
    (job/provider attempts excluded)
```

Identical content under different privacy scopes always produces different
Artifact keys.

### Fixed Decision 26: Compressor Configuration Is Explicit and Identity-Bound

The compressor uses an explicit immutable `ContextCompressorConfig`. It may
reuse the business provider credentials and transport, but it does not infer
model/settings from an arbitrary chat-model object. Provider, model, safe base
URL identity, temperature, timeout policy version, retry count, structured
output mode, tokenizer family, and output limit participate in configuration
resolution or its settings digest.

API keys, authorization headers, credential-bearing URLs, and raw secrets never
enter Artifact identity or telemetry. A configuration change creates a new
Artifact identity; it does not invalidate reading an old completed Artifact
through its exact stored identity.

### Fixed Decision 27: Creation and Consumption Gates Are Independent

Shadow mode may create and validate Artifacts without consuming them. Workflow
and Evidence gates independently authorize consumption. With every flag false,
the runtime does not claim, create, load, or consume a Context Artifact.

The initial gating matrix is fixed:

| Scenario | Shadow | Workflow Flag | Evidence Flag | Behavior |
| --- | ---: | ---: | ---: | --- |
| Shadow measurement | true | any | any | May create/validate; never consume |
| Interview conversation | false/true | Interview true | false | Consume conversation only |
| Interview Evidence | false/true | Interview true | true | Conversation plus eligible Evidence |
| Review non-Evidence context | false/true | Review true | false | Review context only |
| Review Evidence | false/true | Review true | true | Review may consume Evidence Artifact |
| All disabled | false | false | false | No claim, create, load, or consume |

If production observation proves Interview and Review Evidence require separate
rollback domains, split the Evidence flag before enabling either path; do not
overload the shared flag silently.

## Target Architecture

```text
Authoritative Business Sources
        |
        v
Stage 49 Deterministic Selector + Eligibility Decision
        |
        +---------------- no compression ----------------+
        |                                                 |
        v                                                 v
Canonical Artifact Identity                         Bounded Raw Input
        |
        v
ContextArtifactStore.claim()
        |
        +---- completed ---> verify digest/schema -------+
        |
        +---- live owner ---> ContextArtifactBusy
        |
        v
ContextArtifactHeartbeat + Parent Workflow Heartbeat
        |
        v
ContextCompressorAgent
        |
        v
CompressionArtifactValidator
        |
        v
Fenced write-once completion
        |
        v
Owner-bound opaque Artifact Ref
        |
        v
Final Stage 49 Prompt Guard
        |
        v
Business Provider Invocation
```

## Core Contracts

### Artifact Types

```python
ArtifactType = Literal[
    "question_conversation",
    "evidence_compression",
    "prep_context",
]

OwnerType = Literal[
    "prep_run",
    "interview_session",
    "review_job",
]

ArtifactPurpose = Literal[
    "prep_plan_context",
    "interview_conversation_context",
    "interview_evidence_context",
    "review_context",
    "review_evidence_context",
]
```

### Compression Policy

```python
@dataclass(frozen=True)
class ContextCompressionPolicy:
    artifact_type: ArtifactType
    policy_version: str
    prompt_contract_version: str
    output_schema_version: str
    compressor_operation: str
    compressor_input_cap_tokens: int
    target_output_tokens: int
    max_output_units: int
    max_supporting_excerpt_tokens: int
```

### Privacy Scope Resolution

```python
class ContextArtifactPrivacyScopeResolver(Protocol):
    def for_prep(self, *, deployment_scope: str, principal_id: str | None) -> str:
        ...

    def for_interview(self, *, deployment_scope: str, session_id: str) -> str:
        ...

    def for_review(
        self,
        *,
        deployment_scope: str,
        session_id: str,
    ) -> str:
        ...
```

The resolver returns canonical non-secret scope material. Identity stores only
its SHA-256. The initial single-tenant implementation requires a stable
operator-configured deployment scope. It may add stable session scope for
policies that prohibit cross-session reuse. Random Prep run IDs and worker/job
attempt numbers are not privacy scopes.

### Compressor Configuration

```python
@dataclass(frozen=True)
class ContextCompressorConfig:
    provider: str
    model: str
    base_url_identity: str | None
    temperature: float
    request_timeout_seconds: float
    timeout_policy_version: str
    max_retries: int
    structured_output_mode: str
    tokenizer_family: str | None
```

`base_url_identity` is a normalized non-credential provider endpoint identity.
The settings digest is derived from canonical non-secret behavior settings:

```python
compressor_settings_sha256 = sha256(
    canonical_json(
        {
            "temperature": config.temperature,
            "request_timeout_seconds": config.request_timeout_seconds,
            "timeout_policy_version": config.timeout_policy_version,
            "max_retries": config.max_retries,
            "structured_output_mode": config.structured_output_mode,
            "tokenizer_family": config.tokenizer_family,
        }
    ).encode("utf-8")
).hexdigest()
```

The API key, raw authorization headers, and credential-bearing URL components
are excluded.

### Canonical Artifact Identity Material

```python
@dataclass(frozen=True)
class ContextArtifactIdentityMaterial:
    artifact_type: ArtifactType
    privacy_scope_sha256: str
    source_sha256: str
    source_manifest_sha256: str | None
    semantic_focus_sha256: str | None
    compression_policy_version: str
    prompt_contract_version: str
    output_schema_version: str
    compressor_provider: str
    compressor_model: str
    compressor_settings_sha256: str
    target_output_tokens: int


@dataclass(frozen=True)
class ContextArtifactIdentity:
    artifact_key: str
    material: ContextArtifactIdentityMaterial

    @classmethod
    def from_material(
        cls,
        material: ContextArtifactIdentityMaterial,
    ) -> "ContextArtifactIdentity":
        payload = canonical_identity_payload(material)
        return cls(
            artifact_key=sha256(payload.encode("utf-8")).hexdigest(),
            material=material,
        )
```

`canonical_identity_payload()` accepts only
`ContextArtifactIdentityMaterial`. `artifact_key` cannot be supplied to or
serialized by that helper. Raw source text and raw owner IDs are also excluded.

Required direct tests include:

```python
canonical = canonical_identity_payload(material)
assert "artifact_key" not in json.loads(canonical)
assert identity.artifact_key == sha256(
    canonical.encode("utf-8")
).hexdigest()
```

### Artifact Reference

```python
class ContextArtifactRef(BaseModel):
    artifact_ref: str
    artifact_sha256: str
    artifact_type: ArtifactType
    compression_policy_version: str
```

The ref is opaque outside the Store. It must not encode source text, a Resume
name, Evidence title, or filesystem path.

### Authoritative Artifact Record

```python
@dataclass(frozen=True)
class ContextArtifactRecord:
    artifact_id: str
    identity: ContextArtifactIdentity
    status: Literal["completed", "failed"]
    output_sha256: str | None
    payload: dict | None
    last_error_code: str | None
    completed_at: datetime | None
```

The Record is the authoritative consumable Store state. It never exposes
`claim_token`, `claim_owner`, or lease expiry. Claim is a temporary ownership
capability; Record is durable state; Ref is an owner-bound lookup capability.

`claim()` never returns a failed capability. A failed row is reclaimed inside
the claim transaction and returned as a new `running` claim with incremented
attempt count, fencing version, and claim token. Failed terminal state is
available only through the terminal Record API.

### Source Segment

```python
class CompressionSourceSegment(BaseModel):
    segment_index: int
    segment_type: Literal[
        "conversation_message",
        "evidence_paragraph",
        "job_section",
        "resume_section",
        "knowledge_metadata",
    ]
    content: str
    content_sha256: str
```

This model is process-local and must not be written to checkpoints or
telemetry.

### Anchored Compressed Unit

```python
class AnchoredCompressedUnit(BaseModel):
    summary: str
    source_segment_sha256: list[str]
    supporting_excerpts: list[str] = Field(default_factory=list)
```

### Question Conversation Artifact

```python
class QuestionConversationArtifact(BaseModel):
    schema_version: Literal["question-conversation-v1"]
    question_id_sha256: str
    units: list[AnchoredCompressedUnit]
    unresolved_topics: list[AnchoredCompressedUnit] = Field(default_factory=list)
    source_message_count: int
```

The payload stores only the question digest, not a public question ID. The
provider-facing adapter may combine the units with mandatory current raw
content.

### Evidence Compression Artifact

```python
class EvidenceCompressionArtifact(BaseModel):
    schema_version: Literal["evidence-compression-v1"]
    evidence_content_sha256: str
    units: list[AnchoredCompressedUnit]
    exact_excerpts: list[str] = Field(default_factory=list)
```

`exact_excerpts` must be continuous substrings of the validated Evidence
source. Summaries remain advisory and never replace corpus identity checks.

### Prep Context Artifact

```python
class PrepContextArtifact(BaseModel):
    schema_version: Literal["prep-context-v1"]
    role_units: list[AnchoredCompressedUnit]
    responsibility_units: list[AnchoredCompressedUnit]
    experience_units: list[AnchoredCompressedUnit]
    project_units: list[AnchoredCompressedUnit]
    constraint_units: list[AnchoredCompressedUnit]
```

### Artifact Claim

```python
@dataclass(frozen=True)
class ContextArtifactClaim:
    artifact_id: str
    artifact_key: str
    status: Literal["running", "completed"]
    claim_token: str | None
    fencing_version: int
    claim_owner: str | None
    output_sha256: str | None
    payload: dict | None
```

### Parent Workflow Ownership

```python
class ContextCompressionParentOwnership(Protocol):
    def ensure_owned(self) -> None:
        """Synchronously verify authoritative parent workflow ownership."""


class ReviewEffectOwnership(ContextCompressionParentOwnership, Protocol):
    def ensure_owned(self) -> None:
        """Verify the active Report Job lease and Review Effect claim."""
```

Reading a heartbeat thread's local `_lost` flag is insufficient. Each
`ensure_owned()` used as a Stage 50 proof executes the current database fencing
predicate synchronously. The heartbeat remains the background renewal and
early-loss detector.

### Stable Exceptions

| Exception | Stable Code | Retryable |
| --- | --- | ---: |
| `ContextArtifactBusy` | `context_artifact_busy` | Yes |
| `ContextArtifactLeaseLost` | `context_artifact_lease_lost` | Yes |
| `ContextArtifactConflict` | `context_artifact_conflict` | No |
| `ContextArtifactMissing` | `context_artifact_missing` | No |
| `ContextArtifactValidationFailed` | `context_artifact_validation_failed` | No for the same output |
| `ContextArtifactProviderFailed` | existing provider classification | Depends on provider failure |

Exception messages contain only stable machine fields and never include source
text, provider messages, Artifact payloads, owner IDs, or database credentials.

## PostgreSQL Data Model

### Context Artifacts

Create `{table_prefix}_context_artifacts`:

```sql
artifact_id UUID PRIMARY KEY,
artifact_key TEXT NOT NULL UNIQUE,
artifact_type TEXT NOT NULL,
privacy_scope_sha256 TEXT NOT NULL,
source_sha256 TEXT NOT NULL,
source_manifest_sha256 TEXT,
semantic_focus_sha256 TEXT,
compression_policy_version TEXT NOT NULL,
prompt_contract_version TEXT NOT NULL,
output_schema_version TEXT NOT NULL,
compressor_provider TEXT NOT NULL,
compressor_model TEXT NOT NULL,
compressor_settings_sha256 TEXT NOT NULL,
target_output_tokens INTEGER NOT NULL,
status TEXT NOT NULL,
attempt_count INTEGER NOT NULL DEFAULT 0,
output_json JSONB,
output_sha256 TEXT,
claim_owner TEXT,
claim_token UUID,
claim_expires_at TIMESTAMPTZ,
fencing_version BIGINT NOT NULL DEFAULT 0,
last_error_code TEXT,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
completed_at TIMESTAMPTZ,
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

Required checks include:

- allowed Artifact types and statuses;
- positive target budget;
- non-negative attempt count and fencing version;
- completed rows require output JSON, output digest, and completion timestamp,
  and require claim owner/token/expiry to be null;
- running rows require claim owner, token, and expiry and do not carry a
  completion timestamp;
- failed rows contain only a stable error code, not an exception message, and
  require output JSON/digest/completion timestamp/claim fields to be null.

`complete()` and `fail()` explicitly clear `claim_owner`, `claim_token`, and
`claim_expires_at`. Terminal rows do not retain worker identity or look like an
active lease.

Required indexes include:

```text
UNIQUE (artifact_key)
(status, claim_expires_at)
(status, updated_at)
(artifact_type, completed_at)
```

### Artifact Owner References

Create `{table_prefix}_context_artifact_refs`:

```sql
ref_id UUID PRIMARY KEY,
artifact_id UUID NOT NULL REFERENCES {context_artifacts}(artifact_id)
    ON DELETE CASCADE,
owner_type TEXT NOT NULL,
owner_key TEXT NOT NULL,
purpose TEXT NOT NULL,
artifact_sha256 TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
retain_until TIMESTAMPTZ,
UNIQUE (owner_type, owner_key, purpose, artifact_id)
```

`{context_artifacts}` is formatted with `psycopg2.sql.Identifier` for the exact
`{table_prefix}_context_artifacts` relation. Do not interpolate or concatenate
the prefix as SQL text.

Required ref indexes include:

```text
(artifact_id)
(owner_type, owner_key, purpose)
(owner_type, retain_until) WHERE retain_until IS NOT NULL
```

PostgreSQL tests query `pg_constraint` and assert that the foreign key
`confrelid` is the current isolated-prefix Artifact table, not merely that some
foreign key exists.

Allowed owner types are initially:

```text
prep_run
interview_session
review_job
```

The owner key is business-database data and may contain a run/session/job ID.
It is never copied to telemetry. Loading through a ref verifies the expected
owner, Artifact digest, status, identity, and payload schema.

## Interview v2 State Contract

Create a separate `DurableInterviewStateV2`. It retains the minimum v1 control
fields required by existing command, generation, retry, and projection logic,
and adds bounded Artifact reference fields:

```python
class DurableInterviewStateV2(TypedDict):
    # Existing v1 control fields remain version-specific.
    workflow_engine: Literal["langgraph-v2"]
    graph_schema_version: Literal["langgraph-v2"]
    active_context_artifact_ref: str | None
    active_context_artifact_sha256: str | None
    active_context_artifact_type: str | None
    active_context_policy_version: str | None
    context_route: Literal[
        "deterministic",
        "artifact_reused",
        "artifact_created",
        "artifact_fallback",
    ] | None
```

Compressed payloads never enter State. v2 clears active Artifact reference
fields after the generated interviewer message is authoritatively projected.
Historical checkpoint rows may still contain old refs; owner references remain
until explicit session purge.

## Task 0.5: Freeze Cross-Layer Contracts Before Implementation

### Files

Define the final public/internal contracts in the plan and their focused tests
before Store, Runner, or Graph production code is written:

```text
tests/test_context_artifact_contracts.py
tests/test_context_compression_gating.py
tests/test_context_artifact_privacy_scope.py
tests/test_context_compressor_config.py
```

### Test First

Freeze all of the following:

1. `ContextArtifactIdentityMaterial` and the derived, non-self-referential key.
2. `ContextArtifactPrivacyScopeResolver` and different-scope key isolation.
3. `ContextCompressorConfig` and non-secret settings identity.
4. `ContextArtifactClaim`, `ContextArtifactRecord`, and `ContextArtifactRef` as
   separate capability/state types.
5. `load_ref(... expected_identity, purpose)` full identity verification.
6. Completed/failed retention cutoffs, Prep ref expiry, cleanup batch size, and
   cleanup result shape.
7. Review Effect ownership callback semantics.
8. Shadow creation versus workflow/Evidence consumption gating.
9. Synchronous Interview Generation ownership verification points.

### Completion

The contract tests exist first in an expected failing state because Task 2–4
modules do not exist yet. Task 2–4 turn the same tests green; do not create
temporary DTOs merely to make Task 0.5 independently pass. In-memory Store,
PostgreSQL Store, Runner, Graph, and maintenance tasks then implement one frozen
contract rather than discovering incompatible APIs during integration.

## Task 1: Freeze the Stage 49 and v1 Characterization Baseline

### Files

Extend targeted tests around:

```text
app/services/context_selection.py
app/services/context_runtime.py
app/graphs/durable_interview_graph.py
app/graphs/durable_interview_state.py
app/services/interview_workflow.py
app/graphs/durable_review_graph.py
```

### Test First

Prove before Stage 50 implementation:

1. Stage 49 deterministic output is stable for identical inputs.
2. Interview v1 node names, edges, interrupts, State keys, and retry boundaries
   are frozen.
3. Review v1 question Effect identity currently includes provider attempt.
4. Interview context construction currently occurs before Generation heartbeat
   entry; capture this only to drive the v2 test, not to change v1.
5. Existing sessions/jobs resolve the graph version stored in business rows.
6. Committed defaults select Legacy/v1 and never v2.

### Completion

The tests make any accidental v1 checkpoint or rollout change visible.

## Task 2: Implement Artifact Models, Canonical Identity, and Validators

### Files

Create:

```text
app/services/context_artifacts.py
app/services/context_artifact_scope.py
app/services/context_compression_validation.py
tests/test_context_artifacts.py
tests/test_context_artifact_privacy_scope.py
tests/test_context_compression_validation.py
```

### Test First

Cover canonical ordering, identity field changes, omitted attempt/worker data,
derived-key non-self-reference, same-content/different-scope isolation, stable
single-tenant deployment scope, all Artifact schemas, unknown anchors,
repeated anchors, exact excerpt
validation, fabricated IDs, newly introduced numeric/identifier tokens,
oversized output, excessive units, empty summaries, invalid Unicode, and input
immutability.

### Implementation

Add the contracts described above and a canonical JSON helper using sorted
keys and compact separators. Validation must receive the authoritative source
segments in memory, never fetch an unchecked provider-supplied source ID.

Implement `ContextArtifactPrivacyScopeResolver` from trusted runtime/security
context. Fail preflight if the configured deployment could contain multiple
tenants but no trusted tenant/account scope is available.

The validator returns a validated Pydantic object plus safe numeric statistics.
It never returns or logs raw validation excerpts on failure.

### Completion

The same identity input produces the same Artifact key, and only grounded,
bounded structured payloads can reach Store completion.

## Task 3: Implement the Dedicated Compression Provider Boundary

### Files

Create:

```text
app/agents/context_compressor.py
app/services/context_compression.py
tests/test_context_compressor.py
```

Modify:

```text
app/services/llm.py
app/services/context_budget.py
app/services/config.py
app/services/context_runtime.py
app/services/runtime.py
```

### Test First

Cover structured output success, malformed output, provider failure, missing
usage, timeout, model mismatch, final prompt overflow, source ordering,
temperature/settings identity, safe base URL identity, timeout policy version,
retry identity, old-Artifact readability after configuration changes, max
output enforcement, Agent telemetry privacy, and deterministic fallback
behavior.

### Implementation

Add explicit compressor operations and policies:

```text
context_compressor.question_conversation
context_compressor.evidence
context_compressor.prep
```

Resolve and freeze `ContextCompressorConfig` once at runtime composition. The
initial implementation reuses the business LLM provider credentials and
transport unless independent non-secret compressor settings are explicitly
configured. The compressor model and its `ContextRuntime` must match, using the
same fail-closed model/window behavior as `OpenAIInterviewLLM`.

The settings digest includes behavior-affecting non-secret configuration. The
full base URL, API key, DSN, and authorization material are excluded. The
resolved config used by the provider, Artifact identity, and Agent operation
must refer to the same provider/model/settings contract.

Use a dedicated structured-output prompt with stable fixed instructions first
and dynamic source segments afterward. The call uses `fallback=None`; business
fallback is owned by the calling Context coordinator, not the Agent runner.

The compressor receives only deterministically selected source segments that
fit its own Context Policy. It cannot recursively invoke itself.

Agent Run metadata may contain Artifact type, policy version, source segment
count, target output tokens, estimated/provider token usage, validation outcome,
and reuse/creation booleans. It cannot contain refs, IDs, digests, summaries,
excerpts, or source content.

### Completion

Compression is a normal Agent Runtime operation with bounded input/output and
the same privacy and best-effort telemetry semantics as other agents.

## Task 4: Define the Store Protocol and In-Memory Reference Implementation

### Files

Create:

```text
app/ports/context_artifacts.py
app/services/in_memory_context_artifact_store.py
tests/test_in_memory_context_artifact_store.py
app/ports/__init__.py
tests/test_runtime_ports.py
```

### Test First

Cover first claim, completed reuse, active-claim busy, expired reclaim, failed
reclaim, identity conflict, fencing increment, stale completion, stale failure,
heartbeat false/exception, owner-ref idempotency, retain-until handling, digest
mismatch, missing ref, owner/purpose mismatch, complete identity mismatch,
expired Prep refs, separate completed/failed cutoffs, bounded cleanup, and
concurrent cleanup selection.

### Implementation

Define Store operations before PostgreSQL implementation:

```python
claim(identity, *, worker_id, lease_seconds) -> ContextArtifactClaim
heartbeat(claim, *, lease_seconds) -> bool
complete(claim, payload) -> ContextArtifactRecord
fail(claim, *, error_code) -> None
get_terminal_by_key(artifact_key) -> ContextArtifactRecord | None
create_owner_ref(
    record,
    *,
    owner_type,
    owner_key,
    purpose,
    retain_until=None,
) -> ContextArtifactRef
load_ref(
    ref,
    *,
    owner_type,
    owner_key,
    purpose,
    expected_identity,
) -> ContextArtifactRecord
delete_owner_refs(*, owner_type, owner_key) -> int
cleanup(policy: ContextArtifactCleanupPolicy) -> ContextArtifactCleanupResult
```

`load_ref()` verifies ref ID, owner type/key, purpose, Artifact ID/key, every
immutable identity column, Artifact type, policy/prompt/schema versions,
provider/model/settings, target budget, completed status, output digest, and
payload schema. A digest-consistent row with stale source or focus identity is
still a conflict.

Define cleanup policy/result contracts:

```python
@dataclass(frozen=True)
class ContextArtifactCleanupPolicy:
    completed_before: datetime
    failed_before: datetime
    prep_ref_expires_before: datetime
    batch_size: int


@dataclass(frozen=True)
class ContextArtifactCleanupResult:
    deleted_owner_refs: int
    deleted_completed_artifacts: int
    deleted_failed_artifacts: int
```

`get_terminal_by_key()` returns only completed/failed Records. It returns
`None` for missing or running rows, so callers cannot confuse a live row with a
consumable Record; live ownership decisions always go through `claim()`.

`cleanup()` is the single public maintenance entry point. Its implementation
internally deletes expired Prep refs, unreferenced completed rows, and
unreferenced failed rows, then returns all three counts. PostgreSQL cleanup
selects bounded batches with a CTE and `FOR UPDATE SKIP LOCKED`, so multiple
maintenance processes do not repeatedly scan or contend on the same candidates.

The in-memory implementation is for deterministic unit tests only and must
match PostgreSQL conflict semantics. `ContextArtifactStore` is a
`@runtime_checkable` Protocol, consistent with the existing Runtime ports.

### Completion

All ownership behavior is specified independently of SQL and provider code.

## Task 5: Add the PostgreSQL Artifact Schema and Runtime Contract

### Files

Create:

```text
app/services/context_artifact_store.py
tests/test_context_artifact_store_postgres.py
```

Modify:

```text
app/services/postgres_schema_contract.py
app/services/postgres_runtime_migrations.py
app/services/postgres_identifiers.py
tests/test_stage48_release_contract.py
tests/test_stage48_postgres_capacity.py
```

### Test First

Cover migration idempotency, migration checksum conflict, required columns,
check constraints, indexes, identifier length, runtime validate-only startup,
concurrent claim, expired reclaim, fencing, write-once completion, owner refs,
digest/full-identity verification, terminal claim-field cleanup, separate
retention cutoffs, bounded concurrent cleanup, and isolated table-prefix
safety. Also cover the upgraded Interview engine CHECK constraint. Query
`pg_constraint` to prove the ref foreign key targets the exact current-prefix
Artifact table by `confrelid`.

### Implementation

Add a new immutable runtime migration, for example:

```text
stage50_context_artifacts_and_interview_v2
```

Do not edit the checksums of already applied Stage 48 migration specs. Extend
`RUNTIME_MIGRATIONS`, required relation columns, and required index tokens.
Freeze this migration only after both Artifact DDL and the Interview engine
constraint upgrade required by Task 8 are fully specified. Task 8 consumes this
schema; it does not append DDL to an already checksummed/applied migration.

Format both Artifact table identifiers and their foreign-key reference through
`psycopg2.sql.Identifier`. A clean isolated-prefix migration must not reference
an unprefixed `public.context_artifacts` relation.

All Stage 50 DDL is transaction-safe ordinary `CREATE TABLE`, `ALTER TABLE`,
and `CREATE INDEX IF NOT EXISTS`. Do not use `CREATE INDEX CONCURRENTLY` inside
the migration transaction.

The Store accepts the Stage 48 Business `ConnectionProvider`; it does not call
`psycopg2.connect()` directly and does not create a new pool.

### Completion

Migration owns schema creation, runtime validates it read-only, and PostgreSQL
matches the in-memory ownership contract.

## Task 6: Implement the Fenced Context Compression Runner

### Files

Create:

```text
app/services/context_compression_runner.py
tests/test_context_compression_runner.py
```

Modify stable exception ownership in:

```text
app/services/workflow_thread_lock.py
app/services/runtime_work.py
app/services/interview_generation_store.py
```

### Test First

Cover completed reuse with zero provider calls, first completion with one call,
busy live claim, expired reclaim, provider failure, validation failure,
heartbeat false, heartbeat exception with preserved cause, parent ownership
loss detected by synchronous database verification, stale completion,
authoritative reread after claim loss, and no stale state patch.

### Implementation

Add `ContextArtifactHeartbeat` with the Stage 47.1 fail-closed behavior and an
initial `ensure_owned()` call in `__enter__()`.

Add an authoritative Generation predicate, for example:

```python
generation_store.assert_attempt_owned(
    generation_id,
    attempt_number,
    worker_id,
    lease_token=lease_token,
    fencing_version=fencing_version,
) -> bool
```

The predicate checks running status, owner, lease token, fencing version, and
unexpired lease in PostgreSQL. Interview v2 wraps it as
`ContextCompressionParentOwnership`; it does not treat the current v1
heartbeat's local `_lost` flag as a database ownership proof.

The runner sequence is fixed:

```text
build canonical identity
-> claim
-> completed? validate stored payload/digest and attach owner ref
-> running? enter Artifact heartbeat
-> prove parent ownership when supplied
-> call compressor
-> validate structured payload
-> prove Artifact ownership
-> prove parent ownership
-> complete with fencing predicate
-> create owner ref idempotently
-> authoritative reread and digest verification
-> return ref plus process-local validated payload
```

Parent ownership is synchronously verified before the compression Provider
call, before Artifact completion, before the business Provider call, before
Generation completion, and immediately before returning the LangGraph State
patch.

If parent ownership is lost after Artifact completion, the completed Artifact
remains reusable but the caller emits no workflow state patch.

### Completion

The reusable Effect boundary is correct under process loss and worker
replacement without claiming exactly-once provider calls.

## Task 7: Integrate Prep Context Artifacts

### Files

Modify:

```text
app/services/prep.py
app/agents/knowledge.py
app/services/llm.py
app/api/routes.py
```

Add focused tests under:

```text
tests/test_prep_context_compression.py
tests/test_api.py
```

### Test First

Cover short inputs with no Artifact, oversized section coverage loss, identical
source retry reuse, changed source creating a new identity, completed-before-
response loss, invalid Artifact fallback, source-based privacy scope, owner-ref
creation, no public Artifact fields in `/api/prep`, and no payload in telemetry.

### Implementation

Prep identity uses a canonical digest of deterministically segmented JD,
Resume, and approved Knowledge metadata plus the deployment/tenant privacy
scope. It excludes random `prep_run_id`, so an identical retry can reuse a
durably completed Artifact. Each Prep run creates its own owner ref.

The plan provider receives:

```text
mandatory deterministic raw sections
+ validated PrepContextArtifact units for eligible low-priority sections
+ bounded Knowledge candidate metadata
```

The public Prep response remains unchanged. `PrepContext` may retain only
existing public/business fields; internal Artifact refs stay in the Store and
composition layer unless a later durable session adopts them.

### Completion

Prep gains bounded semantic coverage without changing its API payload or
making a random request ID part of Artifact reuse identity.

## Task 8: Add Interview `langgraph-v2` Beside v1

### Files

Create:

```text
app/graphs/durable_interview_state_v2.py
app/graphs/durable_interview_graph_v2.py
tests/test_durable_interview_graph_v2.py
tests/test_langgraph_v2_recovery_postgres.py
```

Modify:

```text
app/graphs/interview_state.py
app/services/config.py
app/services/interview_workflow.py
app/services/interview_workflow_store.py
app/services/postgres_session.py
app/services/runtime.py
app/services/langgraph_canary_status.py
app/services/postgres_schema_contract.py
app/services/postgres_runtime_migrations.py
tests/test_dual_langgraph_rollout.py
tests/test_postgres_runtime_migrations.py
```

### Test First

Cover v1/v2 simultaneous registration, v1 checkpoint resume after v2 deploy,
new work only v2 assignment, unsupported version failure, database engine check
constraint migration, v2 initial State, unchanged command interrupts, Artifact
ref-only State, context route clearing, and v1/v2 business-output parity for
short contexts.

### Implementation

Allow:

```text
INTERVIEW_LANGGRAPH_VERSION=langgraph-v1
INTERVIEW_LANGGRAPH_VERSION=langgraph-v2
```

The committed default remains v1. Register both graphs regardless of the
default so existing sessions always resolve their pinned version.

Update durable engine checks to accept both exact durable versions while still
rejecting unknown values. Do not reinterpret an existing v1 session as v2 and
do not migrate checkpoint rows.

Define one shared version predicate without widening the v1 State Literal:

```python
SUPPORTED_INTERVIEW_GRAPH_VERSIONS = frozenset(
    {"langgraph-v1", "langgraph-v2"}
)


def is_durable_interview_version(value: str | None) -> bool:
    return value in SUPPORTED_INTERVIEW_GRAPH_VERSIONS
```

The dispatch migration is explicit:

1. `choose_workflow_engine()` accepts the configured durable version and
   returns that exact version when the rollout bucket is selected.
2. `InterviewWorkflowService.start()` passes `default_graph_version` to the
   durable session-shell constructor.
3. `insert_durable_session_shell()` accepts and persists the exact engine and
   graph schema version instead of writing v1 constants.
4. Bootstrap selects the matching v1 or v2 initial-State factory.
5. `is_durable_session()`, `graph_for_session()`, `submit_command()`, and
   `snapshot()` accept the supported durable-version set while preserving the
   exact value stored in the business row.
6. Snapshot serialization never rewrites v2 to `langgraph-v1`.
7. `PostgresInterviewWorkflowStore.register_bootstrap_input()` validates the
   exact supported version rather than requiring v1.
8. The Stage 50 migration frozen in Task 5 explicitly drops/replaces the existing workflow
   engine CHECK constraint and allows only
   `legacy`, `langgraph-v1`, and `langgraph-v2`.
9. Runtime schema validation checks the upgraded constraint semantics, not
   only that the column exists.
10. Existing v1 business rows and Checkpointer rows are never updated.

Changing an `ADD COLUMN IF NOT EXISTS` DDL string is not a constraint migration;
the old CHECK must be replaced by a predictably named constraint in the Stage
50 migration.

Keep v1 modules behaviorally frozen. Share only pure, version-neutral helpers
whose extraction is protected by parity tests.

### Completion

The deployment can read v1 and v2 concurrently, while only explicitly
configured new durable sessions receive v2.

## Task 9: Integrate Conversation Artifacts into Interview v2

### Files

Modify:

```text
app/graphs/durable_interview_graph_v2.py
app/services/context_selection.py
app/services/runtime.py
```

Extend:

```text
tests/test_durable_interview_graph_v2.py
tests/test_context_compression_runner.py
```

### Test First

Cover histories that fit deterministically, older turns that would drop,
mandatory latest-answer preservation, same-source replay reuse, generation
retry reuse, Artifact busy, invalid compression fallback, parent Generation
lease loss, Artifact lease loss, completed-before-checkpoint loss, checkpoint-
before-next-node recovery, and final prompt enforcement.

### Implementation

Interview v2 performs:

```text
start/reclaim Generation attempt
-> enter Generation heartbeat
-> run deterministic Context selection
-> if eligible, resolve/create QuestionConversationArtifact
-> prove both leases
-> build mandatory raw + Artifact advisory context + exact Evidence excerpts
-> run final rendered-prompt guard
-> call Examiner provider
-> persist chunks and complete Generation under existing fencing
-> return only Artifact ref/digest metadata in State
```

The conversation Artifact source includes only older eligible units. The
current question, latest interviewer message, and latest candidate answer are
excluded from semantic replacement and remain deterministically bounded raw
content.

### Completion

Long v2 sessions preserve bounded older semantic continuity and replay a
completed Artifact without a second compression provider call.

## Task 10: Integrate Evidence Compression Artifacts

### Files

Modify:

```text
app/services/knowledge_binding.py
app/services/context_selection.py
app/graphs/durable_interview_graph_v2.py
app/services/evaluator_ext.py
app/services/config.py
app/services/runtime.py
.env.example
tests/test_agent_runtime_release_contract.py
tests/test_runtime_preflight.py
```

Add:

```text
tests/test_evidence_context_compression.py
tests/test_context_compression_gating.py
```

### Test First

Cover one large Evidence item, many items, manifest/hash conflict, missing
Evidence, changed focus digest, exact excerpt grounding, advisory summary use,
same Evidence reuse, cross-attempt reuse, invalid output fallback, repository
immutability, Evidence flag independence, all-flags-off zero Store calls, and
no Evidence content in State or telemetry.

### Implementation

Resolve and validate complete Evidence identity before Artifact selection:

```text
Evidence ID/hash/manifest validation
-> deterministic paragraph segmentation
-> canonical source/focus identity
-> EvidenceCompressionArtifact
-> exact excerpt and anchor validation
-> bounded provider representation
```

Question-aware focus is part of the identity when it affects compression.
Generic Evidence reuse without a focus digest is allowed only under a separate
explicit policy version.

Consumption requires the relevant workflow flag and
`CONTEXT_COMPRESSION_EVIDENCE_ENABLED=true`. Shadow mode may create/validate an
Evidence Artifact without consuming it. With all flags false, no Store claim,
load, or creation occurs.

Review scoring continues to use exact bounded source excerpts. Semantic
Evidence units may supply low-priority explanatory context but cannot become
candidate observations or replace repository hashes.

### Completion

Large Evidence gains reusable bounded context while source validation and
scoring provenance remain unchanged.

## Task 11: Integrate Artifacts into Review v1 Without Review v2

### Files

Modify:

```text
app/services/runtime.py
app/services/report_microbatch.py
app/services/evaluator_ext.py
app/agents/shadow_reviewer.py
app/services/review_workflow_store.py
app/services/review_execution.py
```

Extend:

```text
tests/test_durable_review_graph.py
tests/test_expert_evaluator.py
tests/test_report_microbatch.py
tests/test_langgraph_heartbeat_recovery_postgres.py
tests/test_review_workflow_store.py
tests/test_workflow_thread_lock.py
```

### Test First

Cover Question Review Effect attempt 1 creating an Artifact, attempt 2 reusing
it, completed Artifact plus failed outer Review Effect, outer Report lease loss,
inner Artifact lease loss, Effect claim expiry/reclaim while the compressor is
blocked, old-worker business projection rejection, max-parallel Reviews, exact
observed Evidence, compression validation fallback, and unchanged Review v1
State schema.

### Implementation

Review compression runs inside the existing Question Review Effect provider
closure. The Artifact identity excludes `provider_attempt`; the outer Review
Effect operation key keeps its existing attempt behavior.

Change the Review Effect Provider contract so the closure receives an
authoritative ownership capability:

```python
def run_effect(
    ...,
    provider: Callable[[ReviewEffectOwnership], dict],
) -> dict:
    ...
```

`ReviewEffectOwnership.ensure_owned()` synchronously verifies both the active
Report Job lease and Review Effect row using job ID, worker ID, job lease token,
operation key, Effect claim token, fencing version, running status, and both
unexpired leases. It does not merely read `ReviewEffectHeartbeat._lost`.

Add `PostgresReviewWorkflowStore.assert_effect_owned(claim) -> bool` using the
same joined Effect/Report Job predicate as heartbeat renewal without extending
either lease. `ReviewEffectHeartbeat` implements `ReviewEffectOwnership`, calls
the synchronous predicate from `ensure_owned()`, and performs an initial check
in `__enter__()` before starting its thread. `run_effect()` passes that object
to the Provider closure.

The runtime Provider closure accepts the capability and passes
`effect_ownership.ensure_owned` to the Context Compression Runner as parent
ownership. The callback is invoked before the compressor call, before Artifact
completion, before Question Evaluation projection, and before outer Review
Effect completion.

The parent ownership callback verifies the active Report Job lease and Review
Effect claim before Artifact completion is used to complete the question
Effect. If the Artifact completes but the outer Effect fails, a later attempt
reuses the Artifact.

Required fault assertions are:

1. The compressor blocks while the Effect claim expires and is reclaimed.
2. The old worker may have completed an Artifact but cannot complete its old
   Review Effect.
3. The old worker cannot project a Question Evaluation into business tables.
4. The replacement Effect reuses the completed Artifact and total compression
   calls remain one.

Do not add Artifact refs to accumulated `question_outcomes`. Review v1 derives
the identity from its immutable input manifest at the provider boundary.

### Completion

Review retries avoid repeated compression while Review v1 checkpoint shape and
exact scoring provenance remain stable.

## Task 12: Implement the Checkpoint and Effect Recovery Matrix

### Files

Create or extend fault-injection suites:

```text
tests/test_context_artifact_recovery.py
tests/test_context_artifact_recovery_postgres.py
tests/test_langgraph_v2_recovery_postgres.py
```

### Required Scenarios

| Scenario | Expected Authority | Compression Provider Calls |
| --- | --- | ---: |
| Artifact completes before checkpoint, then process dies | Replay verifies and reuses completed row | 1 total |
| Checkpoint stores ref, then process dies before next node | Next node loads ref and verifies digest | 0 on recovery |
| Provider returns, process dies before Artifact completion | Lease expires; replacement may call again | Up to 2 |
| Owner A version 7, owner B reclaims version 8 | A completion rowcount 0; no stale patch | B only after reclaim, A may already have called |
| Artifact completes, process dies before owner ref creation | Retry finds by key and creates ref | 1 total |
| Checkpoint ref points to missing row | `ContextArtifactMissing`; no regeneration under same ref | 0 |
| Checkpoint digest differs from completed row | `ContextArtifactConflict` | 0 |
| Ref belongs to same owner/type/policy but source or focus identity differs | Full identity check raises `ContextArtifactConflict` | 0 |
| Artifact heartbeat returns false | Claim lost; no completion or state patch | At most current call |
| Artifact heartbeat raises | Claim lost with original cause; no patch | At most current call |
| Parent Generation lease expires during compression | Artifact may complete; Generation patch rejected | 1 compression, 0 stale Examiner calls after detection |
| Parent Review lease expires during compression | No outer Effect or Graph completion | At most current call |
| Review Effect claim is reclaimed while old compressor blocks | Old Effect/projection rejected; replacement reuses completed Artifact when available | 1 total after durable Artifact completion |
| Live claim belongs to another worker | Stable busy/retry path; no duplicate call | 0 for contender |
| v1 checkpoint resumes after v2 deployment | v1 graph only; no Artifact behavior added | 0 compression |
| v2 short context resumes | deterministic route, no Artifact | 0 compression |
| Retention runs while a checkpoint owner ref exists | Artifact retained | 0 |

### Completion

Call-count assertions and SQL row-count assertions prove the exact durability
boundary and prevent an exactly-once claim.

## Task 13: Add Reference-Aware Retention and Explicit Purge

### Files

Modify:

```text
app/services/config.py
app/services/durable_workflow_maintenance.py
app/services/interview_workflow.py
app/services/review_workflow.py
app/services/runtime.py
app/ports/context_artifacts.py
app/services/in_memory_context_artifact_store.py
app/services/context_artifact_store.py
```

Extend:

```text
tests/test_durable_workflow_maintenance.py
tests/test_langgraph_recovery_postgres.py
tests/test_durable_review_recovery_postgres.py
tests/test_in_memory_context_artifact_store.py
tests/test_context_artifact_store_postgres.py
```

### Test First

Cover active owner refs, explicit session purge, explicit Review job purge,
unattached completed rows, failed rows, expired Prep refs, cleanup idempotency,
partial purge retry, cleanup/database failure, privacy-safe maintenance logs,
and bounded batch deletion.

### Implementation

Add configuration such as:

```dotenv
CONTEXT_ARTIFACT_UNREFERENCED_RETENTION_HOURS=24
CONTEXT_ARTIFACT_FAILED_RETENTION_HOURS=24
CONTEXT_ARTIFACT_PREP_REF_RETENTION_HOURS=168
CONTEXT_ARTIFACT_CLEANUP_BATCH_SIZE=200
```

Session purge order is:

```text
delete LangGraph thread
-> delete session Artifact owner refs
-> delete generation/workflow control rows
-> business session cascade/explicit cleanup as currently owned
-> later maintenance deletes now-unreferenced Artifacts
```

Review purge deletes its checkpointer thread and owner refs before the Report
Job rows disappear. Cleanup is bounded and does not delete any Artifact that
still has a ref.

Maintenance constructs `ContextArtifactCleanupPolicy`, first deletes expired
`prep_run` refs up to the batch limit, then deletes unreferenced completed and
failed Artifacts using their separate cutoffs. PostgreSQL selection uses
bounded CTEs with `FOR UPDATE SKIP LOCKED`. Return and log the three
`ContextArtifactCleanupResult` counts separately.

Extend `MaintenanceResult` with counts only.

### Completion

Artifact retention cannot break a live checkpoint reference, and explicit
purge eventually removes derived payloads.

## Task 14: Extend Privacy, Failure Classification, and Runtime Signals

### Files

Modify:

```text
app/services/trace_sanitization.py
app/services/agent_runtime.py
app/services/runtime_work.py
app/services/runtime_signal_metrics.py
app/services/langgraph_canary_status.py
```

### Test First

Prove blocked payload/ref/ID/digest fields, allowed exact numeric counts and
booleans, stable error codes, single signal ownership, absent exception text,
file/PostgreSQL recorder parity, and privacy-safe canary output.

### Implementation

Add aggregate signals such as:

```text
context_artifact_created
context_artifact_reused
context_artifact_busy
context_artifact_validation_failed
context_artifact_lease_lost
context_artifact_fenced_write_rejected
context_artifact_missing
context_artifact_conflict
context_artifact_deterministic_fallback
context_artifact_unreferenced_cleanup
context_artifact_provider_duplicate_window
```

Signals contain counts and workflow/artifact type enums only. Artifact refs,
keys, IDs, source digests, output digests, owner IDs, source content, and
payloads are forbidden.

Canary `ROLL_BACK` conditions include conflict, missing live reference,
successful stale state patch, digest mismatch, privacy violation, or v1 recovery
regression. `HOLD` conditions include repeated lease loss, excessive busy rate,
validation failure, deterministic fallback, duplicate-provider windows,
capacity exhaustion, or insufficient samples.

### Completion

Operators can detect correctness and quality problems without seeing any
Artifact or source payload.

## Task 15: Recalculate PostgreSQL Capacity and Preflight

### Files

Modify:

```text
app/services/postgres_connections.py
app/services/postgres_capacity.py
app/services/runtime.py
scripts/runtime_preflight.py
scripts/postgres_capacity_acceptance.py
```

Extend Stage 48 capacity and saturation tests.

### Test First

Cover one Interview Generation heartbeat plus one Artifact heartbeat, parallel
Review Report heartbeat plus Review Effect heartbeats plus Artifact heartbeats,
pool acquisition timeout, telemetry saturation independence, advisory-lock
independence, Checkpointer independence, and clean shutdown.

### Implementation

Update the business-domain worst-case formula to include Artifact concurrency:

```text
business_required >=
    normal_business_queries
  + interview_generation_heartbeats
  + context_artifact_heartbeats
  + parallel_review_effect_heartbeats
  + parallel_review_artifact_heartbeats
  + safety_margin
```

Do not create a separate direct-connection path for Artifacts. If the existing
business maximum cannot safely satisfy the bound, increase the explicit pool
budget and total database-capacity preflight together rather than relying on
unbounded waits.

Preflight verifies schema and upgraded CHECK/FK/index contracts, policies,
model profiles, resolved non-secret `ContextCompressorConfig`, compressor
estimator, stable trusted privacy scope, single-tenant/multi-tenant safety,
positive lease/retention/batch values, the complete creation/consumption gating
matrix, safe defaults, v1/v2 graph registration, and pool capacity before
enabling a Stage 50 flag.

### Completion

Stage 50 cannot be enabled under a connection budget that is correct only for
Stage 48 workloads.

## Task 16: Add Acceptance Automation and Operator Documentation

### Files

Create:

```text
scripts/langgraph_stage50_acceptance.py
docs/langgraph-stage50-context-artifact-acceptance.md
```

Update:

```text
.env.example
README.md
```

### Test First

Cover acceptance status evaluation, missing PostgreSQL gate, migration
readiness, exact prefixed foreign key, upgraded engine constraint, release
defaults including the Evidence flag, v1/v2 registration, privacy-scope and
compressor-config readiness, privacy scan, call-count fault matrix, capacity
gate, and historical Artifact metric absence.

### Implementation

The repository command is:

```powershell
& 'F:\python3.11\python.exe' -m scripts.langgraph_stage50_acceptance
```

Successful repository output is:

```text
READY_FOR_CONTEXT_ARTIFACT_SHADOW
PRODUCTION_OBSERVATION=NOT_RUN
```

It must not report production `PASS`.

The operator document includes migration, preflight, shadow flags, canary
sample requirements, HOLD/ROLL_BACK criteria, drain behavior, ref-aware purge,
and the exact statement that provider calls may duplicate before durable
Artifact completion.

### Completion

Repository readiness is automated, privacy-audited, and operationally distinct
from production observation.

---

## Required Test Matrix

### Identity and Schema

- Every identity field changes the Artifact key when it should.
- `artifact_key` is absent from canonical identity material and cannot
  participate in its own digest.
- Attempt, worker, token, and time fields do not affect identity.
- Identical content under different privacy scopes produces different keys.
- Compressor provider/model/settings changes produce different keys without
  preventing exact-identity reads of historical completed rows.
- Canonical JSON is stable across insertion order and repeated runs.
- Every Artifact type validates its exact schema version.
- Unknown schema/policy/prompt versions fail closed.
- Output payload digest is stable and verified on every load.

### Ownership and Fencing

- First claim, live contention, expired reclaim, failed reclaim.
- Fencing version increments on reclaim.
- Stale heartbeat, completion, and failure are rejected.
- Heartbeat false and exception both fail closed.
- Initial ownership is checked before the heartbeat thread starts.
- Parent and Artifact authority are both required for workflow patches.

### Semantic Validation

- Unknown source anchors are rejected.
- Exact excerpts must be continuous source substrings.
- Truncation markers cannot become supporting excerpts.
- Fabricated Evidence/message IDs are rejected.
- Newly introduced numbers and code identifiers are rejected unless grounded.
- Oversized units/payloads are rejected.
- Invalid output falls back deterministically without replacing a successful
  existing Artifact.

### Prep

- Short source does not compress.
- Large source creates or reuses one Artifact.
- Identical retries reuse after durable completion.
- Changed JD, Resume, Knowledge manifest, policy, model, or target budget creates
  a new identity.
- Public API payload contains no Artifact internals.

### Interview v2

- v1 and v2 register together.
- Existing v1 sessions always use v1.
- Only new explicitly configured sessions use v2.
- Short v2 contexts match v1 business behavior without compression.
- Latest answer and current question remain raw bounded representations.
- Older conversation Artifact is advisory only.
- Generation retry reuses completed Artifact.
- Artifact fields are cleared after projection.
- No compressed payload enters State or checkpoints.

### Evidence and Review

- Identity/hash/manifest checks occur before compression.
- Semantic Evidence never becomes candidate scoring Evidence.
- Review attempt 2 reuses attempt-1 Artifact.
- Outer Review Effect failure does not invalidate a completed Artifact.
- Review v1 State schema remains unchanged.
- Parallel Review capacity stays within the configured business pool.

### Recovery and Retention

- Complete-before-checkpoint call count equals one.
- Replay of a completed Artifact calls the provider zero times.
- Return-before-completion may call twice and is reported truthfully.
- Missing/conflicting refs do not regenerate silently.
- Active refs block cleanup.
- Completed and failed rows obey different cutoffs.
- Expired Prep refs are deleted separately and respect the batch limit.
- Concurrent maintenance uses bounded `SKIP LOCKED` selection.
- Explicit purge removes refs before unreferenced Artifact cleanup.
- Cleanup is bounded, idempotent, and privacy-safe.

### Privacy

- No source or compressed payload in Agent Run, signals, canary, logs, or
  exceptions.
- No Artifact ref, ID, key, owner ID, or digest in telemetry.
- Numeric counts and exact machine enums are allowlisted.
- Credential, DSN, token, prompt, answer, Resume, JD, Evidence, and summary keys
  remain blocked.

### Gating

- Shadow creation never changes the business-operation provider input.
- Interview Conversation can be consumed while Evidence remains disabled.
- Interview Evidence requires both Interview and Evidence gates.
- Review Evidence requires both Review and Evidence gates.
- With every flag false, Store claim/load/create call count is zero.

## Combined Context, Ownership, and Fault Matrix

| Scenario | Business Result | Artifact Result | Provider Calls |
| --- | --- | --- | ---: |
| Short context | Deterministic path | None | Business provider 1 |
| Eligible long context | Compressed advisory context | Completed | Compression 1, business 1 |
| Completed Artifact replay | Same bounded context | Reused | Compression 0 |
| Live Artifact owner | Retry/HOLD path | Busy | Compression 0 for contender |
| Compression provider fails | Deterministic fallback if it fits | Failed/reclaimable | Compression 1 |
| Compression output invalid | Deterministic fallback | Failed with stable code | Compression 1 |
| Artifact completes, checkpoint lost | Replay reuses | Completed | Compression 1 total |
| Provider returned, completion lost | Replacement may repeat | Reclaimed | Compression up to 2 |
| Artifact lease lost | No stale completion | Running/reclaimed later | Current call may have occurred |
| Parent lease lost | No workflow patch | Completed or reclaimable | No later business call by stale owner |
| Missing checkpoint ref | Stable recovery failure | Missing | 0 |
| Digest conflict | Non-retryable conflict | Unchanged | 0 |
| Retention with live ref | Workflow unaffected | Retained | 0 |
| v1 replay after v2 deploy | v1 behavior | No Stage 50 Artifact requirement | 0 compression |
| Telemetry failure | Business result unchanged | Ownership unchanged | Unchanged |

## Rollout Plan

### Phase 0: Migration and Disabled Defaults

Apply the Stage 50 schema migration, validate it, register v1 and v2, and keep
all compression and v2 defaults disabled.

### Phase 1: Offline and Shadow Artifact Measurement

Enable `CONTEXT_COMPRESSION_SHADOW_ENABLED` only in an authorized environment.
Compute eligibility and optionally create Artifacts, but continue using the
Stage 49 deterministic business input. Do not issue a second business-provider
call.

Observe eligibility, creation/reuse, validation failure, source segment count,
estimated/actual compressor usage, latency, pool acquisition, and cleanup.

### Phase 2: Prep Compression Canary

Enable Prep consumption for a small authorized sample. Compare Plan quality,
section coverage, latency, token cost, fallback, and privacy audit results.

### Phase 3: Interview v2 Deterministic Canary

Register and assign a small number of new sessions to v2 while
`CONTEXT_COMPRESSION_INTERVIEW_ENABLED=false`. Prove v1/v2 parity, recovery,
pool capacity, and no State payload growth.

### Phase 4: Interview Conversation Compression Canary

Enable Artifact consumption for eligible v2 sessions only. Observe follow-up
relevance, latest-answer retention, Artifact reuse, duplicate-provider windows,
Generation lease loss, latency, and final prompt utilization.

Keep `CONTEXT_COMPRESSION_EVIDENCE_ENABLED=false` so this phase consumes only
Conversation Artifacts.

### Phase 5: Evidence Compression Canary

Enable Evidence Artifact use after conversation results are accepted. Observe
Knowledge conflict/degradation rates, exact excerpt integrity, and scoring
provenance.

Set `CONTEXT_COMPRESSION_EVIDENCE_ENABLED=true` only for the explicitly
authorized workflow environment. Review Evidence remains off until the Review
workflow flag is separately enabled.

### Phase 6: Review Compression Canary

Enable Review integration separately. Observe parallel heartbeat capacity,
Review Effect retries, Artifact reuse across attempts, scoring drift, exact
candidate Evidence, and completion latency.

### Phase 7: Joint Drain and Recovery Observation

Run the required observation window, stop new v2/compression assignment, drain
active work, replay representative v1/v2 sessions and Review jobs, execute
ref-aware cleanup dry-run, and evaluate Stage 50 canary status.

No phase automatically advances the next phase.

## Final Repository Gates

Run in this order:

1. Artifact identity, schema, validator, and in-memory ownership tests.
2. Compression Agent and Context Budget tests.
3. PostgreSQL Artifact Store and migration tests.
4. Claim/heartbeat/fencing and parent-authority tests.
5. Prep, Interview v2, Evidence, and Review targeted tests.
6. Full checkpoint/effect recovery and provider call-count matrix.
7. Retention, purge, privacy, signals, and canary tests.
8. Stage 46-49 ownership, recovery, heartbeat, telemetry, connection-capacity,
   Context Budget, and release-contract regressions.
9. Full Python regression.
10. Existing browser smoke/E2E gates; no screenshots are required.
11. Privacy scan and `git diff --check`.
12. `python -m scripts.langgraph_stage49_acceptance`.
13. `python -m scripts.langgraph_stage50_acceptance`.

The final command must report:

```text
READY_FOR_CONTEXT_ARTIFACT_SHADOW
PRODUCTION_OBSERVATION=NOT_RUN
```

## Definition of Done

Stage 50 is complete only when all of the following are true:

1. Stage 49 production Context Budget evidence was accepted before Stage 50
   rollout began.
2. All three Artifact types have versioned schemas and policies.
3. Artifact identities include source, policy, model, prompt, settings, privacy
   scope, and target budget.
4. Identity material excludes `artifact_key`; the key is one canonical derived
   SHA-256 result.
5. Attempt and worker data do not fragment reusable identities.
6. Different privacy scopes cannot share an Artifact key.
7. Compressor configuration is explicit, non-secret, and identity-bound.
8. Artifact output is schema-, token-, anchor-, excerpt-, identifier-, and
   digest-validated before completion.
9. Completed Artifacts are write once and terminal rows clear all claim fields.
10. Expired/failed claims are reclaimable with a higher fencing version.
11. Heartbeat false and exceptions both fail closed with the original cause.
12. Parent ownership proofs are synchronous authoritative database checks.
13. Stale owners cannot complete, fail, project business data, or emit a
    workflow state patch.
14. Completed-before-checkpoint replay uses exactly one total compression call.
15. Completed Artifact replay uses zero compression calls.
16. Return-before-completion tests truthfully allow a duplicate provider call.
17. Checkpoints contain refs/digests only, never compressed payloads.
18. `load_ref()` verifies owner, purpose, complete immutable identity, digest,
    completed status, and payload schema.
19. Missing, digest-conflicting, and source/focus-identity-conflicting refs
    raise stable non-silent recovery failures.
20. Current question and latest candidate answer retain deterministic raw
    representations.
21. Semantic summaries never become exact candidate scoring Evidence.
22. Interview v1 and v2 are registered together and recover independently.
23. Only new explicitly configured work is assigned to v2 through the exact
    persisted version chain.
24. Review v1 exposes an authoritative Effect ownership callback and reuses
    Artifacts across attempts without a State schema change.
25. Prep identical retries can reuse a completed source-identity Artifact.
26. Conversation and Evidence consumption have independent gates.
27. Artifact owner refs prevent unsafe age deletion.
28. Completed, failed, and expiring Prep refs use their configured independent
    retention policies and bounded cleanup.
29. Explicit session/job purge removes refs before Artifact cleanup.
30. Stage 48 capacity gates include Artifact heartbeat concurrency.
31. Runtime signals and Agent metadata contain no Artifact/source payloads,
    refs, IDs, keys, owner IDs, or digests.
32. The prefixed ref foreign key targets the exact current-prefix Artifact
    relation and runtime validation checks the upgraded v2 engine constraint.
33. All new flags default false, Interview default remains v1, and rollout
    percentages remain zero.
34. Stage 46-49 regressions pass.
35. Production observation remains `NOT_RUN`.
36. Repository status is `READY_FOR_CONTEXT_ARTIFACT_SHADOW`.

## Post-Stage-50 Backlog

### Stage 51: Review `langgraph-review-v2`

- Explicit per-question attempt records.
- Retry only failed questions.
- Bounded batch outcome accumulation.
- Partial progress persistence.
- Separate question and Report provider attempts.
- Artifact refs in a versioned Review State only where a later node consumes
  them.

### Stage 51.1: Provider Idempotency and Response Escrow

- Provider-native idempotency keys where supported.
- A separately designed non-authoritative response escrow.
- Validation/adoption by a replacement owner.
- No stale owner direct authoritative completion.

### Stage 52: Prompt Caching and Token Cost Optimization

- Stable provider capability profiles.
- Cached-input and cache-write token accounting.
- Byte-identical fixed prompt prefixes.
- Provider-reported cache hits only; no inferred hits.

### Stage 53: Checkpoint Lifecycle and Full State Externalization

- Completed-thread retention and backup/restore governance.
- Further externalization of raw Plan/message payloads from new Graph versions.
- Active-thread-safe cleanup and legal/privacy retention policy.
- No automatic rewrite of historical checkpoints.
