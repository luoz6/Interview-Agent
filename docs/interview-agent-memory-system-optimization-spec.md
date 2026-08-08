# Interview Agent 记忆系统优化规范

## 文档控制

| 字段 | 值 |
|---|---|
| 文档类型 | 工程优化规范 / Reference + Explanation |
| 文档状态 | Draft for Technical Review |
| 版本 | 1.1.2-draft |
| 编写日期 | 2026-07-30 |
| 适用项目 | Interview Agent |
| 目标读者 | 后端工程师、Agent 工程师、数据库工程师、SRE、QA、技术负责人、安全与合规评审人员 |
| 兼容性基线 | legacy、langgraph-v1、langgraph-v2、langgraph-review-v1 |
| 主要关联阶段 | Stage 49 Model-aware Context Budgeting；Stage 50 Durable Context Compression Artifacts |
| 当前生产观察 | NOT_RUN |

### 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0.0-draft | 2026-07-30 | 初始完整 Spec |
| 1.1.0-draft | 2026-07-30 | 明确 Question Memory manifest 和 supersede 语义；调整长期记忆 deployment scope；澄清自然语言忠实性边界；增加配置迁移表；拆分 Knowledge/Retention/Config 工作包；补充多语言预算与前端降级契约 |
| 1.1.1-draft | 2026-07-30 | 消除语义忠实性边界与自动化测试之间的矛盾：可编程违规必须失败，自由语义反例保持 non-authoritative 并禁止进入评分 Evidence |
| 1.1.2-draft | 2026-08-08 | 钉住自适应、任务感知上下文压缩的 27 项规范需求及其唯一验证映射；增加自动化缺失、重复和未引用检查 |

### 变更控制

本规范中的需求使用稳定编号。编号一经进入实现，不应因章节调整而重排。

- `MEM-ARCH-*`：架构和边界要求。
- `MEM-DSP-*`：引擎分流与状态一致性要求。
- `MEM-BUD-*`：上下文预算要求。
- `MEM-SEL-*`：上下文选择要求。
- `MEM-ART-*`：Context Artifact 要求。
- `MEM-SUM-*`：摘要与事实约束要求。
- `MEM-KNW-*`：知识记忆要求。
- `MEM-LCY-*`：生命周期和删除要求。
- `MEM-CFG-*`：配置治理要求。
- `MEM-OBS-*`：可观测性要求。
- `MEM-UX-*`：用户体验和降级展示要求。
- `MEM-SEC-*`：安全与隐私要求。
- `MEM-LTM-*`：跨会话长期记忆要求。
- `MEM-TST-*`：测试与验收要求。
- `MEM-CTX-*`：自适应、任务感知上下文压缩要求。

对本规范的实质性修改必须记录：变更原因、兼容性影响、数据迁移影响、灰度影响和回滚方式。

---

## 1. 执行摘要

Interview Agent 当前已经形成多层记忆体系，而不是单一聊天历史模块。系统能够持久化完整面试事实、恢复 LangGraph 工作流、从 pgvector 检索岗位知识、按 token 预算选择模型上下文，并通过不可变 Context Artifact 跨重试复用语义压缩结果。

现有系统最成熟的部分是持久化和故障恢复，最不成熟的部分是模型可消费的长期语义连续性。默认配置下：

- PostgreSQL 保存完整 JD、简历、面试计划、消息、评审和报告。
- Interview LangGraph rollout 为 `0`。
- Context Budget enforcement 默认关闭。
- Interview 和 Evidence compression 默认关闭。
- Examiner 通常只获得最近四条对话和当前题绑定证据。
- 不存在跨会话候选人个人记忆。

因此，当前系统应定位为：

```text
可靠的单会话事实存储
+ 成熟的工作流恢复
+ 受控的岗位知识检索
+ 试验阶段的语义长期记忆
```

而不应描述为已经完成的长期记忆或持续学习系统。

### 1.1 必须优先解决的正确性问题

在任何 `langgraph-v2` rollout 前，必须解决以下问题：

1. API 层仍以 `workflow_engine == "langgraph-v1"` 判断 durable session，导致 v2 snapshot、answer、stream、skip、finish 进入 legacy 路径。
2. Context selector 使用 `OperationContextPolicy.input_cap_tokens` 分配内容预算，而不是使用模型解析后的 `ContextBudget.available_input_tokens`。
3. Conversation compression 缺少与 Evidence compression 等价的 deterministic fallback。
4. 当前摘要校验能够验证 source anchor、exact excerpt、数字和代码标识符，但不能证明普通自然语言事实的语义忠实性。

### 1.2 优化路线总览

本规范将优化分为四条工作线：

```text
正确性线：统一 durable dispatch → 真实预算 → 故障安全降级
质量线：按需压缩 → 逐题增量记忆 → 更强事实约束
治理线：配置收敛 → 指标 → 数据保留与完整删除
演进线：知识覆盖扩展 → 可选且显式授权的跨会话长期记忆
```

跨会话长期记忆不是第一阶段上线条件。它必须作为独立产品能力设计，不能通过把候选人回答自动写入公共 pgvector 知识库来实现。

---

## 2. 当前系统基线

### 2.1 五层记忆模型

本规范采用五层模型描述当前系统。

| 层 | 权威数据 | 生命周期 | 主要存储 | 主要职责 |
|---|---|---|---|---|
| Session Memory | 面试计划、JD、简历、消息、进度、跳题、评审、报告 | 单次面试及其保留期 | PostgreSQL / 内存实现 | 保存业务事实和用户可见状态 |
| Workflow Memory | graph cursor、command inbox、generation attempt、retry、SSE chunk | 一次 durable workflow 及恢复期 | LangGraph PostgreSQL checkpointer、运行时控制表 | 进程丢失后恢复执行，保证幂等和单写者语义 |
| Knowledge Memory | 技术语料、embedding、版本、manifest、证据绑定 | 跨会话、版本化持久 | pgvector | 提供岗位知识、问题依据和可追溯证据 |
| Context Working Memory | 当前模型调用选中的消息和证据 | 单次 LLM 调用 | 进程内计算 | 控制 token 预算、优先级、截断和最终 prompt |
| Context Artifact Memory | 对话摘要、证据压缩、Prep 压缩 | 跨请求、跨重试、跨进程 | PostgreSQL / 内存实现 | 缓存不可变派生语义制品，避免 replay 重复生成 |

### 2.2 权威性顺序

任何实现和评审都必须遵守以下权威顺序：

```text
原始业务消息和原始文档
    > 绑定的精确证据原文
    > 已完成且可追溯的逐题评审记录
    > Context Artifact 摘要
    > 未落库的模型推断
```

摘要不得覆盖原始消息，模型推断不得自动升级为候选人事实。

### 2.3 Session Memory 基线

当前 Session Memory 具备：

- `state_version` 单调递增。
- `expected_version` 冲突检测。
- `command_id` 幂等。
- 内存和 PostgreSQL 双实现。
- 独立序列化层。
- messages 表按 `session_id + sequence_no` 保序。
- Session、messages、reports、question evaluations 的关系持久化。
- 完整消息用于报告和恢复。

已知限制：

- 内存实现没有 TTL 和容量上限。
- 业务 session 没有统一 retention。
- messages 会随会话增长；当前典型面试规模可接受，但没有显式上限。
- JD、简历和候选人回答属于敏感内容，尚未形成端到端删除链。

### 2.4 Workflow Memory 基线

当前 Workflow Memory 具备：

- graph version 固化。
- PostgreSQL checkpoint。
- command inbox。
- generation attempt 和 fencing。
- SSE chunk 持久化和重放。
- retry timer 和 outbox。
- workflow thread lock。
- projection hash 和业务状态投影。

已知限制：

- HTTP durable dispatch 对 v2 支持不完整。
- checkpoint 仍包含完整 `messages`，与业务消息表重复存储。
- checkpoint 通用 retention 和备份删除传播不在当前实现范围内。

### 2.5 Knowledge Memory 基线

当前 Knowledge Memory 管道包括：

```text
JD + 简历
  → 角色画像
  → 结构化查询
  → pgvector 检索
  → 去重和重排
  → LLM 面试计划
  → Prep Context 证据绑定
  → 运行时 KnowledgeBindingResolver
```

其主要优势是：

- `content_sha256` 和 corpus manifest 完整性校验。
- staged、active、retired 语料生命周期。
- query-hit-evidence 追踪。
- 检索失败时可降级为无知识模式。
- 报告和评审可复用绑定证据。

已知限制：

- 当前语料规模和覆盖范围不足以支撑广泛岗位和负向评估。
- 覆盖标签存在硬编码风险。
- 面试结果不会自动进入公共知识库；这是安全默认，但尚无受控的知识改进流程。

### 2.6 Context Working Memory 基线

当前 Context Working Memory 具备：

- 按 operation 划分的输入和输出预算。
- 模型 context window、protocol reserve、structured output reserve 和 safety margin。
- 对话与证据配额。
- 按 question ID 分组。
- 当前题优先。
- 最新候选人回答强制保留。
- head/tail 截断和 omission marker。
- 最终 rendered prompt measurement。

已知限制：

- selector 不使用 resolved available budget。
- enforcement 默认关闭。
- 多个门控环境变量共同决定行为。
- token estimator 是保守估算，最终 provider usage 可能不同。

### 2.7 Context Artifact Memory 基线

当前 Context Artifact 具备：

- 内容和策略驱动的不可变 identity。
- artifact key 唯一约束。
- claim、lease、heartbeat 和 fencing。
- write-once completion。
- provider/validation failure 状态。
- owner type、owner key、purpose 绑定。
- output digest 和 schema 校验。
- 内存和 PostgreSQL 双实现。
- shadow creation 与 consumption 分离门控。

已知限制：

- Conversation artifact 在门控开启后近似每轮创建，不是真正按需压缩。
- 每轮 source digest 随历史增长，跨轮复用率低。
- Conversation 压缩没有完整 deterministic fallback。
- grounding 校验不能证明自然语言语义等价。
- Interview/Review owner refs 没有统一过期策略。

### 2.8 不属于应用记忆系统的内容

开发工具在用户目录保存的 `MEMORY.md`、语言偏好或 Claude/Codex 工具链记忆，只影响开发者与工具的交互，不属于 Interview Agent 应用运行时，不得计入产品记忆能力或合规边界。

---

## 3. 问题陈述与风险分级

### 3.1 P0：上线阻断问题

#### P0-1：v2 HTTP 分流错误

API 层只识别 `langgraph-v1` 为 durable engine。`langgraph-v2` session 可能通过普通 session store 执行 mutation，造成业务状态和 graph checkpoint 双轨更新。

可能后果：

- snapshot 缺少 durable pending action。
- command inbox 被绕过。
- `expected_version` 和 command idempotency 语义改变。
- generation attempt、SSE replay 和 Context Artifact effect 被绕过。
- checkpoint 状态与业务投影不一致。

#### P0-2：选择预算可超过真实可用预算

当模型真实窗口小于 operation cap 时，selector 仍按 operation cap 选择消息。开启 enforcement 后可能拒绝本可通过进一步裁剪完成的请求；关闭 enforcement 后可能由 provider 拒绝或隐式截断。

#### P0-3：缺少 v2 HTTP 合约测试

底层 v2 graph 和 registry 测试不能替代 API 合约测试。没有测试证明 snapshot、answer、stream、skip、finish 对 v2 使用 durable workflow。

### 3.2 P1：可靠性和语义质量问题

#### P1-1：Conversation compression 失败可阻断追问

Evidence compression 对 busy、provider failure、validation failure 使用 deterministic fallback；Conversation compression 没有同等处理。

#### P1-2：压缩触发条件过宽

只要门控打开且存在旧消息，就可能调用 Context Compressor，即使 deterministic context 没有发生信息丢失。

#### P1-3：非增量压缩造成成本增长

每轮重新压缩完整旧历史，artifact 只在同一 source 的 replay 中复用。随着面试增长，总压缩输入可能接近二次增长。

#### P1-4：摘要和 recent exact window 重叠

摘要 source 使用 `messages[:-2]`，recent exact 使用 `messages[-4:]`，中间两条可能同时出现在摘要和原文中。

#### P1-5：语义 grounding 不充分

当前规则能阻止未知 source digest、伪造 exact excerpt、新数字和部分代码标识符，但普通自然语言虚构事实仍可能通过。

#### P1-6：敏感数据生命周期不完整

业务 session、checkpoint、artifact owner refs、摘要、报告、评审和备份之间没有统一删除协议。

#### P1-7：配置组合难以推断

rollout、runtime、budget shadow、budget enforcement、compression shadow、workflow compression、evidence compression 等开关分散，非法组合主要依赖人工避免。

#### P1-8：Knowledge Coverage 基线会限制追问质量

Question Memory 解决的是候选人历史陈述的连续性，不会自动补足外部技术知识。如果 active corpus coverage manifest 与实际语料不一致，或缺少负向和边界案例，即使记忆召回正确，Examiner 仍可能缺少足够的判别性知识。覆盖标签派生和最小负向/边界语料属于 P1 上线质量条件；大规模扩充 corpus 仍属于 P2。

### 3.3 P2：规模和产品演进问题

- 内存实现无 TTL。
- checkpoint 复制完整消息。
- 知识库覆盖规模有限。
- 缺少长期记忆产品授权和数据模型。
- 缺少跨历史会话的评分校准与离线评测，但不能以直接污染知识库的方式解决。

---

## 4. 优化目标和非目标

### 4.1 目标

- `MEM-ARCH-001`：保持原始业务消息为权威源，任何摘要均为派生制品。
- `MEM-ARCH-002`：保持 legacy、v1、v2 并存，并确保 engine assignment 对 session 不可变。
- `MEM-ARCH-003`：确保压缩是可选优化，失败时不应因普通 provider/validation 问题阻断面试。
- `MEM-ARCH-004`：确保所有模型输入受真实模型预算约束。
- `MEM-ARCH-005`：使旧对话只在确定性选择将丢失有效语义时进入压缩。
- `MEM-ARCH-006`：将长期会话记忆改为逐题、增量、可追溯的记忆单元。
- `MEM-ARCH-007`：建立完整的数据保留、删除、审计和备份传播边界。
- `MEM-ARCH-008`：建立记忆质量、成本、延迟、复用和降级指标。
- `MEM-ARCH-009`：为跨会话长期记忆保留独立、显式授权、可删除的架构边界。

### 4.2 非目标

- 不在本阶段自动训练或微调模型。
- 不把候选人回答、评分或摘要自动写入公共技术知识库。
- 不跨候选人共享个人记忆。
- 不把摘要用作精确评分 Evidence。
- 不承诺外部 LLM 调用 exactly once。
- 不自动将历史 v1 checkpoint 改写为 v2。
- 不在第一阶段实现多层递归摘要、RAPTOR 或通用向量化个人记忆。
- 不立即淘汰 legacy、langgraph-v1 或 langgraph-review-v1。
- 不把开发工具记忆文件纳入产品数据模型。

---

## 5. 设计原则

### 5.1 原始数据权威原则

任何候选人事实、评分证据和报告结论必须能回到原始消息或绑定证据。摘要只用于召回和上下文连续性。

### 5.2 先选择、后压缩原则

压缩不能替代 Context Budget。系统必须先解析真实预算，再执行 deterministic selection，最后判断是否需要压缩。

### 5.3 最近精确、历史压缩原则

当前题和最新候选人回答必须尽可能保持精确。只有已经关闭的历史问题可以进入长期摘要。

### 5.4 派生制品不可变原则

同一 identity 对应唯一 completed artifact。策略、模型、prompt contract 或 source 变化必须产生新 identity，不得修改旧制品。

### 5.5 普通失败可降级、一致性失败关闭原则

- Provider timeout、provider unavailable、payload validation failed、artifact busy：允许 deterministic fallback。
- Parent ownership lost、fencing mismatch、identity conflict、跨 owner ref：必须 fail closed。

### 5.6 隐私最小化原则

Checkpoint、telemetry、日志和管理接口只保存或暴露完成职责所需的最少信息。内容和标识符不得为了调试方便进入聚合指标。

### 5.7 灰度优先原则

任何新记忆消费路径必须依次经过 repository test、shadow measurement、shadow creation、低比例 consumption 和生产观察。

---

## 6. 目标架构

### 6.1 目标调用链

```text
HTTP Command
  ↓
Durable Engine Resolver
  ├─ legacy → InterviewSessionStore
  └─ langgraph-v1/v2 → InterviewWorkflowService
                         ↓
                  Authoritative Session State
                         ↓
                  ContextBudgetResolver
                         ↓
               Deterministic Context Selection
                  ├─ 无语义损失 → Direct Context
                  └─ 有语义损失 → Memory Eligibility
                                        ↓
                              Question Memory Retrieval
                                        ↓
                              Context Artifact Resolve
                               ├─ created/reused
                               └─ recoverable failure
                                      ↓
                           deterministic artifact_fallback
                                        ↓
        Current Exact Messages + Retrieved Summaries + Exact Evidence
                                        ↓
                               RenderedPromptGuard
                                        ↓
                                    Examiner
```

### 6.2 目标写入链

```text
Candidate Answer
  ↓
Command Inbox / Legacy Mutation
  ↓
Append Authoritative Message
  ↓
Close Question?
  ├─ No → 不创建长期 Question Memory
  └─ Yes
       ↓
  Build immutable Question Memory source manifest
       ↓
  Optional Context Artifact compression
       ↓
  Bind owner ref to interview session and question
       ↓
  Persist safe memory index metadata
```

### 6.3 目标读取优先级

一次 follow-up context 的顺序必须稳定：

1. 当前问题 prompt。
2. 当前问题的精确 interviewer/candidate 消息。
3. 最近一个已关闭问题的精确消息，若预算允许。
4. 与当前 focus 或 unresolved topic 相关的历史 Question Memory。
5. 当前题绑定的精确 Evidence。
6. Evidence compression，仅在 Evidence 过大且独立门控允许时使用。

不得简单把摘要全部放在最近消息之前而不做相关性和预算控制。

### 6.4 数据权威和引用关系

```text
Interview Session
  ├─ Messages（权威）
  ├─ Question Evaluations（派生、评分权威之一）
  ├─ Question Memory Refs（派生、非评分）
  └─ Report（派生、用户可见）

Context Artifact
  ├─ Immutable Identity
  ├─ Immutable Payload
  └─ Owner Refs

Knowledge Corpus
  ├─ Versioned Chunks
  ├─ Embeddings
  └─ Evidence Bindings
```

---

## 7. 功能需求

### 7.1 Durable Engine 统一分流

- `MEM-DSP-001`：API 层必须通过单一函数判断 session 是否属于 durable interview engine。
- `MEM-DSP-002`：该函数必须同时识别 `langgraph-v1` 和 `langgraph-v2`。
- `MEM-DSP-003`：`GET session`、`answer`、`answer/stream`、`skip`、`finish` 必须共享同一判断逻辑。
- `MEM-DSP-004`：durable session 不得调用 legacy mutation 方法。
- `MEM-DSP-005`：legacy session 不得进入 command inbox。
- `MEM-DSP-006`：session 创建后 `workflow_engine` 和 `graph_schema_version` 不得改变。
- `MEM-DSP-007`：如果 durable session 缺少已注册 graph version，系统必须返回稳定错误并拒绝 mutation。
- `MEM-DSP-008`：如果 rollout 大于零但 runtime disabled，应用必须在启动 preflight 阶段失败，而不是在第一个请求时失败。
- `MEM-DSP-009`：v2 snapshot 必须返回 durable pending action、active command、active generation 和 stream URL。
- `MEM-DSP-010`：API 合约测试必须覆盖 legacy、v1、v2 三个 engine。

推荐实现：

```python
def is_durable_session_state(state: Mapping[str, Any]) -> bool:
    return is_durable_interview_version(state.get("workflow_engine"))
```

该 helper 应由 API 和 workflow service 共同使用，不得复制字符串判断。

### 7.2 模型真实预算

- `MEM-BUD-001`：任何 context selector 都必须使用 `ContextBudget.available_input_tokens`。
- `MEM-BUD-002`：selector 不得仅使用 operation policy input cap 作为总预算。
- `MEM-BUD-003`：固定系统指令、schema framing 和消息包装必须预留显式预算。
- `MEM-BUD-004`：对话和 Evidence 配额应由真实内容预算派生。
- `MEM-BUD-005`：最终 rendered prompt guard 仍是 provider 调用前的权威防线。
- `MEM-BUD-006`：即使 enforcement 关闭，也必须测量并发布安全的 shadow metadata。
- `MEM-BUD-007`：enforcement 开启时，系统应先执行确定性 shrink；只有无法保留 mandatory floor 时才抛出 `ContextBudgetExceeded`。
- `MEM-BUD-008`：provider actual usage 可用时，应与估算值对比并记录误差分布，但不得记录 prompt 内容。
- `MEM-BUD-009`：未知或代理模型必须显式配置 context window。
- `MEM-BUD-010`：tokenizer family 只有在与部署 provider 验证一致后才能启用。
- `MEM-BUD-011`：预算和截断测试必须覆盖简体中文、英文、中英混合、数字/代码混合四类输入。
- `MEM-BUD-012`：如果 provider 提供 actual usage，估算误差必须按安全语言桶 `zh_hans`、`en`、`mixed`、`other`、`unknown` 聚合，不能假设英文误差可以代表中文。
- `MEM-BUD-013`：语言桶只能作为聚合维度，不得记录原始文本、语言检测样本或可关联 session 的标识。
- `MEM-BUD-014`：无论语言类型如何，mandatory current question 和 latest candidate answer 的保留语义必须一致。

推荐预算计算：

```text
resolved_available_input
  - fixed_prompt_reserve
  - mandatory_current_question_reserve
  = selectable_content_budget

selectable_content_budget
  → conversation budget
  → evidence budget
  → summary budget
```

固定配额 `65/35` 可以作为默认比例，但不是绝对值；当前题 mandatory floor 必须优先于 Evidence 比例。

### 7.3 Context Selection

- `MEM-SEL-001`：消息必须优先按显式 `question_id` 分组。
- `MEM-SEL-002`：无 question ID 的历史消息可使用保守角色配对，但不得猜测跨多轮关系。
- `MEM-SEL-003`：当前题 prompt 和最新候选人回答属于 mandatory content。
- `MEM-SEL-004`：选择器不得原地修改源消息。
- `MEM-SEL-005`：截断必须保留完整 omission marker。
- `MEM-SEL-006`：空内容或只剩 omission marker 的项目应被跳过。
- `MEM-SEL-007`：选择结果必须返回 source、selected、dropped、truncated 统计。
- `MEM-SEL-008`：选择结果必须返回稳定的 compression eligibility reason。
- `MEM-SEL-009`：Evidence 选择必须保留绑定顺序和最大项目数。
- `MEM-SEL-010`：最终 context 不得包含摘要和 exact window 的非必要重复消息。

### 7.4 按需压缩 Eligibility

- `MEM-ART-001`：Context Artifact creation 不能只由全局 gate 和“存在旧消息”决定。
- `MEM-ART-002`：必须先执行 deterministic selection，再判断压缩是否必要。
- `MEM-ART-003`：允许的 eligibility reason 仅包括稳定枚举值。
- `MEM-ART-004`：相同 source、policy、budget 和 focus 必须得出相同 eligibility decision。
- `MEM-ART-005`：短上下文且无 dropped/truncated 内容时不得调用 compressor。
- `MEM-ART-006`：shadow creation 可创建但不得消费 artifact。
- `MEM-ART-007`：没有 eligible source 时 route 必须为 `deterministic`。

建议枚举：

```text
older_complete_turn_would_drop
older_complete_turn_excessively_truncated
unresolved_topic_coverage_loss
evidence_representation_excessive_truncation
prep_section_coverage_loss
review_continuity_would_drop
```

### 7.5 增量 Question Memory

- `MEM-ART-010`：只有已关闭问题才可生成长期 Question Memory。
- `MEM-ART-011`：当前活动问题不得仅以摘要形式提供给 Examiner。
- `MEM-ART-012`：Question Memory source 必须来自原始消息，而不是只来自旧摘要。
- `MEM-ART-013`：Question Memory identity 必须包含 session privacy scope、question ID、规范化 source manifest、policy、model 和 target budget。source manifest 必须由该问题下按 `sequence_no` 排序的全部权威消息构成；每项至少包含 `sequence_no`、`role`、`question_id_sha256` 和 `content_sha256`，不得只保存一个未定义顺序的 digest 集合。
- `MEM-ART-014`：同一问题在 source 不变时必须复用同一 artifact。
- `MEM-ART-015`：问题追加新的候选人回答后必须产生新 identity，不得修改旧 artifact。
- `MEM-ART-016`：session memory index 只保存受限 metadata 和 opaque ref，不保存摘要全文。
- `MEM-ART-017`：检索历史 Question Memory 时必须限制最大 unit 数和总 token。
- `MEM-ART-018`：相关性排序应使用 question focus、skill tags、unresolved topics 和 recency；第一阶段不要求向量检索。
- `MEM-ART-019`：不得递归压缩只有摘要、没有原始 source 的输入。
- `MEM-ART-030`：同一问题产生新 source manifest 后，新 index entry 必须显式 supersede 旧 entry。普通读取只返回最新 `active` 且策略兼容的 entry；旧 entry 和旧 artifact 只用于历史 checkpoint replay、审计和 retention，不得参与新一轮相关性检索。

### 7.6 Artifact 失败和降级

- `MEM-ART-020`：Conversation 和 Evidence coordinator 必须采用一致的 recoverable error 分类。
- `MEM-ART-021`：`ContextArtifactBusy` 必须回退 deterministic context。
- `MEM-ART-022`：`ContextArtifactProviderFailed` 必须回退 deterministic context，除非底层原因是 parent ownership lost。
- `MEM-ART-023`：`ContextArtifactValidationFailed` 必须回退 deterministic context。
- `MEM-ART-024`：fallback route 必须记录为 `artifact_fallback`。
- `MEM-ART-025`：`ContextArtifactLeaseLost` 必须先尝试恢复已完成 artifact；恢复失败后 fail closed 或由 workflow retry。
- `MEM-ART-026`：`ContextArtifactConflict` 不得静默回退，必须 fail closed。
- `MEM-ART-027`：parent generation ownership lost 不得继续 provider 调用或消费 artifact。
- `MEM-ART-028`：fallback 不得改变原始 deterministic context 的内容顺序。
- `MEM-ART-029`：fallback telemetry 只能记录稳定错误码和计数。

### 7.7 摘要事实约束

- `MEM-SUM-001`：每个 summary unit 必须至少引用一个 source digest。
- `MEM-SUM-002`：每个 summary unit 在 production consumption 模式下必须至少包含一个 exact supporting excerpt。
- `MEM-SUM-003`：supporting excerpt 必须是所引用 source 的连续子串。
- `MEM-SUM-004`：summary 中的数字和代码标识符必须存在于所引用 source。
- `MEM-SUM-005`：摘要 schema 必须显式表示否定、比较、时间、因果和不确定性，而不是仅使用自由文本。
- `MEM-SUM-006`：摘要不得引入 source 中不存在的技术、结果、职责或结论。
- `MEM-SUM-007`：本阶段不要求、也不得声称系统能够自动证明自然语言摘要的语义忠实性。所有由 LLM 生成且通过可编程校验的摘要都必须标记为 `non_authoritative`；可编程校验仅覆盖 schema、source anchor、exact excerpt、数字、代码标识符和已明确实现的关系约束。
- `MEM-SUM-008`：Question scoring、报告精确引用和合规审计不得只依赖 summary。
- `MEM-SUM-009`：摘要验证失败必须触发 deterministic fallback。
- `MEM-SUM-010`：自然语言忠实性依赖 exact excerpt 提供机器和人工可检查的证据链。可选 verifier 的输出仍是派生信号，不能替代原始 source 校验，也不能把摘要升级为权威事实。

本阶段的实现边界：

```text
系统能够自动验证：
  schema、anchor、exact excerpt、数字、代码标识符、已编码的 polarity/关系约束

系统不能自动证明：
  自由自然语言摘要与 source 在所有语义上等价

因此：
  通过可编程校验 ≠ 语义已被证明
  accepted summary 始终是 non_authoritative
  exact excerpt 是人工和机器复核的证据链
```

任何实现、UI 或文档不得使用“已验证为事实”“完全忠实”描述 accepted summary。

推荐将自由文本单元扩展为：

```yaml
claim_type: decision | tradeoff | result | skill | unresolved | constraint
summary: string
polarity: positive | negative | uncertain | mixed
source_segment_sha256: [sha256]
supporting_excerpts: [string]
confidence: low | medium | high
```

`confidence` 表示压缩器对表达覆盖的自评或规则评估，不表示候选人事实真实性。

### 7.8 Knowledge Memory

- `MEM-KNW-001`：公共 Knowledge Corpus 只能包含经过策划、审查和版本化的技术知识。
- `MEM-KNW-002`：候选人回答、评分和个人摘要不得自动写入公共 corpus。
- `MEM-KNW-003`：active corpus 必须发布 coverage manifest。
- `MEM-KNW-004`：覆盖标签应由 active manifest 派生，不得只依赖硬编码常量。
- `MEM-KNW-005`：语料必须包含正向、负向、边界和常见误区案例。
- `MEM-KNW-006`：检索必须有离线评测数据集和版本化指标。
- `MEM-KNW-007`：query、hit、evidence binding 和 report citation 必须可追溯。
- `MEM-KNW-008`：向量检索失败必须保持稳定降级，不应阻断面试。
- `MEM-KNW-009`：Evidence digest 或 manifest 不匹配时不得消费对应内容。
- `MEM-KNW-010`：知识改进应通过离线提案、去标识化、人工审查和新 corpus release 完成。

---

## 8. 目标数据模型

### 8.1 QuestionMemoryUnit

建议引入版本化模型：

```python
class QuestionMemoryUnit(BaseModel):
    schema_version: Literal["question-memory-v1"]
    session_scope_sha256: str
    question_id_sha256: str
    question_focus_sha256: str
    source_manifest_sha256: str
    source_message_count: int
    claims: list[QuestionMemoryClaim]
    unresolved_topics: list[QuestionMemoryClaim]
    source_started_at: str | None = None
    source_closed_at: str | None = None
```

要求：

- payload 不保存原始 session ID 或 question ID，只保存 digest。
- owner ref 负责将 artifact 绑定到具体 session 和 purpose。
- `source_message_count` 必须与权威 source 一致。
- 时间字段可选；如保存，必须来自业务状态而不是模型生成。
- `source_manifest_sha256` 的规范输入必须是按消息 `sequence_no` 升序排列的数组：

```json
[
  {
    "sequence_no": 12,
    "role": "interviewer",
    "question_id_sha256": "...",
    "content_sha256": "..."
  },
  {
    "sequence_no": 13,
    "role": "candidate",
    "question_id_sha256": "...",
    "content_sha256": "..."
  }
]
```

- manifest 使用 canonical JSON 计算 SHA-256；数组顺序是 identity 的一部分。
- 如果候选人对同一题追加回答、修正回答或 Examiner 增加追问，manifest 必须变化并生成新 artifact。
- 新 artifact 不覆盖旧 artifact；新 index entry 通过 `supersedes_artifact_ref` 指向旧 entry。
- 正常读取只选取同一 `question_id` 下最新 `active` 且 policy/schema 兼容的 entry。
- 历史 checkpoint replay 可以继续加载其原来引用的旧 artifact，直至对应 retention 到期。

### 8.2 QuestionMemoryClaim

```python
class QuestionMemoryClaim(BaseModel):
    claim_type: Literal[
        "decision",
        "tradeoff",
        "result",
        "skill",
        "constraint",
        "unresolved",
    ]
    summary: str
    polarity: Literal["positive", "negative", "uncertain", "mixed"]
    source_segment_sha256: list[str]
    supporting_excerpts: list[str]
    confidence: Literal["low", "medium", "high"]
```

生产 consumption 模式下，`supporting_excerpts` 不得为空。

### 8.3 SessionMemoryIndex

Session Memory Index 是受限索引，不是摘要 payload：

```python
class SessionMemoryIndexEntry(BaseModel):
    question_id_sha256: str
    focus_sha256: str
    focus_tags: list[str]
    skill_tags: list[str]
    skill_tag_sha256: list[str]
    unresolved_topic_codes: list[str]
    unresolved_topic_sha256: list[str]
    artifact_ref: str
    artifact_sha256: str
    artifact_type: Literal["question_memory"]
    policy_version: str
    source_message_count: int
    source_max_sequence_no: int
    supersedes_artifact_ref: str | None
    created_at: str
```

约束：

- Graph checkpoint 可以保存最多固定数量的 active refs，但不得保存 payload。
- 完整 index 建议存储在业务数据库，并按 session 读取。
- `focus_tags`、`skill_tags` 和 `unresolved_topic_codes` 允许保存明文，但只能来自版本化、有限集合的业务 taxonomy，例如 `distributed_systems`、`idempotency`、`missing_tradeoff`；不得保存候选人原话或自由文本摘要。
- 明文 taxonomy 字段用于确定性相关性匹配，SHA-256 字段用于完整性和 source 关联，两者职责不同。
- 新 entry 创建时必须将同一 question 下旧 `active` entry 原子更新为 `superseded`，并写入 `supersedes_artifact_ref`。
- 读取接口必须忽略 `superseded` 和 `deleted` entry，除非调用方执行历史 checkpoint replay 或受授权审计。

### 8.4 MemoryRoute

统一 route 枚举：

```text
deterministic
artifact_shadow_created
artifact_created
artifact_reused
artifact_fallback
memory_index_retrieved
memory_index_empty
```

### 8.5 MemoryQualityMetadata

允许进入 telemetry 的 metadata：

```yaml
source_message_count: integer
selected_message_count: integer
dropped_message_count: integer
truncated_message_count: integer
memory_unit_count: integer
exact_excerpt_count: integer
estimated_input_tokens: integer
available_input_tokens: integer
budget_utilization_basis_points: integer
route: stable enum
eligibility_reason: stable enum or null
fallback_code: stable enum or null
policy_version: safe version string
```

禁止进入 telemetry：

- session ID、question ID、message ID。
- artifact ref、artifact ID。
- JD、简历、回答、摘要和 excerpt 内容。
- Evidence ID 或 Evidence 内容。
- prompt、provider response、credential 和 DSN。

### 8.6 可选 PrincipalMemoryFact

跨会话长期记忆如进入实现，必须使用独立模型：

```python
class PrincipalMemoryFact(BaseModel):
    schema_version: Literal["principal-memory-fact-v1"]
    deployment_id: str
    principal_id: str
    fact_type: Literal[
        "declared_preference",
        "confirmed_skill",
        "learning_goal",
        "accessibility_preference",
    ]
    normalized_fact: str
    confidence: float
    source_session_id: str
    source_question_id: str | None
    source_excerpt_sha256: str
    user_confirmed: bool
    created_at: str
    expires_at: str | None
    deleted_at: str | None
```

该模型不得与公共 Knowledge Corpus 共表或共 owner scope。

当前路线图以单部署、本地或单租户运行方式为基线，所以 v1 模型使用 `deployment_id + principal_id`，不提前引入 `tenant_id`。如果未来确认多租户产品需求，应通过 `principal-memory-fact-v2` 增加 tenant boundary，并提供显式迁移；不得悄悄改变 v1 字段语义。

---

## 9. API 合约

### 9.1 现有面试 API 的分流要求

下列接口必须根据 session 的 immutable engine assignment 分流：

| API | legacy | langgraph-v1/v2 |
|---|---|---|
| `GET /api/interviews/{session_id}` | `session_store.snapshot` | `workflow_service.snapshot` |
| `POST /api/interviews/{session_id}/answer` | 同步 session mutation | command inbox，返回 `202` |
| `POST /api/interviews/{session_id}/answer/stream` | 当前同步流式兼容路径 | command inbox + durable SSE |
| `POST /api/interviews/{session_id}/skip` | 同步 session mutation | command inbox，返回 `202` |
| `POST /api/interviews/{session_id}/finish` | 同步 session mutation | command inbox，返回 `202` |

API 不能根据当前全局配置推断已有 session 的 engine；必须读取 session 已保存的 assignment。

### 9.2 Snapshot Memory Metadata

可在 session snapshot 中增加受限 metadata：

```json
{
  "memory": {
    "schema_version": "interview-memory-status-v1",
    "context_route": "deterministic",
    "assistance_mode": "full",
    "user_notice_required": false,
    "memory_unit_count": 0,
    "compression_enabled": false,
    "compression_shadow": false,
    "policy_version": "question-memory-policy-v1",
    "last_updated_at": null
  }
}
```

要求：

- `MEM-OBS-001`：普通用户 snapshot 不得返回 artifact ref、artifact ID、source digest 列表或摘要全文。
- `MEM-OBS-002`：`context_route` 表示最近一次已完成 follow-up 的 route，不表示当前正在运行的 effect 已完成。
- `MEM-OBS-003`：legacy session 可以返回 `memory=null` 或稳定的 deterministic 状态，但契约必须固定。
- `MEM-OBS-004`：`assistance_mode` 只允许 `full`、`reduced`、`basic`；它描述 AI 辅助能力，不描述业务消息是否已保存。
- `MEM-OBS-005`：`user_notice_required` 只有在降级会对用户可感知输出质量产生实质影响时才为 true。

### 9.3 删除 API

新增：

```http
DELETE /api/interviews/{session_id}
```

建议响应：

```json
{
  "session_id": "...",
  "deletion_job_id": "...",
  "status": "queued"
}
```

删除应使用异步、可重试、幂等任务，而不是在 HTTP 请求中无界执行所有清理。

要求：

- `MEM-LCY-001`：删除请求必须授权到 session owner 或管理员角色。
- `MEM-LCY-002`：重复删除同一 session 必须返回同一个终态或幂等新任务，不得恢复已删除数据。
- `MEM-LCY-003`：删除开始后必须拒绝新 answer、skip 和 finish 命令。
- `MEM-LCY-004`：删除状态必须可查询。
- `MEM-LCY-005`：删除结果只能返回计数和稳定状态，不返回被删除内容。

建议查询接口：

```http
GET /api/interviews/{session_id}/deletion
```

### 9.4 管理端 Memory Metrics

管理接口只能返回聚合指标，例如：

```json
{
  "window": "1h",
  "routes": {
    "deterministic": 120,
    "artifact_created": 14,
    "artifact_reused": 6,
    "artifact_fallback": 2
  },
  "fallback_codes": {
    "provider_timeout": 1,
    "validation_failed": 1
  },
  "budget": {
    "p50_utilization_basis_points": 4200,
    "p95_utilization_basis_points": 7800
  }
}
```

不得提供按 session、candidate、question 或 artifact 下钻的内容视图，除非另有严格授权和审计规范。

### 9.5 Readiness Contract

现有 readiness 响应应增加 effective memory policy：

```json
{
  "memory_runtime": {
    "budget_mode": "shadow|enforce|disabled",
    "compression_mode": "disabled|shadow|consume",
    "interview_graph_version": "langgraph-v1|langgraph-v2",
    "rollout_percent": 0,
    "artifact_store": "postgres|memory|disabled",
    "configuration_valid": true
  }
}
```

不得返回 provider key、DSN、base URL credential、table 中的敏感标识或 artifact ref。

### 9.6 前端降级体验契约

不是所有内部 fallback 都应打扰候选人。Context Artifact 只是优化层；如果系统完整保存了候选人回答，并用 deterministic context 正常生成追问，候选人界面不应显示“记忆失败”或错误横幅。

- `MEM-UX-001`：`artifact_fallback` 且 Examiner 正常生成追问时，候选人 UI 默认不显示告警；管理端和 telemetry 记录技术 route。
- `MEM-UX-002`：Knowledge retrieval 降级但仍能正常追问时，可将 `assistance_mode` 标为 `reduced`，默认不阻断面试。
- `MEM-UX-003`：业务 follow-up provider 最终使用模板 fallback、追问能力明显下降或报告证据不完整时，`user_notice_required` 必须为 true。
- `MEM-UX-004`：候选人提示必须说明“智能辅助暂时降级，已提交的回答仍已保存，面试可以继续”，不得暗示候选人需要重新提交已成功保存的回答。
- `MEM-UX-005`：技术错误码、artifact 状态、provider 名称、内部重试次数不得显示给候选人。
- `MEM-UX-006`：降级提示必须是非阻塞、可访问的状态消息，并通过现有 `aria-live` 或等价机制通知辅助技术。
- `MEM-UX-007`：恢复到正常模式后不得重复弹出成功提示；状态更新应保持安静。
- `MEM-UX-008`：报告页已有 `full_session_fallback` 等用户可理解的路径说明可以继续使用，但不得暴露内部 artifact ref 或 checkpoint 信息。

推荐用户文案：

```text
智能追问暂时使用基础模式。你已提交的回答仍已保存，可以继续完成面试。
```

管理端可展示更详细但仍不含内容的状态：

```text
assistance_mode=basic
route=artifact_fallback
fallback_code=provider_timeout
```

---

## 10. 数据库设计和迁移

### 10.1 推荐新表：Question Memory Index

建议表名遵循 runtime prefix：

```sql
CREATE TABLE {prefix}_question_memory_refs (
    session_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    source_manifest_sha256 TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    focus_sha256 TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    source_message_count INTEGER NOT NULL CHECK (source_message_count > 0),
    source_max_sequence_no INTEGER NOT NULL CHECK (source_max_sequence_no > 0),
    supersedes_artifact_ref TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'superseded', 'deleted')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    PRIMARY KEY (
        session_id,
        question_id,
        source_manifest_sha256,
        policy_version
    )
);

CREATE UNIQUE INDEX {prefix}_question_memory_active_idx
ON {prefix}_question_memory_refs (
    session_id,
    question_id,
    policy_version
)
WHERE status = 'active';
```

说明：

- 该表是业务索引，不保存摘要 payload。
- `artifact_ref` 仍由 Context Artifact owner ref contract 验证。
- 同一问题的新 source 必须在一个事务中将旧 index 标为 `superseded`，插入新 `active` entry，并通过 `supersedes_artifact_ref` 记录直接前驱；旧 artifact 保持不可变。
- 普通检索必须限定 `status='active'`，并在存在异常多个 active entry 时 fail closed 或选择数据库约束保证的唯一 entry，不能按时间静默猜测。
- 如果 session 表和该表位于同一业务域，可增加外键和 `ON DELETE CASCADE`；如果为了迁移隔离不增加外键，删除服务必须显式清理。

### 10.2 Context Artifact Purpose 扩展

新增或替换 purpose：

```text
interview_question_memory
```

兼容性要求：

- 现有 `interview_conversation_context` 必须继续可读。
- 已创建的 v2 artifact 不做原地 schema rewrite。
- 新 Question Memory 使用新 artifact type 或明确的新 output schema version。
- purpose contract 必须限制 owner type 为 `interview_session`。

### 10.3 Retention 字段

当前只有 prep refs 使用 `retain_until`。优化后应允许受控地为 interview/review refs 设置保留期，或引入 session deletion status 作为清理条件。

要求：

- `MEM-LCY-010`：active session 的 owner refs 不得被定时清理。
- `MEM-LCY-011`：finished session 的 retention 必须由明确策略计算。
- `MEM-LCY-012`：删除请求必须立即使 owner refs 不再可消费。
- `MEM-LCY-013`：unreferenced artifact 在 retention 窗口后才能删除。
- `MEM-LCY-014`：failed artifact 可以使用较短 retention。
- `MEM-LCY-015`：清理必须批量、可重入，并使用 `SKIP LOCKED` 或等价并发策略。

### 10.4 Session Deletion Job

建议新增：

```sql
CREATE TABLE {prefix}_session_deletion_jobs (
    deletion_job_id UUID PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'failed')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    fencing_version BIGINT NOT NULL DEFAULT 0,
    last_error_code TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

删除任务应复用现有 lease、heartbeat 和 fencing 模式，不应另造弱一致性 worker。

### 10.5 Migration 所有权

- `MEM-LCY-020`：所有 DDL 必须由 migration runner 执行。
- `MEM-LCY-021`：运行时 store 构造器在生产模式下必须 `schema_mode="validate"`。
- `MEM-LCY-022`：migration 必须有稳定 ID、checksum 和事务模式。
- `MEM-LCY-023`：已有 workflow engine check constraint 必须包含 legacy、v1、v2。
- `MEM-LCY-024`：迁移不得自动修改已有 checkpoint payload。
- `MEM-LCY-025`：迁移前必须计算连接容量和锁影响。

### 10.6 历史数据处理

历史 session 默认不生成 Question Memory 回填。只有在明确批准的离线任务中才可回填，并必须满足：

- 使用原始业务消息作为 source。
- 使用固定 policy、model revision 和 prompt contract。
- 受 retention 和用户删除状态约束。
- 不处理已进入删除流程的 session。
- 回填结果只能在新消费 gate 开启后使用。
- 回填可随时停止，不影响原 session 和报告。

### 10.7 回滚

回滚优先通过配置停止消费，而不是删除 schema：

```text
compression consume → shadow
shadow → disabled
langgraph-v2 rollout → 0
new sessions → legacy 或 v1
existing v2 sessions → 保持 v2 graph 可执行
```

任何时候都不得删除仍被已有 session 引用的 graph definition、artifact schema 或 runtime table。

---

## 11. 配置治理

### 11.1 目标配置模型

建议将 effective policy 收敛为结构化模型：

```yaml
memory:
  schema_version: memory-runtime-config-v1
  interview_graph:
    runtime_enabled: true
    version: langgraph-v1
    rollout_percent: 0
  model:
    context_window_tokens: 128000
    protocol_reserve_tokens: 512
    structured_output_reserve_tokens: 2048
    safety_margin_tokens: 1024
  budget:
    mode: shadow
    shadow_enabled: true
    followup_policy: followup-context-v2
    review_policy: question-review-context-v2
  compression:
    mode: disabled
    interview_question_memory: false
    evidence: false
    prep: false
    review: false
  selection:
    exact_recent_questions: 1
    max_memory_units: 4
    max_memory_tokens: 2500
    eligibility_utilization_basis_points: 8000
  retention:
    session_days: 90
    report_days: 90
    artifact_unreferenced_hours: 24
    artifact_failed_hours: 24
    checkpoint_days: 30
  artifact:
    lease_seconds: 60
  privacy:
    deployment_id: single-tenant-local
```

环境变量可以作为覆盖入口，但必须先解析成一个 immutable effective config，再供所有组件使用。

建议新增模块：

```text
app/services/memory_config.py
```

其中包含：

```python
class EffectiveMemoryConfig(BaseModel): ...
class StructuredMemoryConfigLoader: ...
class LegacyMemoryEnvironmentAdapter: ...

def load_effective_memory_config() -> EffectiveMemoryConfig: ...
```

除 `memory_config.py` 外，业务组件不得直接读取 Context Memory 和 Context Artifact 相关环境变量。旧 `config.py` getter 在迁移期可以保留，但必须委托给 effective config 或仅供 adapter 使用。

### 11.2 配置模式

```text
budget.mode:
  disabled  仅兼容旧路径，不推荐生产
  shadow    测量但不拒绝
  enforce   测量并执行最终 guard

compression.mode:
  disabled  不创建、不消费
  shadow    可创建、不可消费
  consume   可创建并消费
```

### 11.3 配置约束

- `MEM-CFG-001`：rollout 大于零时，对应 runtime 必须 enabled。
- `MEM-CFG-002`：`langgraph-v2` consumption 必须要求 budget mode 为 `enforce`。
- `MEM-CFG-003`：Evidence consumption 必须要求 Evidence 独立 gate 开启。
- `MEM-CFG-004`：compression consume 必须要求 Context Artifact store 可用。
- `MEM-CFG-005`：未知 model 必须显式配置 context window。
- `MEM-CFG-006`：retention 值必须为正数，并满足 backup 和法律策略。
- `MEM-CFG-007`：非法组合必须在应用启动或 preflight 中失败。
- `MEM-CFG-008`：readiness 必须返回不含秘密的 effective mode。
- `MEM-CFG-009`：运行中不得通过未审计的动态环境变量改变已有 session 的 engine assignment。
- `MEM-CFG-010`：配置版本必须进入安全 telemetry，以便比较灰度阶段。

### 11.4 旧环境变量兼容

第一阶段可继续读取旧变量，但必须通过 `LegacyMemoryEnvironmentAdapter` 映射到统一配置。

冲突规则：

1. 只有新配置存在：使用新配置。
2. 只有旧变量存在：adapter 转换为新配置，并记录一次不含值的 deprecation warning。
3. 新旧同时存在且规范化后相同：使用新配置，并记录旧变量即将废弃。
4. 新旧同时存在且值不同：启动失败，不采用“新值静默覆盖旧值”。
5. 任何冲突都不得自动选择更激进的 rollout、enforcement 或 consumption 模式。

迁移映射：

| 旧环境变量 | 新配置路径 | 规范化和冲突行为 |
|---|---|---|
| `INTERVIEW_LANGGRAPH_RUNTIME_ENABLED` | `memory.interview_graph.runtime_enabled` | 布尔值规范化；冲突时启动失败 |
| `INTERVIEW_LANGGRAPH_VERSION` | `memory.interview_graph.version` | 只允许已注册版本；冲突时启动失败 |
| `INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT` | `memory.interview_graph.rollout_percent` | 规范化为 `0..100`；冲突时启动失败 |
| `LLM_CONTEXT_WINDOW_TOKENS` | `memory.model.context_window_tokens` | 正整数；自定义 provider 必填 |
| `LLM_CONTEXT_PROTOCOL_RESERVE_TOKENS` | `memory.model.protocol_reserve_tokens` | 非负整数；冲突时启动失败 |
| `LLM_STRUCTURED_OUTPUT_RESERVE_TOKENS` | `memory.model.structured_output_reserve_tokens` | 非负整数；冲突时启动失败 |
| `LLM_CONTEXT_SAFETY_MARGIN_TOKENS` | `memory.model.safety_margin_tokens` | 非负整数；冲突时启动失败 |
| `CONTEXT_BUDGET_SHADOW_ENABLED` | `memory.budget.shadow_enabled` | 与 `budget.mode` 联合校验，不能产生矛盾状态 |
| `CONTEXT_BUDGET_PREP_ENFORCEMENT` | `memory.budget.enforcement.prep` | 布尔值；统一转为 operation policy |
| `CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT` | `memory.budget.enforcement.interview` | `compression.mode=consume` 时必须为 true |
| `CONTEXT_BUDGET_REVIEW_ENFORCEMENT` | `memory.budget.enforcement.review` | 布尔值；冲突时启动失败 |
| `CONTEXT_BUDGET_REPORT_ROUTING` | `memory.budget.enforcement.report` | 布尔值；不得被误解为 rollout 百分比 |
| `CONTEXT_COMPRESSION_SHADOW_ENABLED` | `memory.compression.mode` | true 映射为 shadow；与 consume 配置冲突时失败 |
| `CONTEXT_COMPRESSION_PREP_ENABLED` | `memory.compression.prep` | workflow 独立布尔 gate |
| `CONTEXT_COMPRESSION_INTERVIEW_ENABLED` | `memory.compression.interview_question_memory` | v2 + budget enforce 前置校验 |
| `CONTEXT_COMPRESSION_EVIDENCE_ENABLED` | `memory.compression.evidence` | 只有 workflow compression 启用时才有效 |
| `CONTEXT_COMPRESSION_REVIEW_ENABLED` | `memory.compression.review` | Review 独立 gate |
| `CONTEXT_ARTIFACT_LEASE_SECONDS` | `memory.artifact.lease_seconds` | 正整数；需满足 heartbeat interval 约束 |
| `CONTEXT_ARTIFACT_UNREFERENCED_RETENTION_HOURS` | `memory.retention.artifact_unreferenced_hours` | 正整数 |
| `CONTEXT_ARTIFACT_FAILED_RETENTION_HOURS` | `memory.retention.artifact_failed_hours` | 正整数 |
| `CONTEXT_ARTIFACT_PREP_REF_RETENTION_HOURS` | `memory.retention.prep_ref_hours` | 正整数 |
| `CONTEXT_ARTIFACT_CLEANUP_BATCH_SIZE` | `memory.retention.cleanup_batch_size` | 正整数，受数据库容量限制 |
| `CONTEXT_ARTIFACT_DEPLOYMENT_SCOPE` | `memory.privacy.deployment_id` | 受信任、非秘密、长度受限的部署标识 |

迁移阶段：

```text
阶段 A：引入 loader 和 adapter，现有组件行为不变
阶段 B：所有 runtime consumer 改读 EffectiveMemoryConfig
阶段 C：readiness 输出 effective policy 和 legacy-variable-used 布尔值
阶段 D：文档标记旧变量 deprecated
阶段 E：至少一个兼容发布周期后移除旧变量直接读取
```

---

## 12. 生命周期和完整删除

### 12.1 数据分类

| 数据 | 分类 | 默认建议保留 | 删除要求 |
|---|---|---:|---|
| JD、简历 | 高敏感业务内容 | 90 天或产品政策 | 用户删除必须覆盖 |
| 面试消息 | 高敏感业务内容 | 90 天或产品政策 | 用户删除必须覆盖 |
| 逐题评审、报告 | 高敏感派生内容 | 与 session 一致 | 用户删除必须覆盖 |
| LangGraph checkpoint | 高敏感执行副本 | 活动期 + 30 天 | 删除 session 时覆盖 |
| Context Artifact payload | 高敏感派生摘要 | owner retention + orphan window | 删除 owner refs 后清理 |
| Runtime signal | 低敏感聚合数据 | 7～30 天 | 不含内容和标识符 |
| Knowledge Corpus | 公共或内部技术知识 | 版本政策 | 不受候选人删除影响 |

实际保留期限必须由产品、法律和部署地区确认；表中数值只作为工程默认建议。

### 12.2 删除状态机

```text
requested
  ↓
queued
  ↓
running
  ├─ revoke_session_access
  ├─ delete_checkpoints
  ├─ delete_workflow_rows
  ├─ delete_generation_chunks
  ├─ delete_runtime_receipts/outbox where scoped
  ├─ delete_context_artifact_owner_refs
  ├─ delete_question_memory_index
  ├─ delete_business_session and cascades
  └─ schedule_orphan_artifact_cleanup
  ↓
completed | failed_retryable | failed_terminal
```

### 12.3 删除顺序要求

- `MEM-LCY-030`：首先将 session 标记为 deleting，并拒绝新 mutation。
- `MEM-LCY-031`：删除必须在 workflow thread lock 或等价所有权下执行。
- `MEM-LCY-032`：删除 checkpoint 前必须停止或隔离活动 worker。
- `MEM-LCY-033`：删除 artifact owner refs 必须发生在 orphan artifact cleanup 前。
- `MEM-LCY-034`：业务 session 删除应依赖经过验证的 FK cascade 或显式事务。
- `MEM-LCY-035`：删除完成后，snapshot、report 和 SSE 接口必须返回稳定的不存在或已删除响应。
- `MEM-LCY-036`：删除任务不得在日志中打印 session 内容或 ID；需要关联时使用删除任务的安全内部标识。
- `MEM-LCY-037`：备份中的物理删除可以延迟，但必须有明确到期和恢复后再删除程序。

### 12.4 内存实现 TTL

内存实现必须支持：

- 最大 session 数。
- finished session TTL。
- LRU 或按完成时间淘汰。
- 报告和 question evaluation 同步淘汰。
- 测试中可注入 clock。
- 淘汰只用于 memory runtime；不得改变 PostgreSQL 默认持久语义。

建议接口：

```python
class InMemorySessionRetentionPolicy:
    max_sessions: int
    finished_ttl_seconds: int
    cleanup_batch_size: int
```

---

## 13. 状态机和故障矩阵

### 13.1 Follow-up Context Route 状态机

```text
start
  ↓
resolve_budget
  ├─ configuration_error → reject_without_provider_call
  ↓
deterministic_select
  ├─ fits_without_loss → deterministic
  └─ eligible_for_memory
         ↓
   resolve_question_memory
     ├─ completed artifact → artifact_reused
     ├─ claim acquired → compress_and_validate
     │      ├─ completed → artifact_created
     │      ├─ recoverable failure → artifact_fallback
     │      └─ ownership/conflict → fail_closed
     └─ live busy → artifact_fallback
         ↓
build_final_context
  ↓
rendered_prompt_guard
  ├─ fits → provider_call
  └─ exceeds → deterministic_shrink_or_reject
```

### 13.2 故障处理矩阵

| 故障 | 是否重试 | 是否允许 deterministic fallback | 是否允许继续 provider call | 记录码 |
|---|---:|---:|---:|---|
| Context Artifact live busy | 否或短退避 | 是 | 是，使用 fallback context | `context_artifact_busy` |
| Compressor provider timeout | 由 workflow policy 决定 | 是 | 是，使用 fallback context | `provider_timeout` |
| Compressor provider unavailable | 有界重试 | 是 | 是，使用 fallback context | `context_artifact_provider_failed` |
| Compression payload schema invalid | 否 | 是 | 是，使用 fallback context | `context_artifact_validation_failed` |
| Summary grounding invalid | 否 | 是 | 是，使用 fallback context | `context_artifact_grounding_failed` |
| Artifact claim lease lost | 恢复 completed，否则重试 | 否 | 否，直至所有权确认 | `context_artifact_lease_lost` |
| Parent generation lease lost | 由 LangGraph replay | 否 | 否 | `generation_lease_lost` |
| Artifact identity conflict | 否 | 否 | 否 | `context_artifact_conflict` |
| Artifact ref owner mismatch | 否 | 否 | 否 | `context_artifact_ref_scope_conflict` |
| Context budget exceeded after shrink | 否 | 不适用 | 否 | `context_budget_exceeded` |
| Token estimator unavailable | 配置修复 | 否 | 否 | `context_estimator_unavailable` |
| Knowledge store unavailable | 有界重试 | 是，使用无知识上下文 | 是 | `knowledge_unavailable` |
| Evidence digest mismatch | 否 | 是，丢弃不可信 Evidence | 是 | `knowledge_integrity_failed` |

### 13.3 进程丢失矩阵

| 丢失点 | 恢复要求 |
|---|---|
| claim 前进程丢失 | 不产生 artifact；workflow replay 重新判断 eligibility |
| claim 后 provider 前丢失 | lease 到期后可 reclaim；旧 worker 不得完成 |
| provider 返回后 complete 前丢失 | 可能重复外部 provider 调用；不得宣称 exactly once；新结果必须受 fencing 保护 |
| complete 后 owner ref 前丢失 | replay 必须复用 completed artifact 并创建幂等 owner ref |
| owner ref 后 graph checkpoint 前丢失 | replay 必须验证 ref 和 identity，并复用 artifact |
| generation complete 后 graph checkpoint 前丢失 | generation store 为权威 effect；replay 不得重复提交不同 final text |
| checkpoint 后业务 projection 前丢失 | projection node 必须幂等恢复 |
| purge 中途进程丢失 | deletion job 通过 lease/fencing 从最后可重入步骤继续 |

### 13.4 并发约束

- 一个 session 同一时刻只能有一个 authoritative interview workflow writer。
- 一个 generation attempt 同一时刻只能有一个 fenced owner。
- 一个 artifact identity 同一时刻只能有一个有效 claim owner。
- cleanup 不得删除带 owner ref 的 artifact。
- purge 与 follow-up generation 竞争时，purge 必须等待或撤销 generation ownership；不得让 generation 在删除完成后重新写入数据。

---

## 14. 可观测性

### 14.1 指标目标

记忆可观测性必须回答以下问题：

- 当前流量有多少使用 deterministic、artifact created、reused 或 fallback？
- 为什么触发压缩？
- 压缩是否真正减少了被丢弃的信息？
- selector 的估算与 provider usage 差异多大？
- artifact 复用是否只发生在 replay，还是有跨轮有效复用？
- 压缩给 P50/P95 延迟增加了多少？
- 哪类故障导致 fallback？
- checkpoint 和 artifact 存储是否持续增长？
- purge 是否完整、及时、可重试？

### 14.2 必须指标

- `MEM-OBS-010`：按 workflow 和 route 聚合的请求数。
- `MEM-OBS-011`：按 eligibility reason 聚合的压缩候选数。
- `MEM-OBS-012`：artifact created、reused、fallback 比例。
- `MEM-OBS-013`：artifact claim busy、lease lost、validation failed、provider failed 计数。
- `MEM-OBS-014`：source、selected、dropped、truncated 消息和 Evidence 数量分布。
- `MEM-OBS-015`：estimated input、available input、provider actual input 的聚合分布。
- `MEM-OBS-016`：压缩调用 latency 和业务 LLM latency 分离统计。
- `MEM-OBS-017`：每场面试平均压缩调用数和估算 token 成本。
- `MEM-OBS-018`：checkpoint 平均和 P95 payload size。
- `MEM-OBS-019`：session message count、artifact ref count 和 orphan artifact count。
- `MEM-OBS-020`：删除任务 queued/running/completed/failed 数量和完成时长。

### 14.3 安全 metadata 白名单

Telemetry 必须采用白名单，而不是“先收集再清洗”。允许字段仅限：

- stable operation name。
- stable route、reason 和 error code。
- policy/schema/version。
- numeric count、token、latency、attempt 和 utilization。
- boolean fallback、shadow、enforcement 状态。
- 不可逆且无业务关联用途的聚合 bucket。

以下内容禁止进入 Agent Run metadata、runtime signal、trace、canary artifact 和日志：

- prompt 和 provider response。
- JD、简历、回答、摘要和 exact excerpt。
- session、question、message、Evidence、artifact 标识符。
- source digest 列表和 artifact ref。
- credential、DSN、token 和带 credential 的 URL。

### 14.4 告警

建议告警条件：

| 告警 | 建议条件 | 行动 |
|---|---|---|
| v2 durable dispatch mismatch | 任意 durable session 进入 legacy mutation | 立即停止 rollout |
| Context overflow | provider context length error > 0 | 停止 enforcement 扩容，检查预算 |
| Artifact fallback surge | 15 分钟 fallback > 5% | 降级 compression consume → shadow |
| Validation failure surge | 15 分钟 > 1% | 停止消费新摘要，保留 deterministic |
| Lease lost surge | 超过历史基线 | 检查连接池、worker 停顿和数据库负载 |
| Compression latency | P95 超过业务阈值 | 降低创建比例或停用消费 |
| Purge backlog | 最老 queued 超过 SLA | 扩容或修复删除 worker |
| Orphan growth | 超过 retention 预期 | 检查 owner ref 和 cleanup |

---

## 15. 安全与隐私

### 15.1 部署边界

当前 Context Artifact privacy scope 适合单部署、本地或单租户运行。v1 使用 deployment 和 principal 作为边界；进入多租户产品前，必须再把 tenant 纳入明确的授权边界，而不能把 `deployment_id` 重新解释为 tenant。

- `MEM-SEC-001`：当前不同 deployment 的 artifact identity 和 owner ref 不得互相复用；未来不同 tenant 也必须满足相同约束。
- `MEM-SEC-002`：Interview 和 Review scope 必须绑定 session 所属 deployment；多租户版本必须进一步绑定 tenant。
- `MEM-SEC-003`：Prep 如无 principal，不得创建可跨用户消费的个人化 artifact。
- `MEM-SEC-004`：artifact ref 必须保持 opaque，并在每次加载时验证 owner、purpose、identity 和 digest。
- `MEM-SEC-005`：任何管理接口不得把 opaque ref 当作授权凭证。

### 15.2 内容分类

摘要虽然是派生数据，但可能包含与原始回答同等敏感的信息。因此：

- 摘要必须按高敏感内容管理。
- 摘要不得因为“已压缩”而获得更长 retention。
- 摘要不得进入普通应用日志。
- exact excerpt 与原始消息同级保护。
- source digest 也可能用于关联数据，不应公开。

### 15.3 数据库和传输

- PostgreSQL 连接必须使用部署环境允许的加密配置。
- 生产备份必须加密并受 retention 控制。
- 应用层不得在 exception 中包含 provider prompt 或业务内容。
- 管理操作必须审计，但审计只记录 actor、action、时间、结果和安全内部标识。
- 如部署需要列级加密，应优先覆盖 JD、简历、messages、reports 和 artifact payload。

### 15.4 用户权利

如果产品保存跨会话数据，必须提供：

- 查看已保存个人记忆的能力。
- 删除个人记忆和源 session 的能力。
- 更正错误事实的能力。
- 关闭后续记忆写入的能力。
- 清晰说明记忆用途、保留期和是否用于评分。

---

## 16. 跨会话长期记忆

### 16.1 定位

跨会话长期记忆不是公共 Knowledge Memory 的扩展，而是独立的 principal-scoped product feature。默认必须关闭。

### 16.2 允许保存的事实

第一版只允许低风险、可解释、用户可确认的类型：

- 用户明确声明的面试偏好。
- 用户确认的学习目标。
- 用户确认的无障碍或交互偏好。
- 用户明确授权保存的技能陈述。

默认不允许保存：

- 模型推断的人格、诚信、情绪或就业倾向。
- 未经用户确认的能力评分。
- 敏感属性。
- 招聘决策或淘汰结论。
- 来自其他候选人的信息。

### 16.3 写入协议

- `MEM-LTM-001`：长期记忆写入必须有 principal identity。
- `MEM-LTM-002`：必须记录用户授权版本和时间。
- `MEM-LTM-003`：模型建议的 fact 默认处于 proposed 状态。
- `MEM-LTM-004`：只有用户确认或明确的规则允许后才能 active。
- `MEM-LTM-005`：每个 fact 必须有 source session 和 source excerpt digest。
- `MEM-LTM-006`：fact 必须有过期、撤销和删除状态。
- `MEM-LTM-007`：个人 fact 不得写入公共 pgvector corpus。

### 16.4 读取协议

- `MEM-LTM-010`：v1 读取必须按 deployment 和 principal 隔离；多租户版本必须再按 tenant 隔离。
- `MEM-LTM-011`：每次调用最多读取固定数量和 token 的 fact。
- `MEM-LTM-012`：个人记忆只能辅助体验和问题选择，不得直接决定评分。
- `MEM-LTM-013`：使用长期记忆时，前端或产品政策必须允许用户知道系统正在使用历史偏好。
- `MEM-LTM-014`：过期、撤销、删除或未确认的 fact 不得消费。

### 16.5 持续学习边界

“从面试中学习”应分成两个完全不同的过程：

1. 个人记忆：为同一用户提供连续体验，受授权和删除约束。
2. 系统知识改进：将去标识化的失败案例形成离线提案，经人工审查后发布新 corpus。

系统知识改进流程不得自动把单个候选人的回答当作技术真相。

---

## 17. 测试策略

### 17.1 测试层级

```text
Unit
  → Component
  → PostgreSQL Integration
  → LangGraph Recovery
  → HTTP/SSE Contract
  → Fault Injection
  → Privacy Audit
  → Long-context Quality Evaluation
  → Browser Acceptance
```

### 17.2 必须新增的回归测试

- `MEM-TST-001`：v2 session snapshot 必须调用 workflow service。
- `MEM-TST-002`：v2 answer 必须进入 command inbox，不能调用 legacy submit。
- `MEM-TST-003`：v2 stream、skip、finish 必须走 durable path。
- `MEM-TST-004`：selector 的输出估算不得超过传入的真实 selectable budget。
- `MEM-TST-005`：小 context window 下仍可通过确定性 shrink 保留 mandatory current answer。
- `MEM-TST-006`：无 dropped/truncated 内容时 compressor 调用次数为零。
- `MEM-TST-007`：Conversation provider failure 返回 `artifact_fallback`，且 Examiner 仍收到 deterministic context。
- `MEM-TST-008`：加入普通自然语言虚构反例的 characterization test，证明可编程验证器不会把通过结构校验的自由语义摘要升级为权威事实；该摘要必须保持 `non_authoritative`，且不得进入 scoring Evidence。
- `MEM-TST-009`：每个 production summary unit 缺少 excerpt 时失败。
- `MEM-TST-010`：摘要和 recent exact messages 不重复。

### 17.3 Artifact 测试

- identity canonicalization。
- source manifest 顺序稳定性。
- policy/model/settings 变化产生新 key。
- claim conflict。
- live busy。
- failed reclaim。
- heartbeat false/exception。
- stale fencing complete rejection。
- complete 后 replay reuse。
- complete 后 owner ref 前进程丢失。
- owner/purpose/identity/digest mismatch。
- cleanup 不删除 referenced artifact。
- session purge 后 refs 删除和 orphan cleanup。

### 17.4 LangGraph 故障注入

必须在以下边界注入异常：

- command enqueue 后、graph invoke 前。
- append candidate message 后、checkpoint 前。
- generation attempt 开始后、provider 前。
- provider 返回后、chunk 写入前。
- artifact complete 后、owner ref 前。
- owner ref 后、graph checkpoint 前。
- generation complete 后、projection 前。
- finish 后、report event 前。

每个注入点都必须证明：

- 不产生双写或错序消息。
- 不由 stale worker 覆盖新 owner。
- 可恢复 effect 被复用。
- 不可恢复一致性错误 fail closed。

### 17.5 数据生命周期测试

- active session 不被 retention cleanup 删除。
- finished session 到期后进入删除队列。
- 重复 DELETE 幂等。
- purge 与 answer 并发时 answer 被拒绝或在 purge 前完成。
- purge 删除 checkpoint、workflow、generation、artifact refs、业务 session 和派生数据。
- orphan artifact 在窗口后删除。
- 删除完成后所有读接口返回稳定结果。
- backup 恢复演练后删除队列能够再次应用。

### 17.6 隐私测试

自动扫描以下输出：

- Agent Run ledger。
- runtime signals。
- trace files。
- canary JSON。
- exception messages。
- test artifacts。

不得出现：

- prompt、answer、JD、resume、summary、excerpt。
- session/question/message/evidence/artifact ID 或 ref。
- credential、token、DSN。

### 17.7 长上下文质量评测集

建立固定的合成长面试数据集，至少覆盖：

- 20～50 个对话 turn。
- 早期技术决策在后期被引用。
- 否定关系，例如“没有使用 Redis”。
- 数字结果，例如延迟、吞吐、团队规模。
- 多个相似项目，防止事实串线。
- 候选人修正前述回答。
- 未解决问题在后续追问中召回。
- Evidence 与候选人陈述冲突。
- 纯中文长回答，包括中文标点、全角字符和高汉字密度。
- 纯英文长回答。
- 中英混合回答，包括英文技术名词、代码、数字和中文解释。
- 同一事实在中英文之间切换表达。

评测必须使用确定性 fake provider 验证结构和恢复，另使用受控模型评测语义质量；两者不得混为一项通过条件。

### 17.8 多语言 Token 估算测试

- `MEM-TST-020`：固定字符长度的中文、英文和中英混合文本必须分别测量 estimator 结果，测试不得假设字符数与 token 数具有固定比例。
- `MEM-TST-021`：每个受支持 provider/model 组合必须记录中文、英文和 mixed bucket 的估算误差分布。
- `MEM-TST-022`：任何语言 bucket 的最大低估超过批准阈值时，不得扩大 budget enforcement rollout。
- `MEM-TST-023`：中文 head/tail truncation 必须保持有效 UTF-8、完整 omission marker，并避免切断 Unicode surrogate 或产生替换字符。
- `MEM-TST-024`：中英混合回答中的数字、代码标识符和否定关系必须在 Question Memory 校验中保持。
- `MEM-TST-025`：语言检测失败必须归入 `unknown`，不能阻断业务或把原文写入 telemetry。

### 17.9 前端降级测试

- `MEM-TST-030`：透明 `artifact_fallback` 不显示候选人错误横幅。
- `MEM-TST-031`：模板 follow-up fallback 显示一次非阻塞基础模式提示。
- `MEM-TST-032`：提示明确说明回答已保存，不要求重复提交。
- `MEM-TST-033`：技术错误码和内部 provider 信息不进入候选人 DOM。
- `MEM-TST-034`：降级消息满足键盘、屏幕阅读器和 `aria-live` 合约。
- `MEM-TST-035`：刷新后 snapshot 能恢复正确 `assistance_mode`，但不会重复播报已确认提示。

---

## 18. 质量指标和 SLO

### 18.1 正确性指标

| 指标 | 目标 |
|---|---:|
| durable session 进入正确 engine path | 100% |
| stale writer 成功写入 | 0 |
| artifact owner scope 错误消费 | 0 |
| provider context overflow | 0 |
| 当前题最新回答保留率 | 100% |
| 摘要作为唯一评分证据 | 0 |

### 18.2 语义质量指标

| 指标 | 初始门槛 | 说明 |
|---|---:|---|
| 早期关键事实召回率 | ≥ 95% | 固定长会话评测集 |
| 新事实引入率 | ≤ 0.5% | 任意 source 不支持的事实 |
| 否定关系保持率 | ≥ 99% | 不得将否定改为肯定 |
| 数字保持正确率 | 100% | 已有数字不得改变 |
| exact excerpt coverage | 100% | production consumption unit |
| unresolved topic 召回率 | ≥ 95% | 当前 focus 相关场景 |

上述门槛需要在首轮真实评测后校准，但任何降低都必须经过技术和产品评审。

### 18.3 性能和成本指标

| 指标 | 建议目标 |
|---|---:|
| 不需要压缩的短会话 compressor 调用 | 0 |
| 每个关闭问题的 Question Memory 新建调用 | ≤ 1，重放复用 |
| artifact replay reuse | 100% 对相同 identity |
| 压缩增加的 P95 延迟 | ≤ 产品批准预算 |
| follow-up P95 | 不低于现有 SLO |
| artifact fallback 后面试可继续率 | 100% 对 recoverable failure |
| purge 完成时间 | 99% 在定义 SLA 内 |

### 18.4 估算误差指标

当 provider 提供 usage 时记录：

```text
error_basis_points =
  (estimated_input - provider_input) / provider_input * 10000
```

必须观察 P50、P95、最大低估和最大高估。低估风险高于高估，应单独告警。

上述指标必须分别按 `zh_hans`、`en`、`mixed`、`other`、`unknown` 聚合。语言桶只用于全局统计，不得与 session、principal 或原始文本关联。任何单一语言桶样本不足时应标记为 `insufficient_sample`，不能合并后掩盖中文或 mixed 输入的低估风险。

---

## 19. 灰度和回滚计划

### 19.1 Phase 0：正确性修复

范围：

- 统一 durable dispatch。
- 修复真实预算传递。
- Conversation fallback。
- 新增关键回归测试。

退出条件：

- legacy/v1/v2 API contract 全部通过。
- 相关 PostgreSQL 和 recovery 测试通过。
- rollout 默认仍为 0。

### 19.2 Phase 1：Repository Acceptance

范围：

- 完整 unit/integration/fault/privacy tests。
- migration validate。
- compile、diff、privacy audit。
- Stage 50 独立 acceptance runner。

预期状态：

```text
READY_FOR_CONTEXT_ARTIFACT_SHADOW
PRODUCTION_OBSERVATION=NOT_RUN
```

该状态不授权生产 consumption。

### 19.3 Phase 2：Budget Shadow

- rollout 保持 0 或仅在既有安全流量上测量。
- `budget.mode=shadow`。
- 收集 estimated/available/provider usage。
- 不因预算拒绝请求。

退出条件：

- 无敏感 telemetry。
- estimator 低估在批准范围内。
- 无 provider context overflow。

### 19.4 Phase 3：Budget Enforcement

- 对新 session 以 1% 开启。
- compression 仍 disabled。
- 观察 reject、shrink 和 latency。

自动停止条件：

- context budget error 超过阈值。
- follow-up 失败率高于基线。
- 当前回答保留测试在生产 canary 失败。

### 19.5 Phase 4：Artifact Shadow Creation

- `compression.mode=shadow`。
- 创建 artifact，但 Examiner 继续消费 deterministic context。
- 对比 source loss、summary coverage、latency 和 cost。

退出条件：

- validation failure 低于门槛。
- 摘要新事实引入率满足质量门槛。
- artifact store 无一致性错误。
- 连接容量满足要求。

### 19.6 Phase 5：1% Artifact Consumption

- 仅新建 v2 session。
- `budget.mode=enforce`。
- interview Question Memory consumption 开启。
- Evidence compression 保持独立关闭，除非单独批准。
- WP-10a Knowledge Coverage 基线已经通过，运行时覆盖标签与 active manifest 一致。
- WP-9b 前端降级契约已经通过，实质性降级可被用户正确理解。

自动回滚：

```text
compression consume → shadow
interview rollout → 0 for new sessions
existing v2 sessions continue with deterministic fallback
```

### 19.7 Phase 6：逐级扩容

建议阶梯：

```text
1% → 5% → 25% → 50% → 100%
```

每一级必须有固定观察窗口、生产记录和明确批准。不得因为 repository tests 通过而自动提升。

### 19.8 已有 session 规则

- 降低 rollout 只影响新 session assignment。
- 已有 v1 session 保持 v1。
- 已有 v2 session 保持 v2 graph 可执行。
- 停止 compression consumption 后，已有 v2 session 使用 deterministic fallback，不迁移回 v1。

---

## 20. 实施工作包

### WP-1：统一 Durable HTTP Dispatch

优先级：P0。

主要修改：

- API routes 引入统一 durable predicate。
- 替换所有 v1 字符串硬编码。
- 增加 legacy/v1/v2 contract tests。

验收：

- v2 snapshot 进入 workflow service。
- v2 mutation 进入 command inbox。
- legacy 行为无变化。

回滚：恢复旧代码，但 rollout 必须保持 0；该回滚不允许在 v2 已投入生产后执行。

### WP-2：真实 Context Budget 传递

优先级：P0。

主要修改：

- selector 接收 resolved budget。
- 预留固定 prompt overhead。
- 新增小窗口和 mandatory answer 测试。
- 增加中文、英文和中英混合 estimator 误差测试与安全语言桶指标。

验收：最终 context measurement 不超过允许预算，除非稳定返回 `ContextBudgetExceeded` 且未调用 provider。

### WP-3：Compression Eligibility

优先级：P1。

主要修改：

- deterministic selection stats 扩展。
- stable eligibility reason。
- 无 loss 时不调用 compressor。

验收：短会话 compressor 调用为零；相同输入得到相同 eligibility。

### WP-4：Conversation Artifact Fallback

优先级：P1。

主要修改：

- 对齐 Evidence coordinator 的 recoverable failure。
- 实现 `artifact_fallback` route。
- 保留 ownership/conflict fail-closed。

验收：provider timeout、busy、validation failure 不阻断面试。

### WP-5：摘要 Schema 和校验加强

优先级：P1。

主要修改：

- Question Memory schema。
- 强制 exact excerpt。
- polarity、claim type、confidence。
- 新事实、否定、数字和 identifier tests。

验收：所有可编程违规样例必须失败；无法由规则判定的自由语义反例即使通过结构校验，也始终保持 `non_authoritative`、带 exact excerpt，且不进入 scoring Evidence。离线语义质量评测单独满足批准门槛。

### WP-6：增量 Question Memory

优先级：P1。

主要修改：

- 问题关闭时创建 Question Memory。
- memory index。
- focus/topic/recency 检索。
- 消除全历史每轮重压。

验收：同一关闭问题最多一次新建调用；replay 使用 completed artifact；当前题保持原文。

### WP-7a：Retention 基础与内存 TTL

优先级：P1。

主要修改：

- 统一 retention policy model。
- In-memory session max count、finished TTL 和 cleanup batch。
- finished session、report、evaluation 的同步淘汰。
- 可注入 clock 和确定性测试。
- PostgreSQL retention candidate selection，但不执行用户主动 purge。

验收：内存实现达到容量上限后不无限增长；active session 不被淘汰；finished session 按策略清理；默认 PostgreSQL 持久语义不被内存 TTL 改变。

### WP-7b：端到端 Purge

优先级：P1。

主要修改：

- deletion job/store/worker。
- DELETE API。
- checkpoint、workflow、generation、artifact ref 和业务数据清理。
- backup 恢复后的删除重放程序。

验收：删除矩阵和并发测试通过，删除后所有读取路径不可恢复内容。

依赖：WP-7a 的 retention policy 和清理基础。

### WP-8a：配置框架和 Preflight

优先级：P1。

主要修改：

- `EffectiveMemoryConfig`。
- `LegacyMemoryEnvironmentAdapter`。
- 新旧配置冲突检测。
- rollout/runtime/budget/compression preflight。
- readiness 输出 effective mode。

验收：非法组合启动失败；readiness 可解释当前有效行为。

### WP-8b：完整配置迁移和旧变量废弃

优先级：P1，在 WP-6 后完成最终字段冻结。

主要修改：

- 所有 memory runtime consumer 改读 effective config。
- 旧 getter 委托 adapter 或标记 deprecated。
- 文档、部署模板和 runbook 切换新配置路径。
- 经过至少一个兼容发布周期后移除旧变量直接读取。

验收：运行时代码不再分散读取记忆相关环境变量；兼容期新旧等值配置行为一致；冲突配置稳定启动失败。

### WP-9a：记忆可观测性

优先级：P1。

主要修改：安全 metrics、dashboard、alert、privacy audit。

验收：能回答 route、fallback、cost、latency、reuse 和 purge 状态，且无敏感字段。

### WP-9b：前端降级体验

优先级：P1，可与 WP-9a 并行。

主要修改：

- snapshot 增加受限 `assistance_mode` 和 `user_notice_required`。
- 候选人端实现非阻塞基础模式提示。
- 管理端展示安全 route 和 fallback code。
- 增加刷新恢复、去重播报和可访问性测试。

验收：透明 artifact fallback 不打扰候选人；实质性降级有清晰提示；提示不暴露内部错误且明确回答已保存。

### WP-10a：Knowledge Coverage 基线

优先级：P1，必须在首次 Artifact consumption 前完成。

主要修改：

- coverage manifest 成为 active corpus release 的必需产物。
- `KNOWLEDGE_COVERED_TAGS` 或等价能力从 active manifest 派生。
- 为已支持的核心岗位补齐最小负向和边界案例。
- 增加 query-hit-evidence 的离线回归集。

验收：运行时声明的覆盖标签与 active corpus 一致；核心岗位至少具有批准的正向、负向和边界样例；检索回归达到基线。

### WP-10b：Knowledge Corpus 扩展

优先级：P2。

主要修改：扩充岗位和技能语料、提高 chunk 覆盖、完善重排评测、建立离线知识改进提案流程。

验收：active corpus 有跨岗位覆盖报告和检索指标，不以 chunk 数本身作为唯一成功标准。

### WP-11：Stage 50 Acceptance

优先级：P1。

主要修改：独立 acceptance runner、operator record、fault matrix、privacy scan。

验收输出：

```text
READY_FOR_CONTEXT_ARTIFACT_SHADOW
PRODUCTION_OBSERVATION=NOT_RUN
```

### WP-12：可选 Principal Long-term Memory

优先级：P2，需产品、安全和法律批准。

主要修改：principal identity、consent、fact store、查看/更正/删除 API、独立 retrieval policy。

验收：默认关闭；跨 deployment/principal 隔离；未来多租户版本跨 tenant 隔离；未确认事实不可消费；不直接影响评分。

### 20.1 工作包依赖

```text
WP-1 ─┐
WP-2 ─┼→ WP-3 → WP-4 → WP-5 → WP-6 → WP-11 → Production Canary
WP-8a ┘                    │
                          ├→ WP-8b
WP-10a ───────────────────┘

WP-7a → WP-7b 可与 WP-3～WP-6 并行，但 WP-7b 必须在正式生产扩容前完成。
WP-9a 从 WP-1 开始伴随所有阶段；WP-9b 在实际启用用户可感知 fallback 前完成。
WP-10a 阻塞首次 Artifact consumption；WP-10b、WP-12 不阻塞第一轮 v2 Context Memory 上线。
```

---

## 21. 最终验收标准

只有全部满足以下条件，Context Artifact consumption 才可进入正式 rollout：

### 21.1 正确性

- [ ] legacy、langgraph-v1、langgraph-v2 HTTP 分流全部正确。
- [ ] durable session 不会调用 legacy mutation。
- [ ] engine assignment 和 graph schema version 对 session 不可变。
- [ ] selector 使用 resolved available budget。
- [ ] 最终 prompt 不超过强制预算。
- [ ] stale worker、lease lost 和 fencing 测试通过。

### 21.2 记忆质量

- [ ] 无信息丢失风险时不调用 compressor。
- [ ] 当前题和最新候选人回答保持精确。
- [ ] Question Memory 基于原始 source。
- [ ] production summary unit 100% 包含 exact excerpt。
- [ ] 普通自然语言虚构反例不会被系统升级为权威事实或评分 Evidence；可编程违规样例全部失败。
- [ ] 所有 accepted LLM summary 均保持 `non_authoritative`，产品和技术文案不宣称自动证明语义忠实性。
- [ ] 摘要不作为唯一评分 Evidence。
- [ ] 长会话事实召回和否定关系指标达到批准门槛。
- [ ] active Knowledge Coverage 标签由 manifest 派生，并具备批准的最小负向和边界案例。
- [ ] 中文、英文和中英混合输入的 estimator 低估均在批准阈值内。

### 21.3 降级和恢复

- [ ] provider timeout、busy、validation failure 使用 deterministic fallback。
- [ ] fallback route 和稳定错误码可观测。
- [ ] parent ownership lost 和 identity conflict fail closed。
- [ ] artifact complete 后进程丢失可复用 completed artifact。
- [ ] HTTP/SSE 重连不会重复业务副作用。

### 21.4 生命周期和隐私

- [ ] session 删除覆盖业务数据、checkpoint、workflow rows、generation rows、artifact refs 和派生数据。
- [ ] retention cleanup 不删除 active refs。
- [ ] telemetry、trace、canary 和 exception 无敏感内容与标识符。
- [ ] 当前 deployment/principal scope 评审完成；多租户部署前另行完成 tenant scope 和 schema v2 评审。
- [ ] backup retention 和恢复后删除程序有文档与演练记录。

### 21.5 运营准备

- [ ] effective config 可在 readiness 中安全查看。
- [ ] 非法配置组合启动失败。
- [ ] route、fallback、latency、cost、reuse、storage、purge 指标可用。
- [ ] 透明 artifact fallback 不产生不必要的候选人告警，实质性降级有通过可访问性测试的用户提示。
- [ ] 自动停止和回滚动作已演练。
- [ ] Stage 50 repository acceptance 完成。
- [ ] 生产观察记录不再是 `NOT_RUN`，且经明确批准。

---

## 22. 决策记录

### D-001：不把候选人回答自动写入公共知识库

原因：候选人回答可能错误、敏感或只适用于个人上下文，自动写入会造成知识污染和隐私泄露。

### D-002：摘要不替代原始消息

原因：摘要有信息损失和幻觉风险，无法承担评分、合规和争议复核的权威职责。

### D-003：优先使用逐题增量记忆，不先引入向量化会话记忆

原因：面试天然按 question ID、focus 和 skill 组织；结构化检索更确定、更便于审计，规模也较小。

### D-004：压缩失败优先回退，不阻断业务

原因：压缩是优化 effect，而不是提交候选人回答的必要业务 effect。一致性和所有权错误除外。

### D-005：长期记忆独立设计并默认关闭

原因：跨会话个人记忆改变产品隐私和评分边界，必须具备身份、授权、查看、更正、删除和过期能力。

### D-006：生产 rollout 不由 repository acceptance 自动授权

原因：单元测试和故障注入不能替代真实 provider、真实延迟、真实连接容量和真实用户流量观察。

---

## 23. 实施完成后的目标状态

优化完成后，系统应具有以下行为：

```text
数据库完整记住一次面试的权威事实；
LangGraph 可靠记住工作流执行位置；
Knowledge Memory 提供受版本控制的岗位知识；
Context Budget 决定模型真实可见范围；
Question Memory 只在旧信息将丢失时保存可追溯摘要；
Context Artifact 在重试和进程替换后安全复用；
普通压缩故障不会阻断面试；
任何敏感记忆都可以按政策过期并按用户请求完整删除；
跨会话记忆只有在明确授权后才存在。
```

最终产品定位应从：

```text
每次面试保存完整，但 Examiner 主要依赖最近消息
```

演进为：

```text
以原始事实为权威、以逐题摘要维持长程连续性、
以确定性预算控制模型输入、以 durable artifact 保证恢复，
并具备完整隐私生命周期的 AI 面试记忆系统。
```

---

## 24. 代码基线参考

以下文件是实现本规范时的主要代码入口：

- `app/api/routes.py`：HTTP engine dispatch 和 session API。
- `app/graphs/interview_state.py`：engine 类型和 durable version predicate。
- `app/graphs/durable_interview_state.py`：v1 graph state。
- `app/graphs/durable_interview_state_v2.py`：v2 graph state 和 artifact refs。
- `app/graphs/durable_interview_graph.py`：durable follow-up generation 和 graph route。
- `app/services/interview_workflow.py`：durable session lifecycle、command 和 snapshot。
- `app/services/session.py`：legacy/in-memory session semantics。
- `app/services/postgres_session.py`：PostgreSQL business session persistence。
- `app/services/session_serialization.py`：session/message/report serialization。
- `app/services/context_budget.py`：operation policy、budget resolver 和 rendered prompt guard。
- `app/services/context_selection.py`：对话和 Evidence 选择、截断和统计。
- `app/services/context_runtime.py`：model profile 和 estimator runtime。
- `app/services/context_compression.py`：compressor provider boundary。
- `app/services/context_compression_validation.py`：artifact payload 验证。
- `app/services/context_compression_runner.py`：claim、heartbeat、complete、reuse 和 recovery。
- `app/services/interview_context_artifacts.py`：Conversation artifact integration。
- `app/services/evidence_context_artifacts.py`：Evidence artifact integration 和 fallback。
- `app/services/context_artifacts.py`：artifact domain contracts。
- `app/services/context_artifact_store.py`：PostgreSQL artifact store。
- `app/services/in_memory_context_artifact_store.py`：测试和 memory runtime store。
- `app/services/context_artifact_scope.py`：privacy scope。
- `app/services/durable_workflow_maintenance.py`：retention cleanup。
- `app/services/postgres_runtime_migrations.py`：runtime schema migration。
- `app/services/vector_store.py`：Knowledge Memory 和 pgvector。

---

## 25. Definition of Done

本规范的实现完成不以“代码已合并”为标准，而以以下条件同时成立为标准：

1. P0、P1 工作包全部满足验收条件。
2. 所有新 schema 均有 migration、validate 和回滚策略。
3. legacy、v1、v2 兼容性矩阵全部通过。
4. Context Memory 质量评测达到批准门槛。
5. 隐私扫描和完整删除演练通过。
6. Stage 50 repository acceptance 有固定产物。
7. 小比例生产 shadow 和 consumption 有观察记录。
8. 技术负责人、SRE、QA 和安全/隐私负责人明确批准扩容。
9. 默认配置保持保守，未批准环境不会自动启用长期语义记忆。

---

## 26. Adaptive Task-Aware Context Compression Requirements

This section is the normative requirement baseline for
`2026-08-07-adaptive-task-aware-context-compression-optimization.md`. The
keywords MUST, MUST NOT, SHALL, and SHALL NOT are normative. Each
`MEM-CTX-*` identifier appears once as a normative statement and once in the
verification mapping. The repository acceptance runner rejects a missing,
duplicate, or unreferenced statement or mapping.

### 26.1 Normative statements

- `MEM-CTX-PLAN-001`: The adaptive plan and this Spec MUST maintain exact bidirectional traceability, with every planned MEM-CTX requirement represented by one normative statement and one verification mapping.
- `MEM-CTX-CFG-001`: All selection limits, exact-recent policy, and proactive eligibility thresholds MUST be resolved through one immutable, validated effective configuration whose committed defaults preserve current behavior.
- `MEM-CTX-CFG-002`: Deduplication mode, target tiers, provider-circuit policy, validation-quarantine policy, cooldown, and lease settings MUST use explicit validated fields and privacy-safe readiness output.
- `MEM-CTX-INTENT-001`: CompressionIntent v1 MUST be bounded, canonically normalized, built only from trusted workflow metadata, and hashed from the same canonical representation used by intent-aware identity.
- `MEM-CTX-INTENT-002`: The compressor MUST receive the actual canonical semantic intent and preservation contract, while prompts and validation continue to prohibit invented facts, unsupported excerpts, and authoritative scoring use.
- `MEM-CTX-ID-001`: Artifact identity-v0 serialization MUST remain byte-compatible, while identity-v1 MUST include the intent digest and schema version without re-keying or rewriting completed v0 Artifacts.
- `MEM-CTX-ID-002`: In-memory and PostgreSQL persistence MUST reconstruct and verify complete versioned Artifact identity, and any version, digest, owner, scope, or key mismatch MUST fail closed.
- `MEM-CTX-AUTH-001`: Original messages and raw Evidence MUST remain authoritative and retrievable; compressed outputs and Evidence projections MUST remain non-authoritative, source-verifiable, and excluded from scoring provenance.
- `MEM-CTX-AUTH-002`: Interview Semantic Status MUST expose only fields with declared authoritative or explicitly advisory provenance and MUST NOT inject runtime-control, identity, circuit, or private content metadata into Provider prompts.
- `MEM-CTX-ELIG-001`: Proactive compression eligibility MUST use mode-aware pre-loss estimates from the same model, tokenizer resolution, and Provider-message framing estimator as final input, require compressible history, and decide thresholds by integer cross-multiplication rather than rounded telemetry.
- `MEM-CTX-TARGET-001`: One immutable ResolvedCompressionRequest MUST select an allowed target tier and bind that same target to Artifact identity, compressor prompt, Provider output limit, validation, and prompt measurement.
- `MEM-CTX-BUD-001`: Selectable content MUST derive from resolved model availability minus fixed prompt reserve, and mandatory bounded-raw overflow MUST use the stable budget failure or business fallback with zero semantic-compressor calls.
- `MEM-CTX-SOURCE-001`: Conversation and Evidence units MUST use versioned canonical source identities containing authoritative scope, sequence or provenance, role, and content digest; content text alone MUST NOT establish identity.
- `MEM-CTX-DEDUP-001`: Exact deduplication MUST remove only identity-proven equivalent representations, preserve distinct provenance, prefer mandatory bounded-raw representations, and produce deterministic ordering and aggregate counts.
- `MEM-CTX-DEDUP-002`: Deduplication MUST support disabled, shadow, and enforce modes; shadow results MUST remain counterfactual and MUST NOT alter business eligibility, target, source segments, Artifact identity, compressor input, deterministic selection, or final Provider input.
- `MEM-CTX-RAW-001`: The current question, latest candidate answer, configured exact-recent questions, and authoritative raw Evidence MUST remain stored authoritatively and non-semantically-compressed in deterministically bounded Provider representations.
- `MEM-CTX-MEMORY-001`: Question Memory MUST remain reusable, non-authoritative, owner-bound, source-verifiable, identity-safe when subtracting raw units, deterministically ranked, and bounded by configured unit and token caps.
- `MEM-CTX-STATUS-001`: Interview Semantic Status MUST be bounded and deterministic, derive progress and focus from authoritative records, label optional unresolved-topic codes advisory, and preserve checkpoint compatibility.
- `MEM-CTX-FAIL-001`: Provider failures MUST use a durable owner-scoped circuit with atomic transitions, configured thresholds and cooldown, fenced probe leases, deterministic fallback, and at most one half-open probe per scope.
- `MEM-CTX-FAIL-002`: Repeatable source or intent validation failures MUST use a separately keyed durable quarantine whose reset and expiry boundaries cannot disable unrelated work or weaken fail-closed ownership and privacy checks.
- `MEM-CTX-PRIV-001`: Failure-state records MUST persist only approved irreversible digests and stable codes required for correctness; exported telemetry, checkpoints, and acceptance artifacts MUST remain owner-scoped and MUST NOT expose raw candidate, Evidence, prompt, summary, identifier, credential, or those digests as metric dimensions.
- `MEM-CTX-OBS-001`: Observability MUST report bounded aggregate business and counterfactual measurements separately, including eligibility, preservation, fallback, cost, latency, compression ratio, and estimate error without logging source content.
- `MEM-CTX-EVAL-001`: Evaluation MUST cover grounded relevance, preservation, multilingual behavior, adversarial fact changes, deterministic fallback, cost, and latency while treating model-judge output as non-authoritative.
- `MEM-CTX-ACCEPT-001`: Repository acceptance MUST run the fixed declared test matrix with fake Providers, make zero real-Provider calls, enforce plan/spec traceability and privacy scans, and emit only repository readiness.
- `MEM-CTX-RECOVERY-001`: Recovery acceptance MUST cover identity-v0/v1 reload, lease and fencing races, circuit and quarantine persistence, checkpoint compatibility, deletion and retention boundaries, and rollback-safe defaults.
- `MEM-CTX-SHADOW-001`: Deployed shadow observation MUST require separate authorization, preserve business Provider input and source authority, record privacy-safe quality and cost evidence, and satisfy promotion and hold gates before consumption.
- `MEM-CTX-CANARY-001`: Consume canary MUST require separate authorization, use a low stable assignment, preserve scoring provenance and owner isolation, and roll back automatically on declared correctness, privacy, availability, cost, or latency triggers.

### 26.2 Verification mappings

- Verification `MEM-CTX-PLAN-001`: `tests/test_memory_system_optimization_acceptance.py` validates the exact 27-ID bidirectional traceability contract and its negative cases.
- Verification `MEM-CTX-CFG-001`: `tests/test_memory_config.py`, `tests/test_agent_runtime_composition.py`, and `tests/test_memory_config_source_audit.py` verify immutable selection configuration and safe defaults.
- Verification `MEM-CTX-CFG-002`: `tests/test_memory_config.py` and `tests/test_memory_config_source_audit.py` verify mode, tier, cooldown, lease, and readiness validation.
- Verification `MEM-CTX-INTENT-001`: `tests/test_context_artifact_contracts.py` and `tests/test_context_compressor.py` verify bounded canonical intent, normalization, and digests.
- Verification `MEM-CTX-INTENT-002`: `tests/test_context_compressor.py`, `tests/test_context_compression_validation.py`, and `tests/test_context_compression_runner.py` verify semantic prompt material and grounding.
- Verification `MEM-CTX-ID-001`: `tests/test_context_artifact_contracts.py`, `tests/test_interview_context_artifacts.py`, and `tests/test_evidence_context_artifacts.py` verify byte-compatible v0 and intent-aware v1 identities.
- Verification `MEM-CTX-ID-002`: `tests/test_context_artifacts.py`, `tests/test_context_artifact_store_postgres.py`, and `tests/test_in_memory_context_artifact_store.py` verify complete identity persistence and fail-closed reload.
- Verification `MEM-CTX-AUTH-001`: `tests/test_context_compression_validation.py`, `tests/test_evidence_context_artifacts.py`, and `tests/test_durable_interview_graph.py` verify source authority and scoring separation.
- Verification `MEM-CTX-AUTH-002`: `tests/test_interview_status_projection.py` and `tests/test_durable_interview_graph.py` verify declared status provenance and excluded runtime metadata.
- Verification `MEM-CTX-ELIG-001`: `tests/test_context_compression_eligibility.py` and `tests/test_context_selection.py` verify pre-loss equations, integer threshold decisions, and compressible-history gating.
- Verification `MEM-CTX-TARGET-001`: `tests/test_context_compression_eligibility.py`, `tests/test_context_artifact_contracts.py`, and `tests/test_context_compressor.py` verify one bound target tier.
- Verification `MEM-CTX-BUD-001`: `tests/test_context_budget.py`, `tests/test_context_selection.py`, and `tests/test_interview_context_artifacts.py` verify resolved availability and mandatory overflow behavior.
- Verification `MEM-CTX-SOURCE-001`: `tests/test_context_source_identity.py`, `tests/test_evidence_context_artifacts.py`, and `tests/test_question_memory.py` verify canonical identity and replay stability.
- Verification `MEM-CTX-DEDUP-001`: `tests/test_context_source_identity.py` and `tests/test_context_selection.py` verify identity-only deterministic exact deduplication.
- Verification `MEM-CTX-DEDUP-002`: `tests/test_context_selection.py` and `tests/test_context_compression_eligibility.py` verify disabled, shadow, and enforce business-path semantics.
- Verification `MEM-CTX-RAW-001`: `tests/test_context_selection.py`, `tests/test_question_memory.py`, and `tests/test_durable_interview_graph.py` verify bounded-raw preservation.
- Verification `MEM-CTX-MEMORY-001`: `tests/test_question_memory.py` and `tests/test_question_memory_retrieval.py` verify identity-safe subtraction, source verification, ranking, and caps.
- Verification `MEM-CTX-STATUS-001`: `tests/test_interview_status_projection.py`, `tests/test_durable_interview_state.py`, and `tests/test_durable_interview_graph.py` verify deterministic status projection and compatibility.
- Verification `MEM-CTX-FAIL-001`: `tests/test_context_compression_failure_containment.py` and `tests/test_context_compression_failure_store_postgres.py` verify the durable fenced provider circuit.
- Verification `MEM-CTX-FAIL-002`: `tests/test_context_compression_failure_containment.py`, `tests/test_context_compression_runner.py`, and `tests/test_context_compression_validation.py` verify scoped validation quarantine.
- Verification `MEM-CTX-PRIV-001`: `tests/test_trace_sanitization.py`, `tests/test_memory_metrics.py`, and `tests/test_context_compression_failure_containment.py` verify privacy-safe state and artifacts.
- Verification `MEM-CTX-OBS-001`: `tests/test_memory_metrics.py` and `tests/test_context_compression_shadow_acceptance.py` verify separated bounded aggregate metrics.
- Verification `MEM-CTX-EVAL-001`: `tests/test_context_compression_shadow_acceptance.py` and `tests/test_context_compression_validation.py` verify golden, multilingual, adversarial, cost, and latency evaluation.
- Verification `MEM-CTX-ACCEPT-001`: `tests/test_memory_system_optimization_acceptance.py` and `tests/test_context_compression_shadow_acceptance.py` verify the fake-Provider repository gate.
- Verification `MEM-CTX-RECOVERY-001`: `tests/test_context_compression_failure_containment.py`, `tests/test_context_artifact_store_postgres.py`, `tests/test_durable_interview_state.py`, `tests/test_durable_interview_graph.py`, and `tests/test_session_deletion_worker.py` verify recovery, compatibility, and deletion.
- Verification `MEM-CTX-SHADOW-001`: `tests/test_context_compression_shadow_acceptance.py` plus the signed Task 11 observation packet verify deployed-shadow evidence, promotion gates, and unchanged business input.
- Verification `MEM-CTX-CANARY-001`: The signed Task 12 promotion packet and Section 11.2 deployment gate verify stable assignment, rollback triggers, provenance, and isolation.
