# Stage 23 Agent Boundaries And Question Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align Local V1 with the architecture document by introducing explicit agent boundaries and persisted question-level evaluation records without adding Redis, Celery, WebSocket, or LangGraph yet.

**Architecture:** Keep the current FastAPI, Postgres, pgvector, SSE, and report worker runtime stable. Add a thin `app/agents/` layer that names the architecture roles and delegates to existing tested services, then persist one question evaluation per interview question when the report worker produces the final report. This gives the project a traceable evidence chain while preserving the current Local V1 infrastructure.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, PostgreSQL, pgvector, pytest, vanilla ES modules.

---

## Scope

This stage does:

- Close the current browser acceptance evidence gap before feature work.
- Introduce explicit `ExaminerAgent`, `KnowledgeAgent`, `ShadowReviewerAgent`, and `ReportCoachAgent` boundaries.
- Keep `InterviewGraphRunner` as the current lightweight graph runner.
- Save report feedbacks as per-question evaluations in memory and PostgreSQL stores.
- Add an API endpoint for question-evaluation trace inspection.
- Update docs to explain why Stage 23 intentionally delays Redis, Celery, WebSocket, and LangGraph.

This stage does not:

- Add user login, account isolation, or deployment hardening.
- Replace the current Postgres report queue with Celery.
- Replace SSE with WebSocket.
- Replace `InterviewGraphRunner` with LangGraph.
- Redesign the four-page UI.

## File Structure

- Create: `app/agents/__init__.py`
  - Exports the new agent boundary classes.
- Create: `app/agents/examiner.py`
  - Owns follow-up generation and fallback behavior for the real-time interview path.
- Create: `app/agents/knowledge.py`
  - Owns prep-plan generation from JD and resume through the existing LLM interface.
- Create: `app/agents/shadow_reviewer.py`
  - Owns report-time expert evaluation through the existing `ExpertShadowEvaluator`.
- Create: `app/agents/report_coach.py`
  - Owns final report generation through the existing LLM report method.
- Create: `app/services/question_evaluations.py`
  - Defines persisted question-evaluation models and conversion helpers.
- Modify: `app/services/vector_store.py`
  - Adds a `KnowledgeSearchStore` protocol for evaluator and agent dependencies.
- Modify: `app/services/evaluator_ext.py`
  - Uses `KnowledgeSearchStore` and delegates final report generation to `ReportCoachAgent`.
- Modify: `app/graphs/interview_graph.py`
  - Use `ExaminerAgent` instead of calling the LLM directly.
- Modify: `app/services/prep.py`
  - Use `KnowledgeAgent` while preserving the public `prepare_interview()` function.
- Modify: `app/services/report_tasks.py`
  - Persist question evaluations after successful report generation.
- Modify: `app/services/session.py`
  - Add in-memory question-evaluation save/list methods.
- Modify: `app/services/postgres_session.py`
  - Add `interview_question_evaluations` table and Postgres save/list methods.
- Modify: `app/api/routes.py`
  - Add `GET /api/interviews/{session_id}/question-evaluations`.
- Modify: `docs/local-v1-runbook.md`
  - Add Stage 23 architecture note.
- Modify: `README.md`
  - Add current architecture status note.
- Test: `tests/test_agents.py`
- Test: `tests/test_question_evaluations.py`
- Test: `tests/test_report_tasks.py`
- Test: `tests/test_postgres_session_store.py`
- Test: `tests/test_report_api.py`
- Test: `tests/test_local_v1_docs.py`

---

### Task 1: Close Browser Acceptance Evidence Gate

**Files:**
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Run the focused automated browser-readiness checks**

Run each command separately in PowerShell:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_local_v1_docs.py -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

Expected:

- Pytest exits with all selected tests passing.
- Every `node --check` command exits with code `0`.
- CSS build exits with code `0`. The Browserslist warning is acceptable.

- [ ] **Step 2: Run the manual browser checklist**

Follow the checklist already written in `docs/stage-21-browser-e2e-acceptance.md` using the local server at:

```text
http://127.0.0.1:8000/prep
```

Use the JD and resume from `docs/superpowers/plans/2026-07-06-stage-22-browser-e2e-defect-closure.md`.

Expected:

- Each executed manual row gets `Pass` or `Fail`.
- No executed row remains `Pending`.
- Any `Fail` row has a concrete note describing the observed behavior.

- [ ] **Step 3: Record automated verification results**

In `docs/stage-21-browser-e2e-acceptance.md`, replace the automated verification `Pending` entries for commands executed in Step 1 with `Pass`.

Use this exact result text for the full suite if it still matches the local run:

```markdown
| `F:\python3.11\python.exe -m pytest -q` | Pass: 234 passed, 20 skipped |
```

- [ ] **Step 4: Set the final acceptance status**

If every executed manual row passed, replace:

```markdown
Pending manual browser execution.
```

with:

```markdown
Accepted for local four-page browser E2E. No blocking browser defects remain.
```

If any manual row failed, replace it with:

```markdown
Browser E2E found defects. See checklist notes for failing rows.
```

- [ ] **Step 5: Commit the acceptance record**

Run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md
git diff --cached --name-status
git commit -m "docs: record local browser acceptance"
```

Expected staged file:

```text
M	docs/stage-21-browser-e2e-acceptance.md
```

---

### Task 2: Add Agent Boundary Classes

**Files:**
- Create: `app/agents/__init__.py`
- Create: `app/agents/examiner.py`
- Create: `app/agents/knowledge.py`
- Create: `app/agents/report_coach.py`
- Create: `app/agents/shadow_reviewer.py`
- Modify: `app/services/vector_store.py`
- Modify: `app/services/evaluator_ext.py`
- Test: `tests/test_agents.py`

- [ ] **Step 1: Write failing agent boundary tests**

Create `tests/test_agents.py`:

```python
from app.agents.examiner import ExaminerAgent
from app.agents.knowledge import KnowledgeAgent
from app.agents.report_coach import ReportCoachAgent
from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport


class FollowupLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("not used")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Why did you choose delete-after-write instead of write-through?"

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Why did you choose delete-after-write instead of write-through?"

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str):
        raise AssertionError("not used")


class PlanLLM(FollowupLLM):
    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="Backend plan",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain Redis cache invalidation.",
                    focus="Redis consistency",
                )
            ],
        )


class ReportLLM(FollowupLLM):
    def generate_report(self, plan, evaluation_items: list[dict], session_id: str):
        return InterviewReport(
            session_id=session_id,
            overall_score=82,
            overall_dimension_scores=DimensionScores(
                breadth=80,
                depth=82,
                architecture=81,
                engineering=84,
                communication=83,
            ),
            summary="Solid answer with one consistency gap.",
            highlights=["Explained cache-aside"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Explain Redis cache invalidation.",
                    user_answer="Delete cache after database update.",
                    score=82,
                    dimension_scores=DimensionScores(
                        breadth=80,
                        depth=82,
                        architecture=81,
                        engineering=84,
                        communication=83,
                    ),
                    rationale="The answer covered delete-after-write.",
                    critique="It missed race condition handling.",
                    better_answer="Mention delayed double delete and retry.",
                    references=[],
                )
            ],
        )


class VectorStore:
    def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
        return []


def test_examiner_agent_generates_followup_from_context():
    agent = ExaminerAgent(llm=FollowupLLM())

    follow_up = agent.generate_followup(
        context=[{"role": "candidate", "content": "I delete cache after DB writes."}],
        focus="Redis consistency",
    )

    assert follow_up == "Why did you choose delete-after-write instead of write-through?"


def test_examiner_agent_falls_back_when_llm_fails():
    class FailingLLM(FollowupLLM):
        def generate_followup(self, context: list[dict[str, str]]) -> str:
            raise RuntimeError("provider down")

    agent = ExaminerAgent(llm=FailingLLM())

    assert agent.generate_followup(context=[], focus="Redis consistency") == (
        "请继续深挖 Redis consistency：你当时做了什么取舍，为什么这样选？"
    )


def test_knowledge_agent_generates_plan():
    plan = KnowledgeAgent(llm=PlanLLM()).generate_plan(
        job_description="Backend Redis role",
        resume_text="Built Redis cache",
    )

    assert plan.title == "Backend plan"
    assert plan.questions[0].focus == "Redis consistency"


def test_report_coach_agent_generates_report():
    plan = PlanLLM().generate_plan("jd", "resume")
    report = ReportCoachAgent(llm=ReportLLM()).generate_report(
        plan=plan,
        evaluation_items=[],
        session_id="s1",
    )

    assert report.session_id == "s1"
    assert report.feedbacks[0].question_id == "q1"


def test_shadow_reviewer_agent_wraps_expert_evaluator():
    agent = ShadowReviewerAgent(llm=ReportLLM(), vector_store=VectorStore())

    assert agent.llm is not None
    assert agent.vector_store is not None
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_agents.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.agents'`.

- [ ] **Step 3: Create `app/agents/examiner.py`**

Add:

```python
from collections.abc import Iterator

from app.services.llm import InterviewLLM


def fallback_followup(focus: str) -> str:
    return f"请继续深挖 {focus}：你当时做了什么取舍，为什么这样选？"


class ExaminerAgent:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self.llm = llm

    def generate_followup(
        self,
        *,
        context: list[dict[str, str]],
        focus: str,
    ) -> str:
        try:
            llm = self.llm or self._default_llm()
            return llm.generate_followup(context)
        except Exception:
            return fallback_followup(focus)

    def stream_followup(
        self,
        *,
        context: list[dict[str, str]],
        focus: str,
    ) -> Iterator[str]:
        try:
            llm = self.llm or self._default_llm()
            emitted = False
            for chunk in llm.stream_followup(context):
                if not chunk:
                    continue
                emitted = True
                yield chunk
            if not emitted:
                yield fallback_followup(focus)
        except Exception:
            yield fallback_followup(focus)

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
```

- [ ] **Step 4: Create `app/agents/knowledge.py`**

Add:

```python
from app.services.llm import InterviewLLM
from app.services.prep import InterviewPlan


class KnowledgeAgent:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self.llm = llm

    def generate_plan(self, *, job_description: str, resume_text: str) -> InterviewPlan:
        llm = self.llm or self._default_llm()
        return llm.generate_plan(job_description, resume_text)

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
```

- [ ] **Step 5: Create `app/agents/report_coach.py`**

Add:

```python
from app.services.llm import InterviewLLM
from app.services.report import InterviewReport


class ReportCoachAgent:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self.llm = llm

    def generate_report(
        self,
        *,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        llm = self.llm or self._default_llm()
        return llm.generate_report(
            plan=plan,
            evaluation_items=evaluation_items,
            session_id=session_id,
        )

    @staticmethod
    def _default_llm() -> InterviewLLM:
        from app.services.llm import OpenAIInterviewLLM

        return OpenAIInterviewLLM()
```

- [ ] **Step 6: Add the knowledge search protocol**

In `app/services/vector_store.py`, update imports:

```python
from typing import Any, Protocol
```

Add this protocol above `class KnowledgeChunk`:

```python
class KnowledgeSearchStore(Protocol):
    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list["KnowledgeChunk" | dict]:
        """Search role-relevant knowledge chunks for evaluation."""
```

In `app/services/evaluator_ext.py`, replace:

```python
from app.services.vector_store import KnowledgeChunk, PgVectorKnowledgeStore
```

with:

```python
from app.services.vector_store import KnowledgeChunk, KnowledgeSearchStore
```

Then change `ExpertShadowEvaluator.__init__()` to accept:

```python
        vector_store: KnowledgeSearchStore,
```

- [ ] **Step 7: Create `app/agents/shadow_reviewer.py`**

Add:

```python
from collections.abc import Callable

from app.graphs.interview_state import InterviewState
from app.services.evaluator_ext import ExpertShadowEvaluator
from app.services.llm import InterviewLLM
from app.services.report import InterviewReport, ReportProgress
from app.services.vector_store import KnowledgeSearchStore


class ShadowReviewerAgent:
    def __init__(
        self,
        *,
        llm: InterviewLLM,
        vector_store: KnowledgeSearchStore,
    ) -> None:
        self.llm = llm
        self.vector_store = vector_store
        self._evaluator = ExpertShadowEvaluator(
            llm=llm,
            vector_store=vector_store,
        )

    def evaluate(
        self,
        state: InterviewState,
        on_progress: Callable[[ReportProgress], None] | None = None,
    ) -> InterviewReport:
        return self._evaluator.evaluate(state, on_progress=on_progress)
```

- [ ] **Step 8: Create `app/agents/__init__.py`**

Add:

```python
from app.agents.examiner import ExaminerAgent
from app.agents.knowledge import KnowledgeAgent
from app.agents.report_coach import ReportCoachAgent
from app.agents.shadow_reviewer import ShadowReviewerAgent

__all__ = [
    "ExaminerAgent",
    "KnowledgeAgent",
    "ReportCoachAgent",
    "ShadowReviewerAgent",
]
```

- [ ] **Step 9: Run agent tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_agents.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit agent boundaries**

Run:

```powershell
git add app/agents app/services/vector_store.py app/services/evaluator_ext.py tests/test_agents.py
git diff --cached --name-status
git commit -m "refactor: add explicit agent boundaries"
```

Expected staged files:

```text
A	app/agents/__init__.py
A	app/agents/examiner.py
A	app/agents/knowledge.py
A	app/agents/report_coach.py
A	app/agents/shadow_reviewer.py
M	app/services/vector_store.py
M	app/services/evaluator_ext.py
A	tests/test_agents.py
```

---

### Task 3: Route Existing Prep And Interview Logic Through Agents

**Files:**
- Modify: `app/graphs/interview_graph.py`
- Modify: `app/services/session.py`
- Modify: `app/services/prep.py`
- Test: `tests/test_interview_graph.py`
- Test: `tests/test_prep_service.py`

- [ ] **Step 1: Add an interview graph assertion for the Examiner boundary**

Append to `tests/test_interview_graph.py`:

```python
def test_runner_accepts_examiner_agent_boundary():
    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            assert focus == "project"
            return "Agent-generated follow-up."

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(**make_start_kwargs())

    new_state = runner.submit_answer(state, "I improved cache consistency.")

    assert new_state["pending_output"] == "Agent-generated follow-up."


def test_runner_streams_followup_through_same_examiner_boundary():
    class Agent:
        def generate_followup(self, *, context: list[dict[str, str]], focus: str) -> str:
            raise AssertionError("streaming path should call stream_followup")

        def stream_followup(self, *, context: list[dict[str, str]], focus: str):
            assert focus == "project"
            yield "streamed "
            yield "follow-up"

    runner = InterviewGraphRunner(examiner=Agent())
    state = runner.start(**make_start_kwargs())
    prepared = runner.prepare_answer(state, "I improved cache consistency.")

    assert list(runner.stream_followup(prepared)) == ["streamed ", "follow-up"]
```

- [ ] **Step 2: Run the focused graph test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_interview_graph.py::test_runner_accepts_examiner_agent_boundary tests/test_interview_graph.py::test_runner_streams_followup_through_same_examiner_boundary -q
```

Expected: FAIL with `TypeError` because `InterviewGraphRunner` does not accept `examiner`.

- [ ] **Step 3: Update `InterviewGraphRunner` constructor**

In `app/graphs/interview_graph.py`, import:

```python
from app.agents.examiner import ExaminerAgent, fallback_followup as examiner_fallback_followup
```

Replace the constructor with:

```python
class InterviewGraphRunner:
    def __init__(self, llm: InterviewLLM | None = None, examiner=None) -> None:
        self._llm = llm
        self._examiner = examiner or ExaminerAgent(llm=llm)
```

- [ ] **Step 4: Update the graph follow-up call**

Replace the existing local `fallback_followup()` function with:

```python
def fallback_followup(focus: str) -> str:
    return examiner_fallback_followup(focus)
```

In `brain_node()`, add an optional `examiner` parameter:

```python
def brain_node(
    state: InterviewState,
    llm: InterviewLLM | None,
    *,
    examiner=None,
    generate_followup_text: bool = True,
) -> InterviewState:
```

Replace the existing LLM follow-up block with:

```python
    follow_up = None
    if generate_followup_text:
        resolved_examiner = examiner or ExaminerAgent(llm=llm)
        follow_up = resolved_examiner.generate_followup(
            context=_build_followup_context(state),
            focus=question.focus,
        )
```

Update `submit_answer()` to pass `self._examiner`:

```python
        next_state = brain_node(next_state, self._llm, examiner=self._examiner)
```

Update `prepare_answer()` to pass the same examiner even though it does not generate text in the current implementation:

```python
        return brain_node(
            next_state,
            self._llm,
            examiner=self._examiner,
            generate_followup_text=False,
        )
```

Add this method to `InterviewGraphRunner`:

```python
    def stream_followup(self, state: InterviewState):
        question = get_current_question(state)
        focus = question.focus if question is not None else "current question"
        yield from self._examiner.stream_followup(
            context=_build_followup_context(state),
            focus=focus,
        )
```

- [ ] **Step 5: Keep streaming aligned with the Examiner boundary**

In `app/services/session.py`, replace the body of `stream_followup()` after `fallback_text` is computed with:

```python
        emitted = False
        for chunk in self._runner.stream_followup(state):
            emitted = True
            yield chunk
        if not emitted and fallback_text:
            yield fallback_text
```

- [ ] **Step 6: Route prep through `KnowledgeAgent`**

In `app/services/prep.py`, replace:

```python
        llm = llm or _build_default_llm()
        return llm.generate_plan(job_description, resume_text)
```

with:

```python
        from app.agents.knowledge import KnowledgeAgent

        return KnowledgeAgent(llm=llm).generate_plan(
            job_description=job_description,
            resume_text=resume_text,
        )
```

Leave `_build_default_llm()` in place only if tests still import it. If no tests import it, remove `_build_default_llm()` and its dead code after the focused tests pass.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_interview_graph.py tests/test_session_service.py tests/test_prep_service.py tests/test_agents.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit prep and interview agent routing**

Run:

```powershell
git add app/graphs/interview_graph.py app/services/session.py app/services/prep.py tests/test_interview_graph.py
git diff --cached --name-status
git commit -m "refactor: route prep and interview through agents"
```

Expected staged files include:

```text
M	app/graphs/interview_graph.py
M	app/services/session.py
M	app/services/prep.py
M	tests/test_interview_graph.py
```

---

### Task 4: Add Question Evaluation Models And In-Memory Store

**Files:**
- Create: `app/services/question_evaluations.py`
- Modify: `app/services/session.py`
- Test: `tests/test_question_evaluations.py`

- [ ] **Step 1: Write failing question-evaluation model and store tests**

Create `tests/test_question_evaluations.py`:

```python
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewFeedback
from app.services.question_evaluations import (
    QuestionEvaluationRecord,
    question_evaluation_from_feedback,
)
from app.services.session import InterviewSessionStore


def make_feedback() -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Explain Redis cache invalidation.",
        user_answer="Delete cache after database update.",
        score=82,
        dimension_scores=DimensionScores(
            breadth=80,
            depth=82,
            architecture=81,
            engineering=84,
            communication=83,
        ),
        rationale="The answer covered delete-after-write.",
        critique="It missed race condition handling.",
        better_answer="Mention delayed double delete and retry.",
        references=[],
    )


def make_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Backend plan",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="Redis consistency",
            )
        ],
    )


def test_question_evaluation_can_be_created_from_feedback():
    record = question_evaluation_from_feedback(
        session_id="s1",
        feedback=make_feedback(),
    )

    assert record.session_id == "s1"
    assert record.question_id == "q1"
    assert record.status == "completed"
    assert record.answer_state == "answered"
    assert record.feedback.score == 82


def test_in_memory_session_store_saves_question_evaluations():
    store = InterviewSessionStore()
    turn = store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    record = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(),
    )

    store.save_question_evaluations(turn.session_id, [record])

    saved = store.list_question_evaluations(turn.session_id)
    assert len(saved) == 1
    assert saved[0].question_id == "q1"


def test_question_evaluation_record_requires_error_for_failed_status():
    try:
        QuestionEvaluationRecord(
            session_id="s1",
            question_id="q1",
            status="failed",
        )
    except ValueError as exc:
        assert "failed question evaluations require error" in str(exc)
    else:
        raise AssertionError("expected validation failure")
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_question_evaluations.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `app.services.question_evaluations`.

- [ ] **Step 3: Create `app/services/question_evaluations.py`**

Add:

```python
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.report import InterviewFeedback, utc_now_iso


class QuestionEvaluationRecord(BaseModel):
    session_id: str
    question_id: str
    answer_state: Literal["answered", "skipped", "unanswered"] = "answered"
    status: Literal["completed", "failed"]
    feedback: InterviewFeedback | None = None
    error: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def validate_state(self) -> "QuestionEvaluationRecord":
        if self.status == "completed" and self.feedback is None:
            raise ValueError("completed question evaluations require feedback")
        if self.status == "failed" and not self.error:
            raise ValueError("failed question evaluations require error")
        return self


def question_evaluation_from_feedback(
    *,
    session_id: str,
    feedback: InterviewFeedback,
) -> QuestionEvaluationRecord:
    return QuestionEvaluationRecord(
        session_id=session_id,
        question_id=feedback.question_id,
        answer_state=feedback.answer_state,
        status="completed",
        feedback=feedback,
    )
```

- [ ] **Step 4: Add in-memory store methods**

In `app/services/session.py`, import:

```python
from app.services.question_evaluations import QuestionEvaluationRecord
```

In `InterviewSessionStore.__init__()`, add:

```python
        self._question_evaluations: Dict[str, list[QuestionEvaluationRecord]] = {}
```

Add these methods to `InterviewSessionStore`:

```python
    def save_question_evaluations(
        self,
        session_id: str,
        records: list[QuestionEvaluationRecord],
    ) -> None:
        self.get(session_id)
        self._question_evaluations[session_id] = list(records)

    def list_question_evaluations(self, session_id: str) -> list[QuestionEvaluationRecord]:
        self.get(session_id)
        return list(self._question_evaluations.get(session_id, []))
```

- [ ] **Step 5: Run question-evaluation tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_question_evaluations.py tests/test_session_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit question-evaluation models and memory store**

Run:

```powershell
git add app/services/question_evaluations.py app/services/session.py tests/test_question_evaluations.py
git diff --cached --name-status
git commit -m "feat: add question evaluation records"
```

Expected staged files:

```text
A	app/services/question_evaluations.py
M	app/services/session.py
A	tests/test_question_evaluations.py
```

---

### Task 5: Persist Question Evaluations In PostgreSQL

**Files:**
- Modify: `app/services/postgres_session.py`
- Modify: `app/services/session_serialization.py`
- Test: `tests/test_postgres_session_store.py`

- [ ] **Step 1: Add a failing Postgres persistence test**

Append to `tests/test_postgres_session_store.py`:

```python
def test_postgres_store_persists_question_evaluations(postgres_store):
    from app.services.report import DimensionScores, InterviewFeedback
    from app.services.question_evaluations import question_evaluation_from_feedback

    turn = postgres_store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    feedback = InterviewFeedback(
        question_id="q1",
        question_text="Introduce the project.",
        user_answer="Built an API.",
        score=78,
        dimension_scores=DimensionScores(
            breadth=77,
            depth=78,
            architecture=76,
            engineering=80,
            communication=79,
        ),
        rationale="The answer gave implementation context.",
        critique="Business impact was thin.",
        better_answer="Tie the API work to latency and reliability outcomes.",
        references=[],
    )

    postgres_store.save_question_evaluations(
        turn.session_id,
        [question_evaluation_from_feedback(session_id=turn.session_id, feedback=feedback)],
    )

    saved = postgres_store.list_question_evaluations(turn.session_id)
    assert len(saved) == 1
    assert saved[0].question_id == "q1"
    assert saved[0].answer_state == "answered"
    assert saved[0].feedback.score == 78
```

- [ ] **Step 2: Run the focused Postgres test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_postgres_session_store.py::test_postgres_store_persists_question_evaluations -q
```

Expected: FAIL because `PostgresInterviewSessionStore` does not persist question evaluations.

- [ ] **Step 3: Add question-evaluation serialization helpers**

In `app/services/session_serialization.py`, import:

```python
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewFeedback
```

Add:

```python
def question_evaluation_record_to_row(record: QuestionEvaluationRecord) -> dict:
    return {
        "session_id": record.session_id,
        "question_id": record.question_id,
        "answer_state": record.answer_state,
        "status": record.status,
        "feedback_json": record.feedback.model_dump() if record.feedback is not None else None,
        "error": record.error,
        "created_at": record.created_at,
    }


def question_evaluation_record_from_row(row: dict) -> QuestionEvaluationRecord:
    return QuestionEvaluationRecord(
        session_id=row["session_id"],
        question_id=row["question_id"],
        answer_state=row["answer_state"],
        status=row["status"],
        feedback=InterviewFeedback.model_validate(row["feedback_json"])
        if row["feedback_json"] is not None
        else None,
        error=row["error"],
        created_at=row["created_at"],
    )
```

- [ ] **Step 4: Add the Postgres table**

In `PostgresInterviewSessionStore.__init__()`, add:

```python
        self.question_evaluations_table = f"{table_prefix}_question_evaluations"
```

In `_ensure_schema()`, after creating the reports table, add:

```python
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {question_evaluations} (
                            session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                            question_id TEXT NOT NULL,
                            answer_state TEXT NOT NULL CHECK (answer_state IN ('answered', 'skipped', 'unanswered')),
                            status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
                            feedback_json JSONB,
                            error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (session_id, question_id)
                        )
                        """
                    ).format(
                        question_evaluations=sql.Identifier(self.question_evaluations_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
```

- [ ] **Step 5: Add Postgres save/list methods**

In `app/services/postgres_session.py`, import:

```python
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.session_serialization import (
    question_evaluation_record_from_row,
    question_evaluation_record_to_row,
)
```

Merge the new imports into the existing `session_serialization` import block.

Add methods to `PostgresInterviewSessionStore`:

```python
    def save_question_evaluations(
        self,
        session_id: str,
        records: list[QuestionEvaluationRecord],
    ) -> None:
        self.get(session_id)
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DELETE FROM {question_evaluations} WHERE session_id = %s"
                    ).format(
                        question_evaluations=sql.Identifier(self.question_evaluations_table)
                    ),
                    (session_id,),
                )
                for record in records:
                    row = question_evaluation_record_to_row(record)
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {question_evaluations} (
                                session_id, question_id, answer_state, status, feedback_json, error, created_at
                            )
                            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                            ON CONFLICT (session_id, question_id) DO UPDATE
                            SET status = EXCLUDED.status,
                                answer_state = EXCLUDED.answer_state,
                                feedback_json = EXCLUDED.feedback_json,
                                error = EXCLUDED.error,
                                updated_at = NOW()
                            """
                        ).format(
                            question_evaluations=sql.Identifier(
                                self.question_evaluations_table
                            )
                        ),
                        (
                            row["session_id"],
                            row["question_id"],
                            row["answer_state"],
                            row["status"],
                            json.dumps(row["feedback_json"], ensure_ascii=False)
                            if row["feedback_json"] is not None
                            else None,
                            row["error"],
                            row["created_at"],
                        ),
                    )

    def list_question_evaluations(self, session_id: str) -> list[QuestionEvaluationRecord]:
        self.get(session_id)
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT session_id, question_id, answer_state, status, feedback_json, error, created_at
                        FROM {question_evaluations}
                        WHERE session_id = %s
                        ORDER BY question_id
                        """
                    ).format(
                        question_evaluations=sql.Identifier(self.question_evaluations_table)
                    ),
                    (session_id,),
                )
                rows = cursor.fetchall()
        return [
            question_evaluation_record_from_row(
                {
                    "session_id": row[0],
                    "question_id": row[1],
                    "answer_state": row[2],
                    "status": row[3],
                    "feedback_json": row[4],
                    "error": row[5],
                    "created_at": self._iso_timestamp(row[6]),
                }
            )
            for row in rows
        ]
```

- [ ] **Step 6: Run focused Postgres tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_postgres_session_store.py::test_postgres_store_persists_question_evaluations tests/test_question_evaluations.py -q
```

Expected: PASS, or SKIP if the local Postgres integration fixture is intentionally unavailable.

- [ ] **Step 7: Commit Postgres question evaluations**

Run:

```powershell
git add app/services/postgres_session.py app/services/session_serialization.py tests/test_postgres_session_store.py
git diff --cached --name-status
git commit -m "feat: persist question evaluations in postgres"
```

Expected staged files:

```text
M	app/services/postgres_session.py
M	app/services/session_serialization.py
M	tests/test_postgres_session_store.py
```

---

### Task 6: Save Question Evaluations During Report Generation

**Files:**
- Modify: `app/services/report_tasks.py`
- Modify: `app/services/evaluator_ext.py`
- Test: `tests/test_report_tasks.py`
- Test: `tests/test_expert_evaluator.py`

- [ ] **Step 1: Add a failing report-task persistence test**

Append to `tests/test_report_tasks.py`:

```python
def test_execute_report_generation_saves_question_evaluations():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    from app.services.report_tasks import execute_report_generation

    store = InterviewSessionStore()
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = execute_report_generation(
        session_id=session.session_id,
        store=store,
        llm=ReportLLM(),
        vector_store=FakeVectorStore(),
    )

    saved = store.list_question_evaluations(session.session_id)
    assert report.feedbacks[0].question_id == saved[0].question_id
    assert saved[0].status == "completed"
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_tasks.py::test_execute_report_generation_saves_question_evaluations -q
```

Expected: FAIL because report generation does not save question evaluations yet.

- [ ] **Step 3: Route report generation through `ShadowReviewerAgent`**

In `app/services/report_tasks.py`, replace:

```python
from app.services.evaluator_ext import ExpertShadowEvaluator
```

with:

```python
from app.agents.shadow_reviewer import ShadowReviewerAgent
```

Replace evaluator creation with:

```python
    evaluator = ShadowReviewerAgent(
        llm=llm,
        vector_store=vector_store,
    )
```

- [ ] **Step 4: Save question evaluations after report completion**

In `app/services/report_tasks.py`, import:

```python
from app.services.question_evaluations import question_evaluation_from_feedback
```

After:

```python
    store.save_report(session_id, report)
```

add:

```python
    store.save_question_evaluations(
        session_id,
        [
            question_evaluation_from_feedback(
                session_id=session_id,
                feedback=feedback,
            )
            for feedback in report.feedbacks
        ],
    )
```

- [ ] **Step 5: Route final report assembly through `ReportCoachAgent`**

In `app/services/evaluator_ext.py`, import:

```python
from app.agents.report_coach import ReportCoachAgent
```

Replace:

```python
            report = self._llm.generate_report(
                plan=state["plan"],
                evaluation_items=evaluation_items,
                session_id=state["session_id"],
            )
```

with:

```python
            report = ReportCoachAgent(llm=self._llm).generate_report(
                plan=state["plan"],
                evaluation_items=evaluation_items,
                session_id=state["session_id"],
            )
```

- [ ] **Step 6: Run focused report tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_tasks.py tests/test_expert_evaluator.py tests/test_question_evaluations.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit report-time Question Evaluation persistence**

Run:

```powershell
git add app/services/report_tasks.py app/services/evaluator_ext.py tests/test_report_tasks.py
git diff --cached --name-status
git commit -m "feat: save question evaluations during report generation"
```

Expected staged files:

```text
M	app/services/report_tasks.py
M	app/services/evaluator_ext.py
M	tests/test_report_tasks.py
```

---

### Task 7: Expose Question Evaluations Through The API

**Files:**
- Modify: `app/api/routes.py`
- Test: `tests/test_report_api.py`

- [ ] **Step 1: Add a failing API test**

Append to `tests/test_report_api.py`:

```python
def test_get_question_evaluations_returns_saved_records(client):
    from app.services.report import DimensionScores, InterviewFeedback
    from app.services.question_evaluations import question_evaluation_from_feedback
    from app.services.runtime import get_session_store

    response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role",
            "resume_text": "Built APIs",
        },
    )
    session_id = response.json()["session_id"]
    feedback = InterviewFeedback(
        question_id="q1",
        question_text="Explain Redis.",
        user_answer="Cache-aside.",
        score=80,
        dimension_scores=DimensionScores(
            breadth=80,
            depth=80,
            architecture=80,
            engineering=80,
            communication=80,
        ),
        rationale="Covered the basic pattern.",
        critique="Needs more failure handling.",
        better_answer="Add consistency and retry details.",
        references=[],
    )
    get_session_store().save_question_evaluations(
        session_id,
        [question_evaluation_from_feedback(session_id=session_id, feedback=feedback)],
    )

    result = client.get(f"/api/interviews/{session_id}/question-evaluations")

    assert result.status_code == 200
    assert result.json()["items"][0]["question_id"] == "q1"
    assert result.json()["items"][0]["feedback"]["score"] == 80
```

- [ ] **Step 2: Run the focused API test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_api.py::test_get_question_evaluations_returns_saved_records -q
```

Expected: FAIL with `404 Not Found`.

- [ ] **Step 3: Add the API route**

In `app/api/routes.py`, add:

```python
@router.get("/interviews/{session_id}/question-evaluations")
def get_interview_question_evaluations(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        records = store.list_question_evaluations(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    return {
        "session_id": session_id,
        "items": [record.model_dump() for record in records],
        "total": len(records),
    }
```

Place it after `get_interview_report_progress()` so report-related endpoints stay grouped.

- [ ] **Step 4: Run focused API tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_api.py tests/test_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit question-evaluation API**

Run:

```powershell
git add app/api/routes.py tests/test_report_api.py
git diff --cached --name-status
git commit -m "feat: expose question evaluation trace API"
```

Expected staged files:

```text
M	app/api/routes.py
M	tests/test_report_api.py
```

---

### Task 8: Document Stage 23 Architecture Position

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Test: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Add a failing documentation regression test**

Append to `tests/test_local_v1_docs.py`:

```python
def test_docs_describe_stage_23_architecture_position():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 23 keeps Postgres report jobs as the Local V1 async boundary"
    assert expected in readme
    assert expected in runbook
    assert "Redis, Celery, WebSocket, and LangGraph remain future architecture upgrades" in readme
```

- [ ] **Step 2: Run the focused docs test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_23_architecture_position -q
```

Expected: FAIL because the docs do not yet describe the Stage 23 architecture position.

- [ ] **Step 3: Update `README.md`**

Add this section after `## What Works`:

```markdown
## Current Architecture Position

Stage 23 keeps Postgres report jobs as the Local V1 async boundary while adding explicit agent boundaries and per-question evaluation records. Redis, Celery, WebSocket, and LangGraph remain future architecture upgrades rather than Local V1 runtime dependencies.
```

- [ ] **Step 4: Update `docs/local-v1-runbook.md`**

Add this section after `## 1. Environment`:

```markdown
## 1.1 Architecture Position

Stage 23 keeps Postgres report jobs as the Local V1 async boundary while adding explicit agent boundaries and per-question evaluation records. This runbook continues to verify the local single-user runtime, not the future Redis/Celery/WebSocket/LangGraph deployment shape.
```

- [ ] **Step 5: Run documentation tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit docs**

Run:

```powershell
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git diff --cached --name-status
git commit -m "docs: document stage 23 architecture position"
```

Expected staged files:

```text
M	README.md
M	docs/local-v1-runbook.md
M	tests/test_local_v1_docs.py
```

---

### Task 9: Final Verification And Worktree Audit

**Files:**
- Verify only.

- [ ] **Step 1: Run focused backend verification**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_agents.py tests/test_question_evaluations.py tests/test_interview_graph.py tests/test_session_service.py tests/test_report_tasks.py tests/test_report_api.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS. The current baseline is `234 passed, 20 skipped`; the exact passed count should increase after new tests are added.

- [ ] **Step 3: Run JavaScript syntax checks**

Run each command separately:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: all commands exit with code `0`.

- [ ] **Step 4: Rebuild CSS**

Run:

```powershell
npm run build:prototype-css
```

Expected: PASS. Browserslist warning is acceptable.

- [ ] **Step 5: Confirm generated CSS did not drift**

Run:

```powershell
git diff -- app/static/prototype.css
```

Expected: no output. If output appears, inspect it and commit only if it is a deterministic Tailwind rebuild.

- [ ] **Step 6: Audit worktree**

Run:

```powershell
git status --short
git log --oneline -10
```

Expected:

- Recent commits include the Stage 23 agent boundary, question-evaluation, API, and docs commits.
- Remaining untracked files are unrelated local files such as `.idea/`, `.claude/`, historical plans/specs, or files explicitly excluded from this stage.

---

## Self-Review

- Spec coverage: The plan covers the accepted next-stage direction: browser acceptance gate, explicit agent boundaries, question-level evidence records, API trace access, and docs explaining why Redis/Celery/WebSocket/LangGraph are deferred.
- Scope control: The plan avoids login, Docker, voice, UI redesign, and infrastructure swaps.
- Type consistency: `QuestionEvaluationRecord`, `question_evaluation_from_feedback()`, `save_question_evaluations()`, and `list_question_evaluations()` are introduced before later tasks consume them.
- Test strategy: Each code task starts with a failing focused test, then verifies with focused tests before commit and a full suite at the end.

