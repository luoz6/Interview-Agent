# 阶段一：LLM 核心大脑替换实施计划

> **给后续执行者：** 这个计划按 SDD + TDD 编写。每个任务先写失败测试，再写最小实现，再运行测试确认通过。

**目标：** 用 LLM 替换当前硬编码出题和固定追问逻辑，让系统能根据真实 JD、简历、候选人回答和最近上下文动态生成问题。

**架构：** 新增 `app/services/llm.py` 作为 LLM 基础设施层。`prep.py` 负责输入校验、调用 LLM 生成结构化 `InterviewPlan`，并在失败时降级到本地 fallback。`session.py` 负责会话状态，追问时把最近上下文交给 LLM，失败时使用 fallback follow-up。

**Tech Stack:** Python、FastAPI、Pydantic、LangChain、`langchain-openai`、pytest。

---

## Task 1: LLM 基础设施

**Files:**
- Modify: `requirements.txt`
- Create: `app/services/llm.py`
- Create: `tests/test_llm_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_service.py`:

```python
import pytest

from app.services.llm import LLMConfig, MissingLLMConfigError


def test_llm_config_reads_model_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "custom-model")

    config = LLMConfig.from_env()

    assert config.api_key == "test-key"
    assert config.model == "custom-model"


def test_llm_config_uses_deepseek_default_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    config = LLMConfig.from_env()

    assert config.model == "deepseekv4-pro"


def test_llm_config_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(MissingLLMConfigError, match="OPENAI_API_KEY"):
        LLMConfig.from_env()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_llm_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.llm'`.

- [ ] **Step 3: Add dependencies**

Modify `requirements.txt`:

```text
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
pytest>=8.0.0
httpx>=0.27.0
pydantic>=2.0.0
langchain>=1.0.0
langchain-openai>=1.0.0
```

- [ ] **Step 4: Write minimal implementation**

Create `app/services/llm.py`:

```python
import os
from dataclasses import dataclass
from typing import Protocol


class MissingLLMConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "deepseekv4-pro"
    base_url: str | None = None
    temperature: float = 0.2

    @classmethod
    def from_env(cls) -> "LLMConfig":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise MissingLLMConfigError("OPENAI_API_KEY is required")

        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "deepseekv4-pro"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )


class InterviewLLM(Protocol):
    def generate_plan(self, job_description: str, resume_text: str):
        ...

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        ...
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```powershell
python -m pytest tests/test_llm_service.py -v
```

Expected: all tests pass.

## Task 2: Pydantic schema 与现有 prep 测试改造

**Files:**
- Modify: `app/services/prep.py`
- Modify: `tests/test_prep_service.py`

- [ ] **Step 1: Replace existing prep tests with fake LLM tests**

Modify `tests/test_prep_service.py` to stop depending on `_infer_role_title` output:

```python
import pytest

from app.services.prep import InterviewPlan, InterviewQuestion, prepare_interview


class FakePlanLLM:
    def __init__(self):
        self.last_job_description = None
        self.last_resume_text = None

    def generate_plan(self, job_description: str, resume_text: str):
        self.last_job_description = job_description
        self.last_resume_text = resume_text
        return InterviewPlan(
            title="LLM 生成的后端模拟面试",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="请介绍一个最匹配岗位的项目。", focus="项目匹配"),
                InterviewQuestion(id="q2", kind="technical", prompt="请解释 Redis 缓存设计。", focus="Redis"),
                InterviewQuestion(id="q3", kind="system-design", prompt="请设计一个后端服务。", focus="系统设计"),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "请继续展开。"


def test_prepare_interview_uses_llm_for_question_plan():
    llm = FakePlanLLM()

    plan = prepare_interview(
        job_description="后端岗位，要求 Python、Redis、PostgreSQL。",
        resume_text="做过票务系统，使用 Redis 缓存。",
        llm=llm,
    )

    assert plan.title == "LLM 生成的后端模拟面试"
    assert len(plan.questions) == 3
    assert llm.last_job_description.startswith("后端岗位")
    assert llm.last_resume_text.startswith("做过票务系统")


def test_interview_plan_can_be_serialized_for_api():
    plan = prepare_interview(
        job_description="后端岗位，要求 Python 和 Redis。",
        resume_text="做过 Redis 缓存项目。",
        llm=FakePlanLLM(),
    )

    dumped = plan.model_dump()

    assert dumped["title"] == "LLM 生成的后端模拟面试"
    assert dumped["questions"][0]["prompt"]


def test_prepare_interview_rejects_empty_inputs():
    with pytest.raises(ValueError, match="job_description"):
        prepare_interview(job_description="", resume_text="做过后端项目。", llm=FakePlanLLM())

    with pytest.raises(ValueError, match="resume_text"):
        prepare_interview(job_description="后端岗位。", resume_text=" ", llm=FakePlanLLM())
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_prep_service.py -v
```

Expected: failure because `prepare_interview` does not accept `llm` and `InterviewPlan` has no `model_dump`.

- [ ] **Step 3: Write minimal schema implementation**

Modify `app/services/prep.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field


class InterviewQuestion(BaseModel):
    id: str = Field(description="题目 ID")
    kind: Literal["project", "technical", "system-design", "behavioral"] = Field(description="题目类型")
    prompt: str = Field(description="面试官要问的问题")
    focus: str = Field(description="考察重点")


class InterviewPlan(BaseModel):
    title: str = Field(description="面试标题")
    questions: list[InterviewQuestion] = Field(description="题目列表", min_length=3)
```

- [ ] **Step 4: Run test to verify current failure is only llm injection**

Run:

```powershell
python -m pytest tests/test_prep_service.py -v
```

Expected: remaining failure mentions unexpected `llm` argument.

## Task 3: LLM 出题接入与 fallback plan

**Files:**
- Modify: `app/services/prep.py`
- Modify: `tests/test_prep_service.py`

- [ ] **Step 1: Add fallback test**

Append to `tests/test_prep_service.py`:

```python
class FailingPlanLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise RuntimeError("llm failed")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise RuntimeError("llm failed")


def test_prepare_interview_falls_back_when_llm_fails():
    plan = prepare_interview(
        job_description="后端岗位，要求 Redis。",
        resume_text="做过缓存项目。",
        llm=FailingPlanLLM(),
    )

    assert plan.title == "基础模拟面试"
    assert len(plan.questions) == 3
    assert plan.questions[0].kind == "project"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_prep_service.py -v
```

Expected: failure because `prepare_interview` still does not accept `llm`.

- [ ] **Step 3: Replace hard-coded prep logic**

Modify `app/services/prep.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field


class InterviewQuestion(BaseModel):
    id: str = Field(description="题目 ID")
    kind: Literal["project", "technical", "system-design", "behavioral"] = Field(description="题目类型")
    prompt: str = Field(description="面试官要问的问题")
    focus: str = Field(description="考察重点")


class InterviewPlan(BaseModel):
    title: str = Field(description="面试标题")
    questions: list[InterviewQuestion] = Field(description="题目列表", min_length=3)


def prepare_interview(job_description: str, resume_text: str, llm=None) -> InterviewPlan:
    job_description = _require_text("job_description", job_description)
    resume_text = _require_text("resume_text", resume_text)

    if llm is None:
        from app.services.llm import OpenAIInterviewLLM

        llm = OpenAIInterviewLLM()

    try:
        return llm.generate_plan(job_description, resume_text)
    except Exception:
        return fallback_interview_plan()


def fallback_interview_plan() -> InterviewPlan:
    return InterviewPlan(
        title="基础模拟面试",
        questions=[
            InterviewQuestion(id="q1", kind="project", prompt="请介绍一个与你目标岗位最相关的项目。", focus="项目匹配"),
            InterviewQuestion(id="q2", kind="technical", prompt="请说明这个项目中最关键的技术难点和解决方案。", focus="技术深度"),
            InterviewQuestion(id="q3", kind="system-design", prompt="如果让你重新设计这个系统，你会如何改进架构？", focus="系统设计"),
        ],
    )


def _require_text(field_name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
python -m pytest tests/test_prep_service.py -v
```

Expected: all prep tests pass.

## Task 4: OpenAIInterviewLLM structured output

**Files:**
- Modify: `app/services/llm.py`
- Modify: `tests/test_llm_service.py`

- [ ] **Step 1: Add structured output tests**

Append to `tests/test_llm_service.py`:

```python
from app.services.llm import OpenAIInterviewLLM
from app.services.prep import InterviewPlan, InterviewQuestion


class FakeStructuredModel:
    def invoke(self, prompt: str):
        return InterviewPlan(
            title="LLM 生成的模拟面试",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="介绍项目。", focus="项目"),
                InterviewQuestion(id="q2", kind="technical", prompt="解释 Redis。", focus="Redis"),
                InterviewQuestion(id="q3", kind="system-design", prompt="设计服务。", focus="系统设计"),
            ],
        )


class FakeChatModel:
    def __init__(self):
        self.schema = None
        self.method = None

    def with_structured_output(self, schema, method=None):
        self.schema = schema
        self.method = method
        return FakeStructuredModel()


def test_openai_interview_llm_uses_structured_output_for_plan():
    chat_model = FakeChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)

    plan = llm.generate_plan("后端 JD", "后端简历")

    assert plan.title == "LLM 生成的模拟面试"
    assert chat_model.schema is InterviewPlan
    assert chat_model.method == "json_schema"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_llm_service.py -v
```

Expected: `ImportError` or `AttributeError` for missing `OpenAIInterviewLLM`.

- [ ] **Step 3: Implement OpenAIInterviewLLM**

Modify `app/services/llm.py`:

```python
import os
from dataclasses import dataclass
from typing import Protocol

from langchain_openai import ChatOpenAI


class MissingLLMConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "deepseekv4-pro"
    base_url: str | None = None
    temperature: float = 0.2

    @classmethod
    def from_env(cls) -> "LLMConfig":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise MissingLLMConfigError("OPENAI_API_KEY is required")
        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "deepseekv4-pro"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )


class InterviewLLM(Protocol):
    def generate_plan(self, job_description: str, resume_text: str):
        ...

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        ...


class OpenAIInterviewLLM:
    def __init__(self, config: LLMConfig | None = None, chat_model=None) -> None:
        self.config = config
        self.chat_model = chat_model or self._build_chat_model(config or LLMConfig.from_env())

    def generate_plan(self, job_description: str, resume_text: str):
        from app.services.prep import InterviewPlan

        structured_llm = self.chat_model.with_structured_output(InterviewPlan, method="json_schema")
        prompt = (
            "你是一名严格的软件工程面试官。请根据岗位 JD 和候选人简历生成面试大纲。"
            "要求至少 3 道题，覆盖项目经历、技术深度和系统设计。"
            f"\n\n岗位 JD：\n{job_description}"
            f"\n\n候选人简历：\n{resume_text}"
        )
        return structured_llm.invoke(prompt)

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise NotImplementedError

    def _build_chat_model(self, config: LLMConfig):
        kwargs = {
            "model": config.model,
            "api_key": config.api_key,
            "temperature": config.temperature,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url
        return ChatOpenAI(**kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
python -m pytest tests/test_llm_service.py -v
```

Expected: LLM config and structured output tests pass.

## Task 5: Session tests must inject fake LLM

**Files:**
- Modify: `tests/test_session_service.py`
- Modify: `app/services/session.py`

- [ ] **Step 1: Replace existing no-arg session tests**

Modify `tests/test_session_service.py` so every `InterviewSessionStore` receives fake LLM:

```python
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session import InterviewSessionStore


class FakeInterviewLLM:
    def __init__(self):
        self.last_context = None

    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="LLM 生成的后端模拟面试",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="请介绍一个项目。", focus="项目"),
                InterviewQuestion(id="q2", kind="technical", prompt="请解释 Redis。", focus="Redis"),
                InterviewQuestion(id="q3", kind="system-design", prompt="请设计服务。", focus="系统设计"),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        self.last_context = context
        return "你提到了缓存，请继续说明缓存失效时如何保护数据库。"


def make_plan():
    return FakeInterviewLLM().generate_plan("后端岗位", "后端简历")


def test_start_session_returns_first_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = store.start(make_plan())

    assert session.session_id
    assert session.current_question is not None
    assert session.current_question.kind == "project"
    assert session.status == "active"


def test_submit_answer_uses_llm_context_to_generate_followup():
    llm = FakeInterviewLLM()
    store = InterviewSessionStore(llm=llm)
    session = store.start(make_plan())

    response = store.submit_answer(session.session_id, "我用 Redis 缓存热点数据。")

    assert response.follow_up == "你提到了缓存，请继续说明缓存失效时如何保护数据库。"
    assert llm.last_context == [
        {"role": "interviewer", "content": "请介绍一个项目。"},
        {"role": "candidate", "content": "我用 Redis 缓存热点数据。"},
    ]


def test_submit_answer_advances_after_followup():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = store.start(make_plan())

    store.submit_answer(session.session_id, "我用 Redis 缓存热点数据。")
    second_response = store.submit_answer(session.session_id, "我会用逻辑过期和限流兜底。")

    assert second_response.current_question is not None
    assert second_response.current_question.id == "q2"
    assert len(store.get(session.session_id).answers) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_session_service.py -v
```

Expected: `InterviewSessionStore.__init__()` does not accept `llm`.

- [ ] **Step 3: Implement LLM injection and context follow-up**

Modify `app/services/session.py`:

```python
from app.services.llm import InterviewLLM, OpenAIInterviewLLM


class InterviewSessionStore:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._sessions: Dict[str, InterviewSession] = {}
        self._llm = llm or OpenAIInterviewLLM()

    @property
    def llm(self) -> InterviewLLM:
        return self._llm
```

Replace fixed follow-up:

```python
context = [
    {"role": "interviewer", "content": current_question.prompt},
    {"role": "candidate", "content": answer.strip()},
]
try:
    follow_up = self._llm.generate_followup(context)
except Exception:
    follow_up = fallback_followup(current_question.focus)
return self._to_turn(session, follow_up=follow_up)
```

Add fallback:

```python
def fallback_followup(focus: str) -> str:
    return f"请继续深挖{focus}：你当时做了什么取舍，为什么这样选？"
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
python -m pytest tests/test_session_service.py -v
```

Expected: session tests pass without `OPENAI_API_KEY`.

## Task 6: OpenAIInterviewLLM dynamic follow-up with context

**Files:**
- Modify: `app/services/llm.py`
- Modify: `tests/test_llm_service.py`

- [ ] **Step 1: Add context follow-up tests**

Append to `tests/test_llm_service.py`:

```python
class FakeMessage:
    content = "你提到了 Redis，请说明如果 Redis 宕机，系统如何降级。"


class FakeFollowupChatModel:
    def __init__(self):
        self.last_prompt = None

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return FakeMessage()


def test_openai_interview_llm_generates_followup_from_context():
    chat_model = FakeFollowupChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    context = [
        {"role": "interviewer", "content": "请介绍 Redis 缓存方案。"},
        {"role": "candidate", "content": "我用 Redis 缓存热点数据。"},
    ]

    followup = llm.generate_followup(context)

    assert "Redis 宕机" in followup
    assert "请介绍 Redis 缓存方案" in chat_model.last_prompt
    assert "我用 Redis 缓存热点数据" in chat_model.last_prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_llm_service.py -v
```

Expected: `NotImplementedError`.

- [ ] **Step 3: Implement follow-up generation**

Modify `OpenAIInterviewLLM.generate_followup`:

```python
def generate_followup(self, context: list[dict[str, str]]) -> str:
    transcript = "\n".join(f"{item['role']}: {item['content']}" for item in context)
    prompt = (
        "你是一名严格的软件工程面试官。"
        "请基于以下最近对话，生成一个简短、犀利、可继续追问的中文问题。"
        "不要评价，不要给答案，只输出追问。"
        f"\n\n最近对话：\n{transcript}"
    )
    response = self.chat_model.invoke(prompt)
    return str(response.content).strip()
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
python -m pytest tests/test_llm_service.py -v
```

Expected: all LLM service tests pass.

## Task 7: API tests use a single fake LLM class

**Files:**
- Modify: `app/api/routes.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Replace API tests to avoid real API Key**

Modify `tests/test_api.py` to use one fake class, not multiple inheritance:

```python
from fastapi.testclient import TestClient

from app.api.routes import get_session_store
from app.main import app
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session import InterviewSessionStore


class FakeApiLLM:
    def __init__(self):
        self.last_context = None

    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="LLM 生成的后端模拟面试",
            questions=[
                InterviewQuestion(id="q1", kind="project", prompt="请介绍一个项目。", focus="项目"),
                InterviewQuestion(id="q2", kind="technical", prompt="请解释 Redis。", focus="Redis"),
                InterviewQuestion(id="q3", kind="system-design", prompt="请设计服务。", focus="系统设计"),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        self.last_context = context
        return "请继续说明缓存失效时如何保护数据库。"


def make_client():
    app.dependency_overrides[get_session_store] = lambda: InterviewSessionStore(llm=FakeApiLLM())
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()
```

Keep existing API tests, but instantiate client through `make_client()` inside each test instead of module-level `client = TestClient(app)`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_api.py -v
```

Expected: `ImportError` for missing `get_session_store` or API still uses global store.

- [ ] **Step 3: Implement API dependency injection**

Modify `app/api/routes.py`:

```python
from fastapi import APIRouter, Depends, HTTPException


session_store = InterviewSessionStore()


def get_session_store() -> InterviewSessionStore:
    return session_store
```

Use the dependency in routes:

```python
@router.post("/interviews")
def start_interview(payload: PrepRequest, store: InterviewSessionStore = Depends(get_session_store)):
    try:
        plan = prepare_interview(payload.job_description, payload.resume_text, llm=store.llm)
        turn = store.start(plan)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)


@router.post("/interviews/{session_id}/answer")
def submit_answer(session_id: str, payload: AnswerRequest, store: InterviewSessionStore = Depends(get_session_store)):
    try:
        turn = store.submit_answer(session_id, payload.answer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)
```

Also update `/api/prep` to create a local `OpenAIInterviewLLM` by default, while tests can cover the interview flow through injected store.

- [ ] **Step 4: Run API tests**

Run:

```powershell
python -m pytest tests/test_api.py -v
```

Expected: all API tests pass without `OPENAI_API_KEY`.

## Task 8: README and manual verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add LLM configuration docs**

Append:

```markdown
## LLM 配置

默认模型是 `deepseekv4-pro`。

```powershell
$env:OPENAI_API_KEY="你的 API Key"
$env:OPENAI_MODEL="deepseekv4-pro"
```

如果使用兼容 OpenAI API 的代理或国产模型服务，可以额外配置：

```powershell
$env:OPENAI_BASE_URL="https://your-compatible-endpoint/v1"
```
```

- [ ] **Step 2: Run all tests**

Run:

```powershell
python -m pytest -v
```

Expected: all tests pass without requiring `OPENAI_API_KEY`.

- [ ] **Step 3: Manual validation**

Run:

```powershell
python -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

Manual checks:

- 输入真实 JD 和简历。
- 点击“生成题目计划”。
- 确认题目不是固定模板。
- 点击“开始面试”。
- 输入回答。
- 确认追问基于最近上下文动态生成。

## Self-review checklist

- Existing prep tests no longer depend on `_infer_role_title`.
- Existing session tests no longer call `InterviewSessionStore()` without fake LLM.
- API fake is a single class, not multiple inheritance.
- `generate_followup` accepts `context: list[dict[str, str]]`.
- LLM failures fall back instead of returning API 500.
- `ChatOpenAI` uses `temperature=0.2`.
- `requirements.txt` explicitly includes `pydantic>=2.0.0`.
- Default model is `deepseekv4-pro`.
