# Knowledge RAG V2 Implementation Status

Status date: 2026-08-13

Current active corpus identity: `memory-p1-zh-v4`. The messaging pilot and its
five active V2 knowledge documents use RocketMQ delivery, retry/dead-letter,
consumer load-balancing, operations, and backend engineering semantics. The
frozen V1 Kafka corpus remains unchanged for historical replay compatibility.

This status separates repository implementation from evidence that can only be
produced by independent annotators, an authorized database, or production
operations. A green local suite does not promote Hybrid by itself.

| Plan area | Repository implementation | Local test evidence | Required external evidence | State |
| --- | --- | --- | --- | --- |
| Release 0: Eval V3 contracts | Dataset governance, 14 case types, family-isolated tuning/holdout, annotation hashes and agreement gate | Eval V3 contract, artifact, metric and CLI tests pass | Independent 80-120 case dataset with 20%-30% holdout | Repository implemented; externally blocked |
| Release 0: reproducible baseline | Immutable engine/provider/profile/code/corpus identity and privacy-safe per-case artifacts | Artifact identity, replay and privacy tests pass | Real frozen Legacy tuning and holdout artifacts | Repository implemented; externally blocked |
| Release 0: paired promotion gate | Frozen threshold registration, timestamp ordering, exact artifact identity and automatic threshold decision | Preregistration/order/drift tests pass | Approved thresholds and real Hybrid holdout run | Repository implemented; externally blocked |
| Release 1: compatibility engine | Explicit request/result/trace contracts, semantic/evidence lookup ports and one compatibility facade | Architecture and Legacy compatibility tests pass | Real Legacy per-case tolerance artifact | Repository implemented; external comparison blocked |
| Release 2: exact-term Hybrid | Exact-term/alias retrieval, weighted and unweighted RRF, rank-normalized fusion, deterministic rerank and channel degradation | Hybrid, fusion, lexical, runtime and ablation-contract tests pass | Independent tuning/holdout metrics and latency observations | Repository implemented; promotion blocked |
| Release 3: evidence semantics | Three reviewed pilot Knowledge Units (Redis locking, MySQL indexing, RocketMQ delivery); Retrieval and Evaluation Support Gates; authority, task-fit, hard-negative and signal checks; validated orthogonal EvidenceDecision | Gate, Knowledge Unit and impossible-state contract tests pass | Independent calibration annotations and preregistered sufficiency thresholds | Pilot implementation complete; calibration blocked |
| Release 4: Prep question binding | One role candidate pool plus question-specific retrieval only for unbound generated questions; no first-candidate fallback | Grounding and agent tests prove one Base bundle and selective supplemental retrieval | Independent binding-precision annotations | Repository implemented; quality gate blocked |
| Release 4: Reviewer binding | Persisted Base -> Question -> Review lineage, replay-first review, weak-only targeted supplementation and hash/manifest lineage | Reviewer, binding, serialization and row-mapper round-trip tests pass | Independent score-stability and Reviewer blind evaluation | Repository implemented; quality gate blocked |
| Release 4: Report boundary | Every final path re-locks feedback, score and dimensions from persisted QuestionEvaluationRecord; full-session remains a compatibility producer only | Microbatch, full-session/fallback and architecture-boundary tests pass | Independent report-quality evidence under real workload | Repository implemented; production gate blocked |
| Release 5: evidence-gap follow-up | Pilot signal-gap analysis and evidence-grounded adaptive follow-up path | Follow-up gap, agent and graph tests pass | 50-100 independently annotated blind A/B cases | Pilot implementation complete; blind evaluation blocked |
| Release 6: Shadow | Stable assignment and compare-only sanitized Shadow; Legacy remains formal | Runtime, assignment, trace and shadow tests pass | Privacy approval and production Shadow observation | Repository implemented; execution unauthorized |
| Release 6: Canary/Rollback | Cumulative 0 -> 1 -> 5 -> 20 -> 50 -> 100 gate, rollback contract and retirement gate | Canary progression, rollback and retirement tests pass | Staged production evidence and real rollback drill | Repository implemented; rollout blocked |
| Release 7: conditional enhancements | Cross-Encoder, broad taxonomy, FTS and conflict detection remain fail-closed behind data evidence | Retirement/enhancement eligibility tests pass | Gap-specific independent evidence | Intentionally deferred |
| Cross-cutting: Trace/config/degradation | Privacy-safe RetrievalTrace V2, full resolved profile, stable reason-code registry, profile budgets and fail-closed defaults | Config, trace, degradation, privacy and static boundary tests pass | Production telemetry | Repository implemented; operations evidence absent |

## Verified repository evidence

- architecture boundaries prohibit fusion/evidence policy in the pgvector
  adapter and prohibit new `last_search_trace` dependencies;
- Shadow keeps Legacy as the formal result and persists sanitized comparisons;
- PREP assignment is hash-only and Reviewer targeted retrieval reuses it;
- no-evidence, degraded, and unavailable outcomes remain distinct;
- evaluation artifacts exclude query, knowledge body, JD, resume, answers, and
  provider error details;
- frozen artifacts use exclusive creation and self-verifying SHA-256 identities;
- holdout comparison requires thresholds registered after Legacy baseline and
  before the candidate run; the run itself also rejects missing or drifting
  registration identity;
- routing hints remain soft by default, while explicit hard filters and routing
  policy experiments are separately versioned;
- absolute and relative P95 budgets and per-case-type paired deltas are enforced.
- evidence calibration observations bind every final evidence ID to its content
  SHA-256 and corpus manifest; registration/comparison require 100% observation
  completeness and replay stability;
- the committed ingestion manifest carries `knowledge-metadata-v2.1`, stable
  topic values, aliases, and explicit pilot technical terms; the active
  `memory-p1-zh-v4` manifest contains 31 chunks and has SHA-256
  `deb709817c6ea1ac89db8f0452f1183d0168952d5d568e08b704869c90555e84`;
- the offline RocketMQ V4 preflight reproducibly validates the active manifest,
  all runtime chunks, both active retrieval datasets, the reviewed pilot Unit,
  safe runtime defaults, and the frozen V1 Kafka identity without connecting to
  a provider or database; it reports repository readiness separately from the
  external release blockers;
- the read-only RocketMQ V4 target preflight inspects pgvector, physical target
  identity, versioned release state, approval validity, fixed embedding
  identity, and explicit operator authorization. On 2026-08-13 the authorized
  local `interview` target passed this gate with pgvector 0.8.2 and the approved
  physical target fingerprint. The versioned loader embedded all 31 documents
  with `siliconflow` / `BAAI/bge-m3` at revision
  `siliconflow-bge-m3-rmqv4-2026-08-13`, activated `memory-p1-zh-v4`, and a
  post-load read-only preflight verified the exact manifest identity;
- the active release contains exactly 31 version rows at dimension 1024, the
  five approved RocketMQ chunk IDs, and no Kafka chunk ID. The 25-row legacy
  corpus was preserved as retired `legacy-stage42-v1`; it was not deleted or
  promoted. A hash- and embedding-identity reuse check found all 31 active
  vectors reusable without another provider call;
- the real Legacy/pgvector V2 evaluator now passes the dataset's declared
  domain hard constraint to the semantic adapter. The first real run exposed
  that this constraint had not crossed the compatibility `search()` boundary:
  all failures were cross-domain candidates sharing the requested source type
  and `reliability` tag. The adapter, Port, runtime compatibility wrapper, CLI,
  and PostgreSQL integration contract now preserve the domain allowlist;
- Canary advancement requires the exact cumulative `0 -> 1 -> 5 -> 20 -> 50
  -> 100` sequence and rollback drill evidence is machine validated;
- durable initial Report generation and quality repair both re-lock scores,
  dimensions, feedback, and references to authoritative question records.
- new Prep V2 snapshots persist privacy-safe BaseEvidenceBundle and
  QuestionEvidenceBinding objects; Reviewer uses the persisted parent ID and
  the existing question-evaluation JSON envelope round-trips the complete
  ReviewEvidenceBinding, including failed reviews resolved after retrieval;
- evidence reference hashes and corpus bindings are validated as lowercase
  SHA-256, duplicate IDs and invalid final unions fail closed, and all binding
  creation timestamps are timezone-aware.
- `EvidenceDecision` rejects unavailable, empty, not-evaluated, weak, or
  insufficient combinations that claim a false evaluation confidence;
- Retrieval Gate validates hash/corpus and explicit hard-negative risk but does
  not reject authoritative boundary documents merely because their
  `content_kind` is `hard_negative`;
- Evaluation Support Gate executes in the Reviewer, requires a reviewed Unit,
  checks task relevance and controlled source authority, and supplements only
  weak or insufficient replayed evidence;
- ingestion attaches schema-derived authority and provenance metadata without
  persisting source URLs in runtime chunks;
- flattened QuestionEvaluationRecord evidence fields must agree with the full
  ReviewEvidenceBinding whenever both are present, while sparse historical
  records remain readable;
- Report progress distinguishes bound-only reuse from bound evidence with
  targeted supplementation without publishing raw query text.

## Hard release stops

Hybrid must remain unpromoted and Legacy must remain available until all of the
following authoritative records exist:

1. independent 80-120 case Eval V3 dataset with 20%-30% family-isolated holdout;
2. frozen real Legacy tuning and holdout artifacts;
3. approved, pre-registered retrieval and latency thresholds;
4. frozen Hybrid tuning, ablation, and holdout artifacts that pass those gates;
5. blind follow-up and Reviewer quality review;
6. explicitly authorized protected PostgreSQL test evidence (satisfied for the
   approved local target on 2026-08-13: 223 passed, 1 expected skip, zero
   isolated-table residue);
7. privacy audit, production Shadow observation, staged Canary observations,
   and a real rollback drill.

Historical 30-, 18-, and 12-case datasets are not interchangeable with item 1
and must not be concatenated or duplicated to manufacture it.

## Eval V3 annotation authoring package

The repository now includes a deterministic blank authoring scaffold at
`eval/knowledge-v3/authoring/`, bound to baseline revision
`a73b15bdf38c9f8f012f1dce4854de878e20f7dd` and the active RocketMQ V4
manifest. It allocates exactly 100 new slots: 75 tuning and 25 holdout, with
100 unique pre-frozen families and all 14 V3 case types represented by at least
five slots. It also provides two independent annotation templates, an
adjudication template, a privacy-safe chunk catalog, a family-isolation map,
file hashes, and a deterministic validator.

Every query and label field is blank, and no historical case ID is reused.
Accordingly, this package is not item 1 above: qualified humans must author the
new queries, label them independently while blinded to engine output, measure
the pre-registered agreement metric, adjudicate, and freeze a separate runnable
dataset. The holdout files must be controlled by an owner who does not tune
Hybrid.

A separate machine-preannotation candidate now exists under
`eval/knowledge-v3/machine-preannotation/`. It fills all 100 query and label
slots and passes the non-release V3 schema, disjoint-label, 75/25 split,
case-type coverage, filter-confuser, and semantic-family-isolation checks. It
truthfully records zero human annotators, no agreement measurement, no human
adjudication, and ineligibility as independent Eval V3 evidence. The formal V3
release validator rejects it because governance is absent.

The candidate received real Legacy diagnostics against the fixed RocketMQ V4
BGE-M3 identity: 75 tuning and 25 machine-holdout embedding calls completed
with zero retry or provider error. Both splits reached Recall@5 `1.0` and
replay stability `1.0`. Tuning MRR/NDCG/Hit@1 were `0.872388`, `0.879023`, and
`0.791045`; machine holdout values were `0.943478`, `0.920413`, and `0.913043`.
Legacy no-evidence F1 remained `0.0` on both splits, and soft routing produced
filter correctness of `0.749254` and `0.669565`. These are retained capability
gaps, not release evidence or authorization to run Hybrid.
## Business quality evaluation

Follow-up and Reviewer share the privacy-aware blind A/B contract in
`app/services/knowledge_business_eval.py` and the CLI in
`scripts/evaluate_knowledge_business_quality.py`. It creates deterministic
randomized blind packages, imports independently produced human annotations,
validates adjudication/agreement, and emits a hash-only frozen result. Holdout
comparison requires thresholds registered before annotation begins.

Report evaluation is intentionally not duplicated. The existing Stage 40
Report evaluator remains authoritative for report stability, grounding,
ranking, fallback, and forbidden-claim checks.

No independent business dataset, annotation, agreement, or passing holdout
artifact is committed. This implementation supplies the gate; it does not
constitute release evidence.

Evidence calibration has a separate, non-retrieving evaluator in
`app/services/knowledge_evidence_eval.py`. It computes the Plan's binding,
signal coverage, supplementation, sufficiency, failure/no-evidence, and replay
metrics from frozen observation batches. It reuses the existing canonical hash
and frozen artifact writer rather than duplicating the Retrieval runner. No
independent calibration dataset or holdout artifact is committed.

## Local verification snapshot

The RocketMQ migration review on 2026-08-13 produced:

- 2,503 tests collected in total;
- 224 protected PostgreSQL tests executed under explicit target and ownership
  approval: 223 passed and 1 expected permission-boundary skip;
- every protected-test batch finished with zero generated `test_*` table
  residue, and the versioned knowledge release tables remained untouched until
  the separately authorized corpus load;
- 2,276 tests passed and 3 skipped in the authorized non-PostgreSQL scope;
- 151 focused RocketMQ/corpus/binding/graph tests passed;
- 194 knowledge-domain architecture, corpus, Eval V3 and unit tests passed;
- 4 RocketMQ V4 offline preflight contract tests passed;
- 5 RocketMQ V4 read-only target/approval preflight contract tests passed;
- the authorized load discovered 31 chunks, reused 0 pre-existing vectors,
  generated 31 SiliconFlow embeddings, and activated all 31. Post-load checks
  verified the active version, manifest SHA-256, embedding identity, exact
  RocketMQ boundary, zero V4 Kafka IDs, and 31-of-31 reusable vectors;
- a real fixed-revision Legacy/pgvector baseline completed all 30 historical
  V2 cases with no degradation or provider error. The 12-case Pilot passed with
  Recall@5 `1.0`, MRR@5 `0.958333`, NDCG@5 `0.943179`, filter correctness
  `1.0`, replay stability `1.0`, excluded violation rate `0.0`, and P95
  `551.763 ms`. The 18-case Memory P1 set passed with Recall@5 `1.0`, MRR@5
  `0.972222`, NDCG@5 `0.958113`, filter correctness `1.0`, replay stability
  `1.0`, excluded violation rate `0.0`, and P95 `804.899 ms`;
- the baseline used 30 initial query-embedding requests plus four minimal
  corrective requests after the domain-propagation defect was fixed. All 34
  requests completed without retry or provider error. Privacy-safe local
  artifacts contain case IDs, result IDs, scores, latency, metrics, and frozen
  model/corpus identity, but no query text, knowledge body, credentials, DSN,
  resume, JD, or source URL;
- the historical Stage 44B1 runner remains fail-closed because its frozen V1
  Kafka retrieval gate cannot be represented as passing against RocketMQ chunk
  identities; its RocketMQ pilot, ingestion, idempotency and privacy checks
  pass, while release-artifact promotion is correctly rejected;
- `compileall` passed for `app`, `tests`, and `scripts`;
- `git diff --check` passed (Git emitted only Windows line-ending notices);
- no TODO/FIXME/NotImplemented placeholder, `candidates[:1]` fallback, or new
  knowledge-layer dependency on mutable `last_search_trace` was found.

This snapshot proves repository consistency plus the explicitly authorized
local PostgreSQL load, protected-test scope, and the real 12+18 historical-case
Legacy baseline. It is not an independent 80-120 case Eval V3 dataset or
holdout, privacy approval, production Shadow, Canary, rollback drill, or
production-readiness evidence. The two historical datasets must not be
concatenated, relabeled, or treated as independent holdout evidence.
