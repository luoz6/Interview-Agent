# Stage 40 Real-Model Acceptance

Stage 40 completed its full real-model benchmark with 20 cases and two runs per case. Provider evidence was captured once, then deterministically rescored with the released backend rubric without additional provider calls.

The acceptance target was **40 target attempts** with a hard ceiling of **50 provider invocations**; the completed run used 40.

## Run Identity

- Acceptance completed: 2026-07-13
- Run ID: `20260710T124843Z`
- Dataset version: `report-quality-v1`
- Dataset SHA-256: `c04ab13b65e58e0ce3e26d5791f88d6404c43118daae94a0107146ea190b89b5`
- Model: `deepseek-v4-pro`
- Prompt version: `stage40-evidence-v1`
- Rubric version: `stage40-rubric-v2`
- Completed target attempts: `40/40`
- Actual provider invocations: `40`
- Exit code: `0`

## Release Gates

| Gate | Metric | Threshold | Result | Pass |
| --- | --- | ---: | ---: | --- |
| Ranking accuracy | `ranking_accuracy` | `>= 0.85` | `1.000` | Yes |
| Evidence grounding | `evidence_grounding_rate` | `>= 0.90` | `1.000` | Yes |
| Maximum score delta | `score_delta` | `<= 8` | `0.000` | Yes |
| Fallback rate | `fallback_rate` | `<= 0.05` | `0.000` | Yes |

## Blocking Assertions

- Empty and negligible answers score `0`: Passed
- No forbidden claims introduced by the report: Passed
- Applicable dimensions match the backend contract: Passed
- Aggregate recomputation matches deterministic question scores: Passed
- Provider score fields are ignored: Passed

## Decision

`PASS`

The saved provider evidence can be replayed after rubric-only changes without spending additional model calls:

```powershell
python -m scripts.rescore_report_quality --run-dir reports/stage40-acceptance/20260710T124843Z --dataset tests/golden/report_quality_v1.json
```

## Artifact Reference

- Local run directory: `reports/stage40-acceptance/20260710T124843Z`
- `metrics.json` SHA-256: `9edca889aa190e2890fd66ceff2e7091b61502adbf3db2e9781ef9f6d6452b08`
- Follow-up cases: none
