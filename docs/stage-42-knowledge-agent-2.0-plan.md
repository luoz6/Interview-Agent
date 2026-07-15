# Stage 42 Knowledge Agent 2.0 实施计划

状态：`READY FOR EXECUTION`

日期：2026-07-15

## 1. 阶段定位

Stage 42 的目标不是增加新的 Agent 名称，而是把现有关键词式预热升级为可检索、可持久化、可追踪、可评测的知识证据闭环。

当前真实基线：

- `KnowledgeAgent.generate_plan()` 先调用 LLM 生成题目，再通过关键词生成 `prep_context`。
- `PrepKnowledgeTopic.evidence` 是规则文本，不是 pgvector 命中的知识证据。
- Examiner 追问收到 `knowledge_agent` 文本消息，但没有稳定的 `chunk_id`。
- pgvector 检索只发生在 Shadow Reviewer 最终评估阶段，并会针对题目和回答重新搜索。
- `InterviewPlan` 已通过 `plan_json` 持久化，因此新增可选证据字段不需要新增运行表。
- 当前知识库只有 10 个 Markdown chunk，覆盖 Redis、MySQL、Kafka、FastAPI 和系统设计；该规模不足以支撑有区分度的负例评测。

Stage 42 要将链路改为：

```text
JD/简历结构化解析
-> 主题检索查询
-> pgvector Top-K 候选证据
-> 基于证据生成面试计划
-> 后端校验题目与 evidence_id 绑定
-> Examiner 复用题目证据
-> Shadow Reviewer/Report Coach 复用同一 evidence_id
-> 报告引用与检索 trace 可审计
```

## 2. Stage 41 发布前置项（不属于 Stage 42）

Stage 41 发布前置项已完成，以下结果作为 Stage 42 基线记录，不计入 Stage 42 任务、工期或提交：

1. 审查 Stage 40/41 工作区，只提交产品代码、测试、正式文档和白名单验收制品。
2. 确认 `.idea/`、`.claude/`、临时日志、浏览器失败目录和探索性 reports 不进入提交。
3. 从干净 clone 按 `docs/local-v1-runbook.md` 完成安装和 Core RC。
4. 创建 `local-v1.0.0-rc1` 标签，并从该标签创建 Stage 42 分支。
5. 固定 Stage 42 基线结果、Python/Node 版本和知识库 manifest。

完成结果：commit `9708da4`、tag `local-v1.0.0-rc1`。干净 clone 已完成 Python 3.11 独立 venv 带哈希安装、`pip check`、`npm ci`、CSS 构建、`515 passed, 1 opt-in skipped` 和桌面/移动端 Playwright；测试后工作区保持干净。Stage 42 从该标签创建 `stage-42-knowledge-agent-2` 分支，不建立 Task 0。

## 3. 范围

### 3.1 本阶段包含

- JD/简历的确定性岗位画像与主题提取。
- Prep 阶段 pgvector 检索。
- 题目与知识 chunk 的稳定绑定。
- 追问继承题目绑定证据。
- 最终报告优先复用同一证据集合。
- 检索失败、空知识库和低相关结果的明确降级。
- PostgreSQL 恢复后证据 ID 连续性。
- 离线检索评测、浏览器回归和真实模型 smoke。
- 检索 trace、指标和正式验收制品。

### 3.2 本阶段不包含

- WebSocket 传输。
- Redis/LangGraph checkpoint。
- 多用户、登录和权限系统。
- 语音识别、语音合成或数字人。
- 知识库管理后台。
- Parent-Child RAG、重排模型或新的向量数据库。
- 大规模前端框架重写。
- 修改 Stage 40 `stage40-rubric-v2` 评分算法。

## 4. 核心数据契约

先扩展现有 `app/services/prep.py`，所有新增字段均提供默认值，保证旧 `plan_json` 可以恢复。

建议契约：

```text
RoleProfile
  role_title
  seniority
  canonical_tags[]
  domains[]
  technologies[]
  responsibilities[]
  resume_signals[]
  uncovered_technologies[]
  query_terms[]

KnowledgeEvidenceRef
  evidence_id          # 等于受信任知识库 chunk_id
  title
  domain
  source_type
  score
  content_sha256
  corpus_manifest_sha256
  candidate_summary    # 可展示，不包含参考答案全文

KnowledgeBindingSnapshot
  query_id
  topic_id
  filters
  top_k
  hit_ids[]
  hit_content_sha256[]
  corpus_manifest_sha256
  status               # completed / empty / degraded
  degraded_reason

PrepKnowledgeTopicV2
  id
  label
  source               # retrieval / keyword_fallback
  evidence_ids[]
  candidate_summary

PrepQuestionHintV2
  question_id
  topic_ids[]
  evidence_ids[]
  follow_up_hints[]
```

约束：

- `evidence_id` 必须来自本次 Prep 检索候选集合，LLM 不得创造 ID。
- `InterviewPlan` 只保存引用、标题、分数和可展示摘要，不保存知识全文或简历全文副本。
- 内部 Reviewer 需要知识全文时，通过 `get_by_ids()` 从知识库读取，并校验 Prep 保存的内容哈希。
- 对外 API 不返回 benchmark 参考答案全文。
- 旧计划没有 v2 字段时继续使用 Stage 41 行为，并记录 `legacy_plan`。
- `follow_up_hints` 继续由后端按 canonical tag 和 evidence metadata 确定性生成；本阶段不允许 LLM 自由生成该字段。
- 不在知识库覆盖范围内的简历技术进入 `uncovered_technologies`，只用于 UI 覆盖提示和内容 backlog，不生成伪检索证据。

### 4.1 Trace 存储决策

Trace 分为两层，禁止把所有运行细节塞入 `plan_json`：

1. `plan_json` 只持久化紧凑的 `KnowledgeBindingSnapshot`：query ID、filters、hit IDs、每个 hit 的内容哈希、corpus manifest 哈希、状态和降级原因。它负责重启恢复和证据连续性，不保存 latency、完整 query、简历或知识全文。
2. 新建 `KnowledgeTraceRecorder`，通过 `KNOWLEDGE_TRACE_DIR` 写脱敏运行 trace：run ID、规范化 query、latency、Top-K 分数和错误分类。Prep 尚无 session ID，因此 trace 使用独立 `prep_run_id`；创建 session 后在 plan snapshot 中保留该 ID。
3. Stage 42 不新增 trace 数据库表。正式 RC 只复制白名单 trace 到 acceptance 目录；默认本地 trace 仍位于被忽略的临时目录。
4. public Prep/Session API 只返回 evidence 标题、来源和 candidate summary，不返回规范化 query、latency、内容哈希或内部错误。

## 5. 分阶段交付

### Stage 42A：检索与题目证据绑定

完成岗位画像、语料扩充、查询构建、Prep 检索、题目绑定、持久化、Prep UI 和离线检索评测。42A 结束时可以回答“为什么问这道题”，且检索指标已经独立通过。

### Stage 42B：证据继承与报告复用

完成 Examiner、Shadow Reviewer、Report Coach 的 evidence ID/内容哈希连续性、降级路径和最终 RC。42B 结束时可以回答“为什么这样追问、为什么这样评分”。

42A 必须单独通过后才能合并 42B，禁止把两个阶段压成一个不可诊断的大提交。

## 6. 实施任务

### Task 1：定义 v2 契约与兼容序列化

主要文件：

- `app/services/prep.py`
- `app/services/session_serialization.py`
- `tests/test_prep_service.py`
- `tests/test_session_serialization.py`
- `tests/test_postgres_session_store.py`

执行：

1. 增加 `RoleProfile`、`KnowledgeEvidenceRef` 和 `KnowledgeBindingSnapshot`。
2. 为 topic、question hint 和 prep context 增加 v2 可选字段与 `schema_version`。
3. 验证旧 plan JSON、新 plan JSON、内存存储和 PostgreSQL 往返。
4. 明确 public payload，不暴露内部 chunk content。

红灯测试：旧 Stage 41 plan 恢复失败、新 evidence ID 在 PostgreSQL 重启后丢失、API 泄露 chunk 全文。

完成标准：新旧契约均可恢复，数据库不需要迁移，public payload 安全。

### Task 2：岗位画像与确定性查询构建

主要文件：

- 新建 `app/services/knowledge_profile.py`
- 新建 `app/services/knowledge_query.py`
- 新建 `tests/test_knowledge_profile.py`
- 新建 `tests/test_knowledge_query.py`

执行：

1. 从 JD 提取角色、级别、技术、职责和领域。
2. 从简历只提取与岗位交集的项目技术和能力信号。
3. 对每个主题生成稳定 `query_id` 和规范化 query。
4. 去除邮箱、手机号、URL 和无关个人信息，不把完整简历写入 trace。
5. 查询构建必须可离线测试，不依赖 LLM。
6. `extract_job_tags()` 保持唯一的词法标签入口；`knowledge_profile.py` 调用它并映射到统一 canonical taxonomy，不再维护第二套技术关键词表。
7. `canonical_tags` 用于 pgvector filters；`technologies` 保存原始识别结果；知识库未覆盖项进入 `uncovered_technologies`，不得强行映射到 `general` 后宣称命中。
8. `follow_up_hints` 使用 canonical tag 与 evidence metadata 的确定性模板；LLM 只生成题目文本，不生成或覆盖 hints。

红灯测试：相同输入产生不稳定 query；个人信息进入 query；空 JD/简历生成虚假技术主题。

完成标准：固定输入得到稳定岗位画像和查询，敏感文本不进入 trace。

### Task 2B：扩充知识语料与建立版本清单

主要文件：

- `app/data/knowledge/**`
- `scripts/load_knowledge.py`
- 新建 `scripts/build_knowledge_manifest.py`
- 新建 `tests/test_knowledge_manifest.py`

执行：

1. 在写检索指标前，将知识库从 10 个 chunk 扩充到至少 25 个高质量 chunk。
2. 每个主要领域至少包含机制、故障模式、工程实践和 benchmark 四类可区分内容；增加跨领域近似主题作为 hard negatives。
3. 每个 chunk 增加 `content_sha256`，manifest 记录逻辑 chunk ID、内容哈希、domain、source type 和 corpus version。
4. `load_knowledge.py` 将哈希写入 metadata；同一逻辑 ID 内容改变时 manifest 必须改变。
5. 内容必须经过人工技术审核，不能为了指标复制或轻微改写同一段文本。

红灯测试：重复内容被计为不同语料；manifest 顺序不稳定；文件改变但 corpus hash 不变；同一 ID 内容替换无法检测。

完成标准：至少 25 个有区分度的 chunk、稳定 manifest 和可重复加载结果。未达到该条件时不得开始 Task 9 指标验收。

### Task 3：扩展知识库读取边界

主要文件：

- `app/ports/runtime.py`
- `app/services/vector_store.py`
- `scripts/load_knowledge.py`
- `tests/test_vector_store.py`
- `tests/test_vector_store_pgvector.py`
- `tests/test_runtime_ports.py`
- 新建共享 v1/v2 knowledge repository test fake

执行：

1. 先解决双 Protocol：以 `app/ports/runtime.py::KnowledgeRepository` 为唯一所有者，增加 `get_by_ids()`；`app/services/vector_store.py::KnowledgeSearchStore` 暂时作为兼容 re-export，避免调用方一次性断裂。
2. 在 `PgVectorKnowledgeStore` 实现批量 `get_by_ids(ids, expected_hashes=None)`，保持输入顺序、去重，并分别报告 `found`、`missing` 和 `version_mismatch`。
3. 迁移所有测试 Fake/Mock。影响面至少包括 `tests/test_agents.py`、`tests/eval_support.py`、`tests/test_expert_evaluator.py`、`tests/test_report_api.py`、`tests/test_report_tasks.py`、`tests/test_report_worker.py` 和 `tests/test_runtime_ports.py`；共享 fake 应提取到测试 helper，减少二十余个局部实现继续漂移。
4. legacy v1 测试 fake 可以保留 `search()` 行为，但任何声明实现新 Protocol 或进入 v2 路径的 fake 必须实现 `get_by_ids()`；运行时 Protocol 检查增加覆盖。
5. 搜索增加最小相关度阈值、稳定排序和重复 ID 去重。
6. 返回检索耗时、filters 和命中 ID，但不记录 DSN 或 embedding。
7. 保持现有逻辑 chunk ID 稳定，同时使用 `content_sha256` 区分内容版本。

红灯测试：两个 Protocol 能力不一致；v2 fake 缺少 `get_by_ids()`；未知 ID 返回错误内容；相同分数排序不稳定；低相关结果被当作有效证据；内容哈希不匹配仍被静默使用；检索错误暴露 DSN。

完成标准：Protocol、生产实现和 v2 测试替身能力一致；按 ID 可重复读取并验证内容版本，搜索结果稳定、可过滤、可审计。

### Task 4：实现 Grounded Knowledge Agent

主要文件：

- `app/agents/knowledge.py`
- `app/services/llm.py`
- `app/services/prep.py`
- `tests/test_prep_service.py`
- 新建 `tests/test_grounded_knowledge_agent.py`

执行流程：

1. 构建 `RoleProfile`。
2. 对主题执行 Top-K 检索，形成受信任候选池。
3. 将候选证据的 ID、标题和安全摘要传给计划生成器。
4. 计划生成后，由后端将每道题绑定到候选池中的 evidence ID。
5. 拒绝 LLM 生成的未知 ID；无法修复时使用确定性主题匹配或明确降级。
6. 限制每题 1 至 3 个 evidence ID，避免上下文无边界增长。
7. 将异常边界拆开：LLM 计划生成失败才进入现有通用 fallback；知识检索失败必须在 `KnowledgeAgent` 内捕获，并保留已经生成的 Provider plan，只把 prep context 标记为 `degraded`。
8. `/api/prep` 外层继续处理输入和计划生成错误，但不得因为 pgvector、embedding 或空知识表丢弃一份已经有效的计划。

兼容策略：计划生成接口增加可选 `knowledge_context`，现有 fake LLM 和旧调用方保持可用；无知识上下文时行为与 Stage 41 一致。

红灯测试：题目引用候选池外 ID；所有题目共享无关证据；知识不可用时 `/api/prep` 返回 500 或退回无关通用题；检索异常吞掉已有 Provider plan；模型输出改变后端证据分数。

完成标准：正常 seeded 场景中每道题都有合法 evidence ID，降级场景无伪造引用。

### Task 5：Prep API、持久化和 UI

主要文件：

- `app/api/routes.py`
- `app/static/prep.js`
- `app/test1.html`
- `tests/test_api.py`
- `tests/test_static_report_ui.py`
- `tests/browser/local-v1.spec.js`

执行：

1. Prep 返回 topic、题目、证据标题、来源类型和安全摘要。
2. UI 在每道题下展示“提问依据”，不展示 benchmark 答案全文和内部 prompt。
3. 显示 `completed`、`empty`、`degraded` 三种知识状态。
4. 创建 Session 后保存完整 evidence ID 绑定。
5. 刷新和服务重启后证据标题与绑定保持一致。

红灯测试：移动端证据文本溢出；刷新后绑定消失；降级状态被显示为正常命中；API 返回知识全文。

完成标准：桌面和移动端均能理解提问依据，且不泄露参考答案。

### Task 6：Examiner 继承题目证据

主要文件：

- `app/services/prep_context.py`
- `app/graphs/interview_graph.py`
- `app/services/llm.py`
- `tests/test_prep_context.py`
- `tests/test_interview_graph.py`

执行：

1. 根据当前 `question_id` 读取该题 evidence IDs。
2. 通过 `get_by_ids()` 构建内部 `knowledge_agent` context。
3. 明确区分 candidate message、knowledge evidence 和 question prompt。
4. 追问不得使用其他题目的 evidence ID。
5. 无绑定、旧计划或知识库不可用时使用 Stage 41 hint，并记录降级原因。
6. 使用统一的 `KnowledgeBindingResolver` 路由：`schema_version=v2` 且绑定完整时只能走 `get_by_ids()`；v1/legacy plan 才能走现有文本 hint；v2 版本或哈希不匹配不得悄悄改走语义搜索。

红灯测试：q1 追问拿到 q2 证据；证据内容被当作候选人回答；刷新后 evidence IDs 改变；异常导致回答接口失败。

完成标准：追问上下文只包含当前题证据，失败时不影响完成面试。

### Task 7：Reviewer 和报告复用相同证据

主要文件：

- `app/services/evaluator_ext.py`
- `app/services/report_microbatch.py`
- `app/services/report_tasks.py`
- `app/services/report_trace.py`
- `tests/test_report_evaluator.py`
- `tests/test_report_microbatch.py`
- `tests/test_report_tasks.py`

执行：

1. 从 `evaluator_ext.py` 抽出显式路由函数，输入 plan schema version、question ID、binding snapshot 和 repository，输出引用及 `retrieval_path`。
2. `schema_version=v2` 且绑定有效时只调用 `get_by_ids()`，并断言 `search()` 未被调用；这不是“优先”策略，而是强约束。
3. v1/legacy plan 才允许保持当前 `evaluator_ext.py` 的逐题 `search()`，并记录 `legacy_semantic_search`。v2 的 missing/hash mismatch 使用明确降级结果，不得伪装成原证据连续。
4. 路由分支分别建立 spy fake，测试精确调用次数：v2 `get_by_ids=1/search=0`，v1 `search=1/get_by_ids=0`。
5. QuestionEvaluationRecord、Report trace 和最终引用保留相同 chunk IDs 与 content hashes。
6. Provider 返回候选集合外引用时继续由后端丢弃。
7. Stage 40 后端规则评分保持不变。

红灯测试：v2 plan 仍调用 `search()`；v1/v2 路由识别错误；Prep 绑定 A/B，报告引用 C；微批重跑改变证据集合；未知 ID 进入 PDF；同 ID 不同哈希被当作相同证据。

完成标准：Prep、追问、逐题评估和最终报告的 evidence ID、内容哈希和 retrieval path 可逐题对齐。

### Task 8：降级、隐私和可观测性

主要文件：

- 新建 `app/services/knowledge_trace.py`，实现独立 `KnowledgeTraceRecorder`
- `app/services/runtime.py`
- `app/api/routes.py`
- 对应单元和 API 测试

必须覆盖：

- PostgreSQL 不可用。
- vector 扩展缺失。
- 空知识表。
- embedding 维度不匹配。
- Top-K 结果低于阈值。
- chunk ID 在 Prep 后被删除。
- chunk ID 未变但 `content_sha256` 已变化。
- corpus manifest 在 Prep 后变化。
- 旧 session 没有 v2 evidence 字段。

每次降级必须输出稳定错误码或状态，不输出 DSN、API Key、完整简历、embedding 或 Provider 原始响应。

内容版本策略：Reviewer 按 ID 读取时必须同时校验 Prep 保存的 `content_sha256`。不匹配时标记 `evidence_version_mismatch`，不使用新内容冒充旧证据，也不静默执行语义搜索。面试仍可完成，报告以明确降级路径生成。corpus 更新后的新 session 使用新 manifest；旧 session 保留原 snapshot 以供审计。

完成标准：知识链路失败不会阻止完成 Local V1 面试，但 UI、trace 和报告明确标记降级，不伪造证据；重启、删除和内容替换三种漂移均有测试。

### Task 9：建立 Knowledge Retrieval 离线评测

新增：

- `tests/golden/knowledge_retrieval_v1.json`
- `app/services/knowledge_eval_dataset.py`
- `app/services/knowledge_eval_metrics.py`
- `scripts/evaluate_knowledge_retrieval.py`
- 对应单元测试

Task 9 只能在 Task 2B 的 25 个以上区分性 chunk 通过人工审核后开始指标验收。数据集至少包含 30 个 query：20 个相关 query、5 个同义/弱关键词 query、5 个明确无关的 negative query，并覆盖所有主要内容类别：

- Redis 一致性与缓存击穿。
- MySQL 索引与事务。
- Kafka 投递语义。
- FastAPI 工程实践。
- 服务扩缩容与任务队列。
- 同义表达、弱关键词和无关查询。

指标：

- `hit_rate_at_3 >= 0.90`
- `mean_reciprocal_rank >= 0.75`
- 正常 seeded 场景 `question_evidence_binding_rate = 1.00`
- `evidence_continuity_rate = 1.00`
- `invalid_reference_rate = 0`
- negative query `false_positive_rate <= 0.20` 作为首个语料版本的 RC 门禁；只有至少 5 个独立 negative query，且阈值策略已固定时才计算该指标
- warm local retrieval `p95 <= 1500 ms`，模型首次加载时间单独记录，不混入检索 p95

离线评测不得调用真实 LLM。真实模型只验证出题与证据绑定是否遵守契约，不参与检索相关性指标计算。若语料仍只有 10 个 chunk，Task 9 状态只能是 `BLOCKED_CORPUS_TOO_SMALL`，不得通过重复 query 制造覆盖率。

### Task 10：浏览器 RC 与正式验收

自动化场景：

1. Prep 显示检索主题和每题证据依据。
2. 开始面试后 evidence IDs 持久化。
3. 回答、SSE 追问、刷新、409 恢复不改变证据绑定。
4. 报告进度展示 `bound_evidence_reuse` 或明确 fallback。
5. Report Detail 和 PDF 引用相同 evidence IDs。
6. 空知识库展示降级但仍可完成面试。
7. 桌面与移动端无溢出、遮挡或 benchmark 答案泄露。

真实模型 smoke 使用专用 JD/简历，至少完成两道题和一次追问，保存脱敏 session ID、模型、时间、证据 ID 连续性和截图。

正式制品白名单：

```text
reports/stage42-acceptance/<run-id>/
  manifest.json
  metrics.json
  report.md
  retrieval-cases/**
  browser/**
```

探索性运行目录默认忽略。正式目录生成相对路径、大小和 SHA-256 清单，并执行密钥、DSN、绝对路径和个人信息扫描。

## 7. 红绿灯开发规则

每个任务严格执行：

1. 先增加能说明业务风险的失败测试。
2. 保存红灯输出，确认失败原因是缺失行为而不是环境问题。
3. 实现最小代码使测试转绿。
4. 跑相邻模块回归。
5. PostgreSQL 测试使用唯一表名；浏览器测试使用隔离应用和确定性 LLM。
6. 每完成一个数据契约变更，立即验证旧 session 恢复。
7. 真实模型测试不替代离线测试，不允许通过重试掩盖稳定代码错误。

禁止先写完整实现再补测试。

## 8. 并行执行轨道

并行只发生在依赖真实独立的工作之间，不把“提前设计”写成“可并行实现”。

### 42A 执行顺序

1. Task 1 契约先单独完成并冻结。
2. Task 2 岗位画像与 Task 2B 语料扩充可以并行；两者都不修改 vector repository 接口。
3. Task 3 必须等待 Task 1 和 Task 2B 的 manifest 契约稳定。
4. Task 4 必须等待 Task 2、Task 3。
5. Task 5 必须等待 Task 4 public payload 稳定。
6. Task 9 的 dataset schema/指标单元测试可以在 Task 1 后开始，但相关 chunk 标注和正式指标只能在 Task 2B、Task 3 完成后执行。
7. Task 1-5、Task 9 和 42A Playwright 全部通过后，形成独立 `42A PASS` 记录。

### 42B 执行顺序

Task 6-8 的产品代码只能在 `42A PASS` 后开始。Task 6 先提供共享 `KnowledgeBindingResolver`；随后 Task 7 和 Task 8 可在该路由契约上并行实现。Task 10 等 Task 6-8 全部完成后执行。

在 42A 期间允许提前编写 42B 的验收用例清单和 fixture 设计文档，但不允许合并 Examiner/Reviewer 产品代码。

```text
Stage 41 release prerequisite (outside Stage 42)
  -> Task 1
      -> [Task 2 || Task 2B]
          -> Task 3 -> Task 4 -> Task 5
                   \-> Task 9 --------/
          -> 42A PASS
              -> Task 6
                  -> [Task 7 || Task 8]
                      -> Task 10 -> 42B PASS
```

## 9. 提交边界

建议提交：

1. `feat: add versioned knowledge evidence contracts`
2. `feat: add deterministic role profile and retrieval queries`
3. `feat: bind prep questions to pgvector evidence`
4. `feat: reuse bound evidence across followup and report`
5. `test: add knowledge retrieval and continuity gates`
6. `docs: record stage 42 knowledge agent acceptance`

每个提交必须独立通过相关测试，不提交模型缓存、数据库数据目录、IDE 配置和探索产物。

## 10. 发布门禁

Stage 42 只有同时满足以下条件才能标记 `PASS`：

- Stage 41 全部门禁继续通过。
- 新旧 `InterviewPlan` 和 PostgreSQL session 均可恢复。
- 知识 manifest 与数据库 chunk ID、`content_sha256` 和 corpus manifest hash 一致。
- 正式评测语料不少于 25 个有区分度的 chunk；不足时不得计算并宣称检索门禁通过。
- 离线检索全部指标通过。
- 正常 seeded 场景每题均绑定合法 evidence ID。
- Prep、追问、逐题评估、最终报告和 PDF 的 evidence continuity 为 100%。
- Provider 无法注入候选集合外引用。
- 空知识库和检索故障不产生虚假证据，也不阻止完成面试。
- 桌面、移动端和错误路径 Playwright 通过。
- 新鲜真实模型浏览器 smoke 通过。
- 正式 artifacts 通过哈希、密钥、DSN、绝对路径和个人信息扫描。
- 没有未关闭 P0/P1；非阻塞 UI 优化进入后续 backlog。
- 干净 clone 可按 runbook 复现完整流程。

建议标签：`local-v1.1.0-rc1`。最终 `local-v1.1.0` 不允许使用过期真实模型 smoke。

## 11. 时间估算

在一名开发者、现有基础设施可用的前提下：

| 阶段 | 预计工作日 |
| --- | ---: |
| Task 1-2：契约、岗位画像和查询 | 2-3 |
| Task 2B：语料扩充、人工审核和 manifest | 2-3 |
| Task 3-5：Repository、Grounded Prep 和 UI | 3-4 |
| Task 9：离线评测与 42A RC | 2-3 |
| Task 6-8：追问、报告路由、版本漂移和降级 | 3-4 |
| Task 10：浏览器/真实模型 RC 与文档 | 1-2 |
| Stage 42 合计 | 13-19 |

若知识内容不足导致评测覆盖无法建立，应先补充高质量知识 chunk，不降低指标门禁，也不提前引入重排模型掩盖数据问题。

## 12. 完成后的下一步

Stage 42 通过后，根据 trace 和离线指标再决定 Stage 43：

- 若主要问题是召回不足，优先扩充知识内容和查询策略。
- 若 Top-K 命中稳定但排序差，再评估轻量 reranker。
- 若证据连续性稳定且用户确实需要跨进程实时状态，再评估 Redis checkpoint 或 WebSocket。

在 Stage 42 数据证明之前，不启动这些架构升级。
