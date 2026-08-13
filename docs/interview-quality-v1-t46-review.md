# Interview Quality V1 — T46 自动审查

## 结论

T46 Engineering 为 `PASS`。当逐题结构化评价和后端规则分已经有效，而 Report Coach 的总结文案调用超时、不可用、鉴权失败或返回无效输出时，系统现在会发布安全的 `degraded + scored/partial/unscored` 新 Artifact；不会丢弃已验证分数，也不会伪造未完成的 Provider 分析。

Schema、引用或事实 Gate 失败仍使 job 失败且不发布新 Artifact。已有 active Artifact 时始终保留旧版本；没有 active Artifact 时，API 暴露失败状态并允许原 job 安全重排队。T46 没有调用真实 Provider。

## 实现范围

核心安全模板位于 `app/services/report_degraded.py`：

- 只从已完成且身份一致的 `InterviewFeedback` 组装报告；
- 按冻结的 `review_input_manifest.questions` 顺序闭合逐题记录；
- 拒绝缺失、额外、重复或 question/feedback 身份不一致的记录；
- 用“已确定”“未生成”“证据不足”区分可发布事实、缺失文案和证据边界；
- 保留原有 `score_status`、`coverage_status`、逐题分、维度分和分母；
- `unscored` 时总分、逐题分和维度分继续全部为 `null`；
- 总结与动作仅使用已验证 observation、evidence refs 和确定性模板；
- 在技术附录记录模板版本、失败组件、来源错误码、Provider 分析未完成和分数状态已保留。

`app/services/report_contract.py` 新增从已验证 feedback 直接组装报告的公共入口，避免在降级路径重新请求 Provider。`app/services/report_microbatch.py` 在 Report Coach 文案失败时直接构建安全 degraded 报告。Durable Review Graph 和 Runtime 新增可重放的 `report_degraded_fallback` effect；该 effect 不进行 Provider 调用，其 operation key 由 job、冻结输入 hash 和 Provider attempt 决定。

## T46 真值表

| 场景 | 结果 |
|---|---|
| 逐题结构和分数有效，总结 Provider 失败 | 发布 `degraded + scored` 或 `degraded + partial` |
| observations 有效，action Provider 失败 | 使用 observation/evidence 约束的确定性排序与安全模板 |
| 所有证据不足 | 发布 `degraded + unscored`，所有数字分为 `null` |
| schema、引用或事实 Gate 失败 | job failed，不提交新 Artifact |
| 安全 degraded builder 自身失败 | job failed，不提交新 Artifact |
| 旧 active 存在且最新 job 失败 | 旧 active 始终保留并继续可读 |
| 无旧 active 且初始 job 失败 | 报告 API 返回失败详情，允许重排队并重新进入 processing |

## 自动审查发现和修复

### 1. Durable fallback 依赖 Store 自然返回顺序

首轮实现直接消费 Store 返回的 completed records。自动审查后新增 manifest-order closure：按冻结 manifest 排序并要求 question ID 集合完全相等，同时拒绝重复记录、陈旧额外记录和 feedback 身份不一致。

### 2. 新失败组件曾扩张冻结的 Artifact reason-code 枚举

T06 已冻结 public `GenerationReasonCode`。修复后所有候选人可见文案降级在顶层使用既有 `summary_generation_failed`；精确失败组件和来源错误码保存在技术附录。`action_generation_failed` 只用于可扩展的 limitation 明细，不进入冻结 Artifact 枚举。

### 3. 安全 builder 失败不能转成成功发布

Durable Graph 现在只允许 Provider 文案边界错误进入安全 fallback。builder、schema、引用、事实、持久化、fencing、lease 和未知错误仍为 terminal，不提交 Artifact。

### 4. 无 active Artifact 的失败与重试路径缺少显式回归

新增 API 回归测试：初始 job 失败时返回 `active_artifact=null`、latest job 的失败码；调用 requeue 后原 job 回到 `queued`，随后报告端点返回 processing 状态。

## 安全和真实性边界

- degraded fallback 不调用 Provider；
- 不声称模型完成了未完成的总结或行动分析；
- 不把生成状态与评分状态混为一条轴；
- 不显示假分，不以默认值替代缺失数字；
- 不因 `is_fallback` 绕过 Runtime schema、reference 或 factuality Gate；
- 不修改 T06 冻结的 Artifact 枚举；
- T46 Engineering PASS 不代表 T45 独立人工盲审或后续 Quality Gate 已通过。

## 验证结果

```text
T46 focused + store regression: 111 passed
full report regression with PostgreSQL 16: 404 passed, 0 skipped
Durable Review regression: 57 passed, 0 skipped
final focused regression: 45 passed
frontend Vitest: 20 passed
frontend production build: PASS
compileall app/tests: PASS
diff check: PASS
secret scan: PASS
provider_calls: 0
```

非阻塞警告：FastAPI TestClient 依赖仍发出一条既有的 `StarletteDeprecationWarning`；本任务没有修改该依赖边界。
