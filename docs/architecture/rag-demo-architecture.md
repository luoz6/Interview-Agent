# RAG Demo Architecture

> 文档类型：Explanation。读者：希望理解本项目 RAG 技术链路的开发者。

## 系统边界

```text
Interview consumer
  └─ explicit KnowledgeEngine execution
       ├─ Legacy retrieval
       └─ Hybrid V2
            ├─ semantic channel
            ├─ lexical channel
            ├─ deterministic query signal
            ├─ fixed / query-aware fusion
            └─ candidate-aware rerank
                 ↓
            Candidate-aware Evidence Sufficiency
                 ↓
     BaseEvidenceBundle
       ├─ QuestionEvidenceBinding → Follow-up
       └─ ReviewEvidenceBinding   → Reviewer
```

控制台是同一检索系统的安全观察面，不是第二套运行时。Compare 在服务端共享 query、constraints、profile 和 Corpus identity，并隔离两侧失败。

## Query-aware Fusion

Query Signal Analyzer 在两个检索通道完成后、Fusion 之前运行。它综合精确别名、技术词、缩写、Lexical matched terms、查询长度和中文自然语言比例，输出 `lexical_dominant`、`semantic_dominant` 或 `balanced`。实现是确定性的，不调用 LLM，也不把原始 query 写入决策对象。

`query_aware_fusion=false` 保持固定权重和历史重放语义；实验 profile 显式开启动态权重。实际分类、权重和 reason codes 写入独立 `fusion_summary`，不与请求阶段的 `routing_summary` 混用。

Fusion 后的 Reranker 消费 `RetrievalCandidate`。raw Provider score 只参与最低资格判断；排序以 Fusion score 为基础，再应用确定性 exact-term / routing-tag boost，并使用 Fusion rank 和 chunk ID 决胜。这样 raw score 不会在 Fusion 后静默覆盖 RRF 或 rank-normalized policy。

## Evidence Sufficiency

Hybrid Evidence Gate 消费候选级信号：top1 support、top1/top2 gap、channel agreement、domain/topic agreement、source authority、minimum semantic support 与 exact lexical evidence。明确不足时保留候选诊断但清空最终 Evidence，从而表达主动 abstain。Legacy 和历史路径继续使用 `KnowledgeChunk` 兼容入口。

这些规则是 Demo tuning policy，不是生产阈值。No-evidence confusion matrix、错误 case IDs 和 reason-code breakdown 用于诊断，不能被描述为生产质量证明。

## 身份链

```text
source documents
  → corpus version + manifest SHA
  → embedding provider/model/revision/dimension
  → retrieval profile + engine version
  → Eval Artifact SHA
  → Snapshot SHA
```

任何身份不一致都应 fail closed。数据库 active catalog 是当前版本身份的权威来源；source 文档是正文权威来源；本地 manifest 可在二者一致时确定性恢复。

## 能力边界

Console Read 只读安全摘要。Live Execution 处理用户输入并访问真实检索链路。Corpus Write 可能写入 source、调用 Provider 并启用新版本，因此独立关闭。三者都要求 loopback。

## 评测边界

Demo Diagnostic Dataset 是工程比较工具，不是组织级发布系统。Tuning 可用于算法实验；Final Diagnostic 用于冻结后的最终观察。历史 threshold、formal annotation 和 promotion 字段可兼容解析，但不会影响活动 UI 或决策。
