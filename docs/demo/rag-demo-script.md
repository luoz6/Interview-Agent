# RAG 技术演示脚本

> 文档类型：Tutorial。目标：用 5 个固定场景在 10–15 分钟内展示可解释检索。

## 准备

启用 `RAG_CONSOLE_ENABLED=true` 和 `RAG_LIVE_EXECUTION_ENABLED=true`，保持 `RAG_CORPUS_WRITE_ENABLED=false`。先在 Overview 核对 Legacy、`memory-p1-zh-v4`、31 chunks 和 BAAI/bge-m3 身份。

## 场景 1：Alias Query

在 Inspector 输入：“面试官提到 Redis 锁时具体指什么问题，应怎样设计处理方案并证明它有效？”比较 lexical 命中、semantic 命中、fusion rank 和最终 Evidence，说明别名/精确技术词为何受益于 lexical 信号。

## 场景 2：Semantic Paraphrase

输入：“消息重复到达并导致业务副作用重复执行，请给出根因链、控制影响的方法以及恢复后的验证证据。”观察 semantic channel 是否比弱关键词更稳定，并比较 Legacy 与 Hybrid 的 Top-5 overlap。

## 场景 3：No Evidence

输入：“现有语料是否定义量子纠错码的稳定子测量与解码流程？”检查 Evidence Gate 是否 abstain，并明确说明当前 no-evidence F1 仍是已知缺口，不把错误返回证据包装成成功。

## 场景 4：Eval → Frozen Replay

打开 Evaluation，选择 `kev3-tuning-002` 或其他 tuning case，查看指标、case type 和 confusion matrix，再进入 Frozen Replay。强调回放不重新执行检索、不调用 Provider、不暴露原始正文。

## 场景 5：Corpus Add → Embedding Reuse

仅在具有适用结构化授权时开启 `RAG_CORPUS_WRITE_ENABLED=true`。填写一条 RocketMQ 中文资料，点击“校验并预览”，展示当前/目标 manifest、31 条复用向量和 1 条预计新增向量；获得成本与版本启用确认后再点击“创建新版本”。无授权时停在预览说明，不执行创建。
