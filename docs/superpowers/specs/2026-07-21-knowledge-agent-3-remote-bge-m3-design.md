# Knowledge Agent 3.0 Remote BGE-M3 Design

Status: Approved design

Date: 2026-07-21

## 1. Purpose

Knowledge Agent 3.0 is delivered through two serial, independently accepted
stages. Stage 44A replaces the runtime dependency on a locally loaded
SentenceTransformer with a provider-neutral embedding boundary backed by
SiliconFlow's hosted `BAAI/bge-m3`, migrates the current 25-unit corpus into
versioned storage, and preserves the existing Stage 42 quality gates. Stage
44B expands the curated backend-engineering corpus to approximately 140 units
and adds the new 72-case retrieval-quality gates. Neither stage changes the
public interview APIs or the existing Prep-to-Reviewer evidence contract.

The workstation and application processes must not download or initialize a
local embedding model. Real provider calls remain explicit opt-in acceptance
work; deterministic development and CI use a fake provider.

## 2. Current Baseline

The current `PgVectorKnowledgeStore` owns three responsibilities at once:

1. loading `SentenceTransformer`;
2. generating query and document vectors;
3. persisting and searching pgvector rows.

Stage 42 already proves Prep retrieval, evidence binding, Reviewer
`get_by_ids()` reuse, report citations, PDF continuity, degradation, and public
privacy. This design preserves those contracts. It changes how vectors are
obtained, how corpus releases are activated, and how retrieval quality is
measured.

## 3. Goals

1. Use SiliconFlow `BAAI/bge-m3` through a provider-neutral interface.
2. Remove runtime dependence on local model downloads.
3. Build a manually reviewed backend-engineering corpus of 120-180 units, with
   an initial target of approximately 140.
4. Atomically activate complete corpus versions and preserve historical
   evidence continuity.
5. Add deterministic metadata-aware reranking on top of dense retrieval.
6. Establish a versioned 72-case golden retrieval dataset and measurable
   release thresholds.
7. Prevent API keys, raw resumes, raw JDs, provider request bodies, and corpus
   content from entering logs or acceptance artifacts.

### 3.1 Stage Boundaries

Stage 44A owns embedding and storage infrastructure:

- provider protocol, fake provider, and SiliconFlow adapter;
- configuration, timeout, retry, validation, and redaction behavior;
- versioned PostgreSQL tables and migration of the current 25 rows;
- atomic corpus activation, vector reuse, and historical evidence lookup;
- deterministic metadata reranking that does not expand the repository search
  signature;
- unchanged execution of the 30-case Stage 42 v1 dataset and its existing
  release thresholds.

Stage 44B owns content and stronger quality gates:

- update the existing 25 units and add approximately 115 reviewed units;
- extend the query taxonomy for PostgreSQL and reliability topics;
- add the v2 front-matter requirements and corpus validators;
- create a separate 72-case v2 dataset and metric model;
- run the complete SiliconFlow quality and privacy acceptance.

Stage 44B starts only after Stage 44A has a recorded PASS. The two stages use
separate implementation plans and commits so corpus authoring cannot block or
obscure the infrastructure cutover.

## 4. Non-Goals

- No authentication or multi-user behavior.
- No voice input or output.
- No frontend redesign or public API contract change.
- No WebSocket or transport replacement.
- No external vector database.
- No automatic crawling or bulk copying of third-party articles.
- No local fallback that silently downloads `BAAI/bge-m3`.
- No full BM25, PostgreSQL full-text, pg_trgm, sparse-vector, or RRF pipeline in
  this stage. Those require evidence from failed dense-retrieval cases first.
- No provider-generated knowledge content. Corpus prose remains reviewed and
  version-controlled in the repository.

## 5. Embedding Boundary

The application defines an `EmbeddingProvider` protocol independent of
pgvector and SiliconFlow:

```python
class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    model_revision: str
    dimension: int

    def embed_query(self, text: str) -> list[float]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
```

Two implementations are required:

- `SiliconFlowEmbeddingProvider` calls the configured `/embeddings` endpoint
  with model `BAAI/bge-m3`.
- `FakeEmbeddingProvider` produces deterministic finite vectors for unit,
  integration, and browser tests without network access.

`PgVectorKnowledgeStore` receives an `EmbeddingProvider` through its
constructor. It never imports `sentence_transformers`, selects a vendor, or
reads an API key. The store remains responsible for PostgreSQL schema access,
vector search, evidence lookup, and deterministic reranking.

The provider validates every response before returning it:

- response count equals input count;
- every vector contains exactly 1024 values;
- every value is a finite float;
- response item order is reconstructed from provider indices;
- missing, duplicate, or out-of-range indices fail the complete request.

## 6. Configuration And Secret Handling

The default configuration disables embedding instead of selecting a local or
remote model implicitly:

```text
EMBEDDING_PROVIDER=disabled
```

SiliconFlow is enabled explicitly with:

```text
EMBEDDING_PROVIDER=siliconflow
EMBEDDING_API_BASE=https://api.siliconflow.cn/v1
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_MODEL_REVISION=siliconflow-current
EMBEDDING_DIMENSION=1024
EMBEDDING_BATCH_SIZE=32
EMBEDDING_CONNECT_TIMEOUT_SECONDS=5
EMBEDDING_READ_TIMEOUT_SECONDS=30
SILICONFLOW_API_KEY=<secret supplied outside Git>
```

`EMBEDDING_MODEL_REVISION` is an application-controlled release label because
the provider response may not expose an immutable model revision. Formal
acceptance must replace `siliconflow-current` with a dated or release-specific
label so saved metrics identify the deployed embedding behavior.

The API key may exist only in the process environment or an ignored local
secret file. It must never appear in committed `.env` files, command examples
with real values, exception text, logs, trace payloads, database rows, test
fixtures, screenshots, or acceptance artifacts. Any credential pasted into a
chat, issue, log, or terminal transcript must be revoked before use.

`disabled` never initializes a model or performs a network request. Missing
SiliconFlow configuration does not prevent importing the application or
starting non-knowledge commands. The first requested SiliconFlow operation
raises a stable configuration error, which the Prep knowledge path converts to
the existing explicit degraded result. There is no SentenceTransformer or
other local-model fallback for any provider value.

## 7. Curated Corpus

Stage 44A migrates the existing 25-unit Stage 42 corpus without requiring new
content fields. Stage 44B upgrades those units and expands the active corpus to
approximately 140 independently reviewable knowledge units:

| Domain | Target units |
| --- | ---: |
| Python and FastAPI engineering | 20 |
| Redis and caching | 20 |
| MySQL and PostgreSQL | 25 |
| Kafka and asynchronous messaging | 20 |
| Distributed systems and system design | 30 |
| Reliability, observability, and capacity planning | 25 |

Each unit covers one concept that can support both interview questioning and
answer evaluation. Front matter contains:

```yaml
chunk_id: redis-cache-aside-consistency
title: Cache-Aside consistency boundaries
domain: redis
source_type: theory
tags: [redis, cache, consistency]
aliases: [cache aside, cache-aside, 缓存一致性]
difficulty: intermediate
question_patterns:
  - How do you keep cache and database state consistent?
  - Should the application delete the cache before or after updating data?
references:
  - title: Redis documentation
    url: https://redis.io/docs/
```

The body includes the core conclusion, applicability boundary, common errors,
engineering trade-offs, and observable scoring signals. References name the
source and URL, but the repository contains an original technical summary
rather than copied article bodies.

The existing Stage 42 manifest schema remains readable for the 44A legacy
migration. Stage 44B introduces manifest schema v2 and requires the complete
front matter above for all active v2 units. Its validation rejects duplicate
IDs, missing required metadata, invalid domains/source types/difficulties,
empty bodies, duplicate references, unparseable URLs, and units outside
configured size bounds. Every corpus file uses UTF-8 and contributes a
normalized `content_sha256` to the manifest.

## 8. Versioned Storage Model

Stage 44A creates `{PGVECTOR_TABLE}_versions` and
`{PGVECTOR_TABLE}_releases`, using safe SQL identifiers derived from the
configured base table name so DSN-gated tests retain isolated prefixes. The
version table stores immutable corpus rows rather than overwriting the only
copy of a chunk. Each row includes:

```text
corpus_version
chunk_id
content_sha256
embedding_provider
embedding_model
embedding_revision
embedding_dimension
title/content/source_type/domain/tags/metadata
embedding VECTOR(1024)
```

The row identity is `(corpus_version, chunk_id)`. The release table stores the
manifest hash, provider/model identity, dimension, chunk count, lifecycle
status, creation time, and activation time. A partial unique index over active
rows guarantees exactly one active corpus version.

The existing configured knowledge table is not altered in place and its rows
must never be relabeled as SiliconFlow output. Stage 44A handles it as follows:

1. create both new tables and their indexes idempotently;
2. if legacy rows exist, copy them into corpus `legacy-stage42-v1`, preserving
   the current vector, content hash, and truthful provider/model labels;
3. verify legacy source/destination counts, chunk IDs, hashes, and dimensions;
4. parse the current 25-unit manifest and generate a new complete corpus with
   SiliconFlow under a distinct Stage 44A version;
5. atomically activate only the new SiliconFlow corpus after all 25 vectors and
   release metadata validate;
6. switch runtime reads to the new version table after activation;
7. retain the old table and any copied legacy release unchanged through Stage
   44A and Stage 44B acceptance.

An empty legacy table is valid and skips only the optional copy; it does not
skip remote ingestion of the current corpus. The migration is idempotent.
Re-running it with the same source rows succeeds; the same legacy version name
with different IDs or hashes is a blocking error. Vector reuse requires the
same content hash, provider, model, revision, and dimension, so a local legacy
vector is never reused as a SiliconFlow vector.

Runtime dense search reads only the active corpus version. Evidence lookup is
different: `get_by_ids(ids, expected_hashes=...)` may read retained historical
versions and selects the row matching both `chunk_id` and the expected content
hash. This preserves existing interview and report evidence after a newer
corpus changes or removes the same logical chunk.

Neither stage physically deletes historical corpus releases or the legacy
table. At the target scale, retaining versioned vectors is small and provides
reliable rollback and evidence replay. A future retention policy may delete a
version only after proving that no persisted session binding references its
hashes.

## 9. Atomic Ingestion

The current loader already places its row writes inside one psycopg2 connection
transaction. What it lacks is release identity and atomic active-version
cutover. The Stage 44A ingestion command therefore follows this sequence:

1. Parse every corpus file and validate the complete logical corpus.
2. Build the normalized manifest and calculate its SHA-256.
3. Reject a corpus version name already associated with a different manifest.
4. Load reusable vectors whose content hash and provider/model/revision match.
5. Call SiliconFlow only for new or changed units, in batches of 32 by default.
6. Validate all returned vectors before opening the write transaction.
7. In one PostgreSQL transaction, insert the complete version, insert its
   metadata, mark the previous version retired, and mark the new version
   active.
8. Emit a sanitized summary containing counts, hashes, provider/model labels,
   duration, and activation status.

No version becomes searchable until all vectors exist and the activation
transaction commits. Provider failure, process interruption, validation
failure, or database rollback leaves the previous active version unchanged.
Re-running the same version and manifest is idempotent and makes no provider
calls for already persisted matching rows.

## 10. Runtime Retrieval And Reranking

The existing role-profile and query-building path remains authoritative. It
must send only normalized technical queries to the embedding provider, never a
raw JD, resume, candidate name, project paragraph, or complete conversation.
Representative provider inputs are:

```text
redis cache consistency failure recovery
fastapi async blocking io production
postgresql index online migration
```

For each query, the store:

1. embeds the normalized query through the configured provider;
2. applies existing source-type and normalized job-tag filters;
3. retrieves the top 12 dense candidates from the active corpus;
4. computes deterministic metadata boosts;
5. sorts by final score descending and `chunk_id` ascending;
6. returns at most five chunks above the configured minimum score.

The repository search protocol remains unchanged. Because it supplies job tags
but no independent requested-domain parameter, domain and tag matches form one
combined signal rather than two duplicate boosts. The initial score is:

```text
final_score = clamp(
    dense_similarity
    + 0.06 when a normalized query term matches title or aliases
    + 0.04 when chunk.domain or chunk.tags matches a canonical job tag,
    0,
    1,
)
```

Each boost is applied at most once. Domain/tag comparison uses normalized exact
values. Title and alias matching uses the corpus metadata and normalized
technical terms; it does not require general Chinese tokenization. Retrieval
traces expose filters, safe IDs, scores, latency, provider/model labels, and
corpus version, but never query text or corpus content.

The weights are fixed for the initial release. They change only through a new
rubric/version and a before/after evaluation demonstrating improvement without
new excluded-chunk violations.

## 11. Failure And Retry Semantics

The SiliconFlow adapter retries only transient failures:

- HTTP 429, 500, 502, 503, and 504;
- network connection resets and read timeouts.

It performs at most three attempts with exponential backoff and bounded jitter.
HTTP 400, 401, 403, unsupported model responses, malformed JSON, response-count
mismatch, and vector validation failures are permanent and are not retried.

Failure behavior remains bounded by workflow ownership:

- ingestion failure never activates a partial corpus;
- Prep embedding or search failure produces the existing
  `knowledge_status=degraded` path and does not block plan generation;
- Reviewer evidence reuse calls `get_by_ids()` and does not invoke SiliconFlow;
- existing bound evidence either resolves to the expected historical content
  hash or produces the existing explicit missing/version-mismatch degradation;
- provider recovery requires no session repair; the next Prep starts normally.

## 12. Evaluation Dataset And Metrics

Stage 44A preserves `tests/golden/knowledge_retrieval_v1.json`, its 30 cases,
and all current Stage 42 metrics and thresholds, including Hit@3 >= 0.90, MRR
>= 0.75, evidence continuity, false-positive, completeness, and latency gates.
The infrastructure cutover may not weaken or overwrite that baseline.

Stage 44B adds `tests/golden/knowledge_retrieval_v2.json`; it does not replace
v1. The v2 dataset contains 72 cases with 12 manually reviewed queries in each
evaluation group:

- `fastapi`;
- `redis`;
- `relational-database` (MySQL and PostgreSQL chunks);
- `kafka`;
- `system-design`;
- `reliability`.

`evaluation_group` is an evaluation dimension, not a replacement for chunk
domain. Stage 44B extends the runtime canonical taxonomy with explicit
`postgresql` and `reliability` tags while retaining existing `mysql` and
`system-design` compatibility. Each v2 case defines its query, canonical tags,
source filters, primary expected chunks, accepted related chunks, and
explicitly excluded chunks.

The release thresholds are:

| Metric | Threshold |
| --- | ---: |
| Recall@5 | >= 0.90 |
| MRR@5 | >= 0.80 |
| nDCG@5 | >= 0.85 |
| Domain/filter correctness | 1.00 |
| Excluded-chunk violation rate | 0 |
| Vector dimension and finite-value validity | 1.00 |
| Evidence-ID replay stability | 1.00 |

The v1 and v2 metric models have distinct versioned schemas; existing Stage 42
artifacts remain replayable. Metric calculation is deterministic and
independent of an LLM. A failure in attempt completeness is a blocking failure
rather than a reduced denominator. Dataset, corpus manifest, scoring-rubric,
and provider/model revision hashes are recorded together.

## 13. Test Strategy

### 13.1 Deterministic Unit Tests

Stage 44A tests use `FakeEmbeddingProvider` and cover configuration, batch
ordering, transient/permanent retry classification, vector validation, secret
and input redaction, migration idempotency, atomic activation, historical
lookup, metadata boosts, stable ordering, and unchanged v1 metrics. Stage 44B
extends them with manifest-v2 validation, taxonomy expansion, and v2 metric
calculation. They perform no network access.

### 13.2 PostgreSQL Integration

DSN-gated tests use isolated table prefixes in the existing PostgreSQL
`interview` database. They create no new database or container. Tests cover the
version schema, active-version uniqueness, failed activation rollback,
repeated ingestion, vector reuse, dense search, reranking, old-version
evidence lookup, and Stage 42 continuity.

### 13.3 SiliconFlow Acceptance

Real acceptance runs only when `RUN_SILICONFLOW_ACCEPTANCE=1` and a rotated
`SILICONFLOW_API_KEY` is present in the environment. Stage 44A performs a
provider contract smoke plus the existing v1 cases against a newly generated
and activated SiliconFlow-backed 25-unit corpus. Stage 44B embeds the approved
expanded corpus and executes all 72 v2 queries while rerunning v1. Artifacts
record only corpus and dataset identities, safe aggregate metrics, p50/p95
latency, request/error counts, provider/model labels, and relative artifact
hashes.

The acceptance directory must pass a privacy audit that rejects API keys,
authorization headers, raw requests, raw JDs/resumes, absolute paths, and
unallowlisted files.

## 14. Release Gates

### 14.1 Stage 44A Gates

Stage 44A is accepted only when:

1. deterministic provider, retry, validation, migration, activation,
   historical lookup, reranking, and redaction tests pass;
2. PostgreSQL migration proves count/hash/vector parity for every legacy row
   that exists, while also handling an empty legacy table;
3. a distinct SiliconFlow-backed 25-unit corpus is fully ingested and becomes
   the only active release;
4. the unchanged 30-case v1 dataset meets every existing Stage 42 threshold;
5. the opt-in SiliconFlow contract and v1 smoke pass;
6. no runtime path imports or initializes SentenceTransformer;
7. a clean environment proves no local embedding model is downloaded;
8. Reviewer bound-evidence reuse performs zero embedding API calls;
9. existing Python, browser, JavaScript, CSS, Stage 40, Stage 42, and Stage 43
   gates remain green;
10. the Stage 44A artifact privacy audit reports zero violations.

### 14.2 Stage 44B Gates

Stage 44B is accepted only when:

1. Stage 44A has a recorded PASS;
2. the active v2 manifest contains 120-180 valid units with the approved domain
   distribution and all v2 metadata;
3. all 72 v2 observations are complete and meet every new metric threshold;
4. all 30 v1 cases still meet the original Stage 42 thresholds;
5. PostgreSQL activation, rollback, and historical Stage 42 evidence replay
   pass against the expanded corpus;
6. the complete opt-in SiliconFlow run and formal privacy audit pass;
7. all Stage 44A and repository-wide regression gates remain green.

## 15. Delivery Order

### 15.1 Stage 44A

1. Lock protocol, disabled-default configuration, fake-provider, and redaction
   contracts.
2. Implement and test the SiliconFlow adapter.
3. Create versioned PostgreSQL tables and optionally preserve existing legacy
   rows without changing their provider identity.
4. Split ingestion from runtime search, remotely embed the current 25 units,
   and implement atomic activation plus idempotent vector reuse.
5. Add the two-signal deterministic reranker and safe retrieval traces.
6. Run the unchanged v1 dataset, PostgreSQL, full regression, opt-in
   SiliconFlow smoke, and Stage 44A artifact audit.

### 15.2 Stage 44B

1. Lock manifest-v2, taxonomy, evaluation-group, and metric contracts.
2. Upgrade the existing 25 units to v2 metadata.
3. Add and review approximately 115 units in bounded domain batches.
4. Build the separate 72-case v2 dataset and versioned metric calculator.
5. Activate the expanded corpus and verify v1 plus historical evidence replay.
6. Run PostgreSQL, full regression, complete SiliconFlow quality acceptance,
   and Stage 44B artifact audit.

Hybrid lexical retrieval is considered only after this release if recorded
failure cases show exact-term misses and the approved Recall@5 threshold cannot
be met through corpus quality, metadata, and dense retrieval.
