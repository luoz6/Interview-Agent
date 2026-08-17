# Interview-Agent 用户资料库与 RAG 产品化实施计划 v1.4

> 日期：2026-08-15
>
> 状态：U0–U4 ACCEPTED / U1b REAL POSTGRES ACCEPTANCE PENDING / U5 DEFERRED / OPTIONAL
>
> 基线分支：master
>
> 基线提交：2aa4d32bf2489fcb6d410013a6824722d8814ccd
>
> 项目定位：Learning Project / Technical Showcase / Local V1
>
> 文档类型：实施型 How-to Plan
>
> 目标读者：负责 FastAPI、Knowledge RAG、Interview Plan、React/Vite、数据隔离和测试的项目维护者
>
> v1.1 修订：把 v1.0 的 Batch A～H 收敛为 U0～U5 独立里程碑；将安全要求集中为 S1～S9 权威不变量；保留 Owner、Revision、Scope Snapshot、真实 Citation、评分隔离和 Prompt Injection 防护；取消 RBAC 想象、过多 Port、复杂历史报告重写、过细公共状态和重复治理文字；把文本层 PDF 调整为非阻断的 U5 产品增强。
>
> v1.2 修订：按实际工作树记录 U0/U1 已有实现候选，不再把整份计划误标为“未执行”；新增权威执行状态、subagent 分阶段实施门禁和主线程独立复核规则。v1.2 不扩大产品范围，也不降低 S1～S9。
>
> v1.3 修订：在并发工作树复核中发现 U2a 已接受实现被部分覆盖，因此撤销当前 `ACCEPTED` 状态并新增恢复门禁；明确显式 Scope 的 protected Plan Source/Session Binding Owner、不可枚举统一错误，以及“首次 Start 重新验证、成功 Session replay 先读取冻结 Binding”的确定性语义。v1.3 不把历史通过的测试当作当前实现证据。
>
> v1.4 修订：自动审查发现 U3 状态表提前标记为 `ACCEPTED`，但现有产品闭环测试只直接验证 Citation projector，尚未证明 `ExpertShadowEvaluator.evaluate(...)` 经 frozen Session Scope、Review Binding、Final Evidence 和实际消费后生成安全 Citation。v1.4 先将 U3 恢复为 `IMPLEMENTED / VERIFICATION REQUIRED` 并把真实 Evaluator 业务链测试列为硬门禁；随后 subagent 完成该测试切片，主线程独立复核后关闭门禁并重新接受 U3。

## 0. 执行摘要

本计划把当前面向开发者的 RAG Console 拆成两个产品入口：

~~~text
普通产品入口
准备 / 面试 / 报告 / 我的资料 / 我的记忆 / 帮助

开发者实验入口
AI 技术实验室
├── 运行状态
├── 检索演示
├── 评测
├── 证据链
└── Corpus 开发者详情
~~~

普通用户继续使用 RAG，但用户面对的对象变为：

~~~text
文件
处理状态
本次面试是否选用
问题、追问或反馈是否实际引用
停用、重试与删除
~~~

Corpus Version、Manifest SHA、Embedding Identity、RRF、Evidence Gate 和 Eval Artifact 只保留在技术实验室。

这不是把导航中的 RAG 改名为“我的资料”，也不把现有 /api/rag/corpus 伪装成文件上传接口。完成目标必须建立四个权威契约：

1. UserDocument：资料身份、当前 Local Principal 归属、内容修订和生命周期；
2. InterviewKnowledgeScopeSnapshot：某个 Plan Revision / Session 允许使用的资料范围；
3. Source-aware Retrieval：只检索 Scope 允许的资料，并继续复用现有 Semantic、Lexical、Fusion、Rerank 和 Evidence Gate；
4. SafeKnowledgeCitation：只展示真正被业务消费的安全引用。

实施顺序固定为：

~~~text
U0  契约冻结
 ↓
U1  资料库基础闭环与入口分层
 ↓
U2  面试资料 Scope 与受限检索
 ↓
U3  问题、追问、报告与安全引用
 ↓
U4  产品收口、自动化验收与文档
 ↓
U5  文本层 PDF 产品增强（可选，不阻断 U1～U4）
~~~

U1～U4 是当前主计划。U5 不属于当前完成条件。

### 0.1 权威执行状态

当前执行状态只以本表和“U4 最终审计与接受证据”一节为权威状态源。历史工作项复选框不机械回填，也不单独证明代码已经正确：

| 里程碑 | 当前状态 | 已存在的候选实现 | 尚未确认或尚未完成 |
|---|---|---|---|
| U0 | ACCEPTED | ADR、领域模型、两个 Port、Architecture/Contract 测试骨架 | 已通过目标契约和 Interview Plan 风险回归；真实 PostgreSQL 不属于 U0 |
| U1a | ACCEPTED | InMemory Store、摄取/删除服务、Materials API 与部分运行时装配 | 已通过 Owner、生命周期、失败/重试、删除零命中、API 安全投影和 capability 自动化验收 |
| U1b | VERIFIED / REAL POSTGRES ACCEPTANCE PENDING | PostgreSQL 表定义、Store/Repository、Runtime 持久化装配和契约测试 | 静态、Fake 与无实库契约已通过；真实 FK、索引、级联和事务恢复仅在单独授权后验收 |
| U1c | ACCEPTED | `/materials`、导航分层、`/rag/lab` 兼容、前端状态与安全投影 | 自动化、ESLint、Build 与 Bundle Gate 已通过；浏览器视觉验收不在本阶段范围 |
| U2a | ACCEPTED | Prep 安全 Scope、protected Plan Source/Session Owner、Plan/Session 固化、首次 Start revalidate 与 frozen replay | 回退契约已恢复；subagent 与主线程均完成独立扩大验证 |
| U2b | ACCEPTED | Source-aware Retrieval、Owner+Revision allowlist、explicit empty、通道内统一排名和安全降级 | subagent 与主线程均完成独立本地超集验证；未调用真实 PostgreSQL 或外部 Provider |
| U2c | ACCEPTED | Prep Selector、系统知识开关、资料状态、后端确认 Scope 摘要和安全错误投影 | capability-disabled/system-only、恢复/失效状态、触控与 DOM 安全已由 subagent 和主线程独立验收；真实 PostgreSQL 与外部 Provider 不在本阶段范围 |
| U3 | ACCEPTED | Safe Citation、Follow-up frozen Scope、Question/Review Binding、评分隔离与 Interview/Report 展示均完成独立验收 | 真实 PostgreSQL 与外部 Provider 不在 U3 验收范围 |
| U4 | ACCEPTED | Help、README、Runbook、架构/ADR 收口与自动化验收已完成最终审计 | S1～S9 跨里程碑最终审查与独立复核已完成；真实 PostgreSQL/pgvector 和外部 Provider 继续保持未验收边界 |
| U5 | DEFERRED / OPTIONAL | 无 | 不阻断 U1～U4 |

状态只能按以下方向推进：

~~~text
NOT STARTED
→ IMPLEMENTED / VERIFICATION REQUIRED
→ VERIFIED
→ ACCEPTED
~~~

不得因为文件存在、测试被编写、实现者自述成功或某个局部测试通过，就跳过 `VERIFICATION REQUIRED`。

### U3 已接受证据（2026-08-16）

- Report 定向 Vitest：`12/12`；Interview 定向 Vitest：`24/24`。
- 前端全量 Vitest：`15 files / 165 tests`。
- ESLint、生产 Build、独立 Bundle Gate 与 `git diff --check` 均通过。
- Initial JS gzip 为 `67,578 / 67,584` bytes；Initial CSS gzip 为 `12,374 / 20,480` bytes。
- 保护路由保持 lazy，普通导航未重新暴露 `/rag/lab`。
- Report 所有完整读取入口的 Citation read-time sanitizer、Reviewer frozen feedback Scope、Question Source Scope Binding、Prompt Injection 隔离和未回答评分隔离均已有目标测试；Question Binding 的 Source Scope 定向超集由主线程复核为 `36 passed`，`py_compile` 与 `git diff --check` 通过。
- subagent 新增真实 `ExpertShadowEvaluator.evaluate(...)` 参数化测试，在同一 frozen Plan Revision / Session Binding 和 Owner-bound InMemory Store 下证明：只有允许 Scope 内、进入 Review Binding Final Evidence 且实际消费的用户资料 Evidence 才进入 `feedback.knowledge_citations`；只被选择但未消费时 Citation 为空；公共 Report 不含 Owner、Document/Revision ID、Hash、Query、Prompt 或原始资料内容。subagent 得到目标 `2 passed`、Evaluator 全文件 `29 passed`、Citation/Report Contract `16 passed`；主线程重读差异并独立运行 U3 后端超集为 `170 passed / 1 existing warning`，`py_compile` 与 `git diff --check` 通过。
- 主线程首次前端全量复核得到 `164 passed / 1 recovery timing assertion failed`，没有把定向绿色误记为全量通过；subagent 将同一 microtask 内的即时 DOM 读取改为在释放 auxiliary evaluations 前等待 React 提交既有 `error` 状态，保留四个 disabled 断言和 same-command fail-closed 契约，目标测试连续至少五次通过。主线程独立复核目标测试和 ESLint 后，重新运行前端全量为 `15 files / 165 tests`，生产 Build、独立 Bundle Contract 与 Bundle Gate 均通过；该修复只稳定测试时序，没有修改产品组件。

### U4 最终审计与接受证据（2026-08-16）

以下是 U4 接受状态的最终证据；本节与权威执行状态表共同构成当前状态源，不以历史工作项复选框替代：

- Unit/contracts/integration 当前状态非保护全量：
  - `4037 passed`
  - `3 skipped`
  - `273 marker-excluded/deselected`
  - `0 failed`
  - `1 existing Starlette/httpx warning`
- Architecture/Acceptance：
  - `463 passed`
  - `0 failed`
- Frontend：
  - `15 files`
  - `165 tests`
  - `ESLint: 0 warnings`
  - `production build: passed`
  - `bundle gate: passed`
  - `JS gzip: 67,578 / 67,584`
  - `CSS gzip: 12,374 / 20,480`
  - `protected routes: lazy`
- 前端最终审计曾复现 `ReportDetailPage` visibility refresh 测试在 listener effect 提交前派发事件的时序红灯。测试现先等待真实 `visibilitychange` listener 注册，再验证刷新请求携带 `AbortSignal` 且 cleanup 会 abort；未修改产品组件。目标用例连续五轮通过，修复后的前端全量连续两轮均为 `15 files / 165 tests`。并发资源压力下另外两个路由/交互超时定向复跑均通过，独占全量复跑未复现。
- Repository integrity：
  - `compileall: passed`
  - `git diff --check: passed`
- Context Enforcement 使用唯一的 `FOLLOWUP_CONTEXT_POLICY`、实际 model profile、实际 estimator 和实际 rendered prompt，并据此推导恰好超出 1 token 的 model window/input budget。调用真实 `generate_followup` 时，`ContextBudgetExceeded` 在 Provider invocation 之前发生，并断言 `provider calls == 0`。
- 现有 subagent 完成只读跨里程碑最终审查：S1 Identity、S2 Ownership、S3 frozen Scope/non-widening/replay、S4 consumed-only Citation/deleted redaction、S5 scoring isolation、S6 untrusted content/Prompt Injection、S7 deletion zero-hit、S8 capabilities/no RBAC、S9 Semantic/Lexical-only Fusion 在 Local V1 非保护边界内全部 `PASS`；legacy compatibility、API/DOM/Trace 脱敏、Bundle 与保护测试边界也通过审查。
- subagent 发现 ADR governing plan 仍写 `v1.3`；主线程已将其修正为当前 `v1.4`。主线程随后独立重读关键实现与测试，并重新得到 S1～S9 closure `59 passed`、Architecture/Acceptance `463 passed`、unit/contracts/integration `4037 passed / 3 skipped / 273 deselected`、前端连续两轮 `165 passed`、ESLint/Build/Bundle/compileall/diff check 全部通过。
- 工作树中的并发既有修改继续视为用户所有。本轮没有提交、合并、reset、checkout 或清理，也没有用当前脏工作树自动推断可合并提交；这不阻断无提交的 Local V1 功能接受，但仍是未来提交/合并前必须按 hunk 隔离并在最终候选提交树上复验的集成门禁。
- 明确排除的保护 marker 保持不变：`pgvector`、`pg_runtime`、`pg_jobs`、`pg_control`、`langgraph_recovery`、`langgraph_review_recovery`、`langgraph_dual_canary`、`langgraph_single_writer`、`langgraph_fencing`、`langgraph_effect_replay`、`langgraph_fencing_canary`、`langgraph_heartbeat_recovery`、`postgres_capacity`、`real_llm`。
- 未验证或延期边界保持不变：真实 PostgreSQL、pgvector、外部 LLM Provider、外部 Embedding Provider、Corpus lifecycle、Ground Truth、paired evaluation、截图型/视觉型/真实模型 Browser Smoke，以及 U5。非视觉 DOM 契约已经由 Vitest 覆盖；U5 保持 `DEFERRED / OPTIONAL`，未进入实现。

2026-08-15 U0/U1a 验收证据：subagent 目标回归 53 passed；主线程扩大后的 U0/U1a 目标回归 65 passed；主线程 Interview Plan 风险回归 283 passed、1 deselected（受保护的 `pg_runtime`）；`git diff --check` 无格式错误。验收修复了无序资料输入在“Scope 持久化排序”和 `selection_sha256` 计算之间不一致的问题。真实 PostgreSQL、前端和 U2+ 未包含在本次接受范围内。

2026-08-15 U1b 无实库验收证据：subagent 与主线程分别运行完整 Materials 静态/Fake/无实库回归，均为 94 passed；`git diff --check` 无格式错误。验收修复了 PostgreSQL Runtime 仍静默装配 InMemory Materials 的问题，PostgreSQL 模式现在使用共享 business connection provider、`schema_mode=validate` 的 Postgres Store/PgVector Repository，Schema 不可用时安全失败且不回退内存。没有连接真实数据库，因此 U1b 保持 `REAL POSTGRES ACCEPTANCE PENDING`。

2026-08-15 U1c 验收证据：subagent 与主线程均运行全量前端 Vitest，14 files / 147 tests passed；ESLint 0 warnings；生产 Build 和 Bundle Gate 通过；Materials 与 RAG Lab 保持 lazy route。验收修复了 Materials 搜索、用途选择和行操作低于 44px 的触控目标，以及 initial JavaScript 超预算 26 bytes 的回归。当前初始 JavaScript 只比预算低 3 bytes，记录为 U4 必须重新核验的收口风险，不通过放宽预算掩盖。

2026-08-15 U2a 历史验收证据（当前不构成有效状态）：subagent 受影响回归 139 passed；主线程扩大到 Scope Resolver、Prep API、Plan Revision、Session serialization/row mapper、Start/replay 和 configured E2E 共 154 passed；`git diff --check` 无格式错误。当时验收统一了不可枚举 Scope 错误，冻结并规范化请求，给 protected Plan Source/Session Binding 增加服务端 Owner，保持 legacy source hash，修复成功 Session replay 在资料随后停用/删除时被错误阻断的问题，并收紧公共 Scope 投影。

2026-08-15 U2a 回退审计：当前 `PlanSourcePayload` 已不再接受 Owner，Prep 不再把服务端 Principal 写入 protected Source；Scope Resolver 对不存在、非法 UUID 或跨 Owner 返回 `knowledge_scope_document_not_found`；Start 在检查已有 Session replay 前重新验证资料，使成功 Session 可因资料随后停用或删除而无法幂等重放；相关测试也被改成接受该回退行为。因此 U2a 降回 `IMPLEMENTED / VERIFICATION REQUIRED`，恢复并由主线程重跑扩大回归前不得再次标记 `VERIFIED` 或 `ACCEPTED`。

2026-08-15 U2a v1.3 恢复验收证据：subagent 先把回退测试恢复为权威契约并取得 `10 failed / 70 passed` 的真实红灯，随后局部恢复 protected Source Owner、显式/legacy Scope 区分、统一 `knowledge_scope_document_unavailable`、首次 Start 重验和 frozen replay 前置，最终目标与扩大回归共 `378 passed`。主线程重新检查实际差异和最终文件 Hash，独立得到目标 `83 passed`、Session/Launch `18 passed`、Plan/Draft 本地测试 `252 passed / 1 protected PostgreSQL deselected`、configured E2E/API `94 passed`、Architecture/Scope `18 passed`；legacy hash/Owner binding 独立断言和 `py_compile` 通过，`git diff --check` 无空白错误。更宽的 Durable Graph 审计另发现 5 个上下文/NameError 失败和 5 个真实 PostgreSQL 授权门禁，未由 U2a 目标文件引入，不作为 U2a 契约降级理由，但登记为 U4 全量收口必须处理或取得授权的风险。

2026-08-15 U2b 验收证据：原候选实现证明 `source_scope` 只约束来源，Fusion 仍只有 Semantic/Lexical，两类来源在每个通道内统一排名并受单一全局候选上限约束；User Repository 强制 Owner + frozen Revision allowlist，所有来源关闭时 explicit empty，失败不回退到无 Scope 检索。subagent 先修复 Hybrid 顶层把可用通道 `DEGRADED` 错升为 `AVAILABLE` 的缺陷，随后主线程复核又发现 Source-aware 通道合并存在同类回退；新增 Semantic/Lexical 红灯为 `2 failed / 8 passed`，修复后保留第一个安全 non-AVAILABLE reason 且不泄露 Query、Owner、Document/Revision 或原始异常。subagent 本地超集 `128 passed`；主线程重新检查最终差异和 Hash，并独立运行当前责任超集 `135 passed`（Scope/Source-aware 18、Hybrid/Runtime/Evidence/Architecture 68、Owner/Revision/Deletion/Grounding 49）；`py_compile` 与 `git diff --check` 通过。真实 PostgreSQL、外部 Provider、前端和 U2c+ 不在本次接受范围。

2026-08-15 U2c 候选审计：工作树中已经存在 Prep 资料选择器、Ready+Enabled 选择门禁、processing/failed/disabled 状态、系统知识开关、只提交 Document ID 的请求、服务端确认 Scope 摘要、失效阻断和相关 DOM 测试。文件存在不构成验收；U2c 先记为 `IMPLEMENTED / VERIFICATION REQUIRED`，必须额外证明 Materials capability 不可用时的 system-only 行为与后端一致、恢复已有 Plan 时 Scope 正确回填、删除/停用/加载失败不会静默扩大范围、普通 DOM 不含 Owner/Revision/Hash，以及触控、ESLint、Build 和 Bundle Gate 不回归。

2026-08-15 U2c 验收证据：subagent 审查并修复了两个缺陷：显式 system-only/empty Scope 不再无条件构造生产 Materials Resolver，且资料列表权威状态未知时缓存行保持选中但不可交互并显示“待确认”，不会静默缩小或扩大范围；其定向前端为 `32 passed`、全量前端为 `158 passed`、ESLint 零错误零警告、Build/Bundle Gate 通过，后端定向为 `20 passed`、扩大本地回归为 `428 passed / 1 protected PostgreSQL deselected`。主线程重新检查最终差异后独立得到前端定向 `32 passed`、全量 `158 passed`、ESLint 零错误零警告、Build/Bundle Gate 通过、后端 Scope 定向 `20 passed`，且 `git diff --check` 无空白错误。主线程额外扩大到 550 个相邻收集项时先得到 `548 passed / 1 runtime identity boundary failed / 1 protected PostgreSQL blocked`；该运行时边界失败源于 Materials 工厂函数被放入身份解析器源码隔离区间，主线程只调整函数顺序后取得目标 `4 passed`，并将同一扩大集合重跑为 `549 passed / 1 protected PostgreSQL deselected`。受保护测试未获单独真实数据库授权，不得误称已通过。当前 initial JavaScript gzip 为 `67,579 / 67,584 bytes`，仅余 5 bytes，必须在 U4 重验且不得通过放宽预算掩盖。

2026-08-15 U3a1-R 报告读取脱敏验收证据：subagent 对 legacy 当前报告、artifact-first 当前报告、Artifact 历史/详情以及两类 PDF 导出的全部完整 payload 入口完成静态映射，确认它们统一经过 `sanitize_report_knowledge_citations_for_read`；删除、跨 Owner、Store 查询失败、Store 不可用和冻结绑定不可验证均投影为 content-free `deleted`，且不改写持久化 Report/Artifact。本轮无代码修改，subagent 与主线程分别独立得到相同的 `19 passed / 1 existing warning`，最终文件 Hash 一致。报告摘要、任务和进度接口不携带完整 Report/Citation payload。该接受只覆盖 U3a1-R，不代表 Follow-up、Binding、评分隔离、U3 前端或整个 U3 已完成。

### 0.2 分阶段执行与独立复核门禁

本计划后续使用 subagent 实施时遵守以下规则：

1. 主线程一次只下发一个边界明确的里程碑或切片，并冻结允许修改的文件范围、非目标和验收命令；
2. subagent 不得在没有主线程新指示的情况下自动进入下一里程碑；
3. subagent 必须交付实际修改文件、关键设计选择、运行过的测试、未运行测试及剩余风险，不能只给结论；
4. 主线程必须独立检查实际差异，并重新运行与风险相称的测试；实现者的测试结果不能替代主线程复核；
5. 任一阶段发现契约冲突、越界修改、回归或无法复现的测试结果时，该阶段保持 `VERIFICATION REQUIRED`，修复后重新验收；
6. U1～U4 全部阶段验收后，再执行一次跨里程碑自动审查，覆盖 S1～S9、兼容性、前后端、数据库边界、删除零命中、Prompt Injection 和评分隔离；
7. 最终审查通过前，不宣称主计划完成；本计划也不授权自动提交、合并、清理工作树或覆盖既有修改。

## 1. 项目定位与复杂度边界

### 1.1 当前定位

本项目是本地单用户学习项目和技术展示，不是公网多租户 SaaS。当前 Runtime 已有 Local Principal 和 Principal Identity Resolver，但没有产品账号、登录、普通用户角色、开发者角色或管理员角色。

因此本计划：

- 继续让资料 Repository 强制绑定当前 Principal；
- 使用合成 Principal A/B 做隔离契约测试；
- 不新增登录、账号、租户、角色或 RBAC；
- 不建设团队资料空间、共享权限、计费和配额；
- 技术实验室继续复用现有 RAG capability。

Owner-aware 不是为了把项目改造成 SaaS，而是避免从第一天就把个人资料写成无归属的共享表。合成 A/B 测试不需要真实账号体系。

### 1.2 产品目标

完成 U1～U4 后，用户应能：

1. 上传 UTF-8 Markdown / TXT；
2. 看见资料正在处理、可使用或失败；
3. 重试、停用和删除资料；
4. 在准备面试时选择本次资料；
5. 让 Plan 和 Session 固化该选择；
6. 在相关问题、追问和反馈中使用选中资料；
7. 看见 AI 实际引用了哪份资料；
8. 明确区分“我的资料”“系统知识”和“我的记忆”。

开发者仍能在技术实验室查看现有 RAG 运行、检索、评测、证据链和全局 Corpus 信息。

### 1.3 第一阶段明确不做

以下事项不属于 U1～U4，也不得被重新加入其完成定义：

- PDF、DOCX、PPT、图片、扫描件或 OCR；
- 登录、账号、团队、租户、角色或 RBAC；
- GraphRAG、知识图谱或向量空间可视化；
- 新检索通道、新 Fusion 算法、新 Reranker 或 Cross-Encoder；
- 用户资料固定加权或永远优先排名；
- Query-aware Fusion 调参或效果证明；
- No-evidence 阈值调参、F1 优化或新 Sufficiency 策略；
- 75 tuning 人工 Ground Truth；
- Annotator A、Annotator B 或 Adjudication；
- Legacy / Semantic / Lexical / RRF 的真实 PostgreSQL paired evaluation；
- 新 Eval Artifact、Candidate Artifact 或 holdout 运行；
- Reviewer / Follow-up blind A/B；
- 外部 Embedding Provider 调用作为自动化验收条件；
- 新全局 Corpus Version 创建、激活、发布、回滚或重嵌入；
- Shadow、Canary、Promotion 或生产发布治理；
- 截图、人工视觉基准图或图像验收；
- 对长期记忆领域模型进行重构。

## 2. 当前基线与问题

### 2.1 当前导航是开发者心智模型

当前一级导航为：

~~~text
准备
报告
我的记忆
RAG
帮助
~~~

当前 RAG 子导航为：

~~~text
运行概览
检索诊断
评测看板
证据链路
知识语料
~~~

它适合工程展示，不适合普通面试用户。

### 2.2 当前 Corpus 写入不是个人资料上传

当前 Corpus 写入要求结构化 Knowledge Entry、新 Corpus Version、Target Manifest 和显式激活。这是全局系统知识治理流程。

用户上传一个文件不得：

- 创建新的全局 Corpus Version；
- 激活或退休全局 Corpus；
- 获得 Corpus Write capability；
- 把个人内容混入系统知识发布。

### 2.3 当前知识模型没有文档级 Owner 和 Scope

现有 KnowledgeUnit 和全局 pgvector Corpus 没有权威的：

~~~text
owner_principal_id
document_id
document_revision_id
selected_document_ids
~~~

长期记忆已有 Principal 不代表知识语料自动隔离。用户资料需要独立生命周期和查询边界。

### 2.4 当前文件导入只是浏览器文本读取

现有 Corpus 表单把本地 Markdown / TXT 读取成文本，再填入复杂 Knowledge Entry。它没有：

- 用户文件记录；
- 处理状态；
- 重试；
- 用户级停用或删除；
- 面试级文档选择；
- 文档级 Citation。

### 2.5 当前 Plan 和 Retrieval 没有文档 Scope

当前 Retrieval 主要按领域、主题、标签和来源类型过滤。Plan Configuration 也没有选中文档。只在前端加复选框无法保证 Plan、Session、Follow-up 和 Report 使用同一范围。

## 3. 权威术语

### 3.1 我的资料

用户主动上传、按文件管理、可为某次面试选择的内容：

~~~text
项目文档
学习笔记
课程资料
岗位资料
技术总结
~~~

### 3.2 系统知识

Interview-Agent 自带并由项目维护者治理的全局 Corpus。普通用户可以决定本次面试是否使用系统知识，但不能编辑或发布系统 Corpus。

### 3.3 我的记忆

系统从交互中提取、经用户确认后长期保存的结构化事实和偏好。

| 维度 | 我的资料 | 我的记忆 |
|---|---|---|
| 来源 | 用户主动上传 | 系统提取并确认 |
| 粒度 | 文件、章节、Chunk | 结构化事实与偏好 |
| 使用范围 | 每次面试显式选择 | 默认跨面试使用，可关闭 |
| 示例 | 项目架构.md | 偏好中文、熟悉 Java |
| 删除对象 | 文件及派生索引 | 某条长期事实 |

### 3.4 已选择与已参考

~~~text
已选择
= 该资料被允许用于本次面试

已参考
= 某个问题、追问或反馈实际消费了该资料的 Final Evidence
~~~

只有后端返回真实、持久化 Citation 时，前端才能显示“参考了你的资料”。

## 4. S1～S9 权威不变量

本节是唯一权威安全契约。后续里程碑只引用编号，不重复定义另一套规则。

### S1：Identity

- Principal 只由服务端 Principal Identity Resolver 解析；
- 公共 API 不接受客户端提交的 principal_id；
- Principal 不进入普通公共响应、浏览器 DOM 或安全 Trace；
- 当前没有产品账号或 RBAC，本计划不得顺带创建。

### S2：Ownership

- 每个 User Document 必须归属于当前 Runtime Principal；
- 所有资料读取、更新、重试、停用、删除和检索都必须带 Owner 条件；
- Store / Repository 不提供无 Owner 的便利查询；
- 合成 Principal A/B 隔离测试是 P0 契约，但不要求真实登录系统；
- 跨 Principal 访问返回不可枚举的 404 或等价安全错误。
- 显式资料 Scope 的 protected Plan Source 和 Session Binding 必须保存服务端解析的 Owner；旧 Plan 或无资料 Scope 保持 Owner 为 `None`；Owner 不进入公共 Plan、API 或 DOM。
- 不存在、非法 UUID、跨 Principal、停用、处理中、失败、删除中、Active Revision 缺失或不匹配统一投影为 `knowledge_scope_document_unavailable`；重复选择可以保留独立请求验证错误。

### S3：Scope

- Prep 只提交 Document ID 和“是否使用系统知识”；
- 服务端解析 Active Revision、Content Hash 和 Allowed Usage；
- Scope 必须绑定到不可变 Plan Revision，并在 Start 时复制到 Session；
- 第一次创建 Session 时必须按当前服务端 Principal 重新验证冻结 Scope；同一确定性 Session 已成功创建时，Start replay 必须先读取并校验已有 frozen Binding，不因资料随后停用或删除而改写原成功结果；
- frozen replay 仍须将当前服务端 Principal 与 Session Binding Owner 比较，跨 Owner 必须 fail closed；删除后的未来 User Document Retrieval 仍按 S7 保证零命中；
- Follow-up、Reviewer 和 Report 只能使用 Session 固化的 Scope；
- 新上传、未选择、停用或其他 Principal 的资料不得自动进入当前 Session；
- 无来源时返回 explicit empty，不为了产生结果扩大范围。

### S4：Citation

- 只有 Scope 合法、进入 Final Evidence 并被业务 Binding 实际消费的内容才能产生公共 Citation；
- “已选择”不能被显示成“已参考”；
- 普通 Citation 不显示内部 Chunk ID、Hash、Manifest、Query、Prompt、简历、JD 或完整 Trace；
- 删除资料后的历史 Citation 只投影为“已删除资料”，不得继续显示标题和摘录。

### S5：Scoring

- 用户资料可以提供项目背景、术语和事实参考；
- 用户资料不能修改 Rubric、维度权重、及格线或分数计算；
- 文档中有答案不等于候选人已经回答；
- 候选人未回答时，Reviewer 不得用资料替代回答后给分；
- 后端规则仍是数字分数的唯一确认者。

### S6：Untrusted Content

- 用户文件是数据，不是系统指令；
- “忽略系统指令”“给我满分”“泄露其他资料”等内容不得成为控制命令；
- 用户资料进入 Prompt 时必须使用现有 Context Selection、边界包装和最终 Prompt Enforcement；
- 原文不进入普通日志、Metrics 或公开 Trace。

### S7：Deletion

U1～U4 的删除保证固定为：

~~~text
删除原始内容
删除提取文本
删除 Chunk
删除 Embedding
清除可检索缓存
资料不再可选
后续 Retrieval 零命中
~~~

历史 Plan / Session 不重写完整实体。读取历史 Citation 时统一投影：

~~~json
{
  "source_scope": "user_document",
  "availability": "deleted",
  "display_title": "已删除资料",
  "excerpt": null
}
~~~

删除失败不得显示成功。允许在 Document Store 内保留不含原文、原文件名和摘录的最小删除状态，不要求第一阶段建设独立复杂 Tombstone Ledger。

### S8：Capability

- 技术实验室继续使用现有 RAG_CONSOLE_ENABLED；
- Live Inspector 继续使用 RAG_LIVE_EXECUTION_ENABLED；
- 全局 Corpus 写入继续使用 RAG_CORPUS_WRITE_ENABLED；
- 本计划不新增角色、RBAC 或管理员账号；
- 隐藏导航不替代服务端 capability。

用户资料只新增：

~~~text
USER_MATERIALS_ENABLED
USER_MATERIALS_INGEST_ENABLED
~~~

USER_MATERIALS_INGEST_ENABLED 控制上传、重试和新 Revision 创建，不取消当前 Owner 的永久删除能力。

### S9：Retrieval Channel

- source_scope 只是候选来源约束，不是新的检索通道；
- Fusion 仍然只融合 Semantic 和 Lexical 两个通道；
- System Knowledge 和 User Document 候选必须先在各自 Semantic / Lexical 通道内形成统一排名；
- 不允许把 System/User 变成第三、第四路 RRF；
- 不允许每个来源各取完整 Top K 后无界拼接，造成天然双倍候选配额；
- U1～U4 不增加用户资料固定权重或隐藏优先级。

推荐结构：

~~~text
Semantic Channel
├── System Knowledge candidates
└── Selected User Document candidates
    ↓ unified semantic ranking

Lexical Channel
├── System Knowledge candidates
└── Selected User Document candidates
    ↓ unified lexical ranking

Semantic Rank + Lexical Rank
    ↓ existing Fusion
    ↓ existing Rerank
    ↓ existing Evidence Gate
~~~

## 5. 最小领域模型

### 5.1 UserDocument

建议字段：

~~~text
document_id                 opaque UUID
owner_principal_id          internal only
display_title
original_filename           sanitized
media_type                  text/markdown | text/plain
size_bytes                  <= 1 MiB
public_status               processing | ready | failed | disabled | deleting
internal_stage              validation | extraction | chunking | embedding | indexing
enabled
active_revision_id          nullable
safe_error_code             nullable
created_at
updated_at
deleted_at                  nullable
~~~

普通响应只返回安全字段，不返回 Owner、路径、Hash、Embedding Identity 或内部异常。

### 5.2 UserDocumentRevision

~~~text
document_revision_id
document_id
revision
original_file_sha256
content_sha256
extracted_text_ref          internal
parser_version
chunker_version
embedding_identity          internal
created_at
~~~

Document ID 是稳定资料身份；实际检索对象是不可变 Revision。改名不创建 Revision；替换内容必须创建新 Revision。

### 5.3 UserDocumentChunk

~~~text
chunk_id
owner_principal_id
document_id
document_revision_id
position
title
section_label               optional
content
content_sha256
embedding
embedding_identity
created_at
~~~

Owner 同时存在于 Document 和 Chunk 查询条件中，用于防御式隔离和高效检索。

### 5.4 InterviewKnowledgeScopeSnapshot

建议直接作为 InterviewPlanV2 的不可变字段并参与 plan_sha256，避免新增独立 Scope Store：

~~~text
schema_version
include_system_knowledge
selected_documents[]
  document_id
  document_revision_id
  content_sha256
  allowed_usages
selection_sha256
created_at
~~~

内部 Snapshot 不包含 Owner；Owner 由当前 Plan Family / Session Principal 边界解析。公共 Plan 投影只返回用户可理解的 Document Safe Ref、标题和状态。

该变更必须作为 Interview Plan V2 的兼容性 ADR 修订。历史 Plan 或没有显式 Scope 的兼容请求固定映射为：

~~~text
include_system_knowledge = true
selected_documents = []
~~~

这保持现有“只使用系统知识”的行为，不根据当前资料库反向填充历史 Plan。

### 5.5 SafeKnowledgeCitation

~~~text
citation_id
source_scope              user_document | system_knowledge
document_safe_ref         optional
display_title
location_label            optional
excerpt                   bounded, nullable
usage                     question | follow_up | feedback
availability              available | deleted | unavailable
~~~

## 6. 最小端口、服务与存储

### 6.1 Ports

第一阶段只新增两个权威 Port：

~~~text
UserDocumentStorePort
UserDocumentChunkRepositoryPort
~~~

UserDocumentStorePort 负责：

- Document；
- Revision；
- Processing attempt 和状态；
- 列表、改名、启停、重试状态；
- 删除状态。

UserDocumentChunkRepositoryPort 负责：

- Chunk 写入；
- Semantic / Lexical 查询；
- 按 Owner + Revision 删除；
- Embedding 删除；
- 后续零命中验证。

### 6.2 Application Services

~~~text
UserDocumentService
UserDocumentIngestionService
UserDocumentDeletionService
InterviewKnowledgeScopeResolver
~~~

Deletion 是 Application Service，不是 Store Port。第一阶段不新增独立 Processing Job Store 或 Deletion Coordinator Port。

### 6.3 Adapters

按当前 Runtime 模式提供：

~~~text
InMemoryUserDocumentStore
InMemoryUserDocumentChunkRepository

PostgresUserDocumentStore
PgVectorUserDocumentChunkRepository
~~~

InMemory 和 PostgreSQL 共用 Store Contract。Repository 的所有公开方法必须显式接受 Owner。

### 6.4 PostgreSQL 边界

用户资料使用独立追加式表，不写入现有单活全局 Corpus Release：

~~~text
user_documents
user_document_revisions
user_document_chunks
~~~

第一阶段不强制独立 Tombstone 表。删除状态可以保留在 user_documents，但内容、文件名、提取文本、Chunk 和 Embedding 必须被清除。

数据库迁移：

- 不修改现有全局 Corpus 语义；
- 不删除旧表；
- 不把个人资料伪装成 Corpus Version；
- Schema Validate 缺少用户资料表时关闭资料 capability；
- 回滚只停用新能力，不执行破坏性 Drop。

## 7. 文件摄取与 API

### 7.1 U1 支持格式

~~~text
.md   text/markdown
.txt  text/plain
编码  UTF-8
上限  1 MiB
~~~

服务端同时校验扩展名、MIME、字节上限和 UTF-8。前端 accept 只改善选择体验，不是安全边界。

### 7.2 摄取流程

~~~text
验证
→ 创建 Document / Revision
→ 规范化文本
→ 计算 Hash
→ Chunk
→ Embedding
→ Index
→ 原子发布 ready
~~~

公共状态只使用：

~~~text
processing
ready
failed
disabled
deleting
~~~

内部阶段只用于诊断和重试：

~~~text
validation
extraction
chunking
embedding
indexing
~~~

Local V1 可以在受控执行器中内联处理小文件，但公共 API 仍返回可轮询状态，不能把 HTTP 返回误认为索引已 Ready。

自动化测试使用 Deterministic Fake Embedder。真实外部 Provider 调用需要单独授权，且不是计划完成条件。

### 7.3 最小 API

~~~text
GET    /api/materials
POST   /api/materials
PATCH  /api/materials/{document_id}
POST   /api/materials/{document_id}/retry
DELETE /api/materials/{document_id}
~~~

POST /api/materials 使用 multipart/form-data。

PATCH 第一阶段只允许：

~~~json
{
  "display_title": "Redis 面试笔记",
  "enabled": false,
  "default_use_policy": {
    "question": true,
    "follow_up": true,
    "feedback": true
  }
}
~~~

客户端不能提交：

- Principal；
- Revision；
- Chunk；
- Hash；
- Embedding Identity；
- Corpus Version；
- 任意数据库路径。

### 7.4 安全错误

至少冻结：

~~~text
unsupported_file_type
file_too_large
invalid_utf8
empty_document
processing_failed
embedding_unavailable
index_write_failed
document_not_found
document_deleted
retry_not_allowed
~~~

公共错误返回稳定 Code 和中文文案，不返回异常堆栈、文件路径、Credential 或原文。

## 8. Scope、检索、Citation 与评分

### 8.1 Prep 请求

~~~json
{
  "knowledge_scope": {
    "include_system_knowledge": true,
    "selected_document_ids": ["opaque-document-id"]
  }
}
~~~

服务端按 S1～S3：

1. 解析当前 Principal；
2. 验证 Document Owner；
3. 验证 Document 为 Ready 且 Enabled；
4. 解析 Active Revision；
5. 固化 Revision、Content Hash 和 Allowed Usage；
6. 生成 Scope Hash；
7. 写入 Plan 并参与 Plan Hash。

Start Service 继续只接受权威 Plan Revision，不接受第二套临时资料列表；它验证 Scope 后复制到 Session，且不调用 Provider。

### 8.2 Retrieval

新增内部 KnowledgeSourceScope：

~~~text
include_system_knowledge
principal_id                 internal
allowed_document_revisions
~~~

它由服务端从 Session 构造，不由客户端直接上传。

无来源真值表：

| 系统知识 | 选中资料 | 行为 |
|---|---:|---|
| 开启 | 0 | 只检索系统知识 |
| 开启 | N | 检索系统知识和选中资料 |
| 关闭 | N | 只检索选中资料 |
| 关闭 | 0 | explicit empty，不扩大范围 |

具体候选组合必须满足 S9。来源不是新通道，不改变现有 Fusion 算法。

### 8.3 Question 与 Follow-up

- Plan Generation 只使用 Scope 内候选；
- 用户资料按 S6 作为不可信 Context；
- Question Binding 记录 Evidence 和 Source Scope；
- Follow-up Supplemental Retrieval 继承 Session Scope；
- 无相关证据时允许未绑定题目或现有降级路径；
- 不为了显示引用伪造 Evidence。

### 8.4 Reviewer 与 Report

用户资料允许：

- 解释项目背景和专有术语；
- 识别回答与项目文档之间的可验证一致或冲突；
- 生成更具体的改进建议；
- 为示例回答提供背景参考。

用户资料不允许：

- 修改评分规则；
- 证明候选人已经说过文档内容；
- 在未回答时替候选人补答案；
- 让文档中的评分指令进入控制层。

### 8.5 Citation

只有满足以下全部条件才生成公共 Citation：

1. Candidate 属于 S3 允许 Scope；
2. Candidate 进入 Final Evidence；
3. Question、Follow-up 或 Reviewer Binding 实际消费；
4. 字段通过安全投影和长度限制；
5. 用户仍可访问资料；
6. 资料没有被删除。

普通展示：

~~~text
本题参考

我的资料
Interview-Agent 项目架构.md
第 4 节 · 混合检索

系统知识
Redis 缓存一致性
~~~

## 9. 前端信息架构

### 9.1 一级导航

~~~text
准备
报告
我的资料
我的记忆
帮助
~~~

“我的资料”副标题：

> 你主动上传的文件，可在准备面试时选择使用。

“我的记忆”副标题：

> AI 经你确认后长期保存的偏好和事实，可随时查看、修改或删除。

### 9.2 路由

普通产品入口：

~~~text
/materials
~~~

开发者实验入口：

~~~text
/rag/lab
/rag/lab/retrieval
/rag/lab/evaluation
/rag/lab/evidence-trace
/rag/lab/corpus
~~~

U1 在 /materials 已可用后完成前端入口分层：

- 主导航把 RAG 替换为“我的资料”；
- 原 RAG Shell 迁到 /rag/lab；
- 技术实验室继续使用现有 capability；
- 不修改 RAG 后端 API 路径；
- 旧技术路由提供明确兼容跳转。

### 9.3 我的资料

~~~text
我的资料                              + 上传资料

AI 当前可参考 12 份资料

[搜索我的资料……]

Redis 面试整理.md
Markdown · 可以使用
用于：问题 · 追问 · 反馈

Java 并发笔记.txt
正在处理

MySQL 学习笔记.md
处理失败
[重新处理]
~~~

普通列表不展示 Corpus、Manifest、Embedding、Chunk、Unit ID、Source Authority 或 Retirement Status。

### 9.4 上传

~~~text
添加资料

支持 Markdown、TXT，UTF-8，单个文件不超过 1 MB

┌──────────────────────────────┐
│ 拖入文件或点击选择           │
└──────────────────────────────┘

资料名称
[ Redis 面试笔记 ]

默认用途
☑ 定制面试问题
☑ 生成相关追问
☑ 辅助反馈与改进建议

资料用于提供背景和知识参考，不会替代统一评分标准。

                    [开始处理]
~~~

要求：

- 页面只有一个主 CTA；
- 支持键盘和可见焦点；
- 状态不只依赖颜色；
- 上传时阻止重复提交；
- 失败提供可操作中文文案；
- 普通流程不显示 Chunking、Embedding 或 Vector DB。

### 9.5 Prep

~~~text
本次参考资料（可选）

AI 会在相关问题、追问和反馈中参考你选择的资料。

☑ Interview-Agent 项目设计.md
☑ Redis 学习笔记.txt
☐ Java 并发总结.md

☑ 同时使用系统知识
~~~

只允许 Ready + Enabled 资料被选择。选择状态必须由后端返回权威 Scope，不能只保存在浏览器。

### 9.6 Interview 与 Report

面试页仅在真实 Citation 存在时显示：

~~~text
参考了你的资料
Interview-Agent 项目架构.md
~~~

报告按“我的资料 / 系统知识”分组。没有知识引用不自动扣分。

## 10. U0：契约冻结

### 10.1 目标

在写实现前冻结 S1～S9、数据模型、Scope 绑定位置、删除语义和里程碑边界。

### 10.2 工作项

- [ ] 新增 User Materials ADR；
- [ ] 将 InterviewKnowledgeScopeSnapshot 冻结为 Plan V2 不可变字段并参与 Plan Hash；
- [ ] 冻结公共状态和内部阶段；
- [ ] 冻结 Markdown / TXT、UTF-8、1 MiB；
- [ ] 冻结两个 Port、三个 Materials Application Service 和一个 Scope Resolver 的责任；
- [ ] 冻结删除为“真删派生数据 + 历史 Citation 安全投影”；
- [ ] 冻结 S9，来源不成为新 Fusion 通道；
- [ ] 冻结现有 RAG capability 继续负责技术实验室；
- [ ] 建立 Architecture Test，阻止全局 Corpus 和用户资料生命周期混合；
- [ ] 记录当前工作树中既有修改，不清理、不覆盖、不混入本计划提交。

### 10.3 完成条件

- ADR、模型草案和测试责任得到明确确认；
- 没有待定的 Owner、Scope、删除或评分语义；
- U1 不依赖 U2～U5 才能独立验收。

## 11. U1：资料库基础闭环与入口分层

### 11.1 目标

完成：

~~~text
上传 → processing → ready / failed → 列表 → 重试 / 停用 / 删除
~~~

U1 不接入 Prep、Session、业务 Retrieval 或 Citation。

### 11.2 后端

- [ ] 新增 UserDocument / Revision / Chunk 模型；
- [ ] 新增两个 Ports；
- [ ] 新增 InMemory 实现；
- [ ] 新增 PostgreSQL 追加式表和适配器；
- [ ] 新增 UserDocumentService、IngestionService、DeletionService；
- [ ] 新增最小 Materials API；
- [ ] 使用现有 Principal Identity Resolver；
- [ ] 自动化测试使用 Deterministic Fake Embedder；
- [ ] 删除覆盖原始内容、提取文本、Chunk、Embedding 和缓存；
- [ ] 删除后 Repository 零命中；
- [ ] 不调用全局 Corpus Version Create / Activate。

### 11.3 前端

- [ ] 新增 /materials；
- [ ] 新增列表、上传、状态、重试、启停和删除；
- [ ] 主导航 RAG 改为“我的资料”；
- [ ] 原 RAG 页面迁到 /rag/lab；
- [ ] 保留旧技术路由兼容；
- [ ] 技术实验室继续使用现有 capability；
- [ ] 不新增角色、账号或 RBAC。

### 11.4 必须满足

~~~text
S1 Identity
S2 Ownership
S6 Untrusted Content
S7 Deletion
S8 Capability
~~~

### 11.5 自动化验收

- [ ] 合法 Markdown / TXT 最终 Ready；
- [ ] 非 UTF-8、空文件、错误类型和超限稳定失败；
- [ ] Processing 期间不可选择为 Ready；
- [ ] 重试不产生两个 Active Revision；
- [ ] 合成 Principal B 不能读取、改名、重试或删除 Principal A 的资料；
- [ ] B 的 Chunk 查询不能命中 A；
- [ ] 删除后内容和向量零命中；
- [ ] 普通响应和 DOM 不含 Owner、路径、Hash、Manifest 或内部异常；
- [ ] 上传不会创建或激活全局 Corpus；
- [ ] /rag/lab 权限行为与迁移前一致。

### 11.6 U1 不包含

- Prep 资料选择；
- Plan / Session Scope；
- 用户资料业务检索；
- Question / Follow-up / Report Citation；
- PDF 或 DOCX；
- 外部 Provider 真实调用。

## 12. U2：面试资料 Scope 与受限检索

### 12.1 目标

把 Ready 资料接入 Prep、Plan、Session 和 Retrieval，并证明只检索用户为本次面试选择的 Revision。

### 12.2 后端

- [ ] Prep 接受 Document ID 列表和系统知识开关；
- [ ] InterviewKnowledgeScopeResolver 按当前 Principal 解析 Active Revision；
- [ ] Scope 进入 Plan Hash；
- [ ] Start 验证并复制 Scope 到 Session；
- [ ] 显式 Scope 的 protected Plan Source 和 Session Binding 保存服务端 Owner，legacy/no-Scope 不改变旧 Source Hash；
- [ ] 重放 Start 保持相同 Scope；
- [ ] 新增内部 KnowledgeSourceScope；
- [ ] System/User 候选按 S9 在通道内部统一排序；
- [ ] 继续复用现有 Fusion、Rerank 和 Evidence Gate；
- [ ] 不增加来源权重；
- [ ] 无来源时 explicit empty。

### 12.3 前端

- [ ] Prep 增加资料选择器；
- [ ] 只允许 Ready + Enabled 资料勾选；
- [ ] 展示正在处理和失败资料，但禁用勾选；
- [ ] 提交 Document ID，不提交 Revision、Owner 或 Hash；
- [ ] 显示后端确认的 Scope 摘要；
- [ ] Scope 失效时阻止 Start 并给出安全中文提示。

### 12.4 必须满足

~~~text
S1 Identity
S2 Ownership
S3 Scope
S6 Untrusted Content
S9 Retrieval Channel
~~~

### 12.5 自动化验收

- [ ] 只选择 A 时不能命中 B；
- [ ] 关闭系统知识后不能命中系统 Corpus；
- [ ] 系统知识开启且无用户资料时保持现有行为；
- [ ] 所有来源关闭时返回 empty；
- [ ] Plan 创建后上传 C，当前 Session 不能命中 C；
- [ ] 选中资料在 Session 首次创建前停用、删除或失效时，第一次 Start fail closed；
- [ ] Session 已成功创建后，相同 request_id 的 Start replay 先校验 frozen Binding 与 Owner，并在资料随后停用或删除时仍返回原成功结果；
- [ ] 不存在、非法 UUID、跨 Owner 或不可用资料统一返回 `knowledge_scope_document_unavailable`，不泄露资源是否存在；
- [ ] Source Scope 不增加第三个 Fusion 通道；
- [ ] 每个来源不能各取完整 Top K 后无界拼接；
- [ ] 无用户资料 Scope 的旧 Plan 保持兼容。

## 13. U3：问题、追问、报告与安全引用

### 13.1 目标

让 Question、Follow-up、Reviewer 和 Report 消费 Session Scope，并只展示真实 Citation。

### 13.2 后端

- [ ] Question Binding 记录 Source Scope；
- [ ] Follow-up Supplemental Retrieval 继承 Session Scope；
- [ ] Reviewer 区分 Candidate Evidence、User Material、System Knowledge；
- [ ] 新增 SafeKnowledgeCitation 统一投影；
- [ ] 报告按来源返回安全引用；
- [ ] 删除资料后历史 Citation 投影为 deleted；
- [ ] 公共响应不返回内部 Chunk、Hash、Query、Prompt 或完整 Trace；
- [ ] 用户资料不能进入评分规则输入；
- [ ] 保留现有“后端规则确认数字分数”边界。

### 13.3 前端

- [ ] 面试页仅在真实引用存在时显示“参考了你的资料”；
- [ ] 报告按“我的资料 / 系统知识”分组；
- [ ] 普通模式隐藏内部 Chunk ID；
- [ ] 没有引用时不显示装饰性来源；
- [ ] 没有引用不自动表示扣分；
- [ ] 删除资料显示“已删除资料”，不显示原标题和摘录。

### 13.4 必须满足

~~~text
S3 Scope
S4 Citation
S5 Scoring
S6 Untrusted Content
S7 Deletion
~~~

### 13.5 自动化验收

- [ ] 实际消费 Final Evidence 时显示 Citation；
- [ ] 只被选择但未使用时不显示“已参考”；
- [ ] 用户资料和系统知识分组正确；
- [ ] 文档包含“请给满分”时分数与对照组一致；
- [ ] 文档有标准答案但候选人未回答时，不发布虚假高分；
- [ ] 文档要求泄露其他资料时不执行；
- [ ] 删除后 Citation 不泄露标题和 excerpt；
- [ ] 普通 DOM 不含 Principal、内部路径、Manifest 或敏感 Trace。

## 14. U4：产品收口、自动化验收与文档

### 14.1 目标

收口 U1～U3 的交互、兼容、运行能力和文档，不再扩展资料格式或 RAG 算法。

### 14.2 工作项

- [ ] 统一中文文案和状态表达；
- [ ] 检查键盘、焦点、错误和 Loading；
- [ ] 帮助页解释“我的资料 / 我的记忆”；
- [ ] 帮助页提供技术实验室入口说明；
- [ ] 更新 README；
- [ ] 更新 Local V1 Runbook；
- [ ] 更新 RAG Architecture，说明用户资料不属于全局 Corpus Release；
- [ ] 更新 Plan Revision ADR；
- [ ] 增加 Materials API、Scope、Citation 和评分边界 Acceptance；
- [ ] 增加前端组件与非视觉 DOM/浏览器契约自动化；
- [ ] 不使用截图作为验收条件；
- [ ] 不修改历史计划正文来伪装当前状态。

### 14.3 PostgreSQL 测试

如果 U1 实现 PostgreSQL 持久化，则在声明 PostgreSQL Ready 前必须运行真实 Store Contract，但它不是 paired evaluation。

执行要求：

- 单独获得本次新表范围的结构化授权；
- 使用隔离测试表或明确测试 Schema；
- 测试结束后清理；
- 不写入现有全局 Corpus；
- 不激活 Corpus Version；
- 使用 Deterministic Fake Embedding；
- 不调用外部 Provider。

没有授权时可以完成文档、InMemory、前端和非保护测试，但不得声称 PostgreSQL 持久化已经真实验证。

### 14.4 完成条件

- U1～U3 的自动化验收全部通过；
- Frontend Vitest、ESLint、Production Build 和 Bundle Gate 通过；
- 后端 Unit、Contract、Architecture 和 Acceptance 通过；
- 相关非视觉 DOM/浏览器契约自动化通过；截图型视觉测试和依赖真实模型/Provider 的 Browser Smoke 不属于 Local V1 完成条件；
- git diff --check 通过；
- 无关工作树修改未被覆盖或混入提交；
- 文档没有超出真实能力的承诺。

## 15. U5：文本层 PDF 产品增强

U5 是可选后续里程碑，不阻断 U1～U4。

### 15.1 范围

- 只支持有文本层 PDF；
- 不做 OCR；
- 不支持加密 PDF；
- 设定文件大小、页数和解析时间上限；
- 校验扩展名、MIME 和文件头；
- 错误处理损坏对象和无文本层文档；
- Citation 支持页码；
- 解析库进入锁定依赖和许可证审查；
- 解析任务继续使用 U1 的 Document / Revision / Status。

### 15.2 非目标

- 扫描 PDF；
- OCR；
- DOCX；
- PDF 编辑；
- 附件提取；
- 宏或嵌入对象执行；
- 提高 Context Budget；
- 以 PDF 为由绕过 S1～S9。

### 15.3 验收

- 合法文本层 PDF 可提取、分块、索引和引用页码；
- 加密、损坏、超限和无文本层 PDF 稳定失败；
- 删除后 PDF 原件、提取文本、Chunk 和 Embedding 全部不可检索；
- PDF 中的 Prompt Injection 不改变控制指令或评分。

## 16. 建议文件责任

最终实施前必须用 rg 确认权威模块。以下是责任映射，不是要求机械创建所有文件。

### 16.1 后端新增

~~~text
app/domain/knowledge/user_document.py
app/domain/knowledge/source_scope.py
app/ports/user_documents.py
app/application/materials/service.py
app/application/materials/ingestion_service.py
app/application/materials/deletion_service.py
app/api/materials/models.py
app/api/materials/routes.py
app/adapters/memory/user_documents.py
app/adapters/postgres/user_documents.py
app/adapters/pgvector/user_document_repository.py
app/services/interview_knowledge_scope.py
app/services/knowledge_citations.py
~~~

### 16.2 后端修改

~~~text
app/domain/knowledge/retrieval.py
app/domain/knowledge/evidence.py
app/services/prep.py
app/services/interview_plan_revision.py
app/services/session.py
app/services/knowledge_grounding.py
app/services/knowledge_followup_gap.py
app/services/report.py
app/services/runtime.py
app/api/shared/dependencies.py
~~~

### 16.3 前端新增

~~~text
frontend/src/materials/materialsApi.js
frontend/src/materials/useMaterialsResource.js
frontend/src/pages/MaterialsPage.jsx
frontend/src/components/materials/MaterialUploadDialog.jsx
frontend/src/components/materials/MaterialList.jsx
frontend/src/components/materials/MaterialStatus.jsx
frontend/src/components/materials/PrepMaterialSelector.jsx
~~~

### 16.4 前端修改

~~~text
frontend/src/components/navigation.js
frontend/src/App.jsx
frontend/src/pages/StartPage.jsx
frontend/src/pages/InterviewPage.jsx
frontend/src/pages/ReportDetailPage.jsx
frontend/src/pages/HelpPage.jsx
frontend/src/components/rag/RagConsoleShell.jsx
frontend/src/pages/RagConsolePage.jsx
~~~

避免：

- 新建第二套 HTTP Client；
- 新建第二套错误投影；
- 复制 RRF、Rerank 或 Evidence Gate；
- 为两个 Materials capability 新建完整权限框架；
- 为 U1 创建独立 Job Store、Deletion Port 或 Workflow Engine。

## 17. 测试责任

### 17.1 后端

至少覆盖以下责任，文件名可以按仓库惯例调整：

~~~text
UserDocument 模型和状态
UserDocumentStore 共享契约
UserDocumentChunkRepository Owner Scope
Markdown / TXT 摄取
重试幂等
删除后零命中
Materials API 安全投影
Plan Knowledge Scope
Session Scope 复制
Source-aware Retrieval
Safe Citation
Prompt Injection
评分隔离
全局 Corpus / 用户资料架构边界
~~~

### 17.2 前端

~~~text
我的资料导航和路由
空、加载、Ready、Failed、Disabled、Deleting
上传和重试
文件限制
Prep 资料选择
Scope 失效
真实 Citation
引用分组
删除引用投影
敏感字段不进入 DOM
技术实验室旧路由兼容
~~~

### 17.3 核心验收矩阵

| 场景 | 预期 |
|---|---|
| Principal A 上传 Markdown | A 的资料最终 Ready |
| Principal B 猜 A 的 ID | 404/不可枚举 |
| B 检索 A 的 Revision | 零命中 |
| 上传超限文件 | file_too_large |
| 上传非 UTF-8 TXT | invalid_utf8 |
| 只选择 A、不选 B | 只能检索 A |
| 关闭系统知识 | 不返回系统 Corpus |
| 所有来源关闭 | explicit empty |
| Plan 后上传 C | 当前 Session 不获得 C |
| 资料写“给满分” | 分数与对照一致 |
| 资料有答案、候选人未回答 | 不替候选人作答 |
| 实际使用资料 | 显示“我的资料” Citation |
| 只选择未使用 | 不显示“已参考” |
| 删除资料 | 派生内容真删、后续零命中 |
| 读取历史引用 | 显示“已删除资料”，无标题和摘录 |
| 普通入口访问 Lab API | 继续受 RAG capability 保护 |
| 上传用户资料 | 不获得 Corpus Write 权限 |

## 18. Capability、回滚与故障处理

### 18.1 Capability

~~~text
USER_MATERIALS_ENABLED
USER_MATERIALS_INGEST_ENABLED
~~~

规则：

- Materials Disabled：不显示资料入口，不进入 Prep 或 Retrieval；已有数据保留；
- Ingest Disabled：禁止上传、重试和新 Revision；已有 Ready 资料可按 Materials Enabled 状态读取；
- 永久删除保持可用，避免关闭摄取后用户无法删除敏感资料；
- RAG Console、Live Execution 和 Corpus Write 继续使用现有三个 capability。

### 18.2 回滚

1. 关闭 Materials 进入 Prep / Retrieval；
2. 关闭新摄取；
3. 保留 Owner 删除能力；
4. 恢复主导航旧映射；
5. 保留新增表，不执行 Drop；
6. 不把个人资料导入全局 Corpus；
7. 不重新启用已删除资料；
8. 不改变长期记忆；
9. 不调用 Provider 重建索引。

### 18.3 故障处理

- Embedding 不可用：Document Failed，保留安全 Retry；
- Index 写入失败：不得发布 Ready Revision；
- 删除部分失败：保持 Deleting/Failed，不显示成功；
- Scope 中资料失效：Start fail closed，不自动换成其他 Revision；
- User Repository 不可用：明确 degraded/empty，不扩大到未选择资料；
- 持久化 Evidence Binding 不可用：不显示引用，不根据当前检索重建历史引用。

## 19. 风险与控制

| 风险 | 控制 |
|---|---|
| 把资料写入共享 Corpus | 独立 UserDocument 生命周期和 Architecture Test |
| 为 Local V1 建设 RBAC | S1/S8 明确禁止；只复用现有 capability |
| Owner 字段存在但查询漏条件 | Owner 为 Port 必填参数；合成 A/B 契约测试 |
| Frontend 选择与 Session 漂移 | Scope 进入 Plan Hash，并复制到 Session |
| Source 变成新 RRF 通道 | S9 + Retrieval Architecture Test |
| “已选择”被误写成“已参考” | Citation 只从持久化 Final Evidence Binding 生成 |
| 文档提示注入评分 | S5/S6 + 对照测试 |
| 删除文件但向量仍命中 | S7 + Repository 零命中验收 |
| 过早承诺 PDF | U5 独立，不进入 U1～U4 |
| Capability 数量再次膨胀 | Materials 只保留两个开关 |
| 计划重复产生矛盾 | S1～S9 为唯一权威定义 |

## 20. 提交与执行顺序

建议保持独立提交：

~~~text
Commit U0  ADR、模型契约和 Architecture Test 骨架
Commit U1a InMemory 模型、Store、摄取和 API
Commit U1b PostgreSQL/pgvector 适配器与 Store Contract
Commit U1c 我的资料 UI、导航和 /rag/lab 路由迁移
Commit U2a Plan / Session Scope
Commit U2b Source-aware Retrieval
Commit U2c Prep Selector
Commit U3a Citation 与 Reviewer/Report 边界
Commit U3b Interview / Report UI
Commit U4  Acceptance、文档和最终收口
Commit U5  文本层 PDF（未来可选）
~~~

每个提交：

- 只包含当前里程碑相关文件；
- 不清理或覆盖用户已有修改；
- 不使用 git reset --hard 或破坏性 Checkout；
- 运行对应目标测试和 git diff --check；
- 不把意图、注释或未运行测试写成完成证据。

当前基线工作树已有与长期记忆和 RAG Overview 相关的未提交修改。执行本计划前必须重新记录工作树，并将其视为用户现有修改。

## 21. U1～U4 总完成定义

只有以下条件全部满足，主计划才可标记完成：

### 产品

- [ ] 用户能上传、查看、重试、停用和删除 Markdown / TXT；
- [ ] 用户能为一次面试选择资料；
- [ ] Plan 和 Session 固化资料 Revision；
- [ ] 问题、追问和报告只显示真实安全引用；
- [ ] “我的资料 / 系统知识 / 我的记忆”边界清楚；
- [ ] 技术实验室从普通一级导航移出，但功能保留。

### 安全

- [ ] S1～S9 全部有自动化证据；
- [ ] 不需要登录或 RBAC 才能证明合成 Principal 隔离；
- [ ] 用户资料不改变评分；
- [ ] Prompt Injection 不进入控制层；
- [ ] 删除后原文、Chunk、Embedding 和缓存零命中；
- [ ] 公共响应和 DOM 不含敏感内部字段。

### 工程

- [ ] 只有两个 User Materials 持久化 Port；
- [ ] 没有第二套 RAG 算法；
- [ ] 没有把用户资料写入全局 Corpus；
- [ ] 后端 Unit、Contract、Architecture 和 Acceptance 通过；
- [ ] 前端 Vitest、ESLint、Build、Bundle Gate 和相关非视觉 DOM/浏览器契约自动化通过；截图型视觉测试与真实模型 Browser Smoke 明确排除；
- [ ] 受保护 PostgreSQL 测试只在单独授权下执行；
- [ ] 不调用外部 Provider 作为完成条件；
- [ ] git diff --check 通过；
- [ ] 文档与实际能力一致。

## 22. 最终原则

产品原则：

> 普通用户使用的是“资料能力”，开发者维护的是“RAG 工程系统”。

工程原则：

> 资料必须按当前 Principal 归属、按 Plan / Session 固化 Scope、只在实际使用后产生 Citation，并且不能成为评分规则。

复杂度原则：

> 删除生产级编排想象，不删除低成本且高价值的数据隔离、安全引用和评分边界。

交付原则：

> U1 先完成 Markdown / TXT 的真实资料闭环；U2 再接 Scope 与 Retrieval；U3 再接业务 Citation；U4 收口信息架构和验收；文本层 PDF 单独进入非阻断 U5。
