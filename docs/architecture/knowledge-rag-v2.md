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

## Source Scope 与 User Materials 边界

系统 Knowledge Corpus 与 User Materials 是两个生命周期。全局 Corpus 由维护者创建版本并显式激活；本地资料由当前 Principal 上传、选择、停用或删除，不会调用 Corpus create、activate、publish 或 retire。Local V1 的 Principal 由服务端解析，当前产品不引入账号、登录、租户或 RBAC。

Source Scope 是每个现有检索通道的候选约束，不是新的排名通道：

```text
System + frozen selected User candidates → unified Semantic rank
System + frozen selected User candidates → unified Lexical rank
Semantic rank + Lexical rank → existing Fusion → Rerank → Evidence Gate
```

因此 Fusion 始终只有 Semantic 与 Lexical 两个输入，不存在 System/User 第三路 RRF、资料优先权重或越界补齐。资料选择由服务端解析为不可变 Revision 身份并进入 Plan hash；首次 Start 重新验证 owner 与可用性后复制到 Session。成功启动后的恢复和回放使用该冻结绑定，不从当前资料库重建 Scope，也不接受客户端在 Start 临时替换资料列表。

## Citation 与评分隔离

选择资料只表示本次面试允许使用。公共 Citation 只能来自以下交集：业务绑定的 Evidence、通过 Evidence Gate 的 Final Evidence、以及问题/追问/反馈实际消费的 Evidence。被选中但未消费的资料不会产生 Citation。

资料删除或 owner/revision 无法在读取时验证后，持久化报告与 Artifact 不被重写；公共读取统一投影为无安全引用、无标题、无位置、无摘录的“已删除资料”。用户文件内容始终是非权威、不可信上下文，不能修改 rubric、维度、权重、及格线、答题事实或任何数值评分输入。

## 诊断面

控制台提供 Overview、Retrieval Inspector、Evaluation、Evidence Trace 和 Knowledge Corpus。Compare 由服务端用同一个 query、约束、profile 和 corpus identity 并行执行 Legacy 与 Hybrid；它是主动诊断，不是生产 Shadow。

## 数据与可复现性

Corpus source 是知识正文权威来源，数据库 active catalog 是当前版本身份的权威来源，`manifest.json` 是可重建索引。Eval Artifact、Snapshot、Dataset、Corpus、Embedding、profile 和代码树均以 SHA-256 绑定。

Demo Diagnostic Dataset 包含 75 条 tuning 和 25 条 final diagnostic cases。算法只允许在 tuning 上迭代；最终诊断集不用于反复调参，也不构成生产发布证据。

完整组件图与边界见 [rag-demo-architecture.md](rag-demo-architecture.md)。
