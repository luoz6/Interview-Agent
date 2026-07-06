# 面试智能体接口需求文档

## 1. 文档说明

本文档基于当前项目代码与 `app` 目录下 4 个 HTML 原型页生成，用于前后端联调、接口验收和前端页面替换排期。下一阶段前端目标是不再保留 `app/static/index.html` 作为运行入口，而是按四个原型页拆分为准备、面试、报告生成、报告详情四个页面。

分析范围：

| 来源 | 说明 |
| --- | --- |
| `app/api/routes.py` | 当前 FastAPI 对外接口契约 |
| `app/services/prep.py` | 面试计划与题目模型 |
| `app/services/session.py`、`app/graphs/interview_graph.py` | 会话状态、答题流转、追问和结束规则 |
| `app/services/report.py`、`app/services/report_contract.py` | 报告、评分、证据引用和进度模型 |
| `app/services/report_jobs.py`、`app/services/runtime.py` | 报告任务队列与运行时存储设计 |
| `app/test4.html` | 面试准备页原型，目标承载 JD/简历输入、草稿、标签、计划预览和开始面试 |
| `app/test3.html` | 模拟面试进行页原型，目标承载对话、流式追问、跳题、结束、题目导航和会话快照 |
| `app/test2.html` | 报告生成中页原型，目标承载报告进度、事件时间线、RAG 摘要和报告轮询 |
| `app/test1.html` | 结构化面评报告页原型，目标承载报告详情、维度分、逐题反馈、证据和 PDF 下载 |
| `app/static/index.html`、`app/static/app.js` | 旧单页运行界面；下一阶段应被四个原型页替换，不再作为目标前端入口 |

当前后端已实现核心闭环：生成面试计划、创建面试会话、查询会话快照、提交回答、流式追问、跳题、主动结束面试、面试结束后生成报告、查询报告进度与结果、下载 PDF 报告，并已提供匿名草稿保存与恢复、报告中心列表与报告回看接口。

本文默认以本机 `localhost` 单机部署、单用户使用为前提，不规划账号体系、用户隔离或跨设备同步。

### 1.1 Local V1 运行状态

截至 Stage 19，四个 HTML 原型页已经作为运行时页面接入 FastAPI 页面路由，旧 `app/static/index.html`、`app/static/app.js` 和 `app/static/styles.css` 不再作为运行契约。当前推荐本机运行配置为 PostgreSQL `127.0.0.1:5432/interview`、账号密码 `postgres/postgres`、pgvector 表 `knowledge_chunks`、DeepSeek 兼容 OpenAI API。

LLM 调用策略为 structured output 优先；当 DeepSeek 兼容接口拒绝 `response_format` 时，题目计划和报告生成都会走 raw JSON fallback，再通过 Pydantic 模型或报告归一化层校验。`/api/prep` 在 LLM 完全不可用时仍返回本地兜底计划，避免准备页直接 500。

## 2. 接口总览

基础约定：

| 项目 | 约定 |
| --- | --- |
| Base URL | 同源服务，默认 `http://127.0.0.1:8000` |
| 部署模式 | 本地单机部署，默认单用户使用 |
| API 前缀 | `/api` |
| 请求格式 | `application/json` |
| 普通响应格式 | JSON |
| 流式响应格式 | `text/event-stream` |
| 鉴权 | 当前不做用户登录和权限隔离；如后续暴露到公网或多用户环境，需重新设计 |
| 会话标识 | `session_id`，当前内存模式下为 UUID 字符串 |

当前已实现接口：

| 方法 | 路径 | 用途 | 页面映射 |
| --- | --- | --- | --- |
| `GET` | `/api/health` | 健康检查 | 运维检查 |
| `POST` | `/api/prep` | 根据 JD 和简历生成计划响应，顶层包含 `title`、`questions`、`job_tags` | `app/test4.html` 计划预览 |
| `POST` | `/api/interview-drafts` | 匿名保存 JD 和简历草稿 | `app/test4.html` 保存草稿 |
| `GET` | `/api/interview-drafts/{draft_id}` | 匿名恢复 JD 和简历草稿 | `app/test4.html` 恢复草稿 |
| `POST` | `/api/interviews` | 创建面试会话并返回第一题 | `app/test4.html` 开始面试，随后跳转 `app/test3.html` |
| `GET` | `/api/interviews/{session_id}` | 查询会话详情、进度、题目导航和消息记录 | `app/test3.html` 进度区和题目导航 |
| `POST` | `/api/interviews/{session_id}/answer` | 提交回答并返回下一轮状态 | `app/test3.html` 非流式提交回答 |
| `POST` | `/api/interviews/{session_id}/answer/stream` | 提交流式回答，SSE 返回追问片段和最终状态 | `app/test3.html` 推荐使用的提交方式 |
| `POST` | `/api/interviews/{session_id}/skip` | 跳到下一题 | `app/test3.html` 下一题按钮 |
| `POST` | `/api/interviews/{session_id}/finish` | 主动结束面试并触发报告生成 | `app/test3.html` 结束面试，随后跳转 `app/test2.html` |
| `GET` | `/api/interviews/{session_id}/report` | 查询报告生成状态或完整报告 | `app/test2.html` 轮询完成状态；`app/test1.html` 渲染完整报告 |
| `GET` | `/api/interviews/{session_id}/report/progress` | 查询更详细报告任务进度、时间线和 `report_job_id` | `app/test2.html` 报告生成页 |
| `GET` | `/api/interviews/{session_id}/report.pdf` | 下载已完成的 PDF 报告 | `app/test1.html` 下载按钮 |
| `GET` | `/api/reports` | 查询本机报告中心列表，支持状态过滤和数量限制 | 可作为 `app/test1.html` 返回报告中心入口的后续页面 |

当前已实现的 HTML 页面路由：

| 方法 | 路径 | 用途 | 来源 |
| --- | --- | --- | --- |
| `GET` | `/` 或 `/prep` | 返回面试准备页，替代旧 `app/static/index.html` 入口 | `app/test4.html` |
| `GET` | `/interview?session_id=...` | 返回模拟面试页，从查询参数读取会话 ID | `app/test3.html` |
| `GET` | `/report-processing?session_id=...` | 返回报告生成中页，从查询参数读取会话 ID 并轮询进度 | `app/test2.html` |
| `GET` | `/report-detail?session_id=...` | 返回结构化报告详情页，从查询参数读取会话 ID 并拉取报告 | `app/test1.html` |

这些是 HTML 页面路由，不是新的 JSON API。登录、用户隔离和跨设备同步不纳入本机部署范围。

## 3. 页面流程与接口关系

| 流程步骤 | 页面原型 | 当前可用接口 | 需要展示的数据 |
| --- | --- | --- | --- |
| 1. 面试准备 | `app/test4.html` | `POST /api/prep`、`POST /api/interview-drafts`、`GET /api/interview-drafts/{draft_id}`、`POST /api/interviews` | JD、简历、自动标签、计划标题、题目数量、题目列表、考察点 |
| 2. 模拟面试 | `app/test3.html` | `GET /api/interviews/{session_id}`、`POST /api/interviews/{session_id}/answer`、`POST /api/interviews/{session_id}/answer/stream`、`POST /api/interviews/{session_id}/skip`、`POST /api/interviews/{session_id}/finish` | 当前题目、消息列表、追问、状态、题号进度、题目状态、识别标签 |
| 3. 报告生成 | `app/test2.html` | `GET /api/interviews/{session_id}/report`、`GET /api/interviews/{session_id}/report/progress` | processing 状态、阶段、百分比、当前题目、生成提示、任务 ID、事件时间线、RAG 摘要 |
| 4. 面试复盘 | `app/test1.html` | `GET /api/interviews/{session_id}/report`、`GET /api/interviews/{session_id}/report.pdf` | 总分、五维能力分、亮点、逐题反馈、RAG 证据、兜底状态、PDF 下载 |

关键差异：

| HTML 期望 | 当前代码状态 | 建议 |
| --- | --- | --- |
| 四个原型页分别作为运行入口 | 当前 FastAPI `/` 仍返回旧 `app/static/index.html` | 下一阶段用四个页面路由替代旧单页，不再保留 `app/static/index.html` 作为运行入口 |
| 准备页显示自动识别岗位标签 | `POST /api/prep` 已在响应 wrapper 顶层返回 `job_tags`；`GET /api/interviews/{session_id}` 也返回 `job_tags` | 前端从响应顶层读取 `job_tags`，不要把它当作 `InterviewPlan` 模型字段 |
| 面试页显示题号、已完成题数、题目导航 | 已通过 `GET /api/interviews/{session_id}` 返回会话快照 | 前端刷新或答题后调用会话详情接口 |
| 面试页有“下一题”按钮 | 已通过 `POST /api/interviews/{session_id}/skip` 支持显式跳题 | 已结束会话跳题保持幂等 |
| 报告生成页显示 queued、retrieving、analyzing、aggregating、completed 时间线 | 已通过 `GET /api/interviews/{session_id}/report/progress` 返回响应层阶段、事件和 RAG 摘要 | `queued` 仅作为进度端点响应层阶段，不写入 `ReportProgress` 模型 |
| 报告页显示百分位、报告标签、完成时间、PDF 下载 | 当前 `InterviewReport` 不包含这些字段 | 扩展报告模型或新增报告元数据/PDF 接口 |

## 4. 数据模型

### 4.1 PrepRequest

用于生成计划和创建面试。

```json
{
  "job_description": "后端开发工程师，要求熟悉 Python、FastAPI、Redis...",
  "resume_text": "参与多个 Python 后端项目，使用 FastAPI 构建 RESTful API..."
}
```

字段要求：

| 字段 | 类型 | 必填 | 当前后端校验 | 页面期望 |
| --- | --- | --- | --- | --- |
| `job_description` | string | 是 | 非空，否则 `400` | 字数计数，原型中出现 1000 或 5000 上限 |
| `resume_text` | string | 是 | 非空，否则 `400` | 字数计数，原型中出现 1000 或 5000 上限 |

建议后端补齐长度校验，并统一前端与后端上限。

### 4.2 InterviewQuestion

```json
{
  "id": "q1",
  "kind": "technical",
  "prompt": "请简述 Redis 的数据结构及其应用场景。",
  "focus": "Redis 基础与场景选择"
}
```

字段说明：

| 字段 | 类型 | 枚举或约束 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 非空 | 题目唯一标识 |
| `kind` | string | `project`、`technical`、`system-design`、`behavioral` | 题目类型 |
| `prompt` | string | 非空 | 面试官展示的问题 |
| `focus` | string | 非空 | 本题考察重点 |

### 4.3 InterviewPlan

```json
{
  "title": "Backend mock interview",
  "questions": [
    {
      "id": "q1",
      "kind": "project",
      "prompt": "介绍一个项目。",
      "focus": "项目深度"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | string | 面试计划标题 |
| `questions` | `InterviewQuestion[]` | 面试题列表 |

### 4.4 InterviewTurn

用于开始面试和提交回答后的响应。

```json
{
  "session_id": "7d7d7d7d-0000-4000-8000-000000000000",
  "current_question": {
    "id": "q1",
    "kind": "technical",
    "prompt": "解释 Redis 缓存失效。",
    "focus": "Redis reliability"
  },
  "follow_up": null,
  "status": "active"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | string | 面试会话 ID |
| `current_question` | `InterviewQuestion \| null` | 当前题目，面试结束时为 `null` |
| `follow_up` | `string \| null` | 追问或结束提示 |
| `status` | string | 当前公开值为 `active` 或 `finished` |

### 4.5 AnswerRequest

```json
{
  "answer": "我使用 Redis 做热点数据缓存，并通过逻辑过期防止击穿。"
}
```

字段要求：

| 字段 | 类型 | 必填 | 当前后端校验 |
| --- | --- | --- | --- |
| `answer` | string | 是 | 非空，否则 `400` |

### 4.6 ReportProgress

生成中的报告进度。

```json
{
  "stage": "analyzing",
  "percent": 60,
  "message": "Analyzing question-level dimension scores.",
  "current_question_id": "q1"
}
```

字段说明：

| 字段 | 类型 | 枚举或约束 | 说明 |
| --- | --- | --- | --- |
| `stage` | string | `retrieving`、`analyzing`、`aggregating`、`completed` | 当前公开阶段 |
| `percent` | integer | 0 到 100 | 进度百分比 |
| `message` | string | 非空 | 给前端展示的进度说明 |
| `current_question_id` | `string \| null` | 可空 | 当前正在分析的题目 |

`ReportProgress` 模型不包含 `queued`。`queued` 只出现在 `GET /api/interviews/{session_id}/report/progress` 的响应层，用于表示报告任务尚未写入 `ReportRecord.progress`。

### 4.7 InterviewReport

完成后的结构化报告。

```json
{
  "session_id": "7d7d7d7d-0000-4000-8000-000000000000",
  "overall_score": 81,
  "overall_dimension_scores": {
    "breadth": 80,
    "depth": 78,
    "architecture": 82,
    "engineering": 84,
    "communication": 81
  },
  "summary": "候选人项目表达清晰，能说明关键技术取舍。",
  "highlights": ["Explained tradeoffs"],
  "feedbacks": [
    {
      "question_id": "q1",
      "question_text": "Introduce a backend project.",
      "user_answer": "The candidate built a backend cache service.",
      "score": 81,
      "dimension_scores": {
        "breadth": 81,
        "depth": 81,
        "architecture": 81,
        "engineering": 81,
        "communication": 81
      },
      "rationale": "The answer covered implementation tradeoffs clearly.",
      "critique": "Needs stronger business metrics.",
      "better_answer": "I reduced p95 latency using cache-aside Redis.",
      "references": []
    }
  ],
  "status": "completed",
  "is_fallback": false
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | string | 会话 ID |
| `overall_score` | integer | 总分，0 到 100 |
| `overall_dimension_scores` | object | 总体维度分 |
| `summary` | string | 总结 |
| `highlights` | string[] | 亮点，1 到 3 条 |
| `feedbacks` | `InterviewFeedback[]` | 逐题反馈 |
| `status` | string | 固定为 `completed` |
| `is_fallback` | boolean | 是否为兜底报告 |

当前维度分字段：

| 字段 | 页面可映射含义 |
| --- | --- |
| `breadth` | 知识广度 |
| `depth` | 技术深度 |
| `architecture` | 系统设计 |
| `engineering` | 工程实践 |
| `communication` | 表达沟通 |

HTML 复盘页使用了“技术能力、系统设计、表达沟通、项目深度”四维雷达图。当前后端是五维模型，运行页 `app/static/app.js` 已将 `breadth`、`depth`、`architecture`、`engineering`、`communication` 映射为中文标签展示；如果后续恢复雷达图，需要继续统一维度定义。

### 4.8 FeedbackReference

```json
{
  "chunk_id": "redis-1",
  "title": "Redis cache consistency",
  "source_type": "theory",
  "excerpt": "Delete cache after database writes."
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chunk_id` | string | 知识库片段 ID |
| `title` | string | 引用标题 |
| `source_type` | string | 来源类型；当前已知值包括 `theory`、`expert_benchmark`、`answer`、`reference` |
| `excerpt` | string | 引用摘要 |

## 5. 已实现接口详情

### 5.1 GET `/api/health`

用途：服务健康检查。

成功响应：

```json
{
  "status": "ok"
}
```

### 5.2 POST `/api/prep`

用途：只生成面试计划，不创建会话。

请求体：`PrepRequest`

成功响应：响应 wrapper 顶层返回 `title`、`questions` 和 `job_tags`。其中 `title`、`questions` 对应原有 `InterviewPlan` 字段；`job_tags` 是响应 wrapper 的顶层字段，不属于 `InterviewPlan` Pydantic 模型本身。

```json
{
  "title": "Backend mock interview",
  "questions": [
    {
      "id": "q1",
      "kind": "project",
      "prompt": "请从简历中选择一个最能代表你能力的项目...",
      "focus": "项目表达"
    }
  ],
  "job_tags": ["FastAPI", "Redis"]
}
```

运行时依赖：

| 依赖 | 当前行为 |
| --- | --- |
| 会话存储 | 当前 `/api/prep` 不创建 session，也不依赖 `get_session_store()`。 |
| LLM 配置 | `prepare_interview(..., llm=None)` 会尝试构造默认 LLM；如果 LLM 配置缺失或调用失败，会返回兜底计划。 |
| DeepSeek 兼容性 | `OpenAIInterviewLLM.generate_plan()` 先尝试 structured output；如果 provider 拒绝 `response_format`，会自动改用 raw JSON fallback 并校验为 `InterviewPlan`。 |

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `400` | JD 或简历为空 | `{"detail":"job_description is required"}` 或 `{"detail":"resume_text is required"}` |
| `422` | 请求 JSON 缺少字段或类型错误 | FastAPI/Pydantic 默认校验错误 |

业务规则：

| 规则 | 当前行为 |
| --- | --- |
| LLM 正常 | 使用 LLM 生成结构化计划 |
| LLM 缺失或异常 | 返回固定兜底计划，不直接 500 |
| 空 JD/简历 | 不走兜底，直接 `400` |

### 5.3 POST `/api/interviews`

用途：创建面试会话，生成计划并返回第一轮状态。

请求体：`PrepRequest`

成功响应：`InterviewTurn`

```json
{
  "session_id": "uuid",
  "current_question": {
    "id": "q1",
    "kind": "project",
    "prompt": "请从简历中选择一个最能代表你能力的项目...",
    "focus": "项目表达"
  },
  "follow_up": null,
  "status": "active"
}
```

业务规则：

| 规则 | 当前行为 |
| --- | --- |
| 会话创建 | `session_id` 使用 UUID |
| 状态初始化 | 有题目时为 `active` |
| 消息初始化 | 第一题会写入会话消息列表 |
| 岗位标签 | 后端内部从 JD 抽取 `job_tags`，可通过会话快照接口读取 |

错误响应同 `/api/prep`。

### 5.4 POST `/api/interviews/{session_id}/answer`

用途：提交候选人回答，返回追问、下一题或结束状态。

请求体：`AnswerRequest`

成功响应：`InterviewTurn`

业务规则：

| 场景 | 当前行为 |
| --- | --- |
| 当前题第一次回答 | 通常返回 `follow_up`，状态仍为 `active` |
| 同一题第二次回答 | 切换到下一题，`follow_up` 为 `null` |
| 已完成最后一题 | 返回 `status=finished`，`current_question=null`，并触发报告生成 |
| 报告任务存储可用 | 入队 PostgreSQL 报告任务 |
| 报告任务存储不可用 | 回退到 FastAPI BackgroundTasks |

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `400` | 回答为空 | `{"detail":"answer is required"}` |
| `404` | 会话不存在 | `{"detail":"session not found"}` |
| `422` | 请求 JSON 缺少字段或类型错误 | FastAPI/Pydantic 默认校验错误 |

注意：会话不存在时，答题接口和报告查询接口均返回 `404`。

### 5.5 POST `/api/interviews/{session_id}/answer/stream`

用途：提交回答并通过 SSE 流式返回追问文本。

请求体：`AnswerRequest`

响应头：

| Header | 值 |
| --- | --- |
| `Content-Type` | `text/event-stream` |
| `Cache-Control` | `no-cache` |
| `Connection` | `keep-alive` |
| `X-Accel-Buffering` | `no` |

事件格式：

```text
event: chunk
data: {"delta":"请继续说明"}

event: chunk
data: {"delta":"缓存失效时如何保护数据库。"}

event: done
data: {"session_id":"uuid","current_question":null,"follow_up":"本次模拟面试已结束。","status":"finished"}
```

事件说明：

| 事件 | 说明 |
| --- | --- |
| `chunk` | 追问文本片段，仅当本轮动作为追问时出现 |
| `done` | 最终回合状态，必需出现 |
| `error` | 流式处理过程中的异常，数据格式为 `{"detail":"..."}` |

非 SSE 错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `400` | 回答为空 | `{"detail":"answer is required"}` |
| `404` | 会话不存在 | `{"detail":"session not found"}` |
| `422` | 请求 JSON 缺少字段或类型错误 | FastAPI/Pydantic 默认校验错误 |

注意：这些错误发生在 `StreamingResponse` 创建前，响应体是普通 JSON，不是 SSE `error` 事件。SSE `error` 事件只覆盖流式生成过程中发生的异常。

客户端要求：

| 要求 | 说明 |
| --- | --- |
| 必须支持无 `chunk` 的情况 | 切到下一题或结束时可能只返回 `done` |
| 必须解析 `done.status` | 若为 `finished`，停止答题并开始轮询报告 |
| 必须处理 `error` | 展示错误并允许用户重试 |

### 5.6 GET `/api/interviews/{session_id}/report`

用途：查询报告生成状态或完整面评报告。

响应分支：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `200` | 报告已完成 | `InterviewReport` |
| `202` | 报告生成中 | `{"status":"processing","progress":ReportProgress|null}` |
| `404` | 会话不存在 | `{"detail":"session not found"}` |
| `404` | 面试未结束 | `{"detail":"interview is not finished"}` |
| `500` | 报告生成失败 | `{"detail":"错误信息"}` |

生成中响应示例：

```json
{
  "status": "processing",
  "progress": {
    "stage": "retrieving",
    "percent": 20,
    "message": "Retrieving role-specific knowledge references.",
    "current_question_id": null
  }
}
```

轮询要求：

| 项目 | 建议 |
| --- | --- |
| 轮询间隔 | 当前静态页为 3 秒 |
| 终止条件 | 收到 `200`、`500` 或用户离开页面 |
| 生成中 UI | 使用 `progress.percent` 更新进度条，使用 `progress.message` 显示状态 |

失败文案映射：

| 后端 detail | 前端建议展示 |
| --- | --- |
| `pgvector knowledge store is unavailable` | Knowledge retrieval unavailable |
| 其他错误 | Report generation failed 或原始错误 |

### 5.7 POST `/api/interviews/{session_id}/finish`

用途：用户主动结束当前面试，并触发报告生成。

请求体：无业务字段；前端页面可发送空 JSON `{}`。

成功响应：`InterviewTurn`

```json
{
  "session_id": "uuid",
  "current_question": null,
  "follow_up": "本次模拟面试已结束。",
  "status": "finished"
}
```

业务规则：

| 场景 | 当前行为 |
| --- | --- |
| 会话处于 `active` | 后端将会话置为 `finished`，追加结束提示，并调度报告生成 |
| 会话已经 `finished` | 接口幂等返回 `finished`，不会重复追加结束提示 |
| 报告任务已存在 | 调度逻辑保持幂等，不重复创建处理中的报告记录 |

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `404` | 会话不存在 | `{"detail":"session not found"}` |

### 5.8 GET `/api/interviews/{session_id}`

用途：查询面试会话快照，用于刷新恢复、进度展示、题目导航、岗位标签展示和消息列表恢复。

成功响应：

```json
{
  "session_id": "uuid",
  "status": "active",
  "current_index": 1,
  "total_questions": 3,
  "completed_questions": 1,
  "job_tags": ["Redis", "FastAPI"],
  "current_question": {
    "id": "q2",
    "kind": "technical",
    "prompt": "如何处理缓存穿透？",
    "focus": "缓存可靠性"
  },
  "questions": [
    {
      "id": "q1",
      "kind": "project",
      "prompt": "介绍一个后端项目。",
      "focus": "项目表达",
      "state": "completed"
    },
    {
      "id": "q2",
      "kind": "technical",
      "prompt": "如何处理缓存穿透？",
      "focus": "缓存可靠性",
      "state": "current"
    }
  ],
  "messages": [
    {
      "role": "interviewer",
      "content": "介绍一个后端项目。",
      "question_id": "q1"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `current_index` | integer | 当前题下标，从 0 开始；结束后保持最终状态下标 |
| `total_questions` | integer | 题目总数 |
| `completed_questions` | integer | 已完成题数；会话结束时等于 `total_questions` |
| `job_tags` | string[] | 从 JD 中抽取的岗位标签 |
| `questions[].state` | string | `completed`、`current` 或 `pending` |
| `current_question` | `InterviewQuestion \| null` | 已结束会话为 `null` |
| `messages` | object[] | 会话消息记录，供页面恢复对话上下文 |

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `404` | 会话不存在 | `{"detail":"session not found"}` |

### 5.9 POST `/api/interviews/{session_id}/skip`

用途：跳过当前题，进入下一题；如果当前题是最后一题，则结束面试并触发报告生成。

请求体：无业务字段；前端页面可发送空 JSON `{}`。

成功响应：`InterviewTurn`

```json
{
  "session_id": "uuid",
  "current_question": {
    "id": "q2",
    "kind": "technical",
    "prompt": "如何处理缓存穿透？",
    "focus": "缓存可靠性"
  },
  "follow_up": null,
  "status": "active"
}
```

业务规则：

| 场景 | 当前行为 |
| --- | --- |
| 普通跳题 | 当前题标记为已跳过并进入下一题 |
| 跳过最后一题 | 返回 `status=finished`、`current_question=null`，并触发报告生成 |
| 会话已经 `finished` | 接口幂等返回 `finished`，不会重复推进状态 |

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `404` | 会话不存在 | `{"detail":"session not found"}` |

### 5.10 GET `/api/interviews/{session_id}/report/progress`

用途：查询报告生成详情，供报告生成页展示阶段、百分比、事件、RAG 配置摘要和后台任务 ID。

成功响应：

```json
{
  "session_id": "uuid",
  "report_job_id": "job_20260704_104218_a1d9e0",
  "status": "processing",
  "stage": "analyzing",
  "percent": 60,
  "message": "Analyzing question-level dimension scores.",
  "events": [
    {
      "stage": "queued",
      "message": "Waiting for report generation to start."
    },
    {
      "stage": "analyzing",
      "message": "Analyzing question-level dimension scores."
    }
  ],
  "rag": {
    "top_k": 5,
    "source_types": ["theory", "expert_benchmark"],
    "matched_chunks": null
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `report_job_id` | `string \| null` | 从报告任务表按 `session_id` 查询；当前运行模式没有配置任务表或任务不可用时为 `null` |
| `status` | string | `processing`、`completed` 或 `failed` |
| `stage` | string | 响应层阶段，允许 `queued`、`retrieving`、`analyzing`、`aggregating`、`completed`、`failed` |
| `percent` | integer | 0 到 100 |
| `events` | object[] | 用于前端时间线展示的阶段事件 |
| `rag` | object | 当前 RAG 检索配置或摘要 |

阶段说明：

| 阶段 | 说明 |
| --- | --- |
| `queued` | 报告等待生成，但尚无 `ReportRecord.progress`；仅存在于本接口响应层 |
| `retrieving` | 正在检索知识证据 |
| `analyzing` | 正在生成逐题评分或反馈 |
| `aggregating` | 正在聚合总体评分和总结 |
| `completed` | 报告已完成 |
| `failed` | 报告生成失败 |

注意：`queued` 不进入 `ReportProgress` 模型。`/report` 的 202 响应仍只返回 `ReportProgress|null`，更完整的任务 ID、事件和 RAG 摘要由 `/report/progress` 提供。

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `404` | 会话不存在 | `{"detail":"session not found"}` |
| `404` | 面试未结束 | `{"detail":"interview is not finished"}` |

### 5.11 POST `/api/interview-drafts`

用途：匿名保存面试准备页草稿，不要求用户登录。

请求体：

```json
{
  "job_description": "岗位 JD",
  "resume_text": "简历内容"
}
```

成功响应：

```json
{
  "draft_id": "draft_001",
  "job_description": "岗位 JD",
  "resume_text": "简历内容",
  "created_at": "2026-07-04T10:00:00+08:00",
  "updated_at": "2026-07-04T10:00:00+08:00"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `draft_id` | string | 匿名草稿 ID，前端应保存到 `localStorage` |
| `job_description` | string | 已保存的 JD 内容 |
| `resume_text` | string | 已保存的简历内容 |
| `created_at` | string | 草稿创建时间，ISO 8601 字符串 |
| `updated_at` | string | 草稿最后更新时间，ISO 8601 字符串 |

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `400` | JD 或简历为空 | `{"detail":"job_description is required"}` 或 `{"detail":"resume_text is required"}` |
| `422` | 请求 JSON 缺少字段或类型错误 | FastAPI/Pydantic 默认校验错误 |

业务规则：

| 规则 | 当前行为 |
| --- | --- |
| 部署模式 | 当前面向本地单机部署，不做用户登录或用户归属校验 |
| 草稿归属 | 通过 `draft_id` 匿名读取；谁持有 `draft_id` 谁可恢复该草稿 |
| 前端存储 | 保存成功后将 `draft_id` 写入 `localStorage`，例如 `localStorage.setItem("interviewDraftId", draft_id)` |
| 恢复边界 | 当前只支持同一浏览器通过 `localStorage` 中的 `draft_id` 恢复；更换浏览器、清空本地存储或丢失 `draft_id` 后无法自动恢复 |

### 5.12 GET `/api/interview-drafts/{draft_id}`

用途：根据匿名草稿 ID 恢复面试准备页的 JD 和简历。

成功响应：

```json
{
  "draft_id": "draft_001",
  "job_description": "岗位 JD",
  "resume_text": "简历内容",
  "created_at": "2026-07-04T10:00:00+08:00",
  "updated_at": "2026-07-04T10:05:00+08:00"
}
```

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `404` | 草稿不存在或已不可用 | `{"detail":"draft not found"}` |

客户端使用建议：

| 场景 | 建议 |
| --- | --- |
| 页面加载 | 从 `localStorage` 读取 `interviewDraftId`，存在则调用本接口恢复 |
| 保存草稿 | 调用 `POST /api/interview-drafts` 后覆盖本地保存的 `draft_id` |
| 恢复失败 | 如果返回 `404`，前端应清理本地 `draft_id` 并提示用户重新保存 |

## 6. 状态机需求

### 6.1 面试会话状态

当前公开状态：

| 状态 | 含义 |
| --- | --- |
| `active` | 面试进行中 |
| `finished` | 面试已结束，等待或已生成报告 |

内部状态流转：

```text
创建会话 -> active
提交回答 -> follow_up
提交追问回答 -> next_question
最后一题完成 -> finished
```

当前规则：同一题候选人回答次数达到 2 次后进入下一题。该规则由后端状态机控制，前端不应自行推断。

### 6.2 报告状态

公开状态：

| 状态 | 来源 | 含义 |
| --- | --- | --- |
| `processing` | `ReportRecord.status` | 报告生成中 |
| `completed` | `InterviewReport.status` | 报告已完成 |
| `failed` | `ReportRecord.status` | 报告生成失败 |

内部任务状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 报告任务已入队 |
| `running` | Worker 已认领任务 |
| `retrying` | 失败后等待重试 |
| `completed` | 任务完成 |
| `failed` | 任务终止失败 |

当前 `/report` 接口没有返回 `job_id`、`queued_at`、`started_at`、`finished_at`、`attempt_count` 等任务字段。`/report/progress` 已返回响应层 `report_job_id`，该值从报告任务表按 `session_id` 查询；任务表不可用或未找到任务时为 `null`。

## 7. HTML 原型驱动的补充接口需求

### 7.1 匿名草稿保存与恢复

来源：`app/test4.html` 中的“保存草稿”按钮。当前已实现匿名草稿保存与恢复，不依赖用户登录。

已实现接口：

```http
POST /api/interview-drafts
GET /api/interview-drafts/{draft_id}
Content-Type: application/json
```

保存请求：

```json
{
  "job_description": "岗位 JD",
  "resume_text": "简历内容"
}
```

保存和恢复响应：

```json
{
  "draft_id": "draft_001",
  "job_description": "岗位 JD",
  "resume_text": "简历内容",
  "created_at": "2026-07-04T10:00:00+08:00",
  "updated_at": "2026-07-04T10:05:00+08:00"
}
```

错误语义：

| 状态码 | 场景 | 响应 |
| --- | --- |
| `400` | 保存时 JD 或简历为空 | `{"detail":"job_description is required"}` 或 `{"detail":"resume_text is required"}` |
| `404` | 恢复时草稿不存在 | `{"detail":"draft not found"}` |
| `422` | 保存请求字段缺失或类型错误 | FastAPI/Pydantic 默认校验错误 |

客户端使用方式：

| 场景 | 说明 |
| --- | --- |
| 保存 | `POST /api/interview-drafts` 成功后，将响应中的 `draft_id` 写入 `localStorage` |
| 恢复 | 页面加载时读取 `localStorage` 中的 `draft_id`，再调用 `GET /api/interview-drafts/{draft_id}` 填回 JD 和简历 |
| 恢复失败 | 如果返回 `404`，清理本地 `draft_id` 并提示用户重新保存 |
| 恢复边界 | 当前没有用户登录、用户隔离和跨设备同步；草稿恢复依赖当前浏览器保存的 `draft_id` |

### 7.2 Stage 10/11 已落地的原型接口

以下原型需求已实现，下一阶段需要接入四个原型运行页，接口详情见第 5 节：

| 原型需求 | 已实现接口 | 详情 |
| --- | --- | --- |
| 面试进度、题目导航、识别标签和消息列表 | `GET /api/interviews/{session_id}` | 见 5.8 |
| “下一题”按钮 | `POST /api/interviews/{session_id}/skip` | 见 5.9 |
| 报告生成页任务进度、时间线和 `report_job_id` | `GET /api/interviews/{session_id}/report/progress` | 见 5.10 |
| 准备页计划预览岗位标签 | `POST /api/prep` | 见 5.2 |
| 准备页匿名草稿保存和恢复 | `POST /api/interview-drafts`、`GET /api/interview-drafts/{draft_id}` | 见 5.11、5.12 |

仍需后续补齐的增强项：

| 要求 | 说明 |
| --- | --- |
| P1 | 会话详情可继续补充开始时间、已用时、预计剩余时间 |
| P1 | 跳题可记录结构化内部事件，报告中可标记该题未回答 |
| P2 | 报告进度可继续返回 Worker ID、重试次数和错误原因，便于排障 |

### 7.3 下载 PDF 报告

来源：`app/test1.html` 中的“下载报告 / PDF”按钮。

当前已实现接口：

```http
GET /api/interviews/{session_id}/report.pdf
```

响应：

| Header | 说明 |
| --- | --- |
| `Content-Type: application/pdf` | PDF 文件 |
| `Content-Disposition: attachment; filename="interview-report-{session_id}.pdf"` | 下载文件名 |

错误语义：

| 要求 | 说明 |
| --- | --- |
| `404` | `session_id` 不存在 |
| `409` | 面试未结束、报告生成中，或报告生成失败 |

内容范围：

| 要求 | 说明 |
| --- | --- |
| 已实现 | 仅报告完成后允许下载 |
| 已实现 | PDF 内容包含总分、维度分、summary、highlights、逐题反馈和引用证据 |

### 7.4 报告中心列表

来源：顶部导航“报告中心”和复盘页“返回报告中心”按钮。

已实现接口：

```http
GET /api/reports?status=completed&limit=20
```

已实现响应：

```json
{
  "items": [
    {
      "session_id": "uuid",
      "status": "completed",
      "created_at": "2026-07-04T10:00:00Z",
      "finished_at": "2026-07-04T10:02:00Z",
      "overall_score": 84,
      "summary": "Clear project story with practical tradeoffs.",
      "is_fallback": false,
      "error": null,
      "report_url": "/api/interviews/uuid/report",
      "report_pdf_url": "/api/interviews/uuid/report.pdf"
    }
  ],
  "total": 1
}
```

需求：

| 要求 | 说明 |
| --- | --- |
| 已实现 | 基于本机运行时报告记录返回历史报告列表 |
| 已实现 | 支持 `status=processing/completed/failed` 过滤 |
| 已实现 | 支持 `limit` 限制，服务端限制范围为 `1..100` |
| 已实现 | `report_pdf_url` 仅在 completed 状态返回，否则为 `null` |

## 8. 非功能需求

### 8.1 可用性

| 要求 | 说明 |
| --- | --- |
| 报告生成异步化 | 面试结束后接口应立即返回，不阻塞用户等待 LLM 和 RAG |
| 报告轮询稳定 | `/report` 在 processing 状态应持续返回 `202`，直到完成或失败 |
| LLM 失败兜底 | 计划生成失败时返回固定题目，报告结构化输出失败时返回兜底报告 |

### 8.2 一致性

| 要求 | 说明 |
| --- | --- |
| 报告任务幂等 | 同一 `session_id` 只能存在一个非终态报告任务 |
| 结束面试幂等 | 重复完成同一会话不应重复入队 |
| 报告状态一致 | 报告任务状态和报告记录状态不能长期不一致 |

### 8.3 错误处理

| 要求 | 说明 |
| --- | --- |
| 业务错误返回 `detail` | 当前 FastAPI 已使用 `{"detail":"..."}` |
| 前端区分生成失败与检索不可用 | `pgvector knowledge store is unavailable` 需要用户友好文案 |
| 流式接口必须返回 `error` 事件 | SSE 中途异常不能静默断流 |

### 8.4 安全与权限

| 要求 | 当前状态 |
| --- | --- |
| 用户登录 | 当前明确不做，非本项目范围（本地单机部署） |
| 会话归属校验 | 单用户本机部署下不要求应用层归属校验 |
| 报告访问控制 | 单用户本机部署下不要求应用层访问控制 |
| 简历/JD 脱敏 | 当前未实现；如后续需要导出或分享给第三方，可再补齐 |

当前项目面向本机单用户部署，不包含登录、账号体系、用户隔离、跨设备同步或公网访问控制。若未来从本机单用户扩展到多用户或公网部署，需要重新设计鉴权、归属校验和访问控制要求。

## 9. 接口验收标准

当前已实现接口验收：

| 编号 | 标准 |
| --- | --- |
| A1 | `GET /api/health` 返回 `{"status":"ok"}` |
| A2 | `POST /api/prep` 对非空 JD 和简历返回包含 `questions` 的计划，并在响应 wrapper 顶层返回 `job_tags` |
| A3 | `POST /api/interviews` 返回 `session_id`、第一题和 `status=active` |
| A4 | `POST /api/interviews/{session_id}/answer` 首次回答返回追问或下一题 |
| A5 | `POST /api/interviews/{session_id}/answer/stream` 返回 SSE `chunk` 和 `done` 事件 |
| A6 | `POST /api/interviews/{session_id}/finish` 返回 `status=finished`，并触发报告生成 |
| A7 | 面试结束后 `/report` 在生成中返回 `202` |
| A8 | 报告完成后 `/report` 返回 `InterviewReport` |
| A9 | 报告失败后 `/report` 返回 `500` 和错误 `detail` |
| A10 | `GET /api/interviews/{session_id}` 返回会话快照，包含进度、题目状态、消息和 `job_tags` |
| A11 | `POST /api/interviews/{session_id}/skip` 返回下一题或结束状态 |
| A12 | `GET /api/interviews/{session_id}/report/progress` 返回报告进度详情 |
| A13 | `POST /api/interview-drafts` 可匿名保存 JD 和简历草稿，并返回 `draft_id` |
| A14 | `GET /api/interview-drafts/{draft_id}` 可按匿名 `draft_id` 恢复 JD 和简历草稿 |
| A15 | `GET /api/interviews/{session_id}/report.pdf` 可下载已完成报告的 PDF 文件；未结束、生成中或失败时返回 `409` |
| A16 | `GET /api/reports` 可列出本机报告记录，并支持 `status` 和 `limit` 查询参数 |

v1.0 四页前端验收：

| 编号 | 标准 |
| --- | --- |
| B1 | `app/test4.html` 可完成 JD/简历输入、草稿保存与恢复、计划生成、岗位标签展示和开始面试 |
| B2 | `app/test3.html` 可完成会话快照加载、题目导航、流式答题、跳题、主动结束和跳转报告生成页 |
| B3 | `app/test2.html` 可展示报告阶段、百分比、事件时间线、RAG 摘要，并在报告完成后跳转详情页 |
| B4 | `app/test1.html` 可展示完整报告、五维能力分、逐题反馈、证据引用和 PDF 下载 |
| B5 | `app/static/index.html` 不再作为运行入口；页面功能由四个原型页承载 |

## 10. 优先级建议

| 优先级 | 接口或改动 | 原因 |
| --- | --- | --- |
| P0 | 统一现有接口契约与前端字段映射 | 当前核心闭环依赖这些字段 |
| 已实现 | `GET /api/interviews/{session_id}` | 页面刷新、面试进度、题目导航和面试页 `job_tags` 展示已支持 |
| 已实现 | `POST /api/interviews/{session_id}/skip` | `app/test3.html` 下一题按钮可接入 |
| 已实现 | `GET /api/interviews/{session_id}/report/progress` | 报告生成页进度详情已支持 |
| 已实现 | `GET /api/interviews/{session_id}/report.pdf` | 复盘页 PDF 下载已支持 |
| 已实现 | `POST /api/prep` 顶层返回 `job_tags` | 准备页可在创建会话前展示自动识别标签 |
| 已实现 | `POST /api/interview-drafts`、`GET /api/interview-drafts/{draft_id}` | 已支持匿名草稿保存和恢复 |
| 已实现 | `GET /api/reports` | 报告中心列表、回看和 PDF 下载入口已支持 |
| 非范围 | 登录、用户隔离和跨设备同步 | 当前项目面向本地单机部署，不纳入接口规划 |
