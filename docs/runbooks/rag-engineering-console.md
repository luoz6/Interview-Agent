# RAG 本地工程控制台

> 文档类型：How-to。适用于本机单用户学习与技术展示。

## 启用能力

```powershell
$env:RAG_CONSOLE_ENABLED="true"
$env:RAG_LIVE_EXECUTION_ENABLED="true"
$env:RAG_CORPUS_WRITE_ENABLED="false"
```

- `RAG_CONSOLE_ENABLED`：Overview、Evaluation summary、Evidence Trace summary、Corpus read；
- `RAG_LIVE_EXECUTION_ENABLED`：Inspector、Legacy vs Hybrid Compare、Frozen Replay；
- `RAG_CORPUS_WRITE_ENABLED`：校验、Re-index 预览和创建新 Corpus 版本。

三项默认关闭，且所有端点强制 loopback。不要通过反向代理头把远程请求伪装成本机。

## 推荐使用顺序

1. Overview 核对当前 engine、Corpus、Embedding 和 profile；
2. Retrieval Inspector 输入问题，先看双引擎 Compare；
3. Evaluation 查看冻结指标、case type 和 no-evidence；
4. 从 case 打开 Frozen Replay；
5. Evidence Trace 查看安全证据链；
6. Corpus 仅在明确授权写入时使用。

## Corpus 创建流程

```text
校验并预览
→ 核对当前/目标 version 与 manifest
→ 核对新增/复用 chunk、Embedding 数量和 Provider 身份
→ 单次确认创建新版本
```

单一确认只合并 UI；服务端仍验证 validation SHA、active manifest、target manifest、version uniqueness、内容身份和并发冲突。该操作可能调用外部 Embedding Provider 并启用新版本。

## 隐私边界

Live query 不进入 URL、localStorage、sessionStorage 或业务 Session。响应不返回原始 query、知识正文、简历、JD、Provider payload 或 chain-of-thought。失败信息使用稳定 reason code，不回显请求正文。
