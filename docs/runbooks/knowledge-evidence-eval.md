# Knowledge Evidence Calibration Eval Runbook

This runbook covers the Evidence-specific gates required by RAG V2. Retrieval
Eval V3 remains authoritative for ranking and channel performance; the business
blind eval remains authoritative for Follow-up and Reviewer output quality.
This evaluator adds only the missing calibration layer and does not retrieve,
rerank, or call an LLM.

## Metrics

Each baseline and candidate artifact reports:

- observation completeness;
- Question Binding Precision;
- Evidence Precision@5;
- Expected-signal Coverage;
- Irrelevant Fallback Binding Rate;
- Targeted Supplementation Rate;
- Sufficiency Precision and Recall;
- Failure-vs-No-Evidence Confusion Rate;
- Replay Stability;
- per-pilot-topic breakdown.

Targeted Supplementation Rate is diagnostic, not intrinsically “higher is
better.” Register a threshold only when an independently approved policy gives
it a direction and rationale.

## Independent calibration dataset

Use 30–100 independently annotated cases across at least two pilot topics. The
dataset must include sufficient, weak, insufficient, true empty, and system
unavailable cases. It requires 20%–30% family-isolated holdout, at least two
hashed independent annotations per case, a consensus record, blinded labels,
and acceptable agreement.

Question text and expected-signal text stay outside frozen results. The dataset
uses `question_input_sha256`; expected signals are represented by hashes. The
result artifact contains only IDs, hashes, enum states, reason codes, and
metrics.

Validate before collecting engine observations:

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_evidence.py `
  validate `
  --dataset artifacts/private/evidence-calibration-v1.json
```

Generate a blank observation form if needed:

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_evidence.py `
  template `
  --output artifacts/private/evidence-observation-template.json
```

The template contains no labels or observations. Do not derive gold
sufficiency or relevance labels from Legacy or Hybrid output.

## Observation batches

An observation batch binds the exact dataset, split, engine identity, Retrieval
artifact, code tree, selection version, gate version, capture time, and every
case observation. Build the baseline holdout batch first:

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_evidence.py `
  batch `
  --dataset artifacts/private/evidence-calibration-v1.json `
  --observations artifacts/private/legacy-evidence-observations.json `
  --identity artifacts/governance/legacy-evidence-identity.json `
  --split holdout `
  --role baseline `
  --output artifacts/frozen/legacy-evidence-batch.json

F:\python3.11\python.exe scripts/evaluate_knowledge_evidence.py `
  run `
  --dataset artifacts/private/evidence-calibration-v1.json `
  --batch artifacts/frozen/legacy-evidence-batch.json `
  --output artifacts/frozen/legacy-evidence-holdout.json
```

## Holdout preregistration

After the baseline artifact exists and before Candidate holdout observations
are collected, preregister thresholds bound to the exact Candidate identity:

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_evidence.py `
  register-thresholds `
  --baseline artifacts/frozen/legacy-evidence-holdout.json `
  --candidate-identity artifacts/governance/hybrid-evidence-identity.json `
  --policy artifacts/governance/evidence-threshold-policy.json `
  --output artifacts/governance/evidence-threshold-registration.json
```

The required minimum-oriented metrics are completeness, binding precision,
Evidence Precision@5, expected-signal coverage, Sufficiency Precision/Recall,
and replay stability. The required maximum-oriented metrics are irrelevant
fallback binding and failure/no-evidence confusion.

Candidate holdout processing fails if the registration timestamp is not before
the observation batch capture time. This prevents preregistration from being
performed after viewing Candidate labels.

## Candidate and comparison

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_evidence.py `
  batch `
  --dataset artifacts/private/evidence-calibration-v1.json `
  --observations artifacts/private/hybrid-evidence-observations.json `
  --identity artifacts/governance/hybrid-evidence-identity.json `
  --split holdout `
  --role candidate `
  --output artifacts/frozen/hybrid-evidence-batch.json

F:\python3.11\python.exe scripts/evaluate_knowledge_evidence.py `
  run `
  --dataset artifacts/private/evidence-calibration-v1.json `
  --batch artifacts/frozen/hybrid-evidence-batch.json `
  --thresholds artifacts/governance/evidence-threshold-registration.json `
  --output artifacts/frozen/hybrid-evidence-holdout.json

F:\python3.11\python.exe scripts/evaluate_knowledge_evidence.py `
  compare `
  --baseline artifacts/frozen/legacy-evidence-holdout.json `
  --candidate artifacts/frozen/hybrid-evidence-holdout.json `
  --thresholds artifacts/governance/evidence-threshold-registration.json `
  --output artifacts/public/evidence-holdout-comparison.json
```

Do not promote Hybrid without a passing frozen comparison. Repository tooling
and synthetic unit tests are not calibration evidence.

Every final evidence ID must include a matching lineage record with
`content_sha256` and `corpus_manifest_sha256`. A batch rejects lineage from a
different corpus. Threshold registration and paired comparison reject anything
below 100% observation completeness or 100% replay stability, and all timestamps
must preserve baseline -> registration -> candidate capture -> candidate artifact
-> paired comparison order.
