# Knowledge Eval V3 诊断指南

> 文档类型：How-to。目标：验证、运行和比较 Demo Diagnostic Dataset。

## 1. 验证冻结数据集

```powershell
F:\python3.11\python.exe scripts\validate_knowledge_diagnostic_dataset.py
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py validate
```

预期：100 cases、75 tuning、25 diagnostic holdout、14 case types、family 不跨 split、Corpus manifest 与 provenance SHA 有效，`production_claim=false`。

## 2. 查看命令

```powershell
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py run --help
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py compare --help
```

`run` 生成不可覆盖的冻结诊断 Artifact；`compare` 比较两个同 Dataset、split、Corpus 和 case order 的 Artifact。活动命令只有 `validate`、`run`、`compare`，不存在 threshold registration 或 promotion 决策。

## 3. 解释结果

同时检查 Recall@5、MRR@5、NDCG@5、Hit@1、filter correctness、routing accuracy、P95 latency、case-type breakdown 和 no-evidence confusion matrix。不要只根据单个 Recall 指标决定算法优劣。

75 条 tuning 可用于权重、路由和 abstention 实验。25 条 final diagnostic 只在方案冻结后运行，并记录 Artifact SHA；不要反复查看后继续调参。

## 4. 运行 tuning ablation

在具有适用 PostgreSQL 读取授权且当前 Corpus identity 与 Dataset 一致时，依次生成不可覆盖的 tuning Artifact：

```powershell
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py run --engine legacy --split tuning --output <legacy.json>
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py run --engine hybrid-v2 --ablation semantic-only --split tuning --output <semantic.json>
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py run --engine hybrid-v2 --ablation lexical-only --split tuning --output <lexical.json>
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py run --engine hybrid-v2 --ablation weighted-rrf --split tuning --output <fixed-rrf.json>
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py run --engine hybrid-v2 --ablation query-aware-weighted-rrf --split tuning --output <query-aware-rrf.json>
F:\python3.11\python.exe scripts\evaluate_knowledge_retrieval_v3.py run --engine hybrid-v2 --ablation rank-normalized-score --split tuning --output <rank-normalized.json>
```

每个 Artifact 会冻结 Dataset、Corpus、Embedding、profile、code revision 和 engine identity。使用 `compare` 分别与 Legacy 比较，不得覆盖已有输出。

如果当前没有适用结构化授权，停在 Dataset 校验、单元/契约测试和已有 Frozen Artifact 兼容验证，并记录：

```text
REAL TUNING RETRIEVAL NOT RUN — AUTHORIZATION REQUIRED
```

不得用测试 double 或机器预标注结论冒充真实 paired evaluation。

## 5. 回放

在 Evaluation 选择 case 后打开 Frozen Replay。新 Snapshot 显示候选、Fusion Summary、Evidence Decision 和流水线安全字段；历史缺失阶段显示 `partial_historical`，不会重新调用检索或 Provider。
