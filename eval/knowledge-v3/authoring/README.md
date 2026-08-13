# Knowledge Eval V3 annotation authoring package

This directory is a blank, restricted authoring scaffold for exactly 100 new
Knowledge Eval V3 cases: 75 tuning and 25 holdout. It is not a runnable V3
dataset and it is not evidence that independent annotation, agreement, Legacy
V3 evaluation, Hybrid evaluation, Shadow, Canary, or production approval has
been completed.

The package is bound to RocketMQ corpus `memory-p1-zh-v4` and manifest
`deb709817c6ea1ac89db8f0452f1183d0168952d5d568e08b704869c90555e84`.
It contains all 14 case types from the V3 contract and pre-freezes one unique
family per slot so no family can cross tuning and holdout.

## Ownership and access

- A case author fills the tuning authoring file without viewing engine output.
- A separate holdout owner controls the holdout authoring file and must not
  participate in Hybrid tuning.
- Two qualified backend interviewers label every authored case independently.
- An adjudicator resolves disagreements only after both independent records
  have been frozen and hashed.
- Raw query text and reviewer notes stay in the restricted annotation system.
  They must not be copied into runtime evaluation artifacts.

Do not give Hybrid developers access to authored holdout queries, labels,
annotation records, or adjudication output before the final profile and
threshold registration are frozen.

## Files

- `case-quota.json`: exact split, case-type, and evaluation-group quotas.
- `tuning-authoring-template.jsonl`: 75 blank tuning case slots.
- `holdout-authoring-template.jsonl`: 25 blank, sealed holdout case slots.
- `family-isolation-map.json`: pre-frozen family/split ownership.
- `annotator-a-template.jsonl` and `annotator-b-template.jsonl`: independent
  label forms; neither may be derived from retrieval output.
- `adjudication-template.jsonl`: blank consensus form.
- `chunk-catalog.json`: privacy-safe corpus metadata without knowledge body,
  references, content hashes, or question patterns.
- `package-manifest.json`: corpus, baseline, protocol, file-hash, and status
  binding for this scaffold.

## Workflow

1. Verify this untouched scaffold with:

   ```powershell
   F:\python3.11\python.exe scripts\build_knowledge_eval_v3_annotation_package.py validate
   ```

2. Copy the package into the access-controlled annotation system. Do not edit
   the repository copy as if it were completed evidence.
3. Follow `annotation-protocol.md`; freeze authored queries before either
   retrieval engine is run.
4. Keep tuning and holdout in separate access groups. The holdout owner must
   verify that no `case_family` appears in tuning.
5. After two independent annotations and adjudication, compile a separate V3
   dataset, calculate canonical SHA-256, and run the repository V3 validator.
6. Freeze the Legacy tuning and Legacy holdout artifacts before any Hybrid
   holdout access. Pre-register promotion thresholds before Hybrid holdout.

The historical 12-case and 18-case Legacy baselines are deliberately not
copied, concatenated, or relabeled here. They remain historical smoke/baseline
evidence only.
