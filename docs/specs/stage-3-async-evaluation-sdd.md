# 阶段三：异步面评架构 SDD

## 目标

阶段三的目标是在现有面试 Agent 中实现“慢轨”面评能力。

当前系统已经能完成前台面试快轨：生成面试计划、逐题追问、记录完整对话、在最后一题后将会话状态标记为 `finished`。阶段三在此基础上新增后台评估流程：当面试结束后，API 立即返回结束响应，同时把结构化面评报告生成任务投递到 FastAPI `BackgroundTasks` 中，由后台读取完整 `InterviewState`、调用 LLM 生成多维反馈，并将报告缓存到内存 Store。

核心目标：
- 不影响 `POST /api/interviews/{session_id}/answer` 的前台响应速度。
- 复用现有 `InterviewState.messages`、`question_id` 和 `plan` 作为报告输入。
- 用 Pydantic schema 约束 LLM 输出，保证前端可稳定渲染。
- 新增只读报告接口，支持前端轮询。
- 在 LLM 失败、结构化输出失败或超时时提供明确状态和兜底策略。

## 范围

包含：
- 新增结构化面评数据模型。
- 新增 Shadow Evaluator 评估模块。
- 扩展 `InterviewSessionStore`，保存报告生成状态和结果。
- 在提交答案 API 中检测 `finished` 状态并投递后台任务。
- 新增 `GET /api/interviews/{session_id}/report` 查询接口。
- 补充单元测试和 API 测试，所有自动化测试使用 fake LLM。

不包含：
- 不引入 Redis、Celery、RQ、RocketMQ 等外部队列。
- 不引入 WebSocket 或 SSE 主动推送。
- 不引入 PostgreSQL、SQLite 或其他真实数据库持久化。
- 不做用户登录、权限隔离或多租户。
- 不做复杂评分权重配置后台。
- 不重构前台面试 Graph 的核心流程。

## 当前代码基础

阶段三依赖以下现有能力：

- `app/graphs/interview_state.py` 已定义 `InterviewState`，包含 `session_id`、`plan`、`current_index`、`messages`、`decision`、`pending_output`、`status`。
- `InterviewMessage` 已包含 `question_id`，可以按题目切分问答片段。
- `app/graphs/interview_graph.py` 已在最后一题结束后将 `status` 标记为 `finished`。
- `app/services/session.py` 已提供内存 `_sessions` 字典和 `get(session_id)` 方法。
- `app/services/llm.py` 已使用 `with_structured_output(..., method="json_schema")` 生成结构化 `InterviewPlan`。
- `app/api/routes.py` 已通过依赖注入提供 `InterviewSessionStore`，便于 API 测试替换 fake store/fake LLM。

阶段三应保持这些边界：
- Graph 只负责前台面试流转。
- Store 负责内存状态和报告缓存。
- Evaluator 负责报告生成逻辑。
- API 负责触发后台任务和返回 HTTP 状态。

## 数据模型设计

新增文件：

```text
app/services/report.py
```

### InterviewFeedback

`InterviewFeedback` 表示单题反馈。

```python
from typing import Literal

from pydantic import BaseModel, Field


class InterviewFeedback(BaseModel):
    question_id: str = Field(description="题目 ID")
    question_text: str = Field(description="题目原文")
    user_answer: str = Field(description="候选人该题回答摘要")
    score: int = Field(ge=0, le=100, description="单题得分，0-100")
    critique: str = Field(description="核心缺陷或主要问题")
    better_answer: str = Field(description="高分示范回答")
```

字段说明：
- `question_id`：对应 `InterviewQuestion.id`。
- `question_text`：对应 `InterviewQuestion.prompt`。
- `user_answer`：该题所有 candidate message 的摘要，而不是简单拼接原文。
- `score`：0-100 整数，供前端展示和计算总分。
- `critique`：一针见血指出最大问题。
- `better_answer`：面试教练给出的更优回答话术。

### InterviewReport

`InterviewReport` 表示整场面试报告。

```python
class InterviewReport(BaseModel):
    session_id: str
    overall_score: int = Field(ge=0, le=100)
    summary: str
    highlights: list[str] = Field(min_length=1, max_length=3)
    feedbacks: list[InterviewFeedback]
    status: Literal["completed"] = "completed"
```

`InterviewReport.status` 只表示完整报告本身已经完成。生成中和失败状态由内部 `ReportRecord` 管理。

### ReportRecord

`ReportRecord` 是 Store 内部使用的报告缓存记录，用于表达处理中、完成、失败三种状态。

```python
class ReportRecord(BaseModel):
    status: Literal["processing", "completed", "failed"]
    report: InterviewReport | None = None
    error: str | None = None
```

约束：
- `processing`：`report is None`，`error is None`。
- `completed`：`report` 必须存在。
- `failed`：`error` 必须存在。

## Shadow Evaluator 设计

新增文件：

```text
app/services/evaluator.py
```

### 职责

Shadow Evaluator 是纯业务逻辑模块，负责把已完成的 `InterviewState` 转换成结构化 `InterviewReport`。

输入：
- 完整 `InterviewState`。
- 可注入的 `InterviewLLM | None`。

输出：
- `InterviewReport`。

### Chunking 规则

Evaluator 需要按 `plan.questions` 顺序切分历史记录。

对每个 `question`：
1. 从 `state["messages"]` 中筛选 `message["question_id"] == question.id`。
2. 保留该题所有 interviewer 和 candidate message。
3. 将 candidate message 汇总为该题候选人回答材料。
4. 即使某题没有 candidate message，也要生成一条反馈，得分可偏低，并说明候选人未作答。

推荐内部结构：

```python
class EvaluationChunk(BaseModel):
    question_id: str
    question_text: str
    focus: str
    messages: list[dict[str, str]]
```

`messages` 只保留：

```json
{
  "role": "interviewer",
  "content": "..."
}
```

### LLM 调用

`InterviewLLM` 协议新增方法：

```python
def generate_report(
    self,
    plan: InterviewPlan,
    chunks: list[dict],
    session_id: str,
) -> InterviewReport:
    ...
```

`OpenAIInterviewLLM.generate_report` 使用：

```python
structured_model = self.chat_model.with_structured_output(
    InterviewReport,
    method="json_schema",
)
return structured_model.invoke(prompt)
```

Prompt 必须要求：
- 严格基于给定面试记录，不编造候选人没有说过的经历。
- 每题必须返回一条 `InterviewFeedback`。
- `overall_score` 应根据各题得分综合计算。
- `highlights` 返回 1-3 条。
- `critique` 必须具体，避免空泛鼓励。
- `better_answer` 应是可直接练习的中文回答话术。

### 兜底报告

如果结构化输出失败、JSON 解析失败、LLM 返回字段不合法，Evaluator 返回一份极简兜底报告，而不是抛出到 API 层。

兜底规则：
- `overall_score = 60`
- 每题 `score = 60`
- `summary = "AI 评估未能生成完整报告，请结合原始回答进行复盘。"`
- `highlights = ["完成了本轮模拟面试"]`
- 每题 `critique = "AI 评估未能稳定解析该题反馈。"`
- 每题 `better_answer = "建议围绕背景、任务、行动、结果四部分重新组织回答，并补充关键技术取舍。"`
- `status = "completed"`

如果是明确的超时或服务不可用错误，后台任务应将报告记录标记为 `failed`，不返回兜底 completed 报告。这样前端可以停止轮询并展示失败提示。

## Store 扩展设计

修改文件：

```text
app/services/session.py
```

新增内部字段：

```python
self._reports: Dict[str, ReportRecord] = {}
```

新增方法：

```python
def mark_report_processing(self, session_id: str) -> bool:
    ...

def save_report(self, session_id: str, report: InterviewReport) -> None:
    ...

def fail_report(self, session_id: str, error: str) -> None:
    ...

def get_report_record(self, session_id: str) -> ReportRecord | None:
    ...
```

行为约束：
- `mark_report_processing` 必须先确认 session 存在且 `state["status"] == "finished"`。
- 如果报告已是 `processing` 或 `completed`，返回 `False`，避免重复投递后台任务。
- 如果报告曾经 `failed`，阶段三默认不自动重试，返回 `False`。
- `save_report` 和 `fail_report` 必须确认 session 存在。
- Store 仍然是内存字典，进程重启后报告丢失是阶段三可接受约束。

## 异步工作流设计

目标链路：

```text
用户提交最后一次回答
-> POST /api/interviews/{session_id}/answer
-> InterviewSessionStore.submit_answer(...)
-> InterviewGraphRunner 将 state.status 置为 finished
-> API 检测 turn.status == "finished"
-> store.mark_report_processing(session_id)
-> BackgroundTasks.add_task(generate_report_task, session_id)
-> API 立即返回 finished turn
-> 后台任务读取 state
-> ShadowEvaluator 调用 LLM 生成报告
-> store.save_report(...) 或 store.fail_report(...)
```

后台任务函数建议放在 `app/api/routes.py` 或单独文件 `app/services/report_tasks.py`。如果后续会扩展任务数量，优先使用单独文件。

推荐签名：

```python
def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
) -> None:
    ...
```

任务逻辑：
1. `state = store.get(session_id)`。
2. 如果 `state["status"] != "finished"`，调用 `store.fail_report(session_id, "interview is not finished")`。
3. 创建 `ShadowEvaluator(llm=store.llm)`。
4. 调用 `evaluator.evaluate(state)`。
5. 成功则 `store.save_report(session_id, report)`。
6. 超时或服务不可用则 `store.fail_report(session_id, str(exc))`。

## API 设计

修改文件：

```text
app/api/routes.py
```

### 提交答案接口

现有接口：

```text
POST /api/interviews/{session_id}/answer
```

新增行为：
- 当返回 turn 的 `status != "finished"` 时，不触发报告生成。
- 当返回 turn 的 `status == "finished"` 时，调用 `store.mark_report_processing(session_id)`。
- 只有 `mark_report_processing` 返回 `True` 时，才调用 `background_tasks.add_task(...)`。
- 响应体保持兼容，不新增必需字段。

### 查询报告接口

新增接口：

```text
GET /api/interviews/{session_id}/report
```

状态码：

#### 404 Not Found

场景：
- session 不存在。
- session 存在但 `state["status"] != "finished"`。

响应示例：

```json
{
  "detail": "report is only available after interview is finished"
}
```

#### 202 Accepted

场景：
- session 已结束。
- 报告记录不存在，或状态为 `processing`。

响应示例：

```json
{
  "status": "processing"
}
```

#### 200 OK

场景：
- 报告生成完成。

响应示例：

```json
{
  "session_id": "s1",
  "overall_score": 76,
  "summary": "基础扎实，但项目深挖时边界条件说明不足。",
  "highlights": [
    "能清楚说明项目背景",
    "能主动提到缓存失效风险"
  ],
  "feedbacks": [
    {
      "question_id": "q1",
      "question_text": "请介绍一个项目。",
      "user_answer": "候选人介绍了 Redis 缓存项目及限流兜底方案。",
      "score": 78,
      "critique": "业务指标和最终结果不够具体。",
      "better_answer": "我负责的项目背景是..."
    }
  ],
  "status": "completed"
}
```

#### 500 Internal Server Error

场景：
- 报告生成失败且 Store 记录为 `failed`。

响应示例：

```json
{
  "detail": "report generation failed"
}
```

## 前端轮询设计

阶段三后端必须完成报告接口；前端展示可保持轻量。

如果同步修改 `app/static/app.js`，建议行为：
- 当 `renderTurn(turn)` 收到 `turn.status === "finished"`，显示“面试结束，报告生成中...”。
- 每 3 秒调用 `GET /api/interviews/{sessionId}/report`。
- `202` 时继续轮询。
- `200` 时停止轮询并渲染报告。
- `500` 时停止轮询并显示失败提示。
- `404` 时停止轮询或显示“报告暂不可用”。

阶段三不要求实现雷达图。报告可以先渲染为：
- 综合得分。
- 一句话总结。
- 亮点列表。
- 单题反馈列表。

## 测试策略

所有自动化测试必须使用 fake LLM，不调用真实 API。

### 新增测试文件

```text
tests/unit/test_report_evaluator.py
```

覆盖：
- Evaluator 能按 `question_id` 切分完整面试历史。
- 每道题生成一条 feedback。
- LLM 正常返回时，Evaluator 返回结构化 `InterviewReport`。
- LLM 结构化输出失败时，Evaluator 返回兜底 completed 报告。
- 未回答题目也会出现在 feedbacks 中。

### 修改测试文件

```text
tests/unit/test_llm_service.py
```

新增覆盖：
- `OpenAIInterviewLLM.generate_report` 使用 `InterviewReport` 作为 structured output schema。
- prompt 中包含题目、focus 和对话历史。

```text
tests/unit/test_session_service.py
```

新增覆盖：
- `mark_report_processing` 只能用于 finished session。
- 重复调用 `mark_report_processing` 不会重复返回 `True`。
- `save_report` 后可以读取 completed report。
- `fail_report` 后可以读取 failed record。

```text
tests/acceptance/test_api.py
```

新增覆盖：
- 未结束面试查询 report 返回 404。
- 面试结束后提交答案响应仍然立即返回 `finished`。
- finished 后报告状态为 processing 时，`GET /report` 返回 202。
- 报告完成后，`GET /report` 返回 200 和完整 JSON。
- 报告失败时，`GET /report` 返回 500。

### 验证命令

当前仓库应使用 Python 3.11 运行测试：

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

原因：
- 默认 `python` 可能指向 Python 3.8。
- 现有代码使用 `str | None`、`list[dict[str, str]]` 等 Python 3.10+ 类型语法。

## 验收标准

阶段三完成后应满足：

- `InterviewFeedback`、`InterviewReport`、`ReportRecord` 数据模型存在并通过 Pydantic 校验。
- `ShadowEvaluator` 能从完整 `InterviewState` 生成报告。
- LLM 报告生成使用结构化输出，而不是依赖自由文本解析作为主路径。
- `InterviewSessionStore` 能独立保存 session state 和 report record。
- `POST /api/interviews/{session_id}/answer` 在面试结束时投递后台报告任务，响应体保持兼容。
- `GET /api/interviews/{session_id}/report` 按 404、202、200、500 返回明确状态。
- LLM 结构化输出异常时存在兜底 completed 报告。
- LLM 超时或服务不可用时报告状态为 failed。
- 重复 finished 请求不会重复投递报告任务。
- `F:\python3.11\python.exe -m pytest -q` 全部通过。

## 风险与约束

- FastAPI `BackgroundTasks` 基于当前应用进程，进程崩溃会丢失正在执行的报告任务。
- 内存 Store 不适合多进程部署；多个 Uvicorn worker 会导致 session/report 分散在不同进程。
- 报告生成期间用户刷新页面后仍可通过同一个 `session_id` 轮询，但进程重启会丢失报告。
- LLM 评分有主观性，阶段三只保证结构化和稳定返回，不保证评分绝对客观。
- 当前代码中存在中文乱码，阶段三新增文案必须保存为 UTF-8；如果同步触碰旧文案，建议单独修复编码显示问题。

## 后续扩展

阶段三稳定后可以继续扩展：

- 引入 Redis/RQ/Celery，把 BackgroundTasks 替换为可靠队列。
- 将 `ReportRecord` 持久化到数据库。
- 增加报告重试接口。
- 增加评分维度，如技术深度、业务理解、表达结构、故障兜底、系统设计能力。
- 增加前端图表展示，如雷达图、题目对比表、改写前后对比。
- 将 Shadow Evaluator 演进为独立 ReviewGraph。
