# Stage 42A Knowledge Retrieval Acceptance

Status: `PASS`

Date: 2026-07-15

## Scope

Stage 42A covers the versioned Prep contract, deterministic role profiling and
query construction, corpus versioning, trusted repository reads, grounded plan
binding, safe Prep UI persistence, and offline retrieval quality gates. It does
not include Examiner or Reviewer evidence reuse, which remains Stage 42B work.

## Accepted Commits

- `7b6ad2d` - versioned knowledge evidence contracts
- `aec17eb` - deterministic knowledge queries
- `9547536` - versioned 25-chunk knowledge corpus
- `1e6f565` - versioned repository reads and hash checks
- `c01fac2` - trusted evidence plan grounding
- `32ca98e` - persisted Prep evidence UI
- `944115f` - offline knowledge retrieval quality gate

## Corpus Gate

- Corpus version: `stage42-v1`
- Chunk count: `25`
- Domain distribution: Redis, FastAPI, MySQL, Kafka, and system design each
  contain five distinct chunks.
- Content distribution: mechanism, failure mode, engineering practice,
  benchmark, and hard negative each contain five chunks.
- Corpus manifest SHA-256:
  `ad0948a6dc15af835247eb95b8fad4a069bece865779060e19c708f829eb9320`
- PostgreSQL verification: 25 rows, 25 distinct content hashes, and all 25 rows
  carry the accepted corpus manifest hash.

## Retrieval Gate

The offline dataset contains 30 independent cases: 20 relevant queries, five
weak-keyword or synonym queries, and five negative queries. It does not call an
LLM.

| Metric | Result | Gate |
| --- | ---: | ---: |
| Hit rate at 3 | 1.00 | >= 0.90 |
| Mean reciprocal rank | 0.98 | >= 0.75 |
| Question evidence binding rate | 1.00 | = 1.00 |
| Evidence continuity rate | 1.00 | = 1.00 |
| Invalid reference rate | 0.00 | = 0.00 |
| Negative false-positive rate | 0.00 | <= 0.20 |
| Warm retrieval p95 | 203.61 ms | <= 1500 ms |
| Observation completeness | 1.00 | = 1.00 |

Embedding model warmup was recorded separately as 18399.547 ms and was not
included in warm retrieval p95. The accepted minimum similarity score is 0.45;
the measured negative maximum was 0.4216 and the measured relevant minimum was
0.5023.

## Regression Gate

- Full Python suite with PostgreSQL/pgvector: `557 passed, 1 opt-in skipped`
- Tailwind CSS build: passed
- Playwright desktop and Pixel 7 projects: `8 passed, 2 real-model opt-in skipped`
- Browser coverage includes safe evidence rendering, mobile overflow, explicit
  degraded state, persisted evidence IDs, SSE refresh/conflict recovery, report,
  and PDF flow.
- Public Prep and session payloads exclude content hashes, corpus hashes,
  binding snapshots, knowledge content, and provider internals.

## Decision

Stage 42A is accepted. Stage 42B product implementation may begin from this
record. Reviewer and Examiner paths must preserve the v2 rule:
`get_by_ids=1/search=0`; only legacy v1 plans may retain semantic search.
