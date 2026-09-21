# Infrastructure Import Scan

## Scope and Method

- 输入：P0A-T02 的 AST dependency scan 中 `target_layer == external` 的 raw edges。
- 目标：mandated infrastructure packages、model SDK 与 provider transport。
- 规则：不因包名自动判定违规；classification 同时考虑 package 与 source layer。

## Package Summary

| package | category | mandated | edges | source_files | source_layers |
| --- | --- | --- | ---: | ---: | --- |
| anthropic | model_sdk | yes | 0 | 0 | - |
| celery | cache_queue | yes | 1 | 1 | runtime |
| fastapi | web | yes | 63 | 15 | api, other |
| google | model_sdk | no | 0 | 0 | - |
| google_genai | model_sdk | no | 0 | 0 | - |
| httpx | provider_transport | no | 4 | 4 | adapters, evals |
| langchain | model_sdk | no | 0 | 0 | - |
| langchain_core | model_sdk | no | 0 | 0 | - |
| langchain_openai | model_sdk | no | 2 | 2 | adapters |
| langgraph | workflow | yes | 21 | 9 | adapters, graphs, runtime |
| openai | model_sdk | yes | 1 | 1 | adapters |
| psycopg | database | yes | 1 | 1 | runtime |
| psycopg2 | database | yes | 139 | 34 | adapters, evals |
| redis | cache_queue | yes | 0 | 0 | - |
| sentence_transformers | model_sdk | no | 0 | 0 | - |

## Layer x Classification Matrix

| source_layer | classification | edges |
| --- | --- | ---: |
| adapters | 合法技术依赖 | 144 |
| adapters | 明确违规 | 0 |
| adapters | 暂时无法判断 | 0 |
| adapters | 潜在违规 | 0 |
| agents | 合法技术依赖 | 0 |
| agents | 明确违规 | 0 |
| agents | 暂时无法判断 | 0 |
| agents | 潜在违规 | 0 |
| api | 合法技术依赖 | 60 |
| api | 明确违规 | 0 |
| api | 暂时无法判断 | 0 |
| api | 潜在违规 | 0 |
| application | 合法技术依赖 | 0 |
| application | 明确违规 | 0 |
| application | 暂时无法判断 | 0 |
| application | 潜在违规 | 0 |
| domain | 合法技术依赖 | 0 |
| domain | 明确违规 | 0 |
| domain | 暂时无法判断 | 0 |
| domain | 潜在违规 | 0 |
| evals | 合法技术依赖 | 4 |
| evals | 明确违规 | 0 |
| evals | 暂时无法判断 | 0 |
| evals | 潜在违规 | 0 |
| graphs | 合法技术依赖 | 13 |
| graphs | 明确违规 | 0 |
| graphs | 暂时无法判断 | 0 |
| graphs | 潜在违规 | 0 |
| other | 合法技术依赖 | 3 |
| other | 明确违规 | 0 |
| other | 暂时无法判断 | 0 |
| other | 潜在违规 | 0 |
| ports | 合法技术依赖 | 0 |
| ports | 明确违规 | 0 |
| ports | 暂时无法判断 | 0 |
| ports | 潜在违规 | 0 |
| runtime | 合法技术依赖 | 8 |
| runtime | 明确违规 | 0 |
| runtime | 暂时无法判断 | 0 |
| runtime | 潜在违规 | 0 |
| services | 合法技术依赖 | 0 |
| services | 明确违规 | 0 |
| services | 暂时无法判断 | 0 |
| services | 潜在违规 | 0 |

## Top Source Files

| source_file | edges |
| --- | ---: |
| app/adapters/persistence/postgres/principal_memory_rights.py | 18 |
| app/adapters/postgres/principal_memory.py | 12 |
| app/adapters/persistence/postgres/runtime_migrations.py | 11 |
| app/adapters/persistence/postgres/prep_plan_store.py | 9 |
| app/api/interview/routes.py | 8 |
| app/adapters/persistence/postgres/draft_store.py | 7 |
| app/adapters/persistence/postgres/session_deletion.py | 7 |
| app/api/materials/routes.py | 7 |
| app/api/prep/routes.py | 7 |
| app/api/rag/routes.py | 7 |
| app/api/reports/routes.py | 7 |
| app/adapters/persistence/postgres/interview_launch_repository.py | 6 |
| app/adapters/persistence/postgres/memory_metrics.py | 6 |
| app/adapters/persistence/postgres/principal_memory_control.py | 6 |
| app/adapters/persistence/postgres/question_memory_index.py | 6 |
| app/adapters/persistence/postgres/principal_memory_consent.py | 5 |
| app/adapters/postgres/context_artifacts.py | 5 |
| app/graphs/durable_review_graph.py | 5 |
| app/adapters/persistence/postgres/session_deletion_tombstones.py | 4 |
| app/adapters/postgres/owned_scope.py | 4 |

## Classification Counts

- 合法技术依赖: 232
- 潜在违规: 0
- 明确违规: 0
- 暂时无法判断: 0

## Limitations

- 动态 import 不纳入本报告；P0A-T02 已单独记录动态 import limitation。
- 本次只分类强制清单和 provider/model SDK/transport；pydantic 等通用框架不属于强制清单，未做违规分类。
- `services` 中大量直接基础设施依赖当前标记为“潜在违规”而非“明确违规”，因为 services 仍是 brownfield 生产实现；最终去向需 P0A-T04/P2 确认。
