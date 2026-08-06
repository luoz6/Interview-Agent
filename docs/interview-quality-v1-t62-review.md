# Interview Quality V1 — T62 自动审查

## 结论

```text
engineering_status=PASS
automatic_review=PASS
quality_status=NOT_REQUIRED_DETERMINISTIC_ENGINEERING
overall_status=PASS
plan_work_items=10/10
acceptance_invariants=5/5
official_acceptance=7 passed / 0 failed / 0 skipped
expanded_adjacent_regression=86 passed / 0 failed / 0 skipped
provider_calls=0
screenshots=0
open_findings=0
```

T62 已完成真实 PostgreSQL 16 旧 schema 升级、重复迁移、中断恢复、legacy Report JSON 惰性与有界批迁移、reader 双向切换、custom-format 备份恢复、约束/索引/查询计划核验以及非破坏性 rollback runbook。正式 runner 强制预检 PostgreSQL、pgvector、`pg_dump` 和 `pg_restore`，禁止 skip，并将 10 项工作内容和 5 项验收不变量映射到 7 个唯一 pytest 节点。最终 canonical SHA-256 为 `23bbc137075c7fdf49035891593b6929cac09e735b96d7642446d1b0dfeca3ae`。

## 10 项工作内容覆盖

| ID | Plan 工作内容 | 权威证据摘要 | 状态 |
|---|---|---|---|
| T62-W01 | 从旧 schema 升级到新 schema | 旧 Session/Report 数据上执行当前 additive runtime migration，升级后 payload 与 legacy 列保持不变 | PASS |
| T62-W02 | 重复执行 migration | 首次 `applied=true`，第二次 `applied=false`，latest marker/checksum/transaction mode 仅一条 | PASS |
| T62-W03 | 迁移中断后恢复 | Artifact schema 构造后注入异常；事务回滚、latest marker 未提交，随后重试成功 | PASS |
| T62-W04 | 旧 report JSON 惰性/批量迁移 | 单 session lazy promotion、有界 `limit=1` batch、终止空批、非法 limit 关闭失败 | PASS |
| T62-W05 | 新 schema 写入后切回旧 reader | Review Workflow 同事务写 Artifact/Head 与 legacy compatibility shadow，legacy reader 返回相同 report | PASS |
| T62-W06 | 再切回新 reader | `artifact_first → legacy → artifact_first` 后 active head、Artifact 对象和 hash 不漂移 | PASS |
| T62-W07 | 备份恢复后验证 Artifact hash/head | 真实 `pg_dump --format=custom` 与 `pg_restore --exit-on-error` 后 Artifact、active report、latest job、legacy hash 和行数一致 | PASS |
| T62-W08 | 检查 FK、唯一约束、索引和查询计划 | 恢复后约束集合、active-job partial index、revision/source-job 索引及强制 index path 的 `EXPLAIN` 均验证 | PASS |
| T62-W09 | 不删除 legacy 表/列 | 中断、升级、reader rollback 与 restore 后 legacy reports 表和关键列仍存在 | PASS |
| T62-W10 | 输出 rollback runbook | 已提交 fail-closed、非破坏性、含备份/恢复/切换/STOP 条件的 operator runbook | PASS |

完整要求—节点映射位于 `tests/golden/interview_quality_v1/t62-migration-acceptance-v1.json`；builder 校验 ID 完整性、节点存在性、唯一节点投影、禁止 skip、工具要求和 canonical hash。

## 5 项验收不变量

1. 旧数据可读：迁移中断、迁移成功与 restore 后都从 legacy store 读取并比较完整 payload。
2. 新数据不因 rollback 删除：legacy reader 只改变读路由；Artifact、Head 与 compatibility shadow 在切换前后保持存在且一致。
3. active pointer 无悬挂：恢复后以 Head 左连接 Artifact 检查 dangling count 为 0，并比较 active report 与 latest job。
4. migration 0 数据丢失：所有 allowlisted 表的恢复前后行数完全相等，旧 report 内容 hash 不变。
5. 恢复后的历史版本 hash 不变：恢复后的 `ReportArtifact` 与备份前对象相等，canonical Artifact hash 和 legacy source hash 不变。

## 自动审查发现与修复

### 1. 有界批处理最初在跳过已迁移 session 前应用 LIMIT

若排序靠前的 session 已有 Artifact，旧查询可能取满 limit 后全部跳过，从而在仍有未迁移行时错误返回 0。最终查询把 `NOT EXISTS(report_artifacts …)` 放在 `ORDER BY/LIMIT` 之前，并保留逐行二次检查。

### 2. 批处理回归最初依赖随机 UUID 排序

原测试先迁移“第一个创建”的 session，但随机 UUID 不保证它在 SQL 排序中位于另一个 session 之前，因此不能稳定命中上述缺陷。最终测试显式选择字典序最小 session 先迁移，再要求 `limit=1` 必须迁移剩余行。

### 3. 并发 batch 可能同时选择同一 legacy 行

只做 `NOT EXISTS` 与逐行查询仍存在两个事务同时看见空 Artifact 的竞争窗口。最终选择语句增加 `FOR UPDATE OF legacy SKIP LOCKED`，使并发 worker 对 legacy 行分片，不通过唯一约束异常完成协调。

### 4. 直接脚本入口无法导入 app

`python scripts/migrate_legacy_reports.py` 最初缺少仓库根目录模块路径，只能以 `python -m` 调用。脚本现在在直接入口下把仓库根加入 `sys.path`；直接调用和模块调用均以 dry-run、退出码 0 运行，且不读取或输出 DSN。

### 5. lazy dry-run 显示值与实际执行 limit 不一致

带 `--session-id` 时实际只迁移一条，但第一版仍显示默认 `batch_limit=100`。最终统一计算 `effective_limit=1`，dry-run 与 apply 使用同一值，并拒绝空白 session ID；输出不会泄露 session ID。

### 6. 中断事务与 migration registry 的不变量定义过严

旧 schema 可以合法地已经拥有 migration registry，因此“中断后 registry 表不存在”不是正确不变量。最终断言是 Artifact 表 DDL 回滚且 latest migration marker 计数为 0，同时旧报告仍可读。

### 7. latest migration 合约测试硬编码了过期版本

旧断言把 `followup_decision_v1` 当作永远的 latest migration；当前 registry 已前进到 `followup_prompt_lineage_v1`。测试现在分别确认历史 migration 仍存在，并以 `LATEST_RUNTIME_MIGRATION == RUNTIME_MIGRATIONS[-1]` 验证当前 latest。

以上发现均已修复并复测，T62 open findings 为 0。

## 验证结果

```text
acceptance builder --check: PASS
official PostgreSQL acceptance after final fixes: 7 passed / 0 failed / 0 skipped / 1 warning
expanded adjacent regression after final fixes: 86 passed / 0 failed / 0 skipped / 1 warning
direct CLI dry-run: PASS
module CLI dry-run: PASS
lazy CLI effective limit: PASS (1)
blank session ID: fail closed / exit 2
missing DSN runner preflight: BLOCKED_MIGRATION_ENVIRONMENT_UNAVAILABLE / exit 3
missing backup-tool container: BLOCKED_MIGRATION_ENVIRONMENT_UNAVAILABLE / exit 3
PostgreSQL: 16.14
pgvector: 0.8.6
pg_dump: 16.14
pg_restore: 16.14
provider_calls=0
first_data_request_sent=false
screenshots=0
```

唯一警告是既有 Starlette TestClient/httpx 弃用警告，不影响迁移、备份或恢复语义。

有三次诊断没有计为通过证据：交接前启动但未捕获输出的 detached official runner；一次因容器 shell 引号错误而失败的只读版本查询；一次引用不存在的过期 `test_postgres_schema_mode.py` 文件而在收集前退出的相邻回归。最终结果均来自无残留进程、严格串行、命令输出已捕获的重跑。

## 安全与边界

- restore 演练仅删除自动生成且通过 test-prefix guard 的固定 allowlist 表；未接触主工作树或部署数据库。
- custom archive 只保存在测试进程内存中，没有写入、提交或打印。
- rollback runbook 不授权生产删除或覆盖；真实 restore 必须先进入隔离恢复库，并具有明确 STOP 条件。
- T62 不需要真实 Provider，调用数为 0，未发送任何数据。
- 未截图、未生成 trace、未进行图像工作。
- T62 PASS 不改变 Gate 2–5 已记录的 Quality blocker。
- 下一任务为 T63 性能、延迟、调用预算和容量验收；总体 Goal 保持 `active`。
