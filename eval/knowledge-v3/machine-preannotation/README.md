# Demo Diagnostic Dataset

> Curated / Machine-assisted；仅用于本地工程诊断，不代表生产结论。

该目录保留 100 条冻结案例及历史兼容 Artifact：

- 75 条 tuning；
- 25 条 final diagnostic；
- 14 个 case type；
- 100 个互不跨 split 的 case family；
- 10 个 expected no-evidence cases。

验证：

```powershell
F:\python3.11\python.exe scripts\validate_knowledge_diagnostic_dataset.py
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py validate
```

`dataset.json` 与 `provenance.json` 由 SHA-256 绑定。Legacy annotation governance 字段只为历史 JSON identity 兼容读取，不属于活动 schema 或活动流程。现有 Artifact 可用于 Legacy、Semantic-only、Lexical-only、weighted RRF 和 rank-normalized 的诊断比较；25 条 final diagnostic 不得用于反复调参。
