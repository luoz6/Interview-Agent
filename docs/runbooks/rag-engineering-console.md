# RAG 本地工程控制台

> 文档类型：How-to。适用于本机单用户学习与技术展示。

## 启用能力

```powershell
$env:RAG_CONSOLE_ENABLED="true"
$env:RAG_LIVE_EXECUTION_ENABLED="true"
$env:RAG_CORPUS_WRITE_ENABLED="false"
```

- `RAG_CONSOLE_ENABLED`：Overview、Evaluation summary、Frozen Replay、Evidence Trace summary、Corpus read；
- `RAG_LIVE_EXECUTION_ENABLED`：Live Inspector、Legacy vs Hybrid Compare；
- `RAG_CORPUS_WRITE_ENABLED`：校验、Re-index 预览和创建新 Corpus 版本。

三项默认关闭，且所有端点强制 loopback。不要通过反向代理头把远程请求伪装成本机。

## 推荐使用顺序

1. Overview 核对当前 engine、Corpus、Embedding 和 profile；
2. Retrieval Inspector 输入问题，先看 Legacy / Fixed Hybrid 双引擎 Compare；
3. 切换到单引擎 Hybrid，分别运行 Fixed 与 Query-aware Fusion；
4. Evaluation 查看冻结指标、case type 和 no-evidence；
5. 从 case 打开 Frozen Replay；
6. Evidence Trace 查看安全证据链；
7. Corpus 仅在明确授权写入时使用。

## Corpus 创建流程

```text
校验并预览
→ 核对当前/目标 version 与 manifest
→ 核对新增/复用 chunk、Embedding 数量和 Provider 身份
→ 单次确认创建新版本
```

单一确认只合并 UI；服务端仍验证 validation SHA、active manifest、target manifest、version uniqueness、内容身份和并发冲突。该操作可能调用外部 Embedding Provider 并启用新版本。

API 层只有 `POST /corpus/drafts/validate` 与 `POST /corpus/versions`；没有独立 `/releases/activate` endpoint。

## 隐私边界

Live query 不进入 URL、localStorage、sessionStorage 或业务 Session。响应不返回原始 query、知识正文、简历、JD、Provider payload 或 chain-of-thought。失败信息使用稳定 reason code，不回显请求正文。

## 查看融合与拒答原因

Inspector 的“融合决策”显示后端记录的 `query_signal`、实际 Semantic / Lexical 权重和 reason codes。`routing_summary` 只表示请求路由事实，`fusion_summary` 表示检索通道完成后的融合决策；界面不会在前端重新分类或计算权重。

单引擎 Hybrid 提供两个受控选项：

- `fixed_weighted_rrf`：默认模式，保持基础权重；
- `query_aware_weighted_rrf`：调用既有确定性 Query Signal Analyzer，再由服务端返回实际信号、权重和原因。

前端不能上传任意权重或 `rrf_k`。Legacy 不消费 Hybrid Fusion mode；Compare 始终保持 Legacy / Fixed Hybrid。业务 Runtime 默认不因 Inspector 选择而变化。响应中的 `requested_hybrid_fusion_mode` 与 `effective_hybrid_fusion_mode` 为合法枚举或 `null`；Legacy 的 `null` 显示为“不适用”，历史 Frozen Replay 的 `null` 显示为“未记录”。

这些结果只证明控制流和契约正确，不代表 Query-aware 的真实指标优于 Fixed RRF。

Evidence 区域显示 candidate-aware sufficiency 结果。候选表仍可用于排错，但当门禁给出 `insufficient` 时，最终 Evidence 为空。Evaluation 的无证据面板进一步显示错误拒答、错误取证 case IDs 和 reason-code breakdown。

## 本地 Closure Audit

在完成代码、前端和文档修改后运行：

```powershell
python scripts/audit_rag_demo_closure.py
```

审计只读取仓库状态，不调用外部 Provider、不写数据库、不创建 Corpus，也不修改工作树。它验证基线祖先关系但不限制当前分支名，因此合并回 `master` 后仍可使用。
