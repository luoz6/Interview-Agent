# 阶段四专家级评估系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有异步面评链路上引入 `pgvector` 本地知识库、`BGE-M3` embedding、多维评分、证据引用和可视化进度，让报告从“模型主观复盘”升级为“有证据的专家评估”。

**Architecture:** 保留当前 `FastAPI + BackgroundTasks + InterviewSessionStore` 主链路不变，只增强慢轨。新增 `PgVectorKnowledgeStore` 作为知识检索层，新增 `ExpertShadowEvaluator` 作为 RAG + 多维评分层，扩展 `InterviewState` 和 `ReportRecord` 承载 JD 上下文与进度状态，前端继续通过轮询报告接口获取处理进度。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、PostgreSQL、pgvector、psycopg2-binary、sentence-transformers、langchain-huggingface、pytest、原生 HTML/CSS/JavaScript。

---

## 文件结构

- 修改：`app/graphs/interview_state.py`
  - 扩展 `InterviewState`，保存 `job_description`、`resume_text`、`job_tags`
- 修改：`app/graphs/interview_graph.py`
  - 扩展 `InterviewGraphRunner.start(...)` 参数，透传 JD/简历上下文
- 修改：`app/services/session.py`
  - 扩展 `start(...)` 签名
  - 新增 `update_report_progress(...)`
  - 让 `mark_report_processing(...)` 初始化 progress
- 修改：`app/services/report.py`
  - 升级 `InterviewFeedback`、`InterviewReport`、`ReportRecord`
  - 新增 `DimensionScores`、`FeedbackReference`、`ReportProgress`
- 修改：`app/services/evaluator.py`
  - 保留基础 chunking / fallback
  - 让 fallback 报告构造完整新 schema
- 新增：`app/services/vector_store.py`
  - `PgVectorKnowledgeStore`、`KnowledgeChunk`、`get_knowledge_store()`
- 新增：`app/services/job_tags.py`
  - `extract_job_tags(job_description: str) -> list[str]`
- 新增：`app/services/evaluator_ext.py`
  - `ExpertShadowEvaluator`
- 修改：`app/services/llm.py`
  - 升级 `InterviewLLM.generate_report(...)` 输入和返回 schema
- 修改：`app/services/report_tasks.py`
  - 注入 `PgVectorKnowledgeStore`
  - 桥接 `on_progress -> store.update_report_progress(...)`
- 修改：`app/api/routes.py`
  - 在 `POST /interviews` 中生成并传播 `job_tags`
  - 扩展 `GET /report` 返回 progress
- 修改：`app/static/app.js`
  - 渲染阶段性进度与多维报告
- 修改：`app/static/index.html`
  - 增强报告区结构
- 修改：`app/static/styles.css`
  - 增强进度与证据展示样式
- 新增：`app/data/knowledge/benchmarks/redis_backend.md`
- 新增：`app/data/knowledge/theory/redis_consistency.md`
- 新增：`scripts/load_knowledge.py`
- 新增：`tests/test_job_tags.py`
- 新增：`tests/test_vector_store.py`
- 新增：`tests/test_expert_evaluator.py`
- 新增：`tests/test_report_progress.py`
- 修改：`tests/test_report_models.py`
- 修改：`tests/test_report_evaluator.py`
- 修改：`tests/test_llm_report_service.py`
- 修改：`tests/test_llm_service.py`
- 修改：`tests/test_session_report_store.py`
- 修改：`tests/test_session_service.py`
- 修改：`tests/test_report_tasks.py`
- 修改：`tests/test_report_api.py`
- 修改：`tests/test_api.py`
- 修改：`tests/test_interview_graph.py`
- 新增：`tests/golden/redis_strong_answer.json`
- 新增：`tests/golden/redis_weak_answer.json`

统一测试命令：

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

带数据库标记的可选集成测试命令：

```powershell
& 'F:\python3.11\python.exe' -m pytest -q -m pgvector
```

---

### Task 1: 升级报告模型并锁定破坏性变更

**Files:**
- Modify: `app/services/report.py`
- Modify: `tests/test_report_models.py`

- [ ] **Step 1: 先写失败测试，锁定新 schema**

把 `tests/test_report_models.py` 改成下面内容：

```python
import pytest
from pydantic import ValidationError

from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
    ReportRecord,
)


def make_dimension_scores(score: int = 82) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_reference() -> FeedbackReference:
    return FeedbackReference(
        chunk_id="redis-1",
        title="Redis cache consistency",
        source_type="theory",
        excerpt="Delete cache after database update and handle race conditions.",
    )


def make_feedback(score: int = 82) -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Please introduce a backend project.",
        user_answer="The candidate described a FastAPI cache project.",
        score=score,
        dimension_scores=make_dimension_scores(score),
        rationale="The answer covered the cache strategy but missed concrete metrics.",
        critique="The answer missed measurable business results.",
        better_answer="I built a FastAPI service for hot record lookup, reduced repeated database reads with Redis, and measured p95 latency before and after the change.",
        references=[make_reference()],
    )


def test_dimension_scores_validate_range():
    assert make_dimension_scores(100).depth == 100

    with pytest.raises(ValidationError):
        DimensionScores(
            breadth=101,
            depth=80,
            architecture=80,
            engineering=80,
            communication=80,
        )


def test_interview_feedback_requires_dimension_scores_and_references():
    feedback = make_feedback()

    assert feedback.dimension_scores.depth == 82
    assert feedback.references[0].chunk_id == "redis-1"


def test_interview_report_contains_overall_dimension_scores():
    report = InterviewReport(
        session_id="s1",
        overall_score=82,
        overall_dimension_scores=make_dimension_scores(),
        summary="Solid fundamentals with missing result metrics.",
        highlights=["Explained the project context"],
        feedbacks=[make_feedback()],
    )

    assert report.status == "completed"
    assert report.overall_dimension_scores.communication == 82
    assert report.is_fallback is False


def test_report_progress_validates_percent_and_stage():
    progress = ReportProgress(
        stage="retrieving",
        percent=20,
        message="Retrieving Redis references.",
        current_question_id=None,
    )
    assert progress.percent == 20

    with pytest.raises(ValidationError):
        ReportProgress(stage="retrieving", percent=101, message="bad")


def test_report_record_accepts_processing_with_progress():
    report = InterviewReport(
        session_id="s1",
        overall_score=82,
        overall_dimension_scores=make_dimension_scores(),
        summary="Solid answer.",
        highlights=["Clear context"],
        feedbacks=[make_feedback()],
    )

    processing = ReportRecord(
        status="processing",
        progress=ReportProgress(stage="retrieving", percent=20, message="Loading"),
    )
    completed = ReportRecord(status="completed", report=report)
    failed = ReportRecord(status="failed", error="pgvector unavailable")

    assert processing.progress is not None
    assert completed.report is not None
    assert failed.error == "pgvector unavailable"


def test_report_record_rejects_invalid_state_combinations():
    with pytest.raises(ValidationError):
        ReportRecord(status="processing")

    with pytest.raises(ValidationError):
        ReportRecord(status="completed")

    with pytest.raises(ValidationError):
        ReportRecord(status="failed")
```

- [ ] **Step 2: 运行模型测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_models.py -q
```

Expected: FAIL，提示缺少 `DimensionScores`、`FeedbackReference`、`ReportProgress` 或 `InterviewReport` 构造字段不匹配。

- [ ] **Step 3: 实现最小模型升级**

把 `app/services/report.py` 改成下面内容：

```python
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReportGenerationFailed(RuntimeError):
    """Raised when report generation should be marked as failed."""


class ReportGenerationTimeout(ReportGenerationFailed):
    """Raised when report generation times out."""


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
    question_id: str = Field(description="Question identifier")
    question_text: str = Field(description="Original interview question text")
    user_answer: str = Field(description="Summary of the candidate answer")
    score: int = Field(ge=0, le=100, description="Question score from 0 to 100")
    dimension_scores: DimensionScores
    rationale: str = Field(description="Why the score was assigned")
    critique: str = Field(description="Main flaw or critique")
    better_answer: str = Field(description="Improved answer to practice")
    references: list[FeedbackReference]


class InterviewReport(BaseModel):
    session_id: str
    overall_score: int = Field(ge=0, le=100)
    overall_dimension_scores: DimensionScores
    summary: str
    highlights: list[str] = Field(min_length=1, max_length=3)
    feedbacks: list[InterviewFeedback]
    status: Literal["completed"] = "completed"
    is_fallback: bool = False


class ReportProgress(BaseModel):
    stage: Literal["retrieving", "analyzing", "aggregating", "completed"]
    percent: int = Field(ge=0, le=100)
    message: str
    current_question_id: str | None = None


class ReportRecord(BaseModel):
    status: Literal["processing", "completed", "failed"]
    progress: ReportProgress | None = None
    report: InterviewReport | None = None
    error: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "ReportRecord":
        if self.status == "processing":
            if self.progress is None or self.report is not None or self.error is not None:
                raise ValueError("processing report records require progress and cannot contain report or error")
        if self.status == "completed" and self.report is None:
            raise ValueError("completed report records require report")
        if self.status == "failed" and not self.error:
            raise ValueError("failed report records require error")
        return self
```

- [ ] **Step 4: 运行模型测试，确认通过**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_models.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```powershell
git add app/services/report.py tests/test_report_models.py
git commit -m "feat: upgrade expert report schema"
```

---

### Task 2: 先补 `job_tags` 与 `InterviewState` 传播链

**Files:**
- Create: `app/services/job_tags.py`
- Modify: `app/graphs/interview_state.py`
- Modify: `app/graphs/interview_graph.py`
- Modify: `app/services/session.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_interview_graph.py`
- Create: `tests/test_job_tags.py`

- [ ] **Step 1: 先写 `job_tags` 提取的失败测试**

创建 `tests/test_job_tags.py`：

```python
from app.services.job_tags import extract_job_tags


def test_extract_job_tags_matches_known_keywords():
    tags = extract_job_tags(
        "Backend role using Python, FastAPI, Redis, PostgreSQL and Kafka."
    )

    assert tags == ["python", "fastapi", "redis", "postgresql", "kafka"]


def test_extract_job_tags_returns_general_when_no_match():
    tags = extract_job_tags("General backend role with strong communication.")

    assert tags == ["general"]


def test_extract_job_tags_deduplicates_case_insensitive_matches():
    tags = extract_job_tags("Python python PYTHON Redis redis")

    assert tags == ["python", "redis"]
```

- [ ] **Step 2: 先写 `InterviewState` 传播链的失败测试**

在 `tests/test_interview_graph.py` 追加：

```python
def test_build_initial_state_records_job_context():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )

    assert state["job_description"] == "Backend role using Python and Redis."
    assert state["resume_text"] == "Built a Python API with Redis."
    assert state["job_tags"] == ["python", "redis"]
```

- [ ] **Step 3: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_job_tags.py tests/test_interview_graph.py::test_build_initial_state_records_job_context -q
```

Expected: FAIL，提示 `app.services.job_tags` 不存在，或 `build_initial_state()` 参数不匹配。

- [ ] **Step 4: 实现 `job_tags` 提取函数**

创建 `app/services/job_tags.py`：

```python
KEYWORD_TAGS = [
    "python",
    "fastapi",
    "redis",
    "postgresql",
    "mysql",
    "java",
    "spring",
    "kafka",
    "rabbitmq",
    "system-design",
]


def extract_job_tags(job_description: str) -> list[str]:
    text = job_description.lower()
    tags: list[str] = []
    for tag in KEYWORD_TAGS:
        if tag in text and tag not in tags:
            tags.append(tag)
    return tags or ["general"]
```

- [ ] **Step 5: 扩展 `InterviewState` 和 `build_initial_state(...)`**

把 `app/graphs/interview_state.py` 改成：

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
    job_description: str
    resume_text: str
    job_tags: list[str]


def build_initial_state(
    session_id: str,
    plan: InterviewPlan,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> InterviewState:
    first_question = plan.questions[0] if plan.questions else None
    first_output = first_question.prompt if first_question else "Interview finished because the plan is empty."
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
        "job_description": job_description,
        "resume_text": resume_text,
        "job_tags": job_tags,
    }
```

- [ ] **Step 6: 扩展 `InterviewGraphRunner.start(...)` 与 `InterviewSessionStore.start(...)`**

把 `app/graphs/interview_graph.py` 的 `start(...)` 改成：

```python
    def start(
        self,
        session_id: str,
        plan: InterviewPlan,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
    ) -> InterviewState:
        return build_initial_state(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )
```

把 `app/services/session.py` 的 `start(...)` 改成：

```python
    def start(
        self,
        plan: InterviewPlan,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
    ) -> InterviewTurn:
        session_id = str(uuid4())
        state = self._runner.start(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )
        self._sessions[session_id] = state
        return self._to_turn(state, follow_up=None)
```

- [ ] **Step 7: 在 `POST /api/interviews` 中接入 `job_tags`**

把 `app/api/routes.py` 的 imports 增加：

```python
from app.services.job_tags import extract_job_tags
```

把 `start_interview(...)` 改成：

```python
@router.post("/interviews")
def start_interview(
    payload: PrepRequest,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
            llm=store.llm,
        )
        job_tags = extract_job_tags(payload.job_description)
        turn = store.start(
            plan,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            job_tags=job_tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)
```

- [ ] **Step 8: 运行聚焦测试，确认通过**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_job_tags.py tests/test_interview_graph.py::test_build_initial_state_records_job_context -q
```

Expected: PASS

- [ ] **Step 9: 提交**

```powershell
git add app/services/job_tags.py app/graphs/interview_state.py app/graphs/interview_graph.py app/services/session.py app/api/routes.py tests/test_job_tags.py tests/test_interview_graph.py
git commit -m "feat: propagate interview job context"
```

---

### Task 3: 修复现有测试与 Store 入口，适配新 `start(...)` 签名

**Files:**
- Modify: `tests/test_session_service.py`
- Modify: `tests/test_session_report_store.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_report_api.py`
- Modify: `tests/test_report_tasks.py`

- [ ] **Step 1: 先写一个失败断言，锁定 `store.start(...)` 新签名**

在 `tests/test_session_service.py` 增加：

```python
def test_start_session_records_job_context_in_store():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )

    state = store.get(session.session_id)
    assert state["job_tags"] == ["python", "redis"]
```

- [ ] **Step 2: 运行聚焦测试，确认旧测试因签名变化失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py::test_start_session_records_job_context_in_store -q
```

Expected: FAIL，提示其它 `store.start(make_plan())` 调用需要补参数。

- [ ] **Step 3: 统一修复所有 `store.start(...)` 测试调用**

把所有测试中的：

```python
store.start(make_plan())
```

统一替换成：

```python
store.start(
    make_plan(),
    job_description="Backend role using Python and Redis.",
    resume_text="Built a Python API with Redis.",
    job_tags=["python", "redis"],
)
```

需要修改的文件：

- `tests/test_session_service.py`
- `tests/test_session_report_store.py`
- `tests/test_report_tasks.py`

同时在 `tests/test_api.py` 和 `tests/test_report_api.py` 中保留通过真实路由启动 session 的方式，不直接调用 store。

- [ ] **Step 4: 运行 session / API 相关测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py tests/test_session_report_store.py tests/test_api.py tests/test_report_api.py tests/test_report_tasks.py -q
```

Expected: PASS 或仅因后续 schema 升级未完成而失败在报告构造处。

- [ ] **Step 5: 提交**

```powershell
git add tests/test_session_service.py tests/test_session_report_store.py tests/test_api.py tests/test_report_api.py tests/test_report_tasks.py
git commit -m "test: adapt session start to job context inputs"
```

---

### Task 4: 扩展 fallback 报告与所有 fake LLM，修复 schema 升级影响面

**Files:**
- Modify: `app/services/evaluator.py`
- Modify: `tests/test_report_evaluator.py`
- Modify: `tests/test_llm_report_service.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_report_api.py`
- Modify: `tests/test_session_report_store.py`
- Modify: `tests/test_report_tasks.py`

- [ ] **Step 1: 先写 fallback 报告的新字段断言**

在 `tests/test_report_evaluator.py` 的 fallback 测试里新增断言：

```python
    assert report.overall_dimension_scores.depth == 60
    assert report.feedbacks[0].dimension_scores.engineering == 60
    assert report.feedbacks[0].references == []
    assert "fallback" in report.feedbacks[0].rationale.lower()
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_evaluator.py::test_evaluator_returns_fallback_completed_report_when_structured_output_fails -q
```

Expected: FAIL，提示 fallback 报告字段不完整。

- [ ] **Step 3: 升级 `build_fallback_report(...)`**

把 `app/services/evaluator.py` 中 fallback 相关实现改成：

```python
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
)


def _default_dimension_scores(score: int = 60) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def build_fallback_report(
    state: InterviewState,
    chunks: list[EvaluationChunk] | None = None,
) -> InterviewReport:
    chunks = chunks if chunks is not None else build_evaluation_chunks(state)
    return InterviewReport(
        session_id=state["session_id"],
        overall_score=60,
        overall_dimension_scores=_default_dimension_scores(),
        summary=(
            "AI evaluation could not generate a complete report. "
            "Review the original answers manually."
        ),
        highlights=["Completed the mock interview"],
        is_fallback=True,
        feedbacks=[
            InterviewFeedback(
                question_id=chunk.question_id,
                question_text=chunk.question_text,
                user_answer=_summarize_candidate_answers(chunk),
                score=60,
                dimension_scores=_default_dimension_scores(),
                rationale="Fallback report: structured expert evaluation was unavailable for this question.",
                critique="AI evaluation could not parse stable feedback for this question.",
                better_answer=(
                    "Rebuild the answer around context, task, action, and result, "
                    "then add concrete technical tradeoffs."
                ),
                references=[],
            )
            for chunk in chunks
        ],
    )
```

- [ ] **Step 4: 统一修复 fake LLM 的 `InterviewReport(...)` 构造**

所有 fake report 构造补齐：

```python
overall_dimension_scores=DimensionScores(
    breadth=81,
    depth=81,
    architecture=81,
    engineering=81,
    communication=81,
)
```

以及每条 `InterviewFeedback(...)` 补齐：

```python
dimension_scores=DimensionScores(
    breadth=81,
    depth=81,
    architecture=81,
    engineering=81,
    communication=81,
),
rationale="The answer showed practical tradeoffs but missed measurable business impact.",
references=[
    FeedbackReference(
        chunk_id="redis-1",
        title="Redis cache consistency",
        source_type="theory",
        excerpt="Delete cache after updating the database and handle race conditions.",
    )
],
```

需要修复的文件：

- `tests/test_report_evaluator.py`
- `tests/test_llm_report_service.py`
- `tests/test_api.py`
- `tests/test_report_api.py`
- `tests/test_session_report_store.py`
- `tests/test_report_tasks.py`

- [ ] **Step 5: 运行 schema 相关测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_models.py tests/test_report_evaluator.py tests/test_llm_report_service.py tests/test_report_tasks.py tests/test_report_api.py -q
```

Expected: PASS

- [ ] **Step 6: 提交**

```powershell
git add app/services/evaluator.py tests/test_report_evaluator.py tests/test_llm_report_service.py tests/test_api.py tests/test_report_api.py tests/test_session_report_store.py tests/test_report_tasks.py
git commit -m "feat: adapt expert report schema across tests"
```

---

### Task 5: 扩展 `InterviewLLM.generate_report(...)` 输入与 structured output

**Files:**
- Modify: `app/services/llm.py`
- Modify: `tests/test_llm_report_service.py`

- [ ] **Step 1: 先写失败测试，锁定增强输入结构**

在 `tests/test_llm_report_service.py` 把 `generate_report()` 的测试改成：

```python
def test_generate_report_uses_interview_report_schema_and_includes_references():
    class FakeStructuredModel:
        def __init__(self):
            self.last_prompt = None

        def invoke(self, prompt: str):
            self.last_prompt = prompt
            return InterviewReport(
                session_id="s1",
                overall_score=84,
                overall_dimension_scores=DimensionScores(
                    breadth=84,
                    depth=84,
                    architecture=84,
                    engineering=84,
                    communication=84,
                ),
                summary="Strong technical basics.",
                highlights=["Explained Redis fallback"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Please introduce a backend project.",
                        user_answer="The candidate described FastAPI and Redis.",
                        score=84,
                        dimension_scores=DimensionScores(
                            breadth=84,
                            depth=84,
                            architecture=84,
                            engineering=84,
                            communication=84,
                        ),
                        rationale="The answer covered the main cache strategy.",
                        critique="The answer needs clearer metrics.",
                        better_answer="I built a FastAPI API with Redis cache and measured p95 latency.",
                        references=[
                            FeedbackReference(
                                chunk_id="redis-1",
                                title="Redis cache consistency",
                                source_type="theory",
                                excerpt="Delete cache after database updates.",
                            )
                        ],
                    )
                ],
            )

    class FakeChatModel:
        def __init__(self):
            self.schema = None
            self.method = None
            self.structured_model = FakeStructuredModel()

        def with_structured_output(self, schema, method):
            self.schema = schema
            self.method = method
            return self.structured_model

    chat_model = FakeChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    plan = InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            )
        ],
    )

    report = llm.generate_report(
        plan=plan,
        evaluation_items=[
            {
                "question_id": "q1",
                "question_text": "Explain Redis cache invalidation.",
                "focus": "Redis reliability",
                "messages": [{"role": "candidate", "content": "I delete cache after database writes."}],
                "scoring_references": [{"chunk_id": "redis-1", "title": "Redis cache consistency"}],
                "answer_references": [{"chunk_id": "redis-2", "title": "High-score Redis answer"}],
            }
        ],
        session_id="s1",
    )

    assert chat_model.schema is InterviewReport
    assert chat_model.method == "json_schema"
    assert "scoring_references" in chat_model.structured_model.last_prompt
    assert "answer_references" in chat_model.structured_model.last_prompt
    assert report.overall_dimension_scores.depth == 84
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_report_service.py::test_generate_report_uses_interview_report_schema_and_includes_references -q
```

Expected: FAIL，提示 `generate_report()` 参数名仍是 `chunks`，或 prompt 中缺少增强字段。

- [ ] **Step 3: 升级协议与实现**

把 `app/services/llm.py` 中协议和实现统一改成：

```python
class InterviewLLM(Protocol):
    def generate_plan(self, job_description: str, resume_text: str):
        ...

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        ...

    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> "InterviewReport":
        ...
```

```python
    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> "InterviewReport":
        from app.services.report import InterviewReport

        prompt = (
            "You are a strict technical interview coach. Generate a structured expert interview report.\n"
            "Rules:\n"
            "1. Use only the supplied transcript and retrieved references.\n"
            "2. Return one feedback item for every evaluation item.\n"
            "3. Scores must be integers from 0 to 100.\n"
            "4. dimension_scores must include breadth, depth, architecture, engineering, communication.\n"
            "5. rationale must explain the scoring and mention reference gaps when relevant.\n"
            "6. references must only include retrieved evidence.\n"
            "7. Keep highlights to one to three items.\n\n"
            f"session_id: {session_id}\n\n"
            f"plan_title: {plan.title}\n\n"
            "questions:\n"
            f"{json.dumps([question.model_dump() for question in plan.questions], ensure_ascii=False, indent=2)}\n\n"
            "evaluation_items:\n"
            f"{json.dumps(evaluation_items, ensure_ascii=False, indent=2)}"
        )
        structured_model = self.chat_model.with_structured_output(
            InterviewReport,
            method="json_schema",
        )
        return structured_model.invoke(prompt)
```

- [ ] **Step 4: 运行 LLM 报告测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_report_service.py tests/test_llm_service.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```powershell
git add app/services/llm.py tests/test_llm_report_service.py tests/test_llm_service.py
git commit -m "feat: upgrade llm expert report generation input"
```

---

### Task 6: 新增 `PgVectorKnowledgeStore` 与可选集成测试骨架

**Files:**
- Create: `app/services/vector_store.py`
- Create: `tests/test_vector_store.py`

- [ ] **Step 1: 先写无数据库的失败测试，锁定接口**

创建 `tests/test_vector_store.py`：

```python
import pytest

from app.services.vector_store import KnowledgeChunk


def test_knowledge_chunk_preserves_metadata():
    chunk = KnowledgeChunk(
        chunk_id="redis-1",
        title="Redis cache consistency",
        content="Delete cache after updating the database.",
        source_type="theory",
        domain="redis",
        tags=["redis", "backend"],
        metadata={"section": "consistency"},
    )

    assert chunk.tags == ["redis", "backend"]
    assert chunk.metadata["section"] == "consistency"


@pytest.mark.pgvector
def test_pgvector_search_signature_smoke():
    from app.services.vector_store import PgVectorKnowledgeStore

    store = PgVectorKnowledgeStore(
        dsn="postgresql://placeholder",
        table_name="knowledge_chunks",
        embedding_model_name="BAAI/bge-m3",
        embedding_dimension=1024,
    )

    assert store.table_name == "knowledge_chunks"
    assert store.embedding_dimension == 1024
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store.py -q
```

Expected: FAIL，提示 `app.services.vector_store` 不存在。

- [ ] **Step 3: 实现最小 `vector_store.py` 骨架**

创建 `app/services/vector_store.py`：

```python
import os

from pydantic import BaseModel


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
    def __init__(
        self,
        *,
        dsn: str,
        table_name: str,
        embedding_model_name: str,
        embedding_dimension: int,
    ) -> None:
        self.dsn = dsn
        self.table_name = table_name
        self.embedding_model_name = embedding_model_name
        self.embedding_dimension = embedding_dimension

    @classmethod
    def from_env(cls) -> "PgVectorKnowledgeStore":
        return cls(
            dsn=os.environ["POSTGRES_DSN"],
            table_name=os.getenv("PGVECTOR_TABLE", "knowledge_chunks"),
            embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3"),
            embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
        )

    def upsert_chunks(self, chunks: list[KnowledgeChunk]) -> None:
        raise NotImplementedError

    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        raise NotImplementedError


_knowledge_store: PgVectorKnowledgeStore | None = None


def get_knowledge_store() -> PgVectorKnowledgeStore:
    global _knowledge_store
    if _knowledge_store is None:
        _knowledge_store = PgVectorKnowledgeStore.from_env()
    return _knowledge_store
```

- [ ] **Step 4: 运行接口骨架测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store.py -q -m "not pgvector"
```

Expected: PASS

- [ ] **Step 5: 提交**

```powershell
git add app/services/vector_store.py tests/test_vector_store.py
git commit -m "feat: add pgvector store skeleton"
```

---

### Task 7: 实现 `ExpertShadowEvaluator` 组合层

**Files:**
- Create: `app/services/evaluator_ext.py`
- Create: `tests/test_expert_evaluator.py`

- [ ] **Step 1: 先写失败测试，锁定“组合而不是替换”**

创建 `tests/test_expert_evaluator.py`：

```python
from app.graphs.interview_state import build_initial_state
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
)


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            )
        ],
    )


def make_state():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )
    state["messages"].append(
        {
            "role": "candidate",
            "content": "I delete cache after the database update.",
            "question_id": "q1",
        }
    )
    state["status"] = "finished"
    state["current_index"] = 1
    return state


class FakeVectorStore:
    def __init__(self):
        self.last_query = None

    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        self.last_query = (query_text, job_tags, source_types, limit)
        return [
            {
                "chunk_id": "redis-1",
                "title": "Redis cache consistency",
                "content": "Delete cache after database writes and handle race conditions.",
                "source_type": "theory",
                "domain": "redis",
                "tags": ["redis"],
                "metadata": {"section": "consistency"},
                "score": 0.92,
            }
        ]


class FakeExpertLLM:
    def __init__(self):
        self.last_items = None

    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        raise AssertionError

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str) -> InterviewReport:
        self.last_items = evaluation_items
        return InterviewReport(
            session_id=session_id,
            overall_score=85,
            overall_dimension_scores=DimensionScores(
                breadth=84,
                depth=86,
                architecture=80,
                engineering=88,
                communication=87,
            ),
            summary="Strong Redis fundamentals with good practical tradeoffs.",
            highlights=["Explained cache invalidation tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Explain Redis cache invalidation.",
                    user_answer="The candidate deletes cache after database writes.",
                    score=85,
                    dimension_scores=DimensionScores(
                        breadth=84,
                        depth=86,
                        architecture=80,
                        engineering=88,
                        communication=87,
                    ),
                    rationale="The answer matched the retrieved Redis consistency guidance but missed deeper race condition handling.",
                    critique="The answer did not explain retry or delayed double delete strategies.",
                    better_answer="I would explain cache-aside, delete-after-write, race conditions, and delayed cleanup.",
                    references=[
                        FeedbackReference(
                            chunk_id="redis-1",
                            title="Redis cache consistency",
                            source_type="theory",
                            excerpt="Delete cache after database writes and handle race conditions.",
                        )
                    ],
                )
            ],
        )


def test_expert_evaluator_injects_references_and_reports_progress():
    llm = FakeExpertLLM()
    vector_store = FakeVectorStore()
    evaluator = ExpertShadowEvaluator(llm=llm, vector_store=vector_store)
    progress_events: list[ReportProgress] = []

    report = evaluator.evaluate(make_state(), on_progress=progress_events.append)

    assert report.overall_score == 85
    assert vector_store.last_query[1] == ["python", "redis"]
    assert llm.last_items[0]["scoring_references"][0]["chunk_id"] == "redis-1"
    assert [event.stage for event in progress_events] == [
        "retrieving",
        "analyzing",
        "aggregating",
        "completed",
    ]
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_expert_evaluator.py -q
```

Expected: FAIL，提示 `app.services.evaluator_ext` 不存在。

- [ ] **Step 3: 实现组合型 `ExpertShadowEvaluator`**

创建 `app/services/evaluator_ext.py`：

```python
from app.graphs.interview_state import InterviewState
from app.services.evaluator import build_evaluation_chunks, build_fallback_report
from app.services.llm import InterviewLLM
from app.services.report import InterviewReport, ReportProgress
from app.services.vector_store import PgVectorKnowledgeStore


class ExpertShadowEvaluator:
    def __init__(
        self,
        llm: InterviewLLM,
        vector_store: PgVectorKnowledgeStore,
    ) -> None:
        self._llm = llm
        self._vector_store = vector_store

    def evaluate(
        self,
        state: InterviewState,
        on_progress=None,
    ) -> InterviewReport:
        chunks = build_evaluation_chunks(state)
        if on_progress is not None:
            on_progress(
                ReportProgress(
                    stage="retrieving",
                    percent=20,
                    message="Retrieving role-specific knowledge references.",
                )
            )

        evaluation_items = []
        for chunk in chunks:
            references = self._vector_store.search(
                f"{chunk.question_text}\n{chunk.focus}\n{' '.join(message['content'] for message in chunk.messages)}",
                job_tags=state["job_tags"],
                source_types=["theory", "expert_benchmark"],
                limit=5,
            )
            evaluation_items.append(
                {
                    "question_id": chunk.question_id,
                    "question_text": chunk.question_text,
                    "focus": chunk.focus,
                    "messages": chunk.model_dump()["messages"],
                    "scoring_references": references,
                    "answer_references": references,
                }
            )

        if on_progress is not None:
            on_progress(
                ReportProgress(
                    stage="analyzing",
                    percent=60,
                    message="Analyzing question-level dimension scores.",
                    current_question_id=chunks[0].question_id if chunks else None,
                )
            )

        try:
            report = self._llm.generate_report(
                plan=state["plan"],
                evaluation_items=evaluation_items,
                session_id=state["session_id"],
            )
        except (TypeError, ValueError):
            report = build_fallback_report(state, chunks)

        if on_progress is not None:
            on_progress(
                ReportProgress(
                    stage="aggregating",
                    percent=80,
                    message="Aggregating overall expert scores.",
                )
            )
            on_progress(
                ReportProgress(
                    stage="completed",
                    percent=100,
                    message="Expert report completed.",
                )
            )
        return report
```

- [ ] **Step 4: 运行专家评估测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_expert_evaluator.py tests/test_report_evaluator.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```powershell
git add app/services/evaluator_ext.py tests/test_expert_evaluator.py
git commit -m "feat: add expert evaluator with rag injection"
```

---

### Task 8: 扩展 Store 进度方法与后台任务桥接

**Files:**
- Modify: `app/services/session.py`
- Modify: `app/services/report_tasks.py`
- Create: `tests/test_report_progress.py`
- Modify: `tests/test_report_tasks.py`
- Modify: `tests/test_session_report_store.py`

- [ ] **Step 1: 先写失败测试，锁定 `update_report_progress(...)`**

创建 `tests/test_report_progress.py`：

```python
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import ReportProgress
from app.services.session import InterviewSessionStore


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="Backend interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain Redis cache invalidation.",
                    focus="Redis reliability",
                )
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please continue."

    def generate_report(self, plan, evaluation_items, session_id):
        raise AssertionError


def test_update_report_progress_updates_processing_record():
    store = InterviewSessionStore(llm=FakeLLM())
    session = store.start(
        FakeLLM().generate_plan("Backend role", "Backend resume"),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )
    state = store.get(session.session_id)
    state["status"] = "finished"
    state["current_index"] = len(state["plan"].questions)
    store.mark_report_processing(session.session_id)

    store.update_report_progress(
        session.session_id,
        ReportProgress(
            stage="analyzing",
            percent=60,
            message="Analyzing Redis depth.",
            current_question_id="q1",
        ),
    )

    record = store.get_report_record(session.session_id)
    assert record.progress.stage == "analyzing"
    assert record.progress.percent == 60
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_progress.py -q
```

Expected: FAIL，提示 `update_report_progress` 不存在。

- [ ] **Step 3: 实现 Store 进度方法并初始化 progress**

在 `app/services/session.py` 中新增：

```python
from app.services.report import InterviewReport, ReportProgress, ReportRecord
```

把 `mark_report_processing(...)` 改成：

```python
    def mark_report_processing(self, session_id: str) -> bool:
        state = self.get(session_id)
        if state["status"] != "finished":
            raise ValueError("interview is not finished")
        if session_id in self._reports:
            return False
        self._reports[session_id] = ReportRecord(
            status="processing",
            progress=ReportProgress(
                stage="retrieving",
                percent=20,
                message="Retrieving role-specific knowledge references.",
            ),
        )
        return True
```

新增：

```python
    def update_report_progress(
        self,
        session_id: str,
        progress: ReportProgress,
    ) -> None:
        self.get(session_id)
        record = self._reports.get(session_id)
        if record is None:
            raise ValueError("report record not found")
        if record.status != "processing":
            raise ValueError("report is not processing")
        self._reports[session_id] = ReportRecord(
            status="processing",
            progress=progress,
        )
```

- [ ] **Step 4: 用工厂注入 `vector_store` 并桥接进度回调**

把 `app/services/report_tasks.py` 改成：

```python
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.report import ReportGenerationFailed, ReportGenerationTimeout
from app.services.session import InterviewSessionStore
from app.services.vector_store import get_knowledge_store


def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
) -> None:
    try:
        state = store.get(session_id)
    except ValueError:
        return

    try:
        if state["status"] != "finished":
            raise ReportGenerationFailed("interview is not finished")

        def publish_progress(progress):
            store.update_report_progress(session_id, progress)

        evaluator = ExpertShadowEvaluator(
            llm=_resolve_llm(store),
            vector_store=get_knowledge_store(),
        )
        report = evaluator.evaluate(state, on_progress=publish_progress)
        store.save_report(session_id, report)
    except (ReportGenerationTimeout, ReportGenerationFailed) as exc:
        store.fail_report(session_id, str(exc))
    except Exception as exc:
        store.fail_report(session_id, str(exc))
```

- [ ] **Step 5: 运行 Store / 任务相关测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_progress.py tests/test_report_tasks.py tests/test_session_report_store.py -q
```

Expected: PASS 或仅因尚未实现 `get_knowledge_store()` 真实逻辑而在集成路径失败。

- [ ] **Step 6: 提交**

```powershell
git add app/services/session.py app/services/report_tasks.py tests/test_report_progress.py tests/test_report_tasks.py tests/test_session_report_store.py
git commit -m "feat: track report progress in store and tasks"
```

---

### Task 9: 升级报告 API 返回进度，并保持 404 语义不变

**Files:**
- Modify: `app/api/routes.py`
- Modify: `tests/test_report_api.py`

- [ ] **Step 1: 先写失败测试，锁定 `202` body 新结构**

在 `tests/test_report_api.py` 中把 processing 断言改成：

```python
def test_report_endpoint_returns_202_with_progress():
    client, store, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing"
    assert body["progress"]["stage"] == "retrieving"
    assert body["progress"]["percent"] == 20
```

- [ ] **Step 2: 运行聚焦测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py::test_report_endpoint_returns_202_with_progress -q
```

Expected: FAIL，因为当前 `202` 返回体没有 `progress`。

- [ ] **Step 3: 升级 `GET /report` 接口**

把 `app/api/routes.py` 的相关分支改成：

```python
    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        return JSONResponse(
            status_code=202,
            content={
                "status": "processing",
                "progress": record.progress.model_dump() if record and record.progress else None,
            },
        )
```

保留当前 404 逻辑：

```python
    try:
        state = store.get(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=404, detail="interview is not finished")
```

- [ ] **Step 4: 运行报告 API 测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```powershell
git add app/api/routes.py tests/test_report_api.py
git commit -m "feat: return report progress from api"
```

---

### Task 10: 前端显示进度与新报告结构

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: 先写失败静态测试，锁定进度字段读取**

把 `tests/test_static_report_ui.py` 中的 JS 断言扩展成：

```python
def test_app_js_reads_progress_fields():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "body.progress" in js
    assert "progress.message" in js
    assert "progress.percent" in js
    assert "overall_dimension_scores" in js
```

- [ ] **Step 2: 运行静态测试，确认失败**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: FAIL，因为当前前端还没有读取 `progress`。

- [ ] **Step 3: 升级前端轮询与渲染**

在 `app/static/app.js` 中：

1. 把 `pollReport()` 里的 `202` 分支改成先解析 body：

```javascript
    const response = await fetch(`/api/interviews/${sessionId}/report`);
    const body = await response.json().catch(() => ({}));
    if (response.status === 202) {
      renderReportProcessing(body.progress || null);
      reportPollTimer = setTimeout(pollReport, 3000);
      return;
    }
```

2. 把 `renderReportProcessing()` 改成：

```javascript
function renderReportProcessing(progress) {
  reportSection.hidden = false;
  reportSection.className = "report-section";
  reportStatus.textContent = "Report processing";
  reportContent.innerHTML = "";

  const message = progress?.message || "AI is reviewing the interview.";
  const percent = typeof progress?.percent === "number" ? `${progress.percent}%` : "";
  reportContent.appendChild(
    createEl("p", "report-note", percent ? `${percent} - ${message}` : message)
  );
}
```

3. 在 `renderReport(report)` 中，在 overview 后追加全局维度：

```javascript
  const dimensions = createEl("div", "report-dimensions");
  Object.entries(report.overall_dimension_scores).forEach(([name, value]) => {
    const row = createEl("div", "dimension-row");
    row.appendChild(createEl("span", "dimension-name", name));
    row.appendChild(createEl("span", "dimension-value", String(value)));
    dimensions.appendChild(row);
  });
  reportContent.appendChild(dimensions);
```

4. 在 `renderFeedback(feedback)` 中追加：

```javascript
  const dimensions = createEl("div", "feedback-dimensions");
  Object.entries(feedback.dimension_scores).forEach(([name, value]) => {
    const row = createEl("span", "feedback-dimension", `${name}: ${value}`);
    dimensions.appendChild(row);
  });
  item.appendChild(dimensions);

  item.appendChild(createEl("p", "feedback-rationale", feedback.rationale));
```

- [ ] **Step 4: 补充样式**

在 `app/static/styles.css` 追加：

```css
.report-dimensions,
.feedback-dimensions {
  display: grid;
  gap: 8px;
}

.dimension-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.feedback-dimensions {
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  margin-top: 10px;
}

.feedback-dimension {
  background: var(--report-strong);
  border-radius: 4px;
  padding: 8px;
}

.feedback-rationale {
  margin: 10px 0 0;
}
```

- [ ] **Step 5: 运行静态测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected: PASS

- [ ] **Step 6: 提交**

```powershell
git add app/static/index.html app/static/app.js app/static/styles.css tests/test_static_report_ui.py
git commit -m "feat: render expert report progress and dimensions"
```

---

### Task 11: 增加最小知识资产与导入脚本骨架

**Files:**
- Create: `app/data/knowledge/benchmarks/redis_backend.md`
- Create: `app/data/knowledge/theory/redis_consistency.md`
- Create: `scripts/load_knowledge.py`

- [ ] **Step 1: 新增最小 benchmark 文件**

创建 `app/data/knowledge/benchmarks/redis_backend.md`：

```md
# Redis Backend Project Benchmark

## High-score answer pattern

- Start with business context.
- State the technical bottleneck.
- Explain why Redis was introduced.
- Mention cache invalidation, fallback, and measurable results.

## Bonus points

- Mentions race conditions after database writes.
- Mentions delayed double delete or equivalent mitigation.
- Mentions p95 latency, hit ratio, or database load reduction.
```

- [ ] **Step 2: 新增最小 theory 文件**

创建 `app/data/knowledge/theory/redis_consistency.md`：

```md
# Redis Cache Consistency

Cache consistency in a cache-aside design usually means updating the database first and then deleting the cache.

Key gaps to watch for in interview answers:

- Ignoring race conditions between concurrent reads and writes.
- Ignoring fallback behavior when Redis is unavailable.
- Ignoring delayed cleanup or retry strategies.
```

- [ ] **Step 3: 新增导入脚本骨架**

创建 `scripts/load_knowledge.py`：

```python
from pathlib import Path

from app.services.vector_store import KnowledgeChunk


KNOWLEDGE_ROOT = Path("app/data/knowledge")


def iter_markdown_files() -> list[Path]:
    return sorted(KNOWLEDGE_ROOT.rglob("*.md"))


def build_chunks() -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for path in iter_markdown_files():
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        domain = "redis" if "redis" in path.name.lower() else "general"
        source_type = "expert_benchmark" if "benchmarks" in path.parts else "theory"
        chunks.append(
            KnowledgeChunk(
                chunk_id=path.stem,
                title=path.stem.replace("_", " ").title(),
                content=content,
                source_type=source_type,
                domain=domain,
                tags=[domain],
                metadata={"source_path": str(path)},
            )
        )
    return chunks


if __name__ == "__main__":
    print(f"Discovered {len(build_chunks())} knowledge chunks.")
```

- [ ] **Step 4: 手动验证导入脚本发现知识文件**

Run:

```powershell
& 'F:\python3.11\python.exe' scripts/load_knowledge.py
```

Expected: 输出 `Discovered 2 knowledge chunks.`

- [ ] **Step 5: 提交**

```powershell
git add app/data/knowledge/benchmarks/redis_backend.md app/data/knowledge/theory/redis_consistency.md scripts/load_knowledge.py
git commit -m "feat: add minimum knowledge base assets"
```

---

### Task 12: 增加 Golden Dataset 最小基线

**Files:**
- Create: `tests/golden/redis_strong_answer.json`
- Create: `tests/golden/redis_weak_answer.json`

- [ ] **Step 1: 新增强回答样本**

创建 `tests/golden/redis_strong_answer.json`：

```json
{
  "question": "Explain Redis cache invalidation.",
  "job_tags": ["python", "redis"],
  "answer": "I use cache-aside. After database writes I delete cache, handle race conditions with retry or delayed cleanup, keep Redis fallback to the database, and I watch p95 latency and hit ratio.",
  "expected_signals": [
    "cache-aside",
    "race conditions",
    "fallback",
    "p95 latency"
  ]
}
```

- [ ] **Step 2: 新增弱回答样本**

创建 `tests/golden/redis_weak_answer.json`：

```json
{
  "question": "Explain Redis cache invalidation.",
  "job_tags": ["python", "redis"],
  "answer": "I use Redis to make things faster and clear the cache sometimes.",
  "expected_gaps": [
    "race conditions",
    "fallback",
    "consistency"
  ]
}
```

- [ ] **Step 3: 提交**

```powershell
git add tests/golden/redis_strong_answer.json tests/golden/redis_weak_answer.json
git commit -m "test: add golden dataset seed cases"
```

---

### Task 13: 全量回归与计划对照验收

**Files:**
- Review: `app/services/report.py`
- Review: `app/services/job_tags.py`
- Review: `app/services/vector_store.py`
- Review: `app/services/evaluator.py`
- Review: `app/services/evaluator_ext.py`
- Review: `app/services/llm.py`
- Review: `app/services/session.py`
- Review: `app/services/report_tasks.py`
- Review: `app/api/routes.py`
- Review: `app/static/app.js`
- Review: `tests/*`

- [ ] **Step 1: 运行后端全量测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: 全部通过；若 `@pytest.mark.pgvector` 存在但未启用，不影响默认回归。

- [ ] **Step 2: 运行可选 pgvector 标记测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q -m pgvector
```

Expected: 在未配置 PostgreSQL 环境时可以跳过；在已配置环境时通过。

- [ ] **Step 3: 检查变更范围**

Run:

```powershell
git diff -- app tests scripts
```

Expected:

- 没有把 `session/report` 持久化顺手迁到 PostgreSQL
- 没有引入 Redis / Celery / WebSocket
- `job_tags` 只走规则提取，不依赖 LLM
- `BGE-M3` 和 `1024` 维度已固定

- [ ] **Step 4: 运行关键链路测试**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py::test_finished_answer_triggers_report_generation_once -q
```

Expected: PASS

- [ ] **Step 5: 如有收尾修复则提交**

如发现小问题并修复：

```powershell
git add app tests scripts
git commit -m "test: verify expert evaluation flow"
```

如无额外修复，则跳过。

---

## 自检清单

- [ ] `BAAI/bge-m3` 与 `VECTOR(1024)` 已固定，不再悬空。
- [ ] `InterviewState` 已包含 `job_description`、`resume_text`、`job_tags`。
- [ ] `POST /interviews -> store.start(...) -> runner.start(...) -> build_initial_state(...)` 传播链完整。
- [ ] `job_tags` 明确采用规则提取而不是 LLM。
- [ ] `ReportRecord` 支持 `progress`。
- [ ] `InterviewSessionStore.update_report_progress(...)` 已设计并测试。
- [ ] `ExpertShadowEvaluator` 与 `ShadowEvaluator` 的关系是“组合复用”。
- [ ] `generate_report_for_session(...)` 通过 `get_knowledge_store()` 注入向量库。
- [ ] `GET /report` 的 404 语义保持区分。
- [ ] 破坏性 schema 升级带来的测试构造数据修复已单列任务。
- [ ] PostgreSQL 测试策略明确为“默认 mock，集成测试单独打标”。
- [ ] 仓库内至少已有一份 benchmark 和一份 theory 知识文件。

## 执行交接

Plan complete and saved to `docs/superpowers/plans/2026-07-02-stage-4-expert-evaluation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
