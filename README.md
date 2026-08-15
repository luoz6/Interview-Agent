# Interview Agent

## 项目简介

Interview Agent 是一个面向本地单用户的技术面试助手，可生成面试计划、运行交互式模拟面试、生成带 RAG 证据的评估报告，并导出 PDF。

本仓库定位为 **Learning Project / Technical Showcase**。它不是面向公网的多租户产品，也不声明已达到生产可用或算法质量最优。

当前文档入口：

- [Local V1 运行手册](docs/local-v1-runbook.md)
- [RAG Demo 架构](docs/architecture/rag-demo-architecture.md)
- [Knowledge RAG 实现状态](docs/architecture/knowledge-rag-v2-implementation-status.md)
- [RAG 工程控制台指南](docs/runbooks/rag-engineering-console.md)
- [Eval V3 诊断指南](docs/runbooks/knowledge-eval-v3.md)
- [五场景 RAG 演示脚本](docs/demo/rag-demo-script.md)

## 当前架构

```text
React / Vite
    ↓ HTTP / SSE
FastAPI application
    ├─ interview and report workflows
    ├─ PostgreSQL runtime persistence
    ├─ report worker
    └─ Knowledge RAG
         ├─ Legacy retrieval
         └─ Hybrid V2
              ├─ semantic retrieval
              ├─ lexical retrieval
              ├─ weighted RRF
              ├─ deterministic rerank
              └─ candidate-aware evidence sufficiency
```

业务面试流程的默认检索引擎保持不变。RAG 工程控制台是独立、仅限 loopback 的诊断面，不会静默改变 Interview Runtime 行为。

## 核心功能

- 通过 OpenAI-compatible Provider 生成面试计划；
- 支持恢复状态的交互式面试会话；
- 生成逐题证据、评分与最终面试报告；
- 使用 PostgreSQL 持久化会话、报告任务和报告；
- 可选的 pgvector Knowledge Retrieval；
- 独立的 React/Vite 前端和 FastAPI API；
- PDF 报告下载；
- 带安全边界的本地 RAG 工程控制台。

## RAG Highlights

当前 Knowledge RAG 路径包含：

- 显式的 Legacy 与 Hybrid V2 执行；
- Semantic + Lexical 双通道检索与 Weighted RRF；
- 默认的 Fixed Weighted RRF；
- Inspector 可选的 Query-aware Weighted RRF 确定性实验模式；
- deterministic candidate rerank；
- candidate-aware Evidence Sufficiency 与 abstention；
- 隐私安全的 Legacy / Fixed Hybrid Compare；
- diagnostic evaluation 与 no-evidence analysis；
- 不调用实时 Retriever 或外部 Provider 的 Frozen Replay；
- versioned Corpus 与 manifest identity。

在单次 Hybrid Inspector 请求中，维护者可以选择“固定权重 RRF”或“查询感知 RRF”。实际生效的 Profile、Query Signal、权重和 Reason Codes 始终由服务端返回；Compare 固定为 Legacy / Fixed Hybrid 两路比较，业务 Runtime 默认值不变。

Query-aware 的作用是展示可复现的查询感知融合路径，不代表它优于 Fixed RRF，也不代表 Hybrid 优于 Legacy。

## 5–10 分钟 Demo

1. 打开 `http://127.0.0.1:5173/prep`，生成一份面试计划。
2. 完成一轮短面试，等待报告生成并查看逐题评价与最终报告。
3. 打开 `/rag`，查看当前引擎、Corpus identity、Capability 和诊断数据集状态。
4. 打开 `/rag/retrieval`：
   - 运行默认的 Legacy / Hybrid Compare；
   - 切换到 Single Hybrid；
   - 分别选择 Fixed Weighted RRF 与 Query-aware Weighted RRF；
   - 查看服务端返回的 Query Signal、实际权重、Reason Codes 与候选证据。
5. 打开 `/rag/evaluation`，选择一个案例进行 Frozen Replay。Replay 只需要 Console Read，不运行实时检索，也不调用外部 Provider。

这是一条技术演示路径，不是算法质量认证或生产发布审批。

## Quick Start

### 前置条件

- Python 3.11；
- Node.js 20 或 22 LTS（仓库约束为 `>=20 <23`）；
- 持久化运行模式需要 PostgreSQL、pgvector 和本地 `interview` 数据库；
- Provider、数据库和 Redis 等凭据仅通过本地进程或用户环境提供。

### 安装依赖

在干净的 Python 3.11 环境中执行：

```powershell
python -m scripts.reproducibility_preflight --python-only
python -m pip install --require-hashes -r requirements-windows.lock.txt
npm ci
npm --prefix frontend ci
```

Ubuntu 24.04 使用 `requirements-linux.lock.txt`，不要使用 Windows lock。

### 配置运行模式

`.env.example` 提供无外部持久化依赖的 `preview` 默认值，以及完整 `durable` Profile 的配置说明。不要提交真实凭据，也不要在日志或截图中显示密钥。

最小 Provider 配置：

```powershell
$env:OPENAI_API_KEY="<provider-key>"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

即使使用 DeepSeek-compatible Provider，配置名仍为 `OPENAI_API_KEY`。

启用 PostgreSQL 持久化 Profile 时，应一次性配置完整组合，不要混用 `preview` 与 `durable` 的单项值：

```powershell
$env:POSTGRES_DSN="postgresql://<user>:<password>@127.0.0.1:5432/interview"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:REPORT_RUNTIME_PROFILE="durable"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:REPORT_JOB_STORE="postgres"
$env:REPORT_WORKER="external_process"
$env:KNOWLEDGE_STORE="pgvector"
$env:EMBEDDING_PROVIDER="siliconflow"
$env:SILICONFLOW_API_KEY="<embedding-provider-key>"
python -m scripts.init_local_runtime --check
```

### 启动

启动 API：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

在第二个终端启动前端：

```powershell
npm run dev:frontend
```

当使用 `durable + external_process` Profile 时，在第三个终端启动报告 Worker：

```powershell
python -m app.services.report_worker
```

然后打开 `http://127.0.0.1:5173/prep`。Vite 开发服务器通过 `/api` 代理访问 `127.0.0.1:8000`。

### 加载 Knowledge Corpus

版本化知识加载会写 PostgreSQL，并可能调用外部 Embedding Provider、产生费用。只有在目标数据库、Corpus version 和 Provider 调用获得明确授权后才能执行：

```powershell
$env:EMBEDDING_PROVIDER="siliconflow"
$env:EMBEDDING_MODEL_NAME="BAAI/bge-m3"
$env:EMBEDDING_MODEL_REVISION="<fixed-revision>"
$env:SILICONFLOW_API_KEY="<embedding-provider-key>"
python -m scripts.load_knowledge_v2 --corpus-version memory-p1-zh-v4
```

当前中文语料根目录为 `app/data/knowledge_v2/`，manifest identity 为 `memory-p1-zh-v4`。加载失败时不得把不完整版本激活为当前 Corpus；操作细节以适用的 Knowledge Runbook 为准。

## 配置与安全边界

RAG 控制台能力默认关闭，并强制 loopback：

```powershell
$env:RAG_CONSOLE_ENABLED="true"
$env:RAG_LIVE_EXECUTION_ENABLED="false"
$env:RAG_CORPUS_WRITE_ENABLED="false"
```

- `RAG_CONSOLE_ENABLED`：Overview、Evaluation、Frozen Replay、Evidence Trace 和 Corpus read；
- `RAG_LIVE_EXECUTION_ENABLED`：Live Inspector 与 Legacy / Hybrid Compare；
- `RAG_CORPUS_WRITE_ENABLED`：Corpus validation 和 create-version。

Frozen Replay 只需要 Console Read。它读取不可变 Artifact 或 Snapshot，不执行实时检索，并返回 `provider_call_possible=false`。

Corpus create-version 可能写数据并调用外部 Embedding Provider。必须先取得适用授权并遵循 [RAG 工程控制台指南](docs/runbooks/rag-engineering-console.md)。当前没有独立的 `/releases/activate` API。

## 测试

使用锁定依赖运行仓库测试与前端检查：

```powershell
python -m pytest -q
npm --prefix frontend test -- --run
npm --prefix frontend run check
npm run build:frontend
```

Focused RAG 验证和 closure audit 命令见 [RAG 工程控制台指南](docs/runbooks/rag-engineering-console.md)。

常规本地测试不授权或暗示以下操作：

- 真实 PostgreSQL paired evaluation；
- 外部 Embedding Provider 调用；
- 创建新的 Corpus version；
- Production Shadow、Canary、Promotion 或 rollout。

## 当前限制

- 没有登录或多用户账号隔离；
- 没有公网部署安全设计；
- 没有跨设备同步；
- 不声明 Query-aware 优于 Fixed RRF；
- 不声明 Hybrid 优于 Legacy；
- 本次 closure 不包含新 Ground Truth、blind A/B、threshold calibration 或 no-evidence tuning；
- 不包含 Remote Reranker、Cross-Encoder、GraphRAG、Production Shadow、Canary、Promotion 或 Legacy retirement；
- 不支持三路或四路 Compare；
- 本地完成流程不会自动调用外部 Provider。

## 历史归档

Stage-by-stage 实现记录、早期 Release Candidate、历史 Canary / Shadow 表述和以往 production-governance 实验均已移出当前入口：

- [归档边界与索引](docs/archive/README.md)
- [开发历史正文](docs/archive/development-history.md)
- [Implementation Plan Archive](docs/superpowers/plans/README.md)
- [Design Specification Archive](docs/superpowers/specs/README.md)

归档材料只用于解释演进过程，不描述当前 Runtime，也不构成执行、发布或生产授权。
