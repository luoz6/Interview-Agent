# Knowledge Eval V3 machine preannotation candidate

This directory contains a complete 100-case machine-preannotation candidate
for review and offline testing. It fills query text, routing metadata, primary
relevant chunks, accepted related chunks, excluded confusers, and no-evidence
decisions against the active RocketMQ V4 corpus.

It is not an independently annotated Eval V3 release dataset. A single model
created all labels, so the files deliberately contain no human annotator
identity hashes, independent annotation record hashes, agreement score, or
human consensus record. `governance` is `null`, and provenance explicitly marks
the candidate ineligible as independent evaluation evidence.

The candidate is useful for:

- checking the 100-case schema and 75/25 family-isolated split;
- reviewing query and relevance-label coverage before paying for retrieval;
- detecting missing or contradictory chunk labels;
- running a provisional Legacy retrieval diagnostic;
- handing each case to two human reviewers for independent correction.

It must not be used to approve Hybrid, open holdout, start Shadow or Canary,
claim inter-rater agreement, or retire Legacy.

Validate it with:

```powershell
F:\python3.11\python.exe scripts\build_knowledge_eval_v3_machine_preannotations.py validate
```

After review, human annotations must be written to a separate controlled
dataset and validated with the formal V3 release validator. Do not add fake
human hashes to this candidate.

## Legacy diagnostic snapshot

All 100 cases were run against the active RocketMQ V4 corpus with fixed
SiliconFlow `BAAI/bge-m3` revision
`siliconflow-bge-m3-rmqv4-2026-08-13`. The diagnostic used Legacy only; Hybrid,
Shadow, and Canary remained disabled.

| Metric | Tuning (75) | Machine holdout (25) |
| --- | ---: | ---: |
| Recall@5 | 1.000000 | 1.000000 |
| MRR@5 | 0.872388 | 0.943478 |
| NDCG@5 | 0.879023 | 0.920413 |
| Hit@1 | 0.791045 | 0.913043 |
| Filter correctness | 0.749254 | 0.669565 |
| Excluded violation rate | 0.358209 | 0.347826 |
| Hard-negative FPR | 0.166667 | 0.500000 |
| No-evidence F1 | 0.000000 | 0.000000 |
| Replay stability | 1.000000 | 1.000000 |
| Observation completeness | 1.000000 | 1.000000 |
| P95 latency | 410.057 ms | 403.171 ms |
| Embedding requests/retries/errors | 75 / 0 / 0 | 25 / 0 / 0 |

The result identifies real Legacy capability gaps. Soft routing allows
cross-domain candidates, and Legacy has no reliable no-evidence rejection
path. These failures are retained as diagnostic findings rather than relabeled
away. The two runtime artifacts exclude query text, knowledge body,
credentials, DSNs, resumes, job descriptions, authorization headers, and URLs.

Dataset canonical SHA-256:
`8de7f88b14940958ffb0ab9aff4d069fd089f603f5b4e4d57a120d1ddc6b072f`.

Provenance SHA-256:
`0268952fb9b3400d5ae4f8659b45d0c46c4623a9bf09aea568613b23d9ea7a44`.

## Four-way tuning ablation snapshot

The 75-case tuning split was also executed through four real retrieval paths
after latest-master integration. Each artifact is bound to the dataset SHA
above and the same `memory-p1-zh-v4` / SiliconFlow BGE-M3 identity. These are
real runtime Candidate Artifacts, but they remain machine-preannotation
diagnostics and are not independent release evidence.

| Engine | Recall@5 | MRR@5 | NDCG@5 | Hit@1 | Filter correctness | No-evidence F1 | P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Legacy | 1.000000 | 0.872388 | 0.879023 | 0.791045 | 0.749254 | 0.000000 | 410.057 ms |
| Semantic-only | 0.970149 | 0.864925 | 0.870257 | 0.791045 | 0.770149 | 0.000000 | 457.933 ms |
| Lexical-only | 0.783582 | 0.603483 | 0.612997 | 0.492537 | 0.900415 | 0.285714 | 41.313 ms |
| Hybrid weighted RRF | 0.955224 | 0.849502 | 0.846150 | 0.761194 | 0.802985 | 0.000000 | 498.850 ms |
| Rank-normalized fusion | 0.888060 | 0.684826 | 0.699298 | 0.552239 | 0.814947 | 0.000000 | 1339.216 ms |

Candidate artifact SHA-256 values:

- semantic-only: `77a8f2ee0060b73e9beb20877cf64b0da13e52ae8287774fbd8ebd1103c003c1`;
- lexical-only: `7c20bb3180cd4cb52bbd05ea3b0f4ed3f3b9533835bcd5c5f61831c389b39a08`;
- Hybrid weighted RRF: `b12198a5a8fc909f87282b42fd94172a5d7237ee2d277d833a5b474ae7c172bd`;
- rank-normalized: `c6454f3acb0a57835a195d2ec60f382e5888eb868bf73a507c84807436ba9a7e`.

The four paired Legacy comparison artifacts are committed beside the
Candidate Artifacts. Every comparison is restricted to tuning, has
`thresholds_passed: null`, and explicitly records
`independent_eval_evidence: false`. No Hybrid variant beats Legacy overall in
this run. Weighted RRF is the representative Hybrid path for business blind
A/B because it improves filtering relative to Legacy, but it is not a
retrieval-gate winner. Rank-normalized fusion is rejected for this run because
of its lower quality, 1.34-second P95, and sub-1.0 replay stability.
