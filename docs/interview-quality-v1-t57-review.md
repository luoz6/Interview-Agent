# Interview Quality V1 — T57 自动审查

## 结论

T57 Engineering 为 **PASS**，自动审查为 **PASS**；T57 Quality 为 **BLOCKED**，不是 PASS。真实外部 Provider 数据请求数为 **0**，自动模型替换和 fallback 使用数为 **0**。

本阶段建立了版本化初始问题质量数据集、可重复的 fixture/saved/live 三模式评测 runner、冻结 GateConfig 指标计算、严格计划预算 Gate、context/grounding 不回退 Gate、逐请求 Provider 计量与 fail-closed preflight。离线合成回放验证了评测框架、预算和指标算术，但不能替代真实 `deepseek-chat` 质量结果或独立评审。

真实 Provider smoke 在发送第一条 JD/简历前停止。统一授权、dataset hash、GateConfig hash、authorization hash、脱敏、凭据存在性和本地证据持久化均通过；DeepSeek 官方模型列表和官方定价页仍只列出 `deepseek-v4-flash` 与 `deepseek-v4-pro`，不包含授权模型 `deepseek-chat`。授权禁止自动替换，因此状态为 `BLOCKED_MODEL_VERSION_DRIFT`。

## 冻结数据集与版本治理

T04 的 `initial-question-quality-v1.json` 是已冻结的单条 schema fixture。自动审查发现不能把 T57 的 12 组数据原地写入 v1，否则会覆盖旧 hash 和违反数据集目录的“新内容必须新版本”规则。最终实现保留 v1 原文件和原 hash：

```text
initial-question-quality-v1.json
sha256=68287ac738e0749a2810c6d537997ab15278b45b777c2d37646bcf77297c7388
fixture_only=true
```

T57 新增实际 construction set：

```text
initial-question-quality-v2.json
sha256=b8af1283ebe719085009fbe4ad9b9ab04b87216793385ced1409f7d30715d148
fixture_only=false
case_count=12
runs_per_case=2
gate_eligible=false
independent_review=pending
```

数据覆盖：

- 六类场景：backend、frontend、data、platform、general project、system design，各 2 组；
- 三种难度：foundation、intermediate、advanced，各 4 组；
- 四种重点：technical depth、system design、project review、balanced，各 3 组；
- 四种时长：15、30、45、60 分钟，各 3 组；
- train、dev、blind-test 各 4 组；
- 10 组中文、2 组英文；
- 每组均为合成 JD/简历，并带安全的公开技术主题摘要；
- 不含真实候选人身份、未脱敏简历、雇主机密、生产导出、凭据或 Principal Memory。

数据集 builder 是确定性的：连续重建后 v2 文件 SHA-256 不变。T35 follow-up builder 也已更新为保留 v2 manifest 项，重新构建 follow-up 数据不会删除 T57 数据集 hash。

## 评测 runner

新增 `scripts/evaluate_initial_question_quality.py`，支持：

1. `fixture-replay`：构造 24 个安全、确定性、明确标记为 synthetic 的计划，验证指标与预算算术；
2. `saved-replay`：绑定精确 dataset ID/hash，重放本地脱敏输出，不发网络请求；
3. `provider`：在真实数据请求前验证统一授权、模型、数据范围、冻结 hash、官方定价、凭据存在性和证据持久化；请求之间继续验证每次调用的 usage、模型和失败放大。

开发用途不能读取 blind-test；非空 run directory 不能复用；部分 partition、smoke 或显式 case 子集不能取得完整 T57 Quality PASS。saved replay 会按所选 case 过滤尝试，未知、缺失或额外的 `(case_id, run_number)` 均 fail closed。

真实 Provider 输出会先保存在 Git 忽略的本地 run 目录中。每个完整真实尝试要求：

- Provider 和 model identity；
- outbound request count；
- 每次请求都存在 input/output token 计量；
- latency；
- normalized response/plan hash；
- runtime context-budget measurement；
- plan/session snapshot hash；
- 每道问题的独立评审槽位。

待评审问题使用 `reviewer_kind=unassigned`，不会冒充 synthetic reviewer 或 independent reviewer。只要语义评审未完成，语义 Gate 的 sample size 保持为 0 并显式阻塞，不能通过缩小分母得到 PASS。

## 冻结质量指标

runner 复用 `config/interview_quality_v1_gate.json` 的 7 个初始问题质量 Gate，没有降低或静默修改阈值：

| 指标 | Fixture replay | Gate | 状态 |
|---|---:|---:|---|
| JD/简历相关率 | 1.0000（144/144） | >= 0.95 | PASS |
| 配置重点覆盖率 | 1.0000（144/144） | >= 0.90 | PASS |
| 同计划重复题率 | 0.0000（0/144） | <= 0.05 | PASS |
| 难度符合率 | 1.0000（144/144） | >= 0.85 | PASS |
| 单一、清晰、可作答率 | 1.0000（144/144） | >= 0.95 | PASS |
| 参考答案/内部证据泄露 | 0 | == 0 | PASS |
| preview/session plan hash 一致率 | 1.0000（24/24） | == 1.0 | PASS |

以上数字来自 synthetic strong-plan fixture，只证明 runner 会正确计算、拒绝和呈现指标，不证明真实模型达到这些阈值。

额外 T57 检查：

- 24 个计划、144 道问题；
- 每组运行 2 次；
- 计划全部不是纯定义题；
- 每题均在分配的 expected minutes 内可回答；
- 同一 case 两次运行的质量签名稳定；
- 两次运行不存在完全相同 plan；
- 英文回归用例只产生英文 fixture 问题；
- reference/internal marker 泄露为 0。

## 计划预算 Gate

T57 不创建第二套时长算法，直接复用：

```text
interview-plan-duration-budget-v1
main-answer-plus-followups-plus-transitions-v1
```

24 个计划全部满足：

- 问题总数等于配置中的题型数量之和；
- 实际题型计数等于 `question_type_budget`；
- 预计追问数等于 `expected_followup_budget`；
- 单题追问不超过 2；
- 预计时长处于相应 15/30/45/60 profile 的可接受区间；
- warning count 为 0；
- blocking count 为 0。

结果：

```text
exact_plan_budget_pass=24/24
question_count_match_rate=1.0
estimated_duration_fit_rate=1.0
plan_budget_gate=PASS
```

对生成计划而言，任何 budget warning 都会使 T57 预算 Gate FAIL；它不能因为 runtime 仍允许用户手工删题后启动而被降为非阻塞。

## Context 与 grounding Gate

计划生成继续使用 `PLAN_CONTEXT_POLICY`，runner 保存 input candidate count、实际 retained count、estimated input tokens、available input tokens 和 estimator fallback 标记。grounding retention 必须为“全部候选被保留”，不是“至少保留一项”。fixture replay 的结果：

```text
context_budget_no_regression_rate=1.0
grounding_context_retention_rate=1.0
maximum_budget_utilization=0.009125
context_and_grounding_gate=PASS
```

这仍是 synthetic measurement。真实 Provider run 必须保存 runtime measurement；缺失或截断会在下一次请求前停止或使确定性 Gate 失败。

## Provider 计量与 fail-closed 边界

计划 structured-output 路径现在请求 `include_raw=True`，以便在不把 token 字段放进业务 Plan schema 的前提下读取 Provider model 和 usage。旧 adapter/测试替身可回退到不支持 `include_raw` 的调用签名，但真实 Provider runner 要求每次 outbound attempt 都被计量。

`provider_usage` 现在分别记录：

- `provider_attempt_count`；
- `provider_metered_attempt_count`；
- `provider_unmetered_attempt_count`；
- 归一化 input/output/cached/total tokens；
- plan knowledge candidate/retained count。

后一条有 usage 的响应不能掩盖前一条未计量请求。runner 还在 artifact 顶层保存 `outbound_requests_attempted` 和 `outbound_requests_metered`，因此即使第一次数据请求失败、尚未形成规范化 attempt，也不会错误写成 `first_data_request_sent=false`。

synthetic artifact 不能携带 Provider identity、真实调用数或 token；hard-stopped artifact 不能作为 complete replay；完整真实 artifact 的请求数、逐 attempt 调用数和计量数必须一致。

## 真实 Provider preflight

最终 preflight 观测时间：

```text
2026-08-06T13:35:34.901490Z
```

通过项：

- authorization ID/hash：PASS；
- dataset v2 manifest/hash：PASS；
- GateConfig hash：PASS；
- redaction preflight：PASS；
- credential present：PASS，密钥未进入 artifact；
- evidence persistence：PASS；
- environment model 被忽略，显式授权模型仍为 `deepseek-chat`；
- fallback 未启用。

官方 discovery：

```text
authorized_model=deepseek-chat
available_models=deepseek-v4-flash,deepseek-v4-pro
priced_models=deepseek-v4-flash,deepseek-v4-pro
hard_stop=MODEL_VERSION_DRIFT
provider_called=false
first_data_request_sent=false
```

因此没有真实问题质量、token、费用或 latency 数字，也没有 Provider Quality PASS。

## 自动审查发现与修复

1. 初版会原地扩写冻结 v1 fixture；最终改为保留 v1 原 hash并新增 v2。
2. structured plan 只发布 parsed schema 时可能丢失 usage/model metadata；现在优先保留 raw wrapper 并单独发布安全计量。
3. 后一次有 usage 的响应可能掩盖前一次未计量请求；现在分别累计 metered/unmetered attempts，并要求完整真实 run 全量计量。
4. 待评审样本可能被误写为“样本不足”；现在明确区分 `BLOCKED_PENDING_INDEPENDENT_REVIEW`。
5. 待评审问题原先可能带 synthetic reviewer 标签；现在 pending 只能使用 `unassigned`。
6. 一组英文 regression fixture 仍生成中文；现在所有英文 case 的 fixture 问题均为 ASCII 英文。
7. grounding retention 原先只要求至少保留一个候选；现在要求 retained count 精确等于 candidate count。
8. saved replay 原先可能带入未选择 partition 的 attempts；现在按选定 case 过滤并校验精确覆盖。
9. subset/smoke 运行理论上可能在样本足够时得到 Quality PASS；现在只有完整冻结数据集可取得完整 T57 Quality 状态。
10. 第一次真实请求失败且未形成 attempt 时，manifest 可能错误报告未发请求；现在 artifact 顶层独立记录 attempted/metered outbound requests。
11. T35 follow-up builder 的 manifest allowlist 原先不知道 initial v2；现在重建 follow-up 数据时会保留 v2。
12. 运行目录复用会混合旧证据；runner 在非空目录存在时拒绝执行。

## 验证

T57 专用测试：

```text
25 passed, 0 failed
```

扩大邻接回归覆盖 dataset、builder、fixture/saved/provider runner、Provider preflight、authorization、GateConfig、usage、LLM plan structured output、budget、configured generation、context、prep、session serialization 和 follow-up 兼容路径：

```text
430 passed, 0 failed, 1 warning
```

正式全仓回归设置 PostgreSQL DSN 后：

```text
2878 passed, 10 failed, 3 skipped, 1 warning
```

T56 基线是 `2857 passed, 9 failed, 3 skipped`。T57 恰好新增 21 个 passing tests，得到 `2857 + 21 = 2878`；没有 T57 相关失败。九个 T56 基线失败保持不变：旧 perf-counter mock、四类 PostgreSQL cleanup 跨时钟、旧 interview graph 终态期望、publication allowlist、旧 migration 期望和 dependency lock hash 漂移。

本轮额外出现一次 `test_terminal_transitions_are_fenced_against_reclaimed_worker` PostgreSQL lease/fencing 竞态失败；它不在 T57 路径，单独复跑为：

```text
1 passed
```

单独复跑时 PostgreSQL `clock_timestamp()` 比 Python UTC 快 5.274179 秒。正式全仓的 10 个失败仍原样保留，不因单测复跑通过而改写成 9 个。

其他验证：

- `compileall app scripts tests`：PASS；
- `git diff --check`：PASS，仅有 Windows LF/CRLF 提示；
- 数据集连续重建 hash 稳定：PASS；
- 六个数据集 manifest 文件 hash 全部匹配：PASS；
- fixture 与 saved replay 规范化 metrics 完全一致：PASS；
- secret/risk literal scan：PASS_NO_MATCHES；
- Ruff：TOOLING_MISSING；
- Pyflakes：TOOLING_MISSING；
- Black：TOOLING_MISSING；
- 真实 Provider 数据请求：0。

本地详细 run artifacts 位于 `tmp/interview-quality-v1-provider-runs/`，按策略不进入 Git。机器证据见 `docs/interview-quality-v1-t57-evidence.json`。

## 阻塞与后续边界

T57 Quality 的两个真实阻塞：

1. `MODEL_VERSION_DRIFT`：授权的 `deepseek-chat` 不在当前官方模型/定价列表，且 fallback 被禁止；
2. `PENDING_INDEPENDENT_REVIEW`：12 个 case 与真实 Provider 问题均未完成独立评审，`gate_eligible=false`。

这些阻塞不会暂停 T58 及后续不依赖真实 Provider 的 Engineering。统一授权仍有效；阶段、Gate、checkpoint 和提交点不需要重新申请常规授权。Goal 在 T72 真正完成前保持 `active`。

## 回滚

回滚 T57 提交会移除 initial-question-quality-v2、builder、runner、preflight、计划质量/预算/context Gate、逐请求计量增强及相关测试和文档。回滚不得修改 v1 冻结 hash，不得删除统一授权，不得把 v4 模型替换成 `deepseek-chat`，也不得把 synthetic fixture PASS 或 blocked preflight 改写为真实 Provider Quality PASS。
