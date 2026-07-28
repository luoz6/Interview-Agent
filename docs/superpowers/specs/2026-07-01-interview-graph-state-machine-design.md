# LangGraph 面试状态机 SDD

## 目标

本阶段目标是把当前 `app/services/session.py` 中的面试流程控制迁移到 LangGraph。

当前实现依赖 `InterviewSessionStore.submit_answer()` 内部的 if/else 判断来完成：

- 记录候选人回答。
- 判断是否需要追问。
- 判断是否切到下一题。
- 判断面试是否结束。
- 生成下一句面试官输出。

改造后，这些职责拆到 LangGraph 的 State、Node 和 Edge 中。`InterviewSessionStore` 只保留会话状态存取和图执行入口，不再承载核心流程编排。

## 当前范围

本阶段只实现前台考官快轨的状态机，不引入后台影子 Agent。

包含：

- 定义 LangGraph 共享状态 `InterviewState`。
- 拆分 `brain_node` 和 `speaker_node`。
- 使用条件路由表达流程走向。
- 保持现有 FastAPI API 兼容。
- 保持现有测试不依赖真实 LLM。

不包含：

- Redis Checkpointer。
- WebSocket 断线恢复。
- Celery 影子 Agent。
- RAG 检索。
- 数据库持久化。
- 评分报告。
- 压力测试节点。

## 核心流程

目标运行链路：

```text
用户回答
-> FastAPI 接收 answer
-> InterviewSessionStore 追加 candidate message
-> LangGraph 启动
-> brain_node 评估回答并写入 decision
-> 条件路由进入 speaker_node
-> speaker_node 输出追问、下一题或结束语
-> LangGraph 结束本轮
-> FastAPI 返回最新输出
```

Graph 每次 API 请求只跑一轮，不在单次请求内无限循环。真正的闭环由用户下一次提交回答触发。

## State 设计

新增文件：

```text
app/graphs/interview_state.py
```

State 是所有节点共享的“公文包”。节点之间不直接调用，只通过 State 交换信息。

建议定义：

```python
from typing import Literal, TypedDict

from app.services.prep import InterviewPlan


class InterviewMessage(TypedDict):
    role: Literal["interviewer", "candidate"]
    content: str
    question_id: str | None


class InterviewDecision(TypedDict, total=False):
    action: Literal["follow_up", "next_question", "finish"]
    follow_up: str | None
    reason: str | None


class InterviewState(TypedDict):
    session_id: str
    plan: InterviewPlan
    current_index: int
    messages: list[InterviewMessage]
    decision: InterviewDecision | None
    pending_output: str | None
    status: Literal["active", "finished"]
```

字段说明：

- `session_id`：会话 ID，由 Store 创建。
- `plan`：完整面试大纲。
- `current_index`：当前题目下标。
- `messages`：完整对话历史，按发生顺序追加。
- `decision`：Brain 节点写下的下一步决策。
- `pending_output`：Speaker 节点产出的最新面试官话术。
- `status`：当前会话状态。

## Node 设计

新增文件：

```text
app/graphs/interview_graph.py
```

### brain_node

职责：只负责思考和决策，不直接输出给用户。

输入：

- 当前 `InterviewState`。
- 可注入的 `InterviewLLM`。

处理逻辑：

1. 找到当前问题。
2. 找到最近一条候选人回答。
3. 判断当前问题是否已经追问过。
4. 如果当前题还没有追问过，调用 LLM 生成追问。
5. 如果当前题已经追问过，决定切到下一题。
6. 如果没有下一题，决定结束面试。

当前阶段采用简单策略：每道题最多追问一次。

输出示例：

```python
{
    "decision": {
        "action": "follow_up",
        "follow_up": "你提到了 Redis 缓存，请说明缓存失效时如何保护数据库。",
        "reason": "candidate_answer_needs_depth",
    }
}
```

LLM 调用失败时，不抛异常给 API，降级为：

```text
请继续深挖{focus}：你当时做了什么取舍，为什么这样选？
```

### speaker_node

职责：只负责把 Brain 的决策转成面试官输出。

处理逻辑：

- `follow_up`：输出 `decision.follow_up`，不推进 `current_index`。
- `next_question`：推进 `current_index`，输出下一题 prompt。
- `finish`：设置 `status = "finished"`，输出结束语。

每次输出都追加到 `messages`：

```python
{
    "role": "interviewer",
    "content": output,
    "question_id": current_question_id,
}
```

## Edge 与路由设计

Graph 结构：

```text
START
  -> brain
  -> route_after_brain
      -> speaker
      -> END
```

当前阶段的条件路由保持简单：

```python
def route_after_brain(state: InterviewState) -> str:
    return "speaker"
```

原因是即使 Brain 判断面试结束，也需要 Speaker 输出结束语，所以仍然进入 `speaker_node`。

后续如果引入“危险发言阻断节点”或“压力测试节点”，可以把路由扩展成：

```text
brain -> safety_guard -> speaker
brain -> pressure_test -> speaker
brain -> speaker
```

## Store 接入设计

修改文件：

```text
app/services/session.py
```

保留 `InterviewSessionStore` 作为 API 层门面。

调整前：

```text
InterviewSessionStore 自己判断追问、切题和结束。
```

调整后：

```text
InterviewSessionStore 保存 InterviewState，并调用 InterviewGraphRunner。
```

建议新增：

```python
class InterviewGraphRunner:
    def start(self, plan: InterviewPlan) -> InterviewState:
        ...

    def submit_answer(self, state: InterviewState, answer: str) -> InterviewState:
        ...
```

`InterviewSessionStore.start(plan)` 行为：

1. 创建 `InterviewState`。
2. 把第一题作为 interviewer message 追加进 `messages`。
3. 返回兼容现有 API 的 `InterviewTurn`。

`InterviewSessionStore.submit_answer(session_id, answer)` 行为：

1. 从内存 Store 取出 `InterviewState`。
2. 追加 candidate message。
3. 调用 `InterviewGraphRunner.submit_answer()`。
4. 保存新 State。
5. 返回兼容现有 API 的 `InterviewTurn`。

## API 接入设计

修改文件：

```text
app/api/routes.py
```

现有 API 不变：

```text
POST /api/interviews
POST /api/interviews/{session_id}/answer
```

返回结构保持兼容：

```json
{
  "session_id": "...",
  "current_question": {
    "id": "q1",
    "kind": "project",
    "prompt": "...",
    "focus": "..."
  },
  "follow_up": "...",
  "status": "active"
}
```

兼容策略：

- 如果 Speaker 输出的是追问，则 `follow_up` 填追问，`current_question` 仍是当前题。
- 如果 Speaker 输出的是下一题，则 `follow_up` 为 `null`，`current_question` 是新题。
- 如果 Speaker 输出结束语，则 `status = "finished"`，`current_question = null`，`follow_up` 可放结束语。

## 测试策略

新增测试文件：

```text
tests/test_interview_graph.py
```

覆盖以下行为：

- `start()` 后第一条 interviewer message 是第一题。
- 用户第一次回答后，Brain 生成追问。
- 用户第二次回答后，Graph 切到下一题。
- 最后一题完成后，状态变为 `finished`。
- LLM 抛异常时，Graph 返回 fallback 追问。
- `messages` 按顺序记录 interviewer 和 candidate 全量历史。

保留并调整现有测试：

- `tests/test_session_service.py`：断言 Store 通过 Graph 推进状态。
- `tests/test_api.py`：断言 API 返回结构不变。
- `tests/test_llm_service.py`：继续只测试 LLM 包装，不参与 Graph 流程。

所有自动化测试必须使用 FakeLLM，不调用真实 `OPENAI_API_KEY`。

## 验收标准

- `app/graphs/interview_state.py` 定义明确的 `InterviewState`。
- `app/graphs/interview_graph.py` 包含 `brain_node`、`speaker_node` 和条件路由。
- `app/services/session.py` 不再直接承载核心追问/切题 if/else。
- 现有 API 路由不需要前端改造。
- LLM 失败时 API 不返回 500。
- `F:\python3.11\python.exe -m pytest -v` 全部通过。
- 后续新增节点时，不需要改 API 层。

## 风险与约束

- 当前仍然是内存 Store，进程重启后会话丢失。
- 当前每题最多追问一次，这是为了先保持 MVP 简洁；后续可以把追问次数变成 `InterviewState` 中的显式字段。
- LangGraph 初期只负责前台快轨，不和影子 Agent 评分流混在一起。
- 当前不使用 LangGraph Checkpointer，避免提前引入 Redis 复杂度。

## 后续扩展点

稳定后可以继续扩展：

- `safety_guard_node`：检测危险表达、作弊提示、无效回答。
- `pressure_test_node`：根据候选人表现主动提高追问压力。
- `shadow_enqueue_node`：把完整 Q&A 轮次推给后台影子 Agent。
- Redis Checkpointer：支持断线恢复和多实例部署。
- ReviewGraph：面试结束后汇总证据链并生成报告。
