# Stage 31 Knowledge PrepGraph Preheat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the preparation phase produce a visible, deterministic Knowledge Agent preheat context so the interview plan explains what it will test and why.

**Architecture:** Keep Local V1 on the existing FastAPI four-page flow and keep `/api/interviews` creating sessions from an `InterviewPlan`. Add optional prep metadata to `InterviewPlan` instead of introducing a new persistence table: `prep_context` contains role tags, knowledge topics, and per-question hints/evidence summaries. Knowledge preheat is best-effort and deterministic: LLM still generates the question plan, local keyword/topic logic attaches explainable context, and pgvector retrieval is reserved for report/runtime evidence rather than blocking prep.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, vanilla JS, existing static prep page, existing job tag extraction.

---

## File Structure

- Modify: `app/services/prep.py`
  - Add `PrepKnowledgeTopic`, `PrepQuestionHint`, and `PrepContext` Pydantic models.
  - Add optional `prep_context` to `InterviewPlan`.
  - Add pure helpers for deterministic topic and question-hint generation.

- Modify: `app/agents/knowledge.py`
  - Keep the existing LLM plan-generation boundary.
  - Attach deterministic prep context after plan generation.
  - Ensure fallback plans can also be enriched by `prepare_interview()`.

- Modify: `app/services/llm.py`
  - Update the plan prompt to tell providers that prep metadata is optional and the service will enrich it locally.

- Modify: `app/api/routes.py`
  - Keep `/api/prep` returning `title`, `questions`, and top-level `job_tags`.
  - Let `prep_context` pass through as part of `InterviewPlan.model_dump()`.

- Modify: `app/test4.html`
  - Add stable DOM hooks for rendering Knowledge Agent preheat context.

- Modify: `app/static/prep.js`
  - Render `prep_context.topics` and `prep_context.question_hints`.

- Modify: `tests/test_prep_service.py`
  - Cover deterministic preheat context generation, fallback enrichment, and serialization.

- Modify: `tests/test_api.py`
  - Cover `/api/prep` returning `prep_context` with top-level `job_tags`.

- Modify: `tests/test_static_report_ui.py`
  - Cover prep-page DOM hooks and frontend rendering code.

- Modify: `tests/test_local_v1_docs.py`
  - Cover README/runbook documentation for Stage 31.

- Modify: `README.md`
  - Document Stage 31 architecture position.

- Modify: `docs/local-v1-runbook.md`
  - Add local verification notes for Knowledge Agent preheat.

---

### Task 1: Add Prep Context Models And Deterministic Builders

**Files:**
- Modify: `app/services/prep.py`
- Test: `tests/test_prep_service.py`

- [ ] **Step 1: Write the failing prep-context tests**

Append to `tests/test_prep_service.py`:

```python
from app.services.prep import build_prep_context


def test_build_prep_context_extracts_topics_and_question_hints():
    plan = InterviewPlan(
        title="Backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis reliability",
            ),
            InterviewQuestion(
                id="q2",
                kind="system-design",
                prompt="Design a scalable FastAPI service.",
                focus="system design",
            ),
        ],
    )

    context = build_prep_context(
        job_description="Backend role using Python, FastAPI, Redis, MySQL, and Kafka.",
        resume_text="Built a FastAPI API with Redis cache and MySQL indexes.",
        job_tags=["python", "fastapi", "redis", "mysql", "kafka"],
        plan=plan,
    )

    assert context.summary == "Knowledge Agent 预热了 5 个岗位考点，并为 2 道题生成追问线索。"
    assert [topic.id for topic in context.topics] == [
        "topic-python",
        "topic-fastapi",
        "topic-redis",
        "topic-mysql",
        "topic-kafka",
    ]
    redis_topic = context.topics[2]
    assert redis_topic.label == "Redis"
    assert redis_topic.evidence == "JD 和简历同时命中 Redis，适合作为缓存一致性、穿透保护和高并发追问依据。"
    assert redis_topic.source == "jd_resume_keyword"
    assert context.question_hints[0].question_id == "q1"
    assert "topic-redis" in context.question_hints[0].topic_ids
    assert "追问缓存一致性、失效时机、穿透保护和降级兜底。" in context.question_hints[0].follow_up_hints
    assert context.question_hints[1].question_id == "q2"
    assert context.question_hints[1].topic_ids


def test_build_prep_context_uses_general_topic_when_tags_are_empty():
    plan = InterviewPlan(
        title="General interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce your project.",
                focus="project depth",
            )
        ],
    )

    context = build_prep_context(
        job_description="Backend role.",
        resume_text="Built internal tools.",
        job_tags=[],
        plan=plan,
    )

    assert [topic.id for topic in context.topics] == ["topic-general"]
    assert context.topics[0].label == "通用后端能力"
    assert context.question_hints[0].topic_ids == ["topic-general"]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_service.py::test_build_prep_context_extracts_topics_and_question_hints tests/test_prep_service.py::test_build_prep_context_uses_general_topic_when_tags_are_empty -q
```

Expected: FAIL with `ImportError` or `AttributeError` because `build_prep_context` and prep-context models do not exist.

- [ ] **Step 3: Add prep-context models and helpers**

Modify `app/services/prep.py` so the top-level models become:

```python
from typing import Literal

from pydantic import BaseModel, Field

from app.services.llm import InterviewLLM


class PrepKnowledgeTopic(BaseModel):
    id: str
    label: str
    source: Literal["jd_keyword", "resume_keyword", "jd_resume_keyword", "fallback"]
    evidence: str
    tags: list[str] = Field(default_factory=list)


class PrepQuestionHint(BaseModel):
    question_id: str
    topic_ids: list[str] = Field(default_factory=list)
    follow_up_hints: list[str] = Field(default_factory=list)
    evidence_titles: list[str] = Field(default_factory=list)


class PrepContext(BaseModel):
    summary: str
    topics: list[PrepKnowledgeTopic] = Field(default_factory=list)
    question_hints: list[PrepQuestionHint] = Field(default_factory=list)
```

Update `InterviewPlan`:

```python
class InterviewPlan(BaseModel):
    title: str
    questions: list[InterviewQuestion]
    prep_context: PrepContext | None = None
```

Add these constants and helpers below `fallback_interview_plan()`:

```python
_TOPIC_LABELS = {
    "python": "Python",
    "fastapi": "FastAPI",
    "redis": "Redis",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "java": "Java",
    "spring": "Spring",
    "kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "system-design": "系统设计",
    "general": "通用后端能力",
}

_TOPIC_HINTS = {
    "python": "追问 Python 运行时、异步模型、异常处理和工程质量。",
    "fastapi": "追问 FastAPI 依赖注入、请求生命周期、异步接口和可测试性。",
    "redis": "追问缓存一致性、失效时机、穿透保护和降级兜底。",
    "postgresql": "追问索引设计、事务隔离、慢查询定位和连接池配置。",
    "mysql": "追问索引设计、事务隔离、慢查询定位和表结构取舍。",
    "java": "追问 JVM、并发模型、集合框架和服务稳定性。",
    "spring": "追问 Spring Bean 生命周期、事务边界和依赖注入。",
    "kafka": "追问消息可靠性、消费语义、重试和积压处理。",
    "rabbitmq": "追问消息确认、死信队列、重试和削峰策略。",
    "system-design": "追问容量估算、瓶颈定位、故障隔离和演进方案。",
    "general": "追问项目背景、职责边界、技术取舍和量化结果。",
}


def build_prep_context(
    *,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
    plan: InterviewPlan,
) -> PrepContext:
    normalized_tags = _normalize_topic_tags(job_tags)
    topics = [
        _build_topic(tag, job_description=job_description, resume_text=resume_text)
        for tag in normalized_tags
    ]
    question_hints = [
        _build_question_hint(question, topics=topics)
        for question in plan.questions
    ]
    return PrepContext(
        summary=(
            f"Knowledge Agent 预热了 {len(topics)} 个岗位考点，"
            f"并为 {len(question_hints)} 道题生成追问线索。"
        ),
        topics=topics,
        question_hints=question_hints,
    )


def attach_prep_context(
    plan: InterviewPlan,
    *,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> InterviewPlan:
    return plan.model_copy(
        update={
            "prep_context": build_prep_context(
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                plan=plan,
            )
        }
    )


def _normalize_topic_tags(job_tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for tag in job_tags or ["general"]:
        value = tag.strip().lower()
        if not value:
            continue
        if value not in _TOPIC_LABELS:
            value = "general"
        if value not in normalized:
            normalized.append(value)
    return normalized or ["general"]


def _build_topic(
    tag: str,
    *,
    job_description: str,
    resume_text: str,
) -> PrepKnowledgeTopic:
    label = _TOPIC_LABELS[tag]
    jd_hit = tag != "general" and tag in job_description.lower()
    resume_hit = tag != "general" and tag in resume_text.lower()
    if jd_hit and resume_hit:
        source = "jd_resume_keyword"
        evidence = (
            f"JD 和简历同时命中 {label}，适合作为"
            f"{_topic_evidence_focus(tag)}追问依据。"
        )
    elif jd_hit:
        source = "jd_keyword"
        evidence = f"JD 明确要求 {label}，需要验证候选人是否具备岗位匹配能力。"
    elif resume_hit:
        source = "resume_keyword"
        evidence = f"简历出现 {label}，适合围绕真实项目经历继续深挖。"
    else:
        source = "fallback"
        evidence = "未命中特定技术关键词，先围绕通用后端项目表达和工程实践预热。"
    return PrepKnowledgeTopic(
        id=f"topic-{tag}",
        label=label,
        source=source,
        evidence=evidence,
        tags=[tag],
    )


def _build_question_hint(
    question: InterviewQuestion,
    *,
    topics: list[PrepKnowledgeTopic],
) -> PrepQuestionHint:
    text = f"{question.prompt} {question.focus}".lower()
    matched_topics = [
        topic
        for topic in topics
        if topic.tags[0] == "general"
        or topic.tags[0] in text
        or topic.label.lower() in text
    ]
    if not matched_topics:
        matched_topics = topics[:1]
    return PrepQuestionHint(
        question_id=question.id,
        topic_ids=[topic.id for topic in matched_topics],
        follow_up_hints=[
            _TOPIC_HINTS.get(topic.tags[0], _TOPIC_HINTS["general"])
            for topic in matched_topics
        ],
        evidence_titles=[topic.label for topic in matched_topics],
    )


def _topic_evidence_focus(tag: str) -> str:
    if tag == "redis":
        return "缓存一致性、穿透保护和高并发"
    if tag in {"mysql", "postgresql"}:
        return "索引设计、事务边界和慢查询优化"
    if tag in {"kafka", "rabbitmq"}:
        return "消息可靠性、重试和削峰"
    if tag == "fastapi":
        return "接口设计、依赖注入和异步服务"
    if tag == "system-design":
        return "容量估算、故障隔离和服务演进"
    return "项目深度、工程实践和技术取舍"
```

- [ ] **Step 4: Run focused prep tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_service.py::test_build_prep_context_extracts_topics_and_question_hints tests/test_prep_service.py::test_build_prep_context_uses_general_topic_when_tags_are_empty -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/prep.py tests/test_prep_service.py
git commit -m "feat: add prep knowledge context models"
```

---

### Task 2: Enrich KnowledgeAgent Plans With Prep Context

**Files:**
- Modify: `app/agents/knowledge.py`
- Modify: `app/services/prep.py`
- Modify: `app/services/llm.py`
- Test: `tests/test_prep_service.py`

- [ ] **Step 1: Write failing enrichment tests**

Append to `tests/test_prep_service.py`:

```python
def test_prepare_interview_attaches_prep_context_to_llm_plan():
    plan = prepare_interview(
        job_description="后端岗位，要求 Python、FastAPI、Redis、MySQL。",
        resume_text="做过 FastAPI 服务，使用 Redis 缓存和 MySQL 索引。",
        llm=FakePlanLLM(),
    )

    assert plan.prep_context is not None
    assert plan.prep_context.summary == "Knowledge Agent 预热了 4 个岗位考点，并为 3 道题生成追问线索。"
    assert [topic.label for topic in plan.prep_context.topics] == [
        "Python",
        "FastAPI",
        "Redis",
        "MySQL",
    ]
    assert plan.prep_context.question_hints[1].question_id == "q2"
    assert "topic-redis" in plan.prep_context.question_hints[1].topic_ids


def test_prepare_interview_attaches_prep_context_to_fallback_plan():
    plan = prepare_interview(
        job_description="后端岗位，要求 Redis。",
        resume_text="做过缓存项目。",
        llm=FailingPlanLLM(),
    )

    assert plan.title == "基础模拟面试"
    assert plan.prep_context is not None
    assert [topic.label for topic in plan.prep_context.topics] == ["Redis"]
    assert len(plan.prep_context.question_hints) == len(plan.questions)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_service.py::test_prepare_interview_attaches_prep_context_to_llm_plan tests/test_prep_service.py::test_prepare_interview_attaches_prep_context_to_fallback_plan -q
```

Expected: FAIL because `prepare_interview()` does not yet attach prep context.

- [ ] **Step 3: Update KnowledgeAgent**

Modify `app/agents/knowledge.py`:

```python
from app.services.job_tags import extract_job_tags
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan, attach_prep_context


class KnowledgeAgent:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self.llm = llm

    def generate_plan(self, *, job_description: str, resume_text: str) -> InterviewPlan:
        llm = self.llm or self._default_llm()
        plan = llm.generate_plan(job_description, resume_text)
        return attach_prep_context(
            plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=extract_job_tags(job_description),
        )

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
```

- [ ] **Step 4: Enrich fallback plans in prepare_interview**

Replace `prepare_interview()` in `app/services/prep.py` with:

```python
def prepare_interview(
    job_description: str,
    resume_text: str,
    llm: InterviewLLM | None = None,
) -> InterviewPlan:
    job_description = _require_text("job_description", job_description)
    resume_text = _require_text("resume_text", resume_text)

    try:
        from app.agents.knowledge import KnowledgeAgent

        return KnowledgeAgent(llm=llm).generate_plan(
            job_description=job_description,
            resume_text=resume_text,
        )
    except Exception:
        from app.services.job_tags import extract_job_tags

        return attach_prep_context(
            fallback_interview_plan(),
            job_description=job_description,
            resume_text=resume_text,
            job_tags=extract_job_tags(job_description),
        )
```

- [ ] **Step 5: Update the LLM plan prompt**

In `app/services/llm.py`, add this sentence inside `_build_plan_prompt()` after `"Questions should be specific...":`

```python
            "Do not generate prep_context; the service enriches the plan with Knowledge Agent metadata locally.\n"
```

- [ ] **Step 6: Run prep tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/agents/knowledge.py app/services/prep.py app/services/llm.py tests/test_prep_service.py
git commit -m "feat: enrich interview plans with prep context"
```

---

### Task 3: Expose Prep Context Through API Contracts

**Files:**
- Modify: `tests/test_api.py`
- Modify: `docs/local-v1-runbook.md`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API assertions**

Update `test_prepare_endpoint_returns_job_tags_without_session_store()` in `tests/test_api.py` by adding:

```python
    assert body["prep_context"]["summary"].startswith("Knowledge Agent 预热了")
    assert body["prep_context"]["topics"]
    assert body["prep_context"]["question_hints"]
    assert body["prep_context"]["topics"][0]["id"].startswith("topic-")
```

Update `test_get_interview_session_returns_snapshot()` by adding:

```python
    assert body["questions"][0]["id"] == "q1"
```

Append this new API test:

```python
def test_start_interview_persists_plan_prep_context_in_session_snapshot():
    client = make_client()
    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python, FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built a FastAPI service with Redis cache.",
        },
    ).json()

    response = client.get(f"/api/interviews/{started['session_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["questions"][1]["id"] == "q2"
    assert body["messages"][0]["role"] == "interviewer"
```

Note: session snapshots intentionally expose per-question state, not `prep_context`. The persisted `prep_context` remains inside the internal `plan_json` for future Examiner use, while the preparation page consumes it from `/api/prep`.

- [ ] **Step 2: Run API tests and verify the prep assertion fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_prepare_endpoint_returns_job_tags_without_session_store tests/test_api.py::test_start_interview_persists_plan_prep_context_in_session_snapshot -q
```

Expected before Task 2 implementation: FAIL because `prep_context` is missing. Expected after Task 2 implementation: PASS.

- [ ] **Step 3: Keep API implementation unchanged**

No code change is needed in `app/api/routes.py`. It already returns:

```python
response = plan.model_dump()
response["job_tags"] = extract_job_tags(payload.job_description)
return response
```

Because `prep_context` is now an `InterviewPlan` field, it is included in `/api/prep` automatically. `job_tags` remains a top-level wrapper field for backward compatibility.

- [ ] **Step 4: Run focused API tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_prepare_endpoint_returns_job_tags_without_session_store tests/test_api.py::test_start_interview_persists_plan_prep_context_in_session_snapshot -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_api.py
git commit -m "test: cover prep context api contract"
```

---

### Task 4: Render Knowledge Preheat On The Prep Page

**Files:**
- Modify: `app/test4.html`
- Modify: `app/static/prep.js`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write failing static tests**

Append to `tests/test_static_report_ui.py`:

```python
def test_prep_page_has_knowledge_preheat_runtime_hooks():
    html = read_app_file("test4.html")

    for element_id in (
        "prepContextSummary",
        "prepContextTopics",
        "prepQuestionHints",
    ):
        assert f'id="{element_id}"' in html


def test_prep_js_renders_knowledge_preheat_context():
    js = read_static_file("prep.js")

    assert "const prepContextSummary = byId(\"prepContextSummary\")" in js
    assert "const prepContextTopics = byId(\"prepContextTopics\")" in js
    assert "const prepQuestionHints = byId(\"prepQuestionHints\")" in js
    assert "function renderPrepContext(prepContext)" in js
    assert "prepContext.topics" in js
    assert "prepContext.question_hints" in js
```

- [ ] **Step 2: Run static tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_prep_page_has_knowledge_preheat_runtime_hooks tests/test_static_report_ui.py::test_prep_js_renders_knowledge_preheat_context -q
```

Expected: FAIL because the hooks and rendering function do not exist.

- [ ] **Step 3: Add prep-context HTML hooks**

In `app/test4.html`, add this block after the `planQuestions` section and before the blue info box:

```html
                        <div class="pt-6 border-t border-gray-100 mt-6">
                            <div class="text-[14px] font-bold text-gray-800 mb-3 flex items-center gap-2">
                                <i class="fa-solid fa-brain text-gray-400"></i> Knowledge Agent 预热
                            </div>
                            <p id="prepContextSummary" class="text-[12px] text-gray-500 leading-relaxed mb-4">等待生成面试计划后展示考点预热结果。</p>
                            <div id="prepContextTopics" class="flex flex-wrap gap-2 mb-4"></div>
                            <ul id="prepQuestionHints" class="space-y-3 text-[12.5px] text-gray-600"></ul>
                        </div>
```

- [ ] **Step 4: Render prep context in JavaScript**

In `app/static/prep.js`, add constants near the existing DOM lookups:

```javascript
const prepContextSummary = byId("prepContextSummary");
const prepContextTopics = byId("prepContextTopics");
const prepQuestionHints = byId("prepQuestionHints");
```

Add this function after `renderPlan(plan)` or before it:

```javascript
function renderPrepContext(prepContext) {
  clear(prepContextTopics);
  clear(prepQuestionHints);
  if (!prepContext) {
    if (prepContextSummary) {
      prepContextSummary.textContent = "等待生成面试计划后展示考点预热结果。";
    }
    return;
  }

  if (prepContextSummary) {
    prepContextSummary.textContent = prepContext.summary || "Knowledge Agent 已完成考点预热。";
  }

  for (const topic of prepContext.topics || []) {
    const label = topic.label || topic.id || "考点";
    const item = createEl("span", "px-2.5 py-1 bg-blue-50 text-blue-600 text-[12px] rounded border border-blue-100", label);
    item.title = topic.evidence || "";
    prepContextTopics.appendChild(item);
  }

  for (const hint of prepContext.question_hints || []) {
    const item = createEl("li", "bg-gray-50 border border-gray-100 rounded-lg p-3");
    const title = createEl("div", "font-medium text-gray-700 mb-1", `${hint.question_id || "Q"} 追问线索`);
    const body = createEl("div", "leading-relaxed text-gray-500", (hint.follow_up_hints || []).join(" "));
    item.appendChild(title);
    item.appendChild(body);
    prepQuestionHints.appendChild(item);
  }
}
```

Then update `renderPlan(plan)`:

```javascript
function renderPlan(plan) {
  latestPlan = plan;
  setText("planTitle", plan.title || "面试计划");
  clear(planQuestions);
  for (const question of plan.questions || []) {
    const item = createEl("li", "flex items-start gap-3");
    item.appendChild(createEl("span", "w-5 h-5 rounded-full bg-blue-50 text-blue-500 border border-blue-100 flex items-center justify-center text-[11px] font-medium shrink-0 mt-0.5", question.id || "Q"));
    const text = createEl("span", "leading-snug", question.prompt || "");
    if (question.focus) {
      text.title = question.focus;
    }
    item.appendChild(text);
    planQuestions.appendChild(item);
  }
  setCurrentTags(plan.job_tags || []);
  renderPrepContext(plan.prep_context);
}
```

At the end of the file, after `setCurrentTags([]);`, add:

```javascript
renderPrepContext(null);
```

- [ ] **Step 5: Run static and JS checks**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_prep_page_has_knowledge_preheat_runtime_hooks tests/test_static_report_ui.py::test_prep_js_renders_knowledge_preheat_context -q
node --check app/static/prep.js
npm run build:prototype-css
```

Expected: PASS. CSS build may print a Browserslist notice; that notice is acceptable.

- [ ] **Step 6: Commit**

```bash
git add app/test4.html app/static/prep.js tests/test_static_report_ui.py
git commit -m "feat: render prep knowledge preheat"
```

---

### Task 5: Document Stage 31 And Run Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write failing docs test**

Append to `tests/test_local_v1_docs.py`:

```python
def test_docs_describe_stage_31_knowledge_prepgraph_preheat():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 31 makes Knowledge Agent preheat visible during interview preparation"
    assert expected in readme
    assert expected in runbook
    assert "prep_context" in readme
    assert "prep_context" in runbook
    assert "does not add WebSocket or Redis checkpoints" in readme
```

- [ ] **Step 2: Run docs test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_31_knowledge_prepgraph_preheat -q
```

Expected: FAIL because Stage 31 docs do not exist yet.

- [ ] **Step 3: Update README**

Add this paragraph under `## Current Architecture Position` after the Stage 30/Stage 29 paragraph:

```markdown
Stage 31 makes Knowledge Agent preheat visible during interview preparation. `/api/prep` now returns an optional `prep_context` with deterministic role topics, per-question follow-up hints, and evidence summaries derived from the JD, resume, and generated plan. This stage improves explainability of question selection and prepares a future Examiner hint path, but it does not add WebSocket or Redis checkpoints.
```

- [ ] **Step 4: Update runbook**

Add this paragraph under `## 1.1 Architecture Position` after the Stage 30 paragraph:

```markdown
Stage 31 makes Knowledge Agent preheat visible during interview preparation. Local verification should confirm `/api/prep` returns `prep_context.summary`, `prep_context.topics`, and `prep_context.question_hints`, and that the prep page renders those fields before the interview starts.
```

Add this check under the browser acceptance section after the plan-generation step:

```markdown
Stage 31 Knowledge Agent preheat checks:

1. Generate a plan from a JD and resume that mention Redis, MySQL/PostgreSQL, FastAPI, and system design.
2. Confirm `/api/prep` returns `prep_context.summary`, at least one topic, and at least one question hint.
3. Confirm the prep page renders Knowledge Agent preheat topics and per-question follow-up hints.
4. Confirm starting the interview still works without requiring Redis, WebSocket, or a new persistence service.
```

- [ ] **Step 5: Run docs test**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_31_knowledge_prepgraph_preheat -q
```

Expected: PASS.

- [ ] **Step 6: Run focused Stage 31 sweep**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_service.py tests/test_api.py::test_prepare_endpoint_returns_job_tags_without_session_store tests/test_api.py::test_start_interview_persists_plan_prep_context_in_session_snapshot tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
node --check app/static/prep.js
npm run build:prototype-css
```

Expected: PASS.

- [ ] **Step 7: Run full regression**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: PASS, with PostgreSQL-specific tests allowed to skip when their fixture prerequisites are unavailable.

- [ ] **Step 8: Commit**

```bash
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: describe stage 31 knowledge preheat"
```

---

## Verification Sweep

After all tasks are complete, run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_service.py tests/test_api.py::test_prepare_endpoint_returns_job_tags_without_session_store tests/test_api.py::test_start_interview_persists_plan_prep_context_in_session_snapshot tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
node --check app/static/prep.js
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected:

- Prep service tests pass.
- `/api/prep` exposes `prep_context` and keeps top-level `job_tags`.
- Prep page renders Knowledge Agent preheat data.
- Full pytest remains green.
- Static JS syntax remains valid.
- CSS build remains green.

## Self-Review

- Spec coverage: The plan covers visible Knowledge Agent preheat, deterministic topic extraction, per-question hints, API exposure, prep-page rendering, documentation, and regression tests. It intentionally does not add Redis, WebSocket, pgvector blocking prep, or live Examiner hint consumption.
- Placeholder scan: No unresolved placeholder instructions or repeated-task shorthand remains.
- Type consistency: `prep_context`, `topics`, `question_hints`, `topic_ids`, `follow_up_hints`, and `evidence_titles` use consistent names across models, tests, API responses, and frontend rendering.
