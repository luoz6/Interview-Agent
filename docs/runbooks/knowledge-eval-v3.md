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

## 4. 回放

在 Evaluation 选择 case 后打开 Frozen Replay。完整 Snapshot 显示候选和流水线安全字段；历史缺失阶段显示 `partial_historical`，不会重新调用检索或 Provider。
