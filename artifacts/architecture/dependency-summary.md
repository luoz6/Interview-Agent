# Dependency Summary

## Counting Rule

- `edges` 是 raw AST import edge 数：每条 `import` alias 或 `from ... import ...` alias 计为 1。
- `source_files` 是对应 edge 的去重源文件数，因此两者必须分别报告。
- 主指标只统计 `app.* -> app.*` 内部依赖；external import 单列。

## 1. Layer-to-Layer Matrix

| source | target | edges | source_files |
| --- | --- | ---: | ---: |
| adapters | adapters | 340 | 65 |
| adapters | application | 12 | 4 |
| adapters | domain | 480 | 80 |
| adapters | graphs | 24 | 5 |
| adapters | other | 2 | 1 |
| adapters | ports | 48 | 17 |
| adapters | runtime | 49 | 17 |
| agents | adapters | 7 | 5 |
| agents | agents | 4 | 1 |
| agents | application | 5 | 1 |
| agents | domain | 28 | 5 |
| agents | graphs | 1 | 1 |
| agents | ports | 5 | 4 |
| agents | runtime | 7 | 5 |
| api | adapters | 3 | 1 |
| api | api | 101 | 13 |
| api | application | 48 | 10 |
| api | domain | 47 | 10 |
| api | other | 5 | 1 |
| api | ports | 8 | 3 |
| api | runtime | 62 | 10 |
| application | agents | 1 | 1 |
| application | application | 75 | 12 |
| application | domain | 315 | 41 |
| application | ports | 43 | 20 |
| domain | domain | 487 | 80 |
| domain | ports | 1 | 1 |
| evals | adapters | 24 | 8 |
| evals | application | 2 | 1 |
| evals | domain | 48 | 11 |
| evals | evals | 62 | 14 |
| evals | runtime | 13 | 6 |
| graphs | adapters | 9 | 3 |
| graphs | agents | 2 | 2 |
| graphs | application | 8 | 2 |
| graphs | domain | 89 | 10 |
| graphs | graphs | 22 | 6 |
| graphs | ports | 1 | 1 |
| graphs | runtime | 9 | 2 |
| other | adapters | 1 | 1 |
| other | agents | 6 | 1 |
| other | api | 1 | 1 |
| other | domain | 37 | 24 |
| other | other | 103 | 26 |
| other | ports | 3 | 3 |
| other | runtime | 3 | 1 |
| ports | domain | 67 | 17 |
| ports | ports | 12 | 4 |
| runtime | adapters | 116 | 19 |
| runtime | agents | 9 | 6 |
| runtime | application | 35 | 8 |
| runtime | domain | 302 | 30 |
| runtime | graphs | 20 | 6 |
| runtime | other | 6 | 3 |
| runtime | ports | 12 | 4 |
| runtime | runtime | 263 | 35 |

## 1b. Required Cross-Layer Counts

| source | target | edges | source_files |
| --- | --- | ---: | ---: |
| application | services | 0 | 0 |
| application | adapters | 0 | 0 |
| domain | services | 0 | 0 |
| graphs | services | 0 | 0 |
| adapters | services | 0 | 0 |
| ports | services | 0 | 0 |
| api | services | 0 | 0 |

## 2. Top Cross-Layer Source Files

| source_file | cross_layer_edges |
| --- | ---: |
| app/runtime/composition.py | 139 |
| app/graphs/durable_interview_graph.py | 57 |
| app/runtime/interview_prep.py | 56 |
| app/adapters/providers/llm.py | 54 |
| app/api/shared/dependencies.py | 47 |
| app/adapters/memory/session_store.py | 40 |
| app/runtime/expert_evaluator.py | 36 |
| app/graphs/interview_graph.py | 31 |
| app/application/context/compression_runner.py | 30 |
| app/api/interview/routes.py | 29 |
| app/runtime/question_memory.py | 29 |
| app/runtime/memory_metrics.py | 28 |
| app/application/interview/context_artifacts.py | 26 |
| app/runtime/evidence_context_artifacts.py | 26 |
| app/adapters/persistence/postgres/session_store.py | 25 |
| app/adapters/reliability/runtime_failure.py | 25 |
| app/application/knowledge/grounding.py | 25 |
| app/agents/knowledge.py | 23 |
| app/application/interview/plan_editor.py | 23 |
| app/adapters/memory/plan_revision_store.py | 20 |

## 3. Top Cross-Layer Target Modules

| target_module | cross_layer_edges |
| --- | ---: |
| app.domain.context.artifacts | 96 |
| app.domain.interview.plan_revision | 87 |
| app.domain.knowledge.retrieval | 68 |
| app.domain.report.models | 68 |
| app.domain.interview.prep | 61 |
| app.domain.agent_execution | 46 |
| app.runtime.composition | 34 |
| app.domain.context.failure_containment | 32 |
| app.domain.memory.metrics | 32 |
| app.domain.workflow_thread_lock | 31 |
| app.domain.interview.prep_plans | 30 |
| app.domain.context.budget | 30 |
| app.runtime.config.compatibility | 28 |
| app.domain.knowledge.user_document | 26 |
| app.domain.interview.followup_prompts | 24 |
| app.domain.memory.contracts | 22 |
| app.graphs.interview_state | 22 |
| app.domain.interview.followup_diagnostics | 21 |
| app.domain.interview.decision_store | 20 |
| app.domain.runtime_events | 20 |

## 4. Services Dependency Overview

| source_layer | edges | source_files |
| --- | ---: | ---: |

## 5. Graph Dependency Overview

| target_layer | edges | source_files |
| --- | ---: | ---: |
| adapters | 9 | 3 |
| agents | 2 | 2 |
| application | 8 | 2 |
| domain | 89 | 10 |
| graphs | 22 | 6 |
| ports | 1 | 1 |
| runtime | 9 | 2 |

## Scan Metadata

- files_scanned: 513
- internal_edge_count: 3493
- external_edge_count: 2640
- unresolved_relative_import_count: 0
- dynamic_import_site_count: 1
- parse_error_count: 0
