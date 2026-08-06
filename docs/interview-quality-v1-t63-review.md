# Interview Quality V1 — T63 自动审查

## 结论

```text
engineering_status=PASS
automatic_review=PASS
quality_status=BLOCKED
overall_status=BLOCKED
task_classification=ENGINEERING_PASS_QUALITY_BLOCKED
requirement_mapping=22/22
planned_scenarios=432
windows_operation_samples=318
official_tests=20 passed / 0 failed / 0 skipped / 1 warning
expanded_regression=109 passed / 0 failed / 0 skipped / 1 warning
provider_calls=0
screenshots=0
open_engineering_findings=0
```

T63 已完成 Windows/PostgreSQL 本地 Engineering 验收。正式 Gate 在干净修订
`c7a29f79ce32c2335e3767fa1d034107c952e127` 上重新生成并校验全部运行 Artifact，
覆盖 22 项 Plan 要求、432 个计划场景、318 个实际本地操作样本、20 个唯一验收测试节点，
并执行四域 PostgreSQL 连接容量测试。Engineering 没有开放问题。

T63 不宣称 Quality PASS。真实 Provider 使用 Artifact、同路径 Report 完成基线以及 Ubuntu
实测均不存在，且授权模型 `deepseek-chat` 与此前可用模型存在版本漂移。因此准确保留以下四个
Quality blocker：

```text
ACTUAL_PROVIDER_USAGE_ARTIFACT_MISSING
BLOCKED_MODEL_VERSION_DRIFT
INSUFFICIENT_BASELINE
UBUNTU_MEASUREMENT_NOT_RUN
```

## 22 项要求映射

| ID | Plan 要求 | 本次权威证据 | 结论 |
|---|---|---|---|
| T63-M01 | prep plan generation | Windows 40 样本，p50 0.0000590s，p95 0.0000826s | PASS_ENGINEERING |
| T63-M02 | plan revision read/write | PostgreSQL read 40 样本、write 8 样本 | PASS_ENGINEERING |
| T63-M03 | session start，Provider 0 调用 | 24 样本，所有样本 `provider_calls=0` | PASS_ENGINEERING |
| T63-M04 | Decision p50/p95 | T37 冻结 synthetic fixture 分 cohort 验证；没有伪造 fixed Decision baseline | PASS_ENGINEERING_FIXTURE |
| T63-M05 | Generation TTFT/complete 分离 | T37 fixture 独立记录并通过 Engineering Gate | PASS_ENGINEERING_FIXTURE |
| T63-M06 | SSE recovery | T37 fixture p95 0.22–0.32s，恢复路径 0 次重复调用 | PASS_ENGINEERING_FIXTURE |
| T63-M07 | Report job total | 本地 repository commit 26 样本；真实 Provider completion 基线缺失 | BLOCKED_QUALITY_INSUFFICIENT_BASELINE |
| T63-M08 | Artifact list/get/PDF | 每条路径 30 个 Windows 样本 | PASS_ENGINEERING |
| T63-M09 | PostgreSQL connections/capacity | 22 个同时应用连接、4 个 advisory locks、0 timeout | PASS_ENGINEERING |
| T63-M10 | Provider calls/tokens/cost per session | PASS 合同要求 calls、sessions、tokens、cost、path、SHA 全部绑定；本次真实 Artifact 缺失 | BLOCKED_QUALITY_PROVIDER_ARTIFACT |
| T63-M11 | retry amplification | T37 fixture 为 1.0，阈值 1.15 | PASS_ENGINEERING_FIXTURE |
| T63-S01 | questions 3/5/8/10 | 432 场景冻结矩阵和本地样本维度均完整 | PASS |
| T63-S02 | follow-ups 0/1/2 | 432 场景冻结矩阵和本地样本维度均完整 | PASS |
| T63-S03 | scored/partial/unscored | 432 场景冻结矩阵和本地样本维度均完整 | PASS |
| T63-S04 | history 1/5/20 | 三个 history cohort 各 30 个 active-get 样本 | PASS |
| T63-S05 | cold/warm | 冻结矩阵和本地样本分开标记，不混合 cohort | PASS |
| T63-S06 | Windows/Ubuntu | Windows 实测；Ubuntu 明确 `NOT_RUN`，没有伪造样本 | BLOCKED_QUALITY_UBUNTU |
| T63-A01 | 评估 §5.6 全部 Gate | T37 Engineering Gate 通过，缺失真实证据保持 BLOCKED | PASS_FAIL_CLOSED |
| T63-A02 | 无明显 N+1 | active report 使用 `get_latest_job(... LIMIT 1)`；API 回归证明不调用 `list_jobs` | PASS_ENGINEERING |
| T63-A03 | 20 版本 active get 可接受 | 30 样本 p95 0.09431s，固定 3 queries/3 rows，两个查询计划均使用索引 | PASS_ENGINEERING |
| T63-A04 | Quality PASS 必须有真实 Provider Artifact | 模型强校验 path、SHA、sessions、calls、tokens、cost；当前拒绝 PASS | PASS_FAIL_CLOSED |
| T63-A05 | 边界超限不得宣称 Quality PASS | 正式 Gate 重算 metrics，并要求 Engineering failures 为空和 blocker 精确匹配 | PASS_FAIL_CLOSED |

要求、evidence code 与唯一测试节点的完整机器映射位于
`tests/golden/interview_quality_v1/t63-performance-acceptance-v1.json`；其 canonical SHA-256 为
`7244ab7c01e08c23173442827f2c694190e839262f3a9fe997f949bbabe1c147`。

## Windows 实测结果

| Operation | 样本 | p50 (s) | p95 (s) | max (s) |
|---|---:|---:|---:|---:|
| prep_plan_generation | 40 | 0.0000590 | 0.0000826 | 0.0002298 |
| plan_revision_write | 8 | 0.0335213 | 0.0348493 | 0.0348493 |
| plan_revision_read | 40 | 0.0310379 | 0.0327181 | 0.0330944 |
| session_start | 24 | 0.0308084 | 0.0329424 | 0.0432049 |
| report_job_repository_commit | 26 | 0.0938205 | 0.1018760 | 0.1032247 |
| artifact_list | 30 | 0.0265009 | 0.0316234 | 0.0343731 |
| artifact_get | 30 | 0.0296611 | 0.0315005 | 0.0321793 |
| artifact_pdf | 30 | 0.0055203 | 0.0060634 | 0.0072940 |
| active_report_get | 90 | 0.0799810 | 0.0943095 | 0.0954268 |

以上时间只代表 Windows 本地确定性或 PostgreSQL 路径。它们没有被冒充为 DeepSeek 的真实
网络 TTFT、完整 Report Provider 延迟或 Ubuntu 数据。

### Active report 1/5/20 历史版本

| 历史版本数 | 样本 | p50 (s) | p95 (s) | max (s) | DB queries | materialized rows |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 30 | 0.0798012 | 0.0946718 | 0.0954268 | 3 | 3 |
| 5 | 30 | 0.0787925 | 0.0939017 | 0.0940078 | 3 | 3 |
| 20 | 30 | 0.0927791 | 0.0943095 | 0.0944830 | 3 | 3 |

读取路径固定执行 Head、latest job 和 active Artifact 三个应用级查询；latest job 使用
`ORDER BY created_at DESC, job_id DESC LIMIT 1`。latest-job 与 Artifact-history 的强制
index-plan 检查均通过，`n_plus_one_detected=false`。

### PostgreSQL 容量

```text
business peak=12 / max=12
telemetry peak=4 / max=4
advisory-lock peak=4 / max=4
checkpointer peak=2 / max=2
simultaneous application connections=22
observed advisory locks=4
acquire timeouts=0
status=ELIGIBLE_FOR_CAPACITY_CANARY
```

容量子运行器被显式绑定到 T63 自动生成并迁移的隔离 runtime/vector prefix；所有生成关系在
`finally` 中清理。该结果是 repository-level capacity evidence，不是 production canary。

## §5.6 Engineering Gate

T37 的冻结 synthetic fixture 提供 Engineering evaluator 覆盖，不能作为真实 Provider Quality
证据。其结果为：

```text
decision output max=30 tokens <=300
follow-up output max=45 tokens <=120
Provider calls per answer max=2 <=2
Provider calls per main question max=4 <=4
Provider calls after second follow-up=0
retry amplification=1.0 <=1.15
decision degradation rate=0 <=0.02
adaptive Decision p95=0.47–0.57s <=3s
adaptive follow-up E2E TTFT p95=0.89–1.09s <= comparable fixed_v1 *1.20
adaptive next-question p95=0.52–0.62s <=3s
SSE recovery p95=0.22–0.32s <=5s
engineering_status=PASS
quality_status=BLOCKED_NOT_RUN_REAL_PROVIDER
```

fixed_v1 不存在 Decision stage，因此没有用 0 构造虚假 fixed Decision baseline。Report
completion 没有同路径 30/30 样本 cohort，准确记录为 `INSUFFICIENT_BASELINE`。

## 自动审查发现与修复

1. active report 最初通过 `list_jobs` 物化完整 job 历史。新增 store 级
   `get_latest_job`，PostgreSQL 使用排序加 `LIMIT 1`，并以 20-job API 回归证明不会退回
   `list_jobs`。
2. 第一次本地容量诊断使用了默认 runtime prefix，导致 `BLOCKED_SCHEMA`。容量子运行器现在在
   临时环境中绑定同一场 T63 隔离 prefix。
3. 初版数据库合同只有查询/行数。最终增加 latest-job 和 Artifact-history 的索引计划证据，且
   Quality/Engineering evaluator 在索引漂移时失败。
4. 初版 Provider evidence 可由布尔值过早表达 PASS。最终 PASS 必须绑定真实文件 path、SHA-256、
   正数 session/call、输入/输出 Token 和费用；model drift 状态禁止携带任何伪造 usage。
5. 初版 Report baseline 合同不足以阻止小样本或不同路径比较。最终要求同路径 comparable cohort、
   measured/baseline 各至少 30 个样本，并执行 `min(120s, baseline*1.20)`。
6. Ubuntu `MEASURED` 现在必须存在对应 Ubuntu 样本；本次只能记录 `NOT_RUN`。
7. 正式 Gate 最初未把请求 run-id 与 Artifact 双向绑定。最终校验安全 run-id，并要求 manifest 和
   performance Artifact 同时匹配请求值。
8. 正式 Gate 要求 clean Git worktree，把最终运行绑定到确定的 HEAD；随后重新解析 Pydantic
   Artifact、重算 metrics、校验配置/授权/场景哈希和五个文件哈希，再执行权威测试节点。

上述发现均已修复并复测，`open_engineering_findings=0`。

## 验证与诊断记录

```text
acceptance builder --check: PASS
T63 contract tests: 15 passed / 0 failed / 0 skipped
expanded PostgreSQL adjacent regression: 109 passed / 0 failed / 0 skipped / 1 warning
official clean-revision gate: 20 passed / 0 failed / 0 skipped / 1 warning
official artifact validation: PASS
git diff --check before implementation commit: PASS
```

已有 Starlette TestClient/httpx 弃用警告与 T63 语义无关，未隐藏或转换为 PASS。

以下诊断运行没有被当作最终通过证据：

- v1：318 样本，但容量子运行器 prefix 错误，Engineering `FAIL`，
  `POSTGRES_CAPACITY_NOT_ELIGIBLE` / `BLOCKED_SCHEMA`。
- v2、v3：Engineering PASS，但在最终 Provider、Report baseline 和索引合同加固之前生成，已废弃。
- v4：最终合同加固后的 dirty-worktree 诊断，验证器通过，但没有绑定实现提交，不作为正式证据。
- `t63-final-windows-20260807-v1`：唯一正式 T63 运行，绑定
  `c7a29f79ce32c2335e3767fa1d034107c952e127`。

## 安全、数据和阶段边界

- 所有输入为 synthetic/public-safe 数据；没有真实候选人数据。
- Provider 调用数为 0，未发送第一次数据请求，未自动替换模型，未制造 Token/费用记录。
- 原始本地运行 Artifact 只保存在被忽略的 `tmp/`，提交证据仅记录必要指标、文件大小和哈希；
  未提交 DSN、session ID、candidate text 或备份数据。
- 未截图、未执行图像任务、未生成浏览器 trace。
- T63 完成状态为 `ENGINEERING_PASS_QUALITY_BLOCKED`；这不等于 Quality PASS，也不等于整个
  Interview Quality V1 Plan 完成。
- 下一任务为 T64；总体 Goal 保持 `active`，无需用户阶段确认或预算 checkpoint。
