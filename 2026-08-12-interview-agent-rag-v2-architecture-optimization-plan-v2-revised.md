# Interview-Agent RAG V2：Interview Evidence Engine 架构优化与实施计划（修订版）

> 文档类型：Architecture Explanation + Implementation Reference
> 项目：Interview-Agent
> 目标阶段：RAG V2 / Interview Evidence Engine
> 状态：REVISED PROPOSAL
> 适用仓库：`luoz6/Interview-Agent`
> 基线日期：2026-08-12
> 修订日期：2026-08-12
> 替代关系：本文件修订原《Interview-Agent RAG V2：Interview Evidence Engine 与整体知识架构优化计划》；原文件保留为历史提案，不作为实施顺序依据。

---

## 1. 执行摘要

Interview-Agent 的下一阶段不应只追求“检索得更准”，而应建立一套专门服务于面试业务的知识与证据基础设施：

**Interview Evidence Engine**

它负责把以下链路连接成可追踪、可复放、可评测的业务事实：

```text
JD + Resume
    → Role / Skill Understanding
    → Interview Question
    → Question-level Evidence
    → Candidate Answer Gap
    → Evidence-grounded Follow-up
    → Evidence-grounded Review
    → QuestionEvaluationRecord
    → Explainable Final Report
```

本轮保留原方案的目标架构，但调整实施顺序和范围：

1. **评测先于算法。** 在开发 Lexical、RRF 和 Evidence Gate 前，先扩充并冻结能够区分方案优劣的 Eval V3。
2. **结构重构与效果升级分开。** 先交付行为等价的兼容引擎，再单独验证 Hybrid 带来的增益。
3. **证据语义先于业务 Gate。** 先定义最小 Knowledge Unit、Expected Signals 和 Rubric，再判断证据是否足以支持评分。
4. **状态正交化。** 将可用性、充足性、一致性和评价置信度分开表达，不再塞入一个模糊枚举。
5. **以纵向产品切片证明价值。** 在全面扩展 taxonomy 和知识 schema 前，先完成少量主题上的回答缺口识别与自适应追问闭环。
6. **上线治理属于主线。** Shadow、稳定分流、Canary 和真实 Rollback 演练是发布门禁，不是可选增强项。

最终产品验收仍聚焦三个指标：

1. 追问质量；
2. 评分可靠性；
3. 报告质量。

---

## 2. 当前基线与约束

### 2.1 已存在的能力

当前仓库已经具备：

- `KnowledgeAgent`；
- `KnowledgeQuery`；
- `PgVectorKnowledgeStore`；
- pgvector 语义检索；
- metadata、tag、source type 过滤；
- deterministic reranking；
- `PrepContext V2`；
- Question Evidence Binding；
- `content_sha256`；
- `corpus_manifest_sha256`；
- Evidence Replay；
- `KnowledgeBindingResolver`；
- Reviewer Evidence Resolution；
- Knowledge Retrieval Golden Dataset；
- Recall@5、MRR@5、NDCG@5；
- Filter Correctness；
- Evidence Replay Stability；
- P95 Retrieval Latency；
- Shadow、Canary、Rollout 的通用基础能力。

因此，本轮必须以增量演进为原则，不重建一套通用企业 RAG 平台。

### 2.2 经代码核对的现状

当前知识链路存在以下事实：

- `KnowledgeRepository.search()` 输入较窄、返回 `list[Any]`，无法正式表达 trace、降级、候选和证据决策；
- `PgVectorKnowledgeStore.search()` 同时承担 embedding、SQL、向量查询、过滤、候选截断、rerank、minimum score、final top-k 和 trace；
- repository 通过 mutable `last_search_trace` 暴露最近一次搜索信息；
- Question Evidence Binding 主要依赖 tag、domain 和 title term；没有匹配时存在退回首个候选的行为；
- Reviewer 对 PrepContext V2 优先 replay bound evidence，但旧路径仍会执行 `legacy_semantic_search`；
- `QuestionEvaluationRecord` 已有 retrieval path、degraded reason、evidence hash 和 review engine，但尚未正式区分回答质量与评价置信度；
- `KnowledgeRuntimeSettings` 当前主要只有 `minimum_score`，尚未形成统一 profile 配置。

### 2.3 当前语料与评测规模

截至基线日期：

- Legacy corpus：25 chunks；
- 当前 V2 corpus：31 chunks；
- V2 domain：Redis、System Design、FastAPI、RocketMQ、MySQL、PostgreSQL；
- V1 retrieval dataset：30 cases；
- V2 pilot dataset：12 cases；
- memory-p1 V2 dataset：18 cases；
- V2 corpus 已有 aliases，但尚无正式 `technical_terms` 和 `topic` 字段；
- 已有旧基线在 Hit@3、MRR 等指标上接近天花板。

这意味着：现有数据适合做回归保护，但不足以单独证明 Hybrid、Evidence Gate 或 Cross-Encoder 的业务价值。Eval V3 必须先扩充。

---

## 3. 目标、成功条件与非目标

### 3.1 目标

RAG V2 需要完成四类升级：

#### A. Retrieval

```text
Query / Intent / Context
    → Semantic Retrieval
    + Exact Technical-Term / Alias Retrieval
    → Hybrid Fusion
    → Deterministic Rerank
    → Optional Model Rerank
```

#### B. Evidence

```text
Retrieved Candidates
    → Task-specific Evidence Selection
    → Availability / Sufficiency / Consistency Decision
    → Immutable Evidence Binding
    → Replay / Targeted Supplementation
```

#### C. Interview Intelligence

```text
Question + Candidate Answer + Expected Signals + Evidence
    → Mentioned / Missing / Incorrect Signals
    → Adaptive Follow-up
    → Grounded Review
    → Calibrated Confidence
```

#### D. Quality Governance

```text
Offline Eval
    → Ablation
    → Shadow
    → Canary
    → Full Rollout
    → Legacy Retirement
```

### 3.2 成功条件

RAG V2 完成不能只以“新组件已接入”为标准。必须同时证明：

- Hybrid 在独立困难集上优于或不劣于 Legacy；
- Exact term、alias-only 和 hard negative 场景得到可解释改进；
- Question Evidence Binding 的相关性提高；
- Evidence weak、empty 或 unavailable 时，Reviewer 不输出伪高置信度判断；
- Follow-up 更针对候选人的真实回答，并减少泛化、重复和无依据技术断言；
- QuestionEvaluationRecord 到 Final Report 的证据链保持稳定；
- 同一 session 的引擎选择稳定；
- Rollback 经过实际演练。

### 3.3 本轮明确不做

- Elasticsearch 集群；
- Neo4j、Knowledge Graph、Graph RAG；
- Web Search RAG；
- MCP / Skills Retrieval；
- LLM Autonomous Search；
- Agentic Research Loop；
- Multi-Agent Retrieval；
- Multi-model Ensemble；
- 全自动 Taxonomy Generation；
- 全自动知识库扩写；
- 企业多租户知识 ACL。

---

## 4. 固定架构决策

### 4.1 Agent 不拥有检索策略

```text
Agent
    ≠ Retriever
    ≠ Storage Adapter
    ≠ Fusion Policy
    ≠ Ranking Policy
    ≠ Evidence Policy
```

Agent 只声明业务 intent 和必要上下文，并消费受控的 evidence contract。

### 4.2 Adapter 不拥有业务决策

`PgVectorSemanticRetriever` 只负责：

- embedding；
- vector query；
- hard constraints；
- raw candidate loading；
- semantic score；
- semantic rank；
- channel-level latency 和错误。

它不得负责：

- RRF policy；
- Reviewer-specific selection；
- Evidence Gate；
- business final top-k；
- evaluation confidence。

### 4.3 硬约束和软路由信号分离

Hard Constraints 只包含：

- active corpus；
- 明确的 source safety / visibility；
- 调用方明确禁止的 source types；
- 未来可能存在的 tenant / ACL 约束。

以下字段默认作为 routing 或 ranking signal，而不是一律作为 SQL 硬过滤：

- domain；
- topic；
- canonical tags；
- role；
- seniority。

只有调用方明确要求排除其他 domain/topic 时才转为 hard constraint。该决策用于避免 taxonomy 错误在融合前消灭所有候选。

### 4.4 Deterministic reranker 长期保留

它承担：

- 可解释基线；
- compatibility path；
- regression reference；
- Remote Cross-Encoder timeout/error fallback。

### 4.5 先 replay，再补充检索

Reviewer 的顺序固定为：

```text
Existing Question Evidence Binding
    → Replay and Validate
    → Evaluate Task Fit
    → Sufficient: Review
    → Insufficient: Targeted Supplementation
    → New Binding + Review
```

Report 不进行自由检索，只消费 QuestionEvaluationRecord 及其最终证据绑定。

### 4.6 同一 interview 固定引擎

Canary 不得对每个请求随机选择引擎。引擎 assignment 必须：

- 基于稳定的 `session_id` hash；或
- 在创建 interview 时持久化。

一个 interview 从 Prep 到 Report 必须使用同一主引擎配置；Shadow 结果不影响业务事实。

---

## 5. 目标分层架构

```text
Interview Platform / Durable Workflow
                │
       Knowledge-consuming Agents
                │
       Knowledge Application Layer
                │
     Retrieval Orchestrator / Services
        ┌───────┼────────┐
        │       │        │
   Semantic  Lexical  Metadata Router
        │       │        │
        └───────┼────────┘
                │
          Candidate Fusion
                │
      Deterministic Reranking
                │
      Optional Model Reranking
                │
       Evidence Selection / Gate
                │
       Evidence Bundle / Binding
                │
       Prep / Follow-up / Review
```

建议目录逐步演进为：

```text
app/
├── agents/
│   ├── knowledge.py
│   ├── examiner.py
│   ├── shadow_reviewer.py
│   └── report_coach.py
├── application/
│   └── knowledge/
│       ├── retrieval_service.py
│       ├── query_planner.py
│       ├── evidence_service.py
│       ├── binding_service.py
│       └── retrieval_profiles.py
├── domain/
│   └── knowledge/
│       ├── models.py
│       ├── taxonomy.py
│       ├── fusion.py
│       ├── reranking.py
│       ├── evidence_gate.py
│       ├── evidence_selection.py
│       ├── knowledge_unit.py
│       └── scoring.py
├── ports/
│   └── knowledge.py
├── adapters/
│   └── knowledge/
│       ├── pgvector_retriever.py
│       ├── postgres_term_retriever.py
│       ├── deterministic_reranker.py
│       ├── remote_reranker.py
│       └── evidence_repository.py
└── runtime/
    └── config/
        └── knowledge.py
```

本轮不要求一次性移动全部文件。迁移规则是：

```text
建立新边界
    → 新代码进入新边界
    → 旧代码通过 compatibility adapter 接入
    → 按 Release 迁移调用方
    → 通过发布门禁后删除 legacy
```

---

## 6. 核心 Contract 参考

### 6.1 RetrievalIntent

```text
PREP
FOLLOWUP
QUESTION_REVIEW
REPORT_REPAIR
EVAL
SHADOW
```

`SHADOW` 只描述执行模式，不允许写入正式业务证据绑定。

### 6.2 RetrievalRequest

调用方声明业务上下文，不直接散布算法参数：

```text
RetrievalRequest
├── request_id
├── intent
├── query_text
├── role_context
│   ├── role
│   └── seniority
├── topic_context
│   ├── domains[]
│   ├── topics[]
│   └── canonical_tags[]
├── question_context
│   ├── question_id
│   ├── question_text
│   └── focus
├── hard_constraints
│   ├── source_types[]
│   └── filters{}
├── routing_hints{}
├── profile_id
├── session_id
├── prep_run_id
└── parent_bundle_id
```

`candidate_limit`、`fusion_limit`、`rerank_limit` 和 `evidence_limit` 不由一般调用方逐次传入，而由版本化 profile 解析。

### 6.3 ResolvedRetrievalProfile

```text
ResolvedRetrievalProfile
├── profile_id
├── profile_version
├── semantic_enabled
├── lexical_enabled
├── remote_reranker_enabled
├── semantic_candidate_limit
├── lexical_candidate_limit
├── fusion_candidate_limit
├── rerank_candidate_limit
├── evidence_limit
├── rrf_k
├── semantic_weight
├── lexical_weight
├── semantic_timeout_ms
├── lexical_timeout_ms
├── rerank_timeout_ms
└── total_timeout_ms
```

解析后的 profile 必须写入 trace 和 evidence provenance。

### 6.4 RetrievalCandidate

```text
RetrievalCandidate
├── chunk_id
├── title
├── content
├── domain
├── topic
├── source_type
├── tags[]
├── semantic_score / semantic_rank
├── lexical_score / lexical_rank
├── fusion_score / fusion_rank
├── rerank_score / rerank_rank
├── channel_hits[]
├── matched_terms[]
├── content_sha256
├── corpus_manifest_sha256
└── corpus_version
```

Candidate 是内部排序对象，不直接进入公共 API，也不将全部排序内部信息暴露给 Agent prompt。

### 6.5 EvidenceRef

```text
EvidenceRef
├── evidence_id
├── title
├── candidate_summary / safe_excerpt
├── domain
├── topic
├── source_type
├── content_sha256
├── corpus_manifest_sha256
├── corpus_version
├── authority_metadata
└── provenance
```

### 6.6 RetrievalResult

```text
RetrievalResult
├── request_id
├── availability
├── candidates[]
├── selected_evidence[]
├── evidence_decision
├── trace
├── retrieval_engine_version
├── profile_version
├── latency
└── degraded_reasons[]
```

`RetrievalResult` 随本次调用返回。不得要求调用方读取 `repository.last_search_trace`。

### 6.7 EvidenceDecision

证据状态拆成正交维度：

```text
EvidenceDecision
├── availability
│   ├── AVAILABLE
│   ├── DEGRADED
│   └── UNAVAILABLE
├── sufficiency
│   ├── SUFFICIENT
│   ├── WEAK
│   ├── INSUFFICIENT
│   ├── EMPTY
│   └── NOT_EVALUATED
├── consistency
│   ├── CONSISTENT
│   ├── POSSIBLE_CONFLICT
│   ├── CONFIRMED_CONFLICT
│   └── NOT_EVALUATED
├── evaluation_confidence
│   ├── HIGH
│   ├── MEDIUM
│   ├── LOW
│   └── NOT_SCORABLE
├── covered_signals[]
├── missing_signals[]
├── reason_codes[]
└── gate_version
```

语义约束：

- `UNAVAILABLE` 表示系统故障，不能解释为知识库没有证据；
- `EMPTY` 表示系统正常工作，但没有返回可用证据；
- `INSUFFICIENT` 表示存在相关内容，但不足以支持当前任务；
- `CONFLICT` 是一致性维度，不是充足性维度；
- `evaluation_confidence` 表示对评价结论的信心，不等于候选人回答得分。

### 6.8 EvidenceBundle 与派生绑定

基础证据集合和消费者选择分开：

```text
BaseEvidenceBundle
├── bundle_id
├── retrieval_request_id
├── session_id
├── prep_run_id
├── query_hash
├── structured_query_snapshot
├── candidate_evidence_refs[]
├── engine / profile / model / taxonomy versions
├── corpus binding
└── created_at

QuestionEvidenceBinding
├── binding_id
├── bundle_id
├── question_id
├── selected_evidence_ids[]
├── selection_version
├── decision
└── created_at

ReviewEvidenceBinding
├── binding_id
├── parent_question_binding_id
├── replayed_evidence_ids[]
├── supplemental_evidence_ids[]
├── final_evidence_ids[]
├── decision
└── created_at
```

这样可以同时满足：

- Prep 偏 coverage；
- Reviewer 偏 precision；
- Reviewer 必要时可补充检索；
- Report 可复放 Reviewer 当时实际使用的证据；
- 不把 Prep 的 final subset 错当成所有消费者永远必须复用的唯一子集。

### 6.9 QuestionEvaluationRecord V2

在当前字段基础上增加：

```text
answer_quality_score
evaluation_confidence
evidence_availability
evidence_sufficiency
evidence_consistency
evidence_ids[]
gate_reason_codes[]
evidence_binding_id
```

约束：

```text
answer_quality_score ≠ evaluation_confidence
```

示例：

```text
answer_quality_score: 78
evaluation_confidence: LOW
evidence_sufficiency: INSUFFICIENT
reason_codes:
  - insufficient_reference_evidence
```

---

## 7. Minimal Knowledge Unit 与 Evidence Gate

### 7.1 为什么先定义知识语义

`minimum_score` 只能判断相似度是否超过阈值，不能判断这些内容是否足以支持一次面试评价。

证据是否充足取决于任务：同一组 Redis 分布式锁证据，可能足以生成入门问题，却不足以判定候选人达到高级水平。

因此，业务级 Evidence Gate 必须依赖最小 Knowledge Unit 和任务要求。

### 7.2 Minimal Knowledge Unit V2

第一版只要求：

```text
KnowledgeUnit
├── knowledge_unit_id
├── domain
├── topic
├── aliases[]
├── technical_terms[]
├── expected_signals[]
├── failure_modes[]
├── hard_negatives[]
├── weak_answer_signals[]
├── expert_signals[]
├── follow_up_triggers[]
├── evaluation_levels
├── source_references[]
├── review_status
└── schema_version
```

不要求第一版完成全量 taxonomy。先选 2～3 个高价值主题试点，例如：

- Redis Distributed Lock；
- MySQL MVCC / Index；
- RocketMQ Delivery Semantics。

### 7.3 Gate 分层

#### Retrieval Gate

判断：

- 系统是否可用；
- 是否返回有效版本的 evidence；
- evidence 是否满足基本 relevance、hash 和 corpus binding；
- 是否存在明显 hard-negative 风险。

该 Gate 可以在 Minimal Knowledge Unit 完成前交付。

#### Evaluation Support Gate

判断：

- expected signals 覆盖度；
- task relevance；
- evidence authority；
- hard negative；
- 是否足以支持指定 seniority/rubric 的评分；
- 是否需要 targeted supplementation。

该 Gate 必须在 Minimal Knowledge Unit 之后交付。

### 7.4 Conflict 的首版边界

当前语料尚无稳定的 claim、stance 或 contradiction group。第一版允许：

```text
consistency = NOT_EVALUATED
```

只有在 Knowledge Unit 增加人工维护的 claim/stance 或 conflict label 后，才正式启用 `POSSIBLE_CONFLICT` 和 `CONFIRMED_CONFLICT`。不得为了填充枚举，提前引入不可校准的 LLM contradiction judge。

---

## 8. Lexical / Technical-Term Retrieval 设计

### 8.1 第一版目标

解决技术面试中的精确术语和别名召回：

```text
CAS, JMM, AQS, volatile, MVCC, B+Tree, EXPLAIN,
RocketMQ 事务消息回查, Redis AOF, ZGC, G1
```

### 8.2 不将 Lexical V1 等同于通用 PostgreSQL FTS

查询和语料以中文为主。PostgreSQL 默认全文检索不能自动保证中文分词质量。

Lexical V1 应定义为：

```text
Normalized Technical-Term / Alias Retrieval
```

而不是笼统的“对 content 建全文索引”。

### 8.3 Ingestion 要求

在 ingestion 阶段生成并版本化：

```text
aliases[]
technical_terms[]
normalized_title_terms[]
topic
```

规范化规则至少包含：

- Unicode NFKC；
- case folding；
- 全角/半角统一；
- 常见连接符和标点归一化；
- acronym 保留；
- `B+Tree`、`C++` 等符号术语不被错误拆分。

### 8.4 查询和排序

Lexical channel 返回：

- exact term match；
- alias match；
- acronym match；
- matched terms；
- lexical score；
- lexical rank。

通用中文 FTS、`pg_trgm` 或其他分词方案只有在 Eval V3 证明 exact-term channel 不足时再加入。

### 8.5 Hybrid Fusion

初始实现允许以 `RRF k = 60` 作为实验起点，但不得作为长期硬编码结论。

必须在冻结数据集上比较：

- semantic-only；
- lexical-only；
- unweighted RRF；
- weighted RRF；
- rank-normalized score fusion；
- 不同 candidate cutoff；
- hard filter 与 soft routing 策略。

最终参数只能由版本化 Eval Sweep 决定。

---

## 9. Retrieval Profiles

### 9.1 PREP_PROFILE

目标：Coverage 优先。

初始实验范围：

```text
semantic candidates: 15–25
lexical candidates: 15–25
fusion candidates: 10–15
final evidence: 5–8
```

### 9.2 FOLLOWUP_PROFILE

目标：低延迟、高精度、优先使用 bound evidence。

规则：

```text
Bound Evidence Replay
    → Gap Analysis
    → Only if insufficient: Targeted Retrieval
```

### 9.3 QUESTION_REVIEW_PROFILE

目标：Precision、rubric coverage、可复放。

规则：

- 优先 replay QuestionEvidenceBinding；
- 校验 hash、corpus 和 task fit；
- 不足时才补充检索；
- 最终写入 ReviewEvidenceBinding；
- Evidence 不足时降低 evaluation confidence，而不是伪造高置信度。

### 9.4 REPORT_REPAIR_PROFILE

目标：只修复明确的 evidence lineage 或结构问题。

禁止：

- 成为自由搜索 Agent；
- 重新定义 Reviewer 的单题评分；
- 在没有触发条件时扩展检索范围。

---

## 10. 实施路线：Release 0–7

每个 Release 必须可以独立验收、独立回滚，并能明确回答“结构变化”或“效果变化”来自哪里。

### Release 0：Baseline + Eval V3 Foundation

#### 目标

在实现 Hybrid 前建立足以发现差异的评测基线。

#### 工作

- 冻结 corpus version、manifest hash；
- 冻结 embedding provider、model、revision、dimension；
- 冻结 Legacy retrieval config 和代码版本；
- 保存每个 case 的 Legacy candidate IDs、ranks、scores、latency；
- 统一 Eval V3 dataset contract；
- 扩展 case types；
- 建立 paired comparison runner；
- 建立 privacy-safe eval artifact；
- 定义最小 RetrievalTrace contract；
- 将数据划分为 tuning set 与 holdout set。

#### Eval V3 case types

```text
Exact Technical Term
Alias-only Match
Acronym
Semantic Paraphrase
Chinese Paraphrase
Weak Keyword
Multi-topic
Ambiguous
Hard Negative
Out-of-domain
No Evidence
Cross-domain Confusion
Metadata Routing Error
Filter Boundary
```

Conflict cases 在 claim/stance 标注完成前不进入强制 Gate。

#### 数据规模建议

- 初始统一集：至少 80–120 个独立 retrieval cases；
- 每个核心 case type 有足够样本，不能由 1–2 个案例代表；
- 20%–30% 作为 holdout，不用于参数 sweep；
- 新增案例不得只由实现者依据当前算法输出反向标注。

#### Gate

- baseline record 可复现；
- observation completeness = 100%；
- replay stability = 100%；
- dataset 引用的 chunk IDs 全部属于对应 manifest；
- tuning/holdout 划分冻结；
- 没有 Release 0 记录，不得进入 Release 2 算法实验。

### Release 1：Compatibility Retrieval Engine

#### 目标

建立新边界，检索行为保持 Legacy-equivalent。

#### 工作

- 新增 RetrievalIntent、Request、Candidate、Result、Trace；
- 新增 `SemanticRetrieverPort`、`EvidenceLookupPort`、`RetrievalTraceSink`；
- 将 raw pgvector retrieval 与 business rerank 拆开；
- 新建 `KnowledgeRetrievalService`；
- 保留 deterministic reranker；
- 通过 compatibility adapter 接入旧调用方；
- trace 随 RetrievalResult 返回；
- 保留旧 `search()` 作为过渡 facade，但新代码不得依赖 `last_search_trace`。

#### Gate

- 所有旧测试通过；
- Eval V3 的 Legacy subset 指标不下降；
- 逐 case 返回 ID 和排序满足预先定义的兼容容差；
- evidence replay = 100%；
- hash、corpus binding 和 degraded semantics 不变；
- 该 Release 不宣称检索效果提升。

### Release 2：Exact-Term Hybrid MVP

#### 目标

验证 Vector + Exact Term/Alias + Fusion 是否对困难检索场景产生真实增益。

#### 工作

- ingestion 增加 versioned aliases、technical_terms、topic；
- 建立 Postgres technical-term retriever；
- 建立 channel candidate contract；
- 实现 RRF / weighted RRF 实验；
- 引入 PREP、FOLLOWUP、QUESTION_REVIEW profiles；
- hard constraints 与 soft routing 分离；
- 完成 semantic-only、lexical-only、hybrid ablation；
- 支持 lexical failure → semantic 降级，以及 semantic failure → lexical + metadata 降级。

#### Gate

- Holdout Recall@5 不低于 Legacy；
- Holdout MRR@5、NDCG@5 按预注册门槛判断；
- Hit@1 不下降；
- Hard Negative FPR 不高于 Legacy；
- No-Evidence Precision/Recall 不劣化；
- exact-term 和 alias-only 分层指标证明 lexical channel 有独立贡献；
- P95 处于 profile 的绝对和相对预算内；
- 所有参数、版本和 channel contribution 可追踪。

如果 lexical-only 没有独立胜出案例，或 Hybrid 增益不能通过 holdout 验证，不进入全面 rollout；允许保留 compatibility engine 而暂停 Hybrid。

### Release 3：Minimal Evidence Semantics

#### 目标

定义“什么证据足以支持什么任务”。

#### 工作

- 为 2–3 个试点主题建立 Minimal Knowledge Unit；
- 增加 expected signals、weak signals、expert signals、failure modes、hard negatives、rubric；
- 实现 Retrieval Gate；
- 实现 Evaluation Support Gate V1；
- EvidenceDecision 使用正交状态；
- 建立 BaseEvidenceBundle；
- 对 Gate 做 reason-code 和 calibration 数据记录。

#### Gate

- 系统故障与 true no-evidence 可严格区分；
- `UNAVAILABLE` 不映射成 `EMPTY`；
- Gate 对试点主题有人工标注集；
- sufficiency precision/recall 达到预注册门槛；
- low/insufficient evidence 会产生 LOW 或 NOT_SCORABLE；
- Gate V1 为 deterministic，可完整 replay。

### Release 4：Question Binding + Reviewer Integration

#### 目标

统一问题级证据和 Reviewer 的最终评分事实。

#### 工作

- Role-level Candidate Pool；
- Question-level Evidence Selection；
- Candidate Pool 不足时才触发 question-specific retrieval；
- 删除“无相关匹配时默认绑定首个候选”的行为；
- QuestionEvidenceBinding 持久化；
- Reviewer replay-first；
- 必要时 targeted supplementation；
- ReviewEvidenceBinding 持久化；
- QuestionEvaluationRecord 增加 answer quality、confidence 和 evidence 状态；
- Report 只消费 QuestionEvaluationRecord，不重新定义单题评分。

#### Gate

- Question Binding Precision 达到预注册门槛；
- irrelevant fallback binding 显著下降，且不得用“始终不绑定”伪造提升；
- 100% selected evidence 带 chunk ID、content hash 和 corpus binding；
- 100% final review bindings 可 replay；
- Reviewer supplemental retrieval 的触发原因可解释；
- same-input repeated review 的 score variance 不升高；
- no-evidence / unavailable 不输出伪高置信度评分。

### Release 5：Follow-up Evidence Gap Vertical Slice

#### 目标

在少量主题上证明 Evidence Engine 能直接提高追问质量。

#### Pipeline

```text
Question
+ Candidate Answer
+ Bound Evidence
+ Expected Signals
    → Mentioned / Missing / Incorrect Signals
    → Follow-up Trigger Selection
    → Evidence-grounded Follow-up
```

#### 示例

问题：

```text
Redis 分布式锁如何保证安全释放？
```

候选人回答：

```text
设置 expire，业务执行完后 delete。
```

Expected Signals：

```text
owner token
atomic compare-and-delete
lease expiry
fencing token（高级信号）
```

Gap：

```text
mentioned:
- expire
- delete

missing:
- ownership verification
- atomic release
```

追问：

```text
如果锁已经过期并被另一个实例重新获取，旧实例此时直接 delete 会发生什么？
你会如何避免删除不属于自己的锁？
```

#### Gate

通过盲评比较 Legacy 与 V2：

- Answer-specificity 提升；
- Depth Gain 提升；
- Evidence Grounding Rate 提升；
- Repetition Rate 不升高；
- Over-leading Rate 不升高；
- Unsupported Technical Claim Rate 下降或不升高。

首轮先建立可靠 baseline。业务上线阈值必须在查看 holdout 结果前预注册。

如果试点无法证明业务价值，不扩展全量 taxonomy、Knowledge Unit 或 Cross-Encoder。

### Release 6：Shadow、Canary 与 Rollback

#### 目标

以稳定、可观测、可恢复的方式上线。

#### Shadow

```text
Legacy Retrieval ─────────→ Business Result
        │
        └→ Hybrid Shadow → Compare Only
```

保存受控信息：

- legacy/hybrid candidate IDs 和 ranks；
- selected evidence IDs；
- gate differences；
- latency breakdown；
- reason codes；
- engine/profile versions。

Shadow 不得：

- 改变正式 Evidence Binding；
- 改变 Reviewer 或 Report 输出；
- 持久化未经净化的简历、回答和 query 原文。

#### Canary

建议：

```text
0% → 1% → 5% → 20% → 50% → 100%
```

每一档必须满足最小样本、最短观察窗口和错误预算；具体数值在 Release 6 开始前写入 Runbook，不在看到线上结果后临时改变。

#### Rollback

必须演练：

```text
Hybrid Enabled
    → Regression Detected
    → New Sessions Switch to Legacy
    → Existing Session Assignment Remains Interpretable
    → Existing Evidence Binding Remains Replayable
    → Reports Remain Recoverable
```

### Release 7：Data-driven Enhancements / Legacy Retirement

只有数据证明需要时才做：

- Remote Cross-Encoder；
- 完整 Skill / Topic Taxonomy V2；
- 完整 Knowledge Unit Schema V2；
- 中文全文检索或 `pg_trgm`；
- Conflict Detection；
- 更复杂的 fusion/channel weighting。

Legacy Retirement 必须同时满足：

- Offline Eval Passed；
- Ablation 能解释 Hybrid 增益；
- Shadow Passed；
- Canary Passed；
- Evidence Replay Stable；
- No critical regression；
- Rollback exercised；
- runbook、architecture docs 和 compatibility removal plan 已更新。

---

## 11. Eval V3 与业务评测设计

### 11.1 Retrieval 指标

保留：

```text
Recall@5
MRR@5
NDCG@5
Filter Correctness
Excluded Chunk Violation Rate
Vector Validity
Evidence Replay Stability
Observation Completeness
P95 Latency
```

新增：

```text
Hit@1
Hard Negative False Positive Rate
No-Evidence Precision / Recall / F1
Evidence Precision@K
Domain Routing Accuracy
Topic Routing Accuracy
Cross-channel Contribution Rate
Semantic-only Win Rate
Lexical-only Win Rate
Hybrid Win Rate
```

每项必须报告：

```text
Legacy
Candidate Engine
Paired Delta
Case-type Breakdown
Tuning / Holdout Split
```

禁止只报告总体均值而隐藏某个 case type 的显著退化。

### 11.2 Evidence 指标

```text
Question Binding Precision
Evidence Precision@K
Expected-signal Coverage
Irrelevant Fallback Binding Rate
Targeted Supplementation Rate
Sufficiency Precision / Recall
Failure-vs-No-Evidence Confusion Rate
Replay Stability
```

### 11.3 Follow-up 质量

至少评价：

- 是否针对候选人的真实回答；
- 是否抓住真实 missing/incorrect signal；
- 是否比上一问题更深入；
- 是否与岗位和 seniority 相关；
- 是否重复；
- 是否过度引导；
- 是否使用 evidence；
- 是否包含 unsupported technical claim。

### 11.4 Reviewer 质量

至少评价：

- Expert Agreement；
- Score Stability；
- Evidence Support Rate；
- Unsupported Judgment Rate；
- Confidence Calibration；
- No-Evidence Handling；
- System Failure Handling；
- repeated evaluation score variance。

### 11.5 Report 质量

至少评价：

- 是否忠实消费 QuestionEvaluationRecord；
- 是否发生单题分数漂移；
- 是否错误覆盖 Reviewer 判断；
- 是否保留 evidence lineage；
- 建议是否可执行；
- 是否新增无依据技术结论；
- Report Repair 是否只在明确触发条件下执行。

### 11.6 标注协议

业务质量评测必须预先定义：

- 标注人角色和最低专业要求；
- baseline/candidate 随机化和盲评；
- 每个案例的标注人数；
- 分歧处理规则；
- inter-rater agreement；
- 最小样本量；
- 主指标、护栏指标和非劣门槛；
- tuning 与 holdout 隔离；
- 上线门槛的预注册时间点。

建议初始建立 50–100 个 Question + Candidate Answer 业务案例，覆盖：

- 强回答；
- 部分回答；
- 典型错误；
- 误解问题；
- 跳过/无回答；
- 术语堆砌；
- 事实性幻觉；
- 跨领域回答。

---

## 12. Latency、Failure 与 Degradation

### 12.1 延迟预算

每个 profile 同时定义：

- absolute P95 budget；
- 相对 Legacy 的 P95 增量；
- semantic/lexical/rerank timeout；
- total timeout；
- degraded path latency；
- Shadow 计算成本。

不得只使用统一的 `P95 <= 1.5 × Legacy`。PREP、FOLLOWUP 和 REVIEW 的体验目标不同，应有独立预算。

### 12.2 降级链

```text
Semantic unavailable
    → Lexical + Metadata

Lexical unavailable
    → Semantic

Remote reranker unavailable
    → Deterministic reranker

One retrieval channel timeout
    → Remaining channel + degraded reason

All retrieval unavailable
    → availability = UNAVAILABLE
    → evaluation_confidence = NOT_SCORABLE or LOW
```

系统故障不得伪装成 true no-evidence。

### 12.3 Reason Codes

至少包括：

```text
semantic_timeout
lexical_timeout
embedding_provider_error
reranker_timeout
invalid_knowledge_metadata
corpus_manifest_mismatch
evidence_hash_mismatch
no_relevant_candidate
insufficient_signal_coverage
hard_negative_risk
supplemental_retrieval_required
```

---

## 13. Trace、版本与隐私

### 13.1 RetrievalTrace V2

```text
RetrievalTrace
├── request_id
├── intent
├── profile_id / version
├── sanitized query facts
├── hard constraints
├── routing hints
├── semantic channel summary
├── lexical channel summary
├── fusion summary
├── rerank summary
├── evidence decision
├── selected evidence IDs
├── degraded path
├── latency breakdown
└── component versions
```

Trace 主要服务：

- algorithm debug；
- eval；
- shadow compare；
- regression analysis；
- rollback diagnosis。

它不是面向最终用户的完整内部信息展示。

### 13.2 必须记录的版本

```text
corpus_version
content_sha256
corpus_manifest_sha256
embedding_provider
embedding_model
model_revision
retrieval_engine_version
profile_version
fusion_version
reranker_version
evidence_gate_version
taxonomy_version
knowledge_unit_schema_version
```

### 13.3 隐私边界

`RetrievalRequest` 可能包含 JD、简历、问题和候选人回答。持久证据和诊断 trace 必须分开。

默认规则：

#### 持久 EvidenceBundle

- 保存 query hash；
- 保存必要的结构化 intent/topic；
- 保存 evidence IDs、hash、版本和 reason codes；
- 不默认保存完整简历、回答和自由文本 query；
- 跟随 session deletion 和 retention policy。

#### 受控诊断 Trace

- 使用现有 sanitization 机制；
- 短期保留；
- 不进入公共 API；
- Shadow 不保存未经净化的双路原文；
- 禁止记录 provider key、完整简历、完整候选人回答和知识正文。

---

## 14. 配置设计

新增统一 `KnowledgeRuntimeSettings V2`：

```text
engine
hybrid_rollout_percent
assignment_version

semantic_enabled
lexical_enabled
remote_reranker_enabled
evidence_gate_enabled

rrf_k
semantic_weight
lexical_weight

profile_prep
profile_followup
profile_question_review
profile_report_repair

semantic_timeout_ms
lexical_timeout_ms
rerank_timeout_ms

retrieval_engine_version
fusion_version
reranker_version
evidence_gate_version
taxonomy_version
```

要求：

- 环境变量只负责覆盖统一 config model；
- 不允许业务代码直接读取散落环境变量；
- config 解析失败时启动失败，不静默使用危险默认值；
- safe summary 不暴露 key、DSN、完整 URL 或用户内容；
- profile 和 engine assignment 必须可追踪。

---

## 15. 优先级与依赖

### 15.1 P0：形成可上线的 Evidence Engine 主链路

- Release 0：Baseline + Eval V3；
- Release 1：Compatibility Engine；
- Release 2：Exact-Term Hybrid MVP；
- Minimal Knowledge Unit 试点；
- Retrieval/Evaluation Support Gate V1；
- Question-level Binding；
- Reviewer replay-first + targeted supplementation；
- QuestionEvaluationRecord confidence；
- Shadow、稳定 assignment、Canary、Rollback。

### 15.2 P1：数据证明有价值后扩展

- Remote Cross-Encoder；
- 完整 Taxonomy V2；
- 完整 Knowledge Unit V2；
- 中文全文检索；
- Conflict Detection；
- 更复杂 fusion 和 channel weighting；
- 扩展 Follow-up Gap 到更多主题。

### 15.3 P2：收尾与远期能力

- Legacy Retirement；
- 大规模目录搬迁；
- Graph RAG；
- Agentic Retrieval；
- Multi-model Ensemble。

### 15.4 依赖图

```text
Release 0: Baseline + Eval V3
        ↓
Release 1: Compatibility Engine
        ↓
Release 2: Exact-Term Hybrid MVP
        ↓
Minimal Knowledge Unit Pilot
        ↓
Release 3: Evidence Semantics / Gate
        ↓
Release 4: Question Binding + Reviewer
        ↓
Release 5: Follow-up Vertical Slice
        ↓
Release 6: Shadow / Canary / Rollback
        ↓
Release 7: Data-driven Enhancements / Retirement
```

Release 0 和 Minimal Knowledge Unit 的内容准备可以部分并行，但 Gate 实现不得早于其语义 contract。

---

## 16. 原 RAG-00～RAG-20 映射

| 原阶段 | 原内容 | 修订后位置 | 变化 |
|---|---|---|---|
| RAG-00 | Legacy Baseline | Release 0 | 扩展为可复现的逐 case baseline |
| RAG-01 | Retrieval Domain Contract | Release 1 | 保留，Request 参数改由 profile 解析 |
| RAG-02 | Storage / Retrieval Policy | Release 1 | 保留 |
| RAG-03 | Compatibility Engine | Release 1 | 保留，明确不追求效果提升 |
| RAG-04 | Lexical Retriever | Release 2 | 改为 Exact Term/Alias V1，先不承诺通用中文 FTS |
| RAG-05 | RRF | Release 2 | 增加 weighted RRF、ablation 和 filter policy 比较 |
| RAG-06 | Retrieval Profiles | Release 2 | 参数集中在 versioned profile |
| RAG-07 | Evidence Gate | Release 3 | 拆为 Retrieval Gate 和 Evaluation Support Gate |
| RAG-08 | Question Evidence Pipeline | Release 4 | 删除无匹配时默认绑定首候选 |
| RAG-09 | Reviewer Retrieval | Release 4 | replay-first，必要时补充检索 |
| RAG-10 | EvidenceBundle + Replay | Release 3–4 | 拆 Base Bundle、Question Binding、Review Binding |
| RAG-11 | Retrieval Trace V2 | Release 0–1 | 最小 contract 前移，逐步扩展 |
| RAG-12 | Eval V3 | Release 0 | 从后期治理前移为所有算法开发前置条件 |
| RAG-13 | Hybrid Shadow | Release 6 | 保留，增加隐私与稳定 assignment |
| RAG-14 | Cross-Encoder | Release 7 / P1 | 继续后置，以数据决定是否实施 |
| RAG-15 | Follow-up Evidence Gap | Release 5 | 作为早期业务价值纵向切片前移 |
| RAG-16 | Reviewer Confidence | Release 3–4 | 与 EvidenceDecision 和 QuestionEvaluationRecord 联动 |
| RAG-17 | Taxonomy V2 | Release 7 / P1 | 全量后置；试点 topic 可先定义 |
| RAG-18 | Knowledge Unit V2 | Release 3 + Release 7 | 最小 schema 前移，完整 schema 后置 |
| RAG-19 | Canary / Rollback | Release 6 / P0 | 调整为上线主线门禁 |
| RAG-20 | Legacy Retirement | Release 7 / P2 | 保留 |

---

## 17. 最终验收标准

### 17.1 Retrieval

必须在 holdout 上报告并通过预注册门槛：

```text
Recall@5
MRR@5
NDCG@5
Hit@1
Hard Negative FPR
No-Evidence Precision / Recall / F1
P95 latency by profile
```

最低护栏：

- Recall@5 不低于正式 Legacy baseline；
- Hit@1 不下降；
- Hard Negative FPR 不高于 Legacy；
- Excluded Violation Rate 不升高；
- Replay Stability = 100%；
- Observation Completeness = 100%；
- latency 同时满足绝对和相对预算。

MRR@5、NDCG@5 和 No-Evidence 的具体提升门槛，在 Release 0 冻结数据后、查看 Hybrid holdout 结果前预注册。

### 17.2 Evidence

- 100% selected evidence 带 `chunk_id`；
- 100% authoritative evidence 带 `content_sha256`；
- 100% evidence 带 corpus binding；
- 100% final review binding 可 replay；
- 不存在静默 version mismatch；
- `UNAVAILABLE`、`EMPTY`、`INSUFFICIENT` 不混淆；
- Question Binding Precision 和 Sufficiency calibration 达到预注册门槛。

### 17.3 Follow-up

必须通过盲评证明：

- Answer-specificity 提升；
- Depth Gain 提升；
- Evidence Grounding Rate 提升；
- Repetition Rate 不升高；
- Over-leading Rate 不升高；
- Unsupported Technical Claim Rate 下降或不升高。

### 17.4 Reviewer

- answer quality 与 evaluation confidence 独立；
- Score Stability 提升或不下降；
- Expert Agreement 提升或不下降；
- Evidence Support Rate 提升；
- Unsupported Judgment Rate 下降；
- weak/insufficient evidence 产生 LOW confidence；
- unavailable 场景不惩罚候选人；
- no-evidence 场景不伪装高可信评分。

### 17.5 Report

```text
QuestionEvaluationRecord
    → Final Report
```

过程中：

- 单题分数不得漂移；
- Evidence lineage 可追踪；
- ReportCoach 不得重新定义 Reviewer 单题评分；
- Report Repair 必须有明确触发条件；
- Report Repair 不得成为自由搜索 Agent；
- 报告不得新增未被 QuestionEvaluationRecord 或 evidence 支持的技术结论。

### 17.6 架构

代码层必须看到明确边界：

```text
Agent
    → Knowledge Application
    → Knowledge Domain
    → Ports
    → Adapters
```

并且 PgVector adapter 不得持有：

- RRF policy；
- Evidence Gate policy；
- business-specific final ranking；
- Reviewer-specific selection policy。

### 17.7 发布与回滚

- Shadow 不影响正式业务事实；
- 同一 session 的 engine assignment 稳定；
- Canary 每档满足观察门槛；
- rollback 经过真实演练；
-切回 Legacy 后，已有 evidence binding 和 report 仍可解释、可恢复；
- Legacy 删除前 compatibility adapter 仍可用。

---

## 18. 建议的首批 Backlog

为避免再次形成大版本，建议先只创建以下首批任务：

### Backlog A：Release 0

1. 定义 `KnowledgeRetrievalCaseV3` 与 dataset manifest；
2. 建立 Legacy per-case baseline artifact；
3. 增加 exact-term、alias-only、hard-negative、no-evidence cases；
4. 建立 tuning/holdout split；
5. 新增 paired comparison 和 case-type breakdown；
6. 定义 privacy-safe `RetrievalTrace` 最小 schema；
7. 生成 Release 0 baseline record 和门禁文档。

### Backlog B：Release 1

1. 定义 RetrievalIntent / Request / Candidate / Result；
2. 建立 `SemanticRetrieverPort`；
3. 从 pgvector adapter 拆出 deterministic rerank；
4. 新建 legacy-equivalent `KnowledgeRetrievalService`；
5. 建立 compatibility facade；
6. 替换新代码对 `last_search_trace` 的依赖；
7. 运行逐 case compatibility gate。

Release 0 未完成前，可以设计 Release 1 contract，但不开始 RRF 参数或 Hybrid 效果调优。

---

## 19. 最终产品价值链

RAG V2 的目标不是增加组件数量，而是形成：

```text
JD + Resume
    → Role / Skill Understanding
    → Evidence-grounded Interview Plan
    → Evidence-aware Examiner
    → Answer-specific Adaptive Follow-up
    → Evidence-grounded Reviewer
    → Calibrated Question Evaluation Record
    → Report Coach
    → Actionable and Explainable Final Report
```

最终不以“搜索框更强”为验收标准，而以以下结果为准：

```text
问得更准
追得更深
评得更稳
置信度更诚实
解释得更清楚
```

---

## 20. 最终结论

Interview-Agent 已经具备 pgvector retrieval、corpus versioning、evidence binding、evidence replay、Golden Eval、durable workflow 和发布治理基础。下一阶段应沿以下主线推进：

```text
Eval-first
    → Compatibility Architecture
    → Exact-Term Hybrid Retrieval
    → Minimal Evidence Semantics
    → Question / Reviewer Unification
    → Follow-up Business Proof
    → Shadow / Canary / Rollback
    → Data-driven Enhancements
```

只有当每一阶段能通过独立数据说明其价值时，才将新增复杂度保留在正式系统中。

这份修订计划的核心原则是：

```text
先证明问题和指标
再增加算法

先定义证据语义
再判断证据是否充足

先完成小范围业务闭环
再扩展全量知识结构

先保证可回放和可回滚
再删除 Legacy
```
