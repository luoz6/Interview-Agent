# LangGraph 面试状态机 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 LangGraph 替换当前 `InterviewSessionStore.submit_answer()` 中的追问、切题、结束判断逻辑，同时保持现有 FastAPI 返回结构兼容。

**Architecture:** 新增 `app/graphs/` 作为状态机层，`InterviewState` 保存共享状态，`InterviewGraphRunner` 负责编排 `brain_node -> speaker_node`。`InterviewSessionStore` 只负责保存 state、追加用户回答、调用 runner，并把 state 转成现有 `InterviewTurn`。

**Tech Stack:** Python 3.11, FastAPI, Pydantic, LangGraph, LangChain-compatible LLM, pytest.

---

## File Structure

- Create: `app/graphs/__init__.py`
- Create: `app/graphs/interview_state.py`
- Create: `app/graphs/interview_graph.py`
- Modify: `app/services/session.py`
- Modify: `tests/test_session_service.py`
- Create: `tests/test_interview_graph.py`
- Modify: `tests/test_api.py`

执行本计划时，代码可以提交；`docs/` 下的 spec 和 plan 不提交。

---

### Task 1: 定义 InterviewState 和基础工具函数

**Files:**
- Create: `app/graphs/__init__.py`
- Create: `app/graphs/interview_state.py`
- Create: `tests/test_interview_graph.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_interview_graph.py`:

```python
from app.graphs.interview_state import (
    InterviewDecision,
    InterviewMessage,
    build_initial_state,
    get_current_question,
)
from app.services.prep import InterviewPlan, InterviewQuestion


def make_plan():
    return InterviewPlan(
        title="后端模拟面试",
        questions=[
            InterviewQuestion(id="q1", kind="project", prompt="请介绍项目。", focus="项目"),
            InterviewQuestion(id="q2", kind="technical", prompt="请解释 Redis。", focus="Redis"),
            InterviewQuestion(id="q3", kind="system-design", prompt="请设计服务。", focus="系统设计"),
        ],
    )


def test_build_initial_state_records_first_question():
    state = build_initial_state(session_id="s1", plan=make_plan())

    assert state["session_id"] == "s1"
    assert state["current_index"] == 0
    assert state["status"] == "active"
    assert state["decision"] is None
    assert state["pending_output"] == "请介绍项目。"
    assert state["messages"] == [
        {"role": "interviewer", "content": "请介绍项目。", "question_id": "q1"}
    ]


def test_get_current_question_returns_none_after_last_question():
    state = build_initial_state(session_id="s1", plan=make_plan())
    state["current_index"] = 3

    assert get_current_question(state) is None


def test_state_types_accept_decision_and_message_shapes():
    message: InterviewMessage = {
        "role": "candidate",
        "content": "我做过缓存项目。",
        "question_id": "q1",
    }
    decision: InterviewDecision = {
        "action": "follow_up",
        "follow_up": "请继续说明缓存失效策略。",
        "reason": "needs_depth",
    }

    assert message["role"] == "candidate"
    assert decision["action"] == "follow_up"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.graphs'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/graphs/__init__.py`:

```python
"""LangGraph 状态机模块。"""
```

Create `app/graphs/interview_state.py`:

```python
from typing import Literal, TypedDict

from app.services.prep import InterviewPlan, InterviewQuestion


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


def build_initial_state(session_id: str, plan: InterviewPlan) -> InterviewState:
    first_question = plan.questions[0] if plan.questions else None
    first_output = first_question.prompt if first_question else "面试题目为空，面试结束。"
    return {
        "session_id": session_id,
        "plan": plan,
        "current_index": 0,
        "messages": [
            {
                "role": "interviewer",
                "content": first_output,
                "question_id": first_question.id if first_question else None,
            }
        ],
        "decision": None,
        "pending_output": first_output,
        "status": "active" if first_question else "finished",
    }


def get_current_question(state: InterviewState) -> InterviewQuestion | None:
    current_index = state["current_index"]
    questions = state["plan"].questions
    if current_index >= len(questions):
        return None
    return questions[current_index]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit code only**

Run:

```powershell
git add app/graphs/__init__.py app/graphs/interview_state.py tests/test_interview_graph.py
git commit -m "feat: add interview graph state"
```

Do not add files under `docs/`.

---

### Task 2: 实现 InterviewGraphRunner.start

**Files:**
- Modify: `app/graphs/interview_graph.py`
- Modify: `tests/test_interview_graph.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_interview_graph.py`:

```python
from app.graphs.interview_graph import InterviewGraphRunner


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Graph tests should not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "请继续说明缓存失效策略。"


def test_runner_start_returns_initial_state():
    runner = InterviewGraphRunner(llm=FakeLLM())

    state = runner.start(session_id="s1", plan=make_plan())

    assert state["session_id"] == "s1"
    assert state["pending_output"] == "请介绍项目。"
    assert state["messages"][0]["role"] == "interviewer"
    assert state["messages"][0]["question_id"] == "q1"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py::test_runner_start_returns_initial_state -v
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `InterviewGraphRunner`.

- [ ] **Step 3: Write minimal implementation**

Create `app/graphs/interview_graph.py`:

```python
from app.graphs.interview_state import InterviewState, build_initial_state
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan


class InterviewGraphRunner:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._llm = llm

    def start(self, session_id: str, plan: InterviewPlan) -> InterviewState:
        return build_initial_state(session_id=session_id, plan=plan)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit code only**

Run:

```powershell
git add app/graphs/interview_graph.py tests/test_interview_graph.py
git commit -m "feat: add interview graph runner start"
```

Do not add files under `docs/`.

---

### Task 3: 实现 brain_node 的追问决策与兜底

**Files:**
- Modify: `app/graphs/interview_graph.py`
- Modify: `tests/test_interview_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_interview_graph.py`:

```python
class FailingLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Graph tests should not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise RuntimeError("llm failed")


def test_runner_submit_answer_generates_followup_decision():
    llm = FakeLLM()
    runner = InterviewGraphRunner(llm=llm)
    state = runner.start(session_id="s1", plan=make_plan())

    new_state = runner.submit_answer(state, "我用 Redis 缓存热点数据。")

    assert new_state["decision"] == {
        "action": "follow_up",
        "follow_up": "请继续说明缓存失效策略。",
        "reason": "candidate_answer_needs_depth",
    }
    assert new_state["pending_output"] == "请继续说明缓存失效策略。"
    assert new_state["messages"][-2] == {
        "role": "candidate",
        "content": "我用 Redis 缓存热点数据。",
        "question_id": "q1",
    }
    assert new_state["messages"][-1] == {
        "role": "interviewer",
        "content": "请继续说明缓存失效策略。",
        "question_id": "q1",
    }


def test_runner_submit_answer_falls_back_when_llm_fails():
    runner = InterviewGraphRunner(llm=FailingLLM())
    state = runner.start(session_id="s1", plan=make_plan())

    new_state = runner.submit_answer(state, "我用 Redis 缓存热点数据。")

    assert new_state["decision"]["action"] == "follow_up"
    assert new_state["pending_output"] == "请继续深挖项目：你当时做了什么取舍，为什么这样选？"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py::test_runner_submit_answer_generates_followup_decision tests/test_interview_graph.py::test_runner_submit_answer_falls_back_when_llm_fails -v
```

Expected: FAIL with `AttributeError: 'InterviewGraphRunner' object has no attribute 'submit_answer'`.

- [ ] **Step 3: Write minimal implementation**

Modify `app/graphs/interview_graph.py`:

```python
from copy import deepcopy

from app.graphs.interview_state import (
    InterviewState,
    build_initial_state,
    get_current_question,
)
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan
from app.services.session import fallback_followup


class InterviewGraphRunner:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._llm = llm

    def start(self, session_id: str, plan: InterviewPlan) -> InterviewState:
        return build_initial_state(session_id=session_id, plan=plan)

    def submit_answer(self, state: InterviewState, answer: str) -> InterviewState:
        next_state = deepcopy(state)
        question = get_current_question(next_state)
        if question is None:
            next_state["status"] = "finished"
            next_state["decision"] = {
                "action": "finish",
                "follow_up": None,
                "reason": "all_questions_completed",
            }
            next_state["pending_output"] = "本次模拟面试已结束。"
            return next_state

        next_state["messages"].append(
            {
                "role": "candidate",
                "content": answer.strip(),
                "question_id": question.id,
            }
        )
        next_state = brain_node(next_state, self._llm)
        return speaker_node(next_state)


def brain_node(state: InterviewState, llm: InterviewLLM | None) -> InterviewState:
    question = get_current_question(state)
    if question is None:
        state["decision"] = {
            "action": "finish",
            "follow_up": None,
            "reason": "all_questions_completed",
        }
        return state

    try:
        if llm is None:
            from app.services.llm import OpenAIInterviewLLM

            llm = OpenAIInterviewLLM()
        follow_up = llm.generate_followup(_build_followup_context(state))
    except Exception:
        follow_up = fallback_followup(question.focus)

    state["decision"] = {
        "action": "follow_up",
        "follow_up": follow_up,
        "reason": "candidate_answer_needs_depth",
    }
    return state


def speaker_node(state: InterviewState) -> InterviewState:
    decision = state["decision"]
    question = get_current_question(state)
    if decision is None or question is None:
        state["status"] = "finished"
        state["pending_output"] = "本次模拟面试已结束。"
        return state

    if decision["action"] == "follow_up":
        output = decision.get("follow_up") or fallback_followup(question.focus)
        state["pending_output"] = output
        state["messages"].append(
            {"role": "interviewer", "content": output, "question_id": question.id}
        )
    return state


def _build_followup_context(state: InterviewState) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"][-4:]
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit code only**

Run:

```powershell
git add app/graphs/interview_graph.py tests/test_interview_graph.py
git commit -m "feat: add interview graph followup decision"
```

Do not add files under `docs/`.

---

### Task 4: 实现每题追问一次后的切题和结束

**Files:**
- Modify: `app/graphs/interview_state.py`
- Modify: `app/graphs/interview_graph.py`
- Modify: `tests/test_interview_graph.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_interview_graph.py`:

```python
def test_runner_advances_to_next_question_after_followup_answer():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(session_id="s1", plan=make_plan())

    state = runner.submit_answer(state, "我用 Redis 缓存热点数据。")
    state = runner.submit_answer(state, "我会用逻辑过期和限流兜底。")

    assert state["current_index"] == 1
    assert state["decision"]["action"] == "next_question"
    assert state["pending_output"] == "请解释 Redis。"
    assert state["messages"][-1] == {
        "role": "interviewer",
        "content": "请解释 Redis。",
        "question_id": "q2",
    }


def test_runner_finishes_after_last_question_followup_answer():
    runner = InterviewGraphRunner(llm=FakeLLM())
    state = runner.start(session_id="s1", plan=make_plan())

    for answer in [
        "项目回答。",
        "项目追问回答。",
        "技术回答。",
        "技术追问回答。",
        "设计回答。",
        "设计追问回答。",
    ]:
        state = runner.submit_answer(state, answer)

    assert state["status"] == "finished"
    assert state["current_index"] == 3
    assert state["decision"]["action"] == "finish"
    assert state["pending_output"] == "本次模拟面试已结束。"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py::test_runner_advances_to_next_question_after_followup_answer tests/test_interview_graph.py::test_runner_finishes_after_last_question_followup_answer -v
```

Expected: FAIL because graph always generates follow-up and never advances.

- [ ] **Step 3: Add answered question tracking helper**

Modify `app/graphs/interview_state.py`:

```python
def count_candidate_answers_for_question(
    state: InterviewState,
    question_id: str,
) -> int:
    return sum(
        1
        for message in state["messages"]
        if message["role"] == "candidate" and message["question_id"] == question_id
    )
```

- [ ] **Step 4: Implement next-question and finish decisions**

Modify imports and `brain_node` / `speaker_node` in `app/graphs/interview_graph.py`:

```python
from app.graphs.interview_state import (
    InterviewState,
    build_initial_state,
    count_candidate_answers_for_question,
    get_current_question,
)


def brain_node(state: InterviewState, llm: InterviewLLM | None) -> InterviewState:
    question = get_current_question(state)
    if question is None:
        state["decision"] = {
            "action": "finish",
            "follow_up": None,
            "reason": "all_questions_completed",
        }
        return state

    answer_count = count_candidate_answers_for_question(state, question.id)
    if answer_count >= 2:
        next_index = state["current_index"] + 1
        if next_index >= len(state["plan"].questions):
            state["decision"] = {
                "action": "finish",
                "follow_up": None,
                "reason": "all_questions_completed",
            }
        else:
            state["decision"] = {
                "action": "next_question",
                "follow_up": None,
                "reason": "question_completed",
            }
        return state

    try:
        if llm is None:
            from app.services.llm import OpenAIInterviewLLM

            llm = OpenAIInterviewLLM()
        follow_up = llm.generate_followup(_build_followup_context(state))
    except Exception:
        follow_up = fallback_followup(question.focus)

    state["decision"] = {
        "action": "follow_up",
        "follow_up": follow_up,
        "reason": "candidate_answer_needs_depth",
    }
    return state


def speaker_node(state: InterviewState) -> InterviewState:
    decision = state["decision"]
    if decision is None:
        state["status"] = "finished"
        state["pending_output"] = "本次模拟面试已结束。"
        return state

    action = decision["action"]
    question = get_current_question(state)

    if action == "follow_up" and question is not None:
        output = decision.get("follow_up") or fallback_followup(question.focus)
        state["pending_output"] = output
        state["messages"].append(
            {"role": "interviewer", "content": output, "question_id": question.id}
        )
        return state

    if action == "next_question":
        state["current_index"] += 1
        next_question = get_current_question(state)
        if next_question is None:
            state["status"] = "finished"
            state["pending_output"] = "本次模拟面试已结束。"
            return state
        state["pending_output"] = next_question.prompt
        state["messages"].append(
            {
                "role": "interviewer",
                "content": next_question.prompt,
                "question_id": next_question.id,
            }
        )
        return state

    state["current_index"] = len(state["plan"].questions)
    state["status"] = "finished"
    state["pending_output"] = "本次模拟面试已结束。"
    state["messages"].append(
        {
            "role": "interviewer",
            "content": "本次模拟面试已结束。",
            "question_id": None,
        }
    )
    return state
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit code only**

Run:

```powershell
git add app/graphs/interview_state.py app/graphs/interview_graph.py tests/test_interview_graph.py
git commit -m "feat: add interview graph routing decisions"
```

Do not add files under `docs/`.

---

### Task 5: 用 LangGraph State 替换 InterviewSessionStore 内部会话对象

**Files:**
- Modify: `app/services/session.py`
- Modify: `tests/test_session_service.py`

- [ ] **Step 1: Write failing session tests for graph-backed state**

Modify `tests/test_session_service.py` to assert message history through stored state:

```python
def test_store_persists_graph_messages_in_order():
    llm = FakeInterviewLLM()
    store = InterviewSessionStore(llm=llm)
    session = store.start(make_plan())

    store.submit_answer(session.session_id, "我用 Redis 缓存热点数据。")
    store.submit_answer(session.session_id, "我会用逻辑过期和限流兜底。")
    state = store.get(session.session_id)

    assert [message["role"] for message in state["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert state["messages"][-1]["content"] == "请解释 Redis。"
```

Keep existing tests:

- `test_start_session_returns_first_question`
- `test_submit_answer_uses_llm_context_to_generate_followup`
- `test_submit_answer_falls_back_when_llm_followup_fails`
- `test_submit_answer_advances_after_followup`

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py -v
```

Expected: FAIL because `store.get()` still returns `InterviewSession`, not `InterviewState`.

- [ ] **Step 3: Rewrite InterviewSessionStore to use InterviewGraphRunner**

Modify `app/services/session.py`:

```python
from dataclasses import dataclass
from typing import Dict, Optional
from uuid import uuid4

from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import InterviewState, get_current_question
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion


@dataclass(frozen=True)
class RecordedAnswer:
    question_id: str
    answer: str


@dataclass(frozen=True)
class InterviewTurn:
    session_id: str
    current_question: Optional[InterviewQuestion]
    follow_up: Optional[str]
    status: str


class InterviewSessionStore:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._sessions: Dict[str, InterviewState] = {}
        self._llm = llm
        self._runner = InterviewGraphRunner(llm=llm)

    @property
    def llm(self) -> InterviewLLM | None:
        return self._llm

    def start(self, plan: InterviewPlan) -> InterviewTurn:
        session_id = str(uuid4())
        state = self._runner.start(session_id=session_id, plan=plan)
        self._sessions[session_id] = state
        return self._to_turn(state, follow_up=None)

    def get(self, session_id: str) -> InterviewState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ValueError("session not found") from exc

    def submit_answer(self, session_id: str, answer: str) -> InterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")

        state = self.get(session_id)
        new_state = self._runner.submit_answer(state, answer)
        self._sessions[session_id] = new_state
        return self._to_turn(new_state, follow_up=_extract_follow_up(new_state))

    def _to_turn(self, state: InterviewState, follow_up: Optional[str]) -> InterviewTurn:
        return InterviewTurn(
            session_id=state["session_id"],
            current_question=get_current_question(state),
            follow_up=follow_up,
            status=state["status"],
        )


def _extract_follow_up(state: InterviewState) -> str | None:
    decision = state["decision"]
    if decision and decision["action"] == "follow_up":
        return state["pending_output"]
    if state["status"] == "finished":
        return state["pending_output"]
    return None


def fallback_followup(focus: str) -> str:
    return f"请继续深挖{focus}：你当时做了什么取舍，为什么这样选？"
```

- [ ] **Step 4: Run session tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit code only**

Run:

```powershell
git add app/services/session.py tests/test_session_service.py
git commit -m "refactor: back sessions with interview graph state"
```

Do not add files under `docs/`.

---

### Task 6: 保持 API 兼容并跑全量回归

**Files:**
- Modify: `tests/test_api.py`
- Modify: `app/api/routes.py` only if regression exposes incompatibility

- [ ] **Step 1: Add API regression for next-question behavior**

Append to `tests/test_api.py`:

```python
def test_interview_moves_to_next_question_after_followup_answer():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    started = start_response.json()

    first_answer = client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={"answer": "I used Redis to cache hot records."},
    ).json()
    second_answer = client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={"answer": "I use logical expiration and rate limiting."},
    ).json()

    assert first_answer["follow_up"] == "请继续说明缓存失效时如何保护数据库。"
    assert second_answer["follow_up"] is None
    assert second_answer["current_question"]["id"] == "q2"
    assert second_answer["status"] == "active"
```

- [ ] **Step 2: Run API tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py -v
```

Expected: PASS. If it fails because `_turn_to_dict` cannot serialize the new turn, adjust only `app/api/routes.py` serialization while preserving response keys.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -v
```

Expected: all tests pass without `OPENAI_API_KEY`.

- [ ] **Step 4: Manual service smoke test**

Run:

```powershell
$process = Start-Process -FilePath 'F:\python3.11\python.exe' -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8001') -WorkingDirectory 'F:\agent\Interview-Agent' -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3
Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/health'
Stop-Process -Id $process.Id -Force
```

Expected:

```text
status
------
ok
```

- [ ] **Step 5: Commit code only**

Run:

```powershell
git add app/api/routes.py tests/test_api.py
git commit -m "test: preserve api contract for interview graph"
```

If `app/api/routes.py` was not modified, commit only `tests/test_api.py`.

Do not add files under `docs/`.

---

## Self-Review

- Spec coverage: State、Brain、Speaker、Edge、Store、API 兼容、测试策略均有对应任务。
- Placeholder scan: 本计划没有未完成标记或未定义的实现步骤。
- Type consistency: `InterviewState`、`InterviewMessage`、`InterviewDecision`、`InterviewGraphRunner`、`InterviewSessionStore` 在任务中保持同一命名。
- Scope control: 本计划只做 LangGraph 前台快轨状态机，不做 Redis、Celery、RAG、WebSocket 和评分报告。
- TDD flow: 每个功能任务都先写失败测试，再写最小实现，再跑测试。
- Git rule: 执行时只提交代码和测试文件，不提交 `docs/`。
