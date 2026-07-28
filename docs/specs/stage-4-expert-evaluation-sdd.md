# 阶段四：深度复盘与专家级评估系统 SDD

## 1. 目标

阶段四的目标是在当前阶段三“异步面评”能力基础上，升级出一个更高可信度、更细粒度、且能展示实时进度的专家级评估系统。核心变化不是继续堆 Prompt，而是把评估链路从“只依赖大模型主观判断”升级为“检索证据 + 多维评分 + 结构化解释”的可追溯系统。

本阶段聚焦三个结果：

- 高可信度评估：评估结论必须引用本地知识库中的标准知识点、最佳实践或岗位上下文，降低模型幻觉。
- 细粒度反馈：报告按多个明确维度打分，而不是只有单题总分。
- 实时状态追踪：前端在报告生成期间可以看到具体阶段和进度，而不是只看到 `processing`。

## 2. 范围与边界

### 2.1 本阶段包含

- 引入 PostgreSQL + `pgvector`，作为知识库的唯一向量存储方案。
- 新增知识库切片、写入、检索能力。
- 扩展评估器，在单题分析前先做 RAG 检索。
- 扩展报告模型，支持多维评分、证据引用、进度结构。
- 扩展后台任务和报告查询接口，返回进度阶段。
- 扩展前端轮询展示，渲染“检索中 / 分析中 / 汇总中 / 完成”。
- 增加 Golden Dataset 与可解释性测试。

### 2.2 本阶段明确不做

- 不把 `InterviewSessionStore` 的 session 状态迁移到 PostgreSQL。
- 不把 `InterviewReport` / `ReportRecord` 的最终缓存持久化到 PostgreSQL。
- 不引入 Redis。
- 不引入 Celery 或其他外部任务队列。
- 不引入 WebSocket 主动推送。
- 不把多 Agent 真正拆成多个独立运行服务。
- 不做模型微调。

这条边界必须严格执行。当前代码仍然是 `FastAPI + 内存 Store + BackgroundTasks` 的 MVP 形态，而长期架构文档已经上升到 `Redis + PostgreSQL + pgvector + Celery + WebSocket + 多 Agent`。阶段四只做“专家评估能力”的最小闭环，不同时推进底层基础设施重构。

## 3. 与长期架构的关系

结合 [C:\Users\admin\Desktop\interview-agent-architecture.docx](</C:/Users/admin/Desktop/interview-agent-architecture.docx>)，阶段四不是直接跳到完整多 Agent 平台，而是把其中两个角色的最小能力先落到当前单体代码里：

- `Knowledge Agent` 的最小落地：由 `app/services/vector_store.py` 提供本地知识库写入、检索和过滤能力。
- `Shadow Reviewer Agent` 的增强版落地：由 `app/services/evaluator_ext.py` 提供 RAG 注入、多维评分、证据解释和进度回调。

阶段四保留现有主链路：

```text
POST /api/interviews/{session_id}/answer
-> InterviewSessionStore.submit_answer(...)
-> InterviewGraphRunner
-> turn.status == finished
-> BackgroundTasks.add_task(generate_report_for_session, ...)
-> ExpertShadowEvaluator.evaluate(...)
-> InterviewSessionStore.save_report(...)
```

这条链路会被增强，但不会被替换。

## 4. 当前代码现状分析

当前实现中与阶段四最相关的基础如下：

- [app/services/evaluator.py](/abs/path/F:/agent/Interview-Agent/app/services/evaluator.py) 已具备按 `question_id` 切块的能力。
- [app/services/report.py](/abs/path/F:/agent/Interview-Agent/app/services/report.py) 已定义 `InterviewFeedback`、`InterviewReport` 和 `ReportRecord`，但仍是单题单分模型。
- [app/services/report_tasks.py](/abs/path/F:/agent/Interview-Agent/app/services/report_tasks.py) 已具备后台报告任务入口。
- [app/api/routes.py](/abs/path/F:/agent/Interview-Agent/app/api/routes.py) 已具备 `GET /report` 接口，但 `202` 只返回 `{ "status": "processing" }`。
- [app/static/app.js](/abs/path/F:/agent/Interview-Agent/app/static/app.js) 已具备轮询能力，但不知道处理到了哪一步。

当前代码存在四个必须补齐的结构缺口：

### 4.1 `InterviewState` 缺少检索上下文

[app/graphs/interview_state.py](/abs/path/F:/agent/Interview-Agent/app/graphs/interview_state.py) 的 `InterviewState` 目前没有保存：

- `job_description`
- `resume_text`
- `job_tags`

没有这些字段，阶段四只能做“全库泛检索”，无法按 JD 过滤，达不到“高可信度评估”目标。

### 4.2 缺少完整的状态传播链

当前调用链是：

```text
POST /interviews
-> store.start(plan)
-> runner.start(session_id, plan)
-> build_initial_state(session_id, plan)
```

这条链路里，API 层拿到了 `job_description` 和 `resume_text`，但它们没有被传进 graph state。阶段四必须改完整传播路径，而不是只在 `InterviewState` 上“声明要有这些字段”。

### 4.3 `job_tags` 的生成方式悬空

`job_tags` 是整个 RAG metadata 过滤的入口。如果它的生成方式不确定，检索范围、测试预期、导入脚本、回归结果都会不稳定。

### 4.4 `ReportRecord` 缺少进度更新能力

当前 `ReportRecord` 只有：

- `status`
- `report`
- `error`

Store 也没有 `update_report_progress()`。这意味着即使 `ExpertShadowEvaluator` 设计了 `on_progress` 回调，后台任务也没有落地通道把它写回 Store。

## 5. 核心设计原则

阶段四沿用长期架构文档中的三条关键思想，但按当前代码能力收缩实现：

- 前台体验优先：实时提问链路不承担检索或重评分逻辑。
- 慢轨评估增强：RAG 和多维评分只发生在后台报告生成阶段。
- 结论可追溯：所有重要评分理由都应该能回到具体 question chunk 和具体知识片段。

对应到当前仓库：

- `InterviewGraphRunner` 不做实时检索。
- `submit_answer` 不增加同步数据库访问。
- `BackgroundTasks` 仍是任务触发入口。
- 专家评估能力只增强慢轨。

## 6. 知识库与 Embedding 设计

### 6.1 存储方案

向量库统一使用 PostgreSQL 扩展 `pgvector`。不引入 ChromaDB，不引入本地文件向量索引。

原因：

- 用户明确要求使用 `pgvector`。
- 长期架构也把 `PostgreSQL + pgvector` 作为事实存储 + 检索存储组合。
- 这为后续阶段把报告、证据链、任务日志迁库留下自然演进路径。

### 6.2 Embedding 模型固定为 `BAAI/bge-m3`

阶段四不再保持模型开放选择，直接固定：

```text
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
```

选型理由：

- 当前产品界面、知识文档、评估报告都以中文为主。
- `BGE-M3` 同时覆盖中文和多语言，后续扩展空间比纯英文小模型更好。
- 必须先拍板模型和维度，才能定义 `VECTOR(n)`、建表、写导入脚本和做检索测试。

阶段四约定：

- `knowledge_chunks.embedding` 列定义为 `VECTOR(1024)`。
- 阶段四不支持运行时随意切换 embedding 模型。
- 如未来更换 embedding 模型，必须通过单独迁移脚本重建向量列和知识索引。

### 6.3 数据来源

新增目录：

```text
app/data/knowledge/
```

目录内至少支持三类 Markdown 文件：

- `benchmarks/`：专家标杆回答、优秀回答模板、加分项清单。
- `theory/`：技术原理、分布式、中间件、缓存一致性等标准知识点。
- `communication/`：STAR 结构、项目表达、面试话术纠偏模板。

阶段四不要求知识库规模很大，但必须先随仓库带入两份最小可用资产：

- 一份 `benchmarks` 文件
- 一份 `theory` 文件

没有这两份基础文件，RAG 链路即使跑通，也无法验证检索质量。

### 6.4 向量表结构

新增 `knowledge_chunks` 表，建议字段：

```text
id UUID / TEXT
title TEXT
content TEXT
source_path TEXT
source_type TEXT
domain TEXT
tags JSONB
difficulty TEXT
chunk_index INT
metadata JSONB
embedding VECTOR(1024)
created_at TIMESTAMP
updated_at TIMESTAMP
```

说明：

- `source_type`：`expert_benchmark | theory | communication | jd_context`
- `domain`：如 `redis`、`java`、`mq`、`system-design`
- `tags`：支持多标签过滤
- `metadata`：预留原文段落、版本、来源说明等扩展信息

### 6.5 检索策略

每题分析时，评估器基于以下输入形成 query：

- `question_text`
- `focus`
- 该题候选人回答摘要
- `job_description`
- `job_tags`

检索分两步：

1. 先基于 `job_tags` / `domain` / `source_type` 做 metadata 过滤。
2. 再做向量相似度召回，返回 `top_k` 结果。

召回结果至少拆成两类用途：

- 评分依据：偏原理、最佳实践、标准缺失点。
- 推荐回答素材：偏高分表达模板。

不允许把所有片段粗暴拼成一段长上下文。这会降低评估可解释性，也会让 prompt 结构失控。

## 7. `job_tags` 设计与状态传播链

### 7.1 `job_tags` 生成方式

阶段四不使用 LLM 生成 `job_tags`，统一采用“确定性关键词提取”。

原因：

- 当前 `prepare_interview()` 只返回 `InterviewPlan`。如果把 `job_tags` 交给 LLM 生成，就需要同步修改预热返回结构，范围会膨胀。
- `job_tags` 是检索过滤入口，必须在测试中可预测、可断言。
- 规则提取虽然粗糙，但足够稳定，适合作为阶段四 MVP。

新增函数建议：

```python
def extract_job_tags(job_description: str) -> list[str]:
    ...
```

实现方式：

- 使用受控词表 + 小写归一化 + 去重。
- 首批词表覆盖当前项目主场景：
  - `python`
  - `fastapi`
  - `redis`
  - `postgresql`
  - `mysql`
  - `java`
  - `spring`
  - `kafka`
  - `rabbitmq`
  - `system-design`

若未提取到任何标签：

- 使用 `["general"]` 作为保底标签。
- 检索时优先查 `general`。
- 禁止直接退化为无过滤全库泛检索。

### 7.2 `InterviewState` 新增字段

阶段四的 `InterviewState` 扩展为：

```python
class InterviewState(TypedDict):
    session_id: str
    plan: InterviewPlan
    current_index: int
    messages: list[InterviewMessage]
    decision: InterviewDecision | None
    pending_output: str | None
    status: Literal["active", "finished"]
    job_description: str
    resume_text: str
    job_tags: list[str]
```

### 7.3 完整传播路径

阶段四必须明确这条数据传播链：

1. `POST /api/interviews` 读取 `payload.job_description` 和 `payload.resume_text`
2. `routes.py` 调用 `extract_job_tags(payload.job_description)`
3. `InterviewSessionStore.start(...)` 新增参数：
   - `job_description`
   - `resume_text`
   - `job_tags`
4. `InterviewGraphRunner.start(...)` 新增同名参数
5. `build_initial_state(...)` 新增同名参数并写入 state

建议签名修改：

```python
build_initial_state(
    session_id: str,
    plan: InterviewPlan,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> InterviewState
```

```python
InterviewGraphRunner.start(
    session_id: str,
    plan: InterviewPlan,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> InterviewState
```

```python
InterviewSessionStore.start(
    plan: InterviewPlan,
    *,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> InterviewTurn
```

## 8. 新增服务模块设计

### 8.1 `app/services/vector_store.py`

职责：

- 初始化 PostgreSQL / `pgvector` 连接
- 检查扩展是否可用
- 创建向量表
- 写入知识切片
- 按 metadata + 向量相似度检索知识片段

建议接口：

```python
class KnowledgeChunk(BaseModel):
    chunk_id: str
    title: str
    content: str
    source_type: str
    domain: str
    tags: list[str]
    metadata: dict[str, str | int | float | bool | None]
    score: float | None = None


class PgVectorKnowledgeStore:
    def upsert_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        ...

    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        ...
```

实现要求：

- 连接参数来自环境变量
- 明确区分“连接失败”“扩展不存在”“表不存在”“无结果”
- 无结果不算异常
- 连接失败属于报告失败条件

运行时注入方式固定如下：

- 新增模块级工厂函数 `get_knowledge_store()`
- 该函数内部懒加载并缓存一个 `PgVectorKnowledgeStore` 单例
- 生产代码中的后台任务默认通过该工厂获取 store
- 测试代码通过 monkeypatch 覆盖 `get_knowledge_store()`，避免真实数据库泄漏进纯单元测试

建议形态：

```python
_knowledge_store: PgVectorKnowledgeStore | None = None


def get_knowledge_store() -> PgVectorKnowledgeStore:
    global _knowledge_store
    if _knowledge_store is None:
        _knowledge_store = PgVectorKnowledgeStore.from_env()
    return _knowledge_store
```

### 8.2 `app/services/evaluator_ext.py`

职责：

- 在现有 chunking 基础上做 RAG 检索增强
- 组织维度评分 prompt
- 生成带证据链的结构化报告
- 在评估过程中通过回调上报进度

与现有 [app/services/evaluator.py](/abs/path/F:/agent/Interview-Agent/app/services/evaluator.py) 的关系必须明确：阶段四采用“组合”，而不是“替换”。

- `ShadowEvaluator` 继续保留，作为基础 chunking / fallback 工具模块
- `ExpertShadowEvaluator` 复用：
  - `build_evaluation_chunks()`
  - `build_fallback_report()`
- `ExpertShadowEvaluator` 新增职责只有三类：
  - 检索证据
  - 组织增强输入
  - 回调进度

建议接口：

```python
class ExpertShadowEvaluator:
    def __init__(
        self,
        llm: InterviewLLM,
        vector_store: PgVectorKnowledgeStore,
    ) -> None:
        ...

    def evaluate(
        self,
        state: InterviewState,
        on_progress: Callable[[ReportProgress], None] | None = None,
    ) -> InterviewReport:
        ...
```

`evaluate()` 的内部步骤建议为：

1. 复用 `build_evaluation_chunks(state)` 生成题目块
2. 回调 `retrieving`
3. 针对每个 chunk 从 `PgVectorKnowledgeStore` 检索知识片段
4. 回调 `analyzing`
5. 调用 `llm.generate_report(...)`，但输入不再只是 `chunks`，而是增强后的 `evaluation_items`
6. 回调 `aggregating`
7. 成功返回专家报告；若 schema 解析失败，则回退到 `build_fallback_report(...)`

### 8.3 `scripts/load_knowledge.py`

职责：

- 读取 `app/data/knowledge/**/*.md`
- 按段落或小节切片
- 为每个 chunk 生成 embedding
- 写入 `pgvector`

阶段四只要求“能导入、能检索、能测试”，不要求做复杂增量同步框架。

部署说明必须额外注明：

- `BAAI/bge-m3` 首次运行会触发本地模型下载
- 首次下载耗时和磁盘占用显著高于纯 API 方案
- `scripts/load_knowledge.py` 应在日志中区分：
  - 首次模型下载
  - 本地缓存命中
  - 向量写入

## 9. 报告模型升级

### 9.1 单题反馈升级

`InterviewFeedback` 扩展为专家级结构：

```python
class DimensionScores(BaseModel):
    breadth: int = Field(ge=0, le=100)
    depth: int = Field(ge=0, le=100)
    architecture: int = Field(ge=0, le=100)
    engineering: int = Field(ge=0, le=100)
    communication: int = Field(ge=0, le=100)


class FeedbackReference(BaseModel):
    chunk_id: str
    title: str
    source_type: str
    excerpt: str


class InterviewFeedback(BaseModel):
    question_id: str
    question_text: str
    user_answer: str
    score: int = Field(ge=0, le=100)
    dimension_scores: DimensionScores
    rationale: str
    critique: str
    better_answer: str
    references: list[FeedbackReference]
```

设计说明：

- `score` 保留，方便和阶段三 UI 兼容
- `dimension_scores` 是细粒度评估主体
- `rationale` 负责解释“为什么这么评”
- `references` 负责把结论绑定到具体知识片段

### 9.2 全局报告升级

`InterviewReport` 扩展为：

```python
class InterviewReport(BaseModel):
    session_id: str
    overall_score: int = Field(ge=0, le=100)
    overall_dimension_scores: DimensionScores
    summary: str
    highlights: list[str] = Field(min_length=1, max_length=3)
    feedbacks: list[InterviewFeedback]
    status: Literal["completed"] = "completed"
    is_fallback: bool = False
```

### 9.3 进度模型

新增：

```python
class ReportProgress(BaseModel):
    stage: Literal["retrieving", "analyzing", "aggregating", "completed"]
    percent: int = Field(ge=0, le=100)
    message: str
    current_question_id: str | None = None
```

并将 `ReportRecord` 扩展为：

```python
class ReportRecord(BaseModel):
    status: Literal["processing", "completed", "failed"]
    progress: ReportProgress | None = None
    report: InterviewReport | None = None
    error: str | None = None
```

阶段映射：

- `retrieving`：20%
- `analyzing`：60%
- `aggregating`：80%
- `completed`：100%

### 9.4 破坏性升级影响面

这次 schema 升级是破坏性的，阶段四实现计划必须单独安排“模型升级与测试数据修复”任务。

至少受影响的位置包括：

- `app/services/evaluator.py:build_fallback_report()`
- `app/services/llm.py:OpenAIInterviewLLM.generate_report()`
- 所有 fake LLM 的 `InterviewReport(...)` 构造
- 所有 `InterviewFeedback(...)` 测试构造
- `tests/test_report_models.py`
- `tests/test_report_evaluator.py`
- `tests/test_report_api.py`
- `tests/test_session_report_store.py`

不能把这些变更夹带在其他任务里顺手处理，否则会导致后续任务被大面积 schema 失败淹没。

## 10. Store 扩展设计

阶段四为 `InterviewSessionStore` 新增：

```python
def update_report_progress(
    self,
    session_id: str,
    progress: ReportProgress,
) -> None:
    ...
```

行为约束：

- session 不存在时抛 `ValueError`
- 没有 report record 时抛 `ValueError`
- 只有 `status == "processing"` 时允许更新进度
- `completed` / `failed` 后拒绝再次更新

`mark_report_processing()` 的职责也要同步升级：

- 初始化 `ReportRecord(status="processing", progress=ReportProgress(...20%...))`
- 保留当前幂等行为

## 11. 后台任务与评估流程

### 11.1 核心链路

阶段四的慢轨报告生成流程如下：

```text
finished session
-> BackgroundTasks 调用 generate_report_for_session
-> Store 标记 processing + progress=20%
-> ExpertShadowEvaluator 切块
-> 每题向 pgvector 检索知识片段
-> LLM 生成多维单题反馈
-> 汇总总评和全局维度
-> Store 写入 completed + progress=100%
```

### 11.2 后台任务注入方式

`generate_report_for_session(session_id, store)` 当前只有两个参数。阶段四不把 `vector_store` 作为额外参数从 FastAPI 层往下层硬塞，而是在任务内部通过工厂函数获取。

建议任务逻辑：

```python
llm = _resolve_llm(store)
vector_store = get_knowledge_store()
evaluator = ExpertShadowEvaluator(llm=llm, vector_store=vector_store)
```

这样可以满足：

- 生产代码调用简单
- BackgroundTasks 不依赖 FastAPI dependency 注入
- 测试里可以 patch `get_knowledge_store()`

### 11.3 进度回调胶水层

后台任务中必须显式把 `on_progress` 接到 Store：

```python
def publish_progress(progress: ReportProgress) -> None:
    store.update_report_progress(session_id, progress)
```

然后：

```python
report = evaluator.evaluate(state, on_progress=publish_progress)
```

这层胶水逻辑必须写在 `generate_report_for_session()` 内，而不是隐含在 evaluator 内部。

### 11.4 失败策略

需要区分三类失败：

#### A. 检索基础设施失败

例如：

- PostgreSQL 连接失败
- `pgvector` 扩展不可用
- 知识库 SQL 执行失败

处理：

- 标记 `failed`
- API 返回 `500`
- 前端停止轮询并显示失败提示

原因：这是“证据层不可用”，继续产出“专家评估报告”会误导用户。

#### B. LLM 结构化输出失败

例如：

- 维度字段缺失
- JSON schema 解析失败

处理：

- 允许使用 fallback report
- `is_fallback=True`
- `references=[]`
- fallback 也必须构造完整新 schema：
  - `dimension_scores`
  - `overall_dimension_scores`
  - `rationale`
  - 空 references

#### C. 检索为空

处理：

- 不视为失败
- 单题 `references=[]`
- `rationale` 必须说明证据不足

## 12. API 设计

### 12.1 `POST /api/interviews`

该接口保持现有返回体，但需要增加状态传播：

- 提取 `job_description`
- 提取 `resume_text`
- 生成 `job_tags`
- 调用 `store.start(...)` 时显式传入这三项

### 12.2 `POST /api/interviews/{session_id}/answer`

保持现有响应结构。

在后台任务开始前，Store 写入初始进度：

```json
{
  "status": "processing",
  "progress": {
    "stage": "retrieving",
    "percent": 20,
    "message": "正在匹配岗位相关知识库与标杆回答",
    "current_question_id": null
  }
}
```

### 12.3 `GET /api/interviews/{session_id}/report`

返回逻辑：

#### 404

- session 不存在时保留当前语义：`detail = "session not found"`
- session 未结束时保留当前语义：`detail = "interview is not finished"`

阶段四不把这两个 404 合并成统一错误字符串。当前代码和测试已经依赖这两个分支的不同语义，保留差异更利于调试。

#### 202

返回：

```json
{
  "status": "processing",
  "progress": {
    "stage": "analyzing",
    "percent": 60,
    "message": "正在分析第 2 题的工程实践维度",
    "current_question_id": "q2"
  }
}
```

#### 200

返回完整专家评估报告。

#### 500

返回：

```json
{
  "detail": "pgvector knowledge store is unavailable"
}
```

## 13. 前端展示设计

阶段四前端继续使用轮询，不引入 WebSocket 推送。

[app/static/app.js](/abs/path/F:/agent/Interview-Agent/app/static/app.js) 需要升级：

- `202` 时读取 `progress.stage`
- 优先展示 `progress.message`
- 可选展示 `progress.percent`
- 若 `progress` 缺失，则回退到阶段三默认文案，保持兼容

建议状态文案：

- `retrieving`：教练正在检索与你岗位相关的参考知识
- `analyzing`：教练正在逐题分析你的回答深度与工程实践
- `aggregating`：教练正在汇总全局评分与改进建议
- `completed`：报告已完成

渲染层面阶段四不强制做复杂图表，但要为后续雷达图预留结构：

- 总分
- 全局维度分
- 单题维度分
- 单题证据引用

## 14. 测试策略

### 14.1 单元测试

新增：

```text
tests/test_vector_store.py
tests/test_expert_evaluator.py
tests/test_report_progress.py
```

覆盖：

- `pgvector` 检索能按 `job_tags` 过滤
- 每题检索结果能稳定返回 `top_k`
- `ExpertShadowEvaluator` 会把检索结果注入 LLM 输入
- 生成的 `InterviewFeedback` 包含 `dimension_scores` 和 `references`
- 检索基础设施失败时，任务会写入 `failed`
- 结构化输出失败时，任务会写入 fallback completed

还要单独覆盖 `InterviewState` 的传播链：

- `POST /interviews` 传入 JD / 简历
- `InterviewSessionStore.start(...)` 正确接收
- `build_initial_state(...)` 最终保存到 state
- 后台评估读取到 `job_tags`

### 14.2 API 测试

扩展：

```text
tests/test_report_api.py
```

新增覆盖：

- `202` 返回 `progress.stage`、`progress.percent`、`progress.message`
- 进度值会从 `retrieving` 走到 `analyzing`
- `failed` 时返回明确错误
- `completed` 时返回多维评分字段
- unknown session 的 404 和 unfinished session 的 404 保持不同 `detail`

### 14.3 Golden Dataset

新增目录：

```text
tests/golden/
```

建议至少准备 20 组案例，分成：

- 高质量回答
- 中等回答
- 明显空泛回答
- 错误原理回答

验证目标：

- RAG 介入后，错误原理回答会被更稳定地识别
- 高质量回答在 `depth` / `engineering` 维度得分更高
- 评估理由能引用检索片段中的关键点

### 14.4 可解释性断言

可解释性测试不只看分数，还要看：

- `references[].chunk_id` 是否真实存在
- `rationale` 是否提到了引用知识点的核心缺失
- 不能出现“没有引用却宣称依据某协议”的幻觉表述

### 14.5 PostgreSQL 测试隔离

阶段四必须明确测试方案，而不是只承认复杂度上升。

推荐策略：

- 纯单元测试：mock `PgVectorKnowledgeStore`，保持 `pytest -q` 可在无数据库环境下运行
- 集成测试：新增可选标记，例如 `@pytest.mark.pgvector`，仅在本地或 CI 提供 PostgreSQL + `pgvector` 时运行

阶段四默认不要求所有开发者机器都常驻 PostgreSQL。基础回归仍由 mock 驱动。

如后续接入 CI：

- 推荐使用服务容器提供 PostgreSQL + `pgvector`
- 阶段四不引入 `testcontainers-python`

## 15. 配置与依赖

阶段四新增依赖固定为：

- `psycopg2-binary`
- `pgvector`
- `langchain-postgres` 或自定义 SQL 层
- `langchain-huggingface`
- `sentence-transformers`

不再保留双路线：

- PostgreSQL 驱动固定选 `psycopg2-binary`
- 本地 embedding 固定选 `sentence-transformers`

新增环境变量建议：

```text
POSTGRES_DSN=
PGVECTOR_TABLE=knowledge_chunks
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
REPORT_RETRIEVAL_TOP_K=5
```

约束：

- embedding 走本地模型，不把知识文本发给外部 embedding API
- LLM 评估仍可走当前大模型服务

## 16. 验收标准

阶段四完成后应满足：

- 报告评估前会先检索 `pgvector` 本地知识库
- 单题反馈包含维度分、解释和证据引用
- 全局报告包含全局维度分
- `GET /report` 的 `202` 响应包含进度信息，而不是只有 `processing`
- `InterviewState` 能提供 JD / 简历上下文给评估器使用
- 检索基础设施失败会进入 `failed`
- LLM schema 失败时会生成带 `is_fallback=True` 的完整兜底报告
- Golden Dataset 能验证 RAG 引用和评分改善趋势
- 阶段三现有快轨提问链路性能不被明显拖慢

## 17. 风险与约束

- PostgreSQL / `pgvector` 是项目第一次引入数据库边界，测试隔离成本会上升
- 当前 session/report 仍在内存中，服务重启后进度状态会丢失
- `BackgroundTasks` 仍缺少真正的可靠任务能力，长评估任务在进程异常退出时会丢失
- 当前项目存在中文乱码历史，阶段四新增文档和新代码文案必须统一 UTF-8
- 如果知识库内容质量差，RAG 会把错误知识放大，因此知识文件必须视为受控资产
- `BAAI/bge-m3` 首次模型下载会带来明显的启动耗时和磁盘占用

## 18. 后续演进

阶段四稳定后，可以顺着长期架构继续演进：

- 阶段五：把 `session/report/progress` 迁移到 PostgreSQL
- 阶段六：引入 Redis + Celery，把慢轨任务从 `BackgroundTasks` 升级为可靠队列
- 阶段七：把 `Knowledge / Reviewer / Report Coach` 拆成更清晰的 ReviewGraph
- 阶段八：前端接入雷达图、证据对照表、改写前后对比

阶段四的职责只有一个：先把“专家评估”做成有证据、有维度、有进度的闭环。
