# Stage 40 Release Decision: PASS

| Metric | Result | Gate |
| --- | ---: | ---: |
| ranking_accuracy | 1.000 | >= 0.85 |
| evidence_grounding_rate | 1.000 | >= 0.90 |
| max_score_delta | 0.000 | <= 8 |
| fallback_rate | 0.000 | <= 0.05 |

- completed_attempts: 40/40
- failed_gates: none
- blocking_failures: 0

## Focused Rerun

```powershell
python -m scripts.evaluate_report_quality --resume --run-id <run-id> --max-provider-invocations 50
```
