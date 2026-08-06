# Interview Quality V1 — T52 自动审查

## 结论

T52 Engineering 为 **PASS**，自动审查为 **PASS**，Quality 为
**NOT_REQUIRED**，真实 Provider 调用数为 **0**。

配置已经贯穿 `/api/prep → KnowledgeAgent → InterviewLLM → Provider result
enforcement → Schema v2 revision → Agent trace`。四种时长、三种难度和四种
focus 共 48 个组合均使用同一配置快照生成、校验、分配时长并写入 revision。
30 分钟新请求默认生成 5 个主问题；60 分钟配置支持 9 或 10 个问题。

T52 没有增加第二套 Plan Schema、editor、revision store 或启动路径。Provider
边界仍返回兼容的 `InterviewPlan`，共享 prepare 服务在 grounding 完成后只转换
一次 V2；API、Agent trace、regeneration 和持久化复用同一个 V2 identity/hash。

## 新 `/api/prep` 配置契约

`PrepRequest` 新增可选的嵌套 `configuration`，字段仍是 T10/T50 已发布的
`PlanConfigurationSnapshot`：

```text
difficulty
target_duration_minutes
focus_preset
question_type_budget
expected_followup_budget
max_followups_per_question=2
generator_version
followup_policy_version
```

省略时采用服务端新生成默认值：

```text
difficulty=intermediate
target_duration_minutes=30
focus_preset=balanced
question_type_budget={project:1, technical:2, system-design:1, behavioral:1}
expected_followup_budget=5
generator_version=plan-generator-v2
followup_policy_version=fixed_v1
```

旧 v1 plan parser 仍保留历史 3 题兼容默认；它不再代表新 `/api/prep` 请求的
默认生成配置。客户端不能伪造 generator 实现：非 `plan-generator-v2` 在
Provider 调用前以 `unsupported_plan_generator_version` 拒绝。

## 配置化 Prompt

configured prompt 明确包含：

- exact main-question count；
- exact per-type `question_type_budget`；
- 展开到实际目标的 `q1..qN` JSON shape；
- target duration 与“estimate, not exact SLA”语义；
- foundation/intermediate/advanced 的内容深度指导；
- technical_depth/system_design/project_review/balanced 的重点指导；
- aggregate expected-followup budget；
- `max_followups_per_question=2` 硬上限。

配置影响问题深度、类型和重点，不进入评分 rubric 或 passing threshold。
Provider 输出中不接受 `prep_context`；Knowledge Agent 仍在本地添加 grounding
和 evidence binding。

## Provider 输出 enforcement

规则已冻结到 ADR：

| 条件 | 结果 |
|---|---|
| 配置题数 0 或 >10 | Provider 前拒绝 |
| expected follow-up > `question_count * 2` | Provider 前拒绝 |
| Provider 题数少于 exact target | `provider_question_count_under_budget` |
| Provider 题数超过 10 | `provider_question_count_above_safe_maximum` |
| `target < N <= 10` 且前 target 题精确匹配题型预算 | deterministic prefix trim |
| 超额 prefix 题型不匹配 | `provider_question_type_budget_mismatch` |
| ID 非唯一/非连续/非 `q1..qN` | `provider_question_sequence_invalid` |
| retained question text 归一化后重复 | `provider_duplicate_question` |
| focus/question text 为空 | Schema 拒绝 |
| V2 position 重复/不连续 | Schema 拒绝 |

enforcement 不重排 Provider 题目、不合成缺失题目、不把一种题型改写成另一种。
结构化输出已成功但预算不合格时，不再为了同一失败发起 raw-JSON Provider
重试，防止 retry amplification。

初始 prep 可以发布完整的 configured deterministic fallback，并把 Agent run
标为 degraded，`fallback_reason` 保留具体 enforcement code。Regeneration 关闭
fallback；Provider timeout 或非法/过少/过多输出不创建 revision，旧 active
revision 保持不变。

## 时长、position 与稳定 ID

accepted Provider plan 进入 V2 时：

- `q1..qN` 只是 Provider 边界的临时 position 标识；
- 每题一次性分配 opaque UUID；
- position 固定为连续 `1..N`；
- difficulty 从 configuration 写入每题，不再硬编码 intermediate；
- T51 allocator 写入 expected minutes/followups；
- budget assessment 校验启动安全和估算结果。

API 顶层兼容预览、nested `legacy_plan` 和 nested V2 `plan` 现在全部从已保存
revision 投影，三处使用同一 UUID，不再让顶层预览泄漏临时 `q1..qN`。绑定后
若 legacy 内容被修改，`prepared_plan_payload_mismatch` 会阻止持久化。

## Agent trace identity

Agent safe metadata 记录：

```text
configuration_sha256
plan_sha256
generator_version
budget_version
target_duration_minutes
provider_question_count
retained_question_count
generation_enforcement_action
```

`plan_sha256` 与 revision store 最终保存值完全相同。Regeneration 会重新执行
enforcement，但复用 prepare 服务已绑定的 V2，不得在 trace 后二次生成 UUID。

trace sanitizer 只允许 64 位小写十六进制进入两个 hash 字段；`not-a-hash`
被拒绝。JD、resume、prompt、Provider raw response、credential 和 candidate data
仍被阻止，增加 hash 字段没有扩大原文数据范围。

## Context budget

Plan generation 现在无条件执行：

1. 输入选择：为 fixed instructions/schema/framing 预留 20%；
2. 分别裁剪 JD、resume 和 safe knowledge candidates；
3. 对结构化 prompt 执行 rendered-prompt enforcement；
4. structured fallback 的 raw-JSON prompt 再次执行 enforcement；
5. Provider 单次 output 上限继续使用 `PLAN_CONTEXT_POLICY.max_output_tokens`。

这条安全路径不再依赖默认关闭的 staged context rollout flag；interview、review
和 report 的 rollout 行为没有改变。10 题、长中英文输入和 100 个大 grounding
candidate 的测试证明最终 prompt 在可用 input token 内，尾部 evidence 被裁剪。

## 自动审查发现与修复

1. **Agent trace hash 与最终 revision 分叉**：原 route 在 trace 之后才随机分配
   UUID。现改为 prepare 内一次绑定，trace 和 store 复用同一 V2。
2. **regeneration 二次分配 UUID**：planner 已绑定 V2 后 regenerator 又转换一次。
   现先 re-enforce，再复用已绑定结果；未绑定的兼容实现才转换一次。
3. **顶层预览仍暴露临时 q ID**：顶层 response 原来来自 Provider legacy plan。
   现由已保存 revision 投影，与 nested plan/legacy_plan identity 一致。
4. **绑定后可变 plan 漂移**：增加 semantic round-trip 校验；题目文本/focus/type/
   prep context 变化会 fail closed，临时 q ID 与 UUID 的预期转换单独处理。
5. **context enforcement 受默认关闭开关影响**：plan generation 改为无条件选择
   和最终 enforcement，structured/raw 两条路径都受控。
6. **客户端可伪造 generator version**：只接受当前部署的
   `plan-generator-v2`，其他值在 Provider 前拒绝。
7. **预算失败可能触发第二次 Provider 调用**：enforcement 移到 structured/raw
   选择之后，预算失败不再降级为 raw 重试。
8. **trace 字符串安全策略丢弃 hash**：加入显式字段 allowlist，同时增加严格
   SHA-256 pattern，避免把任意字符串借 hash 字段写入 trace。
9. **regeneration 可能吞掉具体原因**：`PlanGenerationValidationError.code` 现在
   映射为 `PlanRegenerationFailed.code`，API 可返回真实 under/over/type 错误。

## 测试与证据

T52 专用文件收集 **62 tests**，其中 48 个是：

```text
4 durations × 3 difficulties × 4 focus presets
```

其余覆盖 under/over budget、valid trim、invalid trim、11 题、重复 ID、重复题目、
重复 position、configured fallback、fallback disabled、generator spoof、中文/英文
source、10 题 context、无 raw retry amplification、API persistence、trace hash、
regeneration config/hash 复用和默认配置。

宽邻接回归覆盖 plan policy/budget/revision/editor/API、PostgreSQL revision store、
LLM、grounding/trace、prep/context、Agent Runtime、principal-memory sink boundary、
session serialization 和 PostgreSQL session store：

```text
338 passed, 0 skipped
```

前端：

```text
Vitest: 28 passed
Vite production build: PASS
```

全仓宽回归：

```text
2639 passed, 9 failed, 3 skipped
```

相比 T51 的 `2577 passed, 9 failed, 3 skipped`，通过数恰好增加 62，失败集合
完全相同，没有新增 T52 回归。4 个 PostgreSQL cleanup 测试失败由可测量的跨
时钟偏差导致：测试时 PostgreSQL `clock_timestamp()` 比 Python UTC 快约
4.85 秒，而旧测试只使用 Python `now + 1s` cutoff。其余 5 个仍是 T51 已记录
的旧 perf-counter mock、旧 graph 终态断言、historical publication allowlist、
过期 latest-migration 断言和 dependency lock hash 漂移。全仓失败/skip 没有被
伪装成 PASS，也没有被用作 T52 acceptance；Phase 6 必须单独处理。

其他检查：

```text
compileall app tests: PASS
git diff --check: PASS
app import/default config preflight: PASS
secret literal scan: PASS_ZERO_CREDENTIAL_LITERALS
Provider calls: 0
```

## 真实性与阶段边界

- T52 没有真实 Provider 调用、费用、请求或 token 用量。
- 48 组合证明工程映射和 prompt 差异，不声称真实 Provider 质量达标。
- 初始问题的真实 Provider Quality benchmark 属于 T57；当前不得标记 PASS。
- T53 仍负责配置化 prep→revision→edit→start→session 全链路 hash 矩阵。
- T54–T55 仍负责最终配置 UI、warning 交互、a11y、keyboard 和 E2E。
- T52 不改变评分 rubric、passing threshold 或任何既有 Quality Gate 状态。

## 回滚

回滚 T52 提交可恢复旧 generation prompt 和 `/api/prep` 请求 shape，但不得
删除 T51 budget policy、既有 Plan Revision/source/session、Provider 授权证据
或 opaque ID。回滚不能让 `/api/interviews` 在启动时调用 Provider，也不能
放宽每题最多两次追问、Provider timeout/retry/output/context 安全限制。
