# Knowledge RAG V2 Canary and Rollback Runbook

Runbook version: `knowledge-canary-2026-08-12-v1`

These thresholds are fixed before any holdout or production result is inspected.

## Canary stages

Use only `0% -> 1% -> 5% -> 20% -> 50% -> 100%`. Engine assignment is a stable
hash of `assignment_version + session_id`. A rollout change applies to new
sessions; a persisted existing assignment remains authoritative.

Each non-zero stage requires all of the following before advancing:

- at least 200 candidate-engine retrieval samples;
- at least 24 hours of observation;
- privacy audit passed;
- evidence replay stability of 100%;
- zero critical regressions;
- unavailable-result rate no greater than 1%;
- profile-specific P95 latency budget satisfied.

Profile P95 budgets are PREP 1500 ms, FOLLOWUP 800 ms, QUESTION_REVIEW 1200 ms,
and REPORT_REPAIR 1200 ms. The corresponding relative budget is no more than
1.25 times the frozen Legacy P95 for that profile. Both the absolute and
relative budgets must pass.

Stage evidence is cumulative and ordered. A `5%`, `20%`, `50%`, or `100%`
observation cannot make the rollout eligible unless every earlier non-zero
stage has a frozen passing observation under the same runbook version. The
machine-readable `evaluate_knowledge_canary_progression` gate rejects duplicate
stages, skipped stages, changed runbook versions, and decisions that do not
match recomputation from their observation.

## Shadow boundary

Legacy remains the formal business result. Hybrid runs compare-only. Persist
only query hash, candidate and evidence IDs/ranks, availability and gate
differences, reason codes, latency, and component versions. Do not persist raw
JD, resume, answer, query, or knowledge body. Shadow has no evidence-binding,
review, or report writer.

## Rollback drill

On any critical regression, privacy failure, evidence replay instability, or
unavailable error-budget breach:

1. set new-session Hybrid rollout to 0%;
2. do not rewrite existing session assignments;
3. verify an existing Hybrid assignment remains interpretable by its stored
   assignment and engine versions;
4. replay at least one existing question and review evidence binding by IDs,
   content hashes, and corpus manifest;
5. regenerate or recover its report from the persisted QuestionEvaluationRecord;
6. record the reason codes and drill result.

The drill record must bind a frozen artifact hash and prove all six conditions:
Hybrid was enabled, a regression was detected, new sessions switched to
Legacy, existing assignments remained interpretable, evidence bindings replayed,
and reports recovered. `evaluate_knowledge_rollback_drill` fails closed when
any step or reason code is absent.

Legacy compatibility code may be removed only after Offline Eval, ablation,
Shadow, Canary, evidence replay, regression, rollback drill, documentation, and
compatibility-removal gates all pass.
