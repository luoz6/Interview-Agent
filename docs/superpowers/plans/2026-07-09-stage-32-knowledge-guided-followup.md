# Stage 32 Knowledge-Guided Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use Stage 31 `prep_context` to guide interview follow-up generation without adding new runtime infrastructure.

**Architecture:** Keep `prep_context` inside `InterviewPlan` as the source of truth. Add a small helper that converts the current question's prep hint into a `knowledge_agent` context message, then append that message to the existing follow-up context used by `InterviewGraphRunner` for both normal and streaming follow-ups. Do not add WebSocket, Redis checkpoints, pgvector prep blocking, or a new persistence table.

**Tech Stack:** Python 3.11, Pydantic v2 models already in `app/services/prep.py`, existing FastAPI/session graph runtime, pytest, existing `OpenAIInterviewLLM` prompt builder.

---

## Execution Notes

- The current worktree may contain unrelated Stage 29/30 dirty files. Before each commit, inspect `git diff -- <files>` and stage only the files listed in that task.
- `tests/test_api.py` is known to have pre-existing dirty hunks in some branches. This plan avoids modifying it.
- Keep user-visible Chinese strings UTF-8, not mojibake.

---

## File Structure

- Create: `app/services/prep_context.py`
  - Pure helper functions for reading `InterviewPlan.prep_context`.
  - Converts the current question hint into a single LLM context message.

- Create: `tests/test_prep_context.py`
  - Unit tests for hint lookup, topic evidence rendering, and missing-context fallback.

- Modify: `app/graphs/interview_graph.py`
  - Import helper from `app.services.prep_context`.
  - Add current question prep hint to `_build_followup_context(state)`.
  - Use the same enriched context for streamed and non-streamed follow-up generation.

- Modify: `tests/test_interview_graph.py`
  - Cover that `InterviewGraphRunner` passes `knowledge_agent` context into the Examiner boundary.
  - Cover that streaming follow-up uses the same enriched context.
  - Cover that plans without `prep_context` preserve current context behavior.

- Modify: `app/services/llm.py`
  - Update `_build_followup_prompt()` so `knowledge_agent` context is explicitly treated as guidance, not transcript.

- Modify: `tests/test_llm_service.py`
  - Cover that the follow-up prompt preserves `knowledge_agent` guidance and tells the model how to use it.

- Modify: `README.md`
  - Document Stage 32 architecture position.

- Modify: `docs/local-v1-runbook.md`
  - Add local verification notes for knowledge-guided follow-up.

- Modify: `tests/test_local_v1_docs.py`
  - Add docs coverage for Stage 32.

---

### Task 1: Add Prep Context Follow-up Helpers

**Files:**
- Create: `app/services/prep_context.py`
- Create: `tests/test_prep_context.py`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_prep_context.py`:

```python
from app.services.prep import (
    InterviewPlan,
    InterviewQuestion,
    PrepContext,
    PrepKnowledgeTopic,
    PrepQuestionHint,
)
from app.services.prep_context import (
    build_question_prep_context_messages,
    get_question_prep_hint,
)


def make_plan_with_prep_context() -> InterviewPlan:
    return InterviewPlan(
        title="Backend plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis consistency",
            ),
            InterviewQuestion(
                id="q2",
                kind="system-design",
                prompt="Design a scalable FastAPI service.",
                focus="system design",
            ),
        ],
        prep_context=PrepContext(
            summary="Knowledge Agent 预热了 2 个岗位考点，并为 2 道题生成追问线索。",
            topics=[
                PrepKnowledgeTopic(
                    id="topic-redis",
                    label="Redis",
                    source="jd_resume_keyword",
                    evidence="JD 和简历同时命中 Redis，适合追问缓存一致性。",
                    tags=["redis"],
                ),
                PrepKnowledgeTopic(
                    id="topic-fastapi",
                    label="FastAPI",
                    source="jd_resume_keyword",
                    evidence="JD 和简历同时命中 FastAPI，适合追问接口设计。",
                    tags=["fastapi"],
                ),
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    topic_ids=["topic-redis"],
                    follow_up_hints=["追问缓存一致性、失效时机、穿透保护和降级兜底。"],
                    evidence_titles=["Redis"],
                ),
                PrepQuestionHint(
                    question_id="q2",
                    topic_ids=["topic-fastapi"],
                    follow_up_hints=["追问 FastAPI 依赖注入、请求生命周期和异步接口。"],
                    evidence_titles=["FastAPI"],
                ),
            ],
        ),
    )


def test_get_question_prep_hint_returns_matching_hint():
    plan = make_plan_with_prep_context()

    hint = get_question_prep_hint(plan, "q1")

    assert hint is not None
    assert hint.question_id == "q1"
    assert hint.topic_ids == ["topic-redis"]


def test_build_question_prep_context_messages_formats_guidance():
    plan = make_plan_with_prep_context()

    messages = build_question_prep_context_messages(plan, "q1")

    assert messages == [
        {
            "role": "knowledge_agent",
            "content": (
                "Prep guidance for q1: focus topics Redis. "
                "Suggested follow-up angles: 追问缓存一致性、失效时机、穿透保护和降级兜底。 "
                "Evidence: JD 和简历同时命中 Redis，适合追问缓存一致性。"
            ),
        }
    ]


def test_build_question_prep_context_messages_returns_empty_without_context():
    plan = InterviewPlan(
        title="No prep context",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis.",
                focus="Redis",
            )
        ],
    )

    assert build_question_prep_context_messages(plan, "q1") == []
    assert get_question_prep_hint(plan, "q1") is None


def test_build_question_prep_context_messages_returns_empty_for_unknown_question():
    plan = make_plan_with_prep_context()

    assert build_question_prep_context_messages(plan, "missing") == []
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_context.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.prep_context'`.

- [ ] **Step 3: Add helper implementation**

Create `app/services/prep_context.py`:

```python
from app.services.prep import InterviewPlan, PrepQuestionHint


def get_question_prep_hint(
    plan: InterviewPlan,
    question_id: str | None,
) -> PrepQuestionHint | None:
    if not question_id or plan.prep_context is None:
        return None
    for hint in plan.prep_context.question_hints:
        if hint.question_id == question_id:
            return hint
    return None


def build_question_prep_context_messages(
    plan: InterviewPlan,
    question_id: str | None,
) -> list[dict[str, str]]:
    hint = get_question_prep_hint(plan, question_id)
    if hint is None or plan.prep_context is None:
        return []

    topic_lookup = {topic.id: topic for topic in plan.prep_context.topics}
    topics = [topic_lookup[topic_id] for topic_id in hint.topic_ids if topic_id in topic_lookup]
    topic_labels = [topic.label for topic in topics] or list(hint.evidence_titles)
    evidence_items = [topic.evidence for topic in topics if topic.evidence]

    parts = [f"Prep guidance for {hint.question_id}:"]
    if topic_labels:
        parts.append(f"focus topics {', '.join(topic_labels)}.")
    if hint.follow_up_hints:
        parts.append(f"Suggested follow-up angles: {' '.join(hint.follow_up_hints)}")
    if evidence_items:
        parts.append(f"Evidence: {' '.join(evidence_items)}")

    content = " ".join(parts).strip()
    return [{"role": "knowledge_agent", "content": content}] if content else []
```

- [ ] **Step 4: Run helper tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_context.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/prep_context.py tests/test_prep_context.py
git commit -m "feat: add prep context followup helper"
```

---

### Task 2: Enrich Interview Graph Follow-up Context

**Files:**
- Modify: `app/graphs/interview_graph.py`
- Modify: `tests/test_interview_graph.py`

- [ ] **Step 1: Write failing graph tests**

Append to `tests/test_interview_graph.py`:

```python
from app.services.prep import PrepContext, PrepKnowledgeTopic, PrepQuestionHint
```

If imports are already grouped near the top, merge these names into the existing `from app.services.prep import ...` import.

Append these tests near the existing Examiner boundary tests:

```python
def make_plan_with_redis_prep_context():
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis consistency",
            )
        ],
        prep_context=PrepContext(
            summary="Knowledge Agent 预热了 1 个岗位考点，并为 1 道题生成追问线索。",
            topics=[
                PrepKnowledgeTopic(
                    id="topic-redis",
                    label="Redis",
                    source="jd_resume_keyword",
                    evidence="JD 和简历同时命中 Redis，适合追问缓存一致性。",
                    tags=["redis"],
                )
            ],
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    topic_ids=["topic-redis"],
                    follow_up_hints=["追问缓存一致性、失效时机、穿透保护和降级兜底。"],
                    evidence_titles=["Redis"],
                )
            ],
        ),
    )


def test_runner_adds_prep_context_to_examiner_followup_context():
    captured_context = []

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            captured_context.extend(context)
            return "How do you prevent cache stampede?"

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(
        session_id="s-prep",
        plan=make_plan_with_redis_prep_context(),
        job_description="Backend role using Redis.",
        resume_text="Built Redis cache.",
        job_tags=["redis"],
    )

    new_state = runner.submit_answer(state, "I delete cache after writing the database.")

    assert new_state["pending_output"] == "How do you prevent cache stampede?"
    prep_messages = [item for item in captured_context if item["role"] == "knowledge_agent"]
    assert len(prep_messages) == 1
    assert "Redis" in prep_messages[0]["content"]
    assert "追问缓存一致性" in prep_messages[0]["content"]


def test_runner_stream_followup_uses_prep_context():
    captured_context = []

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            raise AssertionError("streaming path should call stream_followup")

        def stream_followup(self, *, context: list[dict[str, str]], focus: str):
            captured_context.extend(context)
            yield "streamed prep follow-up"

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(
        session_id="s-prep-stream",
        plan=make_plan_with_redis_prep_context(),
        job_description="Backend role using Redis.",
        resume_text="Built Redis cache.",
        job_tags=["redis"],
    )
    prepared = runner.prepare_answer(state, "I delete cache after DB writes.")

    assert list(runner.stream_followup(prepared)) == ["streamed prep follow-up"]
    assert any(item["role"] == "knowledge_agent" for item in captured_context)


def test_runner_preserves_followup_context_without_prep_context():
    captured_context = []

    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            captured_context.extend(context)
            return "Plain follow-up."

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(**make_start_kwargs())

    runner.submit_answer(state, "I used Redis.")

    assert [item["role"] for item in captured_context] == ["interviewer", "candidate"]
```

- [ ] **Step 2: Run graph tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py::test_runner_adds_prep_context_to_examiner_followup_context tests/test_interview_graph.py::test_runner_stream_followup_uses_prep_context tests/test_interview_graph.py::test_runner_preserves_followup_context_without_prep_context -q
```

Expected: FAIL because `_build_followup_context()` does not yet append `knowledge_agent` messages.

- [ ] **Step 3: Enrich `_build_followup_context()`**

Modify `app/graphs/interview_graph.py`.

Add this import near the other imports:

```python
from app.services.prep_context import build_question_prep_context_messages
```

Replace `_build_followup_context()` with:

```python
def _build_followup_context(state: InterviewState) -> list[dict[str, str]]:
    recent_messages = [
        {"role": message["role"], "content": message["content"]}
        for message in state["messages"][-4:]
    ]
    question = get_current_question(state)
    question_id = question.id if question is not None else None
    return recent_messages + build_question_prep_context_messages(
        state["plan"],
        question_id,
    )
```

- [ ] **Step 4: Run focused graph tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py::test_runner_adds_prep_context_to_examiner_followup_context tests/test_interview_graph.py::test_runner_stream_followup_uses_prep_context tests/test_interview_graph.py::test_runner_preserves_followup_context_without_prep_context -q
```

Expected: PASS.

- [ ] **Step 5: Run existing graph suite**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/graphs/interview_graph.py tests/test_interview_graph.py
git commit -m "feat: guide followups with prep context"
```

---

### Task 3: Teach The LLM Prompt How To Treat Knowledge Guidance

**Files:**
- Modify: `app/services/llm.py`
- Modify: `tests/test_llm_service.py`

- [ ] **Step 1: Write failing prompt test**

Append to `tests/test_llm_service.py` after `test_openai_interview_llm_generates_followup_from_context()`:

```python
def test_openai_interview_llm_followup_prompt_includes_knowledge_guidance():
    chat_model = FakeFollowupChatModel()
    llm = OpenAIInterviewLLM(chat_model=chat_model)
    context = [
        {"role": "interviewer", "content": "Explain Redis cache invalidation."},
        {"role": "candidate", "content": "I delete cache after DB writes."},
        {
            "role": "knowledge_agent",
            "content": "Prep guidance for q1: focus topics Redis. Suggested follow-up angles: 追问缓存一致性。",
        },
    ]

    llm.generate_followup(context)

    assert "knowledge_agent: Prep guidance for q1" in chat_model.last_prompt
    assert "Use knowledge_agent entries as interview guidance, not as candidate answers." in chat_model.last_prompt
```

- [ ] **Step 2: Run prompt test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_service.py::test_openai_interview_llm_followup_prompt_includes_knowledge_guidance -q
```

Expected: FAIL because `_build_followup_prompt()` does not yet include the `knowledge_agent` instruction.

- [ ] **Step 3: Update follow-up prompt**

Modify `_build_followup_prompt()` in `app/services/llm.py` so the return value includes this sentence after the grounding sentence:

```python
        "Use knowledge_agent entries as interview guidance, not as candidate answers.\n"
```

The function should read:

```python
def _build_followup_prompt(context: list[dict[str, str]]) -> str:
    transcript = "\n".join(
        f"{item['role']}: {item['content']}" for item in context if item.get("content")
    )
    return (
        "You are a professional technical interviewer.\n"
        "Based on the recent interview context, ask exactly one sharp follow-up question.\n"
        "The follow-up must be grounded in the candidate's latest answer.\n"
        "Use knowledge_agent entries as interview guidance, not as candidate answers.\n"
        "Prefer tradeoffs, edge cases, fallback plans, performance bottlenecks, or source-code reasoning.\n"
        "Return only the follow-up question, without explanation.\n\n"
        f"Recent context:\n{transcript}"
    )
```

- [ ] **Step 4: Run LLM follow-up tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_service.py::test_openai_interview_llm_generates_followup_from_context tests/test_llm_service.py::test_openai_interview_llm_followup_prompt_includes_knowledge_guidance tests/test_llm_service.py::test_openai_interview_llm_streams_followup_from_context -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/llm.py tests/test_llm_service.py
git commit -m "feat: explain knowledge guidance in followup prompt"
```

---

### Task 4: Document Stage 32

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write failing docs test**

Append to `tests/test_local_v1_docs.py` after the Stage 31 docs test:

```python
def test_docs_describe_stage_32_knowledge_guided_followup():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 32 uses prep_context to guide follow-up generation"
    assert expected in readme
    assert expected in runbook
    assert "knowledge_agent" in readme
    assert "knowledge_agent" in runbook
    assert "does not add WebSocket, Redis checkpoints, or a new persistence table" in readme
```

- [ ] **Step 2: Run docs test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_32_knowledge_guided_followup -q
```

Expected: FAIL because Stage 32 docs do not exist yet.

- [ ] **Step 3: Update README architecture position**

Add this paragraph under `## Current Architecture Position`, after the Stage 31 paragraph:

```markdown
Stage 32 uses prep_context to guide follow-up generation. The interview graph now converts the current question's `prep_context.question_hints` into a `knowledge_agent` context message before calling the Examiner/LLM follow-up boundary, so generated follow-ups can target the role topics and evidence prepared during `/api/prep`. This improves continuity between preparation and live interview behavior, but it does not add WebSocket, Redis checkpoints, or a new persistence table.
```

- [ ] **Step 4: Update runbook**

Add this paragraph under `## 1.1 Architecture Position`, after the Stage 31 paragraph:

```markdown
Stage 32 uses prep_context to guide follow-up generation. Local verification should confirm the first follow-up request can include a `knowledge_agent` context entry derived from `prep_context.question_hints`, while interviews without `prep_context` continue to use the plain transcript-only follow-up path.
```

Add this check under the browser acceptance section after the Stage 31 checks:

```markdown
Stage 32 knowledge-guided follow-up checks:

1. Generate a prep plan whose `prep_context.question_hints` includes Redis or FastAPI follow-up hints.
2. Start the interview and answer the matching question with a partial answer.
3. Confirm the follow-up remains grounded in the user's answer while targeting the preheated topic.
4. Confirm a session created from a plan without `prep_context` still produces a normal fallback or LLM follow-up.
```

- [ ] **Step 5: Run docs test**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_32_knowledge_guided_followup -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: describe stage 32 knowledge-guided followup"
```

---

### Task 5: Verification Sweep

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused Stage 32 tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_context.py tests/test_interview_graph.py tests/test_llm_service.py::test_openai_interview_llm_generates_followup_from_context tests/test_llm_service.py::test_openai_interview_llm_followup_prompt_includes_knowledge_guidance tests/test_llm_service.py::test_openai_interview_llm_streams_followup_from_context tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run session smoke tests for normal and streaming follow-up**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py::test_submit_answer_uses_llm_context_to_generate_followup tests/test_session_service.py::test_prepare_answer_defers_followup_text_for_streaming -q
```

Expected: PASS.

- [ ] **Step 3: Run full regression**

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

Expected: PASS, with PostgreSQL-specific tests allowed to skip when fixture prerequisites are unavailable.

- [ ] **Step 4: Record final status**

Run:

```bash
git status --short
git log --oneline -5
```

Expected:

- The latest commits include the four Stage 32 commits.
- Any remaining dirty files are pre-existing Stage 29/30 worktree changes or explicitly identified unrelated files.

---

## Verification Sweep

After all tasks are complete, run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_context.py tests/test_interview_graph.py tests/test_llm_service.py::test_openai_interview_llm_generates_followup_from_context tests/test_llm_service.py::test_openai_interview_llm_followup_prompt_includes_knowledge_guidance tests/test_llm_service.py::test_openai_interview_llm_streams_followup_from_context tests/test_local_v1_docs.py -q
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py::test_submit_answer_uses_llm_context_to_generate_followup tests/test_session_service.py::test_prepare_answer_defers_followup_text_for_streaming -q
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected:

- Prep context helper tests pass.
- Interview graph sends `knowledge_agent` context for plans with `prep_context`.
- Interview graph preserves transcript-only context for plans without `prep_context`.
- LLM follow-up prompt includes the knowledge guidance instruction.
- Full pytest remains green.
- Static JS syntax remains valid.

## Self-Review

- Spec coverage: The plan connects Stage 31 `prep_context` to follow-up generation, covers normal and streaming follow-up paths, keeps fallback behavior intact, updates prompt semantics, and documents local verification.
- Placeholder scan: No TBD/TODO/fill-in-later placeholders remain; each task includes exact files, code, commands, and expected results.
- Type consistency: `InterviewPlan`, `PrepContext`, `PrepQuestionHint`, `PrepKnowledgeTopic`, `knowledge_agent`, `build_question_prep_context_messages()`, and `get_question_prep_hint()` are used consistently across helper, graph, LLM prompt, tests, and docs.
