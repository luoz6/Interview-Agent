# Knowledge RAG V2 架构说明

> 文档类型：Explanation。当前定位：Learning Project / Technical Showcase。

Knowledge RAG V2 用一条可解释的本地链路支持面试问题生成、追问和复盘。它保留语义检索、词法检索、融合、重排、Evidence Gate、证据绑定与冻结回放，但不包含生产 Shadow、Canary、Promotion 或 Legacy 退役流程。

## 运行主链

```text
Query
  → hard constraints / routing hints
  → Semantic + Lexical retrieval
  → fusion
  → deterministic / optional remote rerank
  → Evidence Gate
  → Evidence Binding
  → Reviewer / Follow-up
```

业务运行只接受显式引擎：`legacy` 或 `hybrid-v2`。Hybrid 技术失败或不可用时最多降级一次 Legacy；正常 no-evidence 不触发降级。运行 trace 只保留安全字段，不保存原始 query 或知识正文。

## 诊断面

控制台提供 Overview、Retrieval Inspector、Evaluation、Evidence Trace 和 Knowledge Corpus。Compare 由服务端用同一个 query、约束、profile 和 corpus identity 并行执行 Legacy 与 Hybrid；它是主动诊断，不是生产 Shadow。

## 数据与可复现性

Corpus source 是知识正文权威来源，数据库 active catalog 是当前版本身份的权威来源，`manifest.json` 是可重建索引。Eval Artifact、Snapshot、Dataset、Corpus、Embedding、profile 和代码树均以 SHA-256 绑定。

Demo Diagnostic Dataset 包含 75 条 tuning 和 25 条 final diagnostic cases。算法只允许在 tuning 上迭代；最终诊断集不用于反复调参，也不构成生产发布证据。

完整组件图与边界见 [rag-demo-architecture.md](rag-demo-architecture.md)。
