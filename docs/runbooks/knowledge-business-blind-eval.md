# Knowledge Business Blind Eval Runbook

This runbook governs the independent human A/B evaluation required for the
RAG V2 Follow-up and Reviewer release gates. It does not replace the existing
Stage 40 Report quality evaluator. Report quality continues to use
`scripts/evaluate_report_quality.py` and `scripts/rescore_report_quality.py`.

## Scope and non-goals

The shared business-eval contract covers exactly two targets:

- `followup`: answer specificity, missing/incorrect-signal targeting, depth
  gain, role/seniority relevance, evidence grounding, repetition,
  over-leading, and unsupported technical claims;
- `reviewer`: expert agreement, score stability, evidence support, confidence
  calibration, no-evidence handling, system-failure handling, unsupported
  judgments, and repeated-evaluation variance.

The tool never invents human labels, agreement values, annotator identities,
adjudication, or consensus. The annotation template deliberately contains
empty `records` and `consensus` arrays. Engine outputs are not acceptable as
human annotations.

## Privacy boundary

The source dataset and blind package contain raw questions, candidate answers,
and engine outputs. They are restricted evaluation inputs and must remain in an
approved private location.

The frozen comparison result contains only case IDs, input hashes, engine
identity, aggregate ratings, annotation/consensus record hashes, and gate
decisions. It does not contain question text, answer text, or output text.

The blind mapping is a restricted unblinding key. Annotators must receive only
the blind package and annotation instructions, never the mapping or engine
identity.

## Dataset gate

The release dataset must contain 50–100 `Question + Candidate Answer` cases.
It must include both Follow-up and Reviewer in tuning and holdout, all eight
required scenario types, and a 20%–30% holdout. Case families cannot cross the
tuning/holdout boundary. The split and engine outputs must be frozen before
annotation.

Validate the dataset:

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_business_quality.py `
  validate `
  --dataset artifacts/private/knowledge-business-v1.json `
  --release-shape
```

Do not tune prompts, routing profiles, thresholds, or implementation from
holdout results.

## Randomized blind package

Use a confidential seed. Keep the package and mapping in different
access-controlled locations.

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_business_quality.py `
  package `
  --dataset artifacts/private/knowledge-business-v1.json `
  --split tuning `
  --seed <confidential-seed> `
  --package-output artifacts/private/tuning-blind-package.json `
  --mapping-output artifacts/restricted/tuning-unblinding-key.json
```

The same process creates the holdout package. Output files are frozen and
cannot be overwritten.

Create a blank annotation template:

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_business_quality.py `
  annotation-template `
  --package artifacts/private/tuning-blind-package.json `
  --output artifacts/private/tuning-annotations-template.json
```

Every case requires at least two distinct, independently hashed annotators and
one consensus/adjudication record that binds all annotation-record hashes.
Observed inter-rater agreement must meet the preregistered minimum.

## Threshold preregistration

Tune and inspect only the tuning split. Before any holdout annotation begins,
register the complete Follow-up and Reviewer thresholds. Registration binds
the exact dataset, blind package, unblinding mapping, baseline identity,
candidate code tree, and candidate profile.

Positive dimensions use minimum candidate-minus-baseline deltas. Negative
dimensions (`repetition`, `over_leading`, unsupported claims/judgments, and
repeated-evaluation variance) use maximum deltas.

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_business_quality.py `
  register-thresholds `
  --dataset artifacts/private/knowledge-business-v1.json `
  --package artifacts/private/holdout-blind-package.json `
  --mapping artifacts/restricted/holdout-unblinding-key.json `
  --thresholds artifacts/governance/business-thresholds.json `
  --rationale-record-sha256 <approved-rationale-sha256> `
  --output artifacts/governance/business-threshold-registration.json
```

Registration after annotation collection starts fails closed.

## Compare independent annotations

```powershell
F:\python3.11\python.exe scripts/evaluate_knowledge_business_quality.py `
  compare `
  --dataset artifacts/private/knowledge-business-v1.json `
  --package artifacts/private/holdout-blind-package.json `
  --mapping artifacts/restricted/holdout-unblinding-key.json `
  --annotations artifacts/private/holdout-independent-annotations.json `
  --registration artifacts/governance/business-threshold-registration.json `
  --output artifacts/public/knowledge-business-holdout-result.json
```

The comparison fails closed for incomplete dimensions, duplicate annotators,
missing consensus, insufficient agreement, mismatched identities, late
registration, or absent holdout registration.

## Release decision

Repository tooling alone is not release evidence. Keep Legacy as the formal
engine and Hybrid rollout at 0% until all of the following exist and pass:

- a valid independent 50–100-case dataset;
- complete blind annotations and adjudication;
- acceptable inter-rater agreement;
- preregistered holdout thresholds;
- a passing frozen holdout result;
- the separate Retrieval Eval V3, privacy, PostgreSQL, Shadow, Canary, and
  rollback gates described in the RAG V2 runbooks.
