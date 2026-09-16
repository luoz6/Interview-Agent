# PostgreSQL Migration Compatibility Result

> 任务：P0C-T02
> 状态：PASS

## 1. Conclusion

在真实 PostgreSQL + pgvector 环境中完成了 V29 → V30 升级验证：

```text
supported old DB state
→ migrations
→ runtime check
→ application/runtime preflight
→ existing data read
```

未使用 SQLite、Mock 或 Fake。

## 2. Environment

- PostgreSQL: `127.0.0.1:5432`
- Database: `interview`
- pgvector extension: present
- Test prefix: `p0c02`
- Test pgvector table: `p0c02_knowledge_chunks`

## 3. Pre-Current State

创建 V29 旧状态：

- `p0c02_schema_migrations` 应用了 29 个 migration。
- 不包含最新 V30 migration：
  - `interview_jit_main_question_v1_v30`
- 从现有 production-shaped 数据复制：
  - `p0c02_sessions`: 1
  - `p0c02_messages`: 8
  - `p0c02_reports`: 1
  - `p0c02_question_evaluations`: 5
  - `p0c02_report_jobs`: 1

## 4. Migration Execution

命令：

```text
INTERVIEW_RUNTIME_TABLE_PREFIX=p0c02
PGVECTOR_TABLE=p0c02_knowledge_chunks
python -m scripts.postgres_runtime_migrate --apply
```

结果：

```text
mode=APPLY
migration=interview_jit_main_question_v1_v30
applied=true
identifier_max_bytes=52
```

迁移后 `p0c02_schema_migrations` 有 30 条 migration，包含 V30。

## 5. Runtime Check

命令：

```text
python -m scripts.init_local_runtime --check
```

结果：

```text
status: ok
initialized: true
vector_extension: true
runtime_tables:
  p0c02_sessions
  p0c02_messages
  p0c02_reports
  p0c02_question_evaluations
  p0c02_report_jobs
```

## 6. Application / Runtime Preflight

命令：

```text
python -m scripts.runtime_preflight --profile runtime
```

关键结果：

```text
runtime_control.tables = 3
runtime_control.indexes = 13
runtime_control.cascade_foreign_keys = 3
runtime_control.ledger_insert_p95_ms = 31.844
```

## 7. Existing Data Read After Migration

| table | rows |
| --- | ---: |
| `p0c02_sessions` | 1 |
| `p0c02_messages` | 8 |
| `p0c02_reports` | 1 |
| `p0c02_question_evaluations` | 5 |
| `p0c02_report_jobs` | 1 |
| `p0c02_schema_migrations` | 30 |

旧数据在 V30 迁移后仍可读取。

## 8. Idempotency Check

对当前生产 prefix `interview` 再次执行 migration：

```text
python -m scripts.postgres_runtime_migrate --apply
```

结果：

```text
migration=interview_jit_main_question_v1_v30
applied=false
identifier_max_bytes=56
```

第二次 apply 未改变 schema。

## 9. Critical Boundary

- 不依赖已删除的 v1 loader / old corpus。
- 不依赖 SQLite / Mock。
- 使用真实 PostgreSQL + pgvector。
- V30 migration 已真实应用并验证数据读取。
