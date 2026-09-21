# P9 Legacy Version Removal Gate

Status: P9-T03 COMPLETE - no removal authorized

## Rule

A legacy module may be deleted only when all four values are zero:

1. production import
2. Runtime wiring
3. integration dependency
4. migration dependency

Unversioned modules that share a base name with `_v2`/`_v3` are audited as v1.

## Summary

- Version families: 3
- Legacy modules audited: 4
- Removal authorized: 0
- Retained due to blockers: 4
- Parse errors: 0

| legacy module | replacement | production | runtime | integration | migration | action |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `app.domain.knowledge.eval_dataset_v2` | `app.domain.knowledge.eval_dataset_v3` | 2 | 0 | 3 | 2 | `retain_blocked` |
| `app.domain.knowledge.eval_metrics_v2` | `app.domain.knowledge.eval_metrics_v3` | 2 | 0 | 2 | 3 | `retain_blocked` |
| `app.graphs.durable_interview_state` | `app.graphs.durable_interview_state_v3` | 3 | 3 | 6 | 1 | `retain_blocked` |
| `app.graphs.durable_interview_state_v2` | `app.graphs.durable_interview_state_v3` | 2 | 2 | 3 | 0 | `retain_blocked` |

## Blocking Evidence

### app.domain.knowledge.eval_dataset_v2

Blocked by: production_import, integration_dependency, migration_dependency.

- `production_import`: `app/domain/knowledge/eval_dataset_v3.py:10` (from_import)
- `production_import`: `app/domain/knowledge/eval_metrics_v2.py:7` (from_import)
- `integration_dependency`: `tests/contracts/test_knowledge_eval_dataset_v2.py:7` (from_import)
- `integration_dependency`: `tests/unit/test_knowledge_eval_cli_v2.py:4` (from_import)
- `integration_dependency`: `tests/unit/test_knowledge_eval_metrics_v2.py:5` (from_import)
- `migration_dependency`: `app/domain/knowledge/eval_dataset_v3.py:10` (from_import)
- `migration_dependency`: `scripts/evaluate_knowledge_retrieval_v2.py:15` (from_import)

### app.domain.knowledge.eval_metrics_v2

Blocked by: production_import, integration_dependency, migration_dependency.

- `production_import`: `app/application/knowledge/eval_artifacts_v3.py:26` (from_import)
- `production_import`: `app/domain/knowledge/eval_metrics_v3.py:13` (from_import)
- `integration_dependency`: `tests/unit/test_knowledge_eval_metrics_v2.py:9` (from_import)
- `integration_dependency`: `tests/unit/test_knowledge_eval_metrics_v3.py:7` (from_import)
- `migration_dependency`: `app/application/knowledge/eval_artifacts_v3.py:26` (from_import)
- `migration_dependency`: `app/domain/knowledge/eval_metrics_v3.py:13` (from_import)
- `migration_dependency`: `scripts/evaluate_knowledge_retrieval_v2.py:19` (from_import)

### app.graphs.durable_interview_state

Blocked by: production_import, runtime_wiring, integration_dependency, migration_dependency.

- `production_import`: `app/graphs/durable_interview_graph.py:16` (from_import)
- `production_import`: `app/graphs/durable_interview_state_v2.py:5` (from_import)
- `production_import`: `app/runtime/interview_workflow.py:10` (from_import)
- `runtime_wiring`: `app/graphs/durable_interview_graph.py:16` (from_import)
- `runtime_wiring`: `app/graphs/durable_interview_state_v2.py:5` (from_import)
- `runtime_wiring`: `app/runtime/interview_workflow.py:10` (from_import)
- `integration_dependency`: `tests/integration/postgres/test_durable_interview_graph.py:16` (from_import)
- `integration_dependency`: `tests/integration/postgres/test_interview_workflow_store.py:15` (from_import)
- `integration_dependency`: `tests/integration/postgres/test_langgraph_recovery_postgres.py:15` (from_import)
- `integration_dependency`: `tests/unit/test_durable_interview_graph.py:34` (from_import)
- `integration_dependency`: `tests/unit/test_durable_interview_state.py:7` (from_import)
- `integration_dependency`: `tests/unit/test_principal_memory_consume_graph.py:5` (from_import)
- `migration_dependency`: `app/graphs/durable_interview_state_v2.py:5` (from_import)

### app.graphs.durable_interview_state_v2

Blocked by: production_import, runtime_wiring, integration_dependency.

- `production_import`: `app/runtime/composition.py:1877` (from_import)
- `production_import`: `app/runtime/interview_workflow.py:11` (from_import)
- `runtime_wiring`: `app/runtime/composition.py:1877` (from_import)
- `runtime_wiring`: `app/runtime/interview_workflow.py:11` (from_import)
- `integration_dependency`: `tests/unit/test_dual_langgraph_rollout.py:13` (from_import)
- `integration_dependency`: `tests/unit/test_durable_interview_graph.py:35` (from_import)
- `integration_dependency`: `tests/unit/test_durable_interview_state.py:8` (from_import)

## Decision

No legacy version is removable under the four-gate rule. Existing version contracts remain active and were not copied or deleted merely to force dependency counts to zero.

No production module, test, script, dataset, or persisted-state compatibility path was deleted in P9-T03.
