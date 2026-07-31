# Memory Knowledge P1 Coverage Acceptance

Status: repository coverage gate passed; production promotion is not
authorized by this record.

## Corpus identity

- Corpus version: memory-p1-zh-v3.
- Chunk count: 31.
- Manifest SHA-256:
  d68eaa532f58d711686b5dc94d606faf7b5bd4ff6a03e264f67be4c78707c1d3.
- Historical stage44b1-zh-v2 corpus: unchanged at 25 chunks when rebuilt with
  the historical version selector.

## Required coverage

Every required tag has at least two positive, two negative, and two boundary
entries:

| Tag | Positive | Negative | Boundary |
| --- | ---: | ---: | ---: |
| fastapi | 3 | 2 | 2 |
| kafka | 3 | 2 | 2 |
| mysql | 3 | 2 | 2 |
| postgresql | 3 | 3 | 3 |
| python | 3 | 2 | 2 |
| redis | 3 | 2 | 2 |
| reliability | 5 | 4 | 4 |
| system-design | 3 | 2 | 2 |

## PostgreSQL extension

Six new Chinese units cover connection capacity, monitoring, connection
saturation, replica lag, high-availability failover, and backup/restore.
References are restricted to Microsoft Learn official Chinese PostgreSQL
documentation checked on 2026-07-30. Candidate answers and personal memory are
not sources for this corpus.

## Safety boundary

- The committed active manifest derives canonical tags and evidence counts.
- P1 readiness requires a minimum count of two for every evidence class.
- PostgreSQL is not treated as an alias of MySQL.
- Historical Stage 44B1 acceptance can still rebuild its exact 25-unit corpus
  by selecting stage44b1-zh-v2.
- Loading or promoting memory-p1-zh-v3 into a production pgvector release
  remains a separately authorized operation.
