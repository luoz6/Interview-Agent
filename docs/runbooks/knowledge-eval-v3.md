# Knowledge Eval V3 Release 0 Runbook

Every core V3 case type must have at least three independent cases; merely
including a type once is not a release-shaped dataset. Evidence Gate
calibration is evaluated separately by `knowledge-evidence-eval.md`.

Runbook version: `knowledge-eval-v3-2026-08-12-v1`

This runbook defines the reproducible Release 0 workflow. It does not claim
that the required human dataset, database authorization, production Shadow,
Canary, privacy review, or rollback evidence already exists.

## 1. Freeze independent annotation

Generate a blank form and a privacy-safe chunk catalog. The command never
queries Legacy or Hybrid and never infers relevance labels:

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py template `
  --manifest app/data/knowledge_v2/manifest.json `
  --output artifacts/knowledge-eval-v3-annotation-template.json `
  --catalog-output artifacts/knowledge-eval-v3-chunk-catalog.json
```

The annotation owner must create 80-120 Chinese retrieval cases covering every
V3 case type. At least two qualified backend interviewers independently label
each case without seeing Legacy or Hybrid output. Record only hashed annotator,
annotation, consensus, and provenance identities in the dataset. Raw review
records stay in the separately controlled annotation system.

Freeze `case_family` assignment and the tuning/holdout split before running an
engine. One family may not cross splits. Holdout must be 20%-30% of all cases.
Record a preselected agreement metric, its minimum, and its observed value.
The observed value must meet the frozen minimum.

Do not concatenate the historical 30-, 18-, and 12-case sets. They overlap in
purpose, lack the V3 governance records, and do not prove independent labeling.

## 2. Validate the frozen dataset

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py validate `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --manifest app/data/knowledge_v2/manifest.json
```

Validation fails closed for wrong corpus identity, missing chunk IDs, fewer
than 80 or more than 120 cases, incomplete case-type coverage, family leakage,
missing independent annotation records, insufficient agreement, or an invalid
holdout ratio.

## 3. Freeze Legacy artifacts

Use the authorized PostgreSQL corpus. The CLI validates the schema; it does not
create or migrate production data.

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py run `
  --engine legacy `
  --split tuning `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --output artifacts/knowledge-eval-v3-legacy-tuning.json

F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py run `
  --engine legacy `
  --split holdout `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --output artifacts/knowledge-eval-v3-legacy-holdout.json
```

Artifacts are created exclusively and never overwritten. They bind the
dataset, corpus manifest, provider/model/revision/dimension, retrieval profile,
engine, Git revision, code-tree hash, metrics, per-case candidate ranks/scores,
latency, channel IDs, evidence IDs, replay result, availability, and reason
codes. They exclude query text, knowledge body, resume, JD, and answers.

## 4. Tune and run ablations without viewing candidate holdout

Run Hybrid only on tuning, diagnose case-type regressions, and freeze the final
profile. The same runner supports semantic-only, lexical-only, unweighted RRF,
weighted RRF, and rank-normalized score fusion; it does not duplicate retrieval
implementations. Run each variant to a separate non-overwritable artifact:

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py run `
  --engine hybrid-v2 `
  --ablation semantic-only `
  --split tuning `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --output artifacts/knowledge-eval-v3-semantic-only-tuning.json

F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py run `
  --engine hybrid-v2 `
  --ablation lexical-only `
  --split tuning `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --output artifacts/knowledge-eval-v3-lexical-only-tuning.json

F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py run `
  --engine hybrid-v2 `
  --ablation unweighted-rrf `
  --split tuning `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --output artifacts/knowledge-eval-v3-unweighted-rrf-tuning.json

F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py run `
  --engine hybrid-v2 `
  --ablation weighted-rrf `
  --split tuning `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --output artifacts/knowledge-eval-v3-weighted-rrf-tuning.json

F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py run `
  --engine hybrid-v2 `
  --ablation rank-normalized-score `
  --split tuning `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --output artifacts/knowledge-eval-v3-rank-normalized-tuning.json

F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py compare `
  --baseline artifacts/knowledge-eval-v3-legacy-tuning.json `
  --candidate artifacts/knowledge-eval-v3-weighted-rrf-tuning.json `
  --output artifacts/knowledge-eval-v3-paired-tuning.json
```

For cutoff, channel-weight, timeout, minimum-score, and soft-vs-hard routing
sweeps, provide a complete `ResolvedRetrievalProfile` JSON with `--profile`.
The selected ablation validates the profile shape, and the entire profile hash
is frozen in every artifact. Use `routing_policy=soft` for normal operation;
`hard` is an explicit experiment that filters the annotated domains/tags before
fusion. Never sweep on holdout.

Each artifact includes per-case-type Recall/MRR/NDCG/Hit@1, no-evidence,
hard-negative, excluded-violation, evidence-precision, routing, and completeness
metrics. Paired artifacts include the corresponding per-case-type deltas so an
aggregate improvement cannot hide a case-type regression.

## 5. Pre-register holdout thresholds

Create a reviewed policy JSON containing `primary_metric`, `minimum_deltas`,
`maximum_deltas`, `absolute_minimums`, `absolute_maximums`,
`profile_p95_budgets_ms`, `profile_p95_relative_limits`, and
`rationale_record_sha256`. It must register all
release metrics: Recall@5, MRR@5, NDCG@5, Hit@1, hard-negative FPR,
no-evidence F1, excluded violation rate, replay stability, observation
completeness, and P95 latency. Do not copy example numbers from documentation;
the accountable owners must approve real thresholds after the dataset and
Legacy baseline are frozen.

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py register-thresholds `
  --baseline artifacts/knowledge-eval-v3-legacy-holdout.json `
  --policy approvals/knowledge-eval-v3-threshold-policy.json `
  --candidate-ablation weighted-rrf `
  --candidate-profile approvals/knowledge-eval-v3-final-profile.json `
  --output artifacts/knowledge-eval-v3-threshold-registration.json
```

The registration binds the exact Legacy artifact and the final Candidate engine,
code revision/tree hash, profile hash, provider/model/revision/dimension, and
absolute plus relative P95 budgets. It is non-overwritable. Its timestamp must
be later than the baseline and earlier than Hybrid holdout. Any identity drift
requires a new registration before touching holdout.

## 6. Run and decide holdout

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py run `
  --engine hybrid-v2 `
  --ablation weighted-rrf `
  --profile approvals/knowledge-eval-v3-final-profile.json `
  --split holdout `
  --dataset tests/golden/knowledge_retrieval_v3.json `
  --thresholds artifacts/knowledge-eval-v3-threshold-registration.json `
  --output artifacts/knowledge-eval-v3-hybrid-holdout.json

F:\python3.11\python.exe scripts/evaluate_knowledge_retrieval_v3.py compare `
  --baseline artifacts/knowledge-eval-v3-legacy-holdout.json `
  --candidate artifacts/knowledge-eval-v3-hybrid-holdout.json `
  --thresholds artifacts/knowledge-eval-v3-threshold-registration.json `
  --output artifacts/knowledge-eval-v3-paired-holdout.json
```

Both the Hybrid holdout run and comparison refuse to run without the matching
prior registration. Registration is checked before the holdout engine runs.
The paired artifact records `thresholds_passed` and every failed threshold.
Any failure keeps Legacy authoritative. Passing offline retrieval is necessary
but not sufficient for production promotion.

## 7. Remaining release evidence

Before a non-zero Canary or Legacy retirement, separately complete and archive:

- blind follow-up and Reviewer quality review;
- protected PostgreSQL integration tests under explicit authorization;
- privacy audit of persisted Shadow and evaluation material;
- production Shadow observation with Legacy as the formal result;
- each pre-registered Canary sample/duration/error-budget gate;
- a real rollback drill proving assignment interpretation, evidence replay, and
  report recovery.

Follow [knowledge-rag-v2-canary.md](knowledge-rag-v2-canary.md) for production
staging. Repository tests and synthetic fixtures cannot substitute for these
operational records.
