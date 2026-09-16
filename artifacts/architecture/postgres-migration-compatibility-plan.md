# PostgreSQL Migration Compatibility Plan

> 任务：P0C-T01
> 状态：PASS（只生成计划，不修改 migration）

## 1. Objective

确定当前仓库支持的已有数据库升级路径，并定义一个可复现的：

```text
pre-current supported DB state
```

用于 P0C-T02 实际验证：

```text
supported old DB state
→ migrations
→ application boot
→ existing data read
```

本任务不修改 migration、schema、runtime 代码或 production data。

## 2. Current Migration Inventory

| component | role |
| --- | --- |
| `app/services/postgres_schema_contract.py` | 定义 runtime schema migration registry、manifest、checksum、transaction mode 与最新 migration |
| `app/services/postgres_runtime_migrations.py` | 执行 PostgreSQL runtime migration，包含 advisory lock、idempotency、checksum 校验 |
| `app/adapters/postgres/migration_harness.py` | 用 owned scope 执行 apply + validate，并验证第二次 apply 不改变 schema |
| `app/ports/postgres_migrations.py` | migration port/result 定义 |
| `scripts/postgres_runtime_migrate.py` | CLI：`--apply` 前默认 dry-run，执行当前最新 runtime migration |
| `scripts/init_local_runtime.py` | 初始化/检查 Local V1 runtime；`--check` 验证 pgvector extension、runtime tables、knowledge tables |
| `scripts/migrate_legacy_reports.py` | 将 legacy report JSON 提升为 immutable V2 Artifacts |
| `app/runtime/config/compatibility.py` | 提供 `POSTGRES_RUNTIME_AUTO_MIGRATE`，默认 `false`；runtime stores 大量使用 `schema_mode="validate"` |

## 3. Latest Current Migration

依据 `app/services/postgres_schema_contract.py`：

- Migration registry 覆盖 V1 到 V30。
- `LATEST_RUNTIME_MIGRATION = RUNTIME_MIGRATIONS[-1]`
- 最新 migration id：

```text
interview_jit_main_question_v1_v30
```

因此 current runtime schema 目标是 V30 manifest。

## 4. Pre-Current Supported DB State

为 P0C-T02 定义可复现的旧数据库状态：

```text
PostgreSQL public schema
+ pgvector extension installed
+ table prefix = interview
+ pgvector table = knowledge
+ runtime schema migrations applied up to V29
+ V30 migration not yet applied
+ minimal production-shaped rows present in:
    interview_sessions
    interview_messages
    interview_reports
    interview_question_evaluations
    interview_report_jobs
    knowledge_versions
    knowledge_releases
```

要求：

- 旧状态必须使用当前 migration registry 的历史迁移逐步构造，而不是手工创建孤立表。
- 不得依赖已删除的 `app/data/knowledge/**`、`scripts/load_knowledge.py` 或 v1 corpus。
- 不得使用 SQLite 或 mock 宣布 compatibility。

## 5. Execution Sequence for P0C-T02

### Step 1 — Provision ephemeral PostgreSQL

- 启动临时 PostgreSQL，安装 `vector` extension。
- 确保默认 `search_path` 为 `public`，否则 migration 会拒绝执行。

### Step 2 — Create pre-current state

- 使用历史 migration 逻辑将 schema 推进到 V29。
- 记录迁移表和已应用 migration id/checksum。
- 写入最小但真实形状的数据：
  - session/message/report/question_evaluation
  - report job
  - active knowledge corpus version/chunks

### Step 3 — Run current migration

```bash
python scripts/postgres_runtime_migrate.py --apply
```

验证：

- `migration_id == interview_jit_main_question_v1_v30`
- `applied == true`
- migration 在 transaction 内完成

### Step 4 — Validate idempotency

再次执行 migration 或使用 `RuntimeMigrationHarness`：

- 第二次 apply 不得改变 schema
- migration id/checksum 不得变化

### Step 5 — Runtime check

```bash
python scripts/init_local_runtime.py --check
```

验证：

- `vector` extension 存在
- runtime tables 存在
- knowledge versions/releases 存在
- 旧数据可被查询

### Step 6 — Application boot

在 `POSTGRES_RUNTIME_AUTO_MIGRATE=false` 下启动 FastAPI/runtime：

- runtime stores 使用 `schema_mode="validate"`
- 不触发隐式 schema 修改
- 旧 session/report data 可读

### Step 7 — Legacy report compatibility

如旧数据包含 legacy report JSON，可选执行：

```bash
python scripts/migrate_legacy_reports.py --apply
```

验证 V2 Artifact migration 不破坏旧报告读取。

## 6. Legacy Corpus / V1 Loader Boundary

- 当前 seeding loader 是 `scripts/load_knowledge_v2.py`。
- 旧 `app/data/knowledge/**` 和 v1 loader 已删除。
- P0C-T02 不得依赖这些已删除对象。
- 旧数据库升级路径只验证 runtime/report/knowledge table 结构，不验证旧语料内容。

## 7. Stop Conditions

出现以下情况，P0C-T02 必须标记：

```text
BLOCKED_ENVIRONMENT
```

- 没有可用 PostgreSQL
- 没有 pgvector extension
- migration 需要修改生产代码才能执行
- 需要恢复已删除 v1 loader/corpus 才能验证

不得：

- 使用 SQLite
- 使用 Mock/Fake 宣布 migration compatibility 成功

## 8. Not Done Here

本计划不执行 migration、不修改 migration、不创建 schema、不写入数据库。
