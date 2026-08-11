# Interview Agent 全量代码重构计划

- 文档版本：v1.89
- 文档状态：已完成（Wave A–H 与最终自动审查全部关闭；完整 Python/PostgreSQL、Frontend、十类 Browser、Residue 与 Audit Gate 已在最后一次生产代码修改后通过，Release 为 `ready`）
- 审查基线日期：2026-08-10
- 最近修订日期：2026-08-11
- 目标读者：项目维护者、后端与前端开发者、测试与发布负责人
- 文档类型：执行型重构计划
- 实施原则：先修复安全与证据可信度，再收敛基础设施，最后拆分业务模块并删除兼容层

## 文档修订记录

### v1.1 修订摘要

本版本根据 2026-08-10 当前代码复核结果修订，重点变化如下：

- 保留现有 Git NUL-delimited Status 输入，修复 Rename 与 Copy 的双路径语义；
- 将 Browser Runner 问题修正为解释器身份未固定，不再描述为仓库硬编码某个 Python 发行版；
- 复用现有 app/ports，不建立第二套 Ports 目录；
- 删除目标设计中的 app/domain/runtime，Runtime Reliability 归入 app/runtime 与现有 Port；
- Evidence 采用 Common Envelope、Domain Payload 与 Domain Policy，不建立万能 Payload Schema；
- Verification Status 与 Promotion Decision 分开建模；
- OwnedPostgresScope 提前到 Acceptance 与 Shadow 迁移之前；
- Reliability 核心 Contract 提前到 Session 与 Report 拆分之前；
- Session Repository 拆分增加 Unit of Work 与事务原子性 Gate；
- Context Artifact 改为边界抽取与 Adapter 收敛，禁止借重构改变现有 Identity、Lease 与兼容协议；
- 第一轮实施范围缩减为仅执行 P0 Safety，后续按 Wave 推进。

### v1.3–v1.15 执行状态修订

v1.3 不改变 v1.1 的目标架构与依赖顺序，重点持续修复“计划描述仍停留在实施前、无法反映当前代码状态”的问题。v1.4 进一步把计划修正为可持续执行、可逐项验证、可自动终审的任务账本，并清除“第一轮”与当前实施进度之间的矛盾。v1.5 根据 C-5 实际交付物和组合回归结果更新任务账本，修正 Stage 40/41 命名，并将当前执行切片推进到 C-6。v1.6 记录 C-6 的真实 PostgreSQL Permission、Target Identity、Ownership、Cleanup 与 Residue 契约结果，将 Wave C 转为已实现待终审，并把执行窗口推进到 Wave D。v1.7 对照外部复核原文再次校正执行状态与阶段名称：未修改 Wave D 生产代码前，D 保持“未开始”，D-1 明确为“下一执行切片”；清除 Phase 3 中残留的 Stage 41 误名；补充共享 PostgreSQL 测试支持的当前路径与目标路径；明确“已实现待终审”到“已完成”只能通过最终自动审查转换。v1.8 记录 D-1 Effective Runtime Config 落地：进程环境访问收敛到 `app/runtime/config/environment.py`，Config/Memory Config 的权威实现迁入 `app/runtime/config`，旧 `app/services/config.py` 与 `app/services/memory_config.py` 变为薄兼容出口；API、LLM、Embedding、Trace、Worker、Vector 与 LangGraph 调用点不再直接读取环境；显式 Mapping、严格数值、敏感输出与轻量导入契约已经补齐。v1.9 修正 D-2 的执行状态：`RuntimeContainer`、Lifecycle 基础设施和首批依赖缓存迁移已经开始，因此 D-2 不再描述为“下一执行切片”；当前实现处于新 Container 与旧 Shutdown/测试注入并存的迁移中间态，必须先恢复 Shutdown、Reset 和隔离测试，再迁移剩余 Singleton。v1.10 记录 D-2 完成：Shutdown/Reset 回归已经恢复，`app/services/runtime.py` 只保留根 Container，剩余实例、Started Flag、Metadata 和 Lock 已迁入 Container；关闭顺序、全资源清理、幂等 Shutdown、Closed 不可重开和 Reset 新容器均形成显式 Lifecycle Contract。v1.11 记录 D-3/D-4 完成：最小 Reliability 核心 Contract 已迁入 `app/runtime/reliability.py`，旧 `runtime_work.py` 作为具体异常映射兼容层；重复实现扫描、定向回归、完整 Python Suite 和显式批准的真实 PostgreSQL Gate 均通过，Wave D 转为已实现待终审，执行窗口推进到 Wave E。v1.12 对照外部复核意见与当前工作区再次修正执行账本：确认外部复核的 12 项架构意见均已映射到正文；记录 E-1 请求模型抽取已完成；把 E-1 拆成共享模型、依赖/错误入口、领域 Router、兼容调用点与回归 Gate 四个可独立验证的步骤；将已经关闭的 D-2 中间态失败明确标记为历史证据。v1.13 根据当前工作区修正 E-1 状态：共享依赖与错误映射、领域 Router 及生产组合入口已经落地，旧 `app/api/routes.py` 已缩减为 22 行 Deprecated Compatibility Facade；架构测试与各领域定向测试通过，但跨文件组合回归暴露 Runtime Memory Metric Store 的进程级测试隔离问题，因此 E-1d 保持进行中。v1.14 关闭 E-1d：Memory Metrics 端点测试在测试前后显式重置 Runtime Container，两个测试顺序均为 9 passed；E-1 组合回归为 148 passed、2 skipped、0 failed，OpenAPI 保持 39 个路径、45 个操作且无重复，执行窗口推进到 E-2。v1.15 完成 E-2：Interview Turn、SessionVersionConflict、Session Command 与状态规则迁入 `app/domain/interview`；新增 `SessionStateMachine`、`SessionCommandService`、`SessionSnapshotProjector`、`StreamingTurnService`、`InterviewStartService` 与 `InterviewApplicationService`；API Router 依赖现有 Port，不再直接编排 Legacy/Durable 命令、RoundClosed、Report Enqueue 或具体 Session Store。状态记录仅表示对应交付物已经落地并通过列出的测试，不代替第 21 节验收标准、第 23 节 Definition of Done 与第 24 节最终自动审查。

### v1.16 修订摘要

v1.16 再次逐条核对桌面复核文档的 12 项意见，确认 v1.1 的架构修订与“外部复核意见映射”仍然有效。当前编号已经将 OwnedPostgresScope 调整为 Phase 2、Acceptance/Shadow 调整为 Phase 3，因此“Phase 1 与 Phase 2 并行，Phase 3 依赖两者”正是复核要求的依赖关系，不得按旧编号误改。执行状态同步到当前仓库：E-3 已经开始，`UnitOfWorkPort`、`PostgresUnitOfWork`、Store Factory 和 Launch Coordinator 的 UoW 迁移已经落地，但 Cursor 关闭失败时的最终状态投影与异常权威性仍待修复和复验，PostgreSQL Repository 尚未拆分，E-3 继续保持未完成。

### v1.17 修订摘要

v1.17 根据当前工作区重新校准 E-3 账本。`PostgresUnitOfWork` 与 `DirectPsycopg2ConnectionProvider` 的 Commit、Rollback、Cursor/Connection 关闭失败和异常权威性已经修复并通过定向 Contract；Session、Message、Report、Question Evaluation、Runtime Outbox 与 Runtime Receipt 六个 PostgreSQL Repository 已经落地，旧 Session/Runtime Control Store 收敛为兼容 Facade，并保留 Session + Message + Outbox、Evaluation + Receipt 的事务组合能力。E-3 仍不得标记完成：Runtime Mutation 尚未全部改为 caller-owned Cursor/UoW，两个 Repository 聚合文件仍需按职责拆小，Session Store 中的 Schema/Migration 职责仍需迁出，且本轮没有获批的真实 PostgreSQL Scope，所有数据库环境 skip 均不构成真实数据库 Gate。

### v1.18 执行状态修订

v1.18 完成 E-3c 的 Runtime Mutation 与 Repository 文件拆分主体：Outbox Lease/Retry/Dead-letter、Receipt Claim/Retry/Finish 等写操作全部改为 caller-owned Cursor，`PostgresRuntimeControlStore` 通过单一 `PostgresUnitOfWork` 显式决定 Commit/Rollback；新增 Fault Injection 验证 Claim、Replay + Receipt Reset、Evaluation + Receipt、Lease Lost、Evaluation Conflict 与成功路径。原 894 行 Session Repository 聚合文件拆为 Message、Session、Report、Question Evaluation 四个模块，原 808 行 Runtime Repository 聚合文件拆为 Outbox、Receipt 两个模块；旧聚合路径仅保留 17 行和 13 行兼容重导出，并保持对象身份。E-3 尚未完成：Store 内 Schema/Migration 职责与真实 PostgreSQL SQL/事务/Cleanup/Residue Gate 仍待关闭。

### v1.19 执行状态修订

v1.19 完成 E-3c：`PostgresInterviewSessionStore` 与 `PostgresRuntimeControlStore` 不再包含 `CREATE TABLE`、`ALTER TABLE` 或 `CREATE INDEX`，原有 SQL 原样迁入 `PostgresSessionSchemaAdapter` 与 `PostgresRuntimeControlSchemaAdapter`；Store 在 `migrate` 模式委托 Adapter，在 `validate` 模式继续使用运行时关系验证，没有新增 Migration Port、没有修改表结构或序列化协议。本地 Schema Adapter、Provider Identity、Repository Architecture、UoW/Fault Injection、API、Launch、Report、Worker、Dispatcher、Consumer 与 Recovery 组合为 206 passed、1 skipped。E-3 只剩显式批准的真实 PostgreSQL SQL/事务/Cleanup/Residue Gate；环境未配置时不得进入 E-4。

### v1.20 执行状态修订

v1.20 在等待真实 PostgreSQL Scope Approval 期间并行关闭 Wave H Browser Runtime：新增单一 `python_runtime.js`，按 canonical env、兼容 env、Virtualenv、Workspace Venv、Windows Launcher、`python3.11` 与 PATH 顺序解析候选；每个候选必须同时满足 Python 3.11、realpath/executable identity、FastAPI/Uvicorn 可用，并由同一解释器执行 `scripts.reproducibility_preflight --python-only`。普通 Runner、Browser Preflight、Playwright Backend Wrapper 与 Real-model Smoke 均复用该 Helper，不再直接使用 `STAGE41_PYTHON || "python"`。Browser Preflight 已验证 Node 22.21、Python 3.11.3、Playwright/Chromium 和 Runtime Preflight 一致；权威 Runner 页面切片为 3 passed，退出后 8011/4173 监听端口为零。当前 Browser 配置仍收集 204 个串行项目用例，完整运行曾在无失败输出时被人工终止，不构成通过证据；Suite 合并去重仍属于 Wave H 后续任务。

### v1.21 执行状态修订

v1.21 删除 Playwright 的整套 desktop/mobile Project 重复：权威 Suite 只保留一个 Chromium Project，移动端关键流程与 Memory Center 显式拥有 390×844 Viewport，已有 Prep、Interview、Report、Help、Accessibility Matrix 继续由各自唯一测试 Owner 负责。收集数从 204 降至 102，完整 Browser Gate 为 101 passed、1 skipped、0 failed；唯一 skip 是显式 opt-in 的 Real-model Nightly Smoke。Browser 结束后 8011/4173 监听端口为零，`test-results` 临时产物已删除；Frontend ESLint、Vite Production Build、Bundle Budget 与 Lazy-route Gate 全部通过。17 个 Spec 文件合并为第 18 节十类权威 Suite 仍未完成，不能把 Project 去重冒充整个 H-2 完成。

### v1.22 计划一致性修订

v1.22 不改变外部复核已经确认的目标架构和 Wave A–H 依赖，只修复执行账本的可读性与当前状态：顶部状态明确区分 E-3 的本地完成项和仍需外部 Scope Approval 的真实 PostgreSQL Gate；Wave H 明确区分已经关闭的 Python Runtime/Project 重复和仍在进行的 17 个 Spec 到十类权威 Suite 合并；将 E-3 Schema Adapter 之前的 202 passed 与 Browser Project 去重之前的 204 项未完成运行标记为历史证据，避免与最新 206 passed/1 skipped、101 passed/1 skipped 结果混淆；Browser 可复现性条目同步当前单一 Runtime Resolver 的候选顺序、能力检查和 fail-closed 规则。外部复核的 12 项意见仍以第 3–24 节和“外部复核意见映射”为执行基线，不重新建立第二套 Ports、`app/domain/runtime` 或平行 Reliability Contract。

### v1.23 执行状态修订

v1.23 关闭 H-2 Browser Suite 收敛：17 个 Spec 在不减少 102 个逻辑测试的前提下合并为 Prep、Interview、Report Center、Report Processing、Report Detail、Memory Center、Recovery、Accessibility、Critical-path E2E 与 Real-model Nightly Smoke 十类权威文件。原 `prep-ui`、`interview-ui`、`reference-ui`、`durable-review-recovery`、`phase4-report-product`、`phase4-diagnostics-capability`、`help-ui` 与 `phase1-safety` 的行为已迁入对应 Owner，重复文件删除；Playwright `--list` 为 102 tests in 10 files。合并后的完整 Browser Gate 为 101 passed、1 Real-model opt-in skipped、0 failed，运行后 8011/4173 监听端口和 `test-results` 残留均为 0。H-1/H-2 已完成，Wave H 继续执行 H-3；由于后续生产代码或 Browser 相关修改会使本结果失效，最终审查仍必须重新运行完整 Gate。

### v1.24 执行状态修订

v1.24 开始 H-3：共享 Browser 几何、Session 与 Report Fixture 从已删除的 `reference-ui-geometry.js` 改名为中性的 `browser-suite-support.js`，十类权威 Suite 不再依赖 Reference UI 兼容命名；删除四个绑定旧 Commit/Tree、ahead/behind、固定测试数量、旧文件清单或原型 HTML 固定摘要的历史 Baseline/Publication/Reference Artifact 测试，并移除 `test_static_report_ui.py` 中读取 Browser 源码字符串和固定文件名的 Gate。历史文档和原型仅保留为非执行档案，不再阻塞当前重构。受影响 Python 文档/配置/Static Compatibility 批次为 65 passed；重命名后的共享 Browser Support 由 Accessibility Suite 覆盖全部 Helper 能力，6 passed，端口与 `test-results` 残留均为 0。静态 Memory Center 仍被现有 Browser 与 Contract 测试使用，必须等 Wave G 完成替代后再删除，不能在 H-3 提前误删。

### v1.25 执行状态修订

v1.25 完成 Phase 11 的结构化 Contract 基础：`contracts/requirements.yaml`、`decisions.yaml`、`tasks.yaml`、`gates.yaml`、`runbooks.yaml` 与 `releases.yaml` 成为稳定权威来源；`scripts/structured_contracts.py` 验证 Schema、全局 ID、交叉引用、Task DAG、Release Readiness 和禁止历史 Revision、Run ID、固定测试结果、机器路径等临时事实，并确定性生成 Acceptance Reference、Requirement Traceability、Execution Reference、Decision Reference、Runbook Reference 与 Release Contract 六份维护者文档。Mutation/引用/生成一致性 Contract 为 14 passed，`python -m scripts.structured_contracts --check` 为 PASS。Release 当前保持 `not_ready`，未完成 Task 存在时 Validator 禁止标为 `ready`；结构化 Contract 不替代最终真实 Gate 或 `docs/refactoring-audit.md`。

### v1.26 执行状态修订

v1.26 再次以 2026-08-10 桌面复核文档逐条校验目标架构，确认其中 12 项意见已由 v1.1 及后续版本完整吸收，不重复建立第二套 Ports、`app/domain/runtime`、万能 Evidence Schema 或平行 Reliability Contract。本次修订只校准当前执行账本：API Router、Interview Application、PostgreSQL Session Repository 与 PostgreSQL Runtime Repository 四个架构测试已从 `tests/` 根目录迁入 `tests/architecture/`；迁移时发现的仓库根路径层级错误已修复，Architecture 批次为 24 passed，完整 Pytest 收集为 2513 tests、0 collection errors。该结果证明首批测试分层可运行，不代表 H-3、Wave H 或完整重构已经完成；真实 PostgreSQL Gate、E-4、Wave F/G、剩余历史兼容清理和最终全量 Gate 状态均保持不变。

### v1.27 执行状态修订

v1.27 继续 H-3 测试分层：Runtime Reliability、Context Artifact、Browser Python Runtime、Memory Center UI、LangGraph Dual Release、LangGraph Runtime、Principal Memory Consumption、Principal Memory、Report 与 Stage 38 PostgreSQL API 共十个纯 Contract 测试迁入 `tests/contracts/`，配套的 Node Runtime Contract 同步迁入该目录；所有因目录层级变化而受影响的仓库根路径与脚本相对路径均已修复。迁移后 `tests/contracts` 为 137 passed、6 skipped，六个 skip 全部因为 `POSTGRES_DSN` 未配置，只证明本地收集策略，不构成真实 PostgreSQL Gate；完整 Pytest 收集仍为 2513 tests、0 collection errors。仍被 Acceptance 脚本或其他测试按旧模块名引用的 Contract 文件本批次不迁移，不新增兼容转发模块。

### v1.28 执行状态修订

v1.28 建立 `tests/acceptance/` 权威验收测试层，将 Agent Runtime、LangGraph、Local V1 Memory、Memory Shadow、PostgreSQL Acceptance Support/Capacity、Stage 38/43B/44A/44B1 与 Stage 48/49 Runner 共十七个验收文件迁入该目录；两个依赖 `__file__` 的测试已修正仓库根路径，Memory Shadow Release Preflight 的权威测试清单同步新路径，不保留旧路径转发模块。Acceptance 目录为 115 passed；受影响 Release Preflight 组合为 24 passed；完整 Pytest 收集仍为 2513 tests、0 collection errors，活动代码中的旧 Acceptance 路径引用为 0。该批次只完成测试分层，不代表历史 Stage 生产入口已经删除。

### v1.29 执行状态修订

v1.29 完成按文件职责可直接识别的 Contract 测试根目录清理：Agent Runtime Release、Durable Review Runtime、LangGraph Stage 47 Release、Question Memory Index、Stage 48 Release 与 UTF-8 Text 七个剩余 Contract 文件迁入 `tests/contracts/`；所有 Acceptance 脚本测试清单、跨测试 Helper 导入和当前 Browser Acceptance 文档路径均同步更新。`tests/` 根目录不再存在 `test_*contract*.py`，活动代码旧 Contract 路径引用为 0；Contract 目录最新为 170 passed、6 skipped，六个 skip 均为未配置 `POSTGRES_DSN`；Contract、Acceptance、相关调用方和文档组合为 328 passed、6 skipped，完整 Pytest 收集保持 2513 tests、0 collection errors。历史 Superpowers 计划中的旧路径作为非执行档案保留，不作为当前 Gate。

### v1.30 执行状态修订

v1.30 建立 `tests/integration/` 首批真实基础设施 Adapter 测试层，将 Context Artifact PostgreSQL、Memory PostgreSQL Validation、Draft Store、Memory Metrics、Principal Memory Consent/Control/Ledger、Runtime Control、Principal Memory Local Consume 与 Workflow Thread Lock 共十个无活动旧路径调用的文件迁入该目录。目录回归为 3 passed、33 skipped；33 个 skip 全部因为 `POSTGRES_DSN` 未配置，只证明本地非数据库分支与收集策略，不构成真实 PostgreSQL Gate。活动代码旧 Integration 路径引用为 0，完整 Pytest 收集保持 2513 tests、0 collection errors。仍被 Acceptance 脚本或其他测试作为 Helper 引用的 PostgreSQL 文件暂不迁移，后续批次必须同步调用方后再移动。

### v1.31 执行状态修订

v1.31 对照第 18 节目标目录修正 Integration 层级：上一版扁平的 `tests/integration/` 只是迁移中间态，现已统一收敛为 `tests/integration/postgres/`。新增迁移 Agent Runtime Metrics、Interview Launch、Principal Memory、Runtime Migrations、Session Store、Runtime Signal Metrics、Stage 48 Capacity 及五个 LangGraph/Review PostgreSQL 文件，并同步所有 Acceptance 测试清单和跨测试 Helper 导入；`tests/` 根目录 `test_*postgres.py` 与活动代码扁平 Integration 路径引用均为 0。PostgreSQL Integration 目录最新为 17 passed、134 skipped；全部 skip 都来自未配置 `POSTGRES_DSN`，不能替代显式批准的真实 PostgreSQL Gate。受影响调用方组合为 68 passed、24 个数据库环境 skip，完整 Pytest 收集仍为 2513 tests、0 collection errors。

### v1.32 执行状态修订

v1.32 建立 `tests/integration/providers/`，将 Embedding Providers、Real LLM Eval、Report Provider Adapter/Scoring、Runtime Provider 与 SiliconFlow Embeddings 六个 Provider Adapter 测试迁入目标目录，并同步 Stage 49 Acceptance Runner 的权威测试路径。Provider Integration 目录为 54 passed、1 skipped；唯一 skip 是显式 opt-in 的 Real LLM Eval，不属于关键基础设施 skip，Stage 49 Runner 受影响回归为 4 passed。PostgreSQL Integration 继续保持 17 passed、134 个未配置 `POSTGRES_DSN` skip；完整 Pytest 收集仍为 2513 tests、0 collection errors。`tests/test_provider_usage.py` 等纯策略测试和 PostgreSQL Provider Injection 单元测试不因名称包含 Provider 而误迁入 Integration。

### v1.33 执行状态修订

v1.33 建立 `tests/unit/` 首批快速隔离测试层，将 Context Artifact Privacy、Compression Gating、Embedding Config、Job Tags、Knowledge Query/Trace、Local Principal Runtime、Long-term Memory Decision Schema、Memory Quality/Fairness、Principal Memory Consent/Consume/Deletion/Extractor/Isolation/Privacy/Safe Refs、Question Memory Recovery、Report Eval Metrics 与 Runtime Recovery 共二十个无活动旧路径调用的文件迁入该目录。Unit 目录为 92 passed，活动代码旧 Unit 路径引用为 0，完整 Pytest 收集仍为 2513 tests、0 collection errors。文档/ADR 固定内容测试、历史 Stage 测试和混合 Unit/Integration 文件不因体积较小而误归入 Unit，后续按职责继续拆分。

### v1.34 执行状态修订

v1.34 删除 `tests/test_stage48_connection_baseline.py`：该文件一项固定断言历史文档中的 43 个 Direct Connect 调用点，另一项读取生产源码字符串并固定七个 Constructor Schema Setup 数量，均属于第 17–18 节禁止的历史数量/源码字符串 Gate。当前有效替代由 Stage 48 Release Contract、PostgreSQL Connection Domain/Provider 测试和显式 Migration 所有权测试承担；替代组合为 31 passed。删除两个历史测试后完整 Pytest 收集为 2511 tests、0 collection errors；测试数量减少是删除冗余 Gate 的预期结果，不得把旧 2513 固定为验收条件。

### v1.35 执行状态修订

v1.35 删除 125 个 Markdown/历史冻结测试：`test_local_v1_docs.py` 的 38 个 Stage 23–47 文案/命令子串 Gate；十个 ADR、Plan、Runbook、Approval/Production Spec 文件中的 73 个固定任务编号、状态与 Markdown 决策 Gate；Hosted V2 Readiness Audit、Local V1 RC Manifest 的 11 个固定文档/Revision/Hash Gate；以及 Principal Memory Causal Boundary 中三个读取产品文案、静态 HTML 和历史 Superpowers Plan 的测试。Markdown 与历史 Acceptance Record 继续作为非执行档案，当前 Runbook 命令和 Memory Shadow Release Preflight 所有权清单已移除失效测试路径。替代证据包括 Frontend/Static/UTF-8/Release/Structured 组合 55 passed，Contract/Acceptance/Unit 组合 377 passed、6 个数据库环境 skip，以及 Principal Memory 运行边界组合 32 passed。最新完整 Pytest 收集为 2386 tests、0 collection errors；剩余 Causal Boundary 测试只验证真实 Provider Sink、Plan/Report 禁止注入和失败关闭行为。

### v1.36 复核结论适用性修订

v1.36 重新对照 2026-08-10 外部复核原文与当前计划，确认复核提出的十二项修订已经分别落入 v1.1 的目标架构、正文第 3–24 节和下方“外部复核意见映射”，无需再次建立新 Phase、第二套 Ports、`app/domain/runtime` 或平行 Reliability Contract。本版补充复核结论的版本适用范围：外部复核给出的 `READY_WITH_REVISIONS` 只针对修订前的旧版 Plan；当前 v1.36 已完成这些计划层修订，可以继续作为执行基线。这里的“可执行”只表示计划结构与依赖已修正，不表示 Wave A–H 已全部完成，也不表示 Release Ready；实际实现状态仍以当前执行账本、结构化 Release Contract 和第 24 节最终自动审查为准。

### v1.37 执行状态修订

v1.37 继续 H-3 Markdown/历史 Evidence Gate 清理：从 Hosted V2 Productization、Long-term Memory Decision Packet、Budget Shadow Observe、Production Shadow Approval/Change、Staging Preflight 与 Principal Memory Data-use 七个行为测试文件中删除十个只读取操作文档、历史 Acceptance 或已提交 JSON，并固定命令子串、旧 Revision、固定测试数量、Pending 文案的测试。对应的生产 Policy、CLI 失败关闭、外部记录路径限制、严格 Payload、受保护 Evidence/Receipt、Ownership/Cleanup 与真实运行行为测试全部保留；受影响行为组合为 107 passed、1 个未配置真实 PostgreSQL 环境的 skip。完整 Pytest 收集按预期从 2386 降为 2376 tests、0 collection errors，`git diff --check` 为 0；测试数量减少来自删除冗余 Gate，不代表覆盖能力丢失。

### v1.38 执行状态修订

v1.38 删除三个重复的文案/精确字符串 Gate：Agent Runtime Release Contract 不再读取 `.env.example` 固定 Rollout 文案，也不再读取历史 Stage 47 Plan 判断 Stage 48 所有权；Reproducibility Preflight 不再固定 README、Local V1 Runbook 与 `package.json` 的命令子串。对应能力继续由 Effective Runtime Config 安全默认值、Stage 48 Repository/Migration/Capacity Contract、Browser Python Runtime Resolver 和 Reproducibility 运行行为测试覆盖。替代组合为 58 passed、1 个数据库环境 skip；完整 Pytest 收集为 2373 tests、0 collection errors，`git diff --check` 为 0。

### v1.39 执行状态修订

v1.39 完成 Stage 40 Artifact Audit 低层能力收敛：Stage 40 继续保留自身 `run_dir/file_count/total_bytes/files` Manifest 结构和原始字节 SHA-256，只复用共享 `ArtifactAuditError`、严格 JSON Reader、敏感内容 Scanner 与摘要原语；独立脚本由 114 行降到 82 行，并新增 CRLF 原始字节摘要兼容测试。审查 Stage 48 源码字符串 Gate 时发现 `build_report_executor()` 在缺少共享 Connection Domains 时仍请求 `schema_mode="migrate"`，现已修为始终 `validate`；新增行为测试与 Architecture AST Gate，禁止 Runtime 组合根拥有 PostgreSQL Migration 或 LangGraph `setup()`。删除 Stage 48 Contract 中四个被 Runtime/Migration/Canary/Capacity 行为测试替代的源码与 `.env` 字符串 Gate，并删除一个仍要求旧 API Facade 导入 `enqueue_report_if_needed` 的失效测试。Artifact Audit 组合为 64 passed；Runtime/Stage 48 组合为 81 passed、4 个数据库环境 skip；Report/API/Lifecycle 扩大回归为 156 passed、1 个数据库环境 skip。最新完整 Pytest 收集为 2371 tests、0 collection errors，结构化 Contract 和 `git diff --check` 均通过。

### v1.40 执行状态修订

v1.40 删除已被独立 Vite/React 六个产品路由完全替代的 `app/static/`：九个旧 JS/CSS 资源、`test_static_report_ui.py` 的十五个源码字符串测试、`test_static_memory_assistance.py` 的一个旧 Interview 字符串测试，以及根 `package.json` 的 `build:prototype-css` 兼容别名全部移除。Release Preflight 的六个退休 HTML 集合扩展为统一 `RETIRED_STATIC_ASSETS`，允许并验证 HTML 与静态资源删除，Memory Validation Acceptance 复用同一权威集合；UTF-8 Contract、前端指南、Memory Shadow Ownership、DESIGN 和 Interface Requirements 已同步当前 React 边界。`frontend/public/memory-center.*` 仍被 Browser/Contract 使用，本批未删除。React/Release/UTF-8/Acceptance 组合为 40 passed；Frontend ESLint 0 warning，Vite Production Build、Bundle Budget 与 Lazy-route Gate 全部通过；完整 Pytest 收集为 2355 tests、0 collection errors，结构化 Contract 和 `git diff --check` 均通过。

### v1.41 执行状态修订

v1.41 将根目录 `tests/test_react_frontend.py` 的十二个混合测试替换为 `tests/architecture/test_frontend_runtime.py` 的三个稳定 Architecture Contract。新 Contract 只验证独立 Vite/React Runtime、六个产品页面由 React 拥有且 FastAPI 不挂载 StaticFiles、退休 HTML/`app/static` 不得恢复；CSS Class、视觉 Token、动画实现、提示文案、设计文档短语和 JSX 精确源码不再由 Python 字符串 Gate 固定。真实页面行为继续由 102 tests in 10 Browser files 覆盖，构建能力继续由 ESLint、Vite Build、Bundle Budget 和 Lazy-route Gate 覆盖。Memory System Acceptance 与 Release Preflight 已同步新路径，活动代码旧路径引用为 0；Architecture/Release/Acceptance 组合为 51 passed，完整 Pytest 收集为 2346 tests、0 collection errors，结构化 Contract 和 `git diff --check` 均通过。

### v1.42 执行状态修订

v1.42 删除 Principal Memory Consumption Draft Spec/Risk Review 的七个 Markdown 短语 Gate，并将唯一有效的“通用 Consumption 未获授权”不变量迁入 `tests/architecture/test_principal_memory_consumption_boundary.py`。新 Contract 通过文件边界与 API AST 扫描确认不存在 Consumption Service、Port、Route 或 Consumer Getter，并以运行行为验证 `MEMORY_LONG_TERM_MODE=consume` 继续失败关闭；历史 Spec 与 Risk Review 作为非执行档案保留。Architecture/Config/Principal Memory 替代组合为 46 passed，旧 Contract 路径引用为 0；完整 Pytest 收集为 2339 tests、0 collection errors，`git diff --check` 通过。

### v1.43 执行状态修订

v1.43 将 `test_principal_memory_knowledge_firewall.py`、`test_principal_memory_prompt_isolation.py` 与 `test_principal_memory_consumption_isolation.py` 的十一个混合测试收敛为四个 Architecture AST Contract 和三个 Unit 行为测试。Architecture 层现在验证 Protected Sink Family 完整且不依赖 Principal Consumer/Fact Store、Public Knowledge 与 Principal Deletion 边界隔离、Consumer 只在 Durable Interview Follow-up 前 prepare/finalize；Unit 层验证 Fact 不可转为 Knowledge/Embedding、Knowledge Loader 拒绝 Principal Schema、Read Shadow 不修改 Provider Context。Release Preflight 必需路径已同步为单一 Architecture Owner，旧路径引用为 0；相关组合为 67 passed，完整 Pytest 收集为 2335 tests、0 collection errors，`git diff --check` 通过。

### v1.44 执行状态修订

v1.44 新增 `tests/architecture/test_runtime_boundaries.py`，统一拥有进程环境访问只允许 `app/runtime/config/environment.py`、旧 Config 模块只能薄重导出、Runtime 组合根只有 `_runtime_container` 可变状态、生产依赖不得引入本地 Sentence Transformer 四类架构边界。原散落在 Effective Config、Memory Config Source Audit、Runtime Container 与 Vector Store 的六个源码字符串/AST 测试迁入四个统一 Contract；Memory Config Source Audit 的另一个 Principal Shadow 字符串测试由现有 Runtime Isolation 与 Shadow 行为测试替代。Principal Identity 隐式身份字段检查改为运行时 Constructor Signature，Exclusive Fact Migration 源码顺序测试删除并由脏数据阻断/显式修复/幂等 Migration Integration 覆盖。Memory System Acceptance 已同步新路径，旧路径引用为 0；扩大组合为 96 passed、4 个数据库环境 skip，Acceptance 组合为 6 passed；完整 Pytest 收集为 2332 tests、0 collection errors，结构化 Contract 和 `git diff --check` 均通过。

### v1.45 执行状态修订

v1.45 将 Production Budget Readiness、Change Preflight、Observation、Window 与 Evidence Manifest 五个受保护 Evidence Runner 的重复源码字符串 Gate 收敛为 `tests/architecture/test_evidence_writers.py` 的统一 AST Contract。新 Contract 验证受保护 Runner 必须复用 `contracts.evidence`、不得直接调用 `Path.write_text()` 写机器证据、不得恢复已删除的本地 Evidence Symbol，也不得重新信任 `docs/memory-production*-evidence.json` 历史机器证据；正常的合同 Markdown 与审批 `.example.json` 输入不在禁止范围。五个原文件中的 CLI、Receipt、签名、严格 Payload、失败关闭和受保护链路行为测试全部保留。Architecture 与五个行为测试文件组合为 79 passed；完整 Pytest 收集为 2328 tests、0 collection errors；结构化 Contract 与 `git diff --check` 通过。该结果只推进 H-3 测试治理，不改变 E-3 真实 PostgreSQL Gate、E-4、Wave F/G、最终 Browser/Frontend/Python Gate 或 Release `not_ready` 状态。

### v1.46 执行状态修订

v1.46 继续 H-3 前端与源码字符串 Gate 收敛。`tests/test_frontend_phase5.py` 中七个绑定 JSX/CSS Selector、组件名、Import 文本、Hook 实现和 Bundle 脚本源码的精确字符串测试已经删除；唯一真实验证 Bundle Analyzer 对超预算、缺失 Route 与非 Lazy Route 失败关闭的运行测试迁入 `tests/contracts/test_frontend_bundle_budget.py`。Knowledge Retrieval Evaluation 的“源码不含 LLM 类名/方法名”测试迁为 `tests/architecture/test_knowledge_evaluation.py` 的 Import Boundary，禁止 Evaluation Runner 依赖 LLM、Agent 或 Report Runtime；原评估指标、Corpus Identity、隐私和 Artifact 行为测试保留。LLM Plan 中读取 `inspect.getsource()` 的实现细节测试删除，Structured Output 成功、Structured 失败后 Raw JSON 回退和无效 JSON 拒绝行为继续覆盖同一契约。替代 Python 组合为 21 passed；Frontend ESLint 0 warning，Vite Production Build、Bundle Budget 与 Lazy-route Gate 通过；完整 Pytest 收集为 2320 tests、0 collection errors；结构化 Contract 与 `git diff --check` 通过。Manifest、Dataset 与生成 Artifact 一致性测试不在本批删除范围。

### v1.47 执行状态修订

v1.47 继续 H-3 Unit 测试分层。Knowledge Evaluation V1/V2 CLI、Knowledge Evaluation Metrics、LLM Service 与 Principal Memory Causal Boundary 五个完全使用 Fake/In-memory、无真实 Provider 或数据库依赖的测试文件从 `tests/` 根目录迁入 `tests/unit/`。Principal Causal Boundary 的共享 Fake 导入已同步到 Unit 路径；Memory Shadow Release Preflight 的必需测试清单、Stage 18 Acceptance Log 与 Stage 3 SDD 当前路径已更新，活动代码旧路径引用为 0，不保留兼容转发模块。迁移组合为 54 passed；根目录测试文件由 196 降为 191，Unit 文件由 20 增为 25；完整 Pytest 收集保持 2320 tests、0 collection errors；结构化 Contract 与 `git diff --check` 通过。本批只改变测试所有权与目录，不减少测试数量，也不改变 Release Readiness。

### v1.48 执行状态修订

v1.48 完成 Context 测试首批职责分层。`ContextArtifact` Payload Schema 与 Context Compression Validation 两个稳定数据/验证契约迁入 `tests/contracts/`；Context Budget、Selection、Language、Compression Eligibility、Prep Context 与 In-memory Context Artifact Store 六个纯算法/内存测试迁入 `tests/unit/`。LangGraph Stage 49、Memory System Optimization 与 Memory Shadow Release Preflight 的权威路径清单，以及 Stage 42 Knowledge Agent 当前计划引用已经同步；活动代码旧路径引用为 0，不保留兼容转发模块。迁移与受影响 Acceptance/Release 组合为 98 passed；根目录测试文件由 191 降为 183，Unit 文件由 25 增为 31，Contract 文件由 22 增为 24；完整 Pytest 收集保持 2320 tests、0 collection errors；结构化 Contract 与 `git diff --check` 通过。Context Runtime、Compressor、Evidence/Interview Coordinator 等跨组件测试继续留待后续按职责拆分。

### v1.49 执行状态修订

v1.49 完成 Context 测试第二批职责分层。Context Compression Runner、Context Compressor Agent、Context Enforcement、Context Runtime、Evidence Context Artifact Coordinator 与 Interview Context Artifact Coordinator 六个跨组件但完全使用 Fake/In-memory、无真实 Provider、文件持久化或 PostgreSQL 依赖的测试迁入 `tests/unit/`。LangGraph Stage 49 与 Memory System Optimization Acceptance 的权威路径清单已同步；活动代码旧路径引用为 0，不保留兼容转发模块。迁移与受影响 Acceptance 组合为 54 passed；根目录测试文件由 183 降为 177，Unit 文件由 31 增为 37；完整 Pytest 收集保持 2320 tests、0 collection errors；结构化 Contract 与 `git diff --check` 通过。本批保持 Context Artifact Store Integration、真实 PostgreSQL 与文件 Artifact 测试原有层级不变。

### v1.50 执行状态修订

v1.50 完成 Knowledge 测试职责分层。Knowledge Corpus Schema、V1/V2 Evaluation Dataset、V1/V2 Manifest 与 Knowledge Coverage Profile 六个真实数据/Schema/Manifest 一致性文件迁入 `tests/contracts/`，没有删除 Artifact Contract；Grounded Knowledge Agent、Binding Resolver、V2 Metrics、Grounding、Ingestion、V1/V2 Loader 与 Static Knowledge Store 八个 Fake/纯算法文件迁入 `tests/unit/`。Interview Graph、PostgreSQL Session Store、Prep Service 与 Knowledge Trace 的共享 Helper 导入，以及 LangGraph Stage 49、Memory Shadow Release Preflight、Memory System Optimization 和 Stage 42 当前文档路径均已同步；活动代码旧路径引用为 0，不保留兼容转发模块。迁移与受影响调用方组合为 181 passed、26 个未配置 PostgreSQL 环境 skip；根目录测试文件由 177 降为 163，Unit 文件由 37 增为 45，Contract 文件由 24 增为 30；完整 Pytest 收集保持 2320 tests、0 collection errors；结构化 Contract 与 `git diff --check` 通过。26 个 skip 只证明本地收集策略，不替代真实 PostgreSQL Gate。

### v1.51 执行状态修订

v1.51 完成 Report 核心测试首批职责分层。Report Models、Evaluation Dataset、Evaluation Artifact Store、Replay Quality Fixture 与 PDF Output 五个稳定 Schema/Dataset/Artifact Contract 迁入 `tests/contracts/`；Rule Score、Report Quality、Reliability、Progress、Runtime Quality、Evaluation Case Builder/Replay/Runner/Evaluator、Microbatch 与 Runtime Preflight 十一个纯算法/Fake/In-memory 文件迁入 `tests/unit/`；真实 PostgreSQL Report Job Store 测试迁入 `tests/integration/postgres/`。LangGraph Stage 49、Dual Workflow Acceptance 与 Stage 3/4、Stage 41/42 当前文档路径已同步；活动代码旧路径引用为 0，不保留兼容转发模块。迁移与受影响 Acceptance 组合为 97 passed、17 个未配置 PostgreSQL 环境 skip；根目录测试文件由 163 降为 146，Unit 文件由 45 增为 56，Contract 文件由 30 增为 35，PostgreSQL Integration 文件由 23 增为 24；完整 Pytest 收集保持 2320 tests、0 collection errors；结构化 Contract 与 `git diff --check` 通过。Report Worker、API、Tasks 与真实 Provider 文件继续留待对应职责批次。

### v1.52 外部复核对齐修订

v1.52 逐条复核用户提供的 2026-08-10 桌面审查文本，确认其中十二项修订均已进入目标架构、Wave 依赖、验收标准和 Definition of Done。本版不重复创建 Phase，也不把已经完成的修订重新列为待办；新增复核适用范围规则：外部文本中的代码现状只代表重构前审查快照，当前实现状态以本版执行账本、仓库事实和最新有效测试证据为准；已经落地的意见转为防回归约束，尚未满足的真实 Gate 继续保留为未完成项。项目状态仍为执行中，Release 继续保持 `not_ready`。

### v1.53 执行状态修订

v1.53 完成 Report 第二批测试职责分层。Durable Enqueue、Orphan Progress Projection、Evaluation CLI 与 Report Task Microbatch 四个 Fake/In-memory 测试迁入 `tests/unit/`，Report Trace JSON 持久化测试迁入 `tests/contracts/`；五个文件均无活动脚本或当前文档旧路径引用，不新增兼容转发模块。迁移前后定向组合均为 24 passed；覆盖根目录、Unit、Contract、PostgreSQL Integration 与 Provider Integration 的全部 29 个 Report 测试文件为 224 passed、18 个未配置 `POSTGRES_DSN` 的 skip；根目录测试文件由 146 降为 141，Unit 文件由 56 增为 60，Contract 文件由 35 增为 36，PostgreSQL Integration 文件保持 24。完整 Pytest 收集保持 2320 tests、0 collection errors。本批次只改变测试所有权，不替代 E-3d 真实 PostgreSQL Gate，也不提前开始 Wave F 的生产代码重构。

### v1.54 执行状态修订

v1.54 将混合职责的 Report Worker 测试拆成两层，而不是整体误迁到 Unit。十六个 Fake/In-memory Worker、Lease、Heartbeat 与错误映射测试迁入 `tests/unit/test_report_worker.py`；真实 PostgreSQL Job/Report 测试迁入 `tests/integration/postgres/test_report_worker.py`；跨层共用的确定性报告对象提取到非测试 Helper `tests/report_worker_fixtures.py`。Stage 47、Dual Workflow Acceptance、PostgreSQL Dual Canary 和 Stage 41/42 当前文档路径已同步，Acceptance Runner 同时保留 Unit 与 PostgreSQL Worker Gate，活动旧路径引用为 0。拆分与调用方组合为 21 passed、7 个数据库环境 skip；全部 30 个 Report 测试文件仍为 224 passed、18 个未配置 `POSTGRES_DSN` 的 skip。根目录测试文件由 141 降为 140，Unit 文件由 60 增为 61，Contract 文件保持 36，PostgreSQL Integration 文件由 24 增为 25；测试总量没有变化。

### v1.55 执行状态修订

v1.55 完成 Report 子系统根目录测试归位。使用 In-memory Store 和 Fake Job/Vector 依赖验证公开 HTTP 生命周期的 Report API 文件迁入 `tests/acceptance/test_report_api.py`；使用 Fake/In-memory 依赖验证生成、质量、失败和持久化编排的 Report Tasks 文件迁入 `tests/unit/test_report_tasks.py`。Memory Shadow Release Preflight、Memory Report Jobs、Stage 18、Stage 4 与 Stage 42 当前引用已同步，活动旧路径引用为 0，根目录 `test_report_*.py` 为 0。迁移文件与直接调用方组合为 103 passed；覆盖六个目标层级的全部 30 个 Report 文件保持 224 passed、18 个未配置 `POSTGRES_DSN` 的 skip；完整收集保持 2320 tests、0 collection errors。根目录测试文件由 140 降为 138，Acceptance 文件由 17 增为 18，Unit 文件由 61 增为 62，Contract 文件保持 36，PostgreSQL Integration 文件保持 25。

### v1.56 执行状态修订

v1.56 完成一批跨子系统小型测试分层。Token Estimation、Trace Sanitization、Interview Assistance、Question Memory Retrieval、Durable Interview/Review State 与 Runtime Event 七个纯算法/In-memory 文件迁入 `tests/unit/`；Golden Evaluation Dataset、Memory Quality Dataset 与 Memory Plan Traceability 三个稳定 Dataset/Traceability 文件迁入 `tests/contracts/`；API-only Backend Page/CORS 行为迁入 `tests/acceptance/`。Stage 49、Dual Workflow、Memory Release/System Acceptance、Graph 测试、PostgreSQL Recovery、当前 Runbook 与相关调用方路径已同步；当前 Runbook 中已删除的 Static Report UI 命令替换为 Frontend Runtime Architecture Gate，历史 Acceptance Log 只更新移动后的路径。迁移前定向组合为 57 passed，迁移和直接调用方组合为 113 passed、10 个数据库环境 skip；活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors。根目录测试文件由 138 降为 127，Acceptance 文件由 18 增为 19，Unit 文件由 62 增为 69，Contract 文件由 36 增为 39，PostgreSQL Integration 文件保持 25。

### v1.57 执行状态修订

v1.57 完成 Runtime 测试职责分层。Effective Config、Container、Event Consumer、Lifecycle、Outbox Dispatcher 与 Runtime Work 六个 Fake/纯逻辑文件迁入 `tests/unit/`；Runtime Ports、Runtime/Redis Preflight、Signal Metrics Privacy Schema 与 Reproducibility Preflight 四个稳定协议文件迁入 `tests/contracts/`；公开 Runtime Boundary HTTP 行为迁入 `tests/acceptance/`。Stage 47/49、Dual Workflow、Memory Release Preflight、Stage 42 当前文档与历史 D-2 证据路径已同步，活动旧路径引用为 0。Reproducibility Contract 移动后暴露仓库根层级错误并已从 `parents[1]` 修正为 `parents[2]`；迁移与调用方最终组合为 141 passed、1 个 POSIX-only skip。完整收集保持 2320 tests、0 collection errors。根目录测试文件由 127 降为 116，Acceptance 文件由 19 增为 20，Unit 文件由 69 增为 75，Contract 文件由 39 增为 43，PostgreSQL Integration 文件保持 25。

### v1.58 执行状态修订

v1.58 完成 Agent 测试职责分层。原 Agent Recorder 混合文件按执行边界拆为 `tests/unit/test_agent_recorders.py` 与 `tests/integration/postgres/test_agent_recorders.py`，共享 Record Builder 提取到非测试 Fixture `tests/agent_runtime_fixtures.py`；Agent Runtime、Composition、Hardening 与 Agents 四个 Fake/纯逻辑文件迁入 `tests/unit/`，Agent Runtime Audit 与 Agent Trace 两个稳定协议文件迁入 `tests/contracts/`。Stage 47.2、Stage 48、LangGraph Dual/Stage 49、Memory Shadow Release Preflight 与当前 Runbook/计划中的活动路径均已同步，不保留旧路径转发模块；活动旧 Agent 路径引用为 0。迁移文件与直接调用方组合为 101 passed、3 个未配置 `POSTGRES_DSN` 的 skip；这些 skip 只证明本地收集策略，不构成真实 PostgreSQL Gate。完整收集保持 2320 tests、0 collection errors。根目录测试文件由 116 降为 109，Acceptance 文件保持 20，Unit 文件由 75 增为 80，Contract 文件由 43 增为 45，PostgreSQL Integration 文件由 25 增为 26。

### v1.59 执行状态修订

v1.59 完成 Session 测试职责分层。原 Session Deletion 混合文件拆为 `tests/unit/test_session_deletion.py` 与 `tests/acceptance/test_session_deletion_api.py`；Tombstone Replay、Session Report Store、Session Service 与使用 Fake Connection 验证 CAS/Outbox/UoW 的 PostgreSQL Session Repository 归入 `tests/unit/`，Session Serialization 与不连接数据库的 PostgreSQL Session Deletion Schema/Mapping Contract 归入 `tests/contracts/`，Streaming Report Enqueue HTTP/SSE 行为归入 `tests/acceptance/`。共享 LLM、Plan 与 Deletion Session Builder 提取到非测试 Fixture `tests/session_fixtures.py`，Memory Retention 不再跨测试模块导入 Helper。迁移前基线暴露的旧 `PostgresInterviewSessionStore` 手工构造测试已修正为直接验证权威 `PostgresReportRepository`，没有向兼容 Facade 重新添加已拆除状态。Memory System Optimization、Memory Shadow Release Preflight、Restore Drill 与当前 Stage 文档路径已同步；活动旧 Session 路径引用为 0。迁移和 Session 扩大组合为 75 passed、26 个未配置 `POSTGRES_DSN` 的 skip，直接调用方为 38 passed；完整收集保持 2320 tests、0 collection errors。根目录测试文件由 109 降为 101，Acceptance 文件由 20 增为 22，Unit 文件由 80 增为 85，Contract 文件由 45 增为 47，PostgreSQL Integration 文件保持 26。

### v1.60 执行状态修订

v1.60 完成 Interview 测试第一批职责分层。Interview Application Service、Event Stream、Graph、Round Transition、Workflow Consumer、Prep Service 与 Question Evaluation 七个 Fake/纯逻辑文件迁入 `tests/unit/`；Interview Launch Bootstrap Recovery 与 Interview Workflow Store 两个真实数据库文件迁入 `tests/integration/postgres/`。原 `test_interview_launch.py` 中被三个 PostgreSQL 文件跨测试导入的 `sample_plan` 已提取为非测试 Fixture `tests/interview_fixtures.py`，PostgreSQL Interview Launch 与 Draft Store 调用方同步使用共享 Builder。LangGraph Dual/Stage 49、Agent Runtime Stage 47.2、Memory Shadow Release Preflight 与当前 Stage 42 文档路径均已同步，不保留旧路径转发模块；活动旧 Interview 路径与旧跨测试 Helper 引用为 0。迁移和共享 Fixture 调用方组合为 71 passed、18 个未配置 `POSTGRES_DSN` 的 skip，Runner/Release 直接调用方为 34 passed；完整收集保持 2320 tests、0 collection errors。根目录测试文件由 101 降为 92，Acceptance 文件保持 22，Unit 文件由 85 增为 92，Contract 文件保持 47，PostgreSQL Integration 文件由 26 增为 28。Durable Interview Graph、Interview Generation Store、Interview Launch 与 Prep Question Regeneration 仍是 Unit/PostgreSQL 或 Unit/API 混合文件，继续留待下一切片拆分，本批不把 Interview 分层或 H-3 标记完成。

### v1.61 执行状态修订

v1.61 完成 Interview 第二批三个混合文件拆分。Interview Launch 的 In-memory Plan/Launch/Recovery 行为迁入 `tests/unit/test_interview_launch.py`，公开 Prep/Launch HTTP 组合迁入 `tests/acceptance/test_interview_launch_api.py`；Prep Question Regeneration 的 Plan/Context/CAS 行为迁入 Unit，公开 Regeneration HTTP 行为迁入 Acceptance；Interview Generation Store 的 Chunk Coalescer 迁入 Unit，八个真实 PostgreSQL Idempotency、Lease/Fencing、Cleanup 与 Schema/Index 场景迁入 `tests/integration/postgres/test_interview_generation_store.py`。Prep Context Plan Builder 与 In-memory Plan Builder 一并收敛到 `tests/interview_fixtures.py`，PostgreSQL Interview Launch 不再从测试模块跨文件导入 Helper；LangGraph Dual Runner 同时列出 Generation Unit 与 PostgreSQL Integration，保留两层 Gate。迁移和直接调用方组合为 22 passed、16 个未配置 `POSTGRES_DSN` 的 skip；包含上一批与仍待拆 Durable Graph 的 Interview/Prep 扩大组合为 107 passed、28 个数据库环境 skip；活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors。根目录测试文件由 92 降为 89，Acceptance 文件由 22 增为 24，Unit 文件由 92 增为 95，Contract 文件保持 47，PostgreSQL Integration 文件由 28 增为 29。Interview 当前只剩 `test_durable_interview_graph.py` 同时包含本地 Graph/Fault Injection 与四个真实 PostgreSQL 场景，必须在下一切片保留专用表 Cleanup 后拆分；本批仍不把 Interview 分层或 H-3 标记完成。

### v1.62 执行状态修订

v1.62 完成 Interview 测试职责分层。最后一个 Durable Interview Graph 混合文件拆为 `tests/unit/test_durable_interview_graph.py` 与 `tests/integration/postgres/test_durable_interview_graph.py`：八个本地 Graph、Heartbeat、Lease Loss 与 Fault Injection 场景归入 Unit；四个真实 PostgreSQL Generation、Retry、Fallback 与 Report Enqueue 场景连同专用表正则白名单、创建前后差集 Cleanup 和清理后 Residue 断言整体归入 Integration，没有降级为 Fake 或丢失资源终态检查。Agent Runtime Stage 47.2、LangGraph Stage 47、Dual Workflow 与 Stage 49 四套 Runner 均同时列出 Unit 和 PostgreSQL Integration 两层 Gate。拆分文件为 8 passed、4 个未配置 `POSTGRES_DSN` 的 skip，Runner 直接调用方为 17 passed；完整 Interview/Prep 权威组合保持 107 passed、28 个数据库环境 skip，根目录 Interview/Prep/Question Evaluation 测试文件为 0，活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors。根目录测试文件由 89 降为 88，Acceptance 文件保持 24，Unit 文件由 95 增为 96，Contract 文件保持 47，PostgreSQL Integration 文件由 29 增为 30。该结果关闭 H-3 的 Interview 测试分层子任务，但不代表真实 PostgreSQL Gate、H-3 其他子系统或 Wave H 完成。

### v1.63 执行状态修订

v1.63 开始 Memory 测试职责分层。Memory Config、Session Retention、Question Memory Coordinator、In-memory Question Memory Index 与 Memory Report Jobs 五个纯配置/Fake/In-memory 文件迁入 `tests/unit/`；不连接数据库、只验证表名和 Provider 注入的 PostgreSQL Question Memory Index 文件迁入 `tests/contracts/`。Question Memory Compressor Agent、State、Context 与 Coordinator Builder 从测试模块提取到非测试 Fixture `tests/question_memory_fixtures.py`，Question Memory Recovery 不再跨测试模块导入 Helper。Memory System Optimization Acceptance 的权威清单已同步，活动旧路径引用为 0；迁移文件、Recovery、Acceptance 与 Traceability 组合为 53 passed，完整收集保持 2320 tests、0 collection errors。根目录测试文件由 88 降为 82，Acceptance 文件保持 24，Unit 文件由 96 增为 101，Contract 文件由 47 增为 48，PostgreSQL Integration 文件保持 30。本批只关闭六个无歧义文件，不把仍待拆的 Memory Metrics HTTP 混合文件、Memory Evidence/CLI、Principal Memory 或整个 Memory/H-3 分层标记完成。

### v1.64 执行状态修订

v1.64 完成 Memory 测试第二批职责分层。原 Memory Metrics 混合文件拆为严格 Payload Privacy Schema Contract、In-memory Aggregate/Resilient Fallback Unit 和公开 Runtime HTTP Acceptance；Runtime Boundary 与 Metrics API 两种执行顺序分别为 9 passed 和 11 passed，未重新引入进程级 Runtime Container 污染。Memory Budget Shadow、Budget Observe、Cleanup Evidence、Production Budget Observation/Readiness/Window、Production Approval Packet、Production Change Preflight、Production Evidence Manifest、Publication Evidence 与共享 Shadow Evidence Support 十一个只验证严格 Payload、Policy、Receipt、受保护写入、CLI 失败关闭和隐私边界的文件迁入 `tests/contracts/`。Memory System Optimization Acceptance 同步 Metrics 三层 Gate，活动旧路径引用为 0；本批迁移与直接调用方组合为 117 passed，完整收集保持 2320 tests、0 collection errors。根目录测试文件由 82 降为 70，Acceptance 文件由 24 增为 25，Unit 文件由 101 增为 102，Contract 文件由 48 增为 60，PostgreSQL Integration 文件保持 30。真实 Restore/Staging Drill、其余 Memory Shadow/Release、Principal Memory 与 PostgreSQL 测试仍待分层，本批不把 Memory 或 H-3 标记完成。

### v1.65 计划基线一致性修订

v1.65 对照桌面复核文档重新核验全部十二项修订意见。目标架构、Phase 依赖与 Wave A–H 顺序继续沿用已经修正的 v1.1 基线，不新增第二套 Ports、`app/domain/runtime`、万能 Evidence Schema 或平行 Reliability Contract。本版修复第 2–3 节的时态歧义：其中列出的风险明确属于 2026-08-10 审查基线，不再以“当前仍存在”的口吻覆盖 v1.2–v1.64 已验证的实施结果；当前完成度只以执行账本、结构化 Release Contract 和最新 Gate 为准。外部复核的 `READY_WITH_REVISIONS` 继续只适用于修订前旧版 Plan；当前计划可以继续执行，但项目 Release 仍为 `not_ready`，Wave E、F、G、H、真实 PostgreSQL Gate 与最终自动审查的状态不变。

### v1.66 执行状态修订

v1.66 完成 Memory/Principal Memory 根目录测试职责分层。`make_fact`、Retrieval Builder、Active Fact Builder 与 Ledger Tombstone Builder 已提取到 `tests/principal_memory_fixtures.py`，Observability Evidence Bundle 与 Staging Declaration/RC Evidence 分别提取到独立非测试 Fixture，Memory/Principal Memory 不再跨测试模块导入 Builder。十五个纯 In-memory/算法/运行边界文件迁入 `tests/unit/`，十七个 Schema、Evidence、CLI、Ledger、Filesystem/Lock 与 Shadow Contract 文件迁入 `tests/contracts/`，Principal Memory HTTP API 迁入 `tests/acceptance/`；Staging Preflight 的二十个本地 Contract 与一个真实 PostgreSQL Migration/Cleanup 场景拆为 Contract 和 PostgreSQL Integration。活动 Runbook 路径已同步，根目录 Memory/Principal Memory 测试文件为 0，相关扩大组合为 566 passed、31 个环境 skip，完整 Pytest 收集保持 2320 tests、0 collection errors。根目录测试文件由 70 降为 37，Unit 文件由 102 增为 117，Contract 文件由 60 增为 77，Acceptance 文件由 25 增为 26，PostgreSQL Integration 文件由 30 增为 31。31 个 skip 不替代真实基础设施 Gate；H-3 仍有 Review/Durable Workflow、Vector/PostgreSQL 共享 Fixture、剩余根目录文件与历史兼容清理，不能标记完成。

### v1.67 执行状态修订

v1.67 完成 Review/Durable Workflow 根目录测试职责分层。Durable Review Graph、Workflow Maintenance、Dual Rollout、Canary Status、Review Workflow/Consumer、Round Review 与 Workflow Thread Lock 八个 Fake/In-memory/算法文件迁入 `tests/unit/`，Canary CLI Evidence 文件迁入 `tests/contracts/`，真实 PostgreSQL Review Workflow Store 迁入 `tests/integration/postgres/`。Fake Review Store、Round Review State、Canary Snapshot 与 Rollout Bucket Builder 已提取到三个非测试 Fixture；Durable Recovery、Dual Canary 和 Runtime Event Consumer 不再跨测试模块导入 Helper。LangGraph Stage 47/49、Dual Workflow、Agent Runtime、Memory Optimization 与 Release Preflight 六类活动 Runner 路径全部同步。迁移文件及直接基础设施调用方为 89 passed、24 个数据库环境 skip，Runner/Release 直接调用方为 49 passed；活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors。根目录测试文件由 37 降为 27，Unit 文件由 117 增为 125，Contract 文件由 77 增为 78，PostgreSQL Integration 文件由 31 增为 32。H-3 仍有 PostgreSQL/Vector 共享 Fixture、剩余跨测试导入、27 个根目录文件与历史兼容清理，不能标记完成。

### v1.68 执行状态修订

v1.68 完成 `tests/` 根目录测试清零。PostgreSQL Connection/Identifier/UoW/Provider Injection 与 Vector Fake 行为七个文件迁入 Unit，Capacity/Schema Adapter/PostgreSQL Harness 三个文件迁入 Contract，真实 PgVector 文件迁入 PostgreSQL Integration；Capacity Settings 与 Fake Embedding Provider 提取为非测试 Fixture，最后两处跨测试导入降为 0。随后 API 迁入 Acceptance，Draft/Event Publisher/Expert Evaluator/LLM Report/Orchestrator/Practice Plan/Provider Usage 七个文件迁入 Unit，Eval Snapshot、Hosted V2 Preflight、Local Runtime Init、Stage 40/42/44A/44B1 Artifact Audit 与 Stage 44B1 Corpus 八个文件迁入 Contract。活动 Runner 与当前 Stage 文档路径已同步；PostgreSQL/Vector 迁移组合为 95 passed、9 个数据库环境 skip，直接调用方 27 passed；最后一批为 185 passed，直接调用方 27 passed。根目录 `test_*.py` 和跨测试模块导入均为 0，完整收集保持 2320 tests、0 collection errors。最终目录为 Unit 139、Contract 89、Acceptance 27、PostgreSQL Integration 33。历史文档和 Release Preflight 中仍有二十处已删除根路径引用需要审计或替换，H-3 与 Wave H 继续保持进行中。

### v1.69 执行状态修订

v1.69 清理测试分层后的非冻结旧路径。Memory Release Preflight 删除已被 Frontend Architecture Contract 替代的 Reference UI Artifact 旧入口；Local V1 Runbook、Memory Acceptance、Stage 18/21/41/42、Expert Evaluation 与 Hosted V2 Ownership 文档改为当前 Unit、Contract、Acceptance、Provider/PostgreSQL Integration 和 Browser 路径。已退休的 Static UI/Docs 字符串 Gate 在历史验收记录中改为明确的“不可再运行的历史结果”，不伪造为当前命令。除明确冻结、只记录历史 Task 0 当时文件身份的 `docs/long-term-memory-production-execution-baseline.md` 外，活动代码、Contract、Runner、Runbook 与当前文档中的 `tests/test_*.py` 根路径引用为 0；Release/Runner 直接调用方 27 passed。H-3 的测试目录分层退出条件已关闭，但剩余历史 Stage、Static/Legacy Compatibility、冻结文档处置和最后一次完整 Gate 尚未完成，因此 H-3 与 Wave H 仍为进行中。

### v1.70 执行状态修订

v1.70 对照 2026-08-10 桌面复核原文、v1.1 已吸收的十二项架构修订和当前工作区，修复“历史兼容层仍被写成当前实现”的状态漂移。Stage 40/42/44A/44B1 四套 Artifact Audit 已合并为 `scripts/release_artifact_audit.py` 的四个 Profile，Stage 48/49 两套 Repository Acceptance Runner 已合并为 `scripts/repository_acceptance.py` 的两个 Profile；原六个重复脚本已删除。API、PostgreSQL Repository、Interview Error、Draft Store、Runtime Config 与 Memory Config 的调用方已迁入权威模块，`app/api/routes.py`、两个 Repository 聚合重导出、`app/services/session_errors.py`、`app/services/drafts.py`、`app/services/config.py` 与 `app/services/memory_config.py` 七个无生产调用者的兼容出口已删除；Architecture Contract 改为验证旧文件不存在和旧 Import 不可恢复。Config/Runtime/Memory/Provider 扩大回归为 171 passed，Stage Profile 与 Legacy 删除组合为 101 passed，`compileall` 通过；完整 Pytest 收集为 2321 tests、0 collection errors，结构化 Contract 与 `git diff --check` 通过。H-3 仍未关闭：冻结历史文档处置、剩余兼容语义审计、最终完整 Python/Frontend/Browser Gate 仍待完成；E-3 真实 PostgreSQL Gate、E-4、Wave F/G 和 Release `not_ready` 状态不变。

### v1.71 执行状态修订

v1.71 完成首轮剩余 Compatibility 语义审计并删除一个已证明无效的接口参数。`enqueue_report_if_needed()` 的 `background_tasks` 参数从未执行任务，唯一生产调用固定传入 `None`；现已删除 FastAPI 依赖、形参、调用参数和七个测试中的 `FakeBackgroundTasks` 冗余，完成访谈后的 Durable Queue、重复报告短路、队列失败投影与 Job Identity 行为保持不变。Report Enqueue、Interview Application、Session、API 与 Report API 扩大回归为 143 passed，完整 Pytest 收集保持 2321 tests、0 collection errors。审计同时确认三类入口尚不能删除：`runtime_work.py` 仍承载 Wave F 所需的领域异常到 Reliability Contract 映射并被三个生产消费者使用；pre-V15 Interview Launch 字段仍有公开 API 行为与依赖分支；`resolve_schema_mode()` 的 DSN-owned 默认迁移仍被多类 Adapter 构造契约使用。`app/runtime/config/compatibility.py` 当前是 39 个生产/测试模块使用的权威 Getter 集，不是无调用者 Facade；其公开 Getter 均有调用者。以上保留项分别交由 Wave F、Wave E 兼容退出策略和 Adapter 收敛阶段处理，不在 H-3 机械删除。

### v1.72 执行状态修订

v1.72 完成冻结历史计划的处置。新增 `docs/superpowers/plans/README.md`，把该目录定义为不可作为当前 Runbook、Release Gate 或命令来源的实现历史档案，并集中列出六个已退休 Stage Wrapper 到当前 Profile CLI 的映射；历史计划正文保留当时仓库事实，不为制造“当前可运行”假象而改写。根 README 已加入档案边界提示。排除历史计划目录和当前重构账本后，当前 README、DESIGN 与维护文档对已删除 Stage Wrapper、API/Config/Draft/Error/Repository 兼容模块的引用为 0。`docs/long-term-memory-production-execution-baseline.md` 继续保留为明确冻结的 Task 0 文件身份记录，不作为当前命令；至此“冻结文档处置”关闭。H-3 剩余项仅包括依赖 Wave F/G 替代实现的 Compatibility/Static Memory Center、最终完整 Python/Frontend/Browser Gate 和资源终态审查。

### v1.73 执行状态修订

v1.73 收敛 Stage 44A/44B1 Knowledge Acceptance 命令入口。两个 Profile 的 Corpus、Manifest、Ingestion、单/双 Dataset、Identity、Metrics、Artifact 与隐私 Gate 保持独立实现，不压成万能 Policy；重复的 Provider Metrics 白名单提取到 `scripts/knowledge_acceptance_support.py`，真实 Provider/Repository/Ingestor 组装、Opt-in 与 Embedding Safety 检查统一进入 `scripts/knowledge_acceptance.py`。唯一活动命令变为 `python -m scripts.knowledge_acceptance stage44a|stage44b1`。原 `scripts/run_stage44a_acceptance.py` 与 `scripts/run_stage44b1_acceptance.py` 重命名为非 CLI 的 Profile 实现模块，旧模块路径、活动代码与非历史文档引用为 0；Local V1 Runbook 和历史档案映射已同步。两个 Profile 与 Artifact Audit 为 50 passed，扩展到 Corpus、Manifest、Ingestion、Evaluation 与 Loader 的组合为 148 passed；完整 Pytest 收集保持 2321 tests、0 collection errors，结构化 Contract 与 `git diff --check` 通过。该结果只证明 Repository Profile 与安全入口，不替代显式批准的 Remote Embedding/PostgreSQL 真实 Acceptance。

### v1.74 计划修复摘要

v1.74 依据 2026-08-10 外部复核文本再次核对目标架构、Phase 依赖、Wave 顺序和当前执行状态。复核提出的十二项修订已经由 v1.1 及后续正文完整吸收：保留 Git NUL 输入并只修 Rename/Copy 双路径语义；把 Browser 风险定义为解释器身份不确定；复用现有 Port 且不新增 `app/domain/runtime`；Evidence 使用 Common Envelope、Domain Payload 与 Domain Policy；OwnedPostgresScope 先于 Acceptance/Shadow；Reliability 核心 Contract 先于 Session/Report；Session 拆分受 UnitOfWork/事务原子性约束；Context Artifact 只做兼容性边界抽取；全程按 Wave A–H 推进。因此本次修复不回退已完成切片，不恢复已删除兼容入口，也不按旧版 Phase 0–12 状态重开工作。

本版同时修复当前账本的遗漏：Stage 47 与 Stage 47.2 Acceptance Runner 收敛被明确列入 H-3。目标是建立单一 Profile Dispatch 与共享 Pytest 执行能力，同时保留两个阶段各自的 Check Group、输出 Schema、Ready/Blocked 状态和 Operator Observation 语义；禁止把不同阶段压成万能状态枚举，禁止用源码字符串测试代替行为 Contract。只有调用方、Contract、Runbook 全部迁移，旧模块活动引用为零，定向回归、完整收集、结构化 Contract 与差异检查通过后，才允许删除旧入口。Release 继续保持 `not_ready`，E-3 真实 PostgreSQL Gate、E-4、Wave F/G/H 和最终自动审查状态不变。

### v1.75 执行状态修订

v1.75 完成 Stage 47/47.2 Acceptance Runner 收敛。`scripts/repository_acceptance.py` 现在通过 `stage47`、`stage47_2`、`stage48`、`stage49` 四个 Profile 提供单一 CLI，并复用同一 Pytest 子进程结果投影；Stage 47 的四组 Check、逐组 Return Code/Duration、`langgraph-stage47-acceptance-v1` Schema、`READY_FOR_OPERATOR_FENCING_CANARY/BLOCKED` 状态与 Operator Observation 保持独立，Stage 47.2 的五组 Check、`agent-runtime-v1` Schema、PostgreSQL/Rollout Default 阻断和 `READY_FOR_AGENT_TELEMETRY_CANARY` 状态同样保持独立。迁移同时关闭旧 Stage 47 的伪 Ready 风险：未配置 `POSTGRES_DSN` 时两个 PostgreSQL Check 直接 `FAIL`，Runner 返回 `BLOCKED`，不再把数据库测试全部 skip 后的 Pytest 0 Exit Code 解释为 Ready。原两个 Runner 已删除，测试 Import、Stage 47 源码字符串 Gate、Local V1 Runbook 与冻结历史命令映射均已迁移；新增 AST Architecture Contract 禁止旧文件或旧 Import 恢复。Profile/Contract 定向批次为 23 passed，扩大组合为 215 passed、37 个未配置 PostgreSQL 的环境 skip；两个真实 CLI 无 DSN 运行分别返回 `BLOCKED` 与 `BLOCKED_POSTGRES_GATE`，完整收集为 2325 tests、0 collection errors，结构化 Contract 通过。该结果不替代真实 PostgreSQL Gate 或最终完整 Suite。

### v1.76 执行状态修订

v1.76 收敛 LangGraph Recovery 与 Dual Workflow 两个历史 Acceptance CLI。新 `scripts/langgraph_acceptance.py` 通过 `recovery`、`dual` 两个 Profile 提供单一命令入口，共享 Commit Identity、Artifact 目录创建、JSON/Markdown 写入和 CLI Dispatch；Recovery 继续使用 `langgraph-recovery-acceptance-v1`、十个 RPO/Privacy Check 与 `PASS/FAIL`，Dual 继续使用 `langgraph-dual-release-acceptance-v1`、Focused/Full Command Matrix、Repository/Operator 双状态和原有 Privacy Result，不合并两个领域 Schema。Recovery 同时改为失败关闭：未配置 `POSTGRES_DSN` 或 Pytest 没有任何真实 passed 用例时均为 `FAIL`，不再允许数据库用例全部 skip 后形成伪 PASS。原两个 CLI 已删除，测试迁入统一模块，冻结历史档案新增当前命令映射，AST Architecture Contract 禁止旧文件与旧 Import 恢复；当前维护文档原本没有这两个命令，因此无需制造新的 Runbook 兼容入口。Profile/Architecture 定向批次为 13 passed，直接调用方扩大组合为 135 passed、74 个未配置 PostgreSQL 的环境 skip，完整收集为 2329 tests、0 collection errors。以上只证明本地 Profile 与失败关闭，不替代真实 PostgreSQL Recovery/Dual Canary Gate。

### v1.77 执行状态修订

v1.77 删除仅剩测试绑定的旧 `memory_budget_shadow_acceptance.py`。该 Runner 仍以宽松 `int()`、`float()`、`bool()` 读取已提交 `docs/memory-budget-shadow-observation.json`，并把历史 Markdown PASS 文本作为第二信任源；当前 `memory_budget_shadow_observe.py` 已完整提供严格数值、显式布尔、`ShadowEvidencePayload/Policy`、签名 Receipt、`AtomicEvidenceWriter` 与写后 `EvidenceVerifier`，Local Runbook 也早已调用新 Observer。旧 Runner 和七个只绑定历史 JSON/Markdown 的测试现已删除，AST Architecture Contract 禁止旧模块恢复；Runbook 改为只接受 `reports/memory/budget-shadow-evidence-v1.json`，明确禁止把裸 JSON 复制到 `docs/` 或再次运行退休 Task 4 Gate。Operational Shadow 默认输出同时从 `docs/memory-operational-shadow-evidence.json` 修正为下游 Approval Packet 已使用的 `reports/memory/operational-shadow-evidence-v1.json`，并以行为 Contract 锁定生产者/消费者默认路径一致。相关 Observer、Evidence Support、Operational、Approval 与 Architecture 组合为 35 passed，完整收集为 2323 tests、0 collection errors。

本轮审计也纠正一个仍未关闭的 Wave C/H-3 状态漂移：`memory_operational_shadow_acceptance.load_default_bundle()` 仍读取多个历史 `docs/*.json` 和 Markdown PASS 文本，其中只有 Proposal Review 已通过签名 Evidence 验证。该路径不得继续被描述为“旧 plain JSON 信任路径全部删除”；下一切片必须优先迁移到现有 Budget/Write/Read/Lifecycle `ShadowEvidencePayload`、Restore Evidence 及其他受保护输入，并把所有输入加入 Receipt/Revision/Scope/Input Manifest 绑定。该 Finding 关闭前，Operational Shadow 收敛和 H-3 均保持未完成。

### v1.78 执行状态修订

v1.78 完成 Operational Shadow 历史输入迁移第一批。Budget、Principal Write、Principal Read 与 Lifecycle 现在必须是 `ShadowEvidencePayload`，Restore 必须是 `RestoreDrillEvidencePayload`；直接调用评估边界会按各自最小样本重新执行 Domain Policy，旧 Mapping、宽松数值和字段存在性判断不再受信。CLI 从 `reports/memory/` 的五个受保护 Artifact 读取输入，逐个验证 Receipt、Revision、固定 Scope、Payload 类型、重新计算的 Policy、Verification Status、Promotion Decision 与 Gate Codes；连同已有 Proposal Review，Operational 输出 `input_manifest` 从 1 项扩展为 6 项。错误 Revision 会以 `OPERATIONAL_INPUT_EVIDENCE_UNVERIFIED` 失败关闭，且不写输出 Artifact。Restore Drill Runbook 已同步权威 `reports/memory/restore-drill-evidence-v1.json` 路径，Operational Runbook 已记录六个输入、环境绑定和迁移边界。

本批 Operational 定向测试为 15 passed；关联 Observer、Evidence Support、Approval Packet 与 Architecture 组合为 37 passed；完整 Pytest 收集为 2325 tests、0 collection errors；结构化 Contract 与 `git diff --check` 通过。该结果只关闭 Budget、Write、Read、Lifecycle、Restore 五类输入迁移，不关闭整个 Finding：RC、Regression、Staging、Status 与 Security 仍从历史 JSON/Markdown 路径进入 `load_default_bundle()`，仓库中的历史 Manifest/审批材料也仍有旧 Evidence 路径引用。下一切片必须为这五类输入建立或复用严格 Payload/Policy 与签名 Bundle，再删除运行时历史信任路径；H-3、最终自动审查与 Release `not_ready` 状态保持不变。

### v1.79 执行状态修订

v1.79 完成 Operational Shadow 历史输入迁移第二批。新增统一的 `memory_operational_input_evidence.py` Profile CLI，为 RC、Regression、Staging、Status 与 Security 五类外部记录分别生成严格的 Domain Payload、Domain Policy、HMAC Receipt 与原子写入 Evidence；发布时必须提供预期 SHA-256、显式 Synthetic Attestation 和 Revision，宽松布尔、错误摘要、错误 Revision、Receipt、Scope、Payload 或 Policy 均失败关闭。`memory_operational_shadow_acceptance.py` 不再读取五个旧 `docs/*.json`/Markdown 输入，最终 `input_manifest` 由 6 项扩展为 11 项签名 Bundle。Staging Preflight 与 Foundation Acceptance 也已改为验证受保护 RC Bundle；Release Preflight 的必需路径已同步，旧静态 `docs/memory-production-shadow-evidence-manifest.json` 已删除，现行 Production Manifest 继续使用受保护 Bundle。

本批扩大组合为 143 passed、1 skipped；唯一 skip 是未配置 `POSTGRES_DSN` 的 Staging PostgreSQL Integration，只证明本地合同，不替代真实 PostgreSQL Gate。最新完整 Pytest 收集为 2336 tests、0 collection errors，结构化 Contract 为 PASS；相关 Python 文件编译通过，`git diff --check` 无空白错误。Operational 消费端第二批迁移已经关闭，但 Historical Trust Finding 尚未全部关闭：`memory_budget_shadow_observe.py` 尚未验证并绑定 RC/Staging 签名 Bundle，`memory_shadow_status.py` 的详细状态聚合上游仍读取 Budget、Write、Read、Lifecycle 与 Proposal 的历史记录，`memory-budget-shadow-runbook.md` 还保留两个已经失效的旧参数。下一切片必须先迁移 Budget Observer，再迁移 Status 聚合上游并扫描非冻结活动旧路径；H-3、最终自动审查与 Release `not_ready` 状态保持不变。

### v1.80 执行状态修订

v1.80 关闭 Operational Historical Trust 的剩余上游。Budget Observer 新增 RC 与 Staging 受保护 Bundle 输入，在任何 DSN 读取或数据库 Scope 打开之前验证 HMAC Receipt、Revision、固定 Scope、Payload 类型、重新计算的 Domain Policy 和 Artifact 状态；输出 Budget Evidence 的 Input Manifest 绑定两份输入的持久化字节与 Receipt 摘要，逻辑路径不泄露机器绝对路径。旧 `memory_budget_shadow.py` validate-only CLI、宽松裸 JSON Reader 和失效 Runbook 参数已删除，纯 Preflight/Stop Gate 逻辑继续由受保护 Observer 复用。

Status 生成器不再读取五个历史 Observation/Quality/Lifecycle 文件，改为验证 Budget、Write、Read、Lifecycle 四份 `ShadowEvidencePayload` 和 Proposal Review 领域 Payload；Budget/Write/Read 生产者的严格 Payload 已补齐详细三面板所需的低基数聚合指标，Status 在验证后的 Payload 上重建原有展示语义。错误 Receipt、Revision、Scope、Payload、Policy 或缺失指标统一以 `STATUS_INPUT_EVIDENCE_UNVERIFIED` 失败关闭并且不写状态记录。十个无活动消费者的历史机器 JSON/固定 Revision Staging Acceptance 已删除，相关源码/Markdown/已提交 JSON Gate 改为 Payload、CLI 与 Architecture 行为 Contract。本批核心组合为 83 passed，加入 Foundation/Staging/Operational 后的最终扩大组合为 106 passed、1 个未配置 `POSTGRES_DSN` 的 Staging Integration skip；最新完整 Pytest 收集为 2339 tests、0 collection errors，结构化 Contract 与 `git diff --check` 通过，18 个修改文件的严格 UTF-8/尾随空白检查无 Finding。Historical Trust Finding 的上述活动路径已关闭，但 H-3、Wave H、最终完整 Gate、最终自动审查与 Release `not_ready` 状态保持不变。

### v1.81 计划一致性修订

v1.81 根据 2026-08-10 外部复核文本再次校准“旧审查快照、当前实现账本、剩余执行项”三者的边界。复核提出的十二项修订已由 v1.1 及后续实现吸收，因此不按旧版 Phase 0–12 重开工作，也不恢复第二套 Ports、`app/domain/runtime`、万能 Evidence Schema、平行 Reliability Contract 或已删除兼容入口。当前仍有效的依赖保持为：E-3 真实 PostgreSQL Gate 必须获得显式 Scope Approval；Gate 关闭后才能进入 E-4；Wave E 关闭后才能进入 Wave F；Context Artifact 只做兼容性边界抽取，Session/Repository 拆分继续受 UnitOfWork 与原子性 Gate 约束。

本版补齐 H-3 的文档治理缺口：`docs/superpowers/specs/` 必须像 `docs/superpowers/plans/` 一样建立明确档案边界；当前 README、Runbook 与维护文档中的 `python -m scripts.*` 入口和 `tests/...` 路径必须由 Architecture Contract 验证可解析性。历史档案、明确 Frozen Baseline、`refactoring-plan.md` 中的历史修订记录，以及 Hosted V2 的未来 Recommended Ownership 不作为当前路径 Gate。该 Contract 只验证当前引用可解析，不固定 Markdown 文案、测试数量或旧阶段名称。以上是 H-3 的下一执行切片，不代表已经完成，也不改变 Release `not_ready`、E-3、E-4、Wave F/G 或最终自动审查状态。

### v1.82 执行状态修订

v1.82 关闭 H-3 当前文档路径治理。`docs/superpowers/specs/README.md` 已把十一份历史 Design Snapshot 定义为不可直接执行的档案，根 README 同时声明 Plan 与 Spec 两类 Superpowers 历史目录边界。新增 `tests/architecture/test_current_document_paths.py`，验证当前 README、Runbook 与维护文档中的 `python -m scripts.*` 模块和 `tests/*.py|js` 路径可解析；该 Contract 排除两类历史档案、两个明确 Frozen Execution Baseline、混合历史/目标路径的本计划和只描述未来 Recommended Ownership 的 Hosted V2 Readiness Audit，不读取或固定 Markdown 业务文案、测试数量和旧阶段名称。扫描覆盖 90 份当前文档、28 个唯一脚本模块和 56 个唯一测试路径，缺失模块与测试路径均为 0。

定向 Current Path、Evidence Writer 与 Repository Acceptance Architecture 组合为 8 passed，Architecture 全目录为 45 passed；最新完整 Pytest 收集为 2343 tests、0 collection errors，结构化 Contract、`git diff --check`、严格 UTF-8 与尾随空白检查通过。账本同时按 Architecture 防恢复清单纠正退休机器记录数量：实际为十七个，其中十个 Historical Trust 记录、六个额外无活动消费者记录，以及单独退休的 Production Evidence Manifest；此前“十六个”的统计遗漏 Manifest。以上只关闭文档路径子任务，H-3、Wave H、E-3 真实 PostgreSQL Gate、E-4、Wave F/G、最终完整 Gate 与 Release `not_ready` 状态保持不变。

### v1.83 执行状态修订

v1.83 完成 H-3 当前可执行范围内的剩余 Stage/Profile 与 Compatibility 所有权审计，没有为了减少文件数量删除仍有行为语义的入口。Stage 38 保留当前受保护 CLI、Runbook 和 Acceptance Owner；Stage 43B 继续被 Recovery Acceptance 与 OwnedPostgresScope Contract 使用；Stage 44A/44B1 Profile 继续作为统一 `knowledge_acceptance` CLI 的领域实现；15 行 `report_runtime_preflight.py` 只调用权威 Service、渲染 Result 和返回稳定 Exit Code，符合薄 CLI 约束并归 Wave F。以上行为组合为 28 passed。

已知 Compatibility 均有生产调用者和后续所有权：`runtime_work.py` 被 Interview/Review Graph、Report Worker、Runtime Consumer 与 Outbox Dispatcher 五个生产模块导入，归 Wave F Reliability Adapter；Runtime Config Compatibility 被二十三个生产模块使用；`resolve_schema_mode()` 出现在二十二个生产文件中，归 PostgreSQL Adapter 收敛；pre-V15 Launch 仍是公开 API 兼容分支，归 Wave E 退出策略。当前没有新的无调用者 Stage Wrapper 或可在 Wave F/G 前删除的 Compatibility 实现。H-3 剩余工作仅包括等待 Wave F/G 替代后删除对应兼容/Static Memory Center，以及最后一次相关修改后的完整 Python、Frontend、Browser 和资源终态 Gate；Release 继续保持 `not_ready`。

### v1.84 E-3 Gate 准备状态修订

v1.84 冻结 E-3 真实 PostgreSQL Gate 的当前权威矩阵，避免继续引用历史 C-6 的固定测试数量。缺少批准环境时，本地 Owned Scope、Stage 38/43B、Connection、Session/Runtime UnitOfWork 失败关闭组合为 56 passed；真实 `test_owned_postgres_scope_postgres.py` 为 4 skipped，唯一原因是 `POSTGRES_DSN` 未配置，该结果明确不构成 Gate。只收集、不执行的当前 PostgreSQL 标记集为 194 tests、2149 deselected，覆盖 `pg_runtime`、`pg_jobs`、`pg_control`、LangGraph Recovery/Review Recovery/Dual Canary/Fencing Canary；批准到位后必须在同一当前树上执行并达到 0 skipped，不能使用历史 207 项结果替代。

当前 `POSTGRES_DSN`、`POSTGRES_TEST_*` 五项 Scope Approval 元数据和 `POSTGRES_ACCEPTANCE_*` 五项 Acceptance Approval 元数据均未配置。Stage 38 Contract 已证明 Dry Run 不连接数据库，`--execute` 缺少受保护配置时在打开 Owned Scope 前失败；Approval Loader 在构造 Connection Provider 前验证 Approval ID、Receipt SHA-256、Approved Fingerprint、Database Allowlist 和带时区 Expiry。未获得用户显式批准前不得连接、查询或修改真实 PostgreSQL；E-3、E-4、Wave F/G 和 Release 状态保持不变。

### v1.85 E-3 真实 PostgreSQL Gate 修订

v1.85 将用户在当前任务中的明确授权物化为仓库外批准记录；仓库内未写入 DSN、密码或机器连接定位。批准目标的 Database Allowlist 为 `interview`，Approved Fingerprint 为 `5e025dd48cab1ffe94fb19b4837cafa66c247e323a1246cb2354f18ba3b0136e`，批准记录 Receipt SHA-256 为 `977184caf46f3c10fc92fbdbc50799ec12763bb67e3fc267e4f5917d22e72ead`。Gate 运行期只在单一受控子进程中注入连接配置和 Approval 元数据，批准过期后不得继续复用。

真实 Gate 首次执行暴露测试夹具的限权角色认证缺陷：临时角色没有独立密码，连接参数却继承管理员密码，而且认证失败发生在原清理边界之前。夹具已改为为每个临时角色生成独立随机密码、使用参数化 SQL 设置密码，并在限权连接中显式传入该密码；失败运行遗留的确切角色 `test_c6_denied_105dc88e126b` 已在验证身份后单独清理，未批量删除任何未知资源。

修复后 Critical Contract 为 4 passed、0 skipped、0 failed；当前 PostgreSQL 权威标记集为 194 passed、2149 deselected、0 skipped、0 failed；Connection、Launch、API、Report、Runtime Consumer/Dispatcher/Recovery 扩大组合为 223 passed、0 skipped、0 failed。标记集后及扩大组合后的独立 Catalog 审查均为 Relation Residue 0、Role Residue 0。E-3/E-3d 因此关闭，当前执行切片推进到 E-4；Wave E 仍为进行中，Release 仍保持 `not_ready`。

### v1.86 E-4 与 Wave E 关闭修订

v1.86 完成 E-4 Serialization 收敛。新增 `app/adapters/postgres/row_mappers/`，由 `SessionRowMapper`、`MessageRowMapper`、`ReportRowMapper`、`QuestionEvaluationRowMapper`、`PrepPlanRowMapper` 与 `MemoryPolicyRowMapper` 分别拥有行映射、Legacy Backfill Policy 和 Unsupported Version Error；生产 Repository 和 Prep Plan Store 全部迁入新 Mapper，旧 `app/services/session_serialization.py` 在活动调用者归零后删除，没有保留第二套并行序列化实现。

Session、Message、Report、Question Evaluation、Prep Plan 和 Prep Plan Version 的物理表新增 `row_schema_version`；新写入显式保存各自 v1 版本，`row_serialization_versions_v16` 迁移在同一事务内为旧行回填对应 v1，随后设为 `NOT NULL` 并纳入运行时 Schema Contract。现有 JSON 顶层 Shape 未包裹，Report Search、Plan Title、CAS、Expected Version、Fencing、Launch Atomicity 与 Transactional Outbox 的 SQL 可观察语义保持不变；任何显式未知版本统一失败关闭。

E-4 本地 Mapper/Schema/Repository Contract 为 57 passed、4 个未注入数据库环境的 skip；真实 Session/Launch/Migration 组合修复测试适配后为 50 passed；Wave E 真实扩大组合为 243 passed、0 skipped。完整 Python Gate 在最后一次 Wave E 生产代码修改之后为 2349 passed、3 skipped、0 failed；三个 skip 分别为两个 Windows 上不适用的 POSIX Contract 和一个显式 opt-in Real LLM Eval，没有 PostgreSQL skip。全量 Gate 后 Relation/Role Residue 均为 0，`git diff --check` 通过。期间将 Long-term Memory Decision Packet 的测试从“要求当前工作区与 HEAD 相同”改为 clean/dirty/untracked 三种确定性行为 Contract；生产 CLI 的真实冻结 Gate 未删除。E-4 与 Wave E 因此转为已实现待终审，下一执行窗口为 Wave F；Release 继续保持 `not_ready`。

### v1.87 Wave F 关闭修订

v1.87 完成 Report Pipeline 与 Runtime Reliability Adapter 收敛。原 `app/services/runtime_work.py` 中的具体 `RuntimeFailure`、`ErrorTaxonomy`、失败分类、重试退避和 Outbox/Receipt 状态迁入 `app/adapters/reliability/runtime_failure.py`，Interview/Review Graph、Report Worker、Runtime Consumer 与 Outbox Dispatcher 全部改用新 Adapter；旧模块在活动调用者归零后删除，Architecture Contract 禁止生产导入恢复。两个遗留错误码 `runtime_work_retry` 同步收敛为中性 `runtime_retry`，没有建立 `app/domain/runtime` 或第二套 Reliability Contract。

新增 `app/services/report_pipeline.py`，由 `ReportGenerationPipeline`、`ReportProgressProjector`、`QuestionEvaluationService`、`MicrobatchEvaluationService`、`FullSessionEvaluationService`、`ReportAssembler` 与 `ReportQualityPolicy` 分别拥有生成、进度、评估、组装和质量职责；`report_tasks.py` 从 322 行缩减为约 80 行入口/兼容编排，并委托单一 Pipeline。现有 `app/ports/runtime.py` 在同一 Ports 树内拆出 `ReportJobRepository`、`ReportJobLeaseAdapter`、`ReportRetryAdapter` 与 `ReportOrphanRepair`，兼容聚合 `ReportJobQueue` 继承四个细分 Port。`ReportWorker`、`ReportReliabilityProjector` 与 `ReportPdfRenderer` 成为对应职责的命名 Owner。

Report Scoring 新增并实际使用 `VersionedReportRubric` 与 `CURRENT_REPORT_RUBRIC`；当前 `stage40-rubric-v2` 显式定义适用维度、权重、Evidence 要求、Signal Point、Score Cap、Blocking Condition 与聚合规则，未知版本失败关闭。Report 测试增加 Runtime Container 前后重置，消除跨测试文件状态依赖。Reliability 定向回归为 90 passed，Report Pipeline/命名 Owner/Rubric 相关回归分别为 116、114 与 71 passed；全部 Report 文件真实 PostgreSQL 组合为 278 passed、0 skipped、0 failed。最后一次 Wave F 生产代码修改后的完整 Python + PostgreSQL Gate 为 2354 passed、3 skipped、0 failed；三个 skip 仍为两个 Windows 上不适用的 POSIX Contract 和一个显式 opt-in Real LLM Eval，没有 PostgreSQL skip。完整 Gate 后 Relation/Role Residue 均为 0，`compileall` 与 `git diff --check` 通过。Wave F 因此转为已实现待终审，下一执行窗口为 Wave G；Release 继续保持 `not_ready`。

### v1.88 Wave G 关闭与 Static Memory Center 替代修订

v1.88 完成 Principal Memory、Knowledge、Vector 与 Context Artifact 边界收敛。Knowledge 的 `KnowledgeChunk`、`KnowledgeQuery` 与重排算法迁入 `app/domain/knowledge/`；PgVector Repository 迁入 `app/adapters/pgvector/repository.py`，并通过实际使用的 `PgVectorCodec` 统一 Vector Literal、JSONB 与 Row Mapping。Embedding 与 Knowledge Repository 继续复用同一 Ports 树；旧 `app/services/vector_store.py` 及其活动 Import 已删除。`KnowledgeReleaseService` 成为语料发布的权威名称，兼容名称只指向同一实现，不建立平行 Service。

Context Artifact 领域契约迁入 `app/domain/context/artifacts.py`，`ContextArtifactIntegrityPolicy`、Purpose Contract、Payload Schema 与 Digest 校验由 Memory/PostgreSQL 两个 Adapter 共享；PostgreSQL 实现迁入 `app/adapters/postgres/context_artifacts.py`，Preview Profile 使用 `app/adapters/memory/context_artifacts.py` 的 Reference Adapter。计划原先写作 `ContextArtifactFilesystemAdapter`，但当前产品没有文件系统持久化 Profile；本版按真实运行 Profile 修正为 `ContextArtifactMemoryAdapter`，不为匹配旧名称虚构未使用的持久化语义，也不改变现有 Port、Artifact Key、Identity、Owner、Lease/Fencing、Completed Immutability 或 Replay Reuse。`ContextArtifactRecoveryService` 已成为 Durable Workflow 有界清理的单一应用边界。

Principal Memory Contract 与 Fact Lifecycle 迁入 `app/domain/memory/`，Memory/PostgreSQL Fact Store 分别迁入 `app/adapters/memory/principal_memory.py` 与 `app/adapters/postgres/principal_memory.py`；共享 Store Contract 同时验证 Proposal Dedup、Activation、Principal Isolation、CAS/Version Conflict 与 Purge。Consent、Control、Lifecycle、Selector、Context Renderer、Rights、Ledger 与 Shadow Observer 已建立权威命名 Owner，Runtime/API 使用权威名称；Memory 数据继续被 Architecture Gate 禁止进入 Report、Scoring、Public Knowledge、Embedding 与 Shared Retrieval。

Wave G 领域/架构扩大组合为 516 passed、1 个 Windows 上不适用的 POSIX-only skip、0 failed；真实 PostgreSQL Adapter 组合为 45 passed、0 skipped、0 failed，独立 Relation/Role Residue 均为 0。Static Memory Center 已由懒加载 React `MemoryCenterPage` 替代，旧 `frontend/public/memory-center.html/.css/.js` 与只固定静态源码字符串的 Contract 已删除；定向 Browser Gate 为 8 passed、0 failed。Frontend ESLint、Production Build、Bundle Budget 与 Lazy-route Gate 全部通过，初始 JavaScript/CSS gzip 分别为 67,332/67,584 与 10,432/20,480 bytes。Wave G 因此转为已实现待终审；这些是阶段证据，不能替代最后一次完整 Python/PostgreSQL 与十类 Browser Gate。Release 继续保持 `not_ready`。

### v1.89 最终自动审查与 Release Ready 修订

v1.89 按第 21、23、24 节完成逐项自动审查，并生成 `docs/refactoring-audit.md`。审查发现并关闭五类问题：Foundation Acceptance 的旧 Principal Memory 路径、Bundle 负向 Fixture 缺少 Memory Center 动态入口、Release Preflight 的旧 Required Path/Static Memory Center 防恢复缺口、生产 Runtime 内置带密码的 PostgreSQL DSN，以及 Release Ready 负向 Contract 隐式依赖未完成 Task 基线。PostgreSQL 配置通过 Windows 用户环境、源码默认删除、PostgreSQL 模式失败关闭和 Memory Preview 可选 DSN 完成修复；生产敏感字面量扫描降为 0。结构化负向测试改为显式构造一个未完成 Task，不再依赖仓库当前状态。

最后一次相关生产代码修改之后，Runtime/Config 定向回归为 77 passed；完整 Python + 真实 PostgreSQL Gate 为 2352 passed、3 个非数据库 skip、0 failed，PostgreSQL 项 0 skipped；独立 Relation/Role Residue 均为 0。Frontend ESLint、Production Build、Bundle Budget 和 Lazy-route Gate 通过；十类 Browser Suite 为 101 passed、1 个 Real-model opt-in skip、0 failed。Architecture/Structured/UTF-8 最终组合为 69 passed，`compileall` 和 `git diff --check` 通过，8011/4173、任务相关进程与 `test-results` 残留均为 0。

第 21 节和第 23 节审计表无 `BLOCKED`，所有 Finding 已形成修复与复验闭环。Wave A–H 因此统一转换为已完成，结构化 Task 转为 `completed`，Release 转为 `ready`。这里的 `ready` 只表示技术重构 Gate 完成，不代表已暂存、提交、推送、创建 PR 或正式发布。

状态只允许使用以下四个值：

- `未开始`：尚未修改生产实现；
- `进行中`：已经开始修改，但本 Wave 的退出条件尚未全部满足；
- `已实现待终审`：实现与定向测试已完成，仍需最终全量回归和逐条审查；
- `已完成`：退出条件已满足，并且该 Wave 已通过第 24 节的最终复验，不存在已知未关闭问题。整份计划只有所有 Wave 均完成第 24 节审查后才能标记为 `已完成`。

| Wave | 当前状态 | 已落地范围 | 进入下一阶段前仍需完成 |
|---|---|---|---|
| A | 已完成 | Stage 38 安全热修、Redis Probe Ownership、Staging 静态 Gate 与 Ownership、Release Rename/Copy 双路径安全、PostgreSQL 测试清理 | None |
| B | 已完成 | Common Evidence Envelope、Domain Payload/Policy、Receipt、Atomic Writer、OwnedPostgresScope、Migration Harness、Pytest Owned Scope Fixture；真实 PostgreSQL权限、目标、所有权、清理与残留契约已执行 | None |
| C | 已完成 | 受保护 Evidence 链、Receipt、Owned Scope、Acceptance、Shadow、Release/Cleanup/Publication 与 Profile 收敛 | None |
| D | 已完成 | Effective Runtime Config、Runtime Container、显式 Lifecycle、最小 Reliability Contract；PostgreSQL 配置失败关闭且无源码凭据 | None |
| E | 已完成 | API/Application/Domain、UnitOfWork、六 Repository、Schema Adapter、六 Row Mapper、物理 Row Schema Version 与 Atomicity | None |
| F | 已完成 | Runtime Reliability Adapter、Report Pipeline、四个 Report Job Port、Worker/Progress/Quality/PDF 与 Versioned Rubric | None |
| G | 已完成 | Principal Memory Domain/Adapter/Store Contract，Knowledge Domain/PgVector Adapter/Codec，Context Domain/Integrity/Memory+PostgreSQL Adapter，React Memory Center | None |
| H | 已完成 | Browser Runtime/十类 Suite、结构化 Contract、测试分层、历史档案边界、Stage/Profile/Compatibility 清理、最终全量 Gate、资源终态与自动审查 | None |

累计测试证据：

以下结果按执行时间累计保留，用于说明各切片曾经通过的范围；同一范围存在多次结果时，以最后一次相关生产代码修改之后的最新结果为当前证据。明确标为“历史”的结果不得覆盖较新的结果，也不得作为最终 Gate。定向测试数量不得相加后冒充完整 Suite。

- Wave A 定向回归：71 passed，1 skipped；
- Evidence Contract 最新定向批次：66 passed；
- OwnedPostgresScope：8 passed；
- Migration Harness 与现有迁移测试批次：67 passed，4 skipped；
- Owned Scope Fixture 相关批次：16 passed；
- Capacity Evidence、Stage 48 Consumer 与 Proposal Review 新契约定向批次：130 passed；
- Budget Shadow Owned Scope 与 Evidence 定向批次：71 passed；
- Staging、Write/Read Shadow、Lifecycle、PostgreSQL Validation 组合回归：52 passed，2 skipped；
- Stage 38 Strict Payload/Policy、HMAC Receipt、Owned Scope 与 API Fixture 定向批次：9 passed，3 skipped；
- 当前 Evidence Contract 与 Wave C 已迁移范围组合回归：133 passed，4 skipped；
- Operational Shadow 第一批历史证据：15 passed；关联 Observer、Evidence Support、Approval Packet 与 Architecture 组合：37 passed；
- Operational Shadow 第二批迁移后扩大组合：143 passed、1 个未配置 `POSTGRES_DSN` 的 Staging Integration skip；最终 Input Manifest 为 11 项签名输入；
- 最新完整 Pytest 收集：2336 tests、0 collection errors；该命令只验证收集，不冒充完整 Suite；
- Budget/Status Historical Trust 上游迁移扩大组合：83 passed；最新完整 Pytest 收集：2339 tests、0 collection errors；该结果不包含真实 PostgreSQL 执行，也不冒充完整 Suite；
- Budget/Status 与 Foundation/Staging/Operational 最终扩大组合：106 passed、1 个未配置 `POSTGRES_DSN` 的 Staging Integration skip；结构化 Contract、差异检查与修改文件 UTF-8/空白检查通过；
- Production Approval Request 最新定向批次：14 passed（包含 4 个 Operational Regression Policy 失败分支）；
- Proposal Review → Operational Shadow → Production Approval Request 受保护链路组合回归：105 passed；
- Production Budget Readiness：20 passed；
- Production Change Preflight：20 passed；
- Production Budget Observation：16 passed；
- Production Budget Window Decision：23 passed；
- Production Budget Acceptance：30 passed；
- Production Shadow Evidence Manifest：5 passed；
- C-4 Approval Request → Readiness → Change Preflight → Observation/Window → Acceptance → Manifest 受保护链组合回归：182 passed；该批次晚于 Approval Request 旧 JSON 信任路径删除与 Operational Regression Policy 补强；
- C-5 定向证据：Restore Drill 6 passed；Release Evidence 21 passed；Cleanup Evidence 3 passed；Publication Evidence 3 passed；Stage 43B Recovery 4 passed；Stage 49 Canary 4 passed；Stage 42/44A/44B1 Artifact Audit 48 passed；Stage 44A/44B1 Acceptance Runner 12 passed；Stage 40 与文档契约组合 41 passed；历史 Publication Contract 10 passed；
- C-5 最终组合回归：206 passed，0 failed；该批次覆盖 Evidence Contract、Restore、Release、Cleanup、Publication、Stage 40/42/43B/44A/44B1/49、Local V1 Docs 与历史 Publication Contract；
- C-6 Owned Scope 单元契约：8 passed；真实 PostgreSQL Permission、Target Identity、Ownership、Cleanup、Residue 与 Stage 43B Receipt 绑定：4 passed，0 skipped；
- C-6 真实 PostgreSQL 标记集：207 passed，0 skipped，2231 deselected；覆盖 pgvector、Runtime、Jobs、Control、LangGraph Recovery/Fencing 与 Capacity；
- C-6 受影响 Unit、Contract 与 Integration 组合回归：118 passed；共享 Scope/Producer 静态回归：22 passed；
- C-6 真实生产入口：Stage 38、Memory PostgreSQL Validation、Capacity Evidence、Stage 48 Repository Gate 与 Budget Shadow Profile B 均通过；Stage 48 五项检查全部 PASS；
- Wave C 最新完整 Python Suite：2435 passed，3 skipped，0 failed；三个 skip 分别为两个 POSIX-only 平台契约和一个显式 opt-in Real LLM Smoke，关键 PostgreSQL skip 为 0；
- D-1 类型化配置、API 与 Report 定向回归：150 passed；配置与服务扩展回归：140 passed，1 skipped；
- D-1 本地完整 Python Suite：2238 passed，210 skipped，0 failed；该运行未提供真实 PostgreSQL Gate 环境，因此不能替代 C-6 的 207 passed、0 skipped 真实 PostgreSQL 证据，也不能替代 D-4 最终真实基础设施回归；
- D-2 Container 基础契约：`tests/unit/test_runtime_container.py` 3 passed；该结果只覆盖 Container 自身，不代表 Runtime 迁移完成；
- D-2 迁移中间态历史失败（已关闭）：36 passed、14 failed；失败曾集中在旧 Shutdown 访问已迁入 Container 的模块变量，以及 Principal Memory 隔离测试注入已删除的旧全局缓存；其关闭证据为后续 63 passed、74 passed/4 skipped 与 301 passed/27 skipped 三组回归，不再属于当前阻断；
- D-2 恢复与 Lifecycle 定向批次：63 passed；API、Report、Recorder、Context 与 Runtime Port 扩展批次：74 passed、4 skipped；
- D-2 Runtime/Agent/Deletion/Context 扩大回归：301 passed、27 skipped；静态架构测试确认 `app/services/runtime.py` 只保留 `_runtime_container` 一个可变模块状态；
- D-3 Reliability Contract 与现有 Runtime/Report/Generation/Review 消费兼容批次：70 passed、26 skipped；核心契约不反向导入业务 Service；
- D-4 Config/Container/Lifecycle/Reliability 定向组合：101 passed、1 skipped；最新本地完整 Python Suite：2259 passed、210 skipped、0 failed；
- D-4 显式批准的真实 PostgreSQL 标记集：195 passed、0 skipped、2274 deselected；覆盖 Runtime、Jobs、Control、LangGraph Recovery/Fencing、Principal Memory 与 pgvector；测试前缀表残留 0、临时角色残留 0、专用容器残留 0；
- E-1 共享请求模型抽取后的 API 组合回归：128 passed、2 skipped、0 failed；该结果仅证明模型抽取兼容，不代表 Router 拆分完成；
- E-1 Router 架构契约：5 passed；生产入口不导入旧 Facade，领域 Router 不反向依赖旧 Facade；迁移期兼容重导出曾保持同一对象身份，调用方迁移后旧 Facade 已删除；OpenAPI 为 39 个路径、45 个操作且无重复操作；
- E-1 领域定向回归：API 39 passed、Report API 55 passed、Interview Launch 11 passed、Prep Regeneration 5 passed、Session Deletion 6 passed、Principal Memory API 9 passed、Runtime Boundary 4 passed、Memory Metrics 单独运行 5 passed、Report Orphan Recovery 3 passed、Streaming Report Enqueue 3 passed；
- E-1 跨文件组合回归历史失败（已关闭）：147 passed、2 skipped、1 failed；唯一失败是 Runtime Boundary 测试先初始化 `ResilientMemoryMetricStore` 后，Memory Metrics 测试仍调用仅测试 Store 提供的 `clear()`；
- E-1 测试隔离关闭证据：Runtime Boundary 与 Memory Metrics 两种文件顺序均为 9 passed；最新完整组合回归为 148 passed、2 skipped、0 failed；Browser Support App 为 46 个测试路径；
- E-2 Application/Domain/API/Session 核心组合：103 passed；新增 Application Contract 6 passed，架构 Contract 扩展后共 8 passed；
- E-2 Durable Graph、Workflow Store、Thread Lock 与 Generation Store 组合：24 passed、23 skipped；这些 skip 来自本轮未提供真实 PostgreSQL 环境，不能作为 E-3 Repository 或 E-4 Atomicity 的真实数据库证据；
- E-2 Runtime/API 架构组合：14 passed；OpenAPI 仍为 39 个路径、45 个操作、0 重复，Browser Support App 为 46 个路径；
- E-3 UnitOfWork 与 PostgreSQL Connection 定向批次：31 passed；覆盖显式 Commit/Rollback、Commit Failure、Cursor/Connection Close Failure、业务异常权威性、资源关闭与 UnitOfWork 不可复用；
- E-3 Repository Architecture、Session Repository、Runtime Repository、UnitOfWork 与 Connection 最新组合回归：41 passed；六个 Repository 公共出口、兼容 Facade 委托和本地事务 Contract 均可收集并通过；
- E-3c Schema Adapter 迁移前的历史扩大组合：202 passed、1 skipped；唯一 skip 为 `POSTGRES_DSN` 未配置，该结果已被下一条 206 passed、1 skipped 覆盖，不替代真实 PostgreSQL Gate；
- E-3c Schema Adapter 迁移后本地扩大组合：206 passed、1 skipped；Store 源码不再包含 Schema DDL，两个 Adapter 的本地 SQL/Provider Contract 为 2 passed；唯一 skip 仍为 `POSTGRES_DSN` 未配置；
- E-3d 真实 Gate：仓库外批准记录绑定 Database Allowlist、Approved Fingerprint 与 Receipt SHA-256；Critical Contract 4 passed、0 skipped；PostgreSQL 权威标记集 194 passed、2149 deselected、0 skipped；扩大组合 223 passed、0 skipped；两次独立 Relation/Role Residue 审查均为 0；
- E-4 Row Mapper/Schema Contract：57 passed、4 个无数据库环境 skip；真实 Session/Launch/Migration 组合 50 passed；Wave E 扩大组合 243 passed、0 skipped；最新完整 Python Gate 2349 passed、3 个非数据库 skip、0 failed，Relation/Role Residue 0；
- Wave F Reliability Adapter 定向回归 90 passed；Report Pipeline/命名 Owner/Rubric/Architecture 相关回归分别为 116、114 与 71 passed；全部 Report 文件真实 PostgreSQL 组合为 278 passed、0 skipped；最后一次 Wave F 生产代码修改后的完整 Python + PostgreSQL Gate 为 2354 passed、3 个非数据库 skip、0 failed，Relation/Role Residue 0；
- Wave H Browser Project 去重前的历史证据：Runtime Contract、Runtime/Reproducibility Preflight 33 passed、1 POSIX-only skip；Browser Preflight PASS；权威 Runner 页面切片 3 passed；服务终态 8011/4173 监听端口为 0；当时 204 项 Browser 运行未完成，该结果已被下一条去重后的完整 Gate 覆盖；
- Wave H Browser Project 与 Spec 收敛后为 102 tests in 10 files；完整权威 Gate：101 passed、1 Real-model opt-in skip、0 failed；此前 Frontend ESLint 0 warning，Vite Build/Bundle Budget/Lazy-route Gate PASS；服务端口与 `test-results` 运行残留均为 0；
- Wave H 首批测试目录分层：API Router、Interview Application、PostgreSQL Session Repository 与 PostgreSQL Runtime Repository 四个文件迁入 `tests/architecture/`，移动后的仓库根路径语义已修复；Architecture 批次 24 passed，完整 Pytest 收集 2513 tests、0 collection errors；该收集结果不是完整 Suite 执行结果；
- Wave H Contract 目录分层：十个纯 Python Contract 测试和一个配套 Node Contract 脚本迁入 `tests/contracts/`；目录批次 137 passed、6 skipped，六个 skip 均为未配置 `POSTGRES_DSN`，不得当作真实 PostgreSQL Gate；完整 Pytest 收集保持 2513 tests、0 collection errors；
- Wave H Acceptance 目录分层：十七个验收/Runner 文件迁入 `tests/acceptance/`，目录回归 115 passed；受影响 Memory Shadow Release Preflight 组合 24 passed；活动代码旧路径引用 0，完整 Pytest 收集保持 2513 tests、0 collection errors；
- Wave H Contract 根目录清理完成后：`tests/` 根目录 `test_*contract*.py` 为 0，Contract 目录最新 170 passed、6 个 `POSTGRES_DSN` 未配置 skip；Contract、Acceptance、调用方与当前文档组合 328 passed、6 skipped，活动代码旧 Contract 路径引用 0；
- Wave H Integration 首批目录分层：十个无活动旧路径调用的 PostgreSQL Adapter 测试迁入 `tests/integration/`；目录回归 3 passed、33 个 `POSTGRES_DSN` 未配置 skip，活动代码旧路径引用 0，完整 Pytest 收集保持 2513 tests、0 collection errors；
- Wave H PostgreSQL Integration 目标目录收敛：扁平中间态已迁入 `tests/integration/postgres/`，根目录 `test_*postgres.py` 与活动代码扁平路径引用均为 0；目录最新 17 passed、134 个 `POSTGRES_DSN` 未配置 skip，受影响调用方组合 68 passed、24 个数据库环境 skip，完整 Pytest 收集保持 2513 tests、0 collection errors；
- Wave H Provider Integration：六个 Provider Adapter/Real-model 文件迁入 `tests/integration/providers/`；目录回归 54 passed、1 个显式 opt-in Real LLM skip，Stage 49 Runner 4 passed，完整 Pytest 收集保持 2513 tests、0 collection errors；
- Wave H Unit 首批分层：二十个快速隔离测试迁入 `tests/unit/`；目录回归 92 passed，活动代码旧路径引用 0，完整 Pytest 收集保持 2513 tests、0 collection errors；
- Wave H 历史 Stage 48 Baseline 删除：移除固定 43 个 Direct Connect 和七个 Constructor Schema Setup 的文档/源码字符串 Gate；Stage 48 Release 与 Connection 替代组合 31 passed，最新完整 Pytest 收集为 2511 tests、0 collection errors；
- Wave H Markdown/历史冻结 Gate 清理：删除 Local V1 Docs、十类 ADR/Plan/Runbook/Spec、Hosted V2 Audit、RC Manifest 和 Causal Boundary 文案测试共 125 项；替代组合分别为 55 passed、377 passed/6 个数据库环境 skip、32 passed，最新完整 Pytest 收集为 2386 tests、0 collection errors；
- Wave H 第二批文档/历史 Evidence Gate 清理：从七个仍有真实行为覆盖的测试文件中删除十个操作文档、历史 Acceptance、已提交 JSON、旧 Revision/固定测试数量与 Pending 文案 Gate；行为替代组合 107 passed、1 个数据库环境 skip，最新完整 Pytest 收集为 2376 tests、0 collection errors，`git diff --check` 为 0；
- Wave H 第三批文案/精确字符串 Gate 清理：删除 `.env.example`、历史 Stage 47 Plan、README/Runbook 与 `package.json` 命令子串的三个重复测试；Config、Stage 48、Browser Runtime 与 Reproducibility 替代组合 58 passed、1 个数据库环境 skip，最新完整 Pytest 收集为 2373 tests、0 collection errors；
- Wave H Stage 40/Runtime Migration Boundary 收敛：Stage 40 复用共享异常、JSON Reader、Scanner 与摘要原语，同时以新增测试锁定原始字节 SHA-256 协议；Runtime Report Executor 不再请求 Schema Migration，Stage 48 四个源码/`.env` Gate 和一个旧 API Facade 字符串 Gate 已由行为/AST Contract 替代。Artifact Audit 64 passed；Runtime/Stage 48 81 passed、4 个数据库环境 skip；Report/API/Lifecycle 156 passed、1 个数据库环境 skip；最新完整 Pytest 收集为 2371 tests、0 collection errors；
- Wave H Static Frontend 删除：九个无运行引用的 `app/static/` JS/CSS、十六个静态源码字符串测试和 `build:prototype-css` 兼容别名已删除；Release/Validation 共用 `RETIRED_STATIC_ASSETS`，React/Release/UTF-8/Acceptance 组合 40 passed，Frontend ESLint、Build、Bundle Budget 与 Lazy-route Gate 全部通过；当时 Memory Center 保留到 Wave G 替代，现已由 v1.88 的 React 页面完成替代；
- Wave H React Architecture 分层：根目录十二个 JSX/CSS/文案字符串测试替换为三个独立 Runtime、页面所有权与退休资产 Architecture Contract；Memory System/Release 路径同步，旧路径引用 0，Architecture/Release/Acceptance 组合 51 passed；Browser 权威收集保持 102 tests in 10 files，最新完整 Pytest 收集为 2346 tests、0 collection errors；
- Wave H Principal Memory Consumption 文档 Gate 清理：七个 Draft Spec/Risk Review 短语测试删除，未授权 Consumption 边界迁入 Architecture AST/行为 Contract；替代组合 46 passed，旧路径引用 0，最新完整 Pytest 收集为 2339 tests、0 collection errors；
- Wave H Principal Memory Isolation 分层：三个根目录源码扫描文件的十一个测试收敛为四个 Architecture AST Contract 与三个 Unit 行为测试；Release 必需路径同步为单一 Owner，旧路径引用 0，相关组合 67 passed，最新完整 Pytest 收集为 2335 tests、0 collection errors；
- Wave H Runtime Boundary 分层：环境访问、Legacy Config、唯一 Container 与本地 Embedding 禁止收敛为四个 Architecture AST Contract；Identity 使用签名检查，Exclusive Migration 源码顺序 Gate 由真实行为覆盖；扩大组合 96 passed、4 个数据库环境 skip，Acceptance 6 passed，旧路径引用 0，最新完整 Pytest 收集为 2332 tests、0 collection errors；
- Wave H Evidence Writer Architecture 收敛：五个受保护 Production Evidence Runner 的重复源码字符串 Gate 合并为一个 AST Contract，五个原文件的 CLI/Receipt/失败关闭行为测试保留；组合 79 passed，最新完整 Pytest 收集为 2328 tests、0 collection errors，结构化 Contract 与 `git diff --check` 通过；
- Wave H Frontend/Knowledge/LLM 字符串 Gate 收敛：删除七个 Phase 5 JSX/CSS/脚本精确字符串测试和一个 LLM `inspect.getsource()` 测试，Bundle Analyzer 失败关闭行为迁入 Contract 层，Knowledge Evaluation 的无 LLM/Report 依赖迁入 Architecture Import Boundary；替代组合 21 passed，Frontend 全部 Gate 通过，最新完整 Pytest 收集为 2320 tests、0 collection errors；
- Wave H Unit 第二批分层：Knowledge Eval V1/V2 CLI、Metrics、LLM Service 与 Principal Causal Boundary 五个 Fake/In-memory 文件迁入 `tests/unit/`；Release Preflight、跨测试 Helper 和当前文档引用同步，54 passed，完整 Pytest 收集保持 2320 tests、0 collection errors；
- Wave H Context 首批分层：两个 Payload/Validation Contract 迁入 `tests/contracts/`，六个算法/In-memory 测试迁入 `tests/unit/`；三套 Acceptance/Release 清单和当前文档路径同步，98 passed，完整 Pytest 收集保持 2320 tests、0 collection errors；
- Wave H Context 第二批分层：Compression Runner/Agent、Enforcement、Runtime 与两个 Artifact Coordinator 测试迁入 `tests/unit/`；Acceptance 清单同步，54 passed，完整 Pytest 收集保持 2320 tests、0 collection errors；
- Wave H Knowledge 分层：六个 Corpus/Dataset/Manifest/Coverage Contract 迁入 `tests/contracts/`，八个 Fake/算法/Loader 测试迁入 `tests/unit/`；调用方、Acceptance/Release 与文档路径同步，181 passed、26 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
- Wave H Report 首批分层：五个 Models/Dataset/Artifact/PDF Contract、十一个评分/质量/评估 Unit 与一个 Report Job PostgreSQL Integration 文件迁入目标层级；Acceptance/文档路径同步，97 passed、17 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
- Wave H Report 第二批分层：Durable Enqueue、Orphan Projection、Evaluation CLI 与 Task Microbatch 四个 Fake/In-memory 文件迁入 `tests/unit/`，Report Trace Artifact 测试迁入 `tests/contracts/`；全部 29 个 Report 测试文件组合为 224 passed、18 个数据库环境 skip，活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors；
- Wave H Report Worker 混合文件拆分：十六个 Fake/In-memory 测试迁入 Unit，真实 PostgreSQL Worker 测试迁入 PostgreSQL Integration，共享 `make_report` 提取为非测试 Fixture；Acceptance Runner 保留两层 Gate，全部 30 个 Report 文件仍为 224 passed、18 个数据库环境 skip；
- Wave H Report 根目录收敛：Report API 迁入 Acceptance，Report Tasks 迁入 Unit，Release Preflight、共享 Helper 与当前文档引用同步；根目录 `test_report_*.py` 为 0，迁移和调用方组合 103 passed，完整收集保持 2320 tests、0 collection errors；
- Wave H 跨子系统小型文件分层：七个 Token/Trace/Interview/Question Memory/Durable State/Runtime Event Unit、三个 Dataset/Traceability Contract 与一个 Page Route Acceptance 迁入目标层级；调用方组合 113 passed、10 个数据库环境 skip，活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors；
- Wave H Runtime 分层：六个 Config/Container/Event/Lifecycle/Outbox/Work Unit、四个 Ports/Preflight/Signal/Reproducibility Contract 与一个 Runtime HTTP Acceptance 迁入目标层级；仓库根解析随目录层级修正，调用方组合 141 passed、1 个 POSIX-only skip，完整收集保持 2320 tests、0 collection errors；
- Wave H Agent 分层：Recorder 混合文件拆为一个 Unit 与一个真实 PostgreSQL Integration 文件，共享 Record Builder 提取为非测试 Fixture；四个 Agent Runtime/Composition/Hardening/Agents 文件迁入 Unit，两个 Audit/Trace 文件迁入 Contract；活动 Runner、当前文档与调用方路径同步，组合 101 passed、3 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
- Wave H Session 分层：Deletion 混合文件拆为 Unit 与 HTTP Acceptance，Session/Tombstone/Report Store/Fake PostgreSQL Repository 归入 Unit，Serialization/PostgreSQL Deletion Schema 归入 Contract，Streaming Report Enqueue 归入 Acceptance；共享 Session Fixture 单一化，活动路径同步，扩大组合 75 passed、26 个数据库环境 skip，直接调用方 38 passed，完整收集保持 2320 tests、0 collection errors；
- Wave H Interview 第一批分层：Application/Event Stream/Graph/Rounds/Workflow Consumer/Prep/Question Evaluation 七个 Unit 与 Launch Bootstrap/Workflow Store 两个 PostgreSQL Integration 已归位，共享 Interview Plan Builder 单一化；活动 Runner/文档路径同步，迁移组合 71 passed、18 个数据库环境 skip，直接调用方 34 passed，完整收集保持 2320 tests、0 collection errors；四个混合文件仍待拆分；
- Wave H Interview 第二批分层：Launch 与 Prep Regeneration 分别拆为 Unit/Acceptance，Generation Store 拆为 Unit/PostgreSQL Integration，共享 Context Plan Builder 单一化；活动旧路径引用为 0，Interview/Prep 扩大组合 107 passed、28 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；只剩 Durable Interview Graph 混合文件待拆；
- Wave H Interview 收敛：Durable Graph 最后一个混合文件已拆为八个 Unit 场景与四个真实 PostgreSQL Integration 场景，专用表白名单 Cleanup/Residue Contract 完整保留；四套 Runner 同步两层 Gate，完整 Interview/Prep 组合 107 passed、28 个数据库环境 skip，根目录对应文件为 0，完整收集保持 2320 tests、0 collection errors；
- Wave H Memory 第一批分层：Config、Retention、Question Memory、In-memory Index 与 Report Jobs 五个 Unit、一个 PostgreSQL Question Memory Index Contract 已归位，共享 Question Memory Builder 单一化；活动路径同步，迁移与调用方组合 53 passed，完整收集保持 2320 tests、0 collection errors；
- Wave H Memory 第二批分层：Metrics 拆为 Contract/Unit/HTTP Acceptance，十一个受保护 Evidence/Receipt/CLI 文件归入 Contract；Runtime 顺序隔离与直接调用方组合 117 passed，活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors；
- Wave H Memory/Principal Memory 收敛：十五个 Unit、十七个 Contract、一个 HTTP Acceptance 与一个真实 PostgreSQL Integration 文件归入目标层级，三个共享 Fixture 消除跨测试 Builder 导入；Staging Preflight 本地/真实数据库职责已拆分，相关扩大组合 566 passed、31 个环境 skip，根目录对应文件为 0，完整收集保持 2320 tests、0 collection errors；
- Wave H Review/Durable Workflow 分层：八个 Unit、一个 Canary CLI Contract 与一个真实 PostgreSQL Integration 文件归入目标层级，三个共享 Fixture 消除 Fake Store、State、Snapshot 与 Rollout Bucket 的跨测试导入；迁移组合 89 passed、24 个数据库环境 skip，Runner/Release 调用方 49 passed，活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors；
- Wave H 根目录测试清零：PostgreSQL/Vector 十一个文件和最后十六个 API/Unit/Contract 文件全部归入目标层级，Capacity/Embedding Fixture 单一化，跨测试模块导入为 0；两批迁移分别为 95 passed、9 个数据库环境 skip 和 185 passed，调用方各 27 passed，根目录 `test_*.py` 为 0，完整收集保持 2320 tests、0 collection errors；
- Wave H 非冻结旧路径清理：Release Preflight、Runner、Runbook 与当前文档中的根目录测试路径已迁到权威分层位置，退休 Static UI/Docs Gate 只保留明确历史结果；非冻结 `tests/test_*.py` 引用为 0，直接调用方 27 passed；
- Wave H Stage/Profile 收敛：Stage 40/42/44A/44B1 Artifact Audit 已合并为 `release_artifact_audit` 的四个 Profile，Stage 48/49 已合并为 `repository_acceptance` 的两个 Profile；六个重复脚本及其活动引用均为 0，Profile 与直接调用方组合为 101 passed；
- Wave H Legacy Compatibility 删除：API Router、Session/Runtime Repository 聚合出口、Interview Error、Draft Store、Runtime Config 与 Memory Config 七个旧出口已删除；Config/Runtime/Memory/Provider 扩大回归为 171 passed，Architecture Contract 验证旧文件和旧 Import 均不可恢复，`compileall` 通过；最新完整 Pytest 收集为 2321 tests、0 collection errors，结构化 Contract 与 `git diff --check` 通过；
- Wave H Compatibility 语义审计：删除 Report Enqueue 无效 `background_tasks` 接口和测试 Fake，扩大回归 143 passed；`runtime_work`、pre-V15 Launch、DSN-owned Schema Mode 与 Runtime Config Getter 均有活动语义或调用者，已明确归属后续 Wave，不作机械删除；完整收集保持 2321 tests、0 collection errors；
- Wave H 冻结历史计划处置：`docs/superpowers/plans/README.md` 明确档案不可作为当前命令来源并提供六个退休 Wrapper 的 Profile CLI 映射；根 README 链接该边界，非档案当前文档中的退休模块引用为 0；冻结 Execution Baseline 保留为历史身份记录；
- Wave H 当前文档路径治理：`docs/superpowers/specs/` 已建立历史设计档案边界，根 README 链接 Plan/Spec 两类档案；Current Documentation Path Contract 扫描 90 份当前文档、28 个唯一脚本模块和 56 个唯一测试路径，缺失均为 0；定向 Architecture 组合 8 passed、Architecture 全目录 45 passed，完整 Pytest 收集为 2343 tests、0 collection errors；
- Wave H 剩余 Stage/Profile 与 Compatibility 所有权审计：Stage 38/43B、Knowledge Profile 和 Report Runtime Preflight 均有当前 CLI、Runbook、Contract 或薄 CLI 语义；当时识别的 `runtime_work.py` 已由 Wave F 替代并删除，Config Compatibility、Schema Mode 与 pre-V15 Launch 仍有生产调用者和后续所有权；
- Wave H Knowledge Acceptance Profile 收敛：Stage 44A/44B1 共用单一 `knowledge_acceptance` CLI 和 Provider Metrics 白名单，领域 Profile 保持独立；旧两个 Runner 模块路径与活动引用为 0，Profile/Artifact 组合 50 passed，Knowledge 扩大组合 148 passed；历史档案映射由六个更新为八个退休入口；完整收集 2321 tests、0 collection errors，结构化 Contract 与 `git diff --check` 通过；
- Wave H Stage 47/47.2 Acceptance Profile 收敛：两阶段共用 `repository_acceptance` CLI 和 Pytest 结果投影，领域 Check/Schema/Status 保持独立；Stage 47 未配置 DSN 时显式 `BLOCKED`，旧两个 Runner 模块与活动引用为 0；Profile/Contract 23 passed，扩大组合 215 passed、37 个数据库环境 skip，完整收集 2325 tests、0 collection errors；
- Wave H LangGraph Recovery/Dual Profile 收敛：两个历史 CLI 共用 `langgraph_acceptance` Dispatch 和 Artifact 基础能力，领域 Schema/Check/状态保持独立；Recovery 无 DSN 或零真实 passed 时显式 `FAIL`，旧两个模块与活动引用为 0；Profile/Architecture 13 passed，扩大组合 135 passed、74 个数据库环境 skip，完整收集 2329 tests、0 collection errors；
- Wave H 旧 Budget Shadow Gate 删除：宽松 JSON/Markdown Runner 和七个历史绑定测试已删除，现行 Observer 的严格 Payload/Policy/Receipt/Atomic Write 成为唯一入口；Operational Output 与 Approval Input 默认路径已统一，相关组合 35 passed，完整收集 2323 tests、0 collection errors；审计发现 Operational Shadow 其他历史输入仍待迁移；
- E-3 其他定向证据：Session Deletion + API 45 passed；Launch + API + Report 105 passed；API + Report API 94 passed；Runtime Outbox/Dispatcher/Recovery 27 passed；Receipt Consumer/Dispatcher/Recovery 19 passed；这些批次证明本地兼容面，不替代真实 PostgreSQL SQL/事务 Gate；
- 未提供获批 PostgreSQL 环境时，`test_postgres_session_store.py` 为 26 skipped、`test_postgres_runtime_control.py` 为 11 skipped、Runtime Migration 为 14 passed/4 skipped；这些结果只证明本地收集与 skip 策略，不证明 SQL 兼容、事务原子性、Cleanup 或零残留；
- C-6 资源终态：完整 Suite 后测试前缀关系 0、临时角色 0、临时 Evidence 0，独占 pgvector 测试容器已删除；
- 历史完整 Python Suite 基线：2193 passed，206 skipped，0 failed；该批次早于最新 Operational Shadow 与 Production Approval Request 修改，只能证明历史基线，不能作为最终验收证据；
- 原有 3 个完整 Suite 失败已提前在 Wave H 部分工作中关闭：历史 Git Diff Publication Contract 改为历史基线身份契约，Lockfile 使用跨平台 canonical UTF-8 LF 摘要，UTF-8 Gate 同步当前产品文案。

状态约束：

1. Wave B 的“已实现待终审”不等于 Acceptance/Shadow 迁移完成；
2. 任一脚本仍保留平行 Evidence、Ownership 或 Cleanup 实现时，Wave C 不得标记完成；
3. 任一完整 Suite 失败出现或重新出现时，Wave H 与全量重构不得标记完成；
4. 定向测试的通过数量不得相加后冒充完整 Suite；测试证据必须标明范围，最终 Suite 必须晚于最后一次生产代码修改；
5. `skipped` 必须分类说明。真实 PostgreSQL Ownership、Permission、Cleanup 与 Residue 契约不能以“环境未配置所以跳过”作为最终验收；
6. 只有所有 Wave 完成，并按第 21 节和第 23 节逐条自动审查、补救、复验后，整份计划才允许标记为完成。

### v1.89 当前执行账本

Wave C 已按依赖顺序完成以下执行切片。一个切片只有同时满足“生产实现、严格 Contract、受保护写入、消费端验证、定向测试”五项条件后才能勾选完成。

- [x] C-1：Common Evidence 基础设施、Capacity 生产者与 Stage 48 消费者；
- [x] C-2：Proposal Review、Budget/Write/Read/Operational Shadow、Lifecycle Drill 与 Production Approval Request；
- [x] C-3：Staging Preflight、Stage 38、共享 PostgreSQL Scope 与旧短指纹流程删除；
- [x] C-4：Production Budget 与 Production Change 受保护链路；
- [x] C-5：剩余 Stage、Release、Publication、Restore 与 Cleanup Evidence；
- [x] C-6：真实 PostgreSQL Permission、Target Identity、Ownership、Cleanup 与 Residue 契约执行。

Wave D 已按配置、组装和可靠性 Contract 的依赖顺序完成全部执行切片：

- [x] D-1：Effective Runtime Config，建立单一配置解析与验证边界；
- [x] D-2：Runtime Container 与显式 Lifecycle，替代分散的 module-level singleton 组装；
  - [x] D-2a：修复 Shutdown、Reset 与 Principal Memory 测试注入，使迁移中间态的 Runtime 定向批次恢复全绿；
  - [x] D-2b：迁移剩余实例、Started Flag、Metadata 与其所有权明确的 Lock，`_runtime_container` 成为唯一 Root Container 引用；
  - [x] D-2c：验证 Start/Shutdown 幂等、Closed 不可重开、Reset 创建新 Container，并确认关闭顺序与全资源清理；
- [x] D-3：LeaseToken、FencedMutation、RetryPolicy、ErrorTaxonomy 与 IdempotencyReceipt；
- [x] D-4：兼容入口迁移、重复实现扫描与 Wave D 定向/完整回归。

Wave D 已实现待终审。Wave E 不得重新引入 module-level Singleton、Started Flag、平行资源关闭逻辑或第二套 Reliability Contract。

Wave E 按 Router、Application/Domain、UnitOfWork/Repository 与 Serialization/Atomicity 顺序执行：

- [x] E-1：拆分 API Router，共享依赖与错误映射保持单一入口；
  - [x] E-1a：请求模型抽取到 `app/api/shared/models.py`；迁移期曾由 `app.api.routes` 兼容重导出，调用方完成迁移后该 Facade 已删除；API 组合回归 128 passed、2 skipped；
  - [x] E-1b：已建立 `app/api/shared/dependencies.py` 与 `app/api/shared/errors.py`；依赖在请求期或调用期解析，不在子 Router 导入时捕获可变 Runtime Getter；
  - [x] E-1c：已完成 Runtime、Prep、Interview/Draft/Streaming、Principal Memory、Deletion 与 Reports Router 迁移，并由 `app/api/router.py` 统一组合；
  - [x] E-1d：测试与 Browser Support 的 Monkeypatch 已迁到权威依赖/Router 入口；Runtime/Memory Metrics 进程级 Store 隔离已修复，架构契约、两种测试顺序与 148 passed/2 skipped 的组合回归均通过。`app/api/routes.py` 已在无活动调用者后删除，Architecture Contract 禁止生产入口恢复该 Import；
- [x] E-2：已抽取 Session Command、State、Snapshot、Start/Streaming 与 Interview Application Service；API 依赖 Domain、Application 和现有 Port，不依赖具体 Session/PostgreSQL Store；
- [x] E-3：建立 UnitOfWork Port，并按现有 Port 收敛策略拆分 PostgreSQL Repository；
  - [x] E-3a：建立 `UnitOfWorkPort`、`PostgresUnitOfWork` 与 Store Factory；Launch Coordinator 不再导入具体 PostgreSQL Store，也不再直接控制 Connection Commit/Rollback；
  - [x] E-3b：已修复并验证 Cursor/Connection 关闭失败语义；只有显式 Commit 且资源 Context 全部成功退出后状态才为 `committed`，其他路径进入 `rolled_back`，业务异常保持权威；
  - [x] E-3c：按风险递增顺序拆分 Repository，并把所有 Mutation 收敛为 caller-owned Cursor/UoW；
    - [x] 已落地 Session、Message、Report、Question Evaluation、Runtime Outbox 与 Runtime Receipt Repository，公共出口保持惰性导入和对象身份兼容；
    - [x] 旧 Session/Runtime Control Store 已委托新 Repository，Session + Message + Outbox 以及 Evaluation + Receipt 的组合写入仍可共享事务；
    - [x] 本地 Contract 已覆盖 CAS Conflict、Duplicate Outbox Event 与成功路径的 Rollback/Commit 次数；
    - [x] Outbox Lease/Retry/Dead-letter 与 Receipt Claim/Retry/Finish 等 Runtime Mutation 已改为 caller-owned Cursor，由 Control Facade 通过 `PostgresUnitOfWork` 决定 Commit/Rollback；
    - [x] `session_repositories.py` 与 `runtime_repositories.py` 已按单一 Repository 职责拆为六个独立文件；调用方迁入权威模块后，两个旧聚合重导出已删除，Architecture Contract 禁止恢复；
    - [x] Store 中 Schema 创建、升级与索引职责已迁入 PostgreSQL Schema Adapter；运行时关系验证仍由现有验证边界负责，未新增 Migration Port，E-3 未改 Serialization 字节格式；
  - [x] E-3d：完成 Repository Architecture/Contract 与真实 PostgreSQL Gate；
    - [x] 本地架构/兼容 Contract 已覆盖六个 Repository 出口、Facade 委托、Connection/UoW 资源语义和核心原子性路径；
    - [x] Runtime Fault Injection 已覆盖 Outbox Claim 失败、Dead-letter Replay + Receipt Reset、Evaluation Upsert + Receipt Lease Lost、Evaluation Conflict 与成功路径单次 Commit；失败路径均为 0 Commit/1 Rollback，成功路径为 1 Commit/0 Rollback；
    - [x] 显式批准已物化为仓库外批准记录，绑定 Database Allowlist `interview`、Approved Fingerprint `5e025dd48cab1ffe94fb19b4837cafa66c247e323a1246cb2354f18ba3b0136e` 与 Receipt SHA-256 `977184caf46f3c10fc92fbdbc50799ec12763bb67e3fc267e4f5917d22e72ead`；仓库和测试输出均未写入 DSN 或密码；
    - [x] 已在批准的 `OwnedPostgresScope` 中验证真实目标、Ownership、SQL、Permission Denied、Cleanup、Residue 与 Stage 43B Cleanup Receipt；Critical Contract 为 4 passed、0 skipped、0 failed；
    - [x] 当前 PostgreSQL 权威标记集为 194 passed、2149 deselected、0 skipped、0 failed；扩大 Connection、Launch、API、Report、Runtime Consumer/Dispatcher/Recovery 组合为 223 passed、0 skipped、0 failed；
    - [x] 已修复限权角色密码与认证失败清理夹具，单独清理失败运行留下的确切角色；标记集后及扩大组合后的独立 Catalog 审查均为 Relation Residue 0、Role Residue 0；
- [x] E-4：收敛 Serialization，执行 Session/Message/Outbox/Receipt 原子性 Gate 与 Wave E 完整回归；
  - [x] 六个独立 Row Mapper 已落地，生产调用者全部迁移，旧 `session_serialization.py` 已删除且活动引用为 0；
  - [x] 六类物理行均持久化 `row_schema_version`；v16 Migration 对旧行执行 v1 Backfill、`NOT NULL` 和运行时列契约，显式未知版本统一失败关闭；
  - [x] JSON Shape、CAS/Expected Version、Lease/Fencing、Launch Atomicity 与 Transactional Outbox 保持兼容；Fault Injection、真实 Session/Launch/Migration 和扩大组合均通过；
  - [x] 最后一次 Wave E 生产代码修改后的完整 Python Gate 为 2349 passed、3 个非数据库 skip、0 failed；两个 POSIX-only、一个 Real LLM opt-in，Relation/Role Residue 均为 0。

Wave F 按 Reliability Adapter、Report Pipeline、细分 Port 与输出/评分 Owner 顺序执行：

- [x] F-1：将具体 Runtime Failure Mapping、Error Taxonomy、Retry Delay 与 Outbox/Receipt 状态迁入 `app/adapters/reliability/`；生产调用者全部迁移，旧 `app/services/runtime_work.py` 删除且 Architecture Contract 禁止恢复；
- [x] F-2：建立 `ReportGenerationPipeline`，拆分 Progress、Question/Microbatch/Full-session Evaluation、Assembly 与 Quality Policy；`report_tasks.py` 仅保留入口和委托；
- [x] F-3：在现有 `app/ports/runtime.py` 中拆出 Report Job Repository、Lease、Retry 与 Orphan Repair 四个 Port；`ReportJobQueue` 作为兼容聚合，不建立第二套 Ports 树；
- [x] F-4：建立 `ReportWorker`、`ReportReliabilityProjector` 与 `ReportPdfRenderer` 命名 Owner；评分由 `VersionedReportRubric` 驱动，显式未知版本失败关闭；
- [x] F-5：修复 Report 测试的 Runtime Container 跨文件状态依赖；全部 Report 文件真实 PostgreSQL 组合为 278 passed、0 skipped；
- [x] F-6：最后一次 Wave F 生产代码修改后的完整 Python + PostgreSQL Gate 为 2354 passed、3 个非数据库 skip、0 failed；两个 POSIX-only、一个 Real LLM opt-in，没有 PostgreSQL skip，Relation/Role Residue 均为 0。

Wave F 已实现待终审。Wave G 不得把 Principal Memory 数据引入评分、报告、公共 Knowledge、Embedding 或 Shared Retrieval，也不得改变 Context Artifact 的 Key、Identity 或 Owner 语义。

Wave G 按 Principal Memory、Knowledge/Vector、Context Artifact 与 React 替代顺序完成全部执行切片：

- [x] G-1：Principal Memory Contract/Fact Lifecycle 迁入 Domain，Memory/PostgreSQL Adapter 不再互相导入状态转换；共享 Store Contract 覆盖 Dedup、Activation、Isolation、CAS 与 Purge；
- [x] G-2：Consent、Control、Lifecycle、Selector、Context Renderer、Rights、Ledger 与 Shadow Observer 建立权威命名 Owner；Runtime/API 已迁移，Architecture Gate 保持评分、报告、公共 Knowledge、Embedding 与 Shared Retrieval 隔离；
- [x] G-3：Knowledge Chunk/Query/Reranking 迁入 Domain，PgVector Repository/Codec 迁入 Adapter；继续复用唯一 Ports 树，旧 Vector Store 路径删除；
- [x] G-4：Context Artifact Contract 与 Integrity Policy 迁入 Domain，Memory/PostgreSQL Adapter 共用 Purpose、Schema 与 Digest 策略；Recovery Service 承担有界清理；
- [x] G-5：Static Memory Center 迁为懒加载 React 页面，旧 HTML/CSS/JS 和静态源码字符串 Contract 删除；Browser 定向 Gate 8 passed，Frontend Lint/Build/Bundle/Lazy-route 全部通过；
- [x] G-6：领域扩大组合 516 passed、1 个 POSIX-only skip；真实 PostgreSQL Adapter 组合 45 passed、0 skipped，Relation/Role Residue 均为 0。

Wave G 已实现待终审。最后一次完整 Python/PostgreSQL 与十类 Browser Gate 仍属于 Wave H 和第 24 节最终审查，不得用以上定向数量替代。

Wave H 可复现性与测试收敛随业务 Wave 同步建设，但不得用局部完成替代 Wave H 最终退出条件：

- [x] H-1：Browser Python Runtime 使用单一 Helper，验证 Python 3.11、FastAPI/Uvicorn 能力、realpath/executable identity，并以同一解释器通过 Reproducibility Preflight；普通 Runner、Preflight、Playwright Backend 与 Real-model Smoke 均已迁移；
- [x] H-2：Browser Suite 已收敛为第 18 节定义的十类权威 Suite；每个页面只保留一套行为测试，Viewport/Accessibility Matrix 只有一个 Owner；
  - [x] 删除重复 desktop/mobile Project，移动关键流程改为显式 Viewport；收集数由 204 降为 102，完整 Gate 为 101 passed、1 Real-model opt-in skip；
  - [x] 17 个 Spec 已合并为十类权威文件，102 个逻辑测试全部保留；合并后完整 Gate 为 101 passed、1 Real-model opt-in skip、0 failed，服务与运行残留为 0；
- [x] H-3：测试目录分层、冻结历史计划处置、Compatibility 审计、依赖 Wave F/G 的旧路径/Static Memory Center 处置、最后一次完整 Python/PostgreSQL、Frontend、十类 Browser Gate、资源终态检查与最终自动审查均已完成；
  - [x] `reference-ui-geometry.js` 已改为 `browser-suite-support.js`，十类权威 Suite 不再依赖已删除 Reference UI 的命名；
  - [x] 已删除冻结旧 Commit/Tree、ahead/behind、固定测试数量、旧文件清单与原型 HTML 摘要的 Baseline/Publication/Reference Artifact 测试；
  - [x] 已删除 `test_static_report_ui.py` 中读取 Browser 源码字符串和固定 Spec 文件名的架构 Gate；
  - [x] API Router、Interview Application、PostgreSQL Session Repository 与 PostgreSQL Runtime Repository 四个架构测试已迁入 `tests/architecture/`；移动后的仓库根路径解析已修复，24 个 Architecture Contract 全部通过，完整测试收集无错误；
  - [x] Runtime Reliability、Context Artifact、Browser Python Runtime、Memory Center UI、LangGraph、Principal Memory、Report 与 Stage 38 API 等十个纯 Contract 测试及其 Node Contract 已迁入 `tests/contracts/`；目录回归 137 passed、6 个数据库环境 skip，完整收集数量保持不变；仍被脚本或测试按模块名引用的 Contract 暂不移动；
  - [x] Agent Runtime、LangGraph、Memory Shadow、PostgreSQL Support/Capacity 与 Stage Runner 等十七个验收文件已迁入 `tests/acceptance/`；权威脚本清单已同步，目录回归 115 passed，活动代码旧路径引用为 0；
  - [x] 剩余七个被脚本、测试或当前文档引用的 Release/Runtime/Memory/UTF-8 Contract 已迁入 `tests/contracts/` 并同步调用方；根目录 `test_*contract*.py` 与活动代码旧路径引用均为 0，Contract 目录最新为 170 passed、6 个数据库环境 skip；
  - [x] 首批十个无活动旧路径调用的 PostgreSQL Adapter 测试已迁入 `tests/integration/`；目录回归 3 passed、33 个数据库环境 skip，活动代码旧路径引用为 0；
  - [x] Integration 扁平中间态已纠正为目标 `tests/integration/postgres/`；剩余明确的 `*_postgres.py`、Repository/Migration/Capacity 文件和所有活动调用方已同步，根目录 `test_*postgres.py` 与扁平路径引用均为 0；目录最新 17 passed、134 个数据库环境 skip；
  - [x] Embedding、Report、Runtime 与 SiliconFlow Provider Adapter 及 Real LLM Eval 已迁入 `tests/integration/providers/`；目录回归 54 passed、1 个显式 opt-in skip，Stage 49 Runner 路径与回归已同步；
  - [x] 首批二十个无活动旧路径调用的快速隔离测试已迁入 `tests/unit/`；目录回归 92 passed，活动代码旧路径引用为 0；
  - [x] 已删除 Stage 48 Connection Baseline 中固定历史调用数量、Constructor 数量和源码字符串的 Gate；有效 Release/Connection 替代组合 31 passed，完整收集按预期从 2513 减为 2511；
  - [x] 已删除 125 个 Local V1/Hosted V2/Memory ADR、Plan、Runbook、Spec、RC Manifest 与静态文案 Markdown Gate；当前 Runbook 和 Release Preflight 失效路径已移除，运行/结构化替代组合全部通过；
  - [x] 已删除第二批十个只固定操作文档、历史 Acceptance、已提交 JSON、旧 Revision/测试数量与 Pending 文案的 Gate；对应 Policy、CLI、Evidence/Receipt、外部路径、Ownership/Cleanup 和运行行为测试保留，完整收集为 2376 tests、0 collection errors；
  - [x] 已删除三个固定 `.env.example`、历史 Stage 47 Plan、README/Runbook 和 `package.json` 命令子串的重复 Gate；运行时 Config、Stage 48、Browser Runtime 与 Reproducibility 行为覆盖保留，完整收集为 2373 tests、0 collection errors；
  - [x] Stage 40 已复用共享 Audit 低层能力并保留原始字节 Manifest 协议；Runtime Report Executor 已移除隐式 `migrate` 路径，Stage 48 与旧 API Facade 的五个源码/字符串 Gate 已由行为和 Architecture AST Contract 替代；完整收集为 2371 tests、0 collection errors；
  - [x] 已删除无运行引用的 `app/static/` 九个 JS/CSS、十六个静态源码字符串测试和 `build:prototype-css` 兼容别名；Static Memory Center 也已由懒加载 React 页面替代并删除旧 HTML/CSS/JS 与源码字符串 Contract；
  - [x] 已将根目录十二个 React 视觉实现/源码字符串测试收敛为 `tests/architecture/test_frontend_runtime.py` 的三个稳定 Contract；Acceptance/Release 调用方已同步，旧路径引用为 0，完整收集为 2346 tests、0 collection errors；
  - [x] 已删除 Principal Memory Consumption Draft Spec/Risk Review 的七个 Markdown Gate，将未授权 Service/Port/API/Config 边界迁入 Architecture AST 与运行行为 Contract；完整收集为 2339 tests、0 collection errors；
  - [x] 已将 Principal Memory Knowledge Firewall、Prompt Isolation 与 Consumption Isolation 三个根目录源码扫描文件收敛为 Architecture AST 与 Unit 行为测试；Release 必需路径同步，旧路径引用为 0，完整收集为 2335 tests、0 collection errors；
  - [x] 已将散落的环境访问、Legacy Config、Runtime Container 与 Local Embedding 源码扫描统一迁入 Runtime Architecture Contract；Principal Identity 使用签名检查，Exclusive Migration 源码顺序 Gate 由 Integration 行为覆盖；完整收集为 2332 tests、0 collection errors；
  - [x] 已将五个受保护 Production Evidence Runner 的重复 Writer/历史路径源码 Gate 收敛为一个 Architecture AST Contract；合同 Markdown 与审批示例输入继续允许，CLI、Receipt、严格 Payload、失败关闭和链路行为测试全部保留；组合 79 passed，完整收集为 2328 tests、0 collection errors；
  - [x] 已删除七个 Phase 5 JSX/CSS/Hook/脚本精确字符串 Gate 和一个 LLM `inspect.getsource()` Gate；Bundle Analyzer 负向运行 Contract 与 Knowledge Evaluation Import Boundary 已迁入正确测试层，真实前端与 LLM/Knowledge 行为覆盖保留；完整收集为 2320 tests、0 collection errors；
  - [x] Knowledge Eval V1/V2 CLI、Metrics、LLM Service 与 Principal Causal Boundary 五个 Fake/In-memory 测试已迁入 `tests/unit/`；活动旧路径引用为 0，迁移组合 54 passed，完整收集保持 2320 tests、0 collection errors；
  - [x] Context Artifact Payload/Compression Validation 两个 Contract 与 Budget/Selection/Language/Eligibility/Prep/In-memory Store 六个 Unit 测试已完成分层；活动旧路径引用为 0，迁移与 Acceptance/Release 组合 98 passed，完整收集保持 2320 tests、0 collection errors；
  - [x] Context Compression Runner/Agent、Enforcement、Runtime、Evidence 与 Interview Artifact Coordinator 六个 Fake/In-memory 测试已迁入 `tests/unit/`；活动旧路径引用为 0，迁移与 Acceptance 组合 54 passed，完整收集保持 2320 tests、0 collection errors；
  - [x] Knowledge Corpus/Dataset/Manifest/Profile 六个 Contract 与 Agent/Binding/Metrics/Grounding/Ingestion/Loader/Static Store 八个 Unit 测试已完成分层；活动旧路径引用为 0，迁移与调用方组合 181 passed、26 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
  - [x] Report Models/Dataset/Artifact/PDF 五个 Contract、评分/质量/评估十一 Unit 与 Report Job PostgreSQL Integration 已完成分层；活动旧路径引用为 0，迁移与 Acceptance 组合 97 passed、17 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
  - [x] Report Durable Enqueue、Orphan Projection、Evaluation CLI、Task Microbatch 四个 Unit 与 Report Trace Artifact Contract 已完成分层；活动旧路径引用为 0，全部 29 个 Report 文件组合 224 passed、18 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
  - [x] Report Worker 混合文件已拆为十六个 Unit 测试和一个真实 PostgreSQL Integration 测试，共享报告 Fixture 单一化；Stage 47/Dual Workflow Runner 与当前文档路径已同步，活动旧路径引用为 0，全部 30 个 Report 文件仍为 224 passed、18 个数据库环境 skip；
  - [x] Report API 已迁入 Acceptance，Report Tasks 已迁入 Unit，Release Preflight、跨测试 Helper 和当前文档引用已同步；根目录 `test_report_*.py` 为 0，全部 30 个 Report 文件保持 224 passed、18 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
  - [x] 七个跨子系统 Unit、三个 Dataset/Traceability Contract 和一个 Page Route Acceptance 已完成分层；Stage 49、Dual Workflow、Memory Acceptance、Graph/Recovery 与当前 Runbook 路径已同步，活动旧路径引用为 0，调用方组合 113 passed、10 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
  - [x] Runtime Config/Container/Event/Lifecycle/Outbox/Work 六个 Unit、Ports/Preflight/Signal/Reproducibility 四个 Contract 与 Runtime Boundary API Acceptance 已完成分层；Stage 47/49、Dual Workflow、Release Preflight 与当前文档路径已同步，活动旧路径引用为 0，调用方组合 141 passed、1 个 POSIX-only skip，完整收集保持 2320 tests、0 collection errors；
  - [x] Agent Recorder 混合文件已拆为 Unit 与真实 PostgreSQL Integration，共享 Record Builder 已提取为非测试 Fixture；Agent Runtime/Composition/Hardening/Agents 四个 Unit 与 Audit/Trace 两个 Contract 已完成分层，Stage 47.2/48、LangGraph Dual/Stage 49、Memory Shadow Release Preflight 和当前文档路径已同步；活动旧路径引用为 0，迁移与调用方组合 101 passed、3 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；
  - [x] Session Deletion 混合文件已拆为 Unit 与 HTTP Acceptance；Session/Tombstone/Report Store/Fake PostgreSQL Repository 五个 Unit、Serialization/PostgreSQL Deletion Schema 两个 Contract 与 Streaming Enqueue/Deletion API 两个 Acceptance 已完成分层，共享 Session Fixture 已单一化；Memory Optimization、Release Preflight、Restore Drill 和当前 Stage 文档路径已同步，活动旧路径引用为 0，扩大组合 75 passed、26 个数据库环境 skip，直接调用方 38 passed，完整收集保持 2320 tests、0 collection errors；
  - [x] Interview 第一批七个 Fake/纯逻辑文件已迁入 Unit，两个真实数据库文件已迁入 PostgreSQL Integration，共享 Interview Plan Builder 已提取为非测试 Fixture；LangGraph Dual/Stage 49、Agent Runtime、Memory Release 与 Stage 42 路径已同步，活动旧路径引用为 0，迁移组合 71 passed、18 个数据库环境 skip，直接调用方 34 passed，完整收集保持 2320 tests、0 collection errors；Durable Graph、Generation Store、Launch 与 Prep Regeneration 四个混合文件仍待下一切片拆分；
  - [x] Interview Launch 与 Prep Regeneration 已分别拆为 Unit/HTTP Acceptance，Generation Store 已拆为 Unit/真实 PostgreSQL Integration；共享 Context Plan Builder 和 LangGraph Dual 两层 Gate 已同步，活动旧路径引用为 0，Interview/Prep 扩大组合 107 passed、28 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；只剩 Durable Interview Graph 需要保留专用表 Cleanup 后拆分；
  - [x] Durable Interview Graph 已拆为八个本地 Unit 与四个真实 PostgreSQL Integration，专用表正则白名单、差集 Cleanup 和 Residue 断言完整保留；Agent Runtime/Stage 47/Dual/Stage 49 Runner 已同步两层 Gate，活动旧路径和根目录 Interview/Prep/Question Evaluation 文件均为 0，完整权威组合 107 passed、28 个数据库环境 skip，完整收集保持 2320 tests、0 collection errors；Interview 测试分层子任务关闭；
  - [x] Memory 第一批五个 Config/Retention/Question/In-memory/Report Jobs Unit 与一个无数据库 PostgreSQL Naming Contract 已归位，共享 Question Memory Fixture 已单一化；Memory System Optimization 清单和 Recovery 调用方已同步，活动旧路径引用为 0，迁移与调用方组合 53 passed，完整收集保持 2320 tests、0 collection errors；Memory Metrics、Evidence/CLI 与 Principal Memory 继续留待后续批次；
  - [x] Memory Metrics 已拆为 Privacy Schema Contract、Aggregate/Fallback Unit 与公开 HTTP Acceptance，Runtime 顺序隔离通过；Budget/Production/Cleanup/Publication/Manifest 等十一个受保护 Evidence/Receipt/CLI 文件已归入 Contract，活动旧路径引用为 0，迁移与调用方组合 117 passed，完整收集保持 2320 tests、0 collection errors；真实 Drill、剩余 Shadow/Release 与 Principal Memory 继续留待后续批次；
  - [x] Memory/Principal Memory 剩余根目录文件已按十五个 Unit、十七个 Contract、一个 HTTP Acceptance 和一个真实 PostgreSQL Integration 分层；Fact/Retrieval/Tombstone、Observability 与 Staging Builder 已提取为三个非测试共享 Fixture，Staging Preflight 的本地 Contract 与真实 PostgreSQL Migration/Cleanup Gate 已分离；活动旧路径引用为 0，相关扩大组合 566 passed、31 个环境 skip，根目录对应文件为 0，完整收集保持 2320 tests、0 collection errors；
  - [x] Review/Durable Workflow 已按八个 Unit、一个 Canary CLI Contract 和一个真实 PostgreSQL Integration 分层；Fake Review Store、Round Review State、Canary Snapshot 与 Rollout Bucket Builder 已提取为三个非测试共享 Fixture，六类活动 Runner/Preflight 路径已同步；迁移组合 89 passed、24 个数据库环境 skip，直接调用方 49 passed，活动旧路径引用为 0，完整收集保持 2320 tests、0 collection errors；
  - [x] PostgreSQL/Vector 最后两处跨测试 Fixture 已提取，十一文件完成 Unit/Contract/PostgreSQL Integration 分层；其余十六个 API、Fake/算法与 Artifact/CLI Contract 文件也已归位，根目录 `test_*.py` 与跨测试模块导入均为 0；迁移批次分别为 95 passed、9 个数据库环境 skip 和 185 passed，调用方各 27 passed，完整收集保持 2320 tests、0 collection errors；
  - [x] 测试目录分层已完成：根目录 `test_*.py`、跨测试模块导入和非冻结根路径引用均为 0；Release/Runner/Runbook/当前文档已同步权威路径，冻结 Execution Baseline 继续作为历史身份记录而非当前命令；
  - [x] Stage 40/42/44A/44B1 Artifact Audit 已收敛为 `release_artifact_audit` Profile CLI，Stage 48/49 Acceptance Runner 已收敛为 `repository_acceptance` Profile CLI；原六个重复脚本及活动引用已删除；
  - [x] 已删除 `app/api/routes.py`、Session/Runtime Repository 聚合重导出、`session_errors.py`、`drafts.py`、Config 与 Memory Config Service Facade；调用方全部指向权威模块，Architecture Contract 验证旧文件不存在且旧 Import 不可恢复；
  - [x] 已删除 `enqueue_report_if_needed()` 无效的 `background_tasks` 兼容参数、FastAPI Import、生产调用参数和测试 Fake；Durable Queue 与失败投影行为回归 143 passed；
  - [x] 首轮剩余兼容语义已完成所有权判定：`runtime_work.py` 已由 Wave F Reliability Adapter 替代并删除；pre-V15 Launch 归公开 API 兼容退出策略，DSN-owned Schema Mode 归 PostgreSQL Adapter 收敛，`runtime/config/compatibility.py` 当前仍是有调用者的权威 Getter 集；剩余入口不得在替代契约落地前机械删除；
  - [x] 冻结历史计划目录已建立档案边界和退休命令映射，根 README 已链接该说明；非档案当前维护文档中的退休模块引用为 0，冻结 Execution Baseline 明确保留为历史身份而非当前命令；
  - [x] Stage 44A/44B1 Knowledge Acceptance 已收敛为单一 Profile CLI 和共享 Provider Metrics 白名单；两个领域 Profile 的 Corpus/Manifest/Dataset/Privacy Gate 保持独立，旧 Runner 模块路径和活动引用为 0；
  - [x] Stage 47 与 Stage 47.2 Acceptance Runner 已收敛到 `repository_acceptance` 的独立 Profile；共享 Pytest Runner 不合并各自 Check/Schema/Status，Stage 47 无 DSN 时失败关闭；Contract/Runbook 已迁移，旧模块与活动引用为 0，AST Architecture Contract 禁止恢复；
  - [x] LangGraph Recovery 与 Dual Workflow Acceptance 已收敛到 `langgraph_acceptance` 的独立 Profile；共享 Commit/Artifact/CLI 基础能力不合并领域 Schema，Recovery 无 DSN 或零真实 passed 时失败关闭；旧模块与活动引用为 0，冻结档案映射和 AST Architecture Contract 已同步；
  - [x] 旧 Budget Shadow Acceptance Runner 已删除；现行 Observer 的严格 Payload/Policy、签名 Receipt、原子写入和写后复验成为唯一入口，Runbook 不再要求裸 JSON 或历史 Task 4 Gate；Operational Output 与 Approval Input 默认路径一致；
  - [x] Operational Shadow 历史输入迁移第一批：Budget、Write、Read、Lifecycle 与 Restore 已改为严格 Domain Payload/Policy 和签名 Bundle；CLI 验证 Receipt、Revision、固定 Scope 与重新计算的 Policy，连同 Proposal Review 共 6 项进入输出 Input Manifest；错误 Revision 失败关闭；
  - [x] Operational Shadow 历史输入迁移第二批：RC、Regression、Staging、Status 与 Security 已建立独立严格 Payload/Policy，并由统一 Profile CLI 发布签名 Bundle；消费端验证 Receipt、Revision、Scope、Payload、重新计算的 Policy 和 Artifact 状态，连同第一批输入共 11 项进入输出 Input Manifest；旧五类 `docs/*.json`/Markdown 路径不再作为 Operational 输入；
  - [x] 迁移 Historical Trust 剩余上游：Budget Observer 已验证 RC/Staging 签名 Bundle 并纳入两项 Input Manifest；Status 聚合已改为验证 Budget/Write/Read/Lifecycle/Proposal 受保护 Payload，同时保留详细三面板低基数语义；失效 Runbook 参数、旧宽松 Reader 和十个历史机器/固定 Revision 记录已删除，非冻结活动旧路径为 0；
  - [x] 删除六个无活动消费者的额外历史机器 JSON；连同十个 Historical Trust 记录和单独退休的 Production Evidence Manifest，全部十七个退休机器记录已加入 Architecture 防恢复清单；仍被当前 Publication/Baseline、测试或合同示例引用的 JSON 不按历史记录机械删除；
  - [x] 为 `docs/superpowers/specs/` 建立档案边界，并从根 README 链接该边界；历史 Design Snapshot 不作为当前 Runbook、Release Gate、测试路径或命令来源；
  - [x] 新增 Current Documentation Path Architecture Contract：验证当前 README、Runbook 与维护文档中的 `python -m scripts.*` 模块和 `tests/...` 文件可解析；排除历史档案、明确 Frozen Baseline、本计划的历史修订/目标路径和 Hosted V2 的未来 Recommended Ownership；
  - [x] 已完成剩余 Stage/Profile 和当前可处置 Compatibility 的所有权审计；Stage 38/43B、Knowledge Profile 与 Report Runtime Preflight 均有当前行为所有权；Wave F 已处置 `runtime_work.py`，Config Compatibility、Schema Mode 与 pre-V15 Launch 继续按真实调用者和退出策略管理；
  - [x] 已在 Wave F/G 替代实现完成后处置 `runtime_work`、Vector Store、Context Artifact、Principal Fact Store 旧路径及 Static Memory Center；保留的 pre-V15 Launch 和 Runtime Config Getter 仍有明确公开/运行语义，不按文件名机械删除；
  - [x] 六类结构化 Contract、Validator、Mutation/Reference/DAG/Readiness Contract 和六份生成 Reference 已完成；权威 YAML 不包含历史 Revision、Run ID、固定测试结果或机器路径；
  - [x] 最后一次完整 Python/PostgreSQL、Frontend 与十类 Browser Gate、资源终态和最终自动审查已完成，证据见 `docs/refactoring-audit.md`。

C-4 已严格按以下顺序完成；该顺序保留为最终回归时的受保护链路基线，避免下游再次消费裸 JSON 或旧的已提交示例文件：

1. `scripts/memory_production_budget_shadow_readiness.py`：验证 `ProductionShadowApprovalRequestPayload` 的 Receipt、Revision 与 Scope，输出严格的 Readiness Payload/Policy；
2. `scripts/memory_production_shadow_change_preflight.py`：验证 Approval Request、Readiness Evidence 与外部 Approval Record，保持未获批准时 fail closed；
3. `scripts/memory_production_budget_shadow_observation.py`、`scripts/memory_production_budget_shadow_window.py` 与 `scripts/memory_production_budget_shadow_acceptance.py`：迁移为领域 Payload/Policy 和受保护 Evidence 链；
4. `scripts/memory_production_shadow_evidence_manifest.py`：只收集已经验证的受保护 Evidence，不再把历史 plain JSON 当成可信输入。

C-5 已覆盖：

- `scripts/memory_shadow_restore_drill.py`；
- Stage 40 Artifact Audit 命名修正，以及 Stage 42、43b、44a、44b1 与 49 的生产脚本和对应测试；
- Release、Publication 与 `CleanupEvidencePayload` 生产输出；
- 已经失效的 Stage 38 文档命令和历史兼容入口。

C-5 已完成：Restore Drill 已迁移为 `RestoreDrillEvidencePayload/Policy` 和受保护写入；Release Preflight 已输出受保护 `ReleaseEvidencePayload`；Cleanup 与 Publication 已形成 Release → Cleanup → Publication Receipt 链；Stage 42/44A/44B1 重复 Artifact Audit 已收敛为共享策略核心；Stage 43B Recovery 与 Stage 49 Context Budget Canary 已迁移到严格 Payload/Policy 和受保护写入；原误命名的 Stage 41 Artifact Audit 已修正为 Stage 40，失效的 Stage 38 与 Stage 40 文档命令已更新。C-5 最终组合回归结果为 206 passed、0 failed。Stage 41 Browser Runner 的解释器身份与测试去重仍属于 Wave H，不混入 C-5 完成条件。

Wave C 每个受保护输出均以以下条件完成迁移，并在最终审查中按相同条件复验：

- 使用 Common Envelope + Domain-specific Payload + Domain-specific Policy；
- 输入通过 Receipt、Revision、Scope 与 Input Manifest 绑定验证；
- 使用 HMAC Receipt、`AtomicEvidenceWriter`，并在落盘后由 `EvidenceVerifier` 复验；
- 禁止以 `Path.write_text(json.dumps(...))` 直接写受保护 Evidence；
- 合成数据即使 `VerificationStatus=PASS`，`PromotionDecision` 也必须保持 `HOLD`；
- 删除被替代的本地 validator、privacy scanner、canonical hash、cleanup helper 与旧 plain JSON 信任路径。

### 外部复核意见映射

桌面复核文档提出的修订意见已按下表纳入，避免后续实施重新引入旧版设计问题。

> 适用性说明：复核中的 `READY_WITH_REVISIONS` 是对修订前旧版 Plan 的判定。下表所列修订随后全部进入执行基线；当前 v1.89 已完成实现、Gate 与最终自动审查，Release 状态以结构化 Contract 的 `ready` 为准。

复核文本中的文件大小、调用方式和缺陷描述属于 2026-08-10 的历史审查证据，不覆盖后续已经验证的实现事实。若某项已经落地，它在本计划中作为 Architecture/Contract/Acceptance 防回归条件继续生效；只有当前账本仍未勾选的交付物和 Gate 才属于待执行工作。任何状态更新都必须附带本轮仓库证据，禁止仅依据历史审查文本把已完成项回退为未开始，或把未运行的 Gate 标记为通过。

| 复核意见 | Plan 中的落实位置 |
|---|---|
| Phase 0 是真实安全问题，应优先处理 | 第 3 节、第 6 节、Wave A |
| 保留现有 NUL Status，仅修复 Rename/Copy 双路径语义 | 第 3.4 节、第 6.4 节 |
| Browser 问题是解释器身份不确定，不是仓库硬编码 Anaconda | 第 3.5 节、第 18 节、第 21.4 节 |
| 不建立第二套 Ports | 第 4.3 节、第 21.1 节、第 23 节 |
| 不新增 app/domain/runtime | 第 4.2–4.3 节、第 21.1 节 |
| Evidence 使用 Common Envelope + Domain Payload + Domain Policy | 第 7 节 |
| Verification Status 与 Promotion Decision 分离 | 第 7.2 节 |
| OwnedPostgresScope 必须先于 Acceptance/Shadow 迁移 | 第 8–9 节、Wave B–C |
| Reliability Contract 必须先于 Session/Report 大拆分 | 第 10.4 节、第 12–13 节、Wave D–F |
| Session 拆分必须保留 UnitOfWork 与事务原子性 | 第 12.2 节、第 12.4 节、第 23 节 |
| Context Artifact 是边界抽取与 Adapter 收敛，不是协议重写 | 第 15.2 节、第 23 节 |
| 以 Wave A–H 推进，避免首轮同时开展多个大型工程 | 第 20 节、第 22 节 |

## 1. 目标

本计划基于对项目锁定范围内 647 个文件的逐一审查，目标是：

- 修复已确认的安全、数据清理和可复现性问题；
- 将重复的 Acceptance、Evidence、配置、数据库与测试基础设施合并为单一实现；
- 拆分职责过多的 API、Runtime、Session、Report、Memory 与 Knowledge 模块；
- 删除已经被新架构替代的历史 Stage、静态 UI 和重复测试；
- 保持现有对外 API、持久化数据和关键业务行为兼容；
- 建立可以持续演进的领域边界、依赖方向和验收标准。

本计划不包含：

- 在一个变更中重写全部业务；
- 未经迁移策略直接改变数据库结构；
- 未经兼容期直接删除仍被外部调用的 CLI 或 API；
- 以降低测试强度换取重构速度；
- 将重复逻辑移动到更多小文件但不建立单一权威实现。

## 2. 审查范围与结论

### 2.1 Manifest

| 编号范围 | 范围 | 数量 |
|---|---|---:|
| 1–264 | 应用、前端与根配置文件 | 264 |
| 265–330 | scripts 根文件 | 66 |
| 331–626 | tests 根文件 | 296 |
| 627–644 | tests/browser 文件 | 18 |
| 645–647 | Report Provider Payload Fixture | 3 |
| 合计 |  | 647 |

Manifest Audit 结果：

- 缺失编号：0；
- 重复编号：0；
- 重复路径：0；
- 不存在文件：0；
- 范围漂移：0。

排除范围：

- tests/golden 下的 10 个数据集文件；
- tests/fixtures/memory_production_budget_shadow/pass_candidate.json。

### 2.2 总体评价

2026-08-10 审查基线显示，项目在领域建模、CAS、幂等、Outbox、Lease、Fencing、Tombstone、Report Job 和 Principal Memory 安全边界方面具备较好的基础；当时的主要问题集中在：

1. Acceptance 与 Evidence 可以通过宽松字段、自我声明或伪造 Artifact 自证成功；
2. PostgreSQL Target Identity、Ownership 和 Cleanup 没有统一安全模型；
3. Runtime 配置与依赖组装分散，环境变量和 Singleton 较多；
4. 大型文件承担多个变化原因；
5. 历史 Stage、文档契约和测试持续累积；
6. 测试 Fixture、数据库清理、Provider Fake 和 Browser Helper 重复；
7. Markdown 与源码字符串被当成业务 Gate；
8. React、Reference、Phase 和静态兼容 UI 存在多套重叠权威测试。

## 3. 审查基线确认的高风险问题

本节记录 2026-08-10 审查基线及其必须长期保留的防回归约束，不表示这些缺陷在当前工作区仍未修复。已关闭项不得因这里保留历史描述而回退状态；当前实现状态以文档前部的当前执行账本、第 22 节当前执行窗口、`contracts/releases.yaml` 和最新 Gate 证据为准。

### 3.1 Acceptance 与 Evidence 自证通过

基线确认的风险包括：

- 字符串 false 被当作真值；
- 缺失字段默认补零；
- NaN 与 Infinity 未统一拒绝；
- 硬编码测试数量和零违规；
- 未执行真实测试仍输出 Ready 或 PASS；
- 少量字段可以伪造 Capacity Artifact；
- 缺少真实 Review Case 与 Revision 仍输出 Proposal Review PASS；
- 零 Sample 可以生成全零 Hard Invariant；
- Synthetic 或 In-memory 结果被描述为接近生产验收结论。

目标链路必须统一为：

Strict Schema → Exact Field Set → Strict Types → Revision/Scope Binding → Input Receipt Verification → Policy Evaluation → Protected Output Receipt。

### 3.2 PostgreSQL 目标与清理风险

统一安全链路必须是：

External Approval → Full Instance Fingerprint → Database Allowlist → Empty Owned Scope → Ownership Marker → Lease → Execute → Failure-safe Cleanup → Full Residue Verification → Cleanup Receipt。

基线确认的问题：

- Staging 静态 Gate 失败后仍可能进入数据库 Cleanup；
- Stage 38 输出和写入完整 DSN；
- Stage 38 默认 DSN 含明文密码；
- Stage 38 使用可被 Python 优化模式移除的断言；
- 正式脚本反向导入测试 Fake；
- PostgreSQL 已配置但不可达时被当作 Skip；
- 多个 PostgreSQL 测试创建随机表后不清理。

### 3.3 Redis Preflight 数据破坏风险

审查基线中的 Runtime Preflight 使用固定 Key `stage41:preflight`，可能覆盖并删除已有数据。防回归约束为：

- 每次运行生成高熵 Run ID；
- Key 绑定 Ownership Token；
- 删除前校验 Ownership；
- Cleanup 只删除本次运行创建的 Key；
- 并发运行互不影响。

### 3.4 Release Rename 漏检

审查基线中的 Release Preflight 已经使用 Git porcelain v1 的 NUL-delimited 输出，因此无需重新实现输入协议。基线缺陷位于 `parse_porcelain_v1_z()` 的双路径处理：解析器识别 Rename 或 Copy 状态后跳过第二个路径，没有让源路径和目标路径分别进入安全策略。

修复要求：

- 保留现有 NUL-delimited 输入；
- 完整解析 Rename 与 Copy 的 source 和 destination；
- 按 porcelain v1 `-z` 规范映射双路径顺序，禁止沿用普通文本输出的顺序假设；
- 对两端分别执行 Ownership、Sensitive Path 与 Release Boundary Policy；
- 检查 Index 与 Worktree；
- 无法解析的双路径状态失败关闭。

### 3.5 可复现性问题

基线确认：

- Windows Checkout 将 Lockfile 转为 CRLF；
- Metadata 使用 LF 原始字节 Hash；
- Browser Runner 当时通过遗留环境变量 `STAGE41_PYTHON` 或 PATH 中的 `python` 解析解释器，没有证明实际解释器与项目要求的 Python 3.11 是同一身份；该问题不是仓库硬编码 Anaconda，当前修复状态以 v1.20、v1.21 和第 22 节为准；
- UTF-8 测试混入已过期产品文案；
- Publication Test 固定一次历史 Git Diff。

## 4. 目标架构

### 4.1 分层

目标依赖方向：

1. API、CLI 与 Worker Entry 调用 Application Command 或 Query；
2. Application 依赖 Domain 与 Ports；
3. Domain 不依赖框架、数据库、Provider SDK 或环境变量；
4. 复用并收敛现有 Port，Adapter 实现现有或经审查后重命名的 Port；
5. Runtime Container 负责配置、依赖组装和生命周期；
6. Contract 层负责 Evidence、Policy、Release、Plan 与 Schema；
7. Markdown 文档由结构化 Contract 生成，不直接作为业务 Gate。

### 4.2 推荐目录

- app/domain/interview
- app/domain/report
- app/domain/memory
- app/domain/knowledge
- app/application/commands
- app/application/queries
- app/application/workflows
- app/application/policies
- app/ports
- app/adapters/postgres
- app/adapters/pgvector
- app/adapters/redis
- app/adapters/llm
- app/adapters/embedding
- app/adapters/filesystem
- app/runtime/config
- app/runtime/container
- app/runtime/lifecycle
- app/runtime/observability
- app/api/prep
- app/api/interview
- app/api/reports
- app/api/memory
- app/api/runtime
- app/api/shared
- contracts/evidence
- contracts/policies
- contracts/releases
- contracts/plans
- contracts/schemas

### 4.3 现有 Port 收敛策略

当前项目已经存在 context_artifacts、drafts、interview_launch、memory_metrics、principal_memory、question_memory、runtime、session_deletion 等 Port。重构不得先建立一套平行的 app/ports/repositories、app/ports/providers 或 app/ports/jobs。

每个现有 Port 按以下流程处理：

1. 确认它表达的是业务能力还是具体实现；
2. 保留正确的 Protocol 与语义；
3. 对职责过载的 Port 做小范围拆分或重命名；
4. 让旧 services 实现逐个迁移到 Adapter；
5. 所有新旧 Adapter 运行同一 Contract Test；
6. 调用点迁移完成后删除旧实现。

Runtime、Lease、Retry、Outbox、Connection 与 Provider Wiring 不属于业务 Domain，不新增 app/domain/runtime。它们分别归入 app/runtime、现有 app/ports/runtime.py 或对应 Adapter。

## 5. 实施原则

每个重构 PR 必须遵守：

- 结构移动与行为修改分开；
- 先补 Characterization Test，再移动实现；
- 优先复用现有 Port；只有现有语义确实不足时才先修订 Port，再迁移 Adapter；
- 新旧实现短期运行同一 Contract Test；
- 数据库变更必须包含 Upgrade、Validate 与恢复策略；
- 删除旧实现前确认调用点为零；
- 不使用长期 Re-export 掩盖循环依赖；
- 不引入第二套临时 Policy；
- 所有资源操作必须有 Cleanup；
- 每个 PR 都必须可以单独回滚；
- 工作区不得留下测试表、进程、线程或临时 Artifact。

## 6. Phase 0：安全热修

- 优先级：P0
- 预计工期：1–3 个工作日
- 依赖：无

### 6.1 Stage 38

处理 scripts/stage38_postgres_runtime_acceptance.py：

- 禁止打印或写入完整 DSN；
- Artifact 只保存不可逆实例指纹；
- 删除默认明文密码 DSN；
- 删除正式代码对 tests 的导入；
- 将安全断言改成显式异常和稳定 Gate Code；
- 默认模式为 Dry Run。

验收：

- stdout、stderr、JSON、Markdown 中无凭据；
- Python 优化模式不改变安全判定；
- 未显式授权时不连接数据库。

### 6.2 Redis Probe

处理 scripts/runtime_preflight.py：

- 使用唯一 Probe Key；
- 写入 Ownership Token；
- 删除前重新验证 Ownership；
- Cleanup 后验证本次 Key 不存在；
- 保证已有 Key 和并发 Probe 不受影响。

### 6.3 Staging Preflight

处理 scripts/memory_shadow_staging_preflight.py：

- 所有静态 Gate 通过前禁止连接数据库；
- 静态失败立即结束；
- Cleanup 前验证完整 Target Identity；
- Cleanup 必须持有 Ownership Marker。

### 6.4 Release Diff

- 处理 `scripts/memory_shadow_release_preflight.py`；
- 保留现有 Git porcelain v1 NUL-delimited 输出；
- 修复 parse_porcelain_v1_z 的 Rename 与 Copy 双路径语义；
- 按 porcelain v1 `-z` 规范识别 source 与 destination，不复用普通文本输出的路径顺序假设；
- 将源路径和目标路径作为两个独立安全身份检查；
- 检查 Index 与 Worktree；
- 处理 Symlink、Submodule、路径编码和大小写；
- 无法解析时失败关闭。

### 6.5 PostgreSQL 测试清理

- 所有数据库 Fixture 改为 yield/finally；
- 修复已确认的 Generation、Workflow、Session Store 测试表残留；
- Cleanup 后查询 Catalog；
- DSN 已配置但不可达时 Fail，不再 Skip。

## 7. Phase 1：统一 Evidence 与 Receipt

- 优先级：P0/P1
- 预计工期：5–8 个工作日
- 依赖：Phase 0

### 7.1 建立 Common Evidence Envelope

建议建立：

- contracts/evidence/envelope.py
- contracts/evidence/payloads.py
- contracts/evidence/canonical.py
- contracts/evidence/privacy.py
- contracts/evidence/digest.py
- contracts/evidence/receipt.py
- contracts/evidence/writer.py
- contracts/evidence/verifier.py
- contracts/evidence/rendering.py

Common Envelope 统一负责：

- Schema Version；
- Producer；
- Tool Version；
- Revision；
- Scope；
- Input Manifest；
- Input Digest；
- Generated At；
- Privacy Metadata；
- Receipt Identity。

Domain Payload 由各业务域维护严格 Schema，例如：

- Capacity Evidence Payload；
- Proposal Review Evidence Payload；
- Shadow Evidence Payload；
- Release Evidence Payload；
- Cleanup Evidence Payload。

Domain Policy 负责解释该 Payload 是否满足对应 Gate。禁止用一个万能 Payload Schema 承载所有 Stage 字段。

Envelope 与 Payload 共同提供：

- Strict Boolean；
- Strict Integer；
- Finite Float；
- Exact Required Fields；
- Unknown Field Rejection；
- Canonical UTF-8 LF；
- Canonical JSON；
- Revision Binding；
- Scope Binding；
- Input Manifest；
- Producer 与 Tool Version；
- Timestamp 与 Expiry；
- Privacy Firewall；
- Atomic Write；
- Post-write Verification；
- Receipt Signature 或可信存储引用。

### 7.2 分离 Verification Status 与 Promotion Decision

VerificationStatus 只表达验证是否完成和是否通过：

- PASS；
- BLOCKED；
- NOT_RUN。

PromotionDecision 表达是否允许继续观察或推进：

- HOLD；
- CONTINUE_OBSERVATION；
- READY_FOR_REVIEW；
- READY；
- 由 Domain Policy 定义的其他封闭枚举。

PromotionDecision 不得覆盖 VerificationStatus。验证未运行时，不得通过 PromotionDecision 绕过 NOT_RUN。

### 7.3 合并重复实现

将当前多套 Artifact Validator、Blocked Formatter、PASS Lines、SUCCESS Lines、Privacy Scan、Revision Check 和 Hash 实现收敛为：

- 一个 Schema Verifier；
- 一个 Policy Evaluator；
- 一个 Result Renderer；
- 一个 Atomic Evidence Writer。

### 7.4 Mutation Test

每种 Artifact 自动执行：

- 删除每一个 Required Field；
- 增加未知字段；
- Boolean 替换为字符串或数字；
- Integer 替换为字符串；
- Float 替换为 NaN 或 Infinity；
- 修改 Revision、Scope、Digest、File Set；
- 注入路径穿越和敏感字段。

完成标准：

- 字符串 false 无法通过；
- 缺失字段无法默认补零；
- 最小伪造 Artifact 无法通过；
- 零 Sample 不支持 PASS 或扩容；
- Synthetic Result 不得冒充 Production Acceptance。

## 8. Phase 2：统一 PostgreSQL 安全与测试 Harness

- 优先级：P1
- 预计工期：4–7 个工作日
- 依赖：Phase 0

### 8.1 OwnedPostgresScope

必须负责：

- Target Approval；
- Instance Fingerprint；
- Database 与 Schema Allowlist；
- Empty Prefix Verification；
- Ownership Marker；
- Lease；
- Cleanup；
- Catalog Residue Audit；
- Cleanup Receipt。

### 8.2 共享 Pytest Fixture

当前 Wave B 已通过 `tests/postgres_support.py` 与 `tests/conftest.py` 提供共享 Prefix、Scope 和 Cleanup 支持，不为追求目录形式重复实现 Harness。Wave H 整理测试目录时，可以在调用点与 Contract 均保持不变的前提下迁移到目标目录：

- tests/support/postgres/owned_scope.py
- tests/support/postgres/contracts.py
- tests/support/postgres/faults.py

Fixture 统一创建 Prefix、Provider、Ownership、Cleanup 与残留验证。物理目录迁移属于 Wave H / PR-30，不得回到各测试文件复制局部 Helper。

### 8.3 Migration Harness

统一提供：

- Apply；
- Validate-only；
- Idempotency；
- Checksum Conflict；
- Dirty Data Block；
- Partial Migration Recovery；
- Upgrade from Previous Version；
- Residue Audit。

## 9. Phase 3：统一 Acceptance 与 Shadow Control Plane

- 优先级：P1
- 预计工期：5–8 个工作日
- 依赖：Phase 1、Phase 2

Phase 1 与 Phase 2 可以在 Phase 0 完成后并行推进。所有涉及 PostgreSQL 的 Acceptance 与 Shadow Migration 必须从第一天开始只通过 OwnedPostgresScope 访问数据库，禁止先搬迁旧清理逻辑后再二次改造。

统一流程：

Typed Operation Request → External Approval Receipt → Physical Target Attestation → Runtime Config Attestation → Window State Machine → Gate Policy → Observation Receipt → Restore/Cleanup Receipt。

迁移范围：

- Budget Shadow；
- Write Shadow；
- Read Shadow；
- Operational Shadow；
- Production Budget Shadow；
- Proposal Review；
- Memory Validation Foundation；
- Stage 38、40、42、43b、44a、44b1、48、49；
- Release Preflight。

Stage 可以保留独立 Domain Payload 与 Policy，但必须共享 Envelope、Parser、Receipt、Privacy、Writer、Renderer 与 Verifier。

## 10. Phase 4：Effective Config 与 Runtime Container

- 优先级：P1
- 预计工期：5–8 个工作日
- 依赖：Phase 0

### 10.1 单一配置边界

建议建立：

- app/runtime/config/environment.py
- app/runtime/config/models.py
- app/runtime/config/loader.py
- app/runtime/config/validation.py

目标：

- 环境变量只在配置边界读取；
- Service 接收类型化 Config；
- Test 直接构造 Config；
- Legacy 环境变量映射只存在于 Compatibility Adapter。

### 10.2 Runtime Container

RuntimeContainer 统一持有：

- Effective Config；
- Connection Domains；
- Session Store；
- Report Job Store；
- Review Workflow Store；
- Knowledge Store；
- Embedding Provider；
- LLM Provider；
- Event Publisher；
- Memory Services；
- Runtime Lifecycle。

逐步删除全局 Store、Publisher、Executor Singleton 和多套 Test Reset。

### 10.3 Runtime Profile

显式定义：

- PreviewProfile；
- DurableProfile；
- TestProfile；
- MigrationProfile；
- RealProviderEvalProfile。

非法组合必须在 Startup 前失败。

### 10.4 提前建立 Runtime Reliability 核心 Contract

在 Session 与 Report 大拆分前，先定义最小且稳定的共享语义：

- LeaseToken；
- FencedMutation；
- RetryPolicy；
- ErrorTaxonomy；
- IdempotencyReceipt；
- LeaseLost；
- RetryableFailure；
- TerminalFailure。

本阶段只冻结 Contract 和兼容测试，不立即建设万能 Reliability Framework。Report、Review、Generation、Outbox 与 Thread Lock 在后续阶段逐个迁移到这些 Contract。

## 11. Phase 5：拆分 API 路由

- 优先级：P1
- 预计工期：3–5 个工作日
- 依赖：Phase 4

将 app/api/routes.py 拆为：

- app/api/shared/models.py
- app/api/prep/routes.py
- app/api/interview/routes.py
- app/api/reports/routes.py
- app/api/memory/routes.py
- app/api/runtime/routes.py
- app/api/deletion/routes.py
- app/api/test_support/routes.py
- app/api/shared/dependencies.py
- app/api/shared/errors.py
- app/api/router.py

迁移顺序：

1. 抽取请求模型并保持短期兼容重导出；
2. 建立共享依赖与错误映射入口；
3. 依次迁移 Runtime、Prep、Draft、Principal Memory、Deletion、Reports、Interview/Streaming Router；
4. 更新测试与 Browser Support 的 Patch 入口；
5. E-1 完成兼容回归后确认旧 Monolith 逻辑已经清空；仍有调用方依赖时只保留无业务逻辑的 Deprecated Compatibility Facade，并在 Wave H 完成全仓迁移扫描后删除。

要求：

- Router 只处理 HTTP 输入输出；
- Router 不构造 Store；
- Router 不直接调用 Worker Executor；
- Router 不读取环境变量；
- Domain Error 由统一 Error Mapper 转换；
- 可变 Runtime Getter 在请求期或调用期解析，子 Router 不得在模块导入时捕获会被测试或 Runtime 替换的依赖；
- `app/api/routes.py` 在迁移期只允许承担短期兼容重导出，生产 Router 组合必须由 `app/api/router.py` 负责；不得在 Facade 中新增业务逻辑，也不得把 Monolith 原样移动到 `legacy_routes.py` 或其他新文件；
- 测试与 Browser Support 最终 Patch 权威依赖或领域 Router，不长期依赖兼容聚合模块；
- 拆分前后的 `/api` 路径、OpenAPI Operation、状态码、响应体、Header 与流式事件顺序保持兼容；
- Test Support Router 仅在明确 Test Profile 注册；
- OpenAPI 不暴露内部 Locator、DSN、Lease Token 或 Durable ID。

## 12. Phase 6：拆分 Session 与 Interview

- 优先级：P1
- 预计工期：7–10 个工作日
- 依赖：Phase 3、Phase 4

### 12.1 Application Service

拆分为：

- SessionCommandService；
- SessionStateMachine；
- SessionSnapshotProjector；
- StreamingTurnService；
- SessionReportProjection；
- SessionDeletionWorkflow。

### 12.2 PostgreSQL Repository

拆分为：

- PostgresSessionRepository；
- PostgresMessageRepository；
- PostgresReportRepository；
- PostgresQuestionEvaluationRepository；
- PostgresRuntimeOutboxRepository；
- PostgresRuntimeReceiptRepository。

Repository 拆分必须通过 UnitOfWork Port 保持现有事务边界：

- UnitOfWorkPort；
- PostgresUnitOfWork；
- 一个 caller-owned connection 与 transaction；
- Session、Message、Outbox、Receipt Repository 共享同一事务；
- Application Workflow 决定 Commit 或 Rollback；
- Repository 不自行隐式 Commit。

UnitOfWork 的资源和异常语义必须满足：

- 默认退出、显式 Rollback、业务异常、Cursor/Connection 关闭异常都进入 Rollback 终态；
- 只有显式 Commit 且 Cursor 与 Connection Context 均成功退出后，才记录 `committed`；
- 业务异常与资源关闭异常同时发生时，业务异常保持权威，资源仍必须关闭；
- UnitOfWork 不得复用，Cursor 只能在 Active Context 中访问；
- Commit、Rollback 与资源关闭次数必须通过 Fault Injection Contract 验证。

Repository 按风险递增顺序迁移：先拆 Message/Report/Evaluation 等只读查询，再拆普通 Upsert，最后拆 Session Insert/CAS Replace、Message Replace、Runtime Outbox 与 Receipt。E-3 只移动事务与 Repository 边界，不同时重写序列化格式；Serialization Mapper 与 Schema Version 属于 E-4。

必须保留现有 caller-owned cursor 能力、Cross-store Launch Atomicity、CAS、Fencing 与 Transactional Outbox。

### 12.3 Serialization

建立独立 Mapper：

- SessionRowMapper；
- MessageRowMapper；
- ReportRowMapper；
- QuestionEvaluationRowMapper；
- PrepPlanRowMapper；
- MemoryPolicyRowMapper。

每种持久化 Payload 必须包含 Schema Version、Backfill Policy 和 Unsupported Version Error。

### 12.4 Session 原子性 Gate

以下条件必须加入 Definition of Done：

- Business Mutation 与 Outbox Event 仍在同一事务；
- Launch Coordinator 的跨 Store 写入仍原子提交；
- CAS 与 Expected Version 语义不变；
- Lease 或 Fencing Token 仍在写入时校验；
- 任一 Repository 失败时全部回滚；
- 拆分前后的 SQL 可观察行为通过 Characterization Test 对齐。

## 13. Phase 7：重构 Report Pipeline

- 优先级：P1
- 预计工期：8–12 个工作日
- 依赖：Phase 4 的 Reliability Contract、Phase 6

目标流程：

Report Command → Durable Job → Lease Claim → Evidence Collection → Question Evaluation → Report Assembly → Quality Policy → Atomic Persistence → Progress Projection → Completion Receipt。

拆分：

- ReportJobRepository；
- ReportJobLeaseAdapter；
- ReportRetryAdapter；
- ReportOrphanRepair；
- ReportProgressProjector；
- QuestionEvaluationService；
- MicrobatchEvaluationService；
- FullSessionEvaluationService；
- ReportAssembler；
- ReportQualityPolicy；
- ReportReliabilityProjector；
- ReportPdfRenderer。

统一：

- Attempt；
- Replay Count；
- Lease Token；
- Heartbeat；
- Retry Due Time；
- Terminal State；
- Orphan Detection；
- Error Taxonomy；
- Fallback Semantics；
- Trace Redaction。

评分规则迁移到 Versioned Rubric，至少包含：

- Rubric Version；
- Applicable Dimensions；
- Evidence Requirements；
- Score Caps；
- Blocking Conditions；
- Aggregate Rules。

## 14. Phase 8：收敛 Principal Memory

- 优先级：P1
- 预计工期：8–12 个工作日
- 依赖：Phase 1、Phase 4

保留并强化：

- Explicit Principal Identity；
- 独立 Consent Purpose；
- Safe Ref；
- Direct Declaration；
- Model Proposal 永不自动 Active；
- Exclusive Fact CAS；
- Operation-time Recheck；
- Session Ignore；
- Tombstone；
- Deletion Fence；
- Hash-chained Ledger；
- Local Consume 只影响 Follow-up。

目标组件：

- PrincipalIdentityResolver；
- PrincipalMemoryConsentPolicy；
- PrincipalMemoryControlPolicy；
- PrincipalMemoryLifecycle；
- PrincipalMemorySelector；
- PrincipalMemoryContextRenderer；
- PrincipalMemoryRightsService；
- PrincipalMemoryLedger；
- PrincipalMemoryShadowObserver。

所有 In-memory 与 PostgreSQL Adapter 必须运行同一 Store Contract。

建立共享 Scenario Matrix，覆盖 Identity、Consent、Disable、Session Ignore、Expiry、Deleted Source、Conflict、Cross-principal、Fact Cap、Token Cap、Context Digest 和 Read-only。

架构隔离改用 AST 或 Import Graph 验证，禁止 Principal Memory 进入评分、报告、公共知识库、Embedding 和 Shared Retrieval。

## 15. Phase 9：Knowledge、Vector 与 Context Artifact 边界抽取

- 优先级：P1/P2
- 预计工期：5–8 个工作日
- 依赖：Phase 4；Context Artifact 迁移还必须先建立本节定义的兼容性 Gate

### 15.1 Vector

拆分为：

- KnowledgeChunk；
- KnowledgeQuery；
- KnowledgeReranker；
- EmbeddingPort；
- KnowledgeRepositoryPort；
- PostgresKnowledgeRepository；
- PgVectorCodec；
- KnowledgeReleaseService。

### 15.2 Context Artifact Boundary Extraction 与 Adapter Convergence

当前项目已经存在 app/ports/context_artifacts.py，并且 Context Artifact 已拥有成熟的 Identity、Privacy、Lease、Fencing、Replay 与错误语义。本阶段不得借模块移动重新设计这些协议。

先锁定以下兼容性 Gate：

- Artifact Key Byte Compatibility；
- Identity v0 与 v1；
- Owner Binding；
- Privacy Scope；
- Source、Manifest 与 Semantic Focus Digest；
- Compression Policy Version；
- Prompt Contract Version；
- Provider、Model 与 Settings Identity；
- Target Token Identity；
- Claim、Lease 与 Fencing；
- Completed Artifact Immutability；
- Replay Reuse；
- Deletion Semantics；
- Busy、LeaseLost、Conflict、Missing 与 ValidationFailed Error。

在兼容 Gate 下抽取：

- ContextArtifact；
- 复用 app/ports/context_artifacts.py 中的现有 Port，不创建第二个 Repository Port；
- ContextArtifactIntegrityPolicy；
- ContextArtifactMemoryAdapter（当前 Preview Profile 的 Reference Adapter；若未来新增真实 Filesystem Profile，应作为独立产品能力与迁移任务，不把 Memory Adapter 虚假改名）；
- ContextArtifactPostgresAdapter；
- ContextArtifactRecoveryService。

目标是让现有 Port、Memory Reference Adapter 与 PostgreSQL Adapter 边界清晰，不改变 Artifact Key、Identity Digest、Owner Binding、Lease/Fencing、Completed Immutability 和 Replay Reuse 的现有语义。

## 16. Phase 10：Runtime Reliability Adapter 收敛与旧实现删除

- 优先级：P1
- 预计工期：5–8 个工作日
- 依赖：Phase 4 的 Reliability Contract，以及对应业务 Adapter 的迁移阶段

Phase 4 已冻结 Reliability 核心 Contract。本阶段负责逐个迁移 Adapter、合并重复实现并删除旧代码，不重新设计第二套 Contract。

可在本阶段补充共享运行组件：

- LeaseHeartbeat；
- BackoffSchedule；
- DeadLetterPolicy；
- OutboxEvent；
- RuntimeIncident。

迁移：

- Report Job Lease；
- Review Effect Lease；
- Runtime Outbox Lease；
- Generation Lease；
- Workflow Thread Lock；
- Retry Delay；
- Error Classification；
- Incident Recording。

完成标准：

- Stale Worker 永远不能写终态；
- Lease Loss 优先于 Failure Mutation；
- Retryable 与 Terminal 由统一 Taxonomy 决定；
- Heartbeat 只有一套权威实现；
- Outbox Completion 与业务写入保持事务一致。

## 17. Phase 11：结构化 Plan、ADR、Runbook 与 Release

- 优先级：P2
- 预计工期：4–7 个工作日
- 依赖：基础 Contract 依赖 Phase 1、Phase 2；最终替换旧 Gate 与 Release 文档依赖 Phase 3–Phase 10 的对应迁移完成

建立：

- contracts/requirements.yaml
- contracts/decisions.yaml
- contracts/tasks.yaml
- contracts/gates.yaml
- contracts/runbooks.yaml
- contracts/releases.yaml

当前实现：

- 六类 YAML 已落地，并由 `scripts/structured_contracts.py` 执行 Schema、引用、Task DAG、Release Readiness 与临时事实禁止校验；
- 生成输出位于 `docs/generated/refactoring-*.md`，包括 Acceptance、Traceability、Execution、Decisions、Runbooks 与 Release Contract；
- `tests/contracts/test_structured_refactoring_contracts.py` 覆盖引用缺失、依赖循环、临时字段/值、未完成任务误标 Ready 和生成文件漂移；
- Release 在真实 PostgreSQL、Frontend、Browser、Residue 与最终自动审查全部通过后转为 `ready`；结构化状态不替代这些运行证据，证据见 `docs/refactoring-audit.md`。

生成 Plan、ADR、Runbook、Release Contract、Acceptance Reference 和 Requirement Traceability。

删除长期固定：

- 一次性 Commit Hash；
- ahead 与 behind；
- 测试数量；
- Run ID；
- 机器路径；
- 历史 Python 版本；
- Markdown 子串 Gate；
- 源码字符串架构 Gate。

## 18. Phase 12：测试体系与前端测试收敛

- 优先级：P1/P2
- 预计工期：7–10 个工作日
- 依赖：测试随 Phase 0–Phase 10 同步建设；目录合并、权威 Suite 切换与旧测试删除在相关实现迁移完成后进行

目标目录：

- tests/unit
- tests/contracts
- tests/integration/postgres
- tests/integration/providers
- tests/architecture
- tests/acceptance
- tests/browser
- tests/fixtures
- tests/support/builders
- tests/support/fakes
- tests/support/postgres

共享 Harness：

- PostgreSQL Resource/Ownership Harness；
- Runtime Container Fixture；
- Principal Memory Store Contract；
- Context Artifact Store Contract；
- Report Job Queue Contract；
- LangGraph Fault Injection；
- Trace Filesystem Security Contract；
- Provider Adapter Contract；
- Evidence Schema Mutation Matrix；
- Numeric、Unicode 与 Privacy Property Tests；
- Browser Page Objects。

浏览器权威 Suite 收敛为：

1. Prep；
2. Interview；
3. Report Center；
4. Report Processing；
5. Report Detail；
6. Memory Center；
7. Recovery；
8. Accessibility；
9. Critical-path E2E；
10. Real-model Nightly Smoke。

当前收敛基线：

- Playwright 只保留一个 Chromium Project，不再用 desktop/mobile 两个 Project 全量复制相同用例；
- 移动端关键流程由测试内部的显式 Viewport Matrix 覆盖，Viewport 与 Accessibility Matrix 必须各自只有一个 Owner；
- 17 个 Spec 的行为覆盖已经迁入十类权威 Suite，最终收集为 102 tests in 10 files，没有通过删除测试降低数量；
- 合并过程中已先运行受影响的权威 Suite，合并后完整 Browser Gate 为 101 passed、1 Real-model opt-in skipped、0 failed；该结果是 H-2 当前基线，后续相关修改后必须重新运行。

合并：

- prep-ui、phase2-prep-plan 与 Reference Prep；
- phase4-report-product 与 report-detail-ui；
- reports-ui 与 Reference Report Center；
- Report Processing 的状态测试与动画测试分离。

Static UI 只保留最小兼容 Contract，完成迁移后删除。

可复现性任务：

- Browser Runner、Browser Preflight、Backend Wrapper 与 Real-model Smoke 共用 `scripts/python_runtime.js`，不得各自解析 Python；
- 候选顺序固定为 `INTERVIEW_RUNTIME_PYTHON`、兼容 `STAGE41_PYTHON`、`VIRTUAL_ENV`、工作区 `.venv`、Windows `py -3.11`、`python3.11`、PATH `python`；
- 每个候选都必须通过 Python 3.11、realpath/executable identity、FastAPI/Uvicorn 能力检查，并由同一解释器通过 `scripts.reproducibility_preflight --python-only`；
- Canonical/Legacy 环境变量身份冲突、版本错误、能力缺失或 Preflight 身份不一致时必须 fail closed；
- Lockfile Digest 以规范化 LF 字节计算，Windows Checkout 不得改变结果；
- 为 Windows 与 Linux 增加相同 Lockfile、相同 Digest 的回归测试。

## 19. 冗余删除清单

### 19.1 合并后删除

- 重复 Artifact Validator；
- 重复 Blocked Output Formatter；
- 重复 PASS 与 SUCCESS Line 常量；
- 重复 Privacy Key Scanner；
- 重复 Revision Check；
- 重复 Canonical Hash；
- 重复 PostgreSQL Prefix Validator；
- 重复 PostgreSQL Drop Helper；
- 重复 Retry Schedule；
- 重复 Lease Heartbeat；
- 重复 Provider Error Mapping；
- 重复 Runtime Singleton Reset；
- 重复 Browser Viewport Helper；
- 重复 Markdown Requirement Parser。

### 19.2 替代完成后删除

- [x] Stage 40、42、44A、44B1 重复 Artifact Audit：已收敛到 `scripts/release_artifact_audit.py` 的 Profile，原四个 Wrapper 已删除；
- [x] Stage 48、49 重复 Acceptance Runner：已收敛到 `scripts/repository_acceptance.py` 的 Profile，原两个 Wrapper 已删除；
- [x] Stage 47、47.2 重复 Acceptance Runner：已收敛到 `scripts/repository_acceptance.py` 的独立 Profile，原两个 Runner 已在迁移 Contract Import、Runbook 和调用方后删除；
- [x] LangGraph Recovery、Dual Workflow 重复 Acceptance CLI：已收敛到 `scripts/langgraph_acceptance.py` 的独立 Profile，原两个 CLI 已在测试迁移和历史档案映射后删除；
- [x] 旧 Budget Shadow Acceptance：宽松 JSON/Markdown Runner 已由严格 `memory_budget_shadow_observe` Evidence 流程替代，原 Runner 和历史绑定测试已删除；
- 历史 Execution Baseline Test；
- 永久绑定历史 Git Diff 的 Publication Test；
- 已退休静态 HTML Runtime Contract；
- 重复 Reference UI 功能测试；
- 源码字符串架构测试；
- Markdown 子串 Gate 测试；
- 正式脚本中的 Test Fake 导入；
- 未使用的局部 Drop Helper；
- 已完成迁移的 Legacy Runtime 分支。

### 19.3 暂时保留的兼容层

旧 CLI 或 API 名称如仍被外部调用，可以保留为薄包装，但只能：

1. 解析参数；
2. 调用统一 Service；
3. 渲染统一 Result；
4. 返回稳定 Exit Code。

兼容层不得包含独立 Policy、Schema、数据库访问和 Evidence 写入实现。

## 20. Wave 与逻辑变更集顺序

以下 `PR-xx` 仅表示可独立验证、可独立回退的逻辑变更集边界，不代表自动创建分支、提交或 Pull Request。Git 操作必须由用户另行授权。实施期间可以在同一工作区连续完成多个变更集，但不得跨越前置依赖，也不得用后续变更掩盖前一变更集的失败。

### Wave A：P0 Safety

目标：消灭真实风险，不进行架构搬家。

1. PR-01：Stage 38 DSN、Test Fake Import 与安全断言；
2. PR-02：Redis Probe Ownership；
3. PR-03：Staging Cleanup 顺序与 Strict Boolean；
4. PR-04：Release Rename/Copy 双路径语义；
5. PR-05：PostgreSQL 测试 Cleanup 与 Broken DSN Fail。

### Wave B：两套基础安全 Contract

Phase 1 与 Phase 2 可以并行。

6. PR-06：Common Evidence Envelope、Status Model 与 Mutation Tests；
7. PR-07：Domain-specific Evidence Payload 基线；
8. PR-08：OwnedPostgresScope 与共享 Fixture；
9. PR-09：Migration Harness 与 Residue Audit。

### Wave C：Acceptance 与 Shadow Migration

10. PR-10：Budget、Write、Read Shadow；
11. PR-11：Operational 与 Production Shadow；
12. PR-12：Stage Acceptance 与 Proposal Review；
13. PR-13：Release、Publication 与 Cleanup Receipt。

### Wave D：Config、Container 与 Reliability Contract

14. PR-14：Effective Runtime Config；
15. PR-15：Runtime Container 与 Singleton 生命周期；
16. PR-16：LeaseToken、FencedMutation、RetryPolicy、ErrorTaxonomy 与 IdempotencyReceipt。

### Wave E：API 与 Session

17. PR-17：API Router 拆分；
18. PR-18：Session Command、State 与 Snapshot；
19. PR-19：UnitOfWork Port 与 PostgreSQL Repository 拆分；
20. PR-20：Session Serialization 与 Atomicity Gate。

### Wave F：Report

21. PR-21：Report Job、Worker、Progress 与 Reliability Adapter；
22. PR-22：Evaluation、Microbatch 与 Report Assembly；
23. PR-23：Quality、Reliability 与 Versioned Rubric；
24. PR-24：Provider Payload Adapter 与 Replay Lineage。

### Wave G：Memory、Knowledge 与 Context Artifact

25. PR-25：Principal Memory Lifecycle 与 Store Contract；
26. PR-26：Principal Memory Rights、Ledger 与 Shadow Matrix；
27. PR-27：Knowledge 与 Vector Adapter Convergence；
28. PR-28：Context Artifact Boundary Extraction 与 Compatibility Gate。

### Wave H：测试、文档与删除

29. PR-29：结构化 Plan、ADR、Runbook 与 Release；
30. PR-30：测试目录与共享 Harness；
31. PR-31：Browser Suite 合并、Python Identity 与跨平台 Lockfile Digest；
32. PR-32：历史 Stage/Profile Runner 收敛、静态 UI 与兼容代码删除；
33. PR-33：历史 Spec 档案边界与当前文档路径 Architecture Contract。

## 21. 验收标准

本节每一项在最终审查时都必须记录为 `PASS`、`BLOCKED` 或 `N/A`。`N/A` 必须写明理由；仅写“测试通过”不能替代结构、安全与冗余扫描证据。

### 21.1 代码结构

- API Router 不承担业务逻辑；
- Runtime 收敛为 Config、Container、Lifecycle 与 Reliability，不新增 app/domain/runtime；
- Session、Report、Memory、Knowledge 分域；
- 正式代码导入 tests 的文件数为零；
- Domain 读取环境变量的文件数为零；
- 同一业务规则只有一个权威实现。

### 21.2 冗余治理

- Common Envelope Validator 收敛为一套，各 Domain Payload 保留独立严格 Schema；
- Blocked Formatter 收敛为一套；
- PostgreSQL Fixture 使用统一 Harness；
- Retry、Lease 与 Heartbeat 共享稳定 Contract，并由各运行 Adapter 复用；
- Markdown 业务 Gate 数量为零；
- 源码字符串架构 Gate 数量为零；
- 每个页面只有一套权威行为测试。

### 21.3 安全

- 无明文 DSN、密码或 Provider Key；
- Redis Probe 不影响已有 Key；
- PostgreSQL 只操作 Owned Scope；
- Rename 与 Copy 两端都检查；
- Artifact 拒绝未知字段、错误类型、NaN 与 Infinity；
- Synthetic Result 不得标记为 Production Acceptance；
- 所有 Cleanup 都生成安全 Receipt。

### 21.4 测试

- Unit、Contract、Integration、Architecture、Acceptance 与 Browser 分层；
- PostgreSQL 配置但不可达时失败；
- PostgreSQL 测试结束后残留对象为零；
- Browser Runner 解析并验证解释器 realpath、executable identity 与 Python 3.11 版本，且与项目 Runtime Preflight 一致；
- Windows 与 Linux Lock Digest 一致；
- Real-model Smoke 不阻塞普通 CI；
- 当前 README、Runbook 与维护文档引用的脚本模块和测试文件均可解析；历史档案、Frozen Baseline 与明确的未来推荐路径不冒充当前 Gate；
- git diff --check 通过；
- Git 状态经过人工或自动清单复核，不存在意外生成物、测试残留或任务范围外修改；是否暂存、提交或创建 PR 不属于技术验收条件。

## 22. 当前执行窗口

Wave A–H 与最终自动审查已关闭。E-3 真实 PostgreSQL Gate、E-4 Serialization/Atomicity、Wave E 完整回归、Wave F Report/Reliability、Wave G Memory/Knowledge/Context 以及 Wave H 完整 Gate 均已通过；以下保留实施序列作为可追溯账本，当前结论以 v1.89 和 `docs/refactoring-audit.md` 为准：

1. Wave D 已完成实现：D-4 本地完整 Suite 为 2259 passed、210 skipped；这些 skip 不能单独作为真实基础设施证据；
2. D-4 真实 PostgreSQL 标记集为 195 passed、0 skipped，表、角色和专用容器残留均为 0；
3. E-1a–E-1d 已完成，最新组合回归为 148 passed、2 skipped、0 failed，OpenAPI 为 39 个路径、45 个操作且无重复；
4. E-2 Session/Interview Application 与 Domain 边界已完成；本地 Durable/Workflow 批次中的 PostgreSQL skip 不得作为数据库验收证据；
5. E-3b UnitOfWork/Connection 资源异常语义已经关闭；Runtime Mutation 已通过 caller-owned Cursor 和 Facade UoW 显式控制事务；
6. 六个 PostgreSQL Repository 已拆为独立模块；生产调用方迁入权威模块后，旧 Session/Runtime 聚合路径已删除，Architecture Contract 禁止恢复聚合重导出；
7. Runtime Fault Injection 已证明 Claim、Replay + Receipt Reset、Evaluation + Receipt 等失败路径 0 Commit/1 Rollback，成功路径 1 Commit/0 Rollback；
8. Session/Runtime Store 的 Schema 创建、升级与索引职责已迁入 PostgreSQL Schema Adapter；Store 不再包含 DDL，迁移后本地扩大组合为 206 passed、1 skipped；
9. H-3 首批四个 Architecture 测试已迁入 `tests/architecture/`，移动后的仓库根路径语义已修复；Architecture 批次 24 passed，完整 Pytest 收集为 2513 tests、0 collection errors；
10. H-3 十个纯 Python Contract 测试与一个 Node Contract 已迁入 `tests/contracts/`；目录回归 137 passed、6 个 `POSTGRES_DSN` 未配置 skip，完整收集仍为 2513 tests、0 collection errors；
11. H-3 十七个验收/Runner 文件已迁入 `tests/acceptance/`；目录回归 115 passed，受影响 Release Preflight 组合 24 passed，活动代码旧路径引用为 0；
12. H-3 根目录 Contract 清理已完成：`test_*contract*.py` 为 0，Contract 目录最新 170 passed、6 个数据库环境 skip，活动代码旧路径引用为 0；
13. H-3 首批十个 PostgreSQL Adapter 测试已迁入 `tests/integration/`；目录回归 3 passed、33 个数据库环境 skip，活动代码旧路径引用为 0；
14. H-3 PostgreSQL Integration 已按目标层级收敛到 `tests/integration/postgres/`；目录最新 17 passed、134 个数据库环境 skip，受影响调用方组合 68 passed、24 个数据库环境 skip，根目录 `test_*postgres.py` 与扁平路径引用均为 0；
15. H-3 Provider Integration 已建立：目录回归 54 passed、1 个显式 opt-in Real LLM skip，Stage 49 Runner 4 passed；
16. H-3 Unit 首批二十个快速隔离测试已迁入 `tests/unit/`；目录回归 92 passed，活动代码旧路径引用为 0；
17. H-3 已删除 Stage 48 Connection 历史数量/源码字符串 Baseline；替代组合 31 passed，最新完整收集为 2511 tests、0 collection errors；
18. H-3 已删除 125 个 Markdown/历史冻结 Gate；替代运行/结构化组合全部通过，最新完整收集为 2386 tests、0 collection errors；
19. H-3 第二批删除十个操作文档、历史 Acceptance、已提交 JSON、旧 Revision/测试数量与 Pending 文案 Gate；行为替代组合 107 passed、1 个数据库环境 skip，最新完整收集为 2376 tests、0 collection errors，`git diff --check` 为 0；
20. H-3 第三批删除三个 `.env.example`、历史 Stage 47 Plan、README/Runbook 与 `package.json` 命令子串 Gate；替代组合 58 passed、1 个数据库环境 skip，最新完整收集为 2373 tests、0 collection errors；
21. H-3 Stage 40 已收敛共享 Reader/Scanner/Digest，Runtime Report Executor 不再拥有隐式 Migration；Stage 48 与旧 API Facade 的五个源码/字符串 Gate 已由行为/AST Contract 替代，最新完整收集为 2371 tests、0 collection errors；
22. H-3 已删除 `app/static/` 九个死资源、十六个静态源码字符串测试与 `build:prototype-css` 别名；Static Memory Center 后续已由 Wave G 的懒加载 React 页面替代，旧 HTML/CSS/JS 与静态源码字符串 Contract 已删除；
23. H-3 已将根目录十二个 React JSX/CSS/文案字符串测试替换为三个 Architecture Contract；旧路径引用为 0，Architecture/Release/Acceptance 组合 51 passed，最新完整收集为 2346 tests、0 collection errors；
24. H-3 已删除七个 Principal Memory Consumption Draft Spec/Risk Review Markdown Gate，并以一个 Architecture AST/行为 Contract 保留未授权边界；最新完整收集为 2339 tests、0 collection errors；
25. H-3 已将 Principal Memory Firewall/Prompt/Consumption 三个根目录源码扫描文件收敛为四个 Architecture AST Contract 与三个 Unit 行为测试；旧路径引用为 0，相关组合 67 passed，最新完整收集为 2335 tests、0 collection errors；
26. H-3 已将 Runtime Config/Container/Embedding 边界收敛为四个 Architecture AST Contract，Identity/Exclusive Migration 源码 Gate 改为签名与真实行为验证；旧路径引用为 0，最新完整收集为 2332 tests、0 collection errors；
27. H-3 已将五个受保护 Production Evidence Runner 的重复 Writer/历史机器证据路径 Gate 合并为一个 Architecture AST Contract，并保留五个文件的真实行为覆盖；组合 79 passed，最新完整收集为 2328 tests、0 collection errors，结构化 Contract 与 `git diff --check` 通过；
28. H-3 已删除七个 Phase 5 前端精确字符串 Gate 与一个 LLM Source Inspection Gate；Bundle Analyzer 失败关闭行为和 Knowledge Evaluation Import Boundary 已迁入 Contract/Architecture 层，替代组合 21 passed，Frontend 全部 Gate 通过，最新完整收集为 2320 tests、0 collection errors；
29. H-3 已将五个 Fake/In-memory Knowledge/LLM/Principal Causal 测试迁入 Unit 层；活动旧路径引用为 0，迁移组合 54 passed，根目录测试文件为 191，Unit 文件为 25，完整收集保持 2320 tests、0 collection errors；
30. H-3 已将两个 Context Payload/Validation Contract 与六个算法/In-memory 测试迁入 Contract/Unit 层；活动旧路径引用为 0，迁移组合 98 passed，根目录测试文件为 183，Unit 文件为 31，Contract 文件为 24，完整收集保持 2320 tests、0 collection errors；
31. H-3 已将六个 Fake/In-memory Context Runtime/Compressor/Coordinator 测试迁入 Unit 层；活动旧路径引用为 0，迁移组合 54 passed，根目录测试文件为 177，Unit 文件为 37，完整收集保持 2320 tests、0 collection errors；
32. H-3 已将六个 Knowledge Artifact Contract 与八个 Fake/算法 Unit 文件迁入目标层级；活动旧路径引用为 0，迁移组合 181 passed、26 个数据库环境 skip，根目录测试文件为 163，Unit 文件为 45，Contract 文件为 30，完整收集保持 2320 tests、0 collection errors；
33. H-3 已将五个 Report Contract、十一个 Report Unit 与一个 Report Job PostgreSQL Integration 文件迁入目标层级；活动旧路径引用为 0，迁移组合 97 passed、17 个数据库环境 skip，根目录测试文件为 146，Unit 文件为 56，Contract 文件为 35，PostgreSQL Integration 文件为 24，完整收集保持 2320 tests、0 collection errors；
34. H-3 已将 Report Enqueue、Orphan Projection、Evaluation CLI、Task Microbatch 四个 Fake/In-memory 文件迁入 Unit 层，并将 Report Trace Artifact 测试迁入 Contract 层；活动旧路径引用为 0，全部 29 个 Report 文件组合 224 passed、18 个数据库环境 skip，根目录测试文件为 141，Unit 文件为 60，Contract 文件为 36，PostgreSQL Integration 文件为 24，完整收集保持 2320 tests、0 collection errors；
35. H-3 已把 Report Worker 混合文件拆为 Unit 与 PostgreSQL Integration，并提取共享报告 Fixture；Stage 47、Dual Workflow、Dual Canary 与当前文档路径已同步，活动旧路径引用为 0，全部 30 个 Report 文件仍为 224 passed、18 个数据库环境 skip，根目录测试文件为 140，Unit 文件为 61，Contract 文件为 36，PostgreSQL Integration 文件为 25；
36. H-3 已将 Report API 迁入 Acceptance、Report Tasks 迁入 Unit，并同步 Release Preflight、共享 Helper 和当前文档路径；活动旧路径引用与根目录 `test_report_*.py` 均为 0，迁移和调用方组合 103 passed，全部 30 个 Report 文件保持 224 passed、18 个数据库环境 skip，根目录测试文件为 138，Acceptance 文件为 18，Unit 文件为 62，Contract 文件为 36，PostgreSQL Integration 文件为 25，完整收集保持 2320 tests、0 collection errors；
37. H-3 已将七个跨子系统纯 Unit、三个 Dataset/Traceability Contract 与一个 Page Route Acceptance 迁入目标层级；Stage 49、Dual Workflow、Memory Acceptance、Graph/Recovery 和当前 Runbook 路径已同步，活动旧路径引用为 0，迁移和调用方组合 113 passed、10 个数据库环境 skip，根目录测试文件为 127，Acceptance 文件为 19，Unit 文件为 69，Contract 文件为 39，PostgreSQL Integration 文件为 25，完整收集保持 2320 tests、0 collection errors；
38. H-3 已将 Runtime 六个 Unit、四个 Contract 与一个 HTTP Acceptance 迁入目标层级；Stage 47/49、Dual Workflow、Memory Release、Stage 42 与历史 D-2 路径已同步，活动旧路径引用为 0，迁移和调用方组合 141 passed、1 个 POSIX-only skip，根目录测试文件为 116，Acceptance 文件为 20，Unit 文件为 75，Contract 文件为 43，PostgreSQL Integration 文件为 25，完整收集保持 2320 tests、0 collection errors；
39. H-3 已将 Agent Recorder 混合文件拆为 Unit 与真实 PostgreSQL Integration，并提取共享非测试 Fixture；四个 Agent Unit 与两个 Agent Contract 已迁入目标层级，Runner/当前文档路径已同步，活动旧路径引用为 0，迁移和调用方组合 101 passed、3 个数据库环境 skip，根目录测试文件为 109，Acceptance 文件为 20，Unit 文件为 80，Contract 文件为 45，PostgreSQL Integration 文件为 26，完整收集保持 2320 tests、0 collection errors；
40. H-3 已完成 Session 测试职责分层：Deletion 混合文件拆为 Unit/Acceptance，共享 Session Fixture 单一化，五个 Unit、两个 Contract 与两个 Acceptance 迁入目标层级；活动旧路径引用为 0，扩大组合 75 passed、26 个数据库环境 skip，直接调用方 38 passed，根目录测试文件为 101，Acceptance 文件为 22，Unit 文件为 85，Contract 文件为 47，PostgreSQL Integration 文件为 26，完整收集保持 2320 tests、0 collection errors；
41. H-3 已完成 Interview 第一批测试职责分层：七个 Unit、两个 PostgreSQL Integration 和共享 Interview Fixture 已归位；活动旧路径引用为 0，迁移组合 71 passed、18 个数据库环境 skip，直接调用方 34 passed，根目录测试文件为 92，Acceptance 文件为 22，Unit 文件为 92，Contract 文件为 47，PostgreSQL Integration 文件为 28，完整收集保持 2320 tests、0 collection errors；四个 Interview 混合文件继续留待下一切片拆分；
42. H-3 已完成 Interview 第二批三个混合文件拆分：Launch/Prep Regeneration 的 Unit/Acceptance 与 Generation Store 的 Unit/PostgreSQL Integration 已归位；活动旧路径引用为 0，Interview/Prep 扩大组合 107 passed、28 个数据库环境 skip，根目录测试文件为 89，Acceptance 文件为 24，Unit 文件为 95，Contract 文件为 47，PostgreSQL Integration 文件为 29，完整收集保持 2320 tests、0 collection errors；只剩 Durable Interview Graph 混合文件待拆；
43. H-3 已完成 Durable Interview Graph 最终拆分：八个 Unit、四个 PostgreSQL Integration 和专用表 Cleanup/Residue Contract 已归位，四套 Runner 同步两层 Gate；活动旧路径与根目录 Interview/Prep/Question Evaluation 文件为 0，完整权威组合 107 passed、28 个数据库环境 skip，根目录测试文件为 88，Acceptance 文件为 24，Unit 文件为 96，Contract 文件为 47，PostgreSQL Integration 文件为 30，完整收集保持 2320 tests、0 collection errors；Interview 测试分层子任务关闭；
44. H-3 已完成 Memory 第一批无歧义文件分层：五个 Unit、一个无数据库 PostgreSQL Naming Contract 与共享 Question Memory Fixture 已归位；活动旧路径引用为 0，迁移与调用方组合 53 passed，根目录测试文件为 82，Acceptance 文件为 24，Unit 文件为 101，Contract 文件为 48，PostgreSQL Integration 文件为 30，完整收集保持 2320 tests、0 collection errors；Memory 其余混合/Evidence/CLI 与 Principal Memory 文件继续留待后续批次；
45. H-3 已完成 Memory 第二批分层：Metrics Contract/Unit/Acceptance 与十一个受保护 Evidence/Receipt/CLI Contract 已归位，Runtime 顺序隔离和直接调用方组合 117 passed；活动旧路径引用为 0，根目录测试文件为 70，Acceptance 文件为 25，Unit 文件为 102，Contract 文件为 60，PostgreSQL Integration 文件为 30，完整收集保持 2320 tests、0 collection errors；真实 Drill、剩余 Shadow/Release、Principal Memory 与 PostgreSQL 文件继续留待后续批次；
46. H-3 已完成 Memory/Principal Memory 根目录收敛：十五个 Unit、十七个 Contract、一个 HTTP Acceptance、一个真实 PostgreSQL Integration 和三个共享 Fixture 已归位，Staging Preflight 本地/真实数据库职责已拆分；活动旧路径引用为 0，相关扩大组合 566 passed、31 个环境 skip，根目录测试文件为 37，Acceptance 文件为 26，Unit 文件为 117，Contract 文件为 77，PostgreSQL Integration 文件为 31，完整收集保持 2320 tests、0 collection errors；
47. H-3 已完成 Review/Durable Workflow 分层：八个 Unit、一个 Canary CLI Contract、一个真实 PostgreSQL Integration 和三个共享 Fixture 已归位，六类 Runner/Preflight 路径已同步；活动旧路径引用为 0，迁移组合 89 passed、24 个数据库环境 skip，直接调用方 49 passed，根目录测试文件为 27，Unit 文件为 125，Contract 文件为 78，PostgreSQL Integration 文件为 32，完整收集保持 2320 tests、0 collection errors；
48. H-3 已完成 PostgreSQL/Vector 分层：七个 Unit、三个 Contract、一个真实 PostgreSQL Integration 与两个共享 Fixture 已归位，最后两处跨测试导入降为 0；迁移组合 95 passed、9 个数据库环境 skip，直接调用方 27 passed，根目录测试文件为 16，Unit 文件为 132，Contract 文件为 81，PostgreSQL Integration 文件为 33，完整收集保持 2320 tests、0 collection errors；
49. H-3 已完成根目录测试清零：一个 API Acceptance、七个 Unit 与八个 Contract 已归位，活动 Runner/当前 Stage 文档路径已同步；迁移组合 185 passed，直接调用方 27 passed，根目录 `test_*.py` 和跨测试模块导入均为 0，Acceptance 文件为 27，Unit 文件为 139，Contract 文件为 89，PostgreSQL Integration 文件为 33，完整收集保持 2320 tests、0 collection errors；历史文档和 Release Preflight 仍有已删除根路径引用需要审计；
50. H-3 已完成非冻结根路径清理：Release Preflight、Runner、Runbook 与当前文档中的 `tests/test_*.py` 引用为 0，退休 Static UI/Docs Gate 已明确标记为历史不可运行结果，直接调用方 27 passed；冻结 Execution Baseline 仍保留历史 Task 0 文件身份；
51. H-3 已完成 Stage/Profile 收敛：四套 Artifact Audit 与 Stage 48/49 Runner 分别合并为单一 Profile CLI；六个重复脚本及活动引用均为 0；
52. H-3 已删除七个无调用者的 API/Repository/Interview/Draft/Config Legacy Compatibility 出口；Config/Runtime/Memory/Provider 扩大回归 171 passed，Stage/Profile 与 Legacy 组合 101 passed，`compileall` 通过；完整收集为 2321 tests、0 collection errors，结构化 Contract 与 `git diff --check` 通过；
53. H-3 已删除 Report Enqueue 无效 `background_tasks` 参数和测试 Fake，扩大回归 143 passed；其余首轮候选均有活动语义并已归属 Wave E/F 或 Adapter 收敛，不作机械删除；
54. H-3 已把 `docs/superpowers/plans/` 定义为冻结历史档案并提供八个退休入口的当前 Profile CLI 映射；非档案当前维护文档中的退休模块引用为 0，冻结 Execution Baseline 明确保留为历史身份；
55. H-3 已将 Stage 44A/44B1 Knowledge Acceptance 收敛为单一 Profile CLI 和共享安全指标过滤；旧 Runner 模块路径与活动引用为 0，Knowledge 扩大组合 148 passed，完整收集 2321 tests、0 collection errors；
56. H-3 已完成 Stage 47/47.2 Acceptance Runner 收敛：四个 Repository Profile 共用单一 CLI，两阶段输出协议保持独立；Stage 47 无 DSN 时显式 `BLOCKED`；旧模块活动引用为 0，Profile/Contract 23 passed，扩大组合 215 passed、37 个数据库环境 skip，完整收集为 2325 tests、0 collection errors；
57. H-3 已完成 LangGraph Recovery/Dual Acceptance 收敛：两个 Profile 共用单一 CLI 和 Artifact 基础能力，Recovery 无 DSN 或零真实 passed 时显式 `FAIL`；旧模块活动引用为 0，定向 13 passed，扩大组合 135 passed、74 个数据库环境 skip，完整收集为 2329 tests、0 collection errors；
58. H-3 已删除旧 Budget Shadow JSON/Markdown Gate，现行严格 Observer 成为唯一入口；Operational Evidence 默认输出与 Approval Packet 默认输入已统一，相关组合 35 passed，历史完整收集为 2323 tests、0 collection errors；
59. H-3 已完成 Operational Shadow 历史输入迁移第一批：Budget、Write、Read、Lifecycle、Restore 五类受保护 Bundle 连同 Proposal Review 共 6 项进入 Input Manifest；错误 Revision 失败关闭，定向 15 passed、关联组合 37 passed，完整收集当时为 2325 tests、0 collection errors；
60. H-3 已完成 Operational Shadow 历史输入迁移第二批：统一 Publisher 为 RC、Regression、Staging、Status、Security 生成严格签名 Bundle，Operational 消费端 Input Manifest 扩展为 11 项，Foundation/Staging 改为验证签名 RC，旧静态 Production Manifest 删除；扩大组合 143 passed、1 个未配置 `POSTGRES_DSN` 的 Staging Integration skip，最新完整收集为 2336 tests、0 collection errors；
61. H-3 已完成 Budget/Status Historical Trust 上游迁移：Budget 输出绑定 RC/Staging 两项 Input Manifest，Status 只从五份受保护 Payload 投影三面板；旧宽松 Reader、失效 Runbook 参数和十个历史机器/固定 Revision 记录已删除，最终扩大组合 106 passed、1 个数据库环境 skip，最新完整收集 2339 tests、0 collection errors；
62. H-3 已为 `docs/superpowers/specs/` 建立档案边界并新增 Current Documentation Path Architecture Contract；90 份当前文档中的 28 个脚本模块和 56 个测试路径均可解析，Architecture 全目录 45 passed，完整收集 2343 tests、0 collection errors；
63. H-3 已完成剩余 Stage/Profile 和不依赖 Wave F/G 的 Compatibility 所有权审计；Stage 38/43B、Knowledge Profile、Report Runtime Preflight 与四类已知 Compatibility 均有当前调用者或后续 Wave 所有权，行为组合 28 passed，没有新的安全删除项；
64. E-3 真实 Gate 已使用仓库外批准记录执行：Critical Contract 为 4 passed、0 skipped，PostgreSQL 权威标记集为 194 passed、2149 deselected、0 skipped，目标、Ownership、Permission、Cleanup 与 Stage 43B Receipt 均已验证；
65. E-3 真实数据库扩大组合为 223 passed、0 skipped；限权角色认证夹具已修复，失败运行留下的确切角色已单独清理，标记集后及扩大组合后 Relation/Role Residue 均为 0；
66. E-3/E-3d 已关闭；E-4 已建立六个独立 Row Mapper、物理 `row_schema_version`、v16 Legacy Backfill 与 Unsupported Version Error，旧集中式 Serialization 模块已删除；
67. E-4 真实 Session/Launch/Migration 组合为 50 passed，Wave E 扩大组合为 243 passed、0 skipped；CAS、Fencing、Launch Atomicity、Transactional Outbox 与 Repository Failure Rollback 保持通过；
68. 最后一次 Wave E 生产代码修改后的完整 Python Gate 为 2349 passed、3 个非数据库 skip、0 failed；skip 为两个 POSIX-only Contract 和一个 Real LLM opt-in，完整 Gate 后 Relation/Role Residue 均为 0；
69. Wave E 已实现待终审，Report Pipeline 与 Reliability Adapter 成为下一顺序执行项；
70. Wave F 已将具体 Runtime Failure Mapping 迁入 `app/adapters/reliability/`，旧 `runtime_work.py` 删除，生产旧 Import 与旧错误码活动引用为 0；
71. `ReportGenerationPipeline`、Progress/Evaluation/Assembly/Quality 组件、四个 Report Job Port、Worker、Reliability、PDF 与 Versioned Rubric 已形成单一命名 Owner；
72. 全部 Report 文件真实 PostgreSQL 组合为 278 passed、0 skipped；最后一次 Wave F 生产代码修改后的完整 Python + PostgreSQL Gate 为 2354 passed、3 个非数据库 skip、0 failed，完整 Gate 后 Relation/Role Residue 均为 0；
73. Wave F 已实现待终审，执行窗口进入 Wave G Principal Memory、Knowledge、Vector 与 Context Artifact 边界收敛；
74. Wave G 已建立 Principal Memory Domain/Adapter/共享 Store Contract 与权威命名 Owner，Knowledge Domain 与 PgVector Adapter/Codec，以及 Context Artifact Domain、共享 Integrity Policy、Memory/PostgreSQL Adapter 与 Recovery Service；
75. Wave G 扩大组合为 516 passed、1 个 POSIX-only skip；真实 PostgreSQL Adapter 组合为 45 passed、0 skipped，Relation/Role Residue 均为 0；
76. Static Memory Center 已迁为懒加载 React 页面，旧三个静态文件和源码字符串 Contract 删除；Memory Browser Gate 8 passed，Frontend Lint/Build/Bundle/Lazy-route Gate 全部通过；
77. Wave G 已实现待终审，执行窗口进入 H-3 最后一次完整 Gate；
78. 最终审查发现并修复 Foundation/Bundle/Release Preflight 路径漂移与生产源码内置 PostgreSQL 凭据；
79. 最后一次生产代码修改后完整 Python + PostgreSQL 为 2352 passed、3 个非数据库 skip、0 failed，PostgreSQL 0 skip，Relation/Role Residue 0/0；
80. Frontend Lint/Build/Bundle/Lazy-route 全部通过；十类 Browser 为 101 passed、1 Real-model opt-in skip、0 failed，端口/进程/临时文件残留为 0；
81. `docs/refactoring-audit.md` 无 BLOCKED，Wave A–H 与 Final Audit 统一完成，Release 转为 `ready`。

Wave E、Wave F 与 Wave G 窗口已经关闭。已保留的 pre-V15 Launch 是公开 API 兼容分支，不得在缺少外部版本退出约定时机械删除；新产品前端只使用权威 Prep Plan Tuple，最终 Compatibility 审查必须确认该分支没有新增调用者。最后阶段只允许关闭 H-3 Gate 或修复 Gate 揭示的问题，不得修改 Report 评分语义、Principal Memory 隔离或绕过已关闭的 UnitOfWork、Reliability、Report Pipeline 与 Context Integrity Contract。

Wave E 的退出条件：

- API Router 不再集中承担所有领域的请求解析与依赖组装；
- Session Command、State、Snapshot 与 Application Workflow 依赖 Domain 和现有 Port，不依赖具体 PostgreSQL 实现；
- UnitOfWork 明确拥有 connection/transaction，Session、Message、Outbox 与 Receipt Repository 可共享同一事务；
- 拆分前后的 CAS、Fencing、Transactional Outbox、Launch Atomicity、序列化字节和对外 API 行为保持兼容；
- In-memory 与 PostgreSQL Adapter 运行同一 Contract Test；
- Wave E 定向测试、原子性 Gate、完整 Python Suite 与必需的真实 PostgreSQL Gate 均在最后一次 Wave E 代码修改之后通过；
- `git diff --check` 通过，且没有任务生成的数据库对象、临时文件、后台进程或其他运行残留。

## 23. Definition of Done

本重构计划只有在以下条件全部满足时才算完成：

- 最终审查开始前，Wave A–H 全部达到各自退出条件并标记为 `已实现待终审`；第 24 节逐项复验通过后，再统一转换为 `已完成`；
- 所有 P0 风险均有回归测试并已关闭；
- Evidence Mutation Matrix 全部通过；
- PostgreSQL Target、Ownership、Cleanup 与 Residue Receipt 全部启用；
- Runtime Config 与 Container 成为唯一组装入口；
- 现有 app/ports 已完成审查与收敛，项目中不存在平行的第二套 Ports；
- Runtime Reliability 不存在 domain/runtime 与 runtime 双重边界；
- API、Session、Report、Memory、Knowledge 的职责边界清晰；
- Session、Message、Outbox 与 Receipt Repository 拆分后仍通过同一 UnitOfWork 保持原事务边界；
- 现有 CAS、Fencing、Launch Atomicity 与 Transactional Outbox 语义未改变；
- In-memory 与 PostgreSQL Adapter 运行同一 Contract；
- Context Artifact Key、Identity、Owner、Lease、Fencing、Immutability 与 Replay 兼容性全部通过；
- 历史 Stage 已迁移为 Policy 或薄 CLI；
- Markdown 不再承担正式 Gate；
- Browser Suite 已去重并可由项目运行时稳定执行；
- Legacy 与 Static Compatibility 已按迁移计划删除；
- 生产代码无测试依赖、无敏感输出、无全局隐式配置；
- 最后一次生产代码修改之后，完整 Python Suite、前端/Browser Gate 与所有必需的真实基础设施 Contract 全部通过；
- 第 21 节和本节已经逐条审查，每项都有可复核证据且没有 `BLOCKED`；
- 全部自动化 Gate 通过，工作区无运行残留、无意外生成物，Git 变更清单与本计划范围一致；
- 最终审查发现的问题已经修复，并重新执行受影响的定向测试和完整 Gate；
- 最终审查报告已写入 `docs/refactoring-audit.md`。是否暂存、提交、推送或创建 PR 由用户决定，不得作为计划完成的隐含前提。

## 24. 最终自动审查流程

所有 Wave 实现完成后必须自动执行本节，不等待额外提醒。审查不是摘要性代码评语，而是一次“发现问题 → 修复 → 复验”的闭环。

### 24.1 冻结审查基线

1. 记录当前分支、Revision、解释器身份和最后一次生产代码修改时间；
2. 导出 Git 变更清单，区分计划内修改、用户原有修改和意外生成物；
3. 记录 PostgreSQL、Redis、Browser 与可选 Provider 的可用性；
4. 禁止用更早的测试结果代替当前基线结果。

### 24.2 静态与结构审查

至少执行并记录以下扫描：

- 生产代码导入 `tests`；
- Domain 或 Application 层直接读取环境变量；
- 平行 Evidence validator、writer、privacy scanner、canonical hash 与 status formatter；
- 本地 PostgreSQL prefix/drop/cleanup helper 和未经 Owned Scope 的数据库操作；
- 裸 `write_text(json.dumps(...))` 受保护 Evidence 写入；
- 运行时读取历史 `docs/*.json` 作为批准、发布或验收事实；
- 明文 DSN、密码、Provider Key、完整连接串和敏感标识输出；
- `app/domain/runtime`、第二套 Ports、重复 Retry/Lease/Heartbeat 与重复 Runtime Singleton；
- Markdown 业务 Gate、源码字符串架构 Gate、历史 Stage 与已替代兼容入口；
- 大文件、循环依赖和越层依赖是否达到各 Wave 设定的拆分目标。

扫描命中不等于一律删除；必须分类为“真实问题、允许的兼容层、测试夹具、历史文档或误报”，并在审查报告中给出依据。

### 24.3 动态验证

按风险从小到大执行：

1. 变更文件对应的 Unit 与 Contract 测试；
2. PostgreSQL、Redis、Provider、Filesystem 与 Browser Integration 测试；
3. Acceptance、Architecture 与跨平台可复现性 Gate；
4. 真实 PostgreSQL Permission、Ownership、Cleanup 与 Residue 测试，残留对象必须为零；
5. 完整 Python Suite；
6. 前端静态检查、单元测试与 Browser 权威 Suite；
7. `git diff --check` 和最终 Git 变更清单复核。

Critical Contract 不允许以 skip 完成验收。可选 Provider 或 Real-model Smoke 可以不阻塞普通 CI，但必须在报告中与关键 skip 分开列出。

### 24.4 逐条审计与修复闭环

在 `docs/refactoring-audit.md` 中为第 21 节和第 23 节建立审计表，至少包含：

| 字段 | 说明 |
|---|---|
| Requirement | 原始验收条目，不得改写为更弱条件 |
| Status | `PASS`、`BLOCKED` 或 `N/A` |
| Evidence | 测试、扫描、代码位置或运行 Receipt |
| Finding | 失败原因、风险与受影响范围 |
| Remediation | 实际修复内容；无问题时写 `None` |
| Reverification | 修复后的定向测试与完整 Gate 结果 |

任何 `BLOCKED` 都必须先尝试修复并复验。只有确实需要新的权限、外部协调或用户决策时才可保留阻塞；不得因为工作量大、环境配置麻烦或测试耗时而跳过。修复后若生产代码发生变化，之前的完整 Suite 结果自动失效，必须重新运行。

### 24.5 完成判定

同时满足以下条件后，才把文档状态改为“已完成”：

- Wave A–H 均无未完成任务，并在逐项复验通过后标记为 `已完成`；
- 第 21 节和第 23 节审计表无 `BLOCKED`；
- 所有修复均已复验；
- 最终完整 Gate 晚于最后一次代码修改；
- 真实基础设施无残留资源；
- 审查报告、计划状态与仓库实际状态一致。

若任一条件不满足，计划保持“执行中”，并把下一项未完成任务写回当前版本的执行账本。
