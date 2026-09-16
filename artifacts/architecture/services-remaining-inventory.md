# P8 Services Retirement Result

Status: P8-T04 COMPLETE - `app/services` RETIRED

## Final Gate

| Measure | Final value |
| --- | ---: |
| `app/services` directory exists | no |
| Production Python imports of `app.services` | 0 |
| Test Python imports of `app.services` | 0 |
| Script Python imports of `app.services` | 0 |
| Services-related dependency baseline exceptions | 0 |
| Services modules in the generated inventory | 0 |
| Dependency scanner parse errors | 0 |

The import gate uses Python AST parsing and covers all supported static forms:

```python
import app.services.module
from app.services.module import Symbol
from app.services import module
```

## Final Owner Map

- Offline evaluation, diagnostics, quality gates, evidence, and replay tooling
  moved to `app/evals`.
- Report evaluation runtime logic moved to
  `app/application/report/evaluator.py`.
- Interview plan generation policy moved to
  `app/application/interview/plan_generation_policy.py`.
- Principal-memory proposal rules were consolidated into
  `app/application/memory/proposals.py`.
- Principal-memory operational probes and task entry moved to `app/runtime`;
  durable ledger replay moved to `app/adapters/memory`.
- Report worker, outbox worker, and workflow task entry modules moved to
  `app/runtime`.

## Protocol Compatibility

Celery imports canonical `app.runtime.*_tasks` modules. The following task-name
strings remain unchanged because they are queue protocol identifiers rather
than Python module dependencies:

```text
app.services.interview_workflow_tasks.run_interview_workflow_event
app.services.principal_memory_tasks.run_principal_memory_proposal_event
app.services.review_workflow_tasks.run_review_workflow_event
app.services.round_review_tasks.run_closed_round_review
```

The final architecture gate freezes both sides of this contract: Runtime owns
the importable modules, while the public task names retain their established
values.

## Exit Decision

- P8-T01 remaining inventory: complete.
- P8-T02 production imports: complete (`0`).
- P8-T03 compatibility shims: complete (`0`).
- P8-T04 Services deletion: complete.
- GATE-P8: satisfied.
