# Interview-Agent 真人面试模式：Question Intent + JIT 主问题生成 Plan

> 状态：LOCALLY_VALIDATED（M0-M5 已落地并通过本地自动化；M6 测试与授权守卫已就绪，外部执行证据及生产切换未完成）
> 日期：2026-08-21
> 最近验证：2026-09-07
> 修订：v0.9，完整 Browser Suite 重跑为 109 passed、1 skipped、0 failed，并保存逐用例 JSON 报告；全量 Unit/Contract、Architecture/Acceptance 与 Provider 授权合同均已按当前工作树重跑；最终独立复审 P0/P1/P2 均为 0

## 1. 决策

新会话不再把最终主问题写入 Plan。Plan 只冻结考察意图；进入当前题时，运行时结合意图、之前回答和受限上下文生成并持久化实际问句。

```text
Question Intent
+ 前序问答与当前上下文
+ 允许的知识证据
        ↓
主问题生成器
        ↓
校验、持久化、发布
        ↓
当前面试官问句
```

旧 `question.prompt` 只保留给旧 Plan、旧 Session 和旧 Report 的兼容读取，不作为新会话的权威来源。

## 2. 实施基线与剩余缺口

| 当前事实 | 代码位置 | 结论 |
|---|---|---|
| V1/V2 仍保存预生成问句 | `app/services/prep.py`、`app/services/interview_plan_revision.py` | 作为旧会话兼容路径保留，不迁移历史语义 |
| V3 Plan 只保存 `QuestionIntentV1` | `app/domain/interview/question_intent.py`、`app/services/interview_plan_revision.py` | 已实现，V1/V2/V3 兼容回归已通过 |
| V3 首题和下一主问题进入统一 JIT Graph | `app/graphs/durable_interview_state_v3.py`、`app/graphs/durable_interview_graph.py` | 已实现，后台 Bootstrap、Answer、Skip、刷新恢复和 timeout fallback E2E 已通过 |
| Generation Store 保存显式主问题 identity、诊断和结果模式 | `app/services/interview_generation_store.py` | 本地契约已实现并通过独立复审；V30 DDL、双事务锁序和回滚仍需真实 PostgreSQL 验证 |
| Follow-up、评分和 Review 统一读取已发布问句 | `app/services/published_question.py`、`app/services/evaluator.py`、`app/services/round_review.py` | 本地提前结束、报告降级和安全 lineage 回归已通过 |
| 创建 V3 Session 返回 `202 preparing_first_question` | `app/api/interview/routes.py`、`app/services/interview_workflow.py` | 后台 Outbox 必须是启动权威；SSE 只能观察和重放 |
| 前端隐藏未来题正文并消费 reveal 事件 | `frontend/src/pages/InterviewPage.jsx` | Answer、Skip、刷新和提前结束浏览器路径已通过 |

本 Plan 不把“代码已存在”等同于“功能已完成”。只有 M6 的自动化矩阵、真实 PostgreSQL 迁移验证和浏览器恢复测试全部通过后，才允许把 V3 设为生产默认。

### 2.1 历史证据审计（v0.5）

上一轮完整 Browser Suite 已实际执行 105 条用例，结果为 **99 通过、5 失败、1 跳过**。失败并非全部指向 JIT 主流程，必须按契约分类处理：

| 失败 | 证据 | 结论与处理 |
|---|---|---|
| Help 页面分区数量 5→7 | `frontend/src/pages/HelpPage.jsx` 当前公开“我的资料”和“维护者工具”两个新增分区；页面没有 inspector/status-bar | 产品页面已扩展，旧测试数量与标题过时；测试应锁定当前 7 个公开分区及其中文标题，不删除产品内容来迎合旧断言 |
| V2 面试回答后期待 `trade-off` | 当前默认 `PlanConfigurationSnapshot.followup_policy_version=adaptive_v1`；该策略在完整回答后推进下一主问题，实际显示 `Explain Redis cache consistency.` | 不是 V3 回归，也不是 SSE 丢消息；测试错误地把固定 `fixed_v1` 行为当成所有 V2 会话的行为。只有显式绑定 `fixed_v1` 时才断言追问 |
| 降级知识报告期待“没有可公开的知识引用”区块 | `FeedbackCitations` 对空引用安全地不渲染区块；API 的 `feedbacks[].references=[]` 是权威结果 | 不生成空的“引用”占位，测试应断言区块不存在且引用数组为空，避免把缺失证据伪装成可展示内容 |

上述测试契约修正不改变旧 Session 的持久化语义，也不降低 V3 的发布、评分或证据安全边界。相同完整 Browser Suite 已于 2026-09-07 重跑并取得 `109 passed, 1 skipped, 0 failed`；历史失败已关闭，但真实模型跳过项及其他 M6 外部证据仍不得视为完成。

兼容性解释补充：J9 保护的是旧 Revision、旧 Session 的已冻结字段和回放结果，不意味着所有 V1/V2 会话都使用 `fixed_v1`。Follow-up policy 是 Revision/Session binding 的显式配置；回归测试必须使用与被测 Revision 一致的 policy snapshot。

### 2.2 修复后验证证据（2026-09-07）

已完成多轮独立复审并修复全部发现；v0.7 最终复审结论为 P0/P1/P2 均为 0。真实 PostgreSQL 双事务锁序、V3 原子启动和 Checkpointer 重启恢复测试定义已经补齐，但在当前有效授权下实际执行前仍只属于“测试就绪”，不能计为外部验收证据：

- Store 已按 `generation_kind` 强制主问题最多 2 次调用，同时保留 Follow-up 最多 3 次的旧契约。
- `start_attempt`、`start_or_reclaim_attempt`、`complete_attempt` 和失败落账统一使用 Generation → Attempt 锁序；主问题 retry CAS 未命中会抛错并回滚整笔事务。
- Provider 不可用时直接构造原生 Intent fallback，不再先创建 legacy 最终问句再反推 Intent。
- 真人面试、Generation Store、Prep、兼容和连接事务定向矩阵：`334 passed`。
- 非 PostgreSQL Unit/Contract 门禁：`4184 passed, 3 skipped, 17 deselected`（当前选择集 4187 项）；命令为 `pytest -q tests/unit tests/contracts -m "not pg_runtime and not pg_jobs and not pg_control and not langgraph_recovery and not langgraph_review_recovery and not langgraph_dual_canary and not langgraph_fencing_canary and not real_llm"`，deselected 项均由命令显式排除的受保护 PostgreSQL/真实 Provider markers 管理。
- Architecture + Acceptance：`466 passed`；新增正式 API + V3 Graph 内存 Acceptance 不宣称覆盖 PostgreSQL Outbox/Consumer。
- 前端完整 Vitest：`16 files / 196 passed`；ESLint、Production Build 与 Bundle Budget 均通过。
- 真实 `build_durable_interview_graph_v3` 浏览器场景：`5 passed`；完整 LangGraph recovery spec：`19 passed`。
- Provider 授权与 JIT 合同：`86 passed, 4 skipped`；4 个跳过项均为受结构化 receipt/ledger 守卫的真实 Provider 用例。
- 完整 Browser Suite：`109 passed, 1 skipped, 0 failed`；JSON 逐用例报告为 `docs/interview-jit-v3-browser-e2e-20260907.json`，SHA-256 为 `5e0aef433ed0aa10fba3708db94cf1d6778b8cd84985fb9028112289e2a3021a`；唯一跳过的是 `real-model-smoke.spec.js` 的真实模型烟测，不得计入“真实模型已验证”。
- Python compileall 和 `git diff --check` 均通过；后者只有 Git 的 LF/CRLF 提示，没有空白错误。
- 当前证据基线：`master` HEAD `6453d01db3dfeb1bc35c44d3da482318a10d846e`；排除本 Plan 自身后，未提交工作树 128 个变更文件的内容指纹为 `0cfecd98d018f9e38fec2765a1ecb985e1196173b23b5642842edd79ebe3ece6`。

因此 M5 的自动化浏览器退出条件已满足；M6 仍保持未完成，原因是受保护 PostgreSQL V30/双事务并发/V3 恢复集成、真实 Provider timeout/fallback 诊断和生产切换演练尚未取得当前有效执行证据。测试收集、内存 Store 和 Fake Provider 均不得冒充这些外部证据。

### 2.3 当前外部阻塞项

- PostgreSQL 受保护测试缺少当前有效的 `POSTGRES_TEST_APPROVAL_ID`、`POSTGRES_TEST_APPROVAL_RECEIPT_SHA256`、`POSTGRES_TEST_APPROVED_FINGERPRINT`、`POSTGRES_TEST_DATABASE_ALLOWLIST` 和 `POSTGRES_TEST_APPROVAL_EXPIRES_AT`；不得伪造或绕过守卫。
- 真实 Provider 会产生外部调用与费用；没有本轮明确授权时保持 `@real_llm` 跳过，不把 Fake Provider 的成功/timeout 测试写成真实模型证据。
- 真实 PostgreSQL 测试已补齐 V3 Session Shell、冻结 Plan Binding 与 Bootstrap Outbox 同事务提交/回滚，以及 Generation 锁序、逐字段诊断约束和 retry CAS 整体回滚；当前仅完成收集，尚未在获批目标上执行。
- 真实 Checkpointer 重启恢复测试已补齐独立行级授权守卫，只允许 `test_jit_v3_<32hex>` 写入既有 `public.checkpoint_blobs`、`public.checkpoint_writes`、`public.checkpoints`，并要求前后 inventory 与零残留；当前未获该行级授权，尚未执行。
- 真实 Provider 测试已补齐 canonical receipt、显式 authorization scope 与 schema 绑定、三请求 JIT/四请求 Reviewer smoke 原子 ledger、输入/输出 Token 硬上限、逐请求过期检查、完整 V3 Graph success 与 1ms timeout/fallback；当前未获本轮外部调用授权，保持 skip。
- V3 启动重放已修复为稳定返回同一 Session 的 `202` 恢复合同；capability 关闭只阻止新建，不影响已创建 V3 Session 恢复。
- capability 默认切换、Graph/Plan schema 兼容矩阵与回滚演练必须在上述外部证据齐全后执行。

## 3. 范围

### 目标

- 新 Plan 冻结 Intent、题型、难度、考察目标和知识绑定。
- 首题、Skip 后下一题、Follow-up 后下一题均 JIT 生成。
- 问句必须承接候选人真实回答。
- 生成结果可重放、恢复、审计，评分使用已发布问句。
- 未来题目只显示编号、题型和状态，不返回 focus 摘要或正文。
- 非法输出在预算内最多重试一次；超时、限流或 Provider 不可用时直接使用确定性 fallback。

### 不做

- 不取消 Plan Revision、Plan Hash、Session Snapshot 和题目数量上限。
- 不允许运行时新增、删除、重排 Intent。
- 不同时重做 RAG、Memory、Evidence Gate 或 Report Engine。
- 不让未校验 Provider 输出进入消息、评分、Report 或长期记忆。
- 不把系统改成无边界聊天。

### 3.1 权威硬约束

以下约束优先于后文示例，实施中不得通过局部兼容逻辑绕过：

| 编号 | 硬约束 |
|---|---|
| J1 | 新 Plan 只持久化 Intent；最终主问题只能存在于已发布的 RenderedQuestion 和对应 interviewer message 中。 |
| J2 | 新 Session 只能使用 `interview-plan-v3 + langgraph-v3`；不得按字段存在性猜测模式。 |
| J3 | 主问题最多调用 Provider 2 次；单次默认最多 20 秒，整题生成默认硬上限 30 秒。 |
| J4 | 预算耗尽、Provider 超时或 Provider 不可用时发布确定性 fallback；不得让用户无限等待。 |
| J5 | 首题准备是可恢复的异步业务状态；创建 Session 的 HTTP 请求不等待模型完成；后台 Outbox/Worker 是启动权威，SSE 只能观察和重放。 |
| J6 | 第一阶段只允许“完整校验后发布”；未校验的 Provider delta 不得进入公共 SSE、消息或评分。 |
| J7 | UI、Follow-up Decision、Follow-up Generation、评分、Reviewer、Report 和 Replay 只能消费已发布 RenderedQuestion；禁止重新生成或从 Intent 推测原问句；提前结束时只处理已发布题集合。 |
| J8 | `assessment_goals` 只是观察目标，不是评分维度、权重或及格线。 |
| J9 | 旧 V1/V2 Plan、旧 Graph 和旧 Session 固定原语义，不静默迁移、不重写历史问句。 |
| J10 | Generation identity 必须同时绑定 Session、Question、Intent、Context、Knowledge Scope、Prompt identity 和 Generator version；禁止依赖未定义的隐式 Hash 构成。 |
| J11 | Generation Store 是生成生命周期的唯一权威；Graph 只保存引用/投影；RenderedQuestion 仅在校验并提交后创建且不可变。 |

主问题预算新增独立配置，不直接复用当前默认 120 秒的全局模型请求超时：

```text
MAIN_QUESTION_MAX_PROVIDER_INVOCATIONS=2
MAIN_QUESTION_ATTEMPT_TIMEOUT_SECONDS=20
MAIN_QUESTION_TOTAL_TIMEOUT_SECONDS=30
```

校验关系：

- 三个值必须为正数；
- Provider 调用上限固定不超过 2；
- 每次调用实际可用时间为 `min(attempt_timeout, total_remaining)`；
- 达到总预算后不再重试，立即进入 fallback；
- 默认值必须在 M6 的真实模型诊断中验证，允许在不改变契约的前提下下调，但不得放宽到全局 120 秒。

## 4. 新领域契约

### 4.1 `QuestionIntentV1`

新增 `question-intent-v1` 模型，建议位于 `app/domain/interview/question_intent.py`，由 Plan Revision 引用。

```text
question_id: UUID/string
position: 1..10
kind: project | technical | system-design | behavioral
focus: 非空、长度受限
difficulty: foundation | intermediate | advanced
assessment_goals: 1..4 个稳定枚举
expected_minutes: 1..60
expected_followups: 0..2
origin: generated | custom | regenerated
knowledge_binding: 现有 QuestionKnowledgeBinding
```

初始 `assessment_goals`：`ownership`、`implementation_depth`、`failure_mode`、`recovery`、`tradeoff`、`scale`、`reliability`、`observability`、`collaboration`。

字段归属必须明确：`position` 是 Plan 顺序元数据，`expected_minutes` 是计划调度元数据，`expected_followups` 是预算投影，不是运行时 Follow-up 上限。运行时上限仍由 `FollowupPolicySnapshot` 管理。

产品中的“自定义题目”统一称为“自定义考察目标”。`origin=custom` 表示用户编辑了 Intent，不表示保留一条精确问句。

Intent 不保存最终问句、候选答案、模型推理或评分结果。

### 4.2 `RenderedQuestionV1`

新增运行时问句快照，作为 Session 当前题的唯一权威文本：

```text
schema_version: rendered-question-v1
question_id
text
intent_sha256
context_sha256
knowledge_scope_sha256
generator_version
prompt_version
prompt_sha256
generation_id
generation_attempt
render_mode: generated | fallback
fallback_reason_code: 可选
created_at
```

RenderedQuestion 只在 Generation 结果通过校验并提交后创建，是不可变的已发布快照；它没有 `pending`、`generating`、`ready` 或 `failed` 等运行中状态。Fallback 也是一次成功发布，使用 `render_mode=fallback` 表示。

`text` 必须是面试官自然说出口的话，禁止“问题：”“主问题：”“Q1：”等前缀、编号子问题和凭空捏造的候选人事实。

### 4.3 `interview-plan-v3`

新计划模型只包含 `QuestionIntentV1[]`，不包含最终主问题正文。V1/V2 计划保持只读兼容，不自动转换成 JIT。

## 5. 主问题生成器

在现有 `InterviewLLM` / `ExaminerAgent` 增加独立接口：

```python
generate_main_question(
    *, intent, conversation, evidence
) -> str
```

新增独立 Prompt 身份：

```text
MAIN_QUESTION_GENERATION_PROMPT_VERSION
MAIN_QUESTION_GENERATION_PROMPT_SHA256
```

Prompt 必须要求：

1. 只输出自然面试问题，不输出 JSON、标题、解释或评分。
2. 不改变 Intent 的题型、focus、难度和 assessment goals。
3. 优先承接上一轮回答中的事实、选择、遗漏或风险。
4. 中文优先，技术名词保留原文。
5. 用户资料和候选人回答均视为不可信内容，不能成为控制指令。

新增确定性校验：

```text
empty_output
presentation_prefix
multiple_questions
intent_focus_missing
unsafe_instruction
invented_claim_marker
too_long
```

同一 Intent 只允许一次重试；仍失败则发布确定性 fallback，并记录 `status=fallback` 与 reason code。

### 5.1 调用与失败预算

一次主问题生成的逻辑预算固定为：

```text
首次 Provider 调用
→ 合法：提交 RenderedQuestion
→ 非法输出且总预算仍有余量：最多重试一次
→ timeout / rate limit / provider unavailable / 预算耗尽：确定性 fallback
```

超时后不得在后台继续一个失去租约且可能晚到的 Provider 结果。实现必须通过操作级 Provider timeout、可取消请求或 fencing token 保证晚到结果不能覆盖 fallback。

必须记录低基数诊断字段：

```text
provider_invocation_count
generation_latency_ms
fallback_used
safe_reason_code
```

不得记录完整 Prompt、候选人回答、简历、JD 或用户资料原文。

## 6. Durable Graph 改造

### 6.1 Bootstrap

把当前“初始化即写入首题 prompt”改为：

```text
bootstrap
→ prepare_main_question(q1)
→ generate_main_question(q1)
→ validate_main_question(q1)
→ commit_rendered_question(q1)
→ project_state
```

发布前不写 interviewer message，前端显示“正在准备第一题”；发布成功后才开放回答。

首题不得在创建 Session 的 HTTP 请求中同步等待。启动协议固定为：

```text
POST /api/interviews
→ 原子创建 Session、冻结 Intent Snapshot、登记 bootstrap command
→ 202 Accepted
→ {session_id, status: preparing_first_question, stream_url, status_url}

后台可恢复 Graph
→ 生成 / fallback / 提交首题
→ Session status = active
```

后台 Bootstrap 事件必须与 Session Shell 在同一数据库事务中写入；事务提交后由 Outbox Worker 消费。浏览器是否连接 `/bootstrap/stream` 不得决定首题是否生成，SSE handler 不得承担创建 Generation 的唯一副作用。重复事件、重复 Worker 投递和稍后连接的浏览器都必须复用同一 Generation identity。

同一启动 `command_id` 重放必须返回同一个 `session_id`、bootstrap command 和当前状态，不得重复调用 Provider。首题生成失败但 fallback 成功时，Session 正常进入 `active`；只有 Session、Graph、Generation Store 或必要 schema 不可用时才返回可重试基础设施错误。

### 6.2 下一道主问题

把 `commit_next_question` 改为：

```text
Decision = next_question
→ 读取下一 QuestionIntent
→ 构造 bounded conversation context
→ 生成并校验主问题
→ 持久化 RenderedQuestion
→ 写入 interviewer message
→ 更新 current_index
```

Follow-up 结束、回答完成和 Skip 都必须经过同一条主问题生成管线。

上下文复用现有 context budget、source identity、知识 Scope 和 untrusted-content 保护，不新建第二套上下文系统。

### 6.2.1 Decision-aware Main Question Context Projection

主问题生成上下文必须按 Decision 和用户动作确定性投影，不能只依赖未定义的 `bounded conversation`：

| 场景 | 允许的上下文 |
|---|---|
| 首题 | 当前 Intent、JD、简历、已选择知识和安全 Memory；没有上一题回答 |
| 正常回答完成 | 下一 Intent、上一条已发布 `RenderedQuestion.text`、最新 substantive answer、必要的 closed-gap summary |
| Skip / Empty | 下一 Intent 和基础上下文；不得声称候选人刚才提到任何内容 |
| Off-topic | 下一 Intent 和基础上下文；不得把跑题内容提升为候选人事实 |
| repeated_state | 下一 Intent 和基础上下文；不得继续从重复内容构造承接语义 |

该投影仍使用现有 Context Runtime，不新增独立 Service。Follow-up Decision 和 Follow-up Generation 也必须接收当前已发布 `RenderedQuestion.text`。

### 6.3 Durable 状态

在新 Graph Schema 增加：

```text
current_rendered_question_id
question_generation_id
question_generation_attempt
question_generation_outcome
question_generation_reason_code
question_generation_context_sha256
```

`question_generation_outcome` 是 Generation Store 的引用投影，不是第二个状态机。Generation Store 生命周期与现有 Store 契约统一为 `pending | running | completed | failed`；Fallback 提交后为 `completed`，具体降级原因写入 `RenderedQuestion.fallback_reason_code`。Follow-up 和 Main Question 共享 Generation Store 的租约、重试和游标基础设施，但新增明确 `generation_kind`，禁止靠字段猜测类型。

## 7. Generation Store 与 SSE

主问题幂等键：

```text
session_id
+ question_id
+ intent_sha256
+ context_sha256
+ knowledge_scope_sha256
+ prompt_sha256
+ generator_version
```

服务端必须提供一个规范化的 `main_question_generation_identity()`，按固定字段、固定顺序和固定编码计算上述 identity。任何 Prompt 版本或 Knowledge Scope 变化都必须产生新的 Generation；不得把这些字段隐式塞入未定义的 `context_sha256`。

重复启动或刷新：

- `completed`：重放已发布文本；
- `running`：校验 lease/fencing owner 后继续同一 Generation；
- `failed`：按策略重试或 fallback；
- Hash 不同：拒绝静默复用。

新增明确 target 的事件：

```text
status: {stage: main_question_generation, question_id}
question_reveal_reset: {question_id, generation_id}
question_reveal_chunk: {question_id, delta}
question_reveal_done: {question_id, state_version}
```

第一版只发布已通过最终校验的持久化内容。Provider-to-SSE 真流式另行定义 provisional、reset、最终提交和恢复协议，不能直接把未校验 delta 推给用户。

### 7.1 第一阶段事件语义

“校验后发布”意味着 `question_reveal_chunk` 可以把已经提交的安全文本按展示粒度分段发送，但这些 chunk 不是实时 Provider delta。服务端必须先完成：

```text
Provider 完整输出
→ 确定性校验
→ RenderedQuestion 持久化提交
→ 安全 SSE 展示事件
```

因此第一阶段不能对外宣称 Provider 真流式。Provider-to-SSE provisional streaming 作为独立后续里程碑，只有定义以下契约后才能启用：

- provisional 内容绝不进入正式 messages；
- 校验失败时发送 reset 并清除客户端临时文本；
- 最终 commit 事件携带 RenderedQuestion identity；
- 重连能够区分 provisional cursor 与 committed cursor；
- 晚到、重试和 fencing 冲突不会发布两道主问题。

## 8. API、评分与证据

### 8.1 启动响应

新 v3 Session 的创建响应使用 `202 Accepted`：

```json
{
  "session_id": "...",
  "status": "preparing_first_question",
  "stream_url": "/api/interviews/.../bootstrap/stream",
  "status_url": "/api/interviews/..."
}
```

客户端收到 202 后进入面试页面并订阅同一 bootstrap command。刷新页面时通过 `status_url` 和持久化 `active_stream_url` 恢复，不重新启动生成。

Provider 不可用但 fallback 可提交时不返回 503。以下情况才允许返回可重试基础设施错误：

- v3 Graph 未注册；
- Generation Store 或 Workflow Store 不可用；
- 必要 schema 校验失败；
- Session 或 bootstrap command 无法原子持久化。

### 8.2 Session Projection

当前 Session Projection：

```json
{
  "current_question": {
    "id": "q2",
    "kind": "project",
    "focus": "Redis 库存扣减与最终一致性",
    "render_state": "available",
    "prompt": "上一题你提到……如果 Redis 已经扣减成功，但 MQ 投递失败，你会怎么恢复？"
  }
}
```

未来题目只返回 `id`、`kind`、`status=pending`，不返回 `focus`、prompt 或 question_text。Prep 页面可以展示 Intent 的 focus 和 assessment goals；Interview 页面只展示题号、题型和状态。

评分、Follow-up Decision、Follow-up Generation、Reviewer、Report 和 Replay 必须使用：

```text
question_id
rendered_question.text
rendered_question.intent_sha256
candidate_answers
evidence_binding
```

需要使用的 Intent 字段必须通过同一 `intent_sha256` 关联冻结的 Intent Snapshot；不得从当前 Plan 或旧 `question.prompt` 重新推断。

Report Replay 优先读取 RenderedQuestion；旧 Session 没有该字段时回退旧 Plan prompt，不重新调用 LLM。Evidence Trace 增加 Intent Hash、RenderedQuestion Hash 和 generation_id 的安全投影。

V3 的 Report/Reviewer 输入集合定义为“该 Session 中已提交 `RenderedQuestion` 且存在对应 interviewer message 的题目”。用户提前结束时，未来 Intent 不生成问句、不创建评分条目，也不能用 `focus` 拼接占位文本。若 RenderedQuestion 与 message 同时存在但正文不一致，报告构建必须 fail closed；旧 V1/V2 才允许读取冻结的 Plan prompt。

## 9. 前端体验

### 9.1 题目导航

`QuestionNavigator` 只显示未来题的编号、题型和状态，删除 focus 摘要及全文 `title` 属性。当前题显示完整 RenderedQuestion。

### 9.2 生成状态

当前题生成期间显示“正在结合刚才的回答组织问题”，禁用回答输入，不创建空 interviewer message；完成或恢复后再开放输入。

### 9.3 Decision 过渡

使用已有 Decision，不新增 LLM 调用：

| Decision | UI-only 提示 |
|---|---|
| `follow_up` | 我想沿着你刚才提到的这个风险继续追问。 |
| `next_question` + complete | 这部分已经比较完整，我们换一个方向。 |
| `next_question` + off_topic | 这个问题先到这里，我们继续下一部分。 |
| `repeated_state` | 这个点我们先收住，继续下一题。 |

过渡提示默认不写入正式 `messages`，避免污染模型上下文和评分。

## 10. Plan 生成与兼容

- Plan Prompt 改为输出 Intent JSON，不再输出最终问句。
- Plan Quality 检查改为检查目标为空、目标超范围、Intent 重复、focus/kind 不匹配和难度不一致。
- “自定义考察目标”先转换为 Intent，不能绕过 Intent 校验；V3 不承诺保留用户输入的精确问句。
- 新 Plan Revision 使用 `interview-plan-v3`，新 Session 使用 `langgraph-v3`；Plan schema、Graph schema 和 Session mode 必须三者一致。
- 旧 V1/V2 Plan、旧 Session、旧 Report 保持原行为，不被静默迁移。
- Provider 不可用时使用确定性 fallback；v3 Graph、Workflow Store、Generation Store 或必要 schema 不可用时返回可重试错误，不降级为预生成题目。

### 10.1 v3 兼容矩阵

| Plan | Graph | 行为 |
|---|---|---|
| V1/V2 | legacy / langgraph-v1 / langgraph-v2 | 保持旧的预生成问句和历史重放 |
| V1/V2 | langgraph-v3 | 拒绝启动；必须先显式生成新的 V3 Revision |
| V3 | legacy / langgraph-v1 / langgraph-v2 | 拒绝启动；旧 Graph 不得猜测最终问句 |
| V3 | langgraph-v3 | 使用 Intent + JIT 主问题生成 |

旧 Plan 转换必须创建新的 V3 Revision、Plan Hash 和审计记录。转换可以从旧 focus/question_text 提取 Intent 候选，但结果必须经过 Intent 校验和用户预览；不得覆盖旧 Revision。

### 10.2 默认切换规则

开发期间使用显式 `INTERVIEW_JIT_MAIN_QUESTION_ENABLED` capability 保护新建 V3 Plan 和新 Session。该 capability 只控制新建流量，不改变任何旧 Session。

M6 全部验收通过后：

- 新生成 Plan 默认 V3；
- 新 Session 默认 `langgraph-v3`；
- capability 关闭时停止创建新的 V3 Session，但已创建的 V3 Session 仍可恢复和完成；
- 不允许 capability 关闭后把进行中的 V3 Session 路由到旧 Graph。

## 11. 实施里程碑

| 里程碑 | 当前状态 | 退出条件 |
|---|---|---|
| M0 契约冻结 | 已完成 | 本文 v0.9 与代码术语一致 |
| M1 Intent Plan | 已完成（本地） | 完整 V1/V2/V3 Revision 与编辑回归通过 |
| M2 主问题生成器 | 已完成（本地） | Fake Provider timeout、最多两次调用、fallback 与晚到 fencing 测试通过；真实 Provider 证据归 M6 |
| M3 JIT Durable Graph | 已完成（本地） | 无 SSE 后台 Bootstrap、Skip、重放和重复投递测试通过 |
| M4 API / 评分 / Evidence | 已完成（本地），真实 PostgreSQL 待验证 | 提前结束、发布问句一致性和安全 Trace lineage 测试通过；V30 迁移与审计需在受保护 PostgreSQL 上复验 |
| M5 前端真人体验 | 已完成 | Desktop/Mobile 浏览器流程、Skip、后台 Bootstrap、刷新重连和旧兼容契约全部通过；完整 Browser Suite 为 109 passed、1 个真实模型烟测跳过 |
| M6 验收与切换 | 未完成（测试已就绪） | 本地门禁和最终审查已通过；真实 PostgreSQL/Checkpointer、真实模型诊断和生产切换/回滚门禁尚未执行 |
| M7 Provider 真流式 | 不在本次范围 | 独立协议与故障测试完成后另行实施 |

### M0：契约冻结

冻结 Intent、不可变 RenderedQuestion、Generation Store 状态所有权、规范化 Generation identity、Decision-aware Context Projection、Plan v3、Graph v3、202 bootstrap 响应、调用/超时预算、错误码、Prompt Hash 和兼容边界。

### M1：Intent Plan

新增 V3 模型，修改 Provider 输出、Plan 校验、编辑保存和 Prep Projection；Prep 改为 Intent/“自定义考察目标”编辑体验，不再提供 V3 最终问句编辑框。

### M2：主问题生成器

新增 LLM/Examiner 接口、Prompt 身份、输出校验、重试和 fallback。

### M3：JIT Durable Graph

改造异步 bootstrap、next question、skip、follow-up transition，扩展 Generation Store 和校验后发布 SSE；Follow-up Decision/Generation 必须读取当前 RenderedQuestion。

### M4：API / 评分 / Evidence

只公开当前问句；评分、Report、Trace 绑定 RenderedQuestion；旧回放兼容。V3 提前结束只处理已发布题；Trace 仅投影 `intent_sha256`、RenderedQuestion SHA 和 `generation_id` 等安全 lineage，不记录完整 JD、简历、回答或内部 Prompt。

### M5：前端真人体验

隐藏未来问句，接入当前题生成状态、Decision 过渡和恢复状态。

### M6：验收与切换

先完成修正后的契约、单元、集成和浏览器测试；不得用修改生产逻辑来掩盖陈旧断言。然后在用户已批准的本机 PostgreSQL 目标上，仅使用 `test_*` 隔离表执行 V30 migration、Generation identity、恢复、幂等、Outbox 和清理验证；缺少当前有效结构化授权时保持未完成，不绕过守卫。最后用真实 Provider 验证默认 20/30 秒预算、最多 2 次调用、timeout/fallback 和 Provider 诊断；V1/V2 旧数据兼容由 provider-free replay/Report 序列化合同验证，不额外消耗真实 Provider 请求。生产 fencing 和跨 runtime 恢复仍必须取得真实 PostgreSQL 证据。只有这些证据齐全，才可把新会话默认切换到 v3。

M6 的最小证据包必须包含：

1. `tests/architecture`、`tests/acceptance`、Unit/Contract 非 PostgreSQL 门禁结果；
2. 前端 `test`、`check`、`build` 和 `git diff --check` 结果；
3. 修正后的完整 Browser Suite 逐用例报告（包括 202、无 SSE 后台 Bootstrap、committed reveal、Skip、刷新重连、fallback、提前结束和 V1/V2 兼容）；本地报告文件为 `docs/interview-jit-v3-browser-e2e-20260907.json`，唯一 skip 必须明确标记为真实 Provider 用例；
4. 受保护 PostgreSQL V30、V3 原子启动与 Generation 集成结果，以及所有 `test_*` 关系的测试后清理证明；
5. 真实模型 timeout/fallback 诊断结果，包含调用次数、延迟、fallback reason 和晚到结果未覆盖证明；
6. 真实 PostgreSQL Checkpointer 跨 runtime 恢复结果，以及三个公共 checkpoint 表按获批随机 `thread_id` 的前后 inventory 和零残留证明；
7. 最终生产切换前的 capability、Graph、Plan schema 三者兼容矩阵与回滚演练。

### M7：可选的 Provider 真流式

M7 不阻断真人面试模式完成，也不得与 M0～M6 混合实现。只有 provisional/reset/commit/fencing/reconnect 契约及其故障测试全部通过后，才允许把 Provider delta 实时投影到前端。

## 12. 验收矩阵

以下勾选项仅表示已有本地自动化、浏览器或静态门禁证据；未勾选项需要受保护 PostgreSQL、公共 Checkpointer 行级写入、真实 Provider 或生产切换证据。

### Intent / Plan

- [x] 新 Plan 不保存最终主问题文本。
- [x] 每个 Intent 至少有一个 assessment goal。
- [x] Intent 不改变题目数量、顺序和题型预算。
- [x] Plan Hash 和 Revision Replay 稳定。

### Runtime

- [x] 首题和每个下一题都 JIT 生成并持久化。
- [x] 下一题能承接上一题回答。
- [x] 相同规范化 Generation identity 不会重复调用 Provider（内存 Store 与 Fake Provider 已验证；真实 PostgreSQL 仍见下方未勾选项）。
- [x] Prompt Hash 或 Knowledge Scope Hash 任一变化都会创建新的 Generation。
- [x] 非法输出不会进入 interviewer message。
- [x] Provider 失败可以 fallback，浏览器断线可以恢复（真实 Provider 诊断仍未执行）。
- [x] 单题 Provider 调用次数不超过 2（Store/Fake Provider 已验证）。
- [x] 单次 20 秒、整题 30 秒预算可配置、可测量并 fail closed（Fake Provider 已验证）。
- [x] 超时晚到的 Provider 结果不能覆盖已提交 fallback（Fake Provider fencing 已验证）。
- [x] Session 创建请求不等待首题 Provider 调用，返回可恢复的 202 状态。
- [x] 客户端完全不连接 Bootstrap SSE 时，后台 Worker 仍生成或 fallback 首题并把 Session 推进到 active。
- [ ] Session Shell、冻结 Plan binding 与 Bootstrap Outbox event 原子提交；任一写入失败时均不可留下半成品。
- [ ] 重复 Bootstrap event、重复 SSE 连接和跨进程恢复不会重复调用 Provider（前两项本地已通过，跨进程 Checkpointer 待授权验证）。
- [x] Generation Store 是唯一生命周期状态 Owner，RenderedQuestion 只在提交后创建且不可变。

### Conversation

- [x] 未来题目不返回 prompt。
- [x] 当前题未 ready 前不能提交回答。
- [x] Decision 过渡提示来自真实 Decision。
- [x] Follow-up Decision 和 Follow-up Generation 使用当前 `RenderedQuestion.text`，不读取 V3 Plan.prompt。
- [x] 首题、正常回答、Skip、Empty、Off-topic、repeated_state 均符合 Context Projection 契约。
- [x] Follow-up 上限、重复防护和评分边界不回归。

### Scoring / Compatibility

- [x] 评分使用已发布 RenderedQuestion。
- [x] Report Replay 不重新生成问句。
- [x] V3 提前结束只评估已发布题，不从未来 Intent 推测问句。
- [x] RenderedQuestion 与 interviewer message 文本冲突时 fail closed。
- [x] Evidence Trace 安全投影包含 `intent_sha256`、RenderedQuestion SHA 和 `generation_id`。
- [x] 用户资料不进入控制层或评分规则。
- [x] V1/V2 Plan、旧 Session、旧 Report 可继续读取。
- [x] v3 不可用时不会静默回到旧模式。
- [x] V1/V2 + Graph v3 和 V3 + 旧 Graph 均被明确拒绝。
- [x] capability 关闭不影响已创建 V3 Session 的恢复和完成。
- [x] Interview 页面不向客户端暴露未来题 focus；Prep 页面仍可编辑 Intent。
- [x] “自定义考察目标”不会绕过 Intent 校验，也不会在 V3 中生成精确问句兼容分支。

### 必跑测试层级

- [x] `tests/architecture`：Domain 不依赖 Service，V3 不绕过应用边界。
- [x] `tests/unit`：Intent、生成校验、预算、Graph、Report/Reviewer 和 capability 矩阵。
- [x] `tests/contracts`：Session Projection、序列化、V30 schema contract 和 Outbox event。
- [x] `tests/acceptance`：V1/V2 旧语义、V3 创建与完整面试流程。
- [ ] PostgreSQL 受保护集成：V30 migration、Generation identity、恢复、幂等和清理。
- [x] 前端 test/check/build：Prep Intent、Answer、Skip、未来题隐藏和状态恢复。
- [x] 浏览器 E2E：202、无 SSE 后台启动、committed reveal、刷新重连、fallback 和提前结束（`109 passed, 1 skipped, 0 failed`）。

## 13. 风险控制与完成定义

主要风险是每题增加一次模型调用、生成问句偏离 Intent、首题启动阻塞、评分难以重放和未校验文本泄露。控制手段分别是 2 次调用与 30 秒总预算、Generation Store 复用、Intent/输出双重校验、异步 202 bootstrap、RenderedQuestion 持久化身份，以及“校验后发布”的事件策略。

完成定义：新 Plan 只保存 Intent；新 Session 每道主问题进入时生成并持久化；首题通过可恢复的异步 bootstrap 准备；用户能看到系统承接上一轮回答；未来问句不泄露；调用与等待有硬上限；失败、重试、断线可恢复；评分、Report 和 Trace 使用已发布问句；旧数据不被重写。M0～M6 完成即代表真人面试模式可用，M7 真流式属于独立增强。

## 14. 明确不采用的捷径

- 不只加打字机动画。
- 不只隐藏前端文字而继续按旧 prompt 运行。
- 不把 `question.prompt` 改名为 `question.intent` 后继续保存完整问句。
- 不把未持久化、未校验的 Provider delta 直接发送给浏览器。
- 不让模型运行时修改题目数量、评分标准或知识 Scope。
- 不为主问题另建一套 RAG、Memory 或 Report 管线。
