# RAG Engineering Console 实施计划 v1.3

> 日期：2026-08-13
>
> 状态：Implementation、v1.3 completion audit 与受保护 PostgreSQL 验证均已完成
>
> 仓库基线：`20348fb54a7f938af0bfda6427af1e2a84880397`
>
> 目标读者：负责 Knowledge RAG、FastAPI、React/Vite、评测治理、隐私与发布门禁的工程师
>
> 文档类型：实施计划；不是运行手册、发布批准或当前 API 参考
>
> 首要交付：Retrieval Inspector 与 Eval Dashboard 形成“发现失败 → 重放 → 定位根因”的诊断闭环

## 0. 执行摘要

本计划建设一套面向 RAG 维护者的工程控制台。控制台的目的不是证明 Hybrid 很强，也不是包装尚未通过的实验指标，而是让维护者能够快速、可靠地回答以下问题：

1. 某个 query 为什么在 Legacy、Semantic-only、Lexical-only 与 Hybrid weighted RRF 中得到不同结果；
2. 某个候选在哪个阶段发生了排名变化：Semantic、Lexical、Fusion、Rerank，还是 Evidence Gate；
3. Lexical 通道是在补足 exact term / alias，还是在 semantic paraphrase、cross-domain 或 weak-keyword case 中污染排序；
4. No-evidence 为什么仍然失败：系统从不 abstain、错误 abstain，还是证据标签本身尚未校准；
5. 一次 Reviewer 或 Follow-up 决策最终使用了哪些 Evidence，以及这些 Evidence 如何从 Base bundle 传播到 Question、Review 与消费端；
6. 当前指标属于机器预标注、历史诊断、人工 tuning，还是有资格参与 promotion 的独立证据。

实施顺序固定为：

```text
Phase 0A  Console Contract Minimum（硬前置）
    ├──→ Phase 1  P0 Retrieval Inspector 基础版
    ├──→ Phase 2  P0 Eval Dashboard 基础版
    └──→ Phase 0B  Diagnostic Fidelity
                 ↓
          P0 Inspector / Dashboard 完整诊断增强
    ↓
Phase 3  P1 RAG Overview
    ↓
Phase 4  P1 Evidence Trace
    ↓
Phase 5  P2 Knowledge Corpus Read-only
    ↓
Phase 6  Hardening、Acceptance 与交付
```

Phase 0A 是任何 Console UI 业务实现的硬前置；它冻结 capability、authorization、Safe DTO、同步 Inspection、只读 Artifact Catalog、Replay/Rerun、raw query 生命周期与 sealed holdout 合同。Phase 0A 通过后，页面骨架、基础 Inspector 与基础 Dashboard 可以开始，同时推进 Phase 0B 的 Ranking Explanation、`RetrievalDiagnosticSnapshotV1`、Trace latency V3 与 No-evidence 诊断质量。前端对 Phase 0B 尚未提供的字段只能显示 `Unavailable` 或 `Not recorded`，不得通过自行推导、相减、重新计算或硬编码填补。

当前 Eval Artifact 不是完整的五阶段检索快照。只有显式携带 `RetrievalDiagnosticSnapshotV1` 的新 Artifact 才能重放 Semantic → Lexical → Fusion → Rerank → Evidence Gate；历史 Artifact 仅展示其原本冻结的字段，不使用当前代码回填缺失阶段。`Replay` 与 `Rerun` 是两个不同操作，必须在 Contract、路由、文案和测试中保持分离。

### 0.1 当前执行台账（2026-08-14）

本节记录实施进度，不降低后文任何验收标准。状态含义如下：

```text
已实现      代码与 Contract 已落地，但仍可能等待更大范围回归
局部验证    相关聚焦测试已通过，不等同于完整验收
待验收      代码存在，但所要求的完整测试或浏览器流程尚未形成最终证据
外部阻塞    当前仓库无法合法补齐所需授权或外部状态
```

| 里程碑 | 当前状态 | 已有证据 | 收口前仍需完成 |
| --- | --- | --- | --- |
| M0A Console Contract Minimum | 本地完成、已验证 | 默认关闭 capability；严格 actual-client loopback；Safe DTO；同步 Inspection；无 Inspection GET/store；allowlisted Artifact Catalog；sealed/private holdout fail-closed；非法 422 不回显请求输入；Contract/权限/Acceptance 回归通过 | 无本地待办 |
| M0B Diagnostic Fidelity | 本地完成、已验证 | `CandidateRankingExplanation`、Trace latency V3、`RetrievalDiagnosticSnapshotV1` sidecar、No-evidence 四格及比率、partial historical 降级；同一 Acceptance Snapshot 直接证明 Semantic、Lexical、Fusion、Rerank、最终 Evidence、Gate、Evidence Decision、各阶段 latency 与 ranking explanation，且 repository/Provider 调用计数为零 | 无本地待办 |
| M1 Diagnostic API | 本地完成、已验证 | Overview、Live Inspection、Artifact list/detail/paired/cases/no-evidence/snapshot、Evidence Trace、Corpus 只读端点；Artifact detail 合法路径返回 Safe DTO，非法 SHA、路径型输入及 sealed/private Artifact fail-closed；OpenAPI inventory 为 60 paths / 66 operations | 无本地待办 |
| M2 Retrieval Inspector | 本地完成、已验证 | Live/Frozen 区分、Provider 费用边界、取消与清理、安全 identity、候选排序、解释 Drawer、Evidence Decision、latency 非可加提示；full-snapshot 前端回放同时验证五阶段、最终 Evidence、Gate、latency 与 explanation；Candidate ID/Artifact identity 全值复制及失败反馈有组件测试 | 无本地待办 |
| M3 Eval Dashboard | 本地完成、已验证 | identity-first、历史 machine holdout 标识、四引擎矩阵、后端 paired delta、No-evidence、动态 case type、Case Explorer、无 query 的 Inspector 链接；Artifact/manifest/profile/code identity 使用共享全值复制；rejected rank-normalized Artifact 默认不被选中 | 无本地待办 |
| M4 Overview | 本地完成、已验证 | 后端版本化 promotion decision、6 个阻断项、证据/行动；前端不计算 promotion；浏览器确认 Legacy、0% rollout、Shadow disabled、Promotion blocked | 无本地待办 |
| M5 Evidence Trace | 本地完成、已验证 | 安全 lineage、稳定 record/parent/time/evidence refs、四个决策维度、CoT 边界；未知 opaque ID 的安全 error/empty 浏览器路径不回显输入 | 无本地待办 |
| M6 Corpus read-only | 本地完成、已验证 | 31 条只读 Unit 与 Safe detail；无发布/删除/重嵌入操作；activation、retirement、embedding、authority、review 无权威来源时均为 `not_recorded` | 无本地待办 |
| M7 Hardening and acceptance | 完成、已验证 | 最终代码：聚焦后端 41 passed；相关 Knowledge/RAG 316 passed；Architecture + Acceptance 441 passed；Frontend 12 files / 129 tests；ESLint、production build、lazy/bundle budget、compileall、OpenAPI、默认运行状态、冗余/隐私扫描及 `git diff --check` 通过；受保护 PostgreSQL 节点在结构化授权、目标指纹校验、自动生成 `test_*` owned scope 与零残留清理下 1 passed | 无本地待办；Human GT、No-evidence、sealed holdout 与 blind A/B 仍是算法 promotion blocker，不是 Console 实施缺口 |

当前 Artifact Catalog 的预期公开边界固定为：

```text
5 × machine-preannotation tuning（每个 75 cases）
1 × 已被查看的 machine holdout（25 cases，仅 historical_diagnostic）
0 × sealed/private holdout
```

这里的 `historical_diagnostic` 既不是 `tuning`，也不是 `sealed_holdout`，不具备独立 promotion 证据资格。

### 0.2 v1.3 修订范围

本次修订不改变已冻结的 API、页面信息架构或运行时边界，只校准完成状态并补强可验证性。最终审查必须完成以下四个闭环：

1. **Full Snapshot Replay**：一个 Acceptance fixture 必须包含 Semantic、Lexical、Fusion、Rerank、最终 Evidence、非空 Evidence Decision、各阶段 latency 与 ranking explanation；后端响应和前端 Frozen Replay 都必须保留这些事实，且 Provider 调用计数保持为零；
2. **Artifact Detail Boundary**：直接覆盖 Artifact detail endpoint 的成功与拒绝路径，证明只返回 Safe DTO，sealed/private Artifact、非法 SHA 和路径型输入均 fail-closed；
3. **Status and Identity Contract**：绿色仅用于正式 `passed/allowed`；warning/degraded 使用黄色；failed/blocked/hard-stop 使用红色；unavailable/not-evaluated/普通 available 使用中性样式。状态同时包含文字与符号，Candidate ID、Artifact SHA、manifest/profile/code revision 支持复制完整值并给出成功/失败反馈；
4. **Final Matrix and Redundancy Audit**：完成上述测试后按 17.7 原样重跑聚焦后端、相关后端、Architecture/Acceptance、前端 test/check/build、静态隐私/冗余扫描与 `git diff --check`，再更新本台账；不得沿用变更前的测试计数宣称最终完成。

实现这些补强时只扩展共享 primitive、既有 fixture 与既有 Acceptance 路径，不新增 UI、chart/state framework，不引入第二套 clipboard/status 实现，也不为测试复制 retrieval/fusion/rerank 算法。

2026-08-13 完成记录：以上四项均已闭环。状态和复制由唯一的 `toneFor`、`StatusPill`、`CopyButton`、`IdentityValue` 实现；临时的显示文字特判已删除；CSS 重复选择器已合并。Full Snapshot 与 Artifact detail 使用既有 Acceptance 文件和既有 DTO/fixture，不增加第二套测试服务或领域算法。

### 0.3 收口顺序

后续工作按下列顺序执行，不再扩展架构或增加装饰性页面：

1. 修复并验证共享状态语义与完整 identity 复制，不在页面内复制 clipboard 状态；
2. 补齐 Full Snapshot Replay 和 Artifact Detail 的后端 Acceptance；
3. 补齐 full-snapshot 前端回放与 status/identity 组件测试；
4. 运行 Knowledge/RAG、architecture、acceptance、frontend 全矩阵；
5. 在不截图、不暴露 raw query 的前提下完成必要的关键浏览器复核；
6. 审查重复 DTO、未使用 API helper、重复数据加载、机械格式化噪声与不诚实的 governance 默认值；
7. 更新 Runbook 与本台账；
8. 仅在完整外部授权元数据存在且隔离表满足 `test_*` 约束时运行受保护 PostgreSQL 测试。

在第 1—7 项完成前，不把本地计划标为完成；第 8 项若仍缺合法授权，应明确记为外部阻塞，不伪造 approval ID、有效期或数据库授权记录。

## 1. 当前执行边界

### 1.1 运行与发布状态

本计划开始时，以下边界保持不变：

```text
Formal engine:              Legacy
Hybrid rollout:             0%
Knowledge Shadow:           Disabled
Knowledge Canary:           Not started
Remote reranker:            Disabled / gated
Hybrid promotion:           Blocked
Business blind A/B:         Pending human annotation
Independent human Eval V3:  Missing
```

控制台代码进入仓库，不代表 Hybrid 已晋级。控制台完成后，Overview 正确显示 `Promotion: BLOCKED` 也属于成功结果。

### 1.2 Eval 与 holdout 边界

当前 `eval/knowledge-v3/machine-preannotation/` 是机器预标注诊断集：

- `human_annotator_count = 0`；
- 不具备独立发布证据资格；
- 不得授权 Hybrid、Shadow 或 Canary；
- 其中历史 25-case machine holdout 已被运行、查看并提交，只能标记为 `historical_diagnostic`；
- 新的 sealed holdout 必须在控制台外部受控保存，普通诊断 API 不得暴露其 case 内容。

Dashboard 必须把以下状态严格区分：

```text
tuning
historical_diagnostic
sealed_holdout
```

### 1.3 计划归档边界

本文件位于按日期冻结的实施计划归档中。实际执行每个 Task 前，必须重新解析当前仓库结构、领域 Contract、API schema、测试位置与命令。完成的正式契约应落入当前权威的代码模型、OpenAPI/结构化 Contract、运行文档与测试，而不是依靠持续修改本计划来保持“看起来最新”。

## 2. 问题定义

当前 RAG 调试主要依赖 JSON Artifact、CLI 输出和代码级测试。已有证据能够说明总体差异，但定位单个失败 case 的成本仍然较高：

- 全局指标无法直接说明具体由哪些 case 导致；
- 单个 Artifact 中存在多层 rank/score，但缺少统一的可视化管线；
- 候选被 Fusion 或 Rerank 改变顺序后，维护者需要手工对照多个字段；
- Evidence Decision 有 availability、sufficiency、consistency、confidence 等正交维度，容易被压缩成一个模糊的“通过/失败”；
- 当前 No-evidence F1 为 0，但没有产品化的业务四格表解释具体失败类型；
- Eval Artifact identity、human annotation status 与 promotion eligibility 不在同一视图中；
- BaseEvidenceBundle 到 Reviewer/Follow-up 的 lineage 存在于后端 Contract 中，但缺少安全的只读审计视图；
- 当前没有面向控制台的 Safe Read Model，直接暴露领域对象会泄露 raw query、完整 KnowledgeChunk 或内部 metadata。

本计划通过一个克制的工程控制台解决这些问题。

## 3. 产品目标与成功标准

### 3.1 核心目标

控制台必须具备以下性质：

```text
Auditability over spectacle
State over decoration
Tables over ornamental charts
Reason codes over vague prose
Stable layout over animation
Backend truth over frontend inference
```

### 3.2 业务成功标准

满足以下条件时，P0 控制台才具有实际价值：

1. 在 Evaluation 中能看到 Legacy 与 Candidate 的 paired delta，并定位造成差异的 case；
2. 点击失败 case 能进入 Inspector 的冻结 Artifact replay，而不是无提示地重新运行当前引擎；历史 Artifact 对未冻结阶段诚实显示 `Not recorded`；
3. 对携带 `RetrievalDiagnosticSnapshotV1` 的 Artifact，Inspector 能显示同一候选的 Semantic、Lexical、Fusion、Rerank 与 Final Evidence 状态；
4. 维护者能区分排名问题、Evidence Gate 问题和数据标注问题；
5. No-evidence 视图能显示 Correct evidence、False abstention、False evidence、Correct abstention 四种计数；
6. 所有指标都绑定 Dataset SHA、Artifact SHA、Corpus manifest、Embedding revision 与代码 revision；
7. 机器预标注指标不会显示成正式 promotion 通过；
8. Live query 只存在于当前浏览器组件内存和当前 POST body，服务端默认不回显，也不进入日志、Trace、Artifact 或持久化存储；前端响应数据不包含简历、JD、候选人答案、Provider prompt/response、embedding vector、DSN、密钥或本机路径；
9. sealed holdout case 无普通读取路径；
10. 控制台没有改变 Legacy 默认、rollout 0 或 Shadow disabled 的运行边界。

### 3.3 工程成功标准

- 所有 Console API 使用显式 Pydantic Safe Read Model；
- API model `extra="forbid"`，避免静默字段扩散；
- 前端只使用 `frontend/src/api/client.js` 的共享请求边界；
- 路由继续使用 React lazy loading；
- 页面不引入新的 UI 框架、图表框架或远程字体；
- 指标与决策在服务端生成，前端只展示；
- Artifact replay 可重复、可追溯且不产生 Provider 费用；缺失的历史字段不通过当前代码补造；
- deployment capability 与 request-principal authorization 分离；V1 必须选择严格 loopback-only，或 capability + authenticated principal + diagnostic permission；
- 前后端 Contract、隐私、权限、可访问性和关键浏览器流程均有测试。

## 4. 非目标

本计划明确不包含：

- GraphRAG 可视化；
- 知识图谱大屏或复杂关系网络；
- embedding/vector 空间图；
- chain-of-thought、“AI 思考过程”或推理文本重建；
- 装饰性 3D 图表、雷达图、大面积渐变、玻璃拟态、发光边框或 AI pulse；
- 在控制台内训练或调参 Query-aware Hybrid；
- 第一版在线修改 `semantic_weight`、`lexical_weight`、RRF `k`、Evidence Gate threshold；
- Shadow、Canary、Rollback 的写操作；
- 在 Overview 根据几个 KPI 由前端自行计算 promotion；
- Corpus 在线编辑、删除、重嵌入、发布、退休或回滚；
- 普通用户访问 RAG Console；
- 暴露 sealed holdout 的 query、label、case 详情或分类型结果；
- 修改现有正式 Legacy 默认运行行为。

## 5. 信息架构与路由

### 5.1 页面结构

```text
RAG
├── Overview
├── Retrieval Inspector
├── Evaluation
├── Evidence Trace
└── Knowledge Corpus
```

未来在获得真实运行授权后，另建：

```text
Operations
├── Shadow
├── Canary
└── Rollback
```

当前不将 Operations 添加到主导航。

### 5.2 React 路由

建议新增：

```text
/rag                     → Overview
/rag/retrieval           → Retrieval Inspector
/rag/evaluation          → Eval Dashboard
/rag/evidence-trace      → Evidence Trace
/rag/corpus              → Knowledge Corpus
```

路由继续在 `frontend/src/App.jsx` 中使用懒加载。第一版将 RAG 作为一个桌面优先的受信任工程入口；在权限和环境开关未通过时，路由显示诚实的 unavailable 状态，不渲染伪数据。

### 5.3 导航策略

不要把五个 RAG 子页面全部塞入产品主导航。建议：

- `PRODUCT_NAVIGATION` 只增加一个 `RAG` 顶层入口；
- RAG 页面内部使用本地 subnav；
- 移动导航只显示顶层 `RAG`；
- subnav 使用清晰文字，不依靠图标辨识；
- 未启用 Diagnostic UI 时，顶层入口可以不返回，或进入 404/unavailable 边界，具体由后端 capability 决定。

## 6. 当前后端能力基线

### 6.1 Retrieval Candidate

当前 `app/domain/knowledge/retrieval.py` 中的 `RetrievalCandidate` 已提供：

```text
semantic_score
lexical_score
semantic_rank
lexical_rank
fusion_score
fusion_rank
rerank_score
rerank_rank
channel_hits
matched_terms
```

这些字段足以构建统一候选表的排名骨架，但还不包含：

```text
base_score_source
exact_term_boost
routing_tag_boost
eligibility_result
selection_result
per-candidate reason_codes
rejection_reason
```

后者必须在 Phase 0 新增专用诊断 Contract。

### 6.2 Evidence Decision

当前 `EvidenceDecision` 已提供四个正交状态维度和解释字段：

```text
availability
sufficiency
consistency
evaluation_confidence
covered_signals
missing_signals
reason_codes
gate_version
```

前端不得把这些字段压缩成一个模糊 `status`。

### 6.3 Retrieval Trace V2

当前 Trace 已包含：

```text
sanitized_query_facts
resolved_profile
hard_constraints summary
routing_hints summary
channel traces
fusion summary
rerank summary
evidence decision
selected evidence IDs/hashes
component versions
latency breakdown
degraded path/reasons
```

Trace 已经把 raw query 转成 SHA-256 与字符数，并使用清洗器阻断敏感字段。这是 Console Safe Read Model 的基础，但不能直接把磁盘 Trace 或领域对象原样返回给浏览器。

### 6.4 当前 latency 粒度

当前 Hybrid latency 更接近：

```text
semantic
lexical
fusion_rerank_and_selection
total
```

Semantic 与 Lexical 并行运行，因此它们的 latency 不能相加后与 total 比较。第一版 UI 可以诚实展示当前组合阶段；只有 Trace V3 提供真实计时后，才拆为 Fusion、Rerank 与 Evidence Gate。

### 6.5 Eval Metrics V3

当前服务层已包含：

```text
Recall@5
MRR@5
NDCG@5
Hit@1
Filter correctness
Hard-negative false-positive rate
No-evidence precision / recall / F1
Evidence precision@5
Domain routing accuracy
Topic routing accuracy
Cross-channel contribution rate
Replay stability
Excluded-chunk violation rate
P95 latency
Case-type breakdown
Paired metric deltas
```

当前未直接输出业务四格计数。Phase 0B 应在后端增加 `NoEvidenceConfusionSummary`，不要让前端通过 observations 自行推导。

### 6.6 当前 Eval Artifact 的诊断保真度

当前 `KnowledgeEvalCandidateV3` 仅冻结候选的 `chunk_id`、`rank`、`score` 与 `channels`；case result 虽然还包含 selected/bound/replayed Evidence IDs、Semantic/Lexical hit IDs、No-evidence declaration、总 latency 与 reason codes，但没有冻结以下五阶段细节：

```text
semantic rank/score per candidate
lexical rank/score per candidate
fusion rank/score and policy inputs
rerank rank/score and explanation
EvidenceDecision snapshot
stage latency breakdown
component-level execution identity
```

因此，现有 Artifact 可以重放其已记录的结果，但不能还原完整的 Semantic → Lexical → Fusion → Rerank → Gate 管线。使用当前实现重新计算缺失字段属于 `current_engine_rerun`，不属于 `artifact_replay`。

### 6.7 API 缺口

当前 `/api/runtime` 只提供通用运行边界，尚无：

```text
/api/rag/overview
/api/rag/inspections
/api/rag/evaluations
/api/rag/evidence-traces
/api/rag/corpus
```

因此 Phase 0 的工作不是简单“接现有 API”，而是先建立受保护、隐私安全、版本化的 Diagnostic API。

## 7. 领域语义与展示 Contract

### 7.1 Availability

前端直接消费后端值：

```text
available
degraded
unavailable
```

含义：检索或证据来源的可用状态，不代表证据已经充分。

### 7.2 Sufficiency

前端直接消费：

```text
sufficient
weak
insufficient
empty
not_evaluated
```

禁止引入 `partially_sufficient` 等后端不存在的枚举。

### 7.3 Consistency

必须展示：

```text
consistent
possible_conflict
confirmed_conflict
not_evaluated
```

`sufficient + possible_conflict` 不能只显示为绿色充分证据。

### 7.4 Evaluation Confidence

必须展示：

```text
high
medium
low
not_scorable
```

置信度是对评估可稳定程度的描述，不等同于 retrieval score。

### 7.5 Consumer Action

当前后端没有统一的 `USE_EVIDENCE / ABSTAIN` 枚举。V1 不为了填满 UI 而创建一个跨 Reviewer、Follow-up、Prep 与 Report Repair 的新策略。Inspector 固定展示 Availability、Sufficiency、Consistency、Evaluation Confidence 与 reason codes；没有权威消费记录时显示：

```text
Not recorded / no unified policy
```

只有现有消费者已经执行并记录可版本化策略时，才返回：

```json
{
  "consumer": "reviewer",
  "action": "abstain",
  "policy_version": "reviewer-evidence-action-v1",
  "reason_codes": ["insufficient_signal_coverage"]
}
```

Reviewer、Follow-up、Prep 与 Report Repair 对 `weak`、`degraded` 或 `possible_conflict` 的处理不一定相同。诊断层只能适配并安全暴露真实记录，不能为统一 UI 面板决定这些产品语义。

前端不得执行类似：

```javascript
const action = sufficiency === "sufficient" ? "use_evidence" : "abstain";
```

### 7.6 Promotion Decision

Promotion 必须由后端返回版本化决策：

```json
{
  "allowed": false,
  "decision_version": "knowledge-promotion-decision-v1",
  "evaluated_at": "2026-08-13T00:00:00Z",
  "artifact_sha256": null,
  "blockers": [
    {
      "code": "HUMAN_TUNING_GT_MISSING",
      "severity": "hard_stop",
      "blocks": ["candidate_activation", "shadow", "canary", "production"],
      "required_action": "Complete independent tuning annotation and adjudication."
    }
  ]
}
```

前端只展示，不重复门禁算法。

## 8. Phase 0：Backend Diagnostic Contracts

Phase 0 分为两个里程碑：

```text
Phase 0A — Console Contract Minimum
  capability + authorization
  Safe Read Models
  synchronous inspection response
  read-only Artifact Catalog
  Replay/Rerun semantics
  raw query lifecycle
  sealed holdout fail-closed
  contract/privacy tests

Phase 0B — Diagnostic Fidelity
  Candidate Ranking Explanation
  RetrievalDiagnosticSnapshotV1
  Trace latency V3
  NoEvidenceConfusionSummary
  optional Consumer Action adapter when a real policy exists
```

Phase 0A 全部通过后，Phase 1/2 的页面骨架与支持字段的基础视图可以开工；Phase 0B 可以并行推进。任何未由后端真实记录的 Phase 0B 字段都显示为 unavailable/not recorded。

### Phase 0A：Console Contract Minimum

### Task 0A.1：建立 RAG API 模块、capability 与 authorization boundary

建议新增：

```text
app/api/rag/__init__.py
app/api/rag/routes.py
app/api/rag/models.py
app/application/knowledge/diagnostics_service.py
app/ports/knowledge_diagnostics.py
```

并在 `app/api/router.py` 组合 `rag_router`。

环境能力建议：

```text
RAG_DIAGNOSTIC_UI_ENABLED=false
RAG_LIVE_INSPECTOR_ENABLED=false
RAG_EVAL_ARTIFACT_ACCESS_ENABLED=false
```

这些环境变量只表示部署 capability，不表示当前请求 principal 已被授权。M0A 必须冻结以下部署模型之一：

1. **Strict loopback-only**：服务只绑定 loopback；不得信任客户端可伪造的 `X-Forwarded-For`；如有反向代理，必须配置精确 trusted proxies，并基于可信连接信息判断来源；或
2. **Authenticated diagnostics**：capability 开启后，仍要求 authenticated principal 具备显式 diagnostic permission，例如 `rag:diagnostics:view`、`rag:diagnostics:live-inspect` 与 `rag:eval-artifacts:view`。

不得继续使用未定义的 `trusted-local` 作为安全边界。前端隐藏导航不构成授权，每个 API 都必须独立执行 capability 与 authorization 检查。

规则：

- 默认关闭；
- 未启用 Diagnostic UI 时，所有 `/api/rag/*` 返回 404；
- Live Inspector 还要求 `RAG_LIVE_INSPECTOR_ENABLED=true`；
- Artifact 列表与读取还要求 Artifact access 开关；
- sealed holdout 不因任何普通开关而开放；
- 不把这些 capability 与 Hybrid rollout 或 Shadow 开关绑定；
- capability 只控制诊断访问，不控制正式运行引擎。

验收：

- 默认环境没有公开 RAG Diagnostic 数据；
- 单独启用 UI 不会自动启用 live query；
- 错误配置 fail closed；
- `/api/runtime` 可只返回安全的 capability boolean，不返回私有路径。
- capability 开启但 principal 未授权时 fail closed；
- loopback 模式测试代理头伪造不会绕过来源校验。

### Task 0A.2：定义 RAG Overview Safe Read Model

新增模型至少包含：

```text
schema_version
generated_at
formal_engine
candidate_engine
hybrid_rollout_percent
shadow_enabled
remote_reranker_enabled
evidence_gate_enabled
corpus identity
embedding identity
profiles
component versions
promotion decision
release-evidence summary
```

要求：

- 从后端配置、激活 corpus 与正式 promotion service 读取；
- 不返回环境变量原值；
- 不返回 DSN、Provider base URL、API key、文件系统路径；
- 不将 `configuration_valid` 等通用字段误当作 RAG promotion；
- blocker 说明使用稳定 code，展示文字可以由前端映射或后端返回经过审查的 public message。

### Task 0A.3：定义 Safe Retrieval Inspection Request

请求模型建议：

```json
{
  "mode": "live",
  "query_text": "...",
  "intent": "eval",
  "profile_id": "question-review@hybrid-v1",
  "engine": "hybrid-v2",
  "hard_constraints": {},
  "routing_hints": {}
}
```

约束：

- `query_text` 只允许 POST body，不允许 URL query string；
- 长度继续受 `RetrievalRequest` 上限约束；
- 不允许从浏览器提交 session resume、JD、简历或候选人答案作为 Inspector query；
- engine/profile 只能从服务端 allowlist 中选择；
- 第一版不允许浏览器提交自定义权重或 threshold；
- 每次 live 请求明确记录是否会产生 Provider 调用；
- 超时、并发饱和与 Provider 错误使用稳定 public error code；
- 普通日志不得记录原始 query。
- Live query 只存在于当前浏览器组件内存、当前 POST body 和服务端请求的短生命周期内存；
- 不把 query 放入 URL、localStorage、sessionStorage、analytics、metric label 或 exception；
- authored Eval query 使用独立 capability/governance，不能因拥有 Artifact 指标读取权而自动开放；sealed holdout query 永不通过普通 Eval API 返回。

### Task 0A.4：定义同步 Safe Retrieval Inspection Response

`POST /api/rag/inspections` 在 V1 同步执行并直接返回专用 Safe DTO，不返回 `RetrievalResult.model_dump()`，也不创建服务端 inspection store、TTL 或后续 GET 端点。

建议字段：

```text
schema_version
request_id
mode: live | artifact_replay | current_engine_rerun
created_at
engine/profile/corpus/embedding/code identity
sanitized query facts
resolved profile
safe routing summary
channel summary
safe candidates
evidence decision
consumer action record or explicit not-recorded state
latency
degraded reasons
artifact references
```

`request_id` 仅用于本次响应、日志和 Trace 相关性，不表示可持久读取的资源。响应默认不回显 `query_text`；只允许返回 query fingerprint、长度、语言或已审查的 signal class 等 sanitized facts。

`artifact_replay` 使用 Evaluation/Snapshot 只读端点返回同构的安全诊断视图，不通过此 POST 创建。若共享 DTO 包含 Phase 0B 字段，这些字段在后端尚未记录时必须为显式 unavailable/not-recorded，而不是前端必填值。

`SafeRetrievalCandidate` 只允许：

```text
candidate_id
title
safe_excerpt
domain
topic
safe tags
source_type
authority status
content_sha256
corpus_manifest_sha256
semantic rank/score
lexical rank/score
fusion rank/score
rerank rank/score
channel_hits
matched_terms
ranking explanation
selection decision
```

禁止：

```text
full chunk content
raw metadata
source URL
provider payload
embedding vector
private locator
raw query
resume/JD/answer
```

### Task 0A.5：建立只读 Artifact Catalog 与基础 Replay Contract

V1 不新增 Artifact 数据库或独立服务。实现只读 allowlisted catalog adapter：

```text
explicit allowlisted roots
  → enumerate supported artifact schemas
  → resolve and verify path remains inside allowlist
  → validate exact SHA/self-hash
  → parse
  → map to Safe DTO
```

规则：

- API 客户端只传 opaque Artifact SHA/case reference，不传文件路径；
- 拒绝 path traversal、allowlist 外路径、未知 schema、SHA 不匹配和符号链接逃逸；
- private/unblinding/provider-cache/sealed-holdout 目录不进入普通 catalog；
- `artifact_replay` 只展示 Artifact 当时冻结的字段，不访问 Provider，不运行当前 retrieval 代码；
- `current_engine_rerun` 使用同步 Inspection endpoint、需要 Live Inspector 授权、可能产生费用，并绑定当前 component identity；
- rerun 不覆盖历史 Artifact，也不静默生成或持久化临时 Artifact。

### Task 0A.6：定义 sealed holdout 访问策略

要求：

- 普通 Artifact catalog 不索引 sealed holdout 原始 case；
- 普通 Evaluation API 不返回 sealed query、labels 或单案例；
- Candidate 冻结前不返回聚合结果；
- 正式运行后的公开范围由独立 governance record 决定；
- 当前机器 25-case 只能标为 `historical_diagnostic`；
- CSS 隐藏、前端条件渲染或客户端权限判断都不能替代后端拒绝。

### Task 0A.7：定义基础 Eval Dashboard Read Model

新增只读服务，将现有 Artifact、Metrics 与 governance 组合为安全响应：

```text
dataset identity
split classification
annotation status
human annotator count
independent evidence eligibility
corpus/embedding/engine/code identity
artifact SHA
metrics
case-type breakdown
paired deltas
case replay references
promotion decision reference
diagnostic fidelity: full_snapshot | partial_historical
```

基础响应不要求历史 Artifact 具备五阶段细节，也不要求前端自行计算 No-evidence 四格。缺失的后端聚合字段显示 `Unavailable`，待 Phase 0B 增强。

### Phase 0A 完成定义

- capability 与 authorization 分开并 fail closed；
- Overview、Inspection、基础 Evaluation 与 Artifact Replay Safe DTO 已冻结；
- `POST /api/rag/inspections` 同步返回且无服务端 inspection storage/GET endpoint；
- Safe Candidate 不暴露完整 KnowledgeChunk；
- Artifact Catalog 仅从 allowlisted roots 只读加载并校验 SHA；
- Replay 与 Rerun 的语义、权限、费用和 identity 明确分离；
- raw query 生命周期已写入 Contract 和 privacy tests；
- sealed holdout 无普通访问路径；
- OpenAPI/Contract/privacy/authorization tests 通过。

Phase 0A 完成后可开始 Phase 1/2 基础 UI。

### Phase 0B：Diagnostic Fidelity

### Task 0B.1：增加 Candidate Ranking Explanation Contract

当前 reranker 需要在计算时输出可审计分解。推荐新增独立模型，而不是把 UI 字段直接塞入核心领域对象：

```text
CandidateRankingExplanation
├── candidate_id
├── base_score_source
├── base_score
├── exact_term_boost
├── routing_tag_boost
├── final_rerank_score
├── eligibility_score
├── eligible
├── selected
└── reason_codes
```

要求：

- `base_score_source` 为 `fusion_score | semantic_score | lexical_score | chunk_score`；
- explanation 必须与实际排序代码使用同一计算路径；
- 不允许诊断层重复实现一份分数算法；
- tie-break 显式记录：rerank score、fusion rank、chunk ID；
- 低于 minimum score、未进入 rerank limit、未进入 evidence limit 等情况使用不同 reason code；
- 对抗测试继续证明 raw `chunk.score` 不能静默取代 Fusion prior。

### Task 0B.2：定义 `RetrievalDiagnosticSnapshotV1`

为未来的新 Eval 运行增加独立 sidecar，而不是扩大或改写既有 metrics Artifact：

```text
KnowledgeEvalCaseResultV3
  └── diagnostic_snapshot_ref
          └── RetrievalDiagnosticSnapshotV1 sidecar
```

Snapshot 至少包含：

```text
schema_version
artifact/run/case identity
sanitized request facts
semantic candidates with rank/score/reason codes
lexical candidates with rank/score/matched terms/reason codes
fused candidates with rank/score/policy inputs/explanation
reranked candidates with before/after rank/score/explanation
selected and rejected Evidence IDs
EvidenceDecision snapshot
latency breakdown
component/profile/corpus/embedding/code versions
snapshot_sha256
```

约束：

- 使用 opaque `diagnostic_snapshot_ref` 和 exact SHA 关联；
- Snapshot 与 metrics Artifact 分离，按需加载；
- Snapshot 不包含 raw query、完整 Knowledge body、Provider payload、embedding、私有 locator 或 unblinding data；
- 不修改、不迁移、不回填任何历史 Artifact，也不改变其 SHA；
- 历史 Artifact 缺失的阶段固定显示 `Not recorded by this artifact schema`；
- 完整五阶段 Replay 仅在 Snapshot 存在、schema 受支持且 SHA 校验成功时可用；
- Snapshot 缺失或损坏时 fail closed，不用当前代码重建；
- 新 Eval runner 必须在产生候选和决策的同一真实执行路径上冻结 Snapshot，诊断服务不得复制 retrieval 算法。

### Task 0B.3：升级 Trace latency contract

目标模型：

```text
semantic_ms
lexical_ms
fusion_ms
rerank_ms
evidence_gate_ms
total_ms
parallel_wall_clock_note/version
```

实施要求：

- 在实际阶段边界调用 `perf_counter()`；
- 不通过 `total - semantic - lexical` 估算子阶段；
- Semantic 与 Lexical 仍可并行；
- 总耗时不要求等于阶段相加；
- 每个 latency 非负并明确单位；
- degraded/fallback 路径仍能生成一致 schema；
- 若某阶段未执行，返回 `null` 或明确 `executed=false`，不返回伪造 0；
- Trace schema 升级需保持历史 Artifact 可读。

在此 Task 完成前，前端只展示现有：

```text
semantic
lexical
fusion_rerank_and_selection
total
```

### Task 0B.4：增加 No-evidence Confusion Summary

`NoEvidenceConfusionSummary` 使用业务语义字段：

```text
correct_evidence
false_abstention
false_evidence
correct_abstention
```

服务端负责计算并测试，不要求前端理解正类定义。

### Task 0B.5：可选 Consumer Evidence Action Adapter

只有代码中已存在权威、版本化、可追溯的消费者决策时，才为对应 consumer 增加安全适配器：

```text
consumer
action
policy_version
reason_codes
source EvidenceDecision hash/reference
```

没有真实策略或持久记录时，DTO 返回显式状态：

```text
recording_status = not_recorded
public_message = Not recorded / no unified policy
```

不得为了完成 Phase 0B 或 UI 验收而定义 Reviewer/Follow-up 的新 sufficiency/action 规则。No-evidence policy 与 Consumer policy 优化属于独立工作流。

### Phase 0B 完成定义

- Candidate ranking explanation 与实际排序同源；
- 新运行可生成并校验 `RetrievalDiagnosticSnapshotV1` sidecar；
- 带 Snapshot 的 case 可完整重放五阶段，历史 case 诚实降级；
- latency contract 真实且可回放，未执行阶段不伪造为 0；
- No-evidence 四格计数由服务端提供；
- Consumer Action 仅在真实记录存在时显示，否则明确 `not_recorded`；
- Phase 1/2 对增强字段的组件与测试通过。

## 9. Phase 1：P0 Retrieval Inspector

### 9.1 页面目标

Inspector 必须回答：

- 当前执行的是 Legacy、Hybrid diagnostic、Artifact replay 还是 current rerun；
- query 使用了哪个 profile、corpus、embedding 与 component version；
- Semantic 与 Lexical 分别返回了什么；
- Fusion 如何合并候选；
- Rerank 如何改变顺序；
- 哪些候选最终成为 Evidence；
- Evidence Gate 的状态、缺失信号和原因是什么；
- 延迟来自哪个真实阶段；
- 结果是否具有发布证据资格。

### 9.2 页面布局

建议桌面结构：

```text
RAG Console Shell
├── RAG subnav
├── Execution identity strip
├── Query / replay controls
├── Current signals and profile
├── Unified candidate pipeline table
├── Candidate explanation drawer
└── Evidence decision + latency panels
```

窄屏：

- 表格使用受控横向滚动；
- 候选详情进入独立 drawer/section；
- 不把每行重排成超长卡片瀑布；
- 核心状态与模式标识保持首屏可见。

### Task 1.1：新增页面与路由骨架

建议新增：

```text
frontend/src/pages/RagRetrievalPage.jsx
frontend/src/pages/RagRetrievalPage.test.jsx
frontend/src/rag/useRetrievalInspection.js
frontend/src/rag/ragDisplay.js
frontend/src/styles/pages/rag-console.css
```

要求：

- 页面懒加载；
- 使用共享 `AppShell`、`PageHeader`、`AsyncState`、`StatusNotice`；
- 不复制 request、timeout 或错误处理；
- route load failure 继续由 `RouteLoadBoundary` 处理；
- API unavailable 时显示诚实边界。

### Task 1.2：实现 Query 与 replay 控件

两种主要入口：

```text
Live diagnostic
Artifact replay
```

Live diagnostic：

- 使用 textarea，不把 query 写入 URL；
- 明确显示可能调用 embedding Provider；
- 展示 engine/profile，只能选择后端 allowlist；
- 提交期间禁用重复操作；
- 提供取消；
- 刷新后不恢复原始 query；
- 不使用 sessionStorage/localStorage 保存原文。

Artifact replay：

- 通过 artifact SHA + case reference 加载；
- 不显示 live fee warning；
- 明确显示冻结时间、revision 与 dataset；
- 显示 `full_snapshot` 或 `partial_historical` 诊断保真度；
- 只有 `RetrievalDiagnosticSnapshotV1` 校验成功时显示完整五阶段；
- 历史 Artifact 只展示原本冻结字段，不调用当前引擎补齐；
- 提供独立的“用当前引擎重跑”操作，只有权限允许时显示。

### Task 1.3：实现 Current Signals 与 Profile 区域

第一版只展示真实后端字段：

```text
intent
requested domains/topics
canonical tags
hard filter summary
semantic enabled
lexical enabled
fusion strategy
fixed semantic/lexical weights
candidate/evidence limits
timeouts
```

允许展示候选级 `matched_terms`。

暂不展示或标记 unavailable：

```text
query signal class
dynamic semantic weight
dynamic lexical weight
classifier confidence
dynamic routing reason
```

前端不自行提取 technical terms 或 alias。

### Task 1.4：实现统一 Candidate Pipeline Table

列建议：

| 列 | 内容 |
| --- | --- |
| Candidate | ID、标题、safe excerpt、domain/topic |
| Semantic | rank 与 score |
| Lexical | rank、score 与 matched terms |
| Fusion | rank、score、strategy |
| Rerank | rank、final score |
| Final | selected / rejected / not considered |
| Reasons | 稳定 reason codes |

交互：

- 按任意 rank、score、selected 状态排序；
- 默认按最终 Evidence 顺序，再按 Fusion rank；
- `null` 与 0 严格区分；
- 分数显示足够精度，不通过颜色夸大微小差异；
- ID、SHA 可复制；
- 行展开打开 Candidate Explanation；
- 表格表头 sticky；
- 键盘可以进入、展开、关闭详情。

### Task 1.5：实现 Candidate Explanation Drawer

展示：

```text
base score source/value
semantic contribution
lexical contribution
fusion strategy/rank
exact-term boost
routing-tag boost
final rerank score
minimum-score eligibility
rerank/evidence limits
tie-break inputs
selection result
reason codes
```

如果历史 Artifact 不包含某个字段：

- 显示 `Not recorded by this artifact schema`；
- 不通过当前代码重新推断；
- 不把 missing 显示为 0。

### Task 1.6：实现 Evidence Decision Panel

固定展示：

```text
Availability
Sufficiency
Consistency
Evaluation Confidence
Consumer Action Record
Covered Signals
Missing Signals
Reason Codes
Gate Version
```

状态颜色：

- Evidence 状态与 Consumer Action 分区展示，不能由前者推导后者；
- Consumer Action 未记录时显示中性 `Not recorded / no unified policy`；
- 只有后端真实记录的 allowed action 才能显示对应 action 状态；
- `weak` 使用 warning；
- `insufficient`、`confirmed_conflict` 使用 blocked/fail；
- `not_evaluated` 与 `unavailable` 使用中性灰，不伪装为失败；
- `degraded` 与 `insufficient` 不共用同一标签。

### Task 1.7：实现 Latency Panel

Trace V2 模式：

```text
Semantic
Lexical
Fusion/Rerank/Selection combined
Total
```

Trace V3 模式：

```text
Semantic
Lexical
Fusion
Rerank
Evidence Gate
Total
```

必须显示说明：Semantic 与 Lexical 可能并行，阶段值不能简单相加。

前端 MUST NOT 通过相减推导阶段 latency。

### Task 1.8：隐私、费用与清理行为

- 原始 query 只存在于当前组件内存和当前 POST body；
- 完成请求后用户可一键清空；
- 离开页面时丢弃；
- 服务端响应默认不回显 query；
- 不进入 URL、local/session storage、analytics、普通日志、Trace、Artifact、metric label 或错误 message；
-错误 UI 只显示 public code/message/request ID；
- Live 模式显示 Provider call 可能性；
- Artifact replay 显示 `No provider call`；
- safe excerpt 仍视作受控内部数据，不提供批量下载。

### Retrieval Inspector MVP 验收

| 编号 | 标准 |
| --- | --- |
| RI-1 | Live 与 Artifact replay 模式在视觉和 Contract 上严格区分 |
| RI-2 | 原始 query 不进入 URL、storage、普通日志或安全 Trace |
| RI-3 | 带 `RetrievalDiagnosticSnapshotV1` 的 case 可看到 Semantic/Lexical/Fusion/Rerank/Final；历史 case 只展示其冻结字段 |
| RI-4 | Fusion→Rerank 排名解释来自后端真实计算 |
| RI-5 | Evidence 展示四个正交状态；Consumer Action 只显示真实记录或显式 `not_recorded` |
| RI-6 | Trace V2 不伪造独立 Fusion/Rerank/Gate latency |
| RI-7 | 历史 Artifact 缺失字段诚实显示为未记录 |
| RI-8 | 页面不返回或渲染完整 KnowledgeChunk、embedding 或私有 metadata |
| RI-9 | Legacy 默认、rollout 0 与 Shadow disabled 未改变 |

## 10. Phase 2：P0 Eval Dashboard

### 10.1 页面目标

Dashboard 必须回答：

- 当前比较绑定哪一份 Dataset 与 Artifact；
- Legacy、Semantic-only、Lexical-only 与 Hybrid RRF 谁在什么指标上更好；
- Candidate 相对 Legacy 的 paired delta；
- 哪些 case type 造成主要赢/输；
- No-evidence 的具体错误分布；
- 哪些 case 可以在 Inspector 中冻结重放；
- 这些结果是否有资格参与 promotion。

### Task 2.1：新增页面与数据 hook

建议新增：

```text
frontend/src/pages/RagEvaluationPage.jsx
frontend/src/pages/RagEvaluationPage.test.jsx
frontend/src/rag/useRagEvaluations.js
frontend/src/rag/evalDisplay.js
```

页面不直接 import 大型 JSON Artifact。所有数据通过受保护 API 获取。

### Task 2.2：Dataset Identity 优先区域

任何 KPI 之前展示：

```text
Dataset name/version/SHA
Split classification
Corpus manifest
Embedding provider/model/revision
Engine/code/profile identity
Artifact SHA
Human annotator count/status
Independent evidence eligibility
Holdout status
Promotion decision
```

当前机器预标注必须明确显示：

```text
Human annotators: 0
Independent release evidence: No
Holdout: Historical diagnostic
Promotion decision: Unavailable / blocked
```

### Task 2.3：实现引擎比较矩阵

第一版主比较：

```text
Legacy
Semantic-only
Lexical-only
Hybrid weighted RRF
```

Rank-normalized 放入 `Historical / rejected candidates`，不与主候选同等强调。

核心指标：

```text
Recall@5
MRR@5
NDCG@5
Hit@1
Filter correctness
No-evidence precision
No-evidence recall
No-evidence F1
P95 latency
Replay stability
```

展示规则：

- 同时显示绝对值和 Candidate - Legacy delta；
- paired delta 必须来自后端配对 Artifact；
- 没有相同 dataset/split/case identity 时禁止比较；
- `thresholds_passed = null` 显示 Not evaluated，不显示失败；
- 指标最优不自动显示 Promotion passed。

### Task 2.4：实现 No-evidence Confusion Matrix

使用业务标签：

| Actual | System | UI label |
| --- | --- | --- |
| Evidence exists | Return evidence | Correct evidence |
| Evidence exists | Abstain | False abstention |
| No evidence | Return evidence | False evidence |
| No evidence | Abstain | Correct abstention |

同时显示：

```text
no-evidence prevalence
abstention rate
precision
recall
F1
```

所有计数来自后端 `NoEvidenceConfusionSummary`。

### Task 2.5：实现 Case Type Breakdown

Case type 列表从 Artifact/Contract 返回，不由前端硬编码。第一版必须支持当前正式类型，包括但不限于：

```text
alias_only
semantic_paraphrase
hard_negative
cross_domain_confusion
no_evidence
out_of_domain
```

每行展示：

```text
case count
Recall@5
MRR@5
NDCG@5
Hit@1
Evidence precision@5
Domain/topic accuracy
No-evidence precision/recall
Candidate paired delta
```

如需 wins/losses/ties，必须由后端定义胜负比较规则并返回，前端不自行选择指标判胜。

### Task 2.6：实现 Case Explorer

从某个 case type 展开失败 case：

```text
case_id
case_type
engine outcomes
primary/accepted/excluded IDs（仅允许公开的 ID）
paired delta summary
failure classification
artifact replay reference
```

不返回 sealed holdout；不返回 raw query，除非该 Artifact 的治理状态明确允许且 Diagnostic API 有额外权限。机器预标注 tuning 的 query 展示也应独立受控。

### Task 2.7：实现 Eval → Inspector 联动

按钮：

```text
Open frozen replay in Inspector
```

传递：

```text
artifact_sha
case_reference
engine_variant
mode=artifact_replay
```

不得把 raw query 放进 URL。Inspector 从后端读取冻结 Artifact。

若 `diagnostic_snapshot_ref` 不存在，按钮仍可打开 partial historical replay，但必须在进入前和页面内说明缺少哪些阶段；不得标记为完整五阶段 replay。

如提供当前重跑，按钮必须单独命名：

```text
Run this case with current engine
```

并显示代码/corpus drift 与 Provider 调用提示。

### Eval Dashboard MVP 验收

| 编号 | 标准 |
| --- | --- |
| ED-1 | Dataset/Artifact/annotation identity 在 KPI 之前展示 |
| ED-2 | 四个主引擎使用同一 dataset/split 才能比较 |
| ED-3 | Paired delta 来自后端 Artifact，不由前端计算 |
| ED-4 | No-evidence 四格计数与服务端一致 |
| ED-5 | Case type 从 Contract 动态读取 |
| ED-6 | 失败 case 可进入冻结 replay；Snapshot case 完整五阶段，历史 case 明确 partial historical |
| ED-7 | 当前 25-case 显示 historical diagnostic，不显示 sealed/formal |
| ED-8 | machine preannotation 不显示 promotion passed |
| ED-9 | sealed holdout 无单案例读取路径 |

## 11. Phase 3：P1 RAG Overview

### 11.1 页面目标

回答：当前系统在运行什么，Candidate 是什么，为什么不能 promotion，以及不同 blocker 阻止的是哪个阶段。

### Task 3.1：运行身份区域

展示：

```text
Formal engine
Candidate engine
Hybrid rollout
Shadow state
Remote reranker state
Evidence gate state
Corpus identity
Embedding identity
Profiles
Component versions
```

### Task 3.2：Promotion blocker 列表

典型 blocker：

```text
HUMAN_TUNING_GT_MISSING
SEALED_HOLDOUT_MISSING
NO_EVIDENCE_GATE_FAILED
HYBRID_NOT_BETTER_THAN_LEGACY
BUSINESS_BLIND_AB_PENDING
SHADOW_NOT_AUTHORIZED
```

每个 blocker 展示：

```text
severity
scope
blocks merge?
blocks candidate activation?
blocks Shadow?
blocks Canary/production?
observed evidence
required action
last evaluated at
```

页面必须帮助用户理解：

```text
Code merged
≠ Candidate promoted
≠ Shadow authorized
≠ Canary started
≠ Legacy retired
```

### Task 3.3：状态真实性

- `not_evaluated` 不显示红色失败；
- `pending human annotation` 不显示运行故障；
- `blocked` 明确显示阻断范围；
- `unavailable` 表示信息不可用；
- 只有后端正式 `allowed=true` 才显示 Promotion passed。

## 12. Phase 4：P1 Evidence Trace

### 12.1 页面目标

以纵向 Timeline 展示：

```text
BaseEvidenceBundle
    ↓
QuestionEvidenceBinding
    ↓
ReviewEvidenceBinding
    ↓
Reviewer Decision
    ↓
Follow-up Decision
```

不使用复杂 DAG。

### Task 4.1：定义 Safe Evidence Trace DTO

允许：

```text
trace_id
binding_id
parent_binding_id
stage
status
evidence_ids
content hashes
corpus manifest
safe source metadata
availability/sufficiency/consistency/confidence
reason codes
policy/gate version
consumer action
latency
timestamp
```

禁止：

```text
raw query
candidate answer
resume
JD
provider prompt/response
embedding
DSN
filesystem path
API key
full knowledge body
chain-of-thought
```

### Task 4.2：实现 Timeline

每个节点显示：

- stage 与稳定 ID；
- parent reference；
- Evidence IDs 与 hash；
- decision；
- reason codes；
- policy version；
- timestamp；
- degraded/failure 状态。

点击 Evidence ID 只能打开 Safe Evidence Ref，不加载完整原文。

### Task 4.3：解释边界

页面固定说明：

> This trace shows persisted evidence lineage and policy decisions. It does not expose or reconstruct model chain-of-thought.

## 13. Phase 5：P2 Knowledge Corpus Read-only

### 13.1 页面目标

只读展示当前 corpus 与 Knowledge Unit 治理状态。

### Task 5.1：Corpus identity

展示：

```text
active corpus version
manifest SHA
chunk count
embedding provider/model/revision/dimension
activation status
legacy/retired versions summary
```

### Task 5.2：Knowledge Unit 列表

展示：

```text
unit ID
domain
topic
aliases
canonical tags
source authority
review status
version
retirement status
embedding status
```

第一版没有：

```text
Edit
Delete
Re-embed
Publish
Activate
Retire
Rollback
```

### Task 5.3：Safe detail

详情只展示可公开摘要、hash 与 governance metadata；不展示 source URL、完整正文或 ingestion 内部定位。

## 14. 前端设计 Contract

### 14.1 视觉方向

RAG Console 是工程控制台，不是营销页或 AI 大屏：

- 信息密度适中；
- 中性背景、克制边框；
- 使用现有 token 与系统字体；
- 标题紧凑，不使用巨大 hero；
- 表格、分隔线与排版承担主要结构；
- 关键 ID、SHA、revision 使用等宽字体；
- 操作以诊断与复制为主，不使用装饰性 CTA。

### 14.2 状态语义

至少区分：

```text
passed
failed
blocked
pending
not_evaluated
unavailable
degraded
```

规则：

- 绿色只表示正式通过；
- 黄色表示 pending、warning 或 degraded；
- 红色只用于 failed、blocked 或 hard stop；
- 灰色表示 unavailable/not evaluated；
- `available`、`sufficient`、`consistent`、`recorded` 等事实状态默认使用中性色，不得因“看起来正向”自动映射为绿色；
- 不仅依赖颜色，必须同时使用文字与图标；
- `blocked` 必须说明阻断范围。

### 14.3 表格

- sticky header；
- 可排序但不隐藏原始 rank；
- 数值列右对齐；
- `null` 显示 `—`；
- score 精度由后端/字段定义，不自行四舍五入到失真；
- 窄屏使用受控滚动；
- 焦点顺序清晰；
- 展开详情使用 button 与 `aria-expanded`。
- Candidate ID、Artifact SHA、manifest/profile/code revision 等诊断 identity 使用共享复制控件，复制的是完整原值而不是界面截断值；
- 复制动作必须提供 `Copied` / `Copy failed` 反馈，且不得为每个页面各自实现一套 clipboard 状态。

### 14.4 Motion

- 只允许轻微展开、tab 切换与状态过渡；
- 不使用滚动驱动叙事；
- 不使用 AI pulse；
- 支持 `prefers-reduced-motion`；
- 动画不承担唯一状态表达。

### 14.5 图表边界

允许：

- paired delta bar；
- latency distribution；
- No-evidence confusion matrix；
- case-type win/loss（后端已有正式规则时）；
- coverage vs false-evidence curve（校准数据具备后）。

不允许：

- 雷达图；
- 3D 图；
- 装饰性 donut；
- vector scatter；
- 没有统计意义的 gauge。

## 15. API Contract

以下路由已经进入实现。收口阶段只允许修正 Contract、安全边界与验收缺口，不再另建重复诊断 API。

### 15.1 `GET /api/rag/overview`

用途：返回安全运行身份、Candidate 与 promotion blockers。

访问条件：Diagnostic UI capability enabled，并通过所选 loopback-only 或 authenticated-principal authorization boundary。

禁止：环境变量、密钥、DSN、路径、raw Provider 配置。

### 15.2 `POST /api/rag/inspections`

用途：同步执行 live diagnostic 或 current rerun，并直接返回 `SafeRetrievalInspectionResponse`。

访问条件：Live Inspector capability enabled，并具备 live-inspect authorization。

约束：POST body query；受限 engine/profile；超时、并发、费用提示；不落原文日志；不回显 raw query；V1 不创建服务端 inspection storage。

### 15.3 `GET /api/rag/evaluations`

用途：列出允许访问的 Artifact 摘要。

过滤：dataset、split classification、engine、date、eligibility。

不列出 sealed holdout 原始 Artifact。

### 15.4 `GET /api/rag/evaluations/{artifact_sha256}`

用途：返回 identity、metrics、governance 与 paired comparison。

要求：SHA 精确匹配；Artifact 自哈希验证；不接受文件路径。

### 15.5 `GET /api/rag/evaluations-paired`

用途：返回后端生成的 paired comparison；前端不得自行配对 Artifact 或重新计算阈值结论。

要求：只比较身份兼容、可验证且在普通 Catalog 可见的 Artifact；`thresholds_passed = null` 表示 `Not evaluated`，不得显示为通过或失败。

### 15.6 `GET /api/rag/evaluations/{artifact_sha256}/cases`

用途：返回允许访问的 case 摘要与 replay reference。

禁止：sealed holdout；未经许可的 raw query；完整 Knowledge body。

case 响应携带 `diagnostic_fidelity` 与可选 opaque `diagnostic_snapshot_ref`。Snapshot 按 Artifact Catalog allowlist 与 SHA 读取，客户端不得提交磁盘路径。

### 15.7 `GET /api/rag/evaluations/{artifact_sha256}/no-evidence`

用途：返回 Correct evidence、False abstention、False evidence、Correct abstention、样本总量、No-evidence prevalence、abstention rate 以及 precision/recall/F1。

要求：四格计数与比率均由后端基于 Artifact 自身的已校验 Dataset 计算，前端不得从 case 列表二次推导。

### 15.8 `GET /api/rag/evaluations/{artifact_sha256}/cases/{case_reference}/diagnostic-snapshot`

用途：按需读取并返回通过 SHA 校验的 `RetrievalDiagnosticSnapshotV1` Safe DTO。

约束：Artifact access capability + authorization；只接受 opaque case reference；Snapshot 不存在时返回明确 unavailable，不使用当前代码补建；sealed holdout 无此普通端点。

### 15.9 `GET /api/rag/evidence-traces/{trace_id}`

用途：返回 Safe Evidence lineage。

要求：只用 opaque ID；不接受磁盘路径；返回 allowlist DTO。

### 15.10 `GET /api/rag/corpus`

用途：返回 active/retired corpus 与 Knowledge Unit 安全摘要。

第一版只读。

## 16. 前端组件与文件建议

建议新增：

```text
frontend/src/pages/RagOverviewPage.jsx
frontend/src/pages/RagRetrievalPage.jsx
frontend/src/pages/RagEvaluationPage.jsx
frontend/src/pages/RagEvidenceTracePage.jsx
frontend/src/pages/RagCorpusPage.jsx

frontend/src/components/rag/RagConsoleShell.jsx
frontend/src/components/rag/RagSubnav.jsx
frontend/src/components/rag/ArtifactIdentityPanel.jsx
frontend/src/components/rag/PromotionBlockerList.jsx
frontend/src/components/rag/RetrievalCandidateTable.jsx
frontend/src/components/rag/CandidateExplanationDrawer.jsx
frontend/src/components/rag/EvidenceDecisionPanel.jsx
frontend/src/components/rag/LatencyBreakdown.jsx
frontend/src/components/rag/MetricComparisonTable.jsx
frontend/src/components/rag/NoEvidenceConfusionMatrix.jsx
frontend/src/components/rag/CaseTypeBreakdown.jsx
frontend/src/components/rag/EvidenceLineageTimeline.jsx

frontend/src/rag/ragApi.js
frontend/src/rag/ragDisplay.js
frontend/src/rag/useRagOverview.js
frontend/src/rag/useRetrievalInspection.js
frontend/src/rag/useRagEvaluations.js
frontend/src/rag/useEvidenceTrace.js

frontend/src/styles/pages/rag-console.css
```

边界：

- API 业务函数放 `ragApi.js`，底层请求仍复用 `client.js`；
- hooks 管理异步状态，不复制领域算法；
- display helpers 只做枚举到公共文案/样式映射；
- 不在前端实现 metric、fusion、promotion 或 Evidence Action 算法；
- 共享组件不依赖某一具体 Artifact schema 的私有字段。

## 17. 测试计划

### 17.1 后端 Contract 测试

建议新增：

```text
tests/contracts/test_rag_console_contracts.py
tests/unit/test_rag_diagnostics_service.py
tests/unit/test_rag_artifact_registry.py
tests/unit/test_rag_console_routes.py
tests/architecture/test_rag_console_boundaries.py
```

覆盖：

-所有 DTO `extra=forbid`；
-字段枚举严格对齐领域 Contract；
- Safe Candidate 不包含 `content`、raw metadata、URL、query；
- Promotion 决策由 service 提供；
- No-evidence 四格计数正确；
- Ranking explanation 与 reranker 同源；
- Artifact SHA/self-hash 校验；
- Artifact detail endpoint 的 Safe DTO、合法 Artifact 成功路径与非法 SHA/路径型输入拒绝路径；
- Snapshot ref/SHA/schema 校验与损坏时 fail-closed；
- 一个完整 Snapshot fixture 同时保留 Semantic、Lexical、Fusion、Rerank、最终 Evidence、Evidence Decision、阶段 latency 与 ranking explanation；
- 历史 Artifact 不补造缺失五阶段字段；
- 同步 Inspection 响应不建立可读取的服务端资源；
- V2/V3 Trace 兼容；
- sealed holdout 拒绝；
-默认 capability 关闭；
-错误不泄露 Provider detail、DSN 或路径。

### 17.2 权限与隐私测试

- 未启用返回 404；
- capability 开启但 principal/来源未授权时 fail closed；
- strict loopback 模式拒绝非 loopback 请求和伪造 forwarded headers；
- UI enabled 但 live disabled 时 POST inspection 返回 404/403；
- raw query 不出现在日志、Trace、exception 或响应；
- raw query 不进入 URL、storage、Artifact、Snapshot、metric label 或 analytics；
- Artifact API 不接受任意文件路径；
- path traversal 被拒绝；
- unblinding key、provider cache 与 private Artifact 不可索引；
- sealed holdout case 无 API；
- authored Eval query 权限与 Artifact 指标读取权限分开；
- safe excerpt 长度与字符清洗受控。

### 17.3 排名与 latency 测试

- Fusion-first 对抗用例在 explanation 中显示 `base_score_source=fusion_score`；
- exact-term 与 routing boost 分解之和与 final score 一致；
- tie-break 可重放；
-未执行阶段不伪造 0ms；
- Semantic/Lexical 并行时 total 不被错误要求等于相加；
- fallback/degraded 路径仍有合法 Trace。

### 17.4 前端单元与组件测试

建议新增页面与关键组件测试，覆盖：

-路由懒加载；
- capability unavailable；
- loading/empty/error/success/degraded；
- Artifact replay 与 live 模式；
- full snapshot 与 partial historical replay；
- full snapshot 页面同时呈现 Semantic、Lexical、Fusion、Rerank、Final Evidence 与 Evidence Gate；
- query 不进 URL/storage；
-候选表排序与 null；
-详情展开与键盘；
-四个 Evidence 状态维度；
-历史 Artifact 缺字段；
- No-evidence 四格；
- machine preannotation eligibility warning；
- Evaluation → Inspector replay 跳转不包含 raw query；
- 前端不计算 promotion；
- 绿色只保留给正式 pass/allow，普通 available 为中性；所有状态同时有文字与符号；
- Candidate ID 与 Artifact/manifest/profile/code identity 复制完整值并显示成功/失败反馈；
- rejected rank-normalized Artifact 不进入默认正式比较视图。

### 17.5 Architecture 测试

验证：

- API route 只依赖 application service 与 Safe DTO；
- frontend 不读取仓库磁盘 Artifact；
- adapter 不包含 UI/promotion policy；
-核心 fusion/rerank 计算不复制到 diagnostics service；
- Safe Read Model 不反向污染领域模型；
- sealed holdout owner 与普通 Artifact registry 分离。

### 17.6 集成与 Acceptance

至少覆盖：

1. 默认启动没有公开 RAG Console API；
2. capability 开启且所选 loopback/principal authorization 通过后能读取 Overview；
3. Live Inspector capability 与 authorization 都通过后可以同步运行固定测试 query；
4. Artifact replay 不调用 Provider；
5. 同一个 Snapshot case 可完整重放 Semantic、Lexical、Fusion、Rerank、最终 Evidence 与 Gate，包含非空 Evidence Decision、阶段 latency 与 ranking explanation；Provider 调用计数为零；历史 case 可打开 partial replay 且不补造字段；
6. Evidence Trace 只含 allowlist 字段；
7. current engine 仍为 Legacy；
8. Hybrid rollout 仍为 0；
9. Shadow/Canary 未启动；
10. sealed holdout 访问失败。
11. 不存在 `GET /api/rag/inspections/{id}` 或服务端 inspection TTL/store；
12. 未记录 Consumer Action 时 UI 显示 `Not recorded / no unified policy`。
13. Artifact detail endpoint 对合法 Artifact 返回 Safe detail；对 sealed/private Artifact、非法 SHA 与路径型输入 fail-closed，错误不泄露磁盘路径、正文或请求载荷；
14. 状态语义与复制动作符合 14.2/14.3：绿色仅表示正式通过，ID/SHA/revision 复制完整原值并有可访问反馈。

### 17.7 建议验证命令

先运行聚焦 Contract/路由/目录/Promotion/检索回归：

```powershell
F:\python3.11\python.exe -m pytest `
  tests/contracts/test_rag_console_contracts.py `
  tests/unit/test_rag_artifact_catalog.py `
  tests/unit/test_rag_console_routes.py `
  tests/unit/test_rag_promotion_service.py `
  tests/unit/test_knowledge_retrieval_service.py `
  tests/unit/test_knowledge_runtime_retrieval.py `
  tests/architecture/test_api_router.py -q
```

再运行相关后端全矩阵：

```powershell
$ragTests = @(rg --files tests/unit tests/contracts | rg "(?:knowledge|rag|effective_runtime_config)")
F:\python3.11\python.exe -m pytest @ragTests -q
F:\python3.11\python.exe -m pytest tests/architecture tests/acceptance -q
```

最后运行前端全矩阵：

```powershell
Set-Location frontend
npm.cmd test -- --run
npm.cmd run check
npm.cmd run build
```

交付前还需执行：

```powershell
git diff --check
git status --short
rg -n "TODO|FIXME|placeholder|chunk\.content|_repository\._repository" app frontend tests docs
```

测试结果必须按“通过 / 与本任务无关的既有失败 / 授权阻塞”分类记录，不能用一次局部通过替代全矩阵结论。

### 17.8 受保护 PostgreSQL 验证前置条件

受保护 PostgreSQL fixture 要求以下授权元数据全部存在且可核验：

```text
POSTGRES_TEST_APPROVAL_ID
POSTGRES_TEST_APPROVAL_RECEIPT_SHA256
POSTGRES_TEST_APPROVED_FINGERPRINT
POSTGRES_TEST_DATABASE_ALLOWLIST
POSTGRES_TEST_APPROVAL_EXPIRES_AT
```

执行前还必须确认：

- 目标指纹与授权记录完全一致；
- 授权尚未过期；
- 测试只使用自动生成的 `test_*` 隔离表前缀并在结束后清理；
- 目标受保护节点必须通过共享 fixture 生成并跟踪 `test_*` 前缀；当前 `test_postgres_round_trips_hash_only_audit` 已满足该代码约束；
- 其他仍使用 `knowledge_<uuid>` 的旧 pgvector 集成测试不属于本节点的授权范围，不得借用本节点授权原样执行；
- 不从聊天文本推导或伪造 approval ID、receipt、allowlist 或 expiry。

任一条件缺失时，本地可完成的 M7 项继续推进，PostgreSQL 项记录为外部阻塞。

2026-08-14 F5 正式执行记录：用户签发结构化授权 `interview-rag-console-pg-20260814-001`。授权收据写入系统临时目录并设为只读，文件 SHA-256 为 `6efbb174ea1a70268228e99d578bca67e0331c30bc6611397101cf7821a216c4`；五项 `POSTGRES_TEST_*` 仅注入指定 pytest 子进程。受保护节点 `test_postgres_round_trips_hash_only_audit` 结果为 `1 passed in 0.87s`。实际数据库名为 `interview`，目标指纹为 `5e025dd48cab1ffe94fb19b4837cafa66c247e323a1246cb2354f18ba3b0136e`，与授权完全一致。共享 fixture 对自动生成的 `test_*` owned scope 完成 ownership/target 校验并要求 cleanup receipt；测试退出后额外只读复核 `test_test_post_*` 前缀族，`residue_count=0`。本授权未扩大到其他 PostgreSQL 节点、旧 `knowledge_<uuid>` 测试、业务表或发布操作。

最终本地证据：

```text
RAG focused contract/unit/architecture: 41 passed
Relevant Knowledge/RAG selection:       316 passed; 1 protected PostgreSQL node
                                         deselected for the local-only proof
Unfiltered relevant selection:           316 passed; 1 authorization setup error
Architecture + Acceptance:               441 passed
Frontend:                                12 files / 129 tests passed
Protected PostgreSQL node:               1 passed in 0.87s; target matched;
                                         owned cleanup proven; residue_count=0
ESLint / production build:           passed
Python compileall:                    passed
Initial JS gzip:                      67,470 / 67,584 bytes
RAG lazy chunk:                       39.44 kB raw / 11.19 kB gzip
OpenAPI:                             60 paths / 66 operations
Browser:                             Overview, Eval/Replay, Inspector drawer,
                                     Evidence Trace error/empty, Corpus Safe detail,
                                     390 × 844 page-overflow containment passed
Runtime defaults:                    legacy / rollout 0 / shadow false /
                                     remote reranker false / console flags false
```

## 18. 里程碑与任务分解

### M0A：Console Contract Minimum

交付：

- Safe Read Models；
- capability 与 authorization policy；
- 同步 Inspection request/response；
- 只读 allowlisted Artifact Catalog；
- Artifact Replay/Rerun 语义；
- raw query 生命周期；
- sealed holdout policy；
- OpenAPI/Contract tests。

完成条件：Phase 0A 全部完成定义并通过 contract/privacy/authorization tests。此时 Phase 1/2 基础 UI 可开始。

### M0B：Diagnostic Fidelity

交付：

- Candidate Ranking Explanation；
- `RetrievalDiagnosticSnapshotV1` sidecar；
- Trace latency V3；
- No-evidence confusion summary；
- 真实 Consumer Action adapter（仅在现有策略与记录存在时）。

完成条件：新 Snapshot 可完整五阶段 replay，历史 Artifact 明确 partial historical，所有缺失字段不被推断。

### M1：Diagnostic API

交付：

- Overview；
- Inspection；
- Evaluation registry/read；
- Evidence Trace；
- Corpus read-only endpoints；
-隐私、权限与 Artifact 校验测试。

完成条件：API 在默认环境 404，在 capability 与 authorization 均通过时返回 Safe DTO。

依赖：M1 在 M0A 通过后开始，可与 M0B 并行；Snapshot、Ranking Explanation、Trace V3 与 No-evidence 增强随 M0B 增量接入，不阻塞基础同步 Inspection 和只读 Artifact API。

### M2：Retrieval Inspector MVP

交付：

- live/artifact 两模式；
-统一候选表；
-支持字段的排名解释与明确 unavailable 降级；
- Evidence Decision；
- latency；
-隐私与费用提示。

完成条件：RI-1 至 RI-9 全部通过。

### M3：Eval Dashboard MVP

交付：

- identity-first 页面；
-四引擎比较；
- paired delta；
- No-evidence 四格；
- case-type breakdown；
- Eval→Inspector replay。

完成条件：ED-1 至 ED-9 全部通过。

### M4：Overview

交付：运行身份、Candidate、component versions 与 blockers。

完成条件：前端绝不自行计算 promotion，所有 blocker 范围正确。

### M5：Evidence Trace

交付：安全纵向 lineage。

完成条件：无 raw query、答案、简历、JD、Provider data 或 CoT。

### M6：Corpus read-only

交付：Corpus 与 Knowledge Unit 安全只读页。

完成条件：无写操作、无 source URL/完整正文泄露。

### M7：Hardening and acceptance

交付：

-全测试矩阵；
-可访问性；
-响应式；
-权限审计；
- Artifact replay 稳定性；
-文档与 runbook；
-默认关闭验证。

完成条件：0.2 的四项补证全部有自动化证据，17.7 全矩阵在最终代码上重新执行并分类记录；受保护 PostgreSQL 只在 17.8 全部前置条件满足后执行。上述条件现已满足，F5 的结构化授权、目标匹配、owned-scope 清理与零残留证据记录在 17.8。

## 19. 建议实施批次

### Batch A：契约与权限

```text
A1 Capability + authorization boundary
A2 Overview DTO
A3 Safe Candidate DTO
A4 Synchronous Inspection request/response
A5 Read-only allowlisted Artifact Catalog
A6 Replay/Rerun + raw query lifecycle
A7 Sealed holdout policy
```

### Batch B：诊断数据真实性

```text
B1 Ranking explanation
B2 RetrievalDiagnosticSnapshotV1 sidecar
B3 Trace latency V3
B4 No-evidence confusion summary
B5 Optional Consumer Action adapter for real recorded policies
```

### Batch C：P0 Inspector

```text
C1 Route and shell
C2 Query/replay controls
C3 Current signals/profile
C4 Candidate table
C5 Explanation drawer
C6 Evidence Decision
C7 Latency/privacy hardening
```

### Batch D：P0 Dashboard

```text
D1 Identity panel
D2 Engine comparison
D3 Paired deltas
D4 No-evidence confusion
D5 Case-type breakdown
D6 Case explorer
D7 Inspector replay link
```

### Batch E：P1/P2 页面

```text
E1 Overview
E2 Evidence Trace
E3 Corpus read-only
```

### Batch F：Acceptance

```text
F1 Contract and privacy
F2 Frontend unit/build
F3 Browser flows
F4 RAG/architecture/acceptance
F5 Protected PostgreSQL
F6 Default-off and no-rollout verification
```

每个 Batch 保持独立、可审查的逻辑变更范围。只有用户明确授权提交时才创建 Git commit；未获授权时保留工作树改动并提供按 Batch 分组的审查清单。禁止把 Contract、所有 API、五个页面与大规模视觉重构压成一个不可审查单元。

依赖关系：Batch A 是硬前置；A 完成后，Batch B 与 Batch C/D 的基础视图可并行。Batch C/D 中依赖 Snapshot、Ranking Explanation、Trace V3 或 No-evidence summary 的增强项，必须等待对应 Batch B Contract 落地；等待期间显示 unavailable/not-recorded，不实现临时推导。

## 20. 风险与缓解措施

| 风险 | 后果 | 缓解 |
| --- | --- | --- |
| 前端自行推导分数或 action | UI 与后端语义漂移 | 后端记录才展示；不存在的 Consumer Action 明确 not recorded |
| 直接返回 `RetrievalResult` | 泄露完整 chunk 或 metadata | 专用 Safe DTO 与字段 allowlist |
| raw query 持久化 | 泄露面试/内部输入 | POST body、内存态、hash Trace、无 storage/log |
| latency 通过相减估算 | 错误解释并行阶段 | 后端真实计时；缺失显示未记录 |
| Artifact replay 实际重跑 | 结果漂移并产生费用 | replay 与 current rerun 分开模式；缺失 Snapshot 不回填 |
| 历史 Artifact 被包装为五阶段快照 | 展示不存在的历史事实 | 仅 Snapshot V1 支持完整 replay；历史数据标记 partial historical |
| Snapshot 塞入主 metrics Artifact | 文件膨胀、schema 与 UI 耦合 | 独立 sidecar + opaque ref + SHA，按需读取 |
| machine preannotation 显示为正式证据 | 错误 promotion 判断 | identity-first 与 eligibility hard label |
| 当前 25-case 被称为 sealed holdout | 评测治理失真 | 固定标记 historical diagnostic |
| sealed holdout 被 UI 隐藏但 API 暴露 | 数据污染 | 后端无普通访问路径 |
| 逐候选解释复制 reranker 算法 | explanation 与实际排序不一致 | 同一计算路径产生 explanation |
| UI 允许临时改权重 | tuning 不可追溯 | 第一版只读 profile；实验另建版本化流程 |
| Live Inspector 产生未知费用 | 成本与滥用 | 默认关闭、提示、限流、超时、审计计数 |
| capability 被误当成用户授权 | 未授权 principal 访问诊断数据 | capability + 明确定义的 loopback/principal authorization，逐端点校验 |
| 诊断端点进入生产暴露 | 扩大攻击面 | 默认 404；生产部署 fail-closed；不使用模糊 trusted-local 判定 |
| 大量图表弱化治理状态 | 指标被误读 | 表格优先、身份优先、少量必要图表 |
| RAG Console 被理解为上线批准 | 绕过门禁 | 固定显示 formal engine、rollout 与 blockers |

## 21. 执行决策台账

M0A 已冻结的决定：

1. V1 使用严格 actual-client loopback-only；忽略客户端 `X-Forwarded-*` 头，不把前端隐藏视为授权；
2. Live Inspector 只有在独立 capability 开启时才可能调用真实 embedding Provider，并在调用前展示费用边界；
3. Safe excerpt 进行控制字符清洗、空白归一化并限制为 320 characters；
4. Artifact Catalog 只读取显式 allowlisted roots，以 SHA、schema、identity 校验失败即关闭；
5. `artifact_replay` 不调用 Provider；`current_engine_rerun` 属于 Live Inspection，绑定当前 identity，可能产生费用；
6. 普通 Artifact 权限不开放 authored query；Eval → Inspector 只传 Artifact SHA 与 opaque case reference；
7. 历史 25-case machine holdout 仅为 `historical_diagnostic`；新的 sealed holdout 由控制台外部治理；
8. capability disabled 时后端 RAG API 返回 404，页面只允许显示诚实 unavailable 状态；
9. V1 Inspection 同步返回、支持客户端取消、无 TTL/store/GET endpoint；服务端使用进程内、非阻塞、最大并发 2 的 bounded guard，饱和时稳定返回可重试的 `RAG_DIAGNOSTIC_CAPACITY_EXHAUSTED` 429；
10. UI 不提供缺少原始 query 的 Artifact case “当前引擎重跑”按钮；若未来开放，必须依赖独立 authored-query 权限，且结果只在浏览器内比较、不覆盖历史 Artifact。

M0B 已冻结的决定：

1. `RetrievalDiagnosticSnapshotV1` 使用独立 sidecar、opaque ref 与 SHA 校验；
2. Candidate explanation 从实际 ranking/reranking 计算路径产生，不复制算法；
3. Trace latency V3 使用真实阶段计时，Semantic/Lexical 并行耗时不可相加解释 total；
4. No-evidence 四格及比率由后端输出；
5. 没有真实 Consumer Action 策略与持久记录时固定返回 `not_recorded`。

M7 已冻结并完成本地验证的决定：

1. Diagnostic concurrency limiter 使用最小的 application-level bounded guard，只保护整个 Live Diagnostic 请求；不复制 Hybrid coordinator 的 Semantic、Lexical 或 Rerank 内部容量策略；
2. Corpus 当前没有 activation、retirement、embedding、source authority 与 review 的权威生命周期记录，因此相应字段固定为 `not_recorded`，不得发明 `active`；
3. v1.3 最终代码上的相关 Knowledge/RAG、Architecture、Acceptance、Frontend、compileall、build、bundle、冗余和隐私回归已重新执行，未复用 v1.2 旧计数；
4. 受保护 PostgreSQL 已在外部结构化授权 `interview-rag-console-pg-20260814-001` 下通过：receipt SHA、approved fingerprint、database allowlist 与 expiry 均完整，实际目标匹配，节点使用共享 `test_*` owned scope，cleanup receipt 与额外只读复核均证明 `residue_count=0`；该授权不适用于任何其他 PostgreSQL 节点。

### 21.1 原始决策检查表（保留作审计）

以下决策必须在 M0A 冻结：

1. V1 使用 strict loopback-only，还是 authenticated principal + diagnostic permissions；
2. Live Inspector 是否允许调用真实 embedding Provider；
3. Safe excerpt 的来源、长度与脱敏规则；
4. Artifact Catalog 的明确 allowlisted roots 与 supported schemas；
5. Replay/Rerun 的公开错误码、费用提示与 drift identity；
6. machine tuning authored query 是否通过独立 capability/permission 在 UI 中显示；
7. sealed holdout 的 owner、接口与最终聚合公开边界；
8. RAG 顶层导航在 capability disabled 时隐藏还是进入 unavailable；
9. Live Inspection 的超时、并发和取消语义；V1 固定无 TTL/store/GET endpoint；
10. Inspector current rerun 是否允许与旧 Artifact 只在浏览器内并排比较，不持久化临时 Artifact。

以下事项在 M0B 冻结，不阻塞 Phase 0A 通过后的基础 UI：

1. `RetrievalDiagnosticSnapshotV1` schema、sidecar 命名、opaque ref 与 SHA 规则；
2. per-candidate reason code 注册表；
3. latency Trace schema 版本与历史兼容；
4. No-evidence confusion summary 的服务端业务语义；
5. 若真实 Consumer Action 已存在，其 consumer、policy version 与 adapter；不存在则固定 `not_recorded`。

未完成 M0A 前，不开始候选表与 Dashboard 业务实现。M0B 未完成的字段在 UI 中必须诚实降级。

## 22. 最终完成定义

整个计划完成时，系统必须满足：

- RAG Console 能准确展示当前正式引擎与 Candidate；
- Eval Dashboard 能从指标下钻到失败 case；
- Retrieval Inspector 对带 `RetrievalDiagnosticSnapshotV1` 的冻结 Artifact 能定位 Semantic、Lexical、Fusion、Rerank 与 Gate 问题；历史 Artifact 只展示原冻结字段并标记 partial historical；
- Evidence Trace 能展示安全 lineage；
- Corpus 页面只读；
- 前端没有发明任何分数、动作、latency、promotion 或 holdout 状态；
- Live Inspection 同步返回，不建立 V1 服务端 inspection store；
- Consumer Action 未被真实记录时明确显示 `Not recorded / no unified policy`；
- 诊断 API 默认关闭；
- 原始 query 与敏感面试数据不被公开；
- sealed holdout 未被污染；
- Artifact identity、human annotation status 与 evidence eligibility 始终可见；
- 所有诊断 ID、SHA 与 revision 复制完整原值并提供可访问反馈；
- 绿色只表示正式通过，所有状态同时使用文字与符号，不依赖颜色单独传意；
- Artifact detail 的正常与拒绝路径均有 Safe DTO Acceptance；
- 同一个 Full Snapshot 在后端与前端都能证明五阶段、最终 Evidence、Gate、Evidence Decision、latency 与 ranking explanation，且 replay 不调用 Provider；
- 完整测试矩阵通过；
- Legacy 默认继续有效，Hybrid rollout 仍为 0，Shadow/Canary 未被自动启动。

计划完成后的合法状态示例：

```text
Formal engine:       Legacy
Candidate engine:    Hybrid V2 / weighted RRF
Hybrid rollout:      0%
Shadow:              Disabled
Promotion:           Blocked
Primary blockers:    Human GT missing, no-evidence gate failed,
                     sealed holdout missing, business blind A/B pending
```

控制台能够清楚、可验证地解释这个状态，就是本计划的成功；它不需要也不得把尚未通过的 Candidate 包装成绿色上线结果。
