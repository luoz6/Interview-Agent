# Knowledge RAG V2 Architecture

The active V2 corpus is `memory-p1-zh-v4`. Its messaging pilot is RocketMQ,
covering at-least-once delivery, business idempotency, bounded retries and dead
letters, consumer-group queue allocation, operational backlog, and transaction
message checkback. Frozen V1 Kafka material remains available only through the
explicit compatibility path for historical replay.

## Runtime orchestration

`RuntimeKnowledgeRetrievalService` is the single application-layer owner of
stable engine assignment, Legacy/Hybrid selection, compare-only Shadow, and
candidate failure isolation. `RuntimeKnowledgeRepository` adapts that service
to the existing `search/get_by_ids` consumers without moving fusion or evidence
policy into pgvector.

Runtime configuration is resolved only through `KnowledgeRuntimeSettings`.
`KNOWLEDGE_ENGINE=hybrid-v2` plus `KNOWLEDGE_HYBRID_ROLLOUT_PERCENT` controls
new assignments; `KNOWLEDGE_SHADOW_ENABLED=true` always keeps Legacy as the
formal result and records only the sanitized comparison. Profile values use
`<profile-id>@<version>` and their channel and total timeouts are enforced by
the Hybrid application service.

PREP, FOLLOWUP, QUESTION_REVIEW, and REPORT_REPAIR have independent channel,
total, absolute P95, and relative P95 budgets. Defaults match the frozen Canary
runbook: 1500 ms, 800 ms, 1200 ms, and 1200 ms respectively. Invalid relations
such as a channel timeout above total timeout, or total timeout above absolute
P95, fail configuration loading.

Both engines emit `retrieval-trace-v2`. It records the intent, resolved profile,
query hash/length, hashed constraint and routing summaries, channel/fusion/rerank
summaries, evidence outcome, selected IDs and hashes, degraded-path latency, and
component versions. It never contains query/JD/resume/answer or knowledge body.
Shadow also records candidate compute and observed orchestration overhead.

PREP persists the hash-only assignment contract inside the private binding
snapshot. Reviewer targeted retrieval reuses that assignment, while bound-ID
replay remains the first choice and bypasses free retrieval.

The same private snapshot now persists the immutable evidence lineage rather
than reconstructing it from question tags later:

```text
BaseEvidenceBundle
    -> QuestionEvidenceBinding
    -> ReviewEvidenceBinding
    -> QuestionEvaluationRecord.record_metadata
```

The base bundle contains only hashed query facts, sanitized filter/profile
facts, component versions, corpus identity, and safe evidence references. It
does not contain query, JD, resume, answer, or knowledge body text. Each new V2
question binding points to the authoritative bundle and is validated against
the public hint before replay. The Reviewer uses that persisted binding ID as
its parent; the full final review binding is stored in the existing question
evaluation JSON envelope, so no parallel repository or database table exists.
Historical V2 snapshots without these fields remain readable through the
explicit compatibility path, but new snapshots do not synthesize parent IDs.

`EvidenceDecision` is validated as a domain value: unavailable evidence is
never empty or scorable, empty and not-evaluated evidence are not scorable, and
weak or insufficient evidence may only be LOW or NOT_SCORABLE. Retrieval Gate
therefore certifies retrieval integrity, not evaluation confidence. Only the
Evaluation Support Gate may produce task-specific confidence after a reviewed
Knowledge Unit, relevance, controlled source authority, signal coverage and
hard-negative checks have run.

The final Report boundary persists question records and then re-locks every
single-question feedback, score and dimension score from those records before
saving the report. Microbatch is the primary path. Full-session evaluation is
retained as a compatibility producer for stores or failures that cannot use
microbatch, but it cannot remain an independent scoring authority after the
records are persisted.

The active boundary is:

```text
Agent / workflow
    -> Knowledge application services
    -> Knowledge domain policy and contracts
    -> Ports
    -> pgvector / exact-term / metadata adapters
```

The pgvector adapter owns embedding calls, SQL, corpus reads, and raw semantic
candidates. It does not own RRF, evidence gates, Reviewer selection, or final
business ranking. Weighted fusion, deterministic reranking, evidence semantics,
follow-up gap analysis, rollout decisions, and shadow comparison live above the
adapter.

Legacy `search()` remains a compatibility façade during rollout. New code uses
explicit retrieval results and does not depend on mutable `last_search_trace`.
Compatibility removal is blocked until the machine-readable retirement gate has
evidence for Offline Eval, ablation, Shadow, Canary, replay, regression, rollback,
runbook, architecture documentation, and a removal plan.

Release 0 is currently fail-closed: the available independent retrieval datasets
contain 30, 18, and 12 cases, and there is no frozen 80-120 case Eval V3 dataset
with a 20%-30% holdout and all required case types. These sets must not be
concatenated or duplicated to simulate release evidence.

Remote Cross-Encoder, broad taxonomy/schema expansion, Chinese FTS/`pg_trgm`,
conflict detection, and more complex fusion remain disabled unless a matching
versioned evaluation proves the corresponding gap.
