# 当前架构目录基线（Current Architecture Baseline）

> 任务：P0A-T01
> 状态：PASS
> 基线 commit：0af2a8b（master）
> 生成方式：只读扫描 `app/`、`contracts/`、`eval/`、`tests/architecture`、`tests/contracts`。未移动、修改或删除任何生产代码。

## 0. 结论

当前仓库是 **brownfield / 半成品分层工程**：

- 已经存在 `domain/application/ports/adapters/runtime` 的目标分层骨架。
- 但生产实现仍大量集中在 `app/services`，且各层之间存在违反目标边界的 import。
- 目录本身不能证明架构约束成立；真实依赖边界需要 P0A-T02 的 import 扫描确认。

## 1. 当前目录事实

| 路径 | 当前存在 | Python 文件数 | 近似 LOC | 当前职责推测 |
| --- | --- | ---: | ---: | --- |
| `app/domain` | 是 | 28 | 3,932 | 领域模型与部分纯规则；按 interview/knowledge/memory/context 初步切分 |
| `app/application` | 是 | 18 | 3,997 | 面向用例的编排，但目前仍与 `services` 混用，不完全纯净 |
| `app/ports` | 是 | 15 | 944 | Protocol/Port 定义，已覆盖 interview、knowledge、unit of work、runtime 等 |
| `app/adapters` | 是 | 40 | 9,800 | 技术实现：Postgres、pgvector、memory、reliability；但未覆盖全部 Postgres 实现 |
| `app/runtime` | 是 | 10 | 2,407 | `RuntimeContainer`、配置加载与生命周期；部分组合根能力仍散落在 `services/runtime.py` |
| `app/api` | 是 | 29 | 4,577 | FastAPI 路由与 DTO；按 interview/prep/materials/memory/reports/rag/runtime 等分模块 |
| `app/graphs` | 是 | 11 | 4,213 | LangGraph 状态机与状态定义；`durable_interview_graph.py` 单文件约 2,134 LOC |
| `app/services` | 是 | 220 | 75,970 | 事实上的巨型 legacy 层，混合 domain rule、application、persistence、provider、workflow、eval 等职责 |
| `app/a2a` | 是 | 38 | 1,616 | A2A-V1 in-process protocol、registry、cards、client/server |
| `app/agents` | 是 | 7 | 627 | Agent 封装，依赖 services 与 graphs |
| `app/data` | 是 | 0 py | 32 文件 | `knowledge_v2` 静态语料（markdown/json），不属于 Python 生产代码但被放在 app 包下 |
| `contracts/` | 是 | 13 py | 未统计 | repository-level executable/governance contracts：evidence、policies、YAML 元数据 |
| `eval/` | 是 | 0 py | 13 文件 | 当前仅 `knowledge-v3` 数据集产物，尚未成为统一 `evals/` 层 |
| `tests/architecture` | 是 | 17 | 未统计 | 既有架构边界测试，但尚未覆盖本计划要求的依赖 ratchet |
| `tests/contracts` | 是 | 73 | 未统计 | 契约/回归测试已相当丰富 |

> LOC 为 `Get-Content | Measure-Object -Line` 的近似值，含空行、注释与 import；只用于观察数量级，不是架构结论。

## 2. 当前职责推测

### `app/domain`

当前实际职责：

- `app/domain/interview/`：commands、models、state_machine、drafts、errors、question_intent。
- `app/domain/knowledge/`：retrieval、evidence、fusion、reranking、source scope、query signals 等知识规则。
- `app/domain/memory/`：memory facts/contracts。
- `app/domain/context/`：context artifacts。

判断：已经承担相当多“纯模型/纯规则”，但部分规则仍留在 `services/context_*`、`services/followup_*` 中。

### `app/application`

当前实际职责：

- `app/application/interview/interview_start.py`：legacy-compatible start 入口。
- `app/application/knowledge/*`：检索/诊断/corpus write 等 use case。
- `app/application/materials/*`：用户资料 ingestion/deletion/service。

判断：是目标分层中的 use-case 层，但尚不纯净。例如 `interview_start.py` 直接 import `app.services.job_tags` 与 `app.services.prep`。

### `app/ports`

当前实际职责：

- 定义 `InterviewSessionRepository`、`InterviewLaunchRepository`、`UnitOfWorkPort`、`ContextArtifactStore`、`KnowledgeRepositoryPort` 等 Protocol。
- 兼容旧名：`EmbeddingProvider`、`KnowledgeRepository` 等仍指向 Port。

判断：是目标分层中的 Port 层，但存在反向依赖。例如 `app/ports/runtime.py` import 了 `app.services.prep`、`app.services.report`、`app.services.question_evaluations`。

### `app/adapters`

当前实际职责：

- `app/adapters/postgres/*`：session/report/principal memory/context artifacts/user documents 等 Postgres adapter。
- `app/adapters/pgvector/*`：pgvector embedding/user document repository。
- `app/adapters/memory/*`：in-memory adapters。
- `app/adapters/reliability/*`：runtime failure classification。

判断：目标归属明确，但当前还只是部分实现。大量 `app/services/postgres_*` 与 `app/services/in_memory_*` 仍在 services 层。

### `app/runtime`

当前实际职责：

- `RuntimeContainer` 管理 config、instance、flag、metadata、lifecycle state。
- `config/` 加载 environment/compatibility/memory/effective runtime config。
- `lifecycle.py` 管理 close/shutdown 顺序。

判断：目标组合根雏形已存在，但真正的“组合所有具体依赖”仍大量在 `app/services/runtime.py`（约 2,193 LOC），API 入口 `app/main.py` 也直接调用 `start_runtime/shutdown_runtime`。

### `app/api`

当前实际职责：

- FastAPI `router` 与各业务路由，共享 dependencies/errors/models/projections。
- 是外部 HTTP 边界，不应承载业务规则。

判断：边界相对清楚，但 API 通过 shared dependencies 深度接触 runtime/services，具体依赖关系待 P0A-T02 确认。

### `app/graphs`

当前实际职责：

- LangGraph `StateGraph` 定义、状态 schema、transition 逻辑。
- `durable_interview_graph.py` 混合大量 provider interaction、context 构建、decision/generation、lease heartbeat 与 LangGraph wiring。

判断：是 Durable Interview/Review workflow 的技术实现，但 God-module 风险高，属于 P4 重点关注对象。

### `app/services`

当前实际职责：

- 巨型 catch-all：`prep_*`、`interview_*`、`report_*`、`context_*`、`principal_memory_*`、`knowledge_*`、`postgres_*`、`in_memory_*`、`runtime.py`、`llm.py`、`session.py`、`review_*`、eval/diagnostics 等。
- 同时存在业务规则、use-case 编排、Postgres 实现、provider 调用、workflow service、compatibility shim 与诊断工具。

判断：这是本次重构的核心问题域。目标应逐步被 domain/application/ports/adapters/runtime 替代，最终删除。

## 3. 主要异常

1. **`app/services` 体量失衡**
   - 220 个 Python 文件，约 75,970 LOC，远高于其他任何层。
   - 同一目录混合了领域规则、应用编排、基础设施实现、provider 封装、workflow、eval 与兼容层。

2. **God module 风险明显**
   - `app/services/runtime.py` 约 2,193 LOC：既是组合根，又含大量具体构造和生命周期逻辑。
   - `app/services/llm.py` 约 1,417 LOC。
   - `app/graphs/durable_interview_graph.py` 约 2,134 LOC，混合业务规则、provider interaction、durable execution 与 LangGraph wiring。

3. **目标层之间已出现反向/跨层 import**
   - `app/application/interview/interview_start.py` 依赖 `app.services.*`。
   - `app/ports/runtime.py` 依赖 `app.services.*`。
   - `app/graphs/durable_interview_graph.py` 依赖大量 `app.services.*`，同时也依赖 `app.application.knowledge.followup_gap_service` 与 `app.adapters.reliability.runtime_failure`。
   - 这些是否构成“明确违规”仍需 P0A-T02 精确统计，但目录基线已经显示目标边界尚未成立。

4. **Persistence 实现分裂**
   - 已有 `app/adapters/postgres/*` 与 `app/adapters/memory/*`，但大量 `app/services/postgres_*`、`app/services/in_memory_*` 仍留在 services。

5. **Runtime composition 分裂**
   - `app/runtime/container.py` 只有容器机制；真正的 wiring/具体实现选择仍在 `app/services/runtime.py`。
   - `app/main.py` 直接 import `app.services.runtime.start_runtime`，说明 API 入口仍耦合 services。

6. **Port 仍暴露基础设施细节**
   - `TransactionalPrepPlanStore.select_locked(..., cursor)`、`UnitOfWorkPort.cursor` 等把数据库游标暴露到 port 层。
   - `interview_launch.py` 中 application-like 逻辑直接读取 `prep_plan_store.durability == "postgres"`。

7. **`contracts/` 与 `eval/` 语义尚未完全归一**
   - `contracts/` 目前更像 repository-level evidence/governance 系统，不是单一纯业务契约集合。
   - `eval/` 目前只是 `knowledge-v3` 数据集，目标 `evals/` 布局尚未形成。

8. **`app/data` 置于应用包内**
   - 静态 knowledge 语料放在 `app/data/knowledge_v2`，它既不是 Python 代码，也不是 runtime 依赖的理想位置。

## 4. 目标归属（非本次修改）

| 当前对象 | 目标归属 | 说明 |
| --- | --- | --- |
| `app/domain/*` | `app/domain` | 保留纯领域模型与规则；继续清理外部技术依赖 |
| `app/application/*` | `app/application` | 只依赖 domain 与 ports，移除 services/adapters/runtime 依赖 |
| `app/ports/*` | `app/ports` | 只定义稳定 Port，移除 services import 与 cursor/connection 泄漏 |
| `app/adapters/*` | `app/adapters` | 收纳所有具体基础设施实现 |
| `app/runtime/*` | `app/runtime` | 成为主要 composition root / lifecycle owner |
| `app/api/*` | `app/api` | 保持 HTTP 边界，通过 runtime 获取依赖 |
| `app/graphs/*` | LangGraph workflow adapter + application/domain 提取 | Graph 只保留 wiring，业务逻辑外移 |
| `app/services/*` | 按职责迁移后最终删除 | 分阶段迁移，不得一次重写 |
| `app/a2a/*` | 协议 adapter 与业务语义分离 | 技术协议走向 adapters/protocols/a2a |
| `app/agents/*` | 语义待 P6 进一步确认 | 当前视为 Core 边缘的 agent 封装 |
| `app/data` | 暂不处理 | 归属需单独决策，避免 P0 阶段移动语料 |
| `contracts/` | repository-level contracts | 边界 ADR 待 P0D-T01 |
| `eval/` | 目标 `evals/`，迁移阶段 P6 | 本次不移动 |

## 5. 暂不处理项

以下事项在 P0A-T01 明确不处理，避免越界：

- 不移动、重命名、删除 `app/services` 下任何文件。
- 不修改任何 import。
- 不拆分 God module。
- 不修正 `app/data` 或 `eval/` 布局。
- 不定义依赖违规 baseline；留待 P0A-T02/P0B-T01。
- 不建立 PostgreSQL migration 兼容性结论；留待 P0C。

## 6. 本任务验证

```text
git diff --stat
git diff
```

预期：只有新增 `docs/architecture/current-architecture-baseline.md`，没有生产代码变化。

## 7. Acceptance

- [x] 只新增文档或分析辅助文件
- [x] 无生产代码行为变化
- [x] 真实记录当前目录而非目标目录
- [x] 明确这是 brownfield / 半成品分层工程
