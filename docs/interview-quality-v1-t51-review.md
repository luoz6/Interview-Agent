# Interview Quality V1 — T51 自动审查

## 结论

T51 Engineering 为 **PASS**，自动审查为 **PASS**，Quality 为
**NOT_REQUIRED**，Provider 调用数为 **0**。

目标时长、建议主问题范围、预计追问、主答预算和题间 transition 已收敛为
一个服务端预算模型。Schema v2/editor 的安全题数范围由无条件 3–5 改为
1–10；偏离建议题数或估算时长只产生 warning。删除到一题仍可启动，删除
最后一题被拒绝；增加到十题仍可启动，第十一题被拒绝。每次 revision API
响应都会重新计算完整 `budget_assessment`，前端没有复制估算公式。

T51 没有运行或伪造 Provider 结果。`app/services/llm.py` 中旧的 3–5 Provider
prompt 仍属于 T52 的明确范围；T51 已完成 deterministic fallback、Schema、
editor、API assessment 和 launch safety 的统一预算基础，T52 将把同一预算
接入 Provider prompt、返回题数 enforcement 和 generation context。

## 冻结预算契约

```text
budget_version  = interview-plan-duration-budget-v1
formula_version = main-answer-plus-followups-plus-transitions-v1
canonical_sha256 =
  4f7213f4dd010032c75c61fa6ee7adf9941868912dbbf35f0deb75e4b3ca3b8b

safe_main_question_count = 1..10
followup_minutes_each     = 2
transition_minutes_each   = 1
max_followups_per_question = 2
```

| 目标时长 | 建议主问题 | 可接受估算 |
|---:|---:|---:|
| 15 分钟 | 3–4 | 12–20 分钟 |
| 30 分钟 | 5–6 | 24–36 分钟 |
| 45 分钟 | 7–8 | 36–54 分钟 |
| 60 分钟 | 9–10 | 48–72 分钟 |

公式的唯一实现位于 `app/services/interview_plan_budget.py`：

```text
estimated_minutes =
  sum(question.expected_minutes)
  + sum(question.expected_followups) * 2
  + max(0, question_count - 1) * 1
```

目标时长只是估算，不是自适应追问后的精确时长承诺。预计追问总数用于生成
与估算，不是 runtime quota；每个主问题最多两次追问仍是不可放宽的硬上限。

## Warning / Blocking 真值表

| 条件 | 状态 | 是否允许启动 |
|---|---|---:|
| 题数低于建议范围 | WARNING | 是 |
| 题数高于建议范围但不超过 10 | WARNING | 是 |
| 估算低于可接受区间 | WARNING | 是 |
| 估算高于可接受区间 | WARNING | 是 |
| 手工编辑后题型计数偏离 generation snapshot | WARNING | 是 |
| 手工编辑后预计追问总数偏离 snapshot | WARNING | 是 |
| 无有效主问题 | BLOCKED | 否 |
| 主问题超过 10 | BLOCKED | 否 |

`PlanBudgetAssessment` 会验证 `status`、`launch_allowed`、warning 和 blocking
的一致性，调用方不能构造“有 blocking code 但仍允许启动”的矛盾结果。

## 实现闭环

- `InterviewPlanV2` 接受 1–10 题，拒绝 0 或 11 题；60 分钟明确支持 9–10 题。
- editor 删除和增加边界与 Schema 使用同一组安全常量。
- revision API 在 edit、restore、regenerate 和 prep 返回路径统一附带服务端
  `budget_assessment`；编辑后不使用旧估算缓存。
- legacy 转换按目标时长、预计追问和 transition 确定性分配每题主答分钟；默认
  30 分钟转换的估算精确闭合到 30。
- expected-followup allocator 保证总数匹配、每题 0–2；超过题数乘二时拒绝。
- configured fallback 严格尊重题型预算总数、题型计数、difficulty、focus、
  duration 和连续 `q1..qN` ID；60 分钟可生成 9 或 10 个主问题。
- 无 configuration 的 legacy launch validator 继续保留 3–5 兼容边界；显式
  configuration 使用 1–10，时长/建议范围偏离不阻止启动。
- duration profiles、公式常量和安全范围进入 canonical SHA-256；原地修改而
  不升级版本会 fail closed。

## 自动审查发现与修复

1. **宽松整数转换**：原估算器使用 `int()`，可能把 Boolean/字符串或越界值
   转成看似有效的预算。现改为严格整数和范围校验；Schema 的 position、
   expected_minutes、expected_followups 也拒绝 coercion。
2. **模型绕过风险**：Pydantic `model_copy()` 不重新运行 validator。assessment、
   configured fallback 和 launch validator 现在先 dump 并重新 model_validate；
   非法 duration、空白 ID 或字符串分钟数不能进入可信结果。
3. **预算漂移不可检测**：四个 profile、公式常量和 1–10 安全范围原本没有
   完整漂移闭包。现冻结 canonical hash，任何原地语义修改必须发布新版本。
4. **状态字段可能矛盾**：为 assessment 增加算术与状态不变量，拒绝错误总和、
   重复代码、错误 status 或与 blocking 不一致的 launch_allowed。
5. **旧 API 测试仍固化 3/5 边界**：更新为删除到 1、增加到 10 的成功链，
   并验证最后删除/第 11 题失败且不追加 revision。
6. **前端重复公式风险**：API 返回所有公式输入、常量和结果；静态测试确认
   frontend source 中没有第二份分钟计算常量或乘法公式。

## 验证证据

T51 专用测试共 25 个参数化 case，覆盖四个 profile、canonical hash、完整
算术、strict coercion、legacy 闭合、allocation、1/10/0/11 Schema 边界、
warning/blocking、model-copy 绕过、configured fallback 和前端单一来源。

邻接回归命令覆盖 budget、generation policy、revision、editor、API、PostgreSQL
revision store、prep、prep context、session serialization 和 PostgreSQL session
store：

```text
170 passed, 0 skipped
```

PostgreSQL 容器为 `interview-quality-v1-pg16`，测试 DSN 显式设置为
`postgresql://postgres@127.0.0.1:55432/interview`；没有把 skip 当作 PASS。

前端：

```text
Vitest: 28 passed
Vite production build: PASS
```

其他检查：

```text
compileall app tests: PASS
git diff --check: PASS
secret scan: PASS_ZERO_MATCHES
frontend duplicate formula scan: PASS_ZERO_MATCHES
T51 direct Provider import/call scan: PASS_ZERO_NEW_CALLS
```

全仓宽回归额外运行一次，结果为 `2577 passed, 9 failed, 3 skipped`。隔离复跑
后，4 个 PostgreSQL cleanup 失败全部通过，证明它们来自全套共享数据库/并发
污染；其余 5 个稳定失败位于 T51 未修改的旧计时 mock、旧 graph 终态断言、
历史 publication allowlist、过期 latest-migration 断言和既有 dependency lock
哈希。它们没有被用来证明 T51 PASS；T51 的 acceptance 使用 170 个 0-skip
邻接测试和 25 个专用 case。后续 Phase 6 全仓 Gate 必须单独清理这些历史失败。

## 真实性边界

- T51 Provider 调用为 0；没有外部费用、请求或 token 用量。
- T51 不声称 Provider prompt 已按配置生成不同题量；该项属于 T52。
- T51 不声称 Provider 过少/过多返回已经裁剪或拒绝；该项属于 T52。
- T51 不声称最终用户交互和无障碍 warning UI 已完成；该项属于 T54–T55。
- T51 不把全仓的 3 个 skip、9 个首次失败或 5 个历史稳定失败伪装为 PASS。
- T51 的 Engineering PASS 不改变任何尚未运行的 Provider/Human Quality 状态。

## 回滚

回滚 T51 代码提交即可恢复旧 3–5 Schema/editor 兼容行为。不要删除既有 Plan
Revision、source record 或 session snapshot；回滚不得使启动接口重新调用
Provider，也不得改变 `max_followups_per_question=2` 的 runtime 硬上限。
