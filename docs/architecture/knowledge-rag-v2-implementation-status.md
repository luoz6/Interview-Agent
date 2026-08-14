# Knowledge RAG V2 当前实现状态

> 文档类型：Reference。更新时间：2026-08-14。

## 已实现

- 显式 `legacy` / `hybrid-v2` 运行模型和单次安全降级；
- Semantic、Lexical、weighted RRF、rank-normalized 对照路径；
- deterministic Query Signal、fixed / query-aware weighted RRF；
- candidate-aware rerank、Evidence Sufficiency、Evidence Binding 与安全 trace；
- 单请求 Legacy vs Hybrid Compare；
- 版本化 Corpus、Embedding 复用、manifest 对账和失败后幂等恢复；
- 100 条 Demo Diagnostic Dataset：75 tuning、25 final diagnostic、14 case types；
- 冻结 Artifact、带 Fusion Summary 的 Snapshot、partial historical replay；
- no-evidence confusion matrix、失败 case IDs 和 reason-code breakdown；
- 三层本地能力开关：Console Read、Live Execution、Corpus Write。

## 当前事实

- 当前默认业务引擎仍是 Legacy；
- 现有机器辅助诊断不能证明 Hybrid 已整体优于 Legacy；
- 历史 Artifact 中 Legacy、Semantic 和 RRF 的 no-evidence F1 仍为 0；
- Query-aware Hybrid 与 candidate-aware evidence sufficiency 已实现并通过本地确定性测试；
- `query_aware_fusion` 默认保持关闭，尚未用新的真实 75 tuning paired Artifact 证明应切换活动 Demo profile；
- 25 条 final diagnostic 尚未用于本轮调参。

## 已移出活动实现

Production rollout assignment、Knowledge RAG Shadow、Promotion gate、threshold registration、formal annotation authoring、business blind A/B 和 evidence calibration governance 已从活动路径删除。简化前实现保存在 Git tag `archive/rag-production-governance-v1`。

Memory 子系统或 LangGraph 文档中的 Shadow / rollout 属于其他机制，不在本次删除范围。

## 验证边界

受影响 Knowledge/RAG 矩阵、前端测试、ESLint、production build、bundle budget 和 lazy-route 检查可在本地执行。受保护 PostgreSQL 测试、Corpus 写入及外部 Embedding 调用必须有适用的结构化授权；无授权时不执行，也不把它们描述为已通过。

本轮没有适用的新 PostgreSQL / Provider 授权，因此真实 75 tuning retrieval、Corpus 创建和 SiliconFlow 调用保持未执行。当前证据只证明实现与无外部副作用的本地验证完成，不证明真实检索指标已经提升。
