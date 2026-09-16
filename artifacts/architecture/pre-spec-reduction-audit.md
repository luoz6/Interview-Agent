# Pre-Spec Legacy Reduction Audit

> 任务：P0A-T07
> 基线 commit：0af2a8b
> 状态：PASS（审计完成；未修改生产代码）

## 1. Method

本审计基于以下证据：

- `git diff HEAD^ HEAD --name-status` 获取上一 commit 删除的 legacy files。
- P0A-T02 `dependency-edges.json` 检查当前 `app/**/*.py` 是否仍 import 已删除的 app 模块。
- `rg` 检查当前 `app/`、`scripts/`、`tests/`、`docs/` 中的残留引用。
- P0A-T06 architecture test 已暴露 25 个当前文档引用了已删除 script/test path。

## 2. Audit Items

| item_group | deleted examples | production_import | runtime_wiring | integration_dependency | migration_dependency | status | note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| old v1 corpus | `app/data/knowledge/**`, `app/data/knowledge/manifest.json` | CLEARED | CLEARED | NOT_CLEARED | CLEARED | NOT_CLEARED | 当前 app 无 v1 corpus import；migration compatibility 已由 P0C-T02 真实验证。 |
| v1 loader | `scripts/load_knowledge.py`, `scripts/build_knowledge_manifest.py` | CLEARED | CLEARED | NOT_CLEARED | CLEARED | NOT_CLEARED | `init_local_runtime` 已切到 v2 loader；P0C-T02 已真实验证 migration compatibility。 |
| legacy scripts | `stage38_postgres_runtime_acceptance.py`, `memory_*_shadow*.py`, `run_t6*.py`, `knowledge_acceptance*.py` | CLEARED | CLEARED | NOT_CLEARED | CLEARED | NOT_CLEARED | 生产 app 不 import；当前 docs 仍有缺失 script 引用。 |
| old tests | deleted `tests/acceptance/*`, `tests/contracts/*`, `tests/unit/*`, `tests/integration/postgres/*` | CLEARED | CLEARED | NOT_CLEARED | CLEARED | NOT_CLEARED | 当前测试不再 import 已删除 app modules；但文档仍引用 25 个缺失 test paths，导致 architecture test 失败。 |
| deleted ports/services | `app/ports/drafts.py`, `app/ports/prep_plans.py`, `app/ports/memory_metrics.py`, `app/ports/memory_shadow_observability.py`, `app/ports/session_deletion.py`, `app/services/knowledge_eval_dataset.py`, `app/services/knowledge_eval_metrics.py`, `app/services/memory_shadow_observability.py` | CLEARED | CLEARED | CLEARED | CLEARED | CLEARED | 当前 app/tests/scripts 无精确引用；仅历史 plan 文档提及。 |
| obsolete artifacts | `reports/stage40-acceptance/**`, stage acceptance reports/evidence | CLEARED | CLEARED | CLEARED | CLEARED | CLEARED | 当前代码和测试未引用。 |

## 3. Current App Import Evidence

对已删除 app 模块执行精确 target 匹配：

```text
app.data.knowledge
app.ports.drafts
app.ports.memory_metrics
app.ports.memory_shadow_observability
app.ports.prep_plans
app.ports.session_deletion
app.services.knowledge_eval_dataset
app.services.knowledge_eval_metrics
app.services.memory_shadow_observability
```

结果：`exact_count=0`

当前代码只引用 v2/v3 后继模块，例如：

- `app.services.knowledge_eval_dataset_v2`
- `app.services.knowledge_eval_dataset_v3`
- `app.services.knowledge_eval_metrics_v2`
- `app.services.knowledge_eval_metrics_v3`

不引用已删除的 v1 模块。

## 4. Current Document Reference Failures

P0A-T06 的 architecture test 发现以下当前文档仍引用已删除 script/test path。

### 缺失 script modules

| document | missing module |
| --- | --- |
| `docs/long-term-memory-decision-packet.md` | `scripts.long_term_memory_decision_packet` |
| `docs/memory-operational-shadow-acceptance.md` | `scripts.memory_operational_input_evidence` |
| `docs/memory-operational-shadow-acceptance.md` | `scripts.memory_operational_shadow_acceptance` |
| `docs/memory-shadow-restore-drill.md` | `scripts.memory_shadow_restore_drill` |
| `docs/memory-shadow-security-review.md` | `scripts.memory_operational_input_evidence` |
| `docs/memory-shadow-security-review.md` | `scripts.memory_shadow_security_review` |
| `docs/principal-memory-data-use-decision-preflight.md` | `scripts.principal_memory_data_use_preflight` |
| `docs/runbooks/knowledge-rocketmq-v4-preflight.md` | `scripts.knowledge_rocketmq_v4_preflight` |
| `docs/runbooks/knowledge-rocketmq-v4-preflight.md` | `scripts.knowledge_rocketmq_v4_target_preflight` |
| `docs/stage-21-browser-e2e-acceptance.md` | `scripts.stage38_postgres_runtime_acceptance` |

### 缺失 test paths

| document | missing test path |
| --- | --- |
| `docs/adr/user-materials-rag-v1.md` | `tests/acceptance/test_memory_operational_shadow_acceptance.py` |
| `docs/adr/user-materials-rag-v1.md` | `tests/contracts/test_memory_production_shadow_approval_packet.py` |
| `docs/context-compression-optimization-acceptance.md` | `tests/acceptance/test_context_compression_repository_acceptance.py` |
| `docs/context-compression-optimization-acceptance.md` | `tests/acceptance/test_context_compression_shadow_acceptance.py` |
| `docs/context-compression-optimization-acceptance.md` | `tests/acceptance/test_memory_system_optimization_acceptance.py` |
| `docs/interview-agent-memory-system-optimization-spec.md` | `tests/acceptance/test_context_compression_repository_acceptance.py` |
| `docs/interview-agent-memory-system-optimization-spec.md` | `tests/acceptance/test_context_compression_shadow_acceptance.py` |
| `docs/interview-agent-memory-system-optimization-spec.md` | `tests/acceptance/test_memory_system_optimization_acceptance.py` |
| `docs/local-v1-runbook.md` | `tests/contracts/test_memory_publication_evidence.py` |
| `docs/memory-shadow-restore-drill.md` | `tests/contracts/test_memory_shadow_restore_drill.py` |
| `docs/memory-shadow-security-review.md` | `tests/contracts/test_memory_shadow_privacy.py` |
| `docs/memory-shadow-security-review.md` | `tests/unit/test_memory_shadow_fairness.py` |
| `docs/stage-21-browser-e2e-acceptance.md` | `tests/contracts/test_stage38_postgres_api_contract.py` |
| `docs/stage-41-local-v1-release-closure-plan.md` | `tests/contracts/test_stage38_postgres_api_contract.py` |
| `docs/stage-42-knowledge-agent-2.0-plan.md` | `tests/contracts/test_knowledge_manifest.py` |

## 5. Status Summary

- CLEARED: 2 item groups
- NOT_CLEARED: 4 item groups
- UNKNOWN: 0 item groups

## 6. Critical Rule Applied

旧语料 / v1 loader：

```text
migration_dependency = CLEARED
```

P0C-T02 已在真实 PostgreSQL + pgvector 环境完成 V29 → V30 升级、runtime check、preflight 和 existing data read。因此旧语料 / v1 loader 的 migration dependency 现在标记为 CLEARED。

## 7. Limitations

- 本审计不修改任何文档、脚本或测试。
- 对历史 plan/spec 文档的引用未逐一清理；它们是否属于“当前运行依赖”仍需后续文档治理决策。
- 当前架构测试因 25 个文档引用失败；这是本 Audit 应记录的 NOT_CLEARED 事实，而不是本 Task 修复。
