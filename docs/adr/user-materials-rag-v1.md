# ADR: User Materials and RAG product boundary V1

- Status: Accepted for Local V1 U0-U4; real PostgreSQL/pgvector acceptance remains pending
- Date: 2026-08-15
- Product scope: Local V1 / single local Principal
- Contract version: `user-materials-v1`
- Scope snapshot version: `interview-knowledge-scope-v1`
- Governing plan: `docs/superpowers/plans/2026-08-15-user-materials-rag-productization-plan.md` v1.4

## Context

The current RAG Console governs a shared system Knowledge Corpus: maintainers
validate entries, create a Corpus Version, verify a target manifest, and explicitly
activate a release. A user selecting a local Markdown or TXT file is a different
lifecycle. Treating that file as another Corpus release would grant the ordinary
product path system-knowledge publication powers, erase ownership, and couple one
person's deletion request to global release governance.

The runtime already resolves a Local Principal through the server-side
`PrincipalIdentityResolver`. It has no product account, login, tenant, role, or RBAC
model. Retrieval already has two authoritative channels (Semantic and Lexical), one
Fusion implementation, one Reranker, and one Evidence Gate. This decision extends
those boundaries; it does not replace them.

The S1-S9 invariants in the governing plan are normative and are adopted by
reference. This ADR records the implementation decisions needed to remove ambiguity;
it does not define a second security policy.

## Decision

### 1. Product and lifecycle ownership

System Knowledge and User Materials remain separate aggregates:

| Concern | System Knowledge | User Materials |
|---|---|---|
| Authority | Maintainer-governed global Corpus | Current Runtime Principal |
| Stable identity | Corpus Version and manifest | User Document and immutable Document Revision |
| Publication | Validate, create version, activate | Ingest one revision, then atomically mark the document ready |
| Selection | Active global Corpus | Explicit Plan/Session scope |
| Deletion | Corpus release governance | Owner-scoped deletion of source and all derived data |
| Capability | Existing RAG capability family | Exactly two User Materials capabilities |

User Materials never create, activate, retire, roll back, or re-embed a global
Corpus Version. They use append-only `user_documents`, `user_document_revisions`,
and `user_document_chunks` storage when PostgreSQL support is implemented. Those
tables are not an alternate representation of the global Corpus release tables.

### 2. Identity and ownership

The service obtains identity only from the existing server-side
`PrincipalIdentityResolver`. Public requests do not accept `principal_id`, and safe
responses do not expose it. Every future document-store or chunk-repository operation
must require the owner Principal explicitly; there is no owner-free convenience
query. A synthetic Principal A/B contract is sufficient to prove Local V1 isolation.
Cross-owner lookup is non-enumerable (`404` or the equivalent stable safe error).

This owner boundary does not authorize accounts, login, roles, tenants, teams,
sharing, administrators, or RBAC.

### 3. Frozen document contract

The stable Document identity and immutable Revision identity are distinct. Rename
changes Document metadata only. Content replacement creates a new Revision; at most
one Revision is active. Chunks carry owner, document, and revision identity so query
and deletion can enforce the same boundary without joining through an owner-free
path.

The first-stage ingest envelope is frozen to:

- `.md` with `text/markdown` or `.txt` with `text/plain`;
- valid UTF-8;
- non-empty normalized text;
- at most 1 MiB (`1_048_576` bytes);
- deterministic fake embeddings in automated tests;
- no PDF, DOCX, OCR, external Provider acceptance call, or global Corpus write.

Public status is exactly `processing | ready | failed | disabled | deleting`.
Internal processing stage is exactly
`validation | extraction | chunking | embedding | indexing`. Internal stage,
owner, paths, content hashes, embedding identity, raw exceptions, and original
content are not public response fields.

Stable safe ingest errors are:

```text
unsupported_file_type
file_too_large
invalid_utf8
empty_document
processing_failed
embedding_unavailable
index_write_failed
document_not_found
document_deleted
retry_not_allowed
```

### 4. Plan V2 knowledge scope

`InterviewPlanV2.knowledge_scope` is an immutable
`InterviewKnowledgeScopeSnapshot` and therefore participates in the existing
canonical `plan_sha256`. The internal snapshot contains:

```text
schema_version = interview-knowledge-scope-v1
include_system_knowledge
selected_documents[]
  document_id
  document_revision_id
  content_sha256
  allowed_usages = question | follow_up | feedback
selection_sha256
created_at
```

The resolver, not the client, supplies Revision ID, content hash, allowed usages,
selection hash, and creation time. Selected revisions are canonically ordered by
Document ID and Revision ID. `selection_sha256` hashes the scope schema version,
the system-knowledge switch, and those ordered selections. It excludes
`created_at`, so equivalent selections have the same selection identity. The full
Plan hash still includes `created_at` and the complete snapshot.

`owner_principal_id` is intentionally absent from the snapshot. Ownership is derived
from the Plan Family/Session Principal boundary and revalidated at Start. Start
copies the validated snapshot into the Session and accepts no second temporary
document list.

For an existing serialized Plan or compatible request with no `knowledge_scope`,
the deterministic mapping is:

```text
include_system_knowledge = true
selected_documents = []
created_at = null
```

`created_at = null` is reserved for that system-knowledge-only compatibility value.
No historical Plan is backfilled from the current materials library. This preserves
the existing behavior and keeps reparse/hash behavior deterministic.

Public Plan projections will expose only a safe document reference, display title,
and usable/unavailable status. They will not expose owner, Revision ID, content hash,
selection hash, or internal deletion state.

### 5. Retrieval topology

Source scope is a candidate constraint inside each existing channel, not a channel:

```text
System + selected User candidates -> unified Semantic rank
System + selected User candidates -> unified Lexical rank
Semantic rank + Lexical rank -> existing Fusion -> existing Rerank -> existing Evidence Gate
```

There is no System/User RRF, no third Fusion input, no per-source full Top-K
concatenation, and no source weight or hidden user-material priority. With all
sources disabled, retrieval returns explicit empty and does not widen scope.

### 6. Citation, scoring, and untrusted content

Selection means “allowed for this interview”; it does not mean “referenced.” A public
User Materials citation requires a scope-valid candidate that reached Final Evidence
and was consumed by the persisted question, follow-up, or feedback binding. Citation
projection is safe and bounded. After document deletion, historical reads show only
`availability=deleted`, `display_title=已删除资料`, and `excerpt=null`.

User file text is untrusted context. It continues through the existing context
selection, boundary wrapping, and final prompt enforcement. It never changes the
rubric, weights, passing line, numeric-score rules, or whether the candidate actually
answered. Raw file content is excluded from ordinary logs, metrics, and public traces.

### 7. Deletion

Owner deletion removes original bytes, extracted text, chunks, embeddings, and
retrieval caches, makes the document unselectable, and guarantees zero future
retrieval hits. A minimal content-free deleted state may remain in the Document
store. Historical Plan and Session entities are not rewritten; citation reads apply
the safe deleted projection. Partial deletion remains `deleting` or a safe failed
state and is never reported as success.

Disabling ingest does not disable permanent owner deletion.

### 8. Ports and services

The only User Materials persistence ports are:

```text
UserDocumentStorePort
UserDocumentChunkRepositoryPort
```

The application layer contains three Materials services and one scope resolver:

```text
UserDocumentService
UserDocumentIngestionService
UserDocumentDeletionService
InterviewKnowledgeScopeResolver
```

Deletion orchestration remains an application service. U1 does not add a Job Store,
Deletion Port, coordinator port, workflow engine, or second HTTP/error client.

### 9. Capability ownership and rollback

Existing developer paths retain their existing controls:

```text
RAG_CONSOLE_ENABLED
RAG_LIVE_EXECUTION_ENABLED
RAG_CORPUS_WRITE_ENABLED
```

User Materials adds exactly:

```text
USER_MATERIALS_ENABLED
USER_MATERIALS_INGEST_ENABLED
```

The first controls visibility/use in Prep and Retrieval. The second controls upload,
retry, and new Revision creation. Neither grants Corpus write. Rollback disables new
ingest and Materials participation, preserves owner deletion, preserves new tables,
performs no destructive drop, and does not change long-term memory.

### 10. Milestone boundary

U1 is independently complete at the owner-scoped Markdown/TXT lifecycle and
Materials product entry. It does not need Prep selection, Plan/Session resolution,
business retrieval, citation, report integration, PDF, or an external Provider.
U2 owns Scope resolution and source-aware retrieval. U3 owns binding-backed citation
and scoring isolation. U4 owns product/docs/acceptance closure. Text-layer PDF is an
optional U5 and is not a U1-U4 completion condition.

### 11. U4 closure record

The U0-U4 Local V1 boundary is implemented and accepted by the governing plan's
targeted and full non-protected evidence. U4 closes product copy, Help, runbook,
architecture explanation, acceptance composition, and the complete non-protected
local regression. The final parent review and S1-S9 cross-milestone audit completed
on 2026-08-16; real PostgreSQL/pgvector and external Provider acceptance remain
separate protected boundaries.

The closure preserves the ordinary navigation order (`准备`, `报告`, `我的资料`,
`我的记忆`, `帮助`) and keeps `/rag/lab` as a maintainer-only route. It does not
include optional text-layer PDF work from U5. Protected PostgreSQL nodes, real
Provider calls, global Corpus lifecycle writes, Ground Truth, and paired evaluation
remain outside this closure and cannot be reported as passed.

## Enforcement

Contract tests freeze the statuses, stages, formats, size limit, capability names,
port names, legacy Scope mapping, Scope immutability, selection hash, and Plan hash
participation. Architecture tests enforce:

- bilateral import/symbol separation between global Corpus lifecycle and User Materials;
- absence of role/RBAC/tenant/account/login/admin concepts in Materials code;
- no User Materials Fusion/RRF implementation;
- exactly two positional inputs to the existing Fusion: Semantic and Lexical;
- no additional User Materials persistence Port name.

U1 and later milestones must extend these tests with store, Principal A/B, deletion
zero-hit, API projection, Session copy, real binding, and scoring-isolation evidence.
U0 does not claim those runtime behaviors already exist.

## Consequences

- Plan hashes for newly materialized V2 objects include the deterministic legacy
  scope even when callers omit it. Persisted payload readers must use the compatibility
  mapping rather than infer documents from current state.
- User Materials require separate storage and owner-scoped queries, but avoid a
  second Corpus release system and avoid RBAC complexity.
- Retrieval integration must merge sources before each channel ranking; it cannot be
  implemented by adding source-specific Fusion inputs.
- Deletion is stronger than disabling and requires coordinated derived-data removal,
  but does not require a separate production-grade workflow engine in Local V1.

## Rejected alternatives

- Reuse `/api/rag/corpus` for file upload: rejected because it mixes publication and
  owner lifecycles.
- Add roles/RBAC for a local Principal: rejected because no account boundary exists
  and ownership can be proved with synthetic A/B identities.
- Add System and User as RRF channels: rejected because source is scope, not a ranking
  channel, and would create hidden quota/weight changes.
- Store only mutable document content: rejected because Plan/Session replay requires
  immutable Revision and content-hash identity.
- Rewrite historical Plans/Citations on deletion: rejected in favor of content
  deletion plus safe read projection.
- Make PDF part of the first closure: rejected; it remains optional U5.

## U0 execution baseline

The following paths were already modified or untracked before U0 and are outside this
ADR change. They must not be cleaned, reset, overwritten, or included as U0 evidence:

```text
M  .env.example
M  app/runtime/config/memory.py
M  frontend/src/pages/RagConsolePage.test.jsx
M  frontend/src/pages/RagOverviewPage.jsx
M  tests/acceptance/test_memory_operational_shadow_acceptance.py
M  tests/acceptance/test_principal_memory_api.py
M  tests/contracts/test_memory_production_shadow_approval_packet.py
M  tests/unit/test_local_principal_runtime.py
M  tests/unit/test_memory_config.py
M  tests/unit/test_principal_memory_operations.py
?? docs/superpowers/plans/2026-08-15-user-materials-rag-productization-plan.md
```

## Follow-up

Proceed in U1-U4 order from the governing plan. PostgreSQL tests remain protected and
require separate authorization. External Provider calls and global Corpus writes are
not acceptance prerequisites.
