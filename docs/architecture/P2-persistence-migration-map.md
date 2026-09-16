# P2 Persistence Migration Map

- Status: Accepted
- Related: P2-T01

## Scope

仅盘点 `app/services/postgres_*.py`。已存在于 `app/adapters/postgres/` 和 `app/adapters/pgvector/` 的实现不重复迁移。

## Business Capability Mapping

| capability | modules |
| --- | --- |
| Interview | `postgres_prep_plan_store`, `postgres_interview_launch_repository`, `postgres_session`, `postgres_draft_store`, `postgres_plan_revision_store`, `postgres_session_deletion`, `postgres_session_deletion_tombstones` |
| Generation | `postgres_decision_store` |
| Report | `postgres_report_artifact_store` |
| Memory | `postgres_memory_metrics`, `postgres_principal_memory_consent`, `postgres_principal_memory_control`, `postgres_principal_memory_ledger`, `postgres_principal_memory_rights`, `postgres_question_memory_index` |
| Knowledge | 无 `postgres_*` 生产 repository；knowledge persistence 已在 `app/adapters/pgvector/` 与 `app/adapters/postgres/user_documents.py` |
| Execution | `postgres_runtime_control`, `postgres_runtime_migrations` |
| Shared DB Infrastructure | `postgres_connections`, `postgres_connection_domains`, `postgres_identifiers`, `postgres_schema`, `postgres_schema_contract`, `postgres_capacity` |

## Detailed Migration Map

| module | capability | suggested target | notes |
| --- | --- | --- | --- |
| `postgres_prep_plan_store.py` | Interview | `adapters/persistence/postgres/interview` | P1 已通过 facade 使用 |
| `postgres_interview_launch_repository.py` | Interview | `adapters/persistence/postgres/interview` | P1 已通过 facade 使用 |
| `postgres_session.py` | Interview | `adapters/persistence/postgres/interview` | P1 已通过 facade 使用 |
| `postgres_draft_store.py` | Interview | `adapters/persistence/postgres/interview` | P2 迁移 |
| `postgres_plan_revision_store.py` | Interview | `adapters/persistence/postgres/interview` | P2 迁移 |
| `postgres_session_deletion.py` | Interview | `adapters/persistence/postgres/interview` | P2 迁移 |
| `postgres_session_deletion_tombstones.py` | Interview | `adapters/persistence/postgres/interview` | P2 迁移 |
| `postgres_decision_store.py` | Generation | `adapters/persistence/postgres/generation` | P2 迁移 |
| `postgres_report_artifact_store.py` | Report | `adapters/persistence/postgres/report` | P2 迁移 |
| `postgres_memory_metrics.py` | Memory | `adapters/persistence/postgres/memory` | P3 关联 |
| `postgres_principal_memory_consent.py` | Memory | `adapters/persistence/postgres/memory` | P3 关联 |
| `postgres_principal_memory_control.py` | Memory | `adapters/persistence/postgres/memory` | P3 关联 |
| `postgres_principal_memory_ledger.py` | Memory | `adapters/persistence/postgres/memory` | P3 关联 |
| `postgres_principal_memory_rights.py` | Memory | `adapters/persistence/postgres/memory` | P3 关联 |
| `postgres_question_memory_index.py` | Memory | `adapters/persistence/postgres/memory` | P3 关联 |
| `postgres_runtime_control.py` | Execution | `adapters/persistence/postgres/execution` | P7 关联 |
| `postgres_runtime_migrations.py` | Execution | `adapters/persistence/postgres/execution` | P0C/P7 关联 |
| `postgres_connections.py` | Shared DB Infrastructure | `adapters/postgres/infrastructure` | P7 关联 |
| `postgres_connection_domains.py` | Shared DB Infrastructure | `adapters/postgres/infrastructure` | P7 关联 |
| `postgres_identifiers.py` | Shared DB Infrastructure | `adapters/postgres/infrastructure` | P7 关联 |
| `postgres_schema.py` | Shared DB Infrastructure | `adapters/postgres/infrastructure` | P7 关联 |
| `postgres_schema_contract.py` | Shared DB Infrastructure | `adapters/postgres/infrastructure` | P7 关联 |
| `postgres_capacity.py` | Shared DB Infrastructure | `adapters/postgres/infrastructure` | P7 关联 |

## Migration Order

1. Interview capability（P1 已建立 facade，P2 完成正式迁移）。
2. Generation / Report capability。
3. Memory capability（与 P3 对齐）。
4. Execution / Shared DB Infrastructure（与 P7 对齐）。

## Forbidden

- 不得一次迁移多个业务能力。
- 不得直接移动文件后统一修 import。
- 不得删除仍被 production import 的 services 实现。

## Non-Goals

- 本任务不移动文件。
- 不实现新 Adapter。
- 不修改 schema/migration。
