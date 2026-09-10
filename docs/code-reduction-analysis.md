# 代码量缩减分析

本文件记录 Interview-Agent 的代码量缩减结论：哪些已安全删除、哪些需要重构、以及如何验证不影响运行。

## 一、已安全删除（已通过验证，不影响运行）

- 磁盘：`tmp/`、`debug.log`、`tmp-*.log/pid`、`test-results/`、`reports/stage40*`，约 430 MB。
- 脚本：删除 t60–t65、stage38/43/44、memory-shadow/principal-memory 取证、context-compression acceptance 等一次性脚本。
- 测试：删除对应阶段/旧版本测试，以及 `test_evidence_writers.py`、canary 测试等。
- 生产 `app/`：删除 8 个完全无引用的模块（6 个 ports/memory-shadow 观测模块 + v1 eval 的 dataset/metrics）。
- v1 eval 集群：删除 `knowledge_eval_dataset.py`、`knowledge_eval_metrics.py`、`evaluate_knowledge_retrieval.py` 及 v1 测试，并迁移 2 个仍引用 v1 的测试。

## 二、当前规模

| 层 | 初始文件 | 初始非空行 | 当前文件 | 当前非空行 |
| --- | ---: | ---: | ---: | ---: |
| `app/` | 426 | 108,819 | 418 | 108,152 |
| `contracts/` | 15 | 2,395 | 15 | 2,395 |
| `scripts/` | 104 | 31,951 | 52 | 14,926 |
| `tests/` | 476 | 115,637 | 397 | 96,973 |
| **合计** | **1,021** | **258,802** | **882** | **222,446** |

净减：139 个文件、36,356 行非空。

## 三、验证记录

- `python -c "import app.main"` 通过。
- `python -m pytest --collect-only -q` 收集 4225 个测试，无 collection error。
- 相关 eval 测试 26 passed。
- 删除后用 `rg` 复查，无残留 import 引用。

## 四、剩余可删减项（不能直接删，需小步迁移）

| 对象 | 为什么不能直接删 |
| --- | --- |
| `requirements.lock.txt` / `requirements.lock.meta.json` | 被 `compile_requirements_lock.py`、`verify_interview_quality_v1_publication.py` 和 ADR 引用，是兼容别名 |
| 历史文档 `docs/archive/`、`docs/superpowers/plans/` | 非代码，删了不影响运行，但会丢失历史 |

## 五、需要重构（版本兼容链，不能当重复删除）

| 对象 | 结论 |
| --- | --- |
| `knowledge_eval_dataset_v2/v3`、`metrics_v2/v3` | v3 依赖 v2（`as_v2()` 兼容层、`RetrievedKnowledgeItemV2`），合并会改变评估契约，属于重构 |
| `durable_interview_state` v1/v2/v3 | 三者被 `interview_workflow.py`、`runtime.py`、`durable_interview_graph.py` 主动引用，属于不同阶段的状态版本 |
| `evaluator.py` + `evaluator_ext.py` | 双层继承，需合并为单一实现 |
| `interview_graph.py`（旧） vs `durable_interview_graph.py` | 已确认旧图仍被 `OrchestratorAgent` 与测试引用，属于当前编排路径，不能退役 |
| `services/` 分层 | Store→adapters、Runtime→runtime、Provider→infrastructure、Context→`app/context`、Eval→`app/eval` |

## 六、每步重构的验证命令

```powershell
python -c "import app.main"
python -m pytest --collect-only -q
rg -n "<旧模块名>" app scripts tests
```

## 七、验证限制

`旧语料 + v1 loader 迁移` 已完成代码层面：`init_local_runtime` 默认 seeding loader 已切到 `load_knowledge_v2`，v1 loader、`build_knowledge_manifest.py`、旧语料 `app/data/knowledge/` 及 v1/rocketmq 相关测试已删除。`import` 和 `pytest --collect-only` 均通过；但真实 `--seed-knowledge` 写入 pgvector 仍需在具备 PostgreSQL 的环境中做端到端验证。
