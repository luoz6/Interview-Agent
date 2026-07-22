# Stage 44B1 Chinese Corpus Acceptance

Status: `PASS`

## Scope

Stage 44B1 keeps `app/data/knowledge/` as the frozen v1 corpus root and
`app/data/knowledge_v2/` as the isolated Chinese v2 corpus root. The v2 corpus
uses Chinese for all natural-language content and runtime retrieval queries;
technical identifiers, code, and SQL may retain their official spelling. Only
sources approved in `docs/stage-44b1-chinese-source-matrix.md` may support the
25 v2 units.

## Release Identity

| Field | Accepted value |
| --- | --- |
| Run ID | `20260722T115946Z-stage44b1-zh` |
| Implementation commit | `6240758405faafe1ec96f66337106c9e03c1af34` |
| Table prefix | `knowledge_chunks_stage44b_rc` |
| Corpus version | `stage44b1-zh-v2` |
| Active chunks | 25 |
| Provider | `siliconflow` |
| Model | `BAAI/bge-m3` |
| Model revision | `siliconflow-bge-m3-20260721` |
| Dimension | 1024 |
| V2 corpus manifest SHA-256 | `68a410d4cf24c283c9a17d766e4036ce8cf4f5fd4b91546bba304744b204033f` |
| V2 pilot dataset SHA-256 | `1130339f153204557a349ff98c767c8a7cbcd192afd6a0173c2f31863811d395` |
| Frozen v1 dataset SHA-256 | `c6007ab316add69d16338c2338e941feeb6711c0a04cc4fc303044565a7c96cc` |
| Natural-language mode | Chinese only |
| Source policy | Approved Chinese sources only |

## Ingestion Evidence

The clean RC ingestion embedded 25 vectors and reused 0. That first evaluation
exposed invalid same-domain exclusion controls in the pilot and was not
accepted. After correcting only the pilot controls, the passing idempotent run
embedded 0, reused 25, and activated all 25 chunks. Both runs demonstrated that
`embedded + reused == activated == 25` without changing corpus content.

## Retrieval Metrics

| V2 pilot gate | Result |
| --- | ---: |
| Recall@5 | 1.00 |
| MRR@5 | 1.00 |
| nDCG@5 | 0.970181 |
| Filter correctness | 1.00 |
| Excluded-chunk violation rate | 0.00 |
| Vector validity | 1.00 |
| Evidence replay stability | 1.00 |
| Observation completeness | 1.00 |
| Retrieval p95 | 557.392 ms |

| Frozen v1 gate | Result |
| --- | ---: |
| Hit@3 | 1.00 |
| MRR | 0.94 |
| False-positive rate | 0.00 |
| Invalid-reference rate | 0.00 |
| Evidence continuity | 1.00 |
| Observation completeness | 1.00 |
| Retrieval p95 | 392.282 ms |

Provider request count was 44 with 0 retries and no recorded provider errors.
Provider latency was 241.879 ms p50 and 348.164 ms p95.

## Verification Evidence

- Deterministic Stage 44B1 focused gates: 83 passed.
- Isolated pgvector integration gates: 9 passed.
- Full Python regression: 912 passed, 1 skipped.
- Browser regression: 15 passed, 9 expected skips.
- CSS production build and seven JavaScript syntax checks passed.
- Stage 42 and Stage 44A historical artifact audits passed.
- Locked clean environment passed `pip check`, excluded both local embedding
  packages, and passed all three focused runtime tests.
- Final RC inventory contains 44 whitelisted artifacts totaling 20,884 bytes at
  `reports/stage44b1-acceptance/20260722T115946Z-stage44b1-zh`.
- Privacy audit found zero blocked keys, secrets, source URLs, personal data, or
  absolute paths.

## Promotion Boundary

The acceptance runner remains on `knowledge_chunks_stage44b_rc`. It must never
switch the production table prefix or promote `stage44b1-zh-v2` automatically.
Production promotion requires a separate explicit operator approval after this
record is completed.
