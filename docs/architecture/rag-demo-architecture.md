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
            ├─ fusion
            └─ rerank
                 ↓
            Evidence Gate
                 ↓
     BaseEvidenceBundle
       ├─ QuestionEvidenceBinding → Follow-up
       └─ ReviewEvidenceBinding   → Reviewer
```

控制台是同一检索系统的安全观察面，不是第二套运行时。Compare 在服务端共享 query、constraints、profile 和 Corpus identity，并隔离两侧失败。

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
