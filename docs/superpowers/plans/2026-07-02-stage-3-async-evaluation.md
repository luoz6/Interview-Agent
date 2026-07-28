# 阶段三异步面评实施计划

> **给 agentic workers：** 执行本计划时必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans。每个步骤使用 checkbox（`- [ ]`）追踪执行状态。

**目标：** 在面试结束后异步生成结构化面评报告，不阻塞前台答题接口。

**架构：** 保持现有前台面试 Graph 不变。新增报告数据模型、Shadow Evaluator、后台任务入口，并扩展内存版 `InterviewSessionStore` 保存报告状态。API 在 `submit_answer` 返回 `finished` 时投递 FastAPI `BackgroundTasks`，前端通过只读报告接口轮询结果。

**技术栈：** Python 3.11、FastAPI、Pydantic v2、LangChain structured output、pytest、原生 HTML/CSS/JavaScript。

---

## 文件结构

- 新增：`app/services/report.py`
  - 定义 `InterviewFeedback`、`InterviewReport`、`ReportRecord` 和报告生成异常。
- 新增：`app/services/evaluator.py`
  - 定义 `EvaluationChunk`、`ShadowEvaluator`、按题切块逻辑和兜底报告逻辑。
- 新增：`app/services/report_tasks.py`
  - 定义后台任务入口 `generate_report_for_session`。
- 修改：`app/services/llm.py`
  - 给 `InterviewLLM` 协议和 `OpenAIInterviewLLM` 增加 `generate_report`。
- 修改：`app/services/session.py`
  - 在现有 session store 中增加报告状态缓存。
- 修改：`app/api/routes.py`
  - 接入 `BackgroundTasks`，新增 `GET /api/interviews/{session_id}/report`。
- 修改：`app/static/index.html`
  - 增加报告展示区域。
- 修改：`app/static/app.js`
  - 增加报告轮询和渲染逻辑。
- 修改：`app/static/styles.css`
  - 增加报告区域样式。
- 新增：`tests/test_report_models.py`
- 新增：`tests/test_report_evaluator.py`
- 修改：`tests/test_llm_service.py`
- 修改：`tests/test_session_service.py`
- 修改：`tests/test_api.py`

统一测试命令：

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

当前机器默认 `python` 可能指向 Python 3.8，而仓库代码使用了 Python 3.10+ 的类型语法，所以所有验证命令都显式使用 `F:\python3.11\python.exe`。

---

### Task 1: 新增报告数据模型

**Files:**
- Create: `app/services/report.py`
- Create: `tests/test_report_models.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_report_models.py`：

```python
import pytest
from pydantic import ValidationError

from app.services.report import InterviewFeedback, InterviewReport, ReportRecord


def make_feedback(score: int = 82) -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Please introduce a backend project.",
        user_answer="The candidate described a FastAPI cache project.",
        score=score,
        critique="The answer missed concrete business metrics.",
        better_answer="I built a FastAPI service for order lookup, reduced repeated database reads with Redis, and measured latency before and after the change.",
    )


def test_interview_feedback_validates_score_range():
    assert make_feedback(score=100).score == 100

    with pytest.raises(ValidationError):
        make_feedback(score=101)

    with pytest.raises(ValidationError):
        make_feedback(score=-1)


def test_interview_report_contains_completed_status():
    report = InterviewReport(
        session_id="s1",
        overall_score=82,
        summary="Solid fundamentals with missing result metrics.",
        highlights=["Explained the project context"],
        feedbacks=[make_feedback()],
    )

    assert report.status == "completed"
    assert report.is_fallback is False
    assert report.feedbacks[0].question_id == "q1"


def test_report_requires_one_to_three_highlights():
    with pytest.raises(ValidationError):
        InterviewReport(
            session_id="s1",
            overall_score=82,
            summary="No highlights should fail.",
            highlights=[],
            feedbacks=[make_feedback()],
        )

    with pytest.raises(ValidationError):
        InterviewReport(
            session_id="s1",
            overall_score=82,
            summary="Too many highlights should fail.",
            highlights=["a", "b", "c", "d"],
            feedbacks=[make_feedback()],
        )


def test_report_record_states():
    completed_report = InterviewReport(
        session_id="s1",
        overall_score=82,
        summary="Solid answer.",
        highlights=["Clear context"],
        feedbacks=[make_feedback()],
    )

    processing = ReportRecord(status="processing")
    completed = ReportRecord(status="completed", report=completed_report)
    failed = ReportRecord(status="failed", error="llm timeout")

    assert processing.report is None
    assert completed.report is not None
    assert failed.error == "llm timeout"


def test_report_record_rejects_invalid_state_combinations():
    with pytest.raises(ValidationError):
        ReportRecord(status="completed")

    with pytest.raises(ValidationError):
        ReportRecord(status="failed")

    with pytest.raises(ValidationError):
        ReportRecord(status="processing", error="should not exist")
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_models.py -q
```

预期：因为 `app.services.report` 不存在而失败。

- [ ] **Step 3: 实现数据模型**

创建 `app/services/report.py`：

```python
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReportGenerationFailed(RuntimeError):
    """Raised when report generation should be marked as failed."""


class ReportGenerationTimeout(ReportGenerationFailed):
    """Raised when report generation times out."""


class InterviewFeedback(BaseModel):
    question_id: str = Field(description="Question identifier")
    question_text: str = Field(description="Original interview question text")
    user_answer: str = Field(description="Summary of the candidate answer for this question")
    score: int = Field(ge=0, le=100, description="Question score from 0 to 100")
    critique: str = Field(description="Main flaw or critique")
    better_answer: str = Field(description="Improved answer the candidate can practice")


class InterviewReport(BaseModel):
    session_id: str
    overall_score: int = Field(ge=0, le=100)
    summary: str
    highlights: list[str] = Field(min_length=1, max_length=3)
    feedbacks: list[InterviewFeedback]
    status: Literal["completed"] = "completed"
    is_fallback: bool = False


class ReportRecord(BaseModel):
    status: Literal["processing", "completed", "failed"]
    report: InterviewReport | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ReportRecord":
        if self.status == "processing" and (self.report is not None or self.error is not None):
            raise ValueError("processing report records cannot contain report or error")
        if self.status == "completed" and self.report is None:
            raise ValueError("completed report records require report")
        if self.status == "failed" and not self.error:
            raise ValueError("failed report records require error")
        return self
```

- [ ] **Step 4: 运行模型测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_models.py -q
```

预期：`tests/test_report_models.py` 全部通过。

- [ ] **Step 5: 提交**

```powershell
git add app/services/report.py tests/test_report_models.py
git commit -m "feat: add interview report models"
```

---

### Task 2: 实现 Shadow Evaluator

**Files:**
- Create: `app/services/evaluator.py`
- Create: `tests/test_report_evaluator.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_report_evaluator.py`：

```python
import pytest

from app.graphs.interview_state import build_initial_state
from app.services.evaluator import ShadowEvaluator
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewFeedback, InterviewReport, ReportGenerationTimeout


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Please introduce a backend project.",
                focus="project communication",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            ),
        ],
    )


def make_finished_state():
    state = build_initial_state(session_id="s1", plan=make_plan())
    state["messages"].extend(
        [
            {"role": "candidate", "content": "I built a FastAPI service and used Redis for hot records.", "question_id": "q1"},
            {"role": "interviewer", "content": "How did you handle cache failure?", "question_id": "q1"},
            {"role": "candidate", "content": "I used logical expiration, rate limiting, and database fallback.", "question_id": "q1"},
            {"role": "interviewer", "content": "Explain Redis cache invalidation.", "question_id": "q2"},
            {"role": "candidate", "content": "I delete cache after database updates and accept short eventual consistency.", "question_id": "q2"},
        ]
    )
    state["current_index"] = 2
    state["status"] = "finished"
    return state


class FakeReportLLM:
    def __init__(self):
        self.last_plan = None
        self.last_chunks = None
        self.last_session_id = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("Evaluator tests do not generate plans")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError("Evaluator tests do not generate followups")

    def generate_report(self, plan: InterviewPlan, chunks: list[dict], session_id: str) -> InterviewReport:
        self.last_plan = plan
        self.last_chunks = chunks
        self.last_session_id = session_id
        return InterviewReport(
            session_id=session_id,
            overall_score=80,
            summary="Clear project story with room for stronger metrics.",
            highlights=["Explained failure handling"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Please introduce a backend project.",
                    user_answer="The candidate described a FastAPI Redis project.",
                    score=82,
                    critique="Business metrics were not specific enough.",
                    better_answer="I built a FastAPI service for hot record lookup, measured p95 latency, and added Redis with database fallback.",
                ),
                InterviewFeedback(
                    question_id="q2",
                    question_text="Explain Redis cache invalidation.",
                    user_answer="The candidate mentioned delete-after-update and eventual consistency.",
                    score=78,
                    critique="The answer did not explain race conditions.",
                    better_answer="I would describe cache-aside, delete-after-write, retry behavior, and consistency windows.",
                ),
            ],
        )


class FailingReportLLM(FakeReportLLM):
    def generate_report(self, plan: InterviewPlan, chunks: list[dict], session_id: str) -> InterviewReport:
        raise ValueError("invalid structured output")


class TimeoutReportLLM(FakeReportLLM):
    def generate_report(self, plan: InterviewPlan, chunks: list[dict], session_id: str) -> InterviewReport:
        raise ReportGenerationTimeout("report generation timed out")


def test_evaluator_chunks_messages_by_question_id():
    llm = FakeReportLLM()
    evaluator = ShadowEvaluator(llm=llm)

    report = evaluator.evaluate(make_finished_state())

    assert report.overall_score == 80
    assert llm.last_session_id == "s1"
    assert llm.last_plan.title == "Backend interview"
    assert [chunk["question_id"] for chunk in llm.last_chunks] == ["q1", "q2"]
    assert [message["role"] for message in llm.last_chunks[0]["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
    ]


def test_evaluator_returns_fallback_completed_report_when_structured_output_fails():
    evaluator = ShadowEvaluator(llm=FailingReportLLM())

    report = evaluator.evaluate(make_finished_state())

    assert report.status == "completed"
    assert report.is_fallback is True
    assert report.overall_score == 60
    assert report.summary == "AI evaluation could not generate a complete report. Review the original answers manually."
    assert len(report.feedbacks) == 2
    assert {feedback.question_id for feedback in report.feedbacks} == {"q1", "q2"}
    assert all(feedback.score == 60 for feedback in report.feedbacks)


def test_evaluator_includes_unanswered_questions_in_fallback():
    state = make_finished_state()
    state["messages"] = [
        message
        for message in state["messages"]
        if message["question_id"] != "q2" or message["role"] != "candidate"
    ]
    evaluator = ShadowEvaluator(llm=FailingReportLLM())

    report = evaluator.evaluate(state)

    q2_feedback = next(feedback for feedback in report.feedbacks if feedback.question_id == "q2")
    assert q2_feedback.user_answer == "No candidate answer was recorded for this question."


def test_evaluator_propagates_timeout_for_background_failure_state():
    evaluator = ShadowEvaluator(llm=TimeoutReportLLM())

    with pytest.raises(ReportGenerationTimeout):
        evaluator.evaluate(make_finished_state())
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_evaluator.py -q
```

预期：因为 `app.services.evaluator` 不存在而失败。

- [ ] **Step 3: 实现 Evaluator**

创建 `app/services/evaluator.py`：

```python
from pydantic import BaseModel

from app.graphs.interview_state import InterviewState
from app.services.llm import InterviewLLM
from app.services.prep import InterviewQuestion
from app.services.report import (
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
)


class EvaluationChunk(BaseModel):
    question_id: str
    question_text: str
    focus: str
    messages: list[dict[str, str]]


class ShadowEvaluator:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._llm = llm

    def evaluate(self, state: InterviewState) -> InterviewReport:
        chunks = build_evaluation_chunks(state)
        try:
            if self._llm is None:
                raise ReportGenerationFailed("report llm is not configured")
            return self._llm.generate_report(
                plan=state["plan"],
                chunks=[chunk.model_dump() for chunk in chunks],
                session_id=state["session_id"],
            )
        except ReportGenerationTimeout:
            raise
        except ReportGenerationFailed:
            raise
        except (TypeError, ValueError):
            return build_fallback_report(state, chunks)


def build_evaluation_chunks(state: InterviewState) -> list[EvaluationChunk]:
    return [
        EvaluationChunk(
            question_id=question.id,
            question_text=question.prompt,
            focus=question.focus,
            messages=_messages_for_question(state, question),
        )
        for question in state["plan"].questions
    ]


def build_fallback_report(
    state: InterviewState,
    chunks: list[EvaluationChunk] | None = None,
) -> InterviewReport:
    chunks = chunks if chunks is not None else build_evaluation_chunks(state)
    return InterviewReport(
        session_id=state["session_id"],
        overall_score=60,
        summary="AI evaluation could not generate a complete report. Review the original answers manually.",
        highlights=["Completed the mock interview"],
        is_fallback=True,
        feedbacks=[
            InterviewFeedback(
                question_id=chunk.question_id,
                question_text=chunk.question_text,
                user_answer=_summarize_candidate_answers(chunk),
                score=60,
                critique="AI evaluation could not parse stable feedback for this question.",
                better_answer="Rebuild the answer around context, task, action, and result, then add concrete technical tradeoffs.",
            )
            for chunk in chunks
        ],
    )


def _messages_for_question(
    state: InterviewState,
    question: InterviewQuestion,
) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"]
        if message["question_id"] == question.id
    ]


def _summarize_candidate_answers(chunk: EvaluationChunk) -> str:
    answers = [
        message["content"].strip()
        for message in chunk.messages
        if message["role"] == "candidate" and message["content"].strip()
    ]
    if not answers:
        return "No candidate answer was recorded for this question."
    return " ".join(answers)

```

- [ ] **Step 4: 运行 Evaluator 测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_models.py tests/test_report_evaluator.py -q
```

预期：模型和 Evaluator 测试通过。

- [ ] **Step 5: 提交**

```powershell
git add app/services/evaluator.py tests/test_report_evaluator.py
git commit -m "feat: add shadow evaluator"
```

---

### Task 3: 扩展 LLM 结构化报告生成

**Files:**
- Modify: `app/services/llm.py`
- Modify: `tests/test_llm_service.py`

- [ ] **Step 1: 增加失败测试**

在 `tests/test_llm_service.py` 追加：

```python
from app.services.report import InterviewFeedback, InterviewReport


class FakeReportStructuredModel:
    def __init__(self):
        self.last_prompt = None

    def invoke(self, prompt: str):
        self.last_prompt = prompt
        return InterviewReport(
            session_id="s1",
            overall_score=84,
            summary="Strong technical basics.",
            highlights=["Explained Redis fallback"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Please introduce a backend project.",
                    user_answer="The candidate described FastAPI and Redis.",
                    score=84,
                    critique="The answer needs clearer metrics.",
                    better_answer="I built a FastAPI API with Redis cache, measured p95 latency, and added database fallback.",
                )
            ],
        )


class FakeReportChatModel:
    def __init__(self):
        self.schema = None
        self.method = None
        self.structured_model = FakeReportStructuredModel()

    def with_structured_output(self, schema, method=None):
        self.schema = schema
        self.method = method
        return self.structured_model


def test_openai_interview_llm_uses_structured_output_for_report():
    chat_model = FakeReportChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    plan = InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Please introduce a backend project.",
                focus="project communication",
            )
        ],
    )
    chunks = [
        {
            "question_id": "q1",
            "question_text": "Please introduce a backend project.",
            "focus": "project communication",
            "messages": [
                {"role": "interviewer", "content": "Please introduce a backend project."},
                {"role": "candidate", "content": "I built a FastAPI service with Redis."},
            ],
        }
    ]

    report = llm.generate_report(plan=plan, chunks=chunks, session_id="s1")

    assert report.overall_score == 84
    assert chat_model.schema is InterviewReport
    assert chat_model.method == "json_schema"
    assert "Backend interview" in chat_model.structured_model.last_prompt
    assert "I built a FastAPI service with Redis." in chat_model.structured_model.last_prompt
    assert "session_id: s1" in chat_model.structured_model.last_prompt
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_service.py -q
```

预期：因为 `OpenAIInterviewLLM.generate_report` 不存在而失败。

- [ ] **Step 3: 修改 LLM 协议和实现**

修改 `app/services/llm.py`。

新增 import：

```python
import json

from app.services.report import InterviewReport
```

在 `InterviewLLM` 中增加：

```python
    def generate_report(self, plan, chunks: list[dict], session_id: str) -> InterviewReport:
        """Generate a structured post-interview report."""
```

在 `OpenAIInterviewLLM` 中增加：

```python
    def generate_report(self, plan, chunks: list[dict], session_id: str) -> InterviewReport:
        prompt = (
            "You are a strict technical interview coach. Generate a structured interview report.\n"
            "Rules:\n"
            "1. Use only the supplied interview transcript and interview plan.\n"
            "2. Return one feedback item for every question chunk.\n"
            "3. Scores must be integers from 0 to 100.\n"
            "4. The critique must be specific and actionable.\n"
            "5. The better_answer must be a practice-ready answer, not generic advice.\n"
            "6. Keep highlights to one to three items.\n\n"
            f"session_id: {session_id}\n\n"
            f"plan_title: {plan.title}\n\n"
            f"questions:\n{json.dumps([question.model_dump() for question in plan.questions], ensure_ascii=False, indent=2)}\n\n"
            f"chunks:\n{json.dumps(chunks, ensure_ascii=False, indent=2)}"
        )
        structured_model = self.chat_model.with_structured_output(
            InterviewReport,
            method="json_schema",
        )
        return structured_model.invoke(prompt)
```

- [ ] **Step 4: 运行 LLM 和 Evaluator 测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_service.py tests/test_report_evaluator.py -q
```

预期：全部通过。

- [ ] **Step 5: 提交**

```powershell
git add app/services/llm.py tests/test_llm_service.py
git commit -m "feat: generate structured interview reports"
```

---

### Task 4: 扩展 Store 保存报告状态

**Files:**
- Modify: `app/services/session.py`
- Modify: `tests/test_session_service.py`

- [ ] **Step 1: 增加失败测试**

先在 `tests/test_session_service.py` 的 `FakeInterviewLLM` 中补充协议桩方法，保证它在类型层面也满足扩展后的 `InterviewLLM`：

```python
    def generate_report(self, plan: InterviewPlan, chunks: list[dict], session_id: str):
        raise AssertionError("Session store report cache tests do not generate reports")
```

然后在 `tests/test_session_service.py` 追加：

```python
import pytest

from app.services.report import InterviewFeedback, InterviewReport


def make_report(session_id: str) -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=80,
        summary="Solid interview.",
        highlights=["Explained project context"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Please introduce a project.",
                user_answer="The candidate described a backend project.",
                score=80,
                critique="The answer needs clearer metrics.",
                better_answer="I built a backend service, measured latency, and improved reliability with cache fallback.",
            )
        ],
    )


def finish_session(store: InterviewSessionStore, session_id: str) -> None:
    state = store.get(session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)


def test_mark_report_processing_requires_finished_session():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = store.start(make_plan())

    with pytest.raises(ValueError, match="interview is not finished"):
        store.mark_report_processing(session.session_id)


def test_mark_report_processing_is_idempotent_after_first_success():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = store.start(make_plan())
    finish_session(store, session.session_id)

    first = store.mark_report_processing(session.session_id)
    second = store.mark_report_processing(session.session_id)
    record = store.get_report_record(session.session_id)

    assert first is True
    assert second is False
    assert record.status == "processing"


def test_store_saves_completed_report_record():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = store.start(make_plan())
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    store.save_report(session.session_id, make_report(session.session_id))

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report.overall_score == 80
    assert record.error is None


def test_store_saves_failed_report_record():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = store.start(make_plan())
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    store.fail_report(session.session_id, "llm timeout")

    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert record.report is None
    assert record.error == "llm timeout"


def test_report_methods_reject_unknown_session():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    with pytest.raises(ValueError, match="session not found"):
        store.get_report_record("missing")
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py -q
```

预期：因为 Store 缺少报告方法而失败。

- [ ] **Step 3: 实现 Store 报告缓存**

修改 `app/services/session.py`。

新增 import：

```python
from app.services.report import InterviewReport, ReportRecord
```

修改 `__init__`：

```python
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._sessions: Dict[str, InterviewState] = {}
        self._reports: Dict[str, ReportRecord] = {}
        self._llm = llm
        self._runner = InterviewGraphRunner(llm=llm)
```

在 `InterviewSessionStore` 中新增：

```python
    def mark_report_processing(self, session_id: str) -> bool:
        state = self.get(session_id)
        if state["status"] != "finished":
            raise ValueError("interview is not finished")
        if session_id in self._reports:
            return False
        self._reports[session_id] = ReportRecord(status="processing")
        return True

    def save_report(self, session_id: str, report: InterviewReport) -> None:
        self.get(session_id)
        self._reports[session_id] = ReportRecord(status="completed", report=report)

    def fail_report(self, session_id: str, error: str) -> None:
        self.get(session_id)
        self._reports[session_id] = ReportRecord(status="failed", error=error)

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        self.get(session_id)
        return self._reports.get(session_id)
```

- [ ] **Step 4: 运行 Store 测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py tests/test_report_models.py -q
```

预期：全部通过。

- [ ] **Step 5: 提交**

```powershell
git add app/services/session.py tests/test_session_service.py
git commit -m "feat: cache report status in session store"
```

---

### Task 5: 新增后台报告任务入口

**Files:**
- Create: `app/services/report_tasks.py`
- Modify: `tests/test_report_evaluator.py`

- [ ] **Step 1: 增加失败测试**

在 `tests/test_report_evaluator.py` 追加：

```python
from app.services.report_tasks import generate_report_for_session
from app.services.session import InterviewSessionStore


def test_generate_report_for_session_saves_completed_report():
    store = InterviewSessionStore(llm=FakeReportLLM())
    state = make_finished_state()
    store._sessions[state["session_id"]] = state
    store.mark_report_processing(state["session_id"])

    generate_report_for_session(state["session_id"], store)

    record = store.get_report_record(state["session_id"])
    assert record.status == "completed"
    assert record.report.overall_score == 80


def test_generate_report_for_session_saves_failed_record_on_timeout():
    store = InterviewSessionStore(llm=TimeoutReportLLM())
    state = make_finished_state()
    store._sessions[state["session_id"]] = state
    store.mark_report_processing(state["session_id"])

    generate_report_for_session(state["session_id"], store)

    record = store.get_report_record(state["session_id"])
    assert record.status == "failed"
    assert "timed out" in record.error


def test_generate_report_for_session_returns_when_session_is_missing():
    store = InterviewSessionStore(llm=FakeReportLLM())

    generate_report_for_session("missing", store)

    with pytest.raises(ValueError, match="session not found"):
        store.get_report_record("missing")
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_evaluator.py -q
```

预期：因为 `app.services.report_tasks` 不存在而失败。

- [ ] **Step 3: 实现后台任务**

创建 `app/services/report_tasks.py`：

```python
from app.services.evaluator import ShadowEvaluator
from app.services.report import ReportGenerationFailed, ReportGenerationTimeout
from app.services.session import InterviewSessionStore


def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
) -> None:
    try:
        state = store.get(session_id)
    except ValueError:
        return

    if state["status"] != "finished":
        store.fail_report(session_id, "interview is not finished")
        return

    try:
        llm = store.llm
        if llm is None:
            from app.services.llm import OpenAIInterviewLLM

            llm = OpenAIInterviewLLM()
        evaluator = ShadowEvaluator(llm=llm)
        report = evaluator.evaluate(state)
        store.save_report(session_id, report)
    except (ReportGenerationTimeout, ReportGenerationFailed) as exc:
        store.fail_report(session_id, str(exc))
    except Exception as exc:
        store.fail_report(session_id, str(exc))
```

- [ ] **Step 4: 运行任务相关测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_evaluator.py -q
```

预期：全部通过。

- [ ] **Step 5: 提交**

```powershell
git add app/services/report_tasks.py tests/test_report_evaluator.py
git commit -m "feat: add report background task"
```

---

### Task 6: 接入 API 触发与报告查询接口

**Files:**
- Modify: `app/api/routes.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: 扩展 API 测试用 fake LLM**

在 `tests/test_api.py` 的 `FakeApiLLM` 中增加：

```python
    def generate_report(self, plan: InterviewPlan, chunks: list[dict], session_id: str):
        from app.services.report import InterviewFeedback, InterviewReport

        return InterviewReport(
            session_id=session_id,
            overall_score=81,
            summary="The candidate explained the project clearly.",
            highlights=["Clear project context"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Please introduce a project.",
                    user_answer="The candidate discussed Redis caching.",
                    score=81,
                    critique="The answer needs more measurable results.",
                    better_answer="I built a FastAPI service, added Redis for hot keys, measured p95 latency, and kept database fallback.",
                )
            ],
        )
```

- [ ] **Step 2: 增加失败 API 测试**

在 `tests/test_api.py` 追加：

```python
from app.services.report import InterviewFeedback, InterviewReport


def finish_interview(client, session_id: str):
    answers = [
        "Project answer.",
        "Project follow-up answer.",
        "Technical answer.",
        "Technical follow-up answer.",
        "System answer.",
        "System follow-up answer.",
    ]
    response = None
    for answer in answers:
        response = client.post(
            f"/api/interviews/{session_id}/answer",
            json={"answer": answer},
        )
    return response


def make_api_report(session_id: str) -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=88,
        summary="Strong interview.",
        highlights=["Clear cache fallback"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Please introduce a project.",
                user_answer="The candidate described a backend cache project.",
                score=88,
                critique="The answer could include more metrics.",
                better_answer="I built the service, measured latency, and explained cache fallback clearly.",
            )
        ],
    )


def test_report_endpoint_returns_404_before_interview_finished():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 404
    assert "report is only available after interview is finished" in response.json()["detail"]


def test_report_endpoint_returns_404_for_missing_session():
    client = make_client()

    response = client.get("/api/interviews/missing/report")

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


def test_report_endpoint_returns_202_when_processing():
    store = InterviewSessionStore(llm=FakeApiLLM())
    app.dependency_overrides[get_session_store] = lambda: store
    client = TestClient(app)
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]
    state = store.get(session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)
    store.mark_report_processing(session_id)

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 202
    assert response.json() == {"status": "processing"}


def test_report_endpoint_returns_completed_report():
    store = InterviewSessionStore(llm=FakeApiLLM())
    app.dependency_overrides[get_session_store] = lambda: store
    client = TestClient(app)
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]
    state = store.get(session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)
    store.save_report(session_id, make_api_report(session_id))

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 200
    assert response.json()["overall_score"] == 88


def test_report_endpoint_returns_500_for_failed_report():
    store = InterviewSessionStore(llm=FakeApiLLM())
    app.dependency_overrides[get_session_store] = lambda: store
    client = TestClient(app)
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]
    state = store.get(session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)
    store.fail_report(session_id, "llm timeout")

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 500
    assert response.json()["detail"] == "report generation failed"


def test_finished_answer_triggers_report_generation_once():
    store = InterviewSessionStore(llm=FakeApiLLM())
    app.dependency_overrides[get_session_store] = lambda: store
    client = TestClient(app)
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    finished_response = finish_interview(client, session_id)

    assert finished_response.status_code == 200
    assert finished_response.json()["status"] == "finished"
    record = store.get_report_record(session_id)
    assert record.status == "completed"
    assert record.report.overall_score == 81
```

- [ ] **Step 3: 运行 API 测试确认失败**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py -q
```

预期：因为接口和后台任务触发尚不存在而失败。

- [ ] **Step 4: 实现 API 接入**

修改 `app/api/routes.py`。

更新 imports：

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response

from app.services.report_tasks import generate_report_for_session
```

修改 `submit_answer`：

```python
@router.post("/interviews/{session_id}/answer")
def submit_answer(
    session_id: str,
    payload: AnswerRequest,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        turn = store.submit_answer(session_id, payload.answer)
        if turn.status == "finished" and store.mark_report_processing(session_id):
            background_tasks.add_task(generate_report_for_session, session_id, store)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)
```

新增报告查询接口：

```python
@router.get("/interviews/{session_id}/report")
def get_interview_report(
    session_id: str,
    response: Response,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="session not found")

    if state["status"] != "finished":
        raise HTTPException(
            status_code=404,
            detail="report is only available after interview is finished",
        )

    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        response.status_code = 202
        return {"status": "processing"}

    if record.status == "failed":
        raise HTTPException(status_code=500, detail="report generation failed")

    return record.report.model_dump()
```

- [ ] **Step 5: 运行 API 测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py -q
```

预期：API 测试通过。

- [ ] **Step 6: 运行后端相关全量测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_models.py tests/test_report_evaluator.py tests/test_llm_service.py tests/test_session_service.py tests/test_api.py -q
```

预期：全部通过。

- [ ] **Step 7: 提交**

```powershell
git add app/api/routes.py tests/test_api.py
git commit -m "feat: expose async report API"
```

---

### Task 7: 前端轻量轮询和报告渲染

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`

- [ ] **Step 1: 增加报告区域**

在 `app/static/index.html` 的 `.interview-panel` 中，把下面代码放到 `conversation` 之后、`answerForm` 之前：

```html
        <section id="reportPanel" class="report-panel" hidden>
          <div class="report-heading">
            <h3>Interview Report</h3>
            <span id="reportStatus" class="report-status">Processing</span>
          </div>
          <div id="reportContent" class="report-content"></div>
        </section>
```

- [ ] **Step 2: 增加轮询逻辑**

修改 `app/static/app.js`。

在顶部元素变量附近新增：

```javascript
let reportPollId = null;

const reportPanel = document.querySelector("#reportPanel");
const reportStatus = document.querySelector("#reportStatus");
const reportContent = document.querySelector("#reportContent");
```

在 `startButton` handler 里，`conversation.innerHTML = "";` 之后新增：

```javascript
  stopReportPolling();
  reportPanel.hidden = true;
  reportContent.innerHTML = "";
  reportStatus.textContent = "Processing";
```

在 `renderTurn(turn)` 设置 status 文案后新增：

```javascript
  if (turn.status === "finished") {
    addMessage("agent", turn.follow_up || "Interview finished. Report is being generated.");
    startReportPolling();
    return;
  }
```

在文件末尾新增：

```javascript
async function getReport() {
  const response = await fetch(`/api/interviews/${sessionId}/report`);
  if (response.status === 202) {
    return { status: "processing" };
  }
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Report request failed");
  }
  return response.json();
}

function startReportPolling() {
  if (!sessionId) {
    return;
  }
  reportPanel.hidden = false;
  reportStatus.textContent = "Processing";
  reportContent.textContent = "Report is being generated.";
  stopReportPolling();
  pollReport();
  reportPollId = window.setInterval(pollReport, 3000);
}

function stopReportPolling() {
  if (reportPollId) {
    window.clearInterval(reportPollId);
    reportPollId = null;
  }
}

async function pollReport() {
  try {
    const report = await getReport();
    if (report.status === "processing") {
      reportStatus.textContent = "Processing";
      return;
    }
    stopReportPolling();
    renderReport(report);
  } catch (error) {
    stopReportPolling();
    reportPanel.hidden = false;
    reportStatus.textContent = "Failed";
    reportContent.textContent = error.message;
  }
}

function renderReport(report) {
  reportPanel.hidden = false;
  reportStatus.textContent = report.is_fallback ? "Fallback" : "Completed";
  reportContent.innerHTML = "";

  const score = document.createElement("div");
  score.className = "report-score";
  score.textContent = `${report.overall_score}/100`;
  reportContent.appendChild(score);

  const summary = document.createElement("p");
  summary.className = "report-summary";
  summary.textContent = report.summary;
  reportContent.appendChild(summary);

  const highlights = document.createElement("ul");
  highlights.className = "report-highlights";
  report.highlights.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    highlights.appendChild(li);
  });
  reportContent.appendChild(highlights);

  report.feedbacks.forEach((feedback) => {
    const item = document.createElement("article");
    item.className = "feedback";
    item.innerHTML = `
      <h4>${escapeHtml(feedback.question_text)}</h4>
      <p><strong>Score:</strong> ${feedback.score}/100</p>
      <p><strong>Answer:</strong> ${escapeHtml(feedback.user_answer)}</p>
      <p><strong>Critique:</strong> ${escapeHtml(feedback.critique)}</p>
      <p><strong>Better answer:</strong> ${escapeHtml(feedback.better_answer)}</p>
    `;
    reportContent.appendChild(item);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
```

- [ ] **Step 3: 增加样式**

追加到 `app/static/styles.css`：

```css
.report-panel {
  border-top: 1px solid var(--line);
  margin-top: 18px;
  padding-top: 16px;
}

.report-heading {
  align-items: center;
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.report-heading h3 {
  font-size: 1.05rem;
  margin: 0;
}

.report-status {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--accent-dark);
  font-size: 0.78rem;
  font-weight: 800;
  padding: 5px 9px;
}

.report-content {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

.report-score {
  color: var(--accent-dark);
  font-size: 2rem;
  font-weight: 900;
}

.report-summary {
  margin: 0;
}

.report-highlights {
  margin: 0;
  padding-left: 20px;
}

.feedback {
  background: var(--panel);
  border-left: 4px solid var(--accent);
  border-radius: 4px;
  padding: 12px;
}

.feedback h4 {
  margin: 0 0 8px;
}

.feedback p {
  margin: 8px 0 0;
}
```

- [ ] **Step 4: 手动验证前端**

启动服务：

```powershell
uvicorn app.main:app --reload
```

打开：

```text
http://127.0.0.1:8000
```

预期：
- 开始一场面试。
- 连续提交答案直到面试结束。
- 页面出现报告区域，先显示 `Processing`。
- 后台生成完成后，真实报告显示 `Completed`，兜底报告显示 `Fallback`，并展示总分、summary、highlights 和单题反馈。
- 面试未结束前原有对话流程不受影响。

- [ ] **Step 5: 提交**

```powershell
git add app/static/index.html app/static/app.js app/static/styles.css
git commit -m "feat: poll and render interview reports"
```

---

### Task 8: 最终验证和清理

**Files:**
- Review: `app/services/report.py`
- Review: `app/services/evaluator.py`
- Review: `app/services/report_tasks.py`
- Review: `app/services/llm.py`
- Review: `app/services/session.py`
- Review: `app/api/routes.py`
- Review: `tests/test_report_models.py`
- Review: `tests/test_report_evaluator.py`
- Review: `tests/test_llm_service.py`
- Review: `tests/test_session_service.py`
- Review: `tests/test_api.py`

- [ ] **Step 1: 运行全量测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

预期：全部测试通过。

- [ ] **Step 2: 检查 diff**

```powershell
git diff -- app/services/report.py app/services/evaluator.py app/services/report_tasks.py app/services/llm.py app/services/session.py app/api/routes.py tests/test_report_models.py tests/test_report_evaluator.py tests/test_llm_service.py tests/test_session_service.py tests/test_api.py
```

预期：
- 没有无关重构。
- `POST /api/interviews/{session_id}/answer` 的返回结构保持兼容。
- 自动化测试没有调用真实 LLM。
- `app/graphs/interview_graph.py` 没有被塞入报告生成逻辑。

- [ ] **Step 3: 运行关键 API 流程测试**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_finished_answer_triggers_report_generation_once -q
```

预期：测试通过。

- [ ] **Step 4: 如有清理变更则提交**

如果 Step 2 发现并修复了小问题：

```powershell
git add app tests
git commit -m "test: verify async evaluation flow"
```

如果没有清理变更，跳过该提交。

---

## 自检清单

- [ ] `InterviewFeedback`、`InterviewReport`、`ReportRecord` 已实现并有模型测试。
- [ ] `ShadowEvaluator` 能按 `question_id` 切分完整面试历史。
- [ ] LLM 报告生成使用 `with_structured_output(InterviewReport, method="json_schema")`。
- [ ] Store 同时保存 session state 和 report record。
- [ ] finished 后只投递一次后台报告任务。
- [ ] `GET /api/interviews/{session_id}/report` 支持 404、202、200、500。
- [ ] 结构化输出失败时返回 `status="completed"` 且 `is_fallback=True` 的兜底报告。
- [ ] 超时和服务不可用错误会进入 failed 状态。
- [ ] 前端能在面试结束后轮询并渲染报告。
- [ ] `F:\python3.11\python.exe -m pytest -q` 全部通过。

## 执行交接

计划已保存到：

```text
docs/superpowers/plans/2026-07-02-stage-3-async-evaluation.md
```

执行选项：

1. **Subagent-Driven（推荐）**：每个任务派发一个新 subagent，实现后逐任务 review，适合快速并行推进。
2. **Inline Execution**：在当前会话中使用 executing-plans 按任务顺序执行，适合需要持续上下文的实现。

建议先执行 Task 1 到 Task 6，完成后端闭环；Task 7 前端轮询可以作为独立提交处理。
