# RAG 学习演示收尾实施计划 v1.1

> 日期：2026-08-15
>
> 状态：`COMPLETED`
>
> 基线分支：`master`
>
> 基线提交：`e674c3658be28472ec2a20871ec641542e38acd4`
>
> 目标读者：负责 Knowledge RAG、FastAPI、React/Vite、测试与项目文档的维护者
>
> 文档类型：实施型 How-to Plan；不是发布批准、生产上线计划或算法效果报告
>
> v1.1 修订：增加 Baseline & Audit Migration；冻结 Fusion 缺失值、Diagnostic Profile Identity 和 Corpus capability 契约；执行方式改为单一实施子代理按主线程逐轮指令执行，主线程逐轮独立复核并在最终 Review Gate 收口
>
> 完成记录：Batch A～E 与 Review Gate 已完成；聚焦后端 `107 passed`、相关 Knowledge/RAG `353 passed / 1 protected PostgreSQL node deselected`、Architecture + Acceptance `449 passed`、前端 `135 passed`，ESLint、production build、bundle gate、compileall、Closure Audit `20/20` 与 Git 模式 `23/23`、`git diff --check` 均通过。PostgreSQL、外部 Provider、Corpus 写入、人工标注、paired evaluation、No-evidence 调参、截图与视觉验证均未执行。

## 0. 执行摘要

当前 Knowledge RAG 已完成从生产发布治理模型到学习项目/技术展示模型的主体简化。本轮只完成最后一轮代码、交互和文档收尾，不继续扩展检索架构，也不把人工标注、真实数据库评测或外部 Provider 运行作为完成条件。

实施顺序固定为：

```text
Batch 0  Baseline & Audit Migration
    ↓
Batch A  Frozen Replay 权限边界修正
    ↓
Batch B  Knowledge RAG 遗留语义清理
    ↓
Batch C  Inspector 单引擎 Fusion 策略切换
    ↓
Batch D  README 精简与开发历史归档
    ↓
Batch E  本地回归、静态审计与交付
    ↓
Review Gate  主线程独立自动审查与去冗余收口
```

本轮完成后，维护者应能在不改变业务 Runtime 默认行为的前提下：

1. 仅开启 RAG Console 时读取 Frozen Replay；
2. 明确区分 Fixed Weighted RRF 与 Query-aware Weighted RRF；
3. 在 Inspector 的单引擎 Hybrid 诊断中主动选择两种 Fusion 模式；
4. 从活动 Knowledge RAG 领域模型中移除无效的 Shadow 概念和旧生产 Gate 文案；
5. 从 README 快速理解项目现状，并通过归档链接查看完整开发历史。

## 1. 范围冻结

### 1.1 本轮包含

- 修正 Frozen Replay 的只读 capability 边界；
- 增加并验证 Frozen Replay、Live Inspection、Compare 的权限隔离测试；
- 审计并清理活动 Knowledge RAG 范围内的 `RetrievalIntent.SHADOW`；
- 保留 Memory、Context Compression、LangGraph 等其他子系统的 Shadow 语义；
- 将 Remote Reranker 的旧 production-gate 错误文案改为当前 Demo Scope 文案；
- 为 Inspector 单引擎 Hybrid 模式增加受控的 Fixed / Query-aware Fusion 选择；
- 为策略选择建立明确的请求契约、服务端 Profile Identity 和安全响应字段；
- 建立仓库内、branch-neutral 的 Closure Audit，并与上一轮 Simplification Audit 分离；
- 精简 README，并把历史 Stage 记录迁入 `docs/archive/development-history.md`；
- 更新当前 RAG 架构状态文档和 Console Runbook；
- 运行不依赖外部授权的后端、前端、架构与 Acceptance 测试。

### 1.2 本轮明确不包含

以下事项不是阻塞项，也不得在执行中被重新加入完成定义：

- 75 tuning 人工 Ground Truth；
- Annotator A、Annotator B 或 Adjudication；
- Legacy / Semantic / Lexical / RRF 的真实 PostgreSQL paired evaluation；
- 新 Eval Artifact、Candidate Artifact 或 holdout 运行；
- SiliconFlow、BAAI/bge-m3 或其他外部 Embedding Provider 调用；
- 新 Corpus 版本创建、激活、发布、回滚或重嵌入；
- No-evidence 阈值调参、Evidence Sufficiency 策略校准或 F1 优化；
- 证明 Query-aware 优于 Fixed RRF；
- Reviewer / Follow-up blind A/B；
- Remote Reranker 或 Cross-Encoder 集成；
- GraphRAG、Shadow、Canary、Promotion、Retirement 或 Rollout 控制面；
- 三路 Compare、四路 Compare 或新的在线实验状态机；
- 浏览器截图、视觉基准图或图像验收。

### 1.3 允许作出的结论

本轮只允许证明：

```text
权限边界符合只读/实时执行语义；
两种 Fusion 模式能够被显式选择并正确传递；
Query-aware 算法路径被调用并返回实际决策；
活动领域模型和文档与学习演示定位一致；
本地自动化测试通过。
```

本轮不得声称：

```text
Query-aware 的真实指标优于 Fixed RRF；
No-evidence 已达到目标质量；
Hybrid 已整体优于 Legacy；
系统已达到生产发布条件。
```

### 1.4 执行与审查协议

本计划采用“单一实施子代理逐轮执行、主线程逐轮独立复核、最终 Review Gate 收口”的执行方式：

1. 主线程逐轮下达明确范围，先从 Batch 0 冻结计划、基线提交、当前工作树状态和 Closure Audit 身份；
2. 单一实施子代理只执行主线程本轮明确授权的 Batch 范围，不跨批次提前扩展；
3. 实施子代理每轮完成修改、验证、证据记录和风险回报后立即停止；没有主线程下一轮明确指令时，不自行推进；
4. 主线程在每轮后独立检查 diff、测试结果、重复实现和剩余风险，再决定是否下达下一轮指令；
5. 实施与复核均不得把实现意图、注释或变更前测试当作完成证据；
6. Batch A～E 完成后，由主线程独立执行最终 Review Gate，重新运行 Closure Audit、自动审查和完整本地回归；
7. Review Gate 发现问题时，由主线程重新划定修复范围并下达指令，修复后重复验证，直到完成定义逐项有权威证据。

为避免冗余代码，执行过程冻结以下约束：

- Fusion 模式枚举只能有一个权威定义；
- Diagnostic Profile 派生只能有一个 helper；
- 不复制 `QuerySignalAnalyzer`、Weighted RRF、Rerank 或 Evidence Gate 算法；
- 前端复用现有 RAG 表单、状态、显示映射和 CSS primitive；
- 不为两个选项引入新的状态管理库、UI 框架或组件体系；
- 不新增第二套 API client、错误处理、复制按钮或响应格式化逻辑；
- 测试优先扩展现有 fixture/fake，不复制完整检索实现；
- README 历史迁移采用移动语义，避免当前文档与归档长期维护两份相同正文；
- 纯机械格式化、无关重命名和无关重构不得混入本轮 diff。

## 2. 当前基线与问题定义

### 2.1 Frozen Replay capability 不一致

当前路由：

```text
GET /api/rag/evaluations/{artifact_sha256}/cases/{case_id}/diagnostic-snapshot
```

在 `app/api/rag/routes.py` 中依赖 `require_live_execution`，但 `artifact_replay()` 只读取已冻结 Artifact/Snapshot：

```text
不调用 Retriever
不调用 Embedding Provider
不产生新的检索结果
不持久化 Live Query
provider_call_possible = false
```

这与 README 中“未启用 Live Execution 时仍可使用 frozen replay”的说明不一致。

### 2.2 Query-aware 已实现，但缺少受控演示入口

当前 `ResolvedRetrievalProfile` 已有：

```python
query_aware_fusion: bool = False
```

`QuerySignalAnalyzer` 也已支持 lexical-dominant、semantic-dominant 与 balanced 决策，但 Inspector 请求只能选择 `legacy | hybrid-v2`，不能显式选择 Hybrid Fusion 模式。

本轮只解决“如何安全地选择和展示”，不解决“哪种策略效果更好”。

### 2.3 活动领域模型仍有旧治理残留

`RetrievalIntent.SHADOW` 仍存在于活动枚举，但 Knowledge RAG Shadow 已被移出活动实现。配置加载器中的 Remote Reranker 错误信息仍使用旧的 ranking-gap production gate 语言。

### 2.4 README 当前状态与历史记录混排

README 顶部已正确描述 Learning Project / Technical Showcase，但后续保留大量 Stage 23～47、Canary、Shadow 与历史生产治理记录。历史信息需要保留，但不应继续占据项目首页正文。

### 2.5 旧审计身份不适用于本轮 Closure

现有 `scripts/audit_rag_demo_simplification_plan.py` 绑定上一轮 Simplification Plan 的外部路径、固定 SHA 和 `git.master_not_modified` 规则。它可以继续保留历史用途，但不能作为本轮 Closure 的权威完成 Gate：外部路径不可移植，固定旧 SHA 与当前计划身份不一致，分支限制也会在最终合并回 `master` 后天然失效。

## 3. 冻结设计决策

### 3.1 Fusion 模式使用受控枚举

新增面向诊断请求的枚举或 Literal：

```text
fixed_weighted_rrf
query_aware_weighted_rrf
```

建议字段名：

```text
hybrid_fusion_mode
```

规则固定为：

- 默认值为 `fixed_weighted_rrf`，保持现有行为；
- 只允许 `engine=hybrid-v2` 使用 `query_aware_weighted_rrf`；
- `engine=legacy` 携带 Query-aware 模式时返回稳定的 422 请求错误；
- 前端不得上传任意 `semantic_weight`、`lexical_weight` 或 `rrf_k`；
- 后端根据枚举生成 Profile，不信任前端上传的 Profile 内容；
- Compare Request 本轮不增加该字段，继续执行 Legacy compatibility profile 与当前 Fixed Hybrid profile；
- 业务 Runtime 配置不增加默认启用开关，不改变 `KNOWLEDGE_ENGINE` 和活动 Profile；
- Frozen Replay 只展示 Artifact 已记录的策略，不使用当前设置回填历史字段。

### 3.2 Diagnostic Variant 与 Runtime Profile Identity 分离

服务端增加单一 helper，从 Runtime Profile 派生 Diagnostic Profile，例如：

```text
resolve_diagnostic_profile(runtime_profile, hybrid_fusion_mode)
```

派生规则：

```text
fixed_weighted_rrf
→ query_aware_fusion = false
→ 保持基础 semantic/lexical 权重

query_aware_weighted_rrf
→ query_aware_fusion = true
→ 保持基础权重作为 QuerySignalAnalyzer 的输入
→ 实际最终权重由现有 Query-aware 算法产生
```

本轮冻结以下身份规则，不再使用 identity suffix，也不新增 diagnostic profile version：

```text
profile_id
profile_version
→ 保持 Runtime Profile 的原始身份，不因 Inspector 选择而变化

requested_hybrid_fusion_mode
effective_hybrid_fusion_mode
→ 作为 Diagnostic Variant 的权威身份
```

响应契约固定为：

```python
requested_hybrid_fusion_mode: HybridFusionMode | None
effective_hybrid_fusion_mode: HybridFusionMode | None
```

规则：

- `engine=hybrid-v2` 时，两字段均为受控 `HybridFusionMode`；
- `engine=legacy` 时，两字段均为 `null`，表示“不适用”；
- 历史 Frozen Replay 没有记录时，两字段均为 `null`，表示“未记录”；
- API 不返回字符串 `not_recorded`，也不把它加入 `HybridFusionMode`；
- UI 根据响应上下文把 `null` 映射为“未记录”或“不适用”；
- `profile_id`、`profile_version` 不拼接 `diag-*` 后缀；
- 不修改业务 Runtime Profile 常量、默认配置或 Interview Consumer 行为。

### 3.3 单引擎 Inspector 优先，不扩展 Compare DTO

前端交互固定为：

```text
诊断方式
├── Legacy / Hybrid 对比
└── 单引擎诊断
    ├── Legacy
    └── Hybrid V2
        └── Fusion 模式
            ├── 固定权重 RRF
            └── 查询感知 RRF
```

仅在“单引擎诊断 + Hybrid V2”时显示 Fusion 模式选择。Compare 页面继续保持两侧，避免把本轮收尾改造成新的多路比较系统。

### 3.4 SHADOW 清理只限 Knowledge Retrieval

删除前先扫描：

```text
app/
tests/
eval/
artifacts/
docs/architecture/
docs/runbooks/
```

重点检查：

```text
RetrievalIntent.SHADOW
"intent": "shadow"
intent=shadow
```

处理规则：

- 若活动代码与可发现 Artifact 均无历史值，直接删除枚举项并补契约测试；
- 若历史 Artifact 存在 `intent=shadow`，在 Artifact 读取边界增加独立 historical mapping；
- 不为历史兼容继续向活动请求 DTO 暴露 `SHADOW`；
- 不删除 Memory、Context Compression、LangGraph 或 Report Pipeline 中语义独立的 Shadow。

### 3.5 README 历史只迁移，不删除

README 仅保留当前项目入口；历史内容移动到：

```text
docs/archive/development-history.md
```

迁移必须保留原始 Stage 信息，并在 README 与归档文件顶部互相链接。不得把历史事实改写成当前能力，也不得因精简 README 删除仍有效的启动说明。

## 4. Batch 0：Baseline & Audit Migration

### 0.1 冻结基线和实施分支

权威基线固定为：

```text
master @ e674c3658be28472ec2a20871ec641542e38acd4
```

开始实施前：

1. 记录 `git rev-parse HEAD`、`git branch --show-current` 和 `git status --short`；
2. 确认当前 HEAD 等于权威基线，或确认当前分支以该提交为祖先；
3. 在不清理、不重置、不覆盖现有工作树修改的前提下，创建或复用 `codex/rag-learning-demo-closure-v1`；
4. 若同名分支已存在，只能在祖先关系和现有 diff 均可解释时复用；
5. 不执行 `reset --hard`、强制 checkout、自动 stash、自动 commit 或自动 merge。

分支用于隔离实施过程，不是最终完成条件。Closure 合并回 `master` 后，审计仍必须有效。

### 0.2 迁移 Closure Audit 身份

保留以下旧文件作为上一轮 Simplification Plan 的历史审计：

- `scripts/audit_rag_demo_simplification_plan.py`
- `tests/unit/test_rag_demo_simplification_audit.py`

新增本轮权威审计：

- `scripts/audit_rag_demo_closure.py`
- `tests/unit/test_rag_demo_closure_audit.py`

Closure Audit 固定遵守：

- 不读取用户 Downloads 目录或其他开发机绝对路径；
- 不校验外部 Plan 文件 SHA；
- 以仓库中的本计划和最终代码契约为执行说明，但审计结论只由仓库状态、测试和静态不变量产生；
- 使用 `git merge-base --is-ancestor e674c3658be28472ec2a20871ec641542e38acd4 HEAD` 验证基线祖先关系；
- 不使用 `branch != master` 或 `git.master_not_modified` 作为完成 Gate；
- 对合并前的实施分支和合并后的 `master` 保持 branch-neutral；
- 执行 `git diff --check` 等价校验；工作树是否 dirty 只作为证据报告，不因未获授权创建 commit 而直接失败；
- 临时文件、调试输出、生成缓存和截图仍由最终 Review Gate 阻断；
- 只读、确定性、无 Provider、无数据库写入，不修改工作树。

Closure Audit 至少覆盖：

- Frozen Replay、Live Inspection、Compare 的 capability 边界；
- Knowledge Retrieval 活动 Shadow 清理；
- Remote Reranker Demo Scope 文案；
- Fusion 模式枚举、`null` 缺失语义和 Diagnostic Variant 身份；
- Runtime/Compare 不变；
- README、历史归档和当前权威文档；
- 禁止任意权重输入、外部 Provider 运行和新 Corpus 写入。

### 0.3 完成条件

- 基线提交和当前工作树状态已记录；
- 实施工作不再直接依赖 `master` 的未隔离状态；
- 新 Closure Audit 有独立测试；
- 新审计不依赖下载目录、旧 Plan SHA 或 `git.master_not_modified`；
- 旧 Simplification Audit 的历史身份未被冒充为本轮 Closure Audit；
- Batch A～E 和最终 Review Gate 均引用新 Closure Audit。

## 5. Batch A：Frozen Replay 权限边界修正

### A1. 修改路由依赖

目标文件：

- `app/api/rag/routes.py`

修改：

```text
evaluation_snapshot
require_live_execution
        ↓
require_rag_console
```

Live Inspection 与 Compare 继续依赖 `require_live_execution`。

### A2. 增加 capability boundary 测试

目标文件：

- `tests/unit/test_rag_console_routes.py`
- `tests/contracts/test_rag_console_contracts.py`
- 必要时扩展 `tests/acceptance/test_rag_console_acceptance.py`

测试矩阵：

| Console | Live Execution | Frozen Replay | Live Inspection | Compare |
| --- | --- | --- | --- | --- |
| false | false | 404 | 404 | 404 |
| true | false | 200 | 404 | 404 |
| true | true | 200 | 200 | 200 |

附加断言：

- 非 loopback 请求继续被拒绝；
- Replay 调用路径不调用 repository live retrieval；
- `provider_call_possible=false`；
- 响应不包含 raw query、KnowledgeChunk 正文、Provider payload、本机路径或密钥；
- 非法 Artifact SHA、case ID 和路径型输入继续 fail-closed；
- 错误响应不回显用户输入。

### A3. 更新文档契约

目标文件：

- `docs/runbooks/rag-engineering-console.md`
- `README.md`

明确三个 capability：

```text
RAG_CONSOLE_ENABLED
→ Overview、Evaluation、Frozen Replay、Evidence Trace、Corpus 只读

RAG_LIVE_EXECUTION_ENABLED
→ Live Inspection、Legacy / Hybrid Compare

RAG_CORPUS_WRITE_ENABLED
→ Corpus validate / create version 等受控写操作
```

API 层只有 `POST /corpus/drafts/validate` 和 `POST /corpus/versions`；不得重新描述或恢复独立 `/releases/activate` endpoint。内部 `create_version()` 的提交/激活语义不改变该 API 边界。

### A4. 完成条件

- 上述三组 capability 状态均有自动化测试；
- Frozen Replay 在 Console-only 状态可用；
- Live 请求仍然 fail-closed；
- 没有扩大 Corpus Write 权限；
- 没有 Provider 调用。

## 6. Batch B：Knowledge RAG 遗留语义清理

### B1. 审计并删除 `RetrievalIntent.SHADOW`

目标文件可能包括：

- `app/domain/knowledge/retrieval.py`
- `app/application/knowledge/diagnostics_service.py`
- 相关 DTO、fixture 与测试

步骤：

1. 扫描活动代码和可发现 Artifact；
2. 判断是否需要 historical parser；
3. 从活动 `RetrievalIntent` 删除 `SHADOW`；
4. 增加测试，证明活动请求不接受 `intent=shadow`；
5. 再次全局扫描，确认剩余 Shadow 均属于其他明确子系统或历史归档。

### B2. 修改 Remote Reranker 错误文案

目标文件：

- `app/runtime/config/loader.py`
- `tests/unit/test_effective_runtime_config.py`

建议英文错误信息：

```text
knowledge remote reranker is not enabled in the current demo scope
```

要求：

- 继续 fail-closed；
- 不新增 Remote Reranker 开关路径；
- 不保留 ranking-gap promotion gate 暗示；
- 同步更新测试断言和当前状态文档。

### B3. 更新 Closure Audit

目标文件：

- `scripts/audit_rag_demo_closure.py`
- `tests/unit/test_rag_demo_closure_audit.py`

增加或强化检查：

- 活动 `RetrievalIntent` 不含 Shadow；
- Knowledge RAG 路由不含 Shadow/Promotion/Retirement；
- Remote Reranker 错误信息使用 Demo Scope 语义；
- 不把 Memory/LangGraph 的 Shadow 当作违规项。

旧 Simplification Audit 可继续验证其历史计划，但不得继续承担本轮 Closure 的 plan identity、分支或完成条件。

### B4. 完成条件

- 活动 Knowledge Retrieval API 不再暴露 `shadow` intent；
- 历史 Artifact 如有需要仍可安全读取；
- 其他子系统的 Shadow 没有被误删；
- Remote Reranker 仍禁用，但错误原因符合当前项目定位。

## 7. Batch C：Inspector 单引擎 Fusion 策略切换

### C1. 扩展安全请求契约

目标文件：

- `app/application/knowledge/diagnostic_models.py`
- `tests/contracts/test_rag_console_contracts.py`

为 `RetrievalInspectionRequest` 增加：

```text
hybrid_fusion_mode:
  fixed_weighted_rrf
  | query_aware_weighted_rrf
```

默认值固定为 `fixed_weighted_rrf`。

契约测试必须证明：

- 任意自定义权重字段仍被 `extra="forbid"` 拒绝；
- 非法策略值返回 422；
- Legacy + Query-aware 组合被拒绝；
- Compare Request 没有获得任意策略或权重上传能力；
- 请求校验错误不回显 query 原文。

### C2. 派生 Diagnostic Profile

目标文件建议：

- `app/application/knowledge/retrieval_profiles.py`
- `app/application/knowledge/diagnostics_service.py`

实现单一派生 helper，并在 `inspect()` 中使用。要求：

- 不修改 `PREP_PROFILE`、`FOLLOWUP_PROFILE`、`QUESTION_REVIEW_PROFILE`、`REPORT_REPAIR_PROFILE` 常量；
- 不修改业务 Runtime 默认配置；
- Fixed 模式生成 `query_aware_fusion=false`；
- Query-aware 模式生成 `query_aware_fusion=true`；
- 实际权重继续由现有 `QuerySignalAnalyzer` 决定；
- 响应记录请求模式、实际 query signal、实际 semantic/lexical 权重和 reason codes；
- 不根据前端显示文字推导策略。

### C3. 扩展安全响应

目标文件：

- `app/application/knowledge/diagnostic_models.py`
- `app/application/knowledge/diagnostics_service.py`

在 `SafeRetrievalInspectionResponse` 或现有 `resolved_profile/fusion_summary` 的明确位置返回：

```text
requested_hybrid_fusion_mode
effective_hybrid_fusion_mode
query_aware_fusion
query_signal
semantic_weight
lexical_weight
reason_codes
```

历史 Frozen Replay 若未记录相关字段，API 返回 `null`，UI 显示“未记录”，不得使用当前算法重算。`not_recorded` 只能作为 UI 显示语义或内部展示 key，不得成为 `HybridFusionMode` 枚举值或 API 字符串哨兵值。

### C4. 前端增加中文策略选择

目标文件：

- `frontend/src/pages/RagRetrievalPage.jsx`
- `frontend/src/pages/RagConsolePage.test.jsx`
- 必要时更新 `frontend/src/rag/ragDisplay.js`
- 必要时更新 `frontend/src/styles/pages/rag-console.css`

交互要求：

- 仅单引擎 Hybrid 模式显示“融合模式”；
- 选项使用中文：`固定权重 RRF`、`查询感知 RRF`；
- 默认选择固定权重；
- 切换回 Legacy 时自动回到固定权重或不发送 Query-aware 值；
- 结果区显示“请求策略”和“实际策略”；
- Query-aware 结果显示问题信号、实际权重和 reason codes；
- 文案明确“用于算法演示，不代表质量更优”；
- 不新增权重输入框、滑块或自由文本配置；
- 不改变 Compare 结果模型和页面布局主体。

### C5. 后端行为测试

目标测试：

- `tests/unit/test_knowledge_query_signals.py`
- `tests/unit/test_knowledge_hybrid_retrieval.py`
- 新增或扩展 Diagnostics Service 聚焦测试
- `tests/contracts/test_rag_console_contracts.py`
- `tests/unit/test_rag_console_routes.py`

至少覆盖：

1. 未传字段时仍走 Fixed Weighted RRF；
2. Fixed 模式保留基础权重并产生 disabled reason code；
3. Query-aware + lexical-dominant query 使用现有 lexical-dominant 决策；
4. Query-aware + semantic-dominant query 使用现有 semantic-dominant 决策；
5. Query-aware + balanced query 保持 balanced 决策；
6. 响应的 effective mode、query signal 与实际权重一致；
7. Legacy 不消费 Query-aware Profile；
8. Runtime 业务检索默认仍为原有 Fixed Profile；
9. Compare 继续是 Legacy / Fixed Hybrid 两路；
10. 不调用新的 Provider，不写 Artifact。

这些测试只证明控制流和契约正确，不使用 Recall、MRR、NDCG 或 No-evidence F1 作为通过条件。

### C6. 前端测试

至少覆盖：

- Compare 模式不显示 Fusion 模式选择；
- 单引擎 Legacy 不显示 Fusion 模式选择；
- 单引擎 Hybrid 显示两个中文选项；
- 默认请求发送 `fixed_weighted_rrf`；
- 用户选择后发送 `query_aware_weighted_rrf`；
- 响应正确显示实际权重和 query signal；
- loading、error、cancel 状态不回显敏感 query；
- 键盘可以访问选择控件；
- 页面不新增横向溢出。

### C7. 完成条件

- Inspector 可以稳定切换两种模式；
- 默认行为与当前 master 一致；
- Compare 和业务 Runtime 没有变化；
- 响应能解释实际使用的 Fusion 模式；
- UI 不允许任意调权重；
- 文档不宣称 Query-aware 效果更优。

## 8. Batch D：README 精简与历史归档

### D1. 新建历史归档

新文件：

- `docs/archive/development-history.md`

内容来源：README 当前 `Historical implementation log` 及后续阶段性说明。迁移时：

- 保留原有标题、Stage 编号、日期和历史结论；
- 文件顶部声明“历史记录，不代表当前运行状态”；
- 链接回 README、当前架构和当前 Runbook；
- 不顺手重写历史事实；
- 不移动当前仍权威的 Quick Start、运行方式和安全边界。

### D2. 重构 README

README 目标结构：

```text
Interview Agent
├── 项目简介
├── 当前架构
├── 核心功能
├── RAG 技术亮点
├── 5～10 分钟 Demo 路径
├── Quick Start
├── 配置与安全边界
├── 测试
├── 当前限制 / 非目标
└── Development History → docs/archive/development-history.md
```

RAG 技术亮点只描述已实现事实：

- Legacy / Hybrid 显式执行；
- Semantic + Lexical + Weighted RRF；
- Fixed / Query-aware Inspector 演示；
- Deterministic Rerank；
- Candidate-aware Evidence Sufficiency；
- Privacy-safe Compare；
- Diagnostic Evaluation；
- Frozen Replay；
- Versioned Corpus。

删除或改写以下容易误导的当前态文案：

- “等待新的 75 tuning paired Artifact 才能切换 Demo profile”；
- Promotion blocked、Canary gate、Legacy retirement 等旧治理语言；
- 将机器辅助诊断结果写成正式质量结论的表达。

建议替换为：

```text
Query-aware Fusion 是 Inspector 中可选择的确定性实验模式；
项目不声明它在真实检索指标上优于 Fixed Weighted RRF。
```

### D3. 更新当前权威文档

目标文件：

- `docs/architecture/knowledge-rag-v2-implementation-status.md`
- `docs/architecture/rag-demo-architecture.md`
- `docs/runbooks/rag-engineering-console.md`

更新内容：

- Frozen Replay 的 Console-only capability；
- Fixed / Query-aware 的 Inspector 演示入口；
- Compare 仍为 Legacy / Fixed Hybrid；
- Query-aware 不进入业务 Runtime 默认；
- 本轮不以人工标注或真实 paired evaluation 为后续门槛；
- No-evidence 仍是可观察的诊断状态，但本轮不调参。

### D4. 文档链接测试

目标文件：

- `tests/architecture/test_current_document_paths.py`
- `tests/unit/test_rag_demo_simplification_audit.py`

验证：

- README 指向的当前文档均存在；
- Development History 归档路径存在；
- README 不再包含大段 Stage 历史正文；
- 当前 RAG 文档不重新引入 Shadow/Promotion/Canary 作为活动流程；
- 归档中的历史用词不触发活动代码审计误报。

### D5. 完成条件

- README 可以在一次短阅读中解释项目当前能力；
- 历史记录完整保留并可访问；
- 当前文档与代码 capability 一致；
- 不再把人工评测或真实数据库运行写成本轮完成门槛。

## 9. Batch E：本地验证与交付

### E1. 聚焦后端测试

```powershell
F:\python3.11\python.exe -m pytest `
  tests/unit/test_rag_console_routes.py `
  tests/contracts/test_rag_console_contracts.py `
  tests/unit/test_rag_artifact_catalog.py `
  tests/unit/test_knowledge_query_signals.py `
  tests/unit/test_knowledge_hybrid_retrieval.py `
  tests/unit/test_effective_runtime_config.py `
  tests/unit/test_rag_demo_simplification_audit.py `
  tests/unit/test_rag_demo_closure_audit.py `
  tests/acceptance/test_rag_console_acceptance.py `
  tests/architecture/test_api_router.py `
  tests/architecture/test_current_document_paths.py -q
```

如果新增独立 Diagnostics Service 测试文件，应加入这一组。

### E2. 相关 Knowledge/RAG 回归

```powershell
$ragTests = @(
  rg --files tests/unit tests/contracts tests/acceptance tests/architecture |
    rg "(?:knowledge|rag|effective_runtime_config|current_document_paths)"
)
F:\python3.11\python.exe -m pytest @ragTests -q
```

运行前审查测试列表，排除需要真实 PostgreSQL、外部 Provider 或未授权写操作的节点。本轮不把这些节点的未运行状态记为失败。

### E3. 前端验证

```powershell
Set-Location frontend
npm.cmd test -- --run
npm.cmd run check
npm.cmd run build
```

不要求截图或视觉基准验证。布局变化通过组件测试、构建、可访问性断言和既有响应式规则验证。

### E4. 静态审计

```powershell
F:\python3.11\python.exe scripts/audit_rag_demo_closure.py

git diff --check
git status --short

rg -n "RetrievalIntent\.SHADOW|\"intent\"\s*:\s*\"shadow\"" app tests eval artifacts
rg -n "ranking-gap evidence gate|Promotion|Knowledge RAG Shadow|Legacy retirement" README.md app docs/architecture docs/runbooks
rg -n "semantic_weight|lexical_weight|rrf_k" frontend/src/pages/RagRetrievalPage.jsx
```

审计结果需要人工分类：

- 活动 Knowledge RAG 残留：必须修；
- 其他子系统的合法 Shadow：保留；
- `docs/archive/` 历史记录：允许；
- 前端只读显示字段：允许；
- 前端可编辑权重控件：不允许。

### E5. 交付记录

最终记录只包含：

- 修改文件清单；
- capability 矩阵结果；
- Fixed / Query-aware 控制流测试结果；
- 后端、前端、架构与 Acceptance 测试结果；
- 未执行的外部授权项说明；
- 已知但明确不在本轮范围内的问题。

不得在交付记录中补造指标、数据库运行结果或 Provider 成功记录。

## 10. Review Gate：独立自动审查与去冗余收口

Batch A～E 完成后，主线程必须基于最终工作树重新审查，不沿用变更前测试、实现意图或批次内临时结论。自动审查至少包含以下部分。

### 10.1 需求逐项追踪

建立“计划要求 → 修改文件 → 测试/静态证据”映射，逐项确认：

- Frozen Replay capability；
- Live Inspection/Compare 隔离；
- Knowledge Retrieval Shadow 清理与历史兼容；
- Remote Reranker Demo Scope 文案；
- 受控 Fusion 模式请求、派生 Profile 和安全响应；
- 中文 Inspector 交互；
- Runtime 与 Compare 不变；
- README 精简和历史归档；
- 所有非目标均未被触发。

任何只由实现意图、注释或执行者描述支持的事项均视为未证明。

### 10.2 冗余代码审查

至少执行并人工分类：

```powershell
rg -n "fixed_weighted_rrf|query_aware_weighted_rrf|hybrid_fusion_mode" app frontend tests
rg -n "def .*diagnostic.*profile|QuerySignalAnalyzer|query_aware_fusion" app
rg -n "runRagInspection|postJson\(\"/api/rag/inspections" frontend/src
rg -n "固定权重 RRF|查询感知 RRF" frontend/src
```

审查标准：

- 同一枚举或显示映射没有多个手写副本；
- Profile 派生没有散落在 Route、Service 和 Adapter 三处；
- 前端没有复制 API client 或请求状态机；
- CSS 没有为同一控件增加重复选择器；
- 测试没有复制生产 Fusion/Rerank 算法来证明自身；
- 没有未使用 import、dead branch、兼容别名或临时 TODO；
- 没有与本轮无关的大规模格式化 diff。

发现重复时，优先合并到现有权威 primitive/helper；若抽象只服务一个调用点且不会降低重复，不新增抽象。

### 10.3 权限与隐私审查

通过路由依赖、测试 fake/call counter 和响应字段检查证明：

- Replay 只依赖 Console capability；
- Live Inspection 与 Compare 仍依赖 Live Execution；
- Corpus Write 边界没有变化；
- Replay 不触发 repository live retrieval 或 Provider；
- DTO 不暴露 query、正文、Provider payload、向量、路径或密钥；
- 422/404/429 错误不回显敏感输入。

### 10.4 架构与行为审查

- API Route 只负责权限、校验和错误映射；
- Fusion 模式解析与 Profile 派生位于 application/profile 边界；
- Query-aware 算法继续由既有领域实现提供；
- Runtime Repository 和 Interview Consumer 没有被诊断功能反向污染；
- Compare DTO 和服务端两侧执行保持原状；
- Frozen Replay 不回填历史事实。

### 10.5 文档审查

- README 只描述当前能力；
- 历史正文只在 archive 保留一份；
- 当前架构和 Runbook 与实际 capability、Fusion 模式一致；
- 文档不要求人工 Ground Truth、真实 PostgreSQL paired evaluation 或 No-evidence 调参；
- 文档不宣称 Query-aware 效果更优或系统达到生产条件。

### 10.6 Review Gate 完成条件

- 所有需求均有直接代码、测试或文档证据；
- 所有自动化回归在最终代码上重新运行；
- 新 Closure Audit 在最终工作树上通过，且不依赖外部 Plan 路径、旧 SHA 或当前分支名；
- 冗余扫描结果已经分类，没有未解释的活动重复实现；
- `git diff --check` 通过；
- `git diff --stat` 与逐文件 diff 不包含无关修改；
- 工作树中不存在临时文件、调试输出、生成缓存或截图；
- 审查发现的缺陷已经修复并重新验证。

## 11. 任务依赖与建议提交边界

建议按以下逻辑变更拆分，只有用户明确要求时才创建 Git commit：

```text
Commit 1  chore(rag): establish closure audit invariants
Commit 2  fix(rag): allow console-only frozen replay
Commit 3  refactor(rag): remove obsolete knowledge shadow semantics
Commit 4  feat(rag): expose controlled inspector fusion modes
Commit 5  docs: archive development history and simplify readme
Commit 6  test(rag): close local demo acceptance matrix
```

依赖关系：

- Batch 0 必须最先完成，并成为后续批次和 Review Gate 的审计入口；
- Batch A 在 Batch 0 完成后独立执行，是第一个业务行为批次；
- Batch B 必须先完成 Artifact 兼容审计；
- Batch C 依赖现有 Query-aware 算法，不依赖 Batch B 的 SHADOW 删除；
- Batch D 应在 A～C 的最终契约确定后完成；
- Batch E 在最终代码上运行，不沿用变更前测试结果。

## 12. 风险与缓解

| 风险 | 后果 | 缓解措施 |
| --- | --- | --- |
| Frozen Replay 放宽时误放开 Live Execution | 未授权 Provider 调用 | 逐端点 capability 矩阵；Replay service 使用无 live repository 的 fake 验证 |
| 删除所有 Shadow 命中 | 破坏 Memory/LangGraph 功能 | 只按 `RetrievalIntent` 和 Knowledge RAG namespace 清理；剩余命中分类审计 |
| 历史 Artifact 包含 `intent=shadow` | 回放解析失败 | 活动枚举与 historical parser 分离 |
| 前端直接控制权重 | 形成不可追踪在线调参 | 只允许受控枚举；请求模型 `extra=forbid` |
| Query-aware 意外进入业务 Runtime | 改变面试行为 | 仅 Diagnostics Service 派生 Profile；Runtime Profile 与环境默认不变 |
| Compare 被顺手扩成三路 | DTO 和 UI 复杂度扩大 | 本轮冻结 Compare 契约，不增加 Fusion mode |
| `not_recorded` 污染 Fusion 枚举 | 领域类型混入展示哨兵值 | API 使用 `HybridFusionMode | None`；UI 按上下文显示“未记录/不适用” |
| 历史 Replay 被当前算法回填 | 伪造历史事实 | 缺字段返回 `null`，不重算 |
| Closure Audit 绑定下载目录或分支名 | 换机或合并后审计天然失败 | 使用仓库不变量与基线祖先关系；审计 branch-neutral |
| README 精简导致历史丢失 | 工程演进证据缺失 | 迁移至 archive，保留原文与双向链接 |
| 文档重新承诺人工/数据库评测 | 范围回弹 | 在范围、DoD 和当前状态文档中统一声明非目标 |

## 13. 最终完成定义

全部满足以下条件时，本计划才算完成：

- [ ] `RAG_CONSOLE_ENABLED=true`、`RAG_LIVE_EXECUTION_ENABLED=false` 时 Frozen Replay 可访问；
- [ ] 同一状态下 Live Inspection 与 Compare 仍不可访问；
- [ ] Replay 不调用 Retriever、Embedding Provider 或外部服务；
- [ ] Replay 响应继续符合 Safe DTO 和隐私边界；
- [ ] 活动 `RetrievalIntent` 不再包含 `SHADOW`；
- [ ] 其他子系统的 Shadow 未被误删；
- [ ] Remote Reranker 仍 fail-closed，错误文案改为 Demo Scope；
- [ ] Inspector 单引擎 Hybrid 支持 Fixed / Query-aware 两种受控模式；
- [ ] 默认模式仍为 Fixed Weighted RRF；
- [ ] Query-aware 模式展示实际 query signal、权重和 reason codes；
- [ ] `requested_hybrid_fusion_mode` 与 `effective_hybrid_fusion_mode` 的类型为 `HybridFusionMode | None`，不存在 `not_recorded` 枚举/字符串哨兵；
- [ ] Runtime `profile_id/profile_version` 保持原身份，Diagnostic Variant 只由明确的 requested/effective mode 字段表达；
- [ ] Legacy 不消费 Query-aware Fusion；
- [ ] Compare 仍保持 Legacy / Fixed Hybrid 两路；
- [ ] 业务 Runtime 默认和现有 Interview Consumer 行为不变；
- [ ] 前端不能编辑任意权重或 RRF 参数；
- [ ] Fusion 枚举、Diagnostic Profile 派生和前端显示映射各自只有一个权威实现；
- [ ] 没有复制 Query Signal、RRF、Rerank 或 Evidence Gate 算法；
- [ ] 没有新增重复 API client、状态组件、CSS primitive 或测试检索实现；
- [ ] README 已精简，历史 Stage 内容已迁入 `docs/archive/development-history.md`；
- [ ] 当前架构、Runbook 与代码 capability 一致；
- [ ] 聚焦后端测试通过；
- [ ] 相关 Knowledge/RAG 本地回归通过；
- [ ] 前端 test、check、build 通过；
- [ ] 新 Closure Audit 通过，且不依赖下载目录、外部 Plan SHA、`branch != master` 或 `git.master_not_modified`；
- [ ] 基线提交 `e674c3658be28472ec2a20871ec641542e38acd4` 是最终 HEAD 的祖先；
- [ ] `git diff --check` 通过；
- [ ] 未执行人工 Ground Truth；
- [ ] 未执行真实 PostgreSQL paired evaluation；
- [ ] 未调用外部 Embedding Provider；
- [ ] 未创建或激活新 Corpus；
- [ ] 未修改 No-evidence 阈值或以指标提升作为验收条件。

## 14. 完成后的项目状态

计划完成后的合法状态应是：

```text
Project scope:              Learning Project / Technical Showcase
Business runtime default:   unchanged
Live Compare:               Legacy / Fixed Hybrid
Inspector Hybrid modes:     Fixed RRF / Query-aware RRF
Frozen Replay:              Console-only, no live execution required
Remote reranker:            not enabled in current demo scope
Knowledge Shadow intent:    removed from active domain model
No-evidence tuning:         not part of this plan
Human Ground Truth:         not required by this plan
PostgreSQL paired eval:      not required by this plan
External Provider run:      not required by this plan
Closure audit:              repository-local and branch-neutral
```

本计划的目标是让现有 RAG 更一致、更容易演示和维护，而不是再次证明算法质量或扩展生产治理体系。
