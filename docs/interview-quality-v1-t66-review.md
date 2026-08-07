# Interview Quality V1 — T66 自动审查

## 结论

```text
engineering_status=PASS
automatic_review=PASS
quality_status=NOT_REQUIRED_DETERMINISTIC_ENGINEERING
overall_status=PASS
plan_requirements=10/10
focused_t66=10 passed / 0 failed
adjacent_non_postgresql=264 passed / 0 failed / 6 PostgreSQL deselected
postgres_artifact_and_migration=30 passed / 0 failed
provider_calls=0
real_candidate_data_used=false
screenshots=0
traces=0
secret_leaks=0
cleanup_residue=0
open_findings=0
```

T66 的 10 项隐私、安全和因果边界要求均已实现并通过自动回归。此次任务属于确定性工程验收，不需要调用真实 Provider。T65 的正式 Provider Quality 基准仍因已授权模型 `deepseek-chat` 与 Provider 当前暴露模型不一致而保持 `BLOCKED_MODEL_VERSION_DRIFT`；T66 没有修改或覆盖该结论。

## 要求覆盖

| ID | Plan 要求 | 关键验证 | 状态 |
|---|---|---|---|
| T66-R01 | 报告/评分函数签名不接受 Principal Memory | 对生产函数签名进行反射检查 | PASS |
| T66-R02 | Report Artifact 不包含 Memory payload | 发布模型和持久化模型递归拒绝 Memory 键 | PASS |
| T66-R03 | Prompt injection 不泄露系统/知识参考答案 | 使用真实 `ExaminerAgent` 验证危险输出在显示和持久化前终止 | PASS |
| T66-R04 | logs/metrics 不记录完整简历、答案、Prompt 或密钥 | 日志、指标 schema 和 agent metadata canary 回归 | PASS |
| T66-R05 | family 级 PlanSourceRecord 因果语义符合 T05 | 单份 source、引用、tombstone、删除/保留与禁止 regenerate 回归 | PASS |
| T66-R06 | Report history/会话删除明确 | 内存与 PostgreSQL 删除、重放、enqueue/publish fail-closed 回归 | PASS |
| T66-R07 | PDF 不含内部敏感字段 | 对提取后的 PDF 文本进行 Prompt lineage、Provider ID、DSN、密钥 canary 检查 | PASS |
| T66-R08 | API 不可跨 session 读取 report ID | 直接报告和 PDF 路由强制 session binding，跨会话统一返回通用 404 | PASS |
| T66-R09 | Hash 不作为访问控制 | 同时掌握 report ID 与 Artifact SHA-256 仍不能跨 session 读取 | PASS |
| T66-R10 | Error response 不泄露数据库/Provider secret | detail、list、PDF、progress 及 latest-job 嵌套错误均脱敏 | PASS |

精确的要求—pytest 节点映射记录在 `docs/interview-quality-v1-t66-evidence.json`。

## 自动审查发现并关闭的问题

1. Legacy `InMemoryReportJobStore` 删除会话历史时遗漏 `_session_jobs` 索引。删除路径现同步清理该索引，重放删除保持幂等。
2. Artifact-first API 会公开原始 `ReportJobV2.error_code`，包括嵌套的 `active_artifact.latest_job.error_code`。现在只返回稳定的公开错误码，未知内部错误统一为 `report_generation_failed`。
3. `ReportProgress.metadata` 原先可能携带任意内部字段。现在采用显式公开 allowlist，Prompt、Provider key、DSN 和自由文本均被丢弃。
4. 会话删除最初只清理部分 review job 的 context Artifact owner refs。现在遍历全部 v2 和 legacy review jobs，删除所有关联引用。
5. PostgreSQL 聚焦测试最初残留临时表和零参数 trigger functions。fixture 已补充双重清理，复测后两类对象计数均为 0。

以上问题均已修复并完成聚焦测试与相邻回归，当前 open findings 为 0。

## 关键安全边界

- `PublishReportArtifact` 和 `ReportArtifact` 会递归拒绝 Principal Memory、memory context/payload/facts、assistance memory、historical preference 等键，嵌套结构也不能绕过。
- Follow-up Provider 输出先完整缓冲，再由 `validate_followup_output` 检查；危险内容不会产生已持久化 chunk，也不会先显示后撤回。失败结果固定为 `unsafe_generation`、不可重试、terminal。
- 报告与 PDF 直接读取都必须绑定 `session_id`。跨 session、不存在的报告和处于 deleting 状态的 session 使用不可区分的通用 404；缺少 binding 为 422。
- PostgreSQL Artifact UPDATE 继续被禁止。Artifact DELETE 仅允许 owning session 已处于 `deleting`；普通直接删除仍违反 immutable contract。
- 新增 append-only migration `report_history_session_deletion_v1`，checksum 为 `ce772763ca6db4de38fc63a1ff5a549b254891e875d335e25449ec4aef55fda7`，未改写历史 migration checksum。

## 验证结果

```text
focused T66 full stack: 10 passed / 0 failed / 1 warning
focused T66 PostgreSQL: 1 passed / 0 failed / 9 deselected / 1 warning
adjacent non-PostgreSQL: 264 passed / 0 failed / 6 PostgreSQL deselected / 1 warning
PostgreSQL Artifact + runtime migration: 30 passed / 0 failed / 1 warning
py_compile: PASS
git diff --check: PASS
credential values leaked into diff: 0
temporary PostgreSQL tables after cleanup: 0
temporary PostgreSQL functions after cleanup: 0
image/zip changes: 0
```

唯一警告是既有的 Starlette TestClient/httpx 弃用警告，不改变测试语义。环境未安装 Ruff，因此 Ruff 为 `NOT_RUN_MODULE_UNAVAILABLE`，没有被伪报为通过；Ruff 也不是既有的 T66 验收 Gate。

## 真实性与继续执行

- 未调用 Provider，未发送首次数据请求。
- 未使用真实候选人数据。
- 未截图、未生成 trace、未进行任何图像工作。
- 未把环境中的 Provider key、DSN 或其他凭证值写入 diff。
- T64/T65 冻结的 Provider candidate 仍为 revision `214a70646df9f23f38874a2854b9a334a10c269f`、tree `83d01ae72a483fb181b435dea4628843aa40c809`。
- T66 PASS 不等于 Gate 2–5 Quality PASS，也不解除 T65 的外部模型版本漂移阻塞。
- 下一任务是 T67；总体 Goal 保持 `active`。
