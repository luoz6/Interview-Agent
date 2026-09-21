# MA0-T08 - Agent Artifact Persistence Inventory

## Status

```text
TASK = MA0-T08
STATUS = COMPLETE
INVENTORY_STATUS = FROZEN_FOR_MA0_REVIEW
TASK_ACCEPTANCE = PASS
NEXT_TASK = NOT_STARTED
```

This inventory records the repository's current Artifact and artifact-like
ownership. It distinguishes durable business records from runtime models and
transport task memory. It does not introduce a neutral `DomainArtifact` store
and does not claim that A2A output is durable.

## Executive Matrix

| Asset family | Current durable owner | Runtime-only or projection forms | Immutable identity / schema / digest | Session deletion |
| --- | --- | --- | --- | --- |
| Context artifacts | `ContextArtifactPostgresAdapter` (`*_context_artifacts`, `*_context_artifact_refs`) | `InMemoryContextArtifactStore`; compressor result objects | `artifact_id`, deterministic `artifact_key`, opaque `artifact_ref`, `artifact_sha256`, `output_schema_version`, optional `identity_schema_version=identity-v1` | Yes, owner refs and unreferenced payload cleanup; session owner is `interview_session` |
| Report artifacts | `PostgresReportArtifactStore` (`*_report_artifacts`, `*_report_heads`, jobs) | `InMemoryReportArtifactStore`; report Pydantic models | `report_id`, session revision, `source_job_id`, `artifact_sha256`, schema/rubric versions, immutable supersession chain | Yes, authorized deleting-session path removes heads, artifacts and jobs |
| Evaluation artifacts | Question evaluation repository / report review stores; durable evaluation rows and report Artifact payload | A2A `EvaluationArtifactPayload` and evaluation-set payload; evaluator return objects | Evaluation row schema/version and input/output digests where applicable; A2A base `schema_version` and timestamp but no durable identity | Session-owned rows are deleted with session; report Artifact history deletion removes embedded report evaluations |
| Grounding / evidence artifacts | No standalone generic Artifact store; plan/session `PrepContext`, `BaseEvidenceBundle`, `KnowledgeBindingSnapshot`, and review bindings are serialized by their owning stores; source corpus is owned by knowledge/pgvector stores | `GroundingResult`, `GroundedCandidate`, retrieval result and citation projections | Evidence `content_sha256`, corpus manifest SHA-256, retrieval request/query SHA-256, binding/bundle IDs, component/profile versions | Indirectly with owning plan/session/report; user-document citation becomes content-free/deleted projection; no independent grounding purge API |
| Interview plans | `PostgresPrepPlanStore` (`*_prep_plans`, `*_prep_plan_versions`) plus draft/revision stores | Memory prep-plan/draft stores; `InterviewPlan` model | `plan_id`, monotonic `plan_version`, `source_sha256`, row schema versions; plan revision has revision ID, parent, plan SHA-256 and configuration snapshot | TTL/consumption cleanup; source-draft deletion removes non-consumed plans; consumed plan is retained for configured period |
| Questions / follow-ups | Session/message repositories, `PostgresInterviewGenerationStore`, `PostgresDecisionStore`, workflow command/projection tables | Graph state models, `QuestionIntentV1`, `RenderedQuestionV1`, decision objects | Question intent/rendered schema versions and prompt/input digests; generation ID/attempt and decision ID/source command ID; session projection digest and CAS revision | Yes through session workflow/generation/decision deletion paths |
| A2A output artifacts | None beyond caller's selected application store | `LocalA2AServer._tasks`, official SDK `InMemoryTaskStore`/`InMemoryQueueManager`, `A2ATask.output_artifact` | `DomainArtifact.schema_version`, `artifact_type`, `created_at`, transport task/context IDs outside payload; no durable Artifact ID or digest contract in base class | Process restart drops them; session deletion cannot recover or purge a durable A2A ledger because none exists |

## 1. Context Artifacts

### Owner and lifecycle

`app/adapters/postgres/context_artifacts.py` is the durable owner for compressed
conversation, question-memory, evidence-compression, and prep-context records.
The adapter stores an artifact row and a separate owner reference row. The
reference binds an artifact to `prep_run`, `interview_session`, or `review_job`
and a purpose such as interview conversation context or review evidence context.
The memory adapter has matching behavior for isolated local tests and preview
runtimes but is process-local.

The compressor claims a deterministic identity before provider work, persists a
completed/failed result, and validates the stored payload when a reference is
read. Lease token, fencing version, claim expiry, and failure state are durable
control data, not Artifact payload fields.

### Identity and integrity

- `ContextArtifactIdentity.artifact_key` is SHA-256 over canonical identity
  material: artifact type, privacy scope, source/message manifest, semantic
  focus, policy/prompt/output versions, provider/model/settings, and target
  tokens.
- The database assigns `artifact_id`; references expose an opaque
  `context-artifact-ref:<id>` string.
- `artifact_sha256` is checked against the stored serialized payload.
- Payload models use fixed schema versions such as
  `question-conversation-v1`, `question-memory-v1`, and
  `evidence-compression-v1`; identity extensions use `identity-v1`.

### Deletion and retention

`delete_owner_refs()` deletes owner references and then cleans completed or
failed artifacts only when no remaining reference/retention policy protects
them. Session deletion invokes the `interview_session` owner path. This is a
real durable deletion boundary, although shared artifacts can outlive one
owner while another reference remains.

## 2. Report Artifacts

`PostgresReportArtifactStore` is the canonical report Artifact owner. It keeps
jobs, immutable report artifacts, and a per-session head. Publishing locks the
session and job, validates the active lease/fencing token, inserts the immutable
artifact, advances the head, marks the job completed, and updates the report
compatibility projection in the same business transaction. Replays of the same
job return the existing artifact only when its digest agrees.

`ReportArtifact` requires a UUID report ID, session ID, positive revision,
schema/rubric/generation/score/coverage versions, source job, payload digest,
and optional source/supersedes IDs. The supersession chain and unique
`(session_id, revision)` constraint provide immutable lineage. The report model
rejects principal-memory fields so Artifact ownership cannot silently absorb
private memory.

The store's authorized deletion method requires the session row to already be
in `deleting` state, then removes report heads, artifacts, and jobs. Foreign
keys also cascade with the session. The in-memory report store mirrors behavior
for non-PostgreSQL runtimes but is not restart-safe.

## 3. Evaluation Artifacts

There are two layers and they must not be conflated:

1. Durable question evaluations are stored by the PostgreSQL question-evaluation
   repository and consumed by report/review assembly. They are session-owned
   business records, generally upserted inside the session store's explicit
   UoW. Report review stores also persist evaluation/review effect state and
   commit it with the report Artifact boundary where applicable.
2. `EvaluationArtifactPayload` and `EvaluationArtifactSetPayload` are typed A2A
   transport/domain payloads. They validate score/evidence sufficiency and have
   `schema_version`, `artifact_type`, `created_at`, and context metadata through
   `DomainArtifact`, but they are not a durable ledger. They become durable only
   if an application owner explicitly stores the underlying evaluation in its
   own repository or report Artifact payload.

Evaluation records therefore have durable business ownership, but there is no
generic cross-agent evaluation Artifact store yet. The Scheduler must reference
the owning evaluation record or report Artifact, not treat an A2A payload as
proof of committed completion.

## 4. Grounding and Evidence Artifacts

Knowledge retrieval has durable source ownership in the corpus/pgvector and
user-material stores. The consumed grounding result is represented as bounded
business projections:

- `EvidenceRef` binds an evidence ID to title/domain/source type,
  `content_sha256`, and (for system evidence) `corpus_manifest_sha256`.
- `BaseEvidenceBundle` records retrieval request ID, query SHA-256, structured
  query snapshot, engine/profile/component versions, candidate refs, corpus
  manifest and creation time.
- `KnowledgeBindingSnapshot` and question/review evidence bindings freeze the
  selected evidence IDs and source scope for plan/question/review lineage.
- `GroundingResult` and `GroundedCandidate` are runtime assembly objects; they
  are not independent persistence owners.

The interview plan store serializes the prep context, binding snapshot and
evidence catalog in its plan JSON/internal context JSON. Durable session state
then binds the consumed plan snapshot and plan SHA-256. Review flows carry
evidence bindings into review/report-owned records. This gives durable lineage
through the owner, but not a reusable grounding Artifact table.

User-document citations have a deletion-safe projection: after document
deletion, the public citation can become `已删除资料` without title, location,
excerpt, or safe reference. System corpus evidence instead remains governed by
corpus retention/version policy. No single `delete_grounding_artifact()` API
exists today.

## 5. Interview Plan Persistence

`PostgresPrepPlanStore` owns editable, consumed and expired plans. It stores the
public plan JSON, internal context JSON, source digest, source draft link,
expiry, consumption command/session identity, and row schema version. Every
revision is copied into `*_prep_plan_versions` with a public snapshot, change
type and row schema version. Plan edits use row locking and expected-version
CAS; cleanup transitions expired plans and deletes expired/consumed rows after
configured retention.

`InterviewPlan` itself is a runtime domain model. Durable identity is supplied
by the plan record (`plan_id`, version and source SHA-256), not by an A2A task.
Plan revision contracts additionally carry family/revision IDs, parent revision,
plan SHA-256, source SHA-256, configuration snapshot and audit hashes. A
session's `SessionPlanBinding` freezes the plan origin, revision metadata,
configuration snapshot, plan snapshot and plan SHA-256 so later plan edits do
not silently change an active interview.

## 6. Question and Follow-up Persistence

Question intent and rendered question models are typed, versioned runtime
artifacts (`question-intent-v1`, `rendered-question-v1`). Rendered questions
carry intent/context/knowledge-scope/prompt digests and generation identity.
They are published into durable session messages/projection state only at the
publication boundary; checkpoints may retain an uncommitted result payload but
do not publish it as a business question.

Main-question and follow-up generation attempts are owned by
`PostgresInterviewGenerationStore`, with generation ID, attempt number, source
identity, lease token, fencing version, expiry, result digests and terminal
status. Follow-up decisions are owned by `PostgresDecisionStore`, keyed by
session/source command and carrying decision/input/prompt hashes plus attempt
usage metadata. Workflow commands and projection rows bind the accepted result
to a session revision using CAS.

These are durable domain-specific records, not neutral Scheduler Artifacts.
They support replay, stale-worker rejection and session deletion through their
own stores. Their stable IDs and schema/digest fields are strong inputs to a
future `DomainArtifactRef`, but should be referenced rather than copied.

## 7. A2A Output Artifacts

`DomainArtifact` is a frozen Pydantic transport payload with `schema_version`,
`artifact_type`, `created_at`, and `context_ref`. Evaluation, grounding,
follow-up, interview-plan and report payload subclasses add typed fields and
validation. Transport metadata (`taskId`, `contextId`, `artifactId`,
`messageId`) is intentionally outside the business payload.

`LocalA2AServer` stores complete tasks and `output_artifact` in
`self._tasks`, a Python dictionary. Its idempotency lookup, retry behavior,
cancel state and artifact result disappear on process restart. Official A2A
routes construct one SDK `InMemoryTaskStore` and `InMemoryQueueManager` per
agent route; those are also process-local. No A2A store currently persists a
durable Artifact ID, payload digest, lease, attempt receipt, or reconciliation
record.

Therefore A2A output is an invocation transport result, not a Scheduler
Artifact authority. A future adapter may persist a validated output after the
Scheduler owns the invocation ledger and commit boundary.

## Cross-Cutting Answers

### Which Artifacts already have durable owners?

Context artifacts and report artifacts have dedicated PostgreSQL owners with
immutable identity/digest and deletion paths. Interview plans, question/
follow-up records, evaluations and session-bound grounding snapshots have
domain-specific durable owners. There is no generic cross-agent Artifact
repository.

### Which are runtime objects only?

`GroundingResult`, `GroundedCandidate`, evaluator/provider return objects,
unpersisted `DomainArtifact` instances, graph state before projection, and
in-memory adapter records are runtime objects. They become durable only after a
specific owner commits them.

### Which exist only in A2A task memory?

`A2ATask.output_artifact`, task status/attempts, LocalA2A `_tasks`, official SDK
task stores and queue managers, and their route-local cancellation state. These
are explicitly process-local and are not restart-safe invocation ledgers.

### Which have immutable identity, schema version and digest?

- Context: deterministic artifact key, artifact ID/ref, payload SHA-256,
  output/identity schema versions.
- Report: report ID, revision, source job, payload SHA-256, schema/rubric
  versions and supersession links.
- Plans: plan ID/version, source/plan SHA-256, row schema and revision schema.
- Questions/generation/decisions: generation/decision IDs, attempt/fence,
  schema versions and input/prompt/result digests.
- Grounding: bundle/binding/request IDs, content/query/corpus SHA-256 and
  engine/profile/component versions.
- A2A payloads: type/schema/timestamp only by default; no durable digest or
  identity contract.

### Which support session deletion?

Context refs, report history, session state/messages, workflow commands,
generation attempts, decisions, question evaluations and session-local memory
have explicit deletion or cascade paths. Prep plans have TTL/consumed cleanup
rather than a general session purge. Grounding snapshots disappear with their
owning plan/session/report records, while system corpus data is governed by its
own retention. A2A memory has no durable deletion surface; process restart is
the only effective reset.

## Scheduler Implications

1. The neutral Scheduler Artifact contract must reference existing owner IDs,
   schema versions and digests instead of copying Context, Report, Plan,
   Evaluation or Question payload models.
2. `ArtifactRef` needs owner type/key, artifact type/version, immutable digest,
   session/execution scope and deletion authority. A2A transport IDs must not be
   mistaken for this reference.
3. Artifact publication must happen inside the same canonical execution commit
   or through the explicit outbox/reconciliation protocol selected in MA0-T07.
4. Context and Report stores are the strongest reuse candidates; grounding and
   A2A require adapters around their current projection/transport boundaries.
5. Session deletion must call each owner-specific deletion path. A generic
   Scheduler deletion claim cannot assume that removing execution state removes
   report history, context references, plan retention or private memory.

## Evidence Index

- Context identity/models: `app/domain/context/artifacts.py`
- Context durable owner: `app/adapters/postgres/context_artifacts.py`
- Report Artifact models: `app/domain/report/artifact.py`
- Report durable owner and deletion: `app/adapters/persistence/postgres/report_artifact_store.py`
- Evaluation payloads: `app/a2a/contracts/evaluation.py`,
  `app/a2a/contracts/evaluation_set.py`; durable question evaluation repository:
  `app/adapters/postgres/question_evaluation_repository.py`
- Evidence lineage/bundles: `app/domain/knowledge/evidence.py`,
  `app/application/knowledge/grounding.py`,
  `app/domain/interview/prep.py`
- Plan owner/revisions: `app/adapters/persistence/postgres/prep_plan_store.py`,
  `app/adapters/persistence/postgres/plan_revision_store.py`,
  `app/domain/interview/plan_revision.py`,
  `app/domain/interview/session_plan_binding.py`
- Questions and generation: `app/domain/interview/question_intent.py`,
  `app/domain/interview/published_question.py`,
  `app/adapters/persistence/postgres/interview_generation_store.py`
- Follow-up decisions: `app/adapters/persistence/postgres/decision_store.py`
- A2A payload/task boundary: `app/a2a/contracts/common.py`,
  `app/a2a/protocol.py`, `app/a2a/server.py`, `app/a2a/official_server.py`
- Session deletion orchestration: `app/application/interview/session_deletion.py`,
  `app/runtime/session_deletion_worker.py`

## Verification

Executed on 2026-09-18:

```text
223 passed in 4.15s
```

The focused suite covered context Artifact contracts and lifecycle, evidence
contracts and deletion-safe projections, knowledge evaluation artifacts, report
Artifact publishing/replay, interview plan revisions, bootstrap outbox, runtime
outbox dispatch, and A2A task/artifact behavior.

## Acceptance Decision

```text
PASS
```

Reason: every required artifact family has a named current owner or an explicit
runtime-only classification; immutable identity, schema version, digest and
session-deletion behavior are recorded; A2A task memory is explicitly excluded
from durable ownership; and the focused artifact/persistence suite passes.
MA0-T09 has not been started.
