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
