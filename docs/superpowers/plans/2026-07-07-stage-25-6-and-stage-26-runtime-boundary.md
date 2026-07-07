# Stage 25.6 And Stage 26 Runtime Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the lightweight Stage 25.6 browser-fix acceptance record as a pre-flight gate, then introduce Stage 26 runtime ports and service boundaries without replacing the current PostgreSQL worker runtime.

**Architecture:** Stage 25.6 is intentionally included as Task 1 because it is documentation-only closure for defects already fixed during manual browser use, and Stage 26 should not start while the RC record still implies those defects are open. The commits remain separate: Task 1 commits Stage 25.6 evidence, while Tasks 2-7 commit Stage 26 architecture work. Stage 26 adds typed ports, shared runtime event schemas, and a report enqueue boundary so FastAPI depends on stable service contracts instead of worker internals. The current PostgreSQL session store, PostgreSQL report job store, SSE answer stream, polling report progress, pgvector knowledge store, and report worker remain the default runtime.

**Tech Stack:** FastAPI, Pydantic v2, Python `typing.Protocol`, PostgreSQL 5432, pgvector, vanilla ES modules, static HTML, pytest, Node syntax checks, PowerShell on Windows.

---

## Scope

This plan does:

- Record Stage 25.6 browser-fix closure for the recently fixed UI defects:
  - streamed follow-up output now renders inside the conversation;
  - Enter submits answers and Shift+Enter inserts a newline;
  - report detail buttons navigate to `/prep` and `/reports`;
  - top navigation links route to `/prep`, `/reports`, and `/help`;
  - `/reports` and `/help` pages are available.
- Add runtime port protocols that describe the current store, queue, publisher, LLM, and knowledge-store contracts.
- Split store protocols by responsibility and keep `InterviewSessionRepository` only as the current Local V1 aggregate protocol. Later stages can replace it with separate `SessionCommandRepository`, `ReportRepository`, and `QuestionEvaluationRepository` adapters.
- Add canonical event schemas for interview streaming and report progress.
- Move report enqueue scheduling out of `app/api/routes.py` into a service boundary.
- Add tests that prevent API routes from importing worker execution internals.
- Keep Local V1 behavior unchanged for users.

This plan does not:

- Add Redis, Celery, WebSocket, LangGraph, Docker, login, or multi-user isolation.
- Replace the existing PostgreSQL report worker.
- Replace SSE with WebSocket.
- Redesign the frontend.
- Rename existing database tables.

## Preconditions

- `app/static/report-center.js` and `app/test0.html` must already exist from the report-center fix commit `e288a99`.
- If either file is missing, stop and restore/apply the report-center fix before running this plan; Task 1 and Task 7 intentionally include `node --check app/static/report-center.js` to catch that gap.

## File Structure

- Modify: `docs/stage-21-browser-e2e-acceptance.md`
  - Record Stage 25.6 UI defect fixes and change final status only after manual browser verification is recorded.
- Modify: `docs/local-v1-runbook.md`
  - Add Stage 25.6 browser checks and include `report-center.js` in JS syntax checks.
- Create: `app/ports/__init__.py`
  - Package marker for runtime ports.
- Create: `app/ports/runtime.py`
  - Protocol definitions for session commands, report repository, question evaluations, report queue, knowledge store, event publisher, and LLM provider.
- Create: `app/services/event_publisher.py`
  - No-op runtime event publisher used to make the Local V1 publisher boundary explicit.
- Create: `app/services/runtime_events.py`
  - Pydantic event schemas shared by API, frontend contracts, and future WebSocket adapters.
- Create: `app/services/report_enqueue.py`
  - Service function that enqueues report generation and owns the Local V1 fallback background task behavior.
- Modify: `app/api/routes.py`
  - Remove direct import of `generate_report_for_session`.
  - Depend on `get_report_job_store`.
  - Call `enqueue_report_if_needed()` instead of `_schedule_report_if_needed()`.
  - Use typed runtime event payloads for `/answer/stream`.
- Modify: `tests/test_local_v1_docs.py`
  - Lock Stage 25.6 acceptance language and runbook checks.
- Create: `tests/test_runtime_ports.py`
  - Verify current concrete runtime classes satisfy the port protocols structurally.
- Create: `tests/test_runtime_events.py`
  - Verify SSE and report progress event schema serialization.
- Create: `tests/test_report_enqueue.py`
  - Verify report enqueue behavior and fallback background task behavior without involving FastAPI routes.
- Modify: `tests/test_api.py`
  - Keep existing streaming behavior green after typed event payloads.
- Modify: `tests/test_static_report_ui.py`
  - Ensure JS syntax checklist includes `report-center.js`.

---

### Task 1: Record Stage 25.6 Browser Fix Closure

**Files:**
- Modify: `tests/test_local_v1_docs.py`
- Modify: `docs/stage-21-browser-e2e-acceptance.md`
- Modify: `docs/local-v1-runbook.md`

- [ ] **Step 1: Add failing documentation assertions**

Append this test to `tests/test_local_v1_docs.py`:

```python
def test_stage_25_6_browser_fix_closure_is_recorded():
    doc = read_doc("stage-21-browser-e2e-acceptance.md")

    assert "Stage 25.6 Browser Fix Closure" in doc
    assert "S25-UI-1" in doc
    assert "streamed follow-up renders inside the conversation" in doc
    assert "S25-UI-2" in doc
    assert "Enter submits answers and Shift+Enter inserts a newline" in doc
    assert "S25-UI-3" in doc
    assert "report detail actions navigate to /prep and /reports" in doc
    assert "S25-UI-4" in doc
    assert "top navigation links route to /prep, /reports, and /help" in doc


def test_runbook_includes_stage_25_6_browser_checks():
    runbook = read_doc("local-v1-runbook.md")

    assert "Confirm streamed follow-up text appears in the conversation pane, not below the answer box." in runbook
    assert "Confirm Enter submits the answer and Shift+Enter inserts a newline." in runbook
    assert "Confirm Report Detail buttons: 再次模拟 opens /prep and 返回报告中心 opens /reports." in runbook
    assert "Confirm top navigation: 首页 opens /prep, 报告中心 opens /reports, 帮助 opens /help." in runbook
    assert "node --check app/static/report-center.js" in runbook
```

- [ ] **Step 2: Run the documentation tests and verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_stage_25_6_browser_fix_closure_is_recorded tests/test_local_v1_docs.py::test_runbook_includes_stage_25_6_browser_checks -q
```

Expected: FAIL because the Stage 25.6 closure text is not recorded yet.

- [ ] **Step 3: Update the browser acceptance record**

In `docs/stage-21-browser-e2e-acceptance.md`, insert this section after `## Stage 25.5 Attempt Notes` and before `## Final Status`:

```markdown
## Stage 25.6 Browser Fix Closure

| ID | Severity | Page/API | Symptom | Fix commit | Verification |
| --- | --- | --- | --- | --- | --- |
| S25-UI-1 | High | `/interview?session_id=...` | streamed follow-up renders below the answer box before entering the conversation | `81abb8b`, `be77d26` | `tests/test_static_report_ui.py::test_interview_page_streams_followup_inside_conversation_and_enter_submits`; service returned versioned `/static/interview.js?v=20260707-stream-chat` |
| S25-UI-2 | High | `/interview?session_id=...` | Enter did not submit answers | `81abb8b`, `be77d26` | Enter submits answers and Shift+Enter inserts a newline; `node --check app/static/interview.js` |
| S25-UI-3 | Medium | `/report-detail?session_id=...` | report detail actions navigate to /prep and /reports | `e288a99` | `tests/test_static_report_ui.py::test_report_detail_action_buttons_navigate_to_prep_and_report_center`; `/reports` route served report center |
| S25-UI-4 | Medium | top navigation | top navigation links route to /prep, /reports, and /help | `3e4027d` | `tests/test_static_report_ui.py::test_runtime_top_navigation_uses_real_routes`; `/help` route served help page |
```

Then update `## Final Status` to:

```markdown
Manual GUI acceptance remains required before declaring Local V1 RC accepted. API, worker, PostgreSQL, LLM, question-evaluation persistence, PDF generation, worker-delayed completion, service restart persistence, and HTTP/static browser-shell checks passed. Stage 25.6 closed the browser defects found during manual use; the final RC status should be changed to `Accepted as Local V1 RC` only after a human records the complete real-browser checklist as Pass.
```

- [ ] **Step 4: Update the runbook browser checklist**

In `docs/local-v1-runbook.md`, update section `5. Automated Smoke` by adding:

```powershell
node --check app/static/report-center.js
```

In section `6. 真实浏览器验收`, add these steps after the current step 11 and renumber the remaining items:

```markdown
12. Confirm streamed follow-up text appears in the conversation pane, not below the answer box.
13. Confirm Enter submits the answer and Shift+Enter inserts a newline.
```

Add these steps after the report detail step:

```markdown
21. Confirm Report Detail buttons: 再次模拟 opens /prep and 返回报告中心 opens /reports.
22. Confirm top navigation: 首页 opens /prep, 报告中心 opens /reports, 帮助 opens /help.
```

- [ ] **Step 5: Run documentation tests and verify they pass**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add tests/test_local_v1_docs.py docs/stage-21-browser-e2e-acceptance.md docs/local-v1-runbook.md
git commit -m "docs: record stage 25.6 browser fix closure"
```

---

### Task 2: Add Runtime Port Protocols

**Files:**
- Create: `app/ports/__init__.py`
- Create: `app/ports/runtime.py`
- Create: `app/services/event_publisher.py`
- Create: `tests/test_runtime_ports.py`

- [ ] **Step 1: Write failing protocol conformance tests**

Create `tests/test_runtime_ports.py`:

```python
from app.ports.runtime import (
    InterviewSessionRepository,
    KnowledgeRepository,
    QuestionEvaluationRepository,
    ReportJobQueue,
    ReportRepository,
    RuntimeEventPublisher,
    RuntimeLLMProvider,
    SessionCommandRepository,
)
from app.services.event_publisher import NoopRuntimeEventPublisher
from app.services.llm import OpenAIInterviewLLM
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.session import InterviewSessionStore
from app.services.vector_store import PgVectorKnowledgeStore


def test_runtime_protocols_are_runtime_checkable():
    for protocol in (
        SessionCommandRepository,
        ReportRepository,
        QuestionEvaluationRepository,
        InterviewSessionRepository,
        ReportJobQueue,
        KnowledgeRepository,
        RuntimeLLMProvider,
        RuntimeEventPublisher,
    ):
        assert getattr(protocol, "_is_runtime_protocol", False)


def test_memory_session_store_matches_split_repository_protocols():
    store = InterviewSessionStore()

    assert isinstance(store, SessionCommandRepository)
    assert isinstance(store, ReportRepository)
    assert isinstance(store, QuestionEvaluationRepository)
    assert isinstance(store, InterviewSessionRepository)


def test_postgres_session_store_matches_split_repository_protocols_without_connecting():
    store = object.__new__(PostgresInterviewSessionStore)

    assert isinstance(store, SessionCommandRepository)
    assert isinstance(store, ReportRepository)
    assert isinstance(store, QuestionEvaluationRepository)
    assert isinstance(store, InterviewSessionRepository)


def test_postgres_job_store_matches_report_queue_protocol_without_connecting():
    queue = object.__new__(PostgresReportJobStore)

    assert isinstance(queue, ReportJobQueue)


def test_noop_event_publisher_makes_local_v1_publisher_boundary_explicit():
    publisher = NoopRuntimeEventPublisher()

    assert isinstance(publisher, RuntimeEventPublisher)
    assert publisher.publish({"event": "ignored"}) is None


def test_vector_store_and_llm_expose_runtime_contracts_without_network_calls():
    vector_store = object.__new__(PgVectorKnowledgeStore)
    llm = object.__new__(OpenAIInterviewLLM)

    assert isinstance(vector_store, KnowledgeRepository)
    assert isinstance(llm, RuntimeLLMProvider)
```

- [ ] **Step 2: Run the protocol tests and verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_runtime_ports.py -q
```

Expected: FAIL because `app.ports.runtime` and `app.services.event_publisher` do not exist.

- [ ] **Step 3: Create the ports package marker**

Create `app/ports/__init__.py`:

```python
"""Runtime port definitions for Local V1 and future adapters."""
```

- [ ] **Step 4: Create runtime protocol definitions**

Create `app/ports/runtime.py`:

```python
from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

from app.graphs.interview_state import InterviewState
from app.services.prep import InterviewPlan
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.session import InterviewTurn, PreparedInterviewTurn


@runtime_checkable
class RuntimeLLMProvider(Protocol):
    def stream_followup(self, context: list[dict[str, str]]) -> Iterator[str]:
        ...


@runtime_checkable
class KnowledgeRepository(Protocol):
    def search(
        self,
        query_text: str,
        *,
        job_tags: list[str],
        source_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[Any]:
        ...


@runtime_checkable
class SessionCommandRepository(Protocol):
    @property
    def llm(self) -> RuntimeLLMProvider | None:
        ...

    def start(
        self,
        plan: InterviewPlan,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
    ) -> InterviewTurn:
        ...

    def get(self, session_id: str) -> InterviewState:
        ...

    def snapshot(self, session_id: str) -> dict[str, Any]:
        ...

    def submit_answer(self, session_id: str, answer: str) -> InterviewTurn:
        ...

    def prepare_streaming_answer(self, session_id: str, answer: str) -> PreparedInterviewTurn:
        ...

    def complete_streaming_answer(self, session_id: str, *, follow_up_text: str | None = None) -> InterviewState:
        ...

    def stream_followup(self, session_id: str) -> Iterator[str]:
        ...

    def skip(self, session_id: str) -> InterviewTurn:
        ...

    def finish(self, session_id: str) -> InterviewTurn:
        ...


@runtime_checkable
class ReportRepository(Protocol):
    def mark_report_processing(self, session_id: str) -> bool:
        ...

    def update_report_progress(self, session_id: str, progress: ReportProgress) -> None:
        ...

    def save_report(self, session_id: str, report: InterviewReport) -> None:
        ...

    def fail_report(self, session_id: str, error: str) -> None:
        ...

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        ...

    def list_reports(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class QuestionEvaluationRepository(Protocol):
    def save_question_evaluations(self, session_id: str, records: list[QuestionEvaluationRecord]) -> None:
        ...

    def list_question_evaluations(self, session_id: str) -> list[QuestionEvaluationRecord]:
        ...


@runtime_checkable
class InterviewSessionRepository(
    SessionCommandRepository,
    ReportRepository,
    QuestionEvaluationRepository,
    Protocol,
):
    """Current Local V1 aggregate protocol.

    Later stages can inject the smaller SessionCommandRepository,
    ReportRepository, and QuestionEvaluationRepository ports separately.
    """


@runtime_checkable
class ReportJobQueue(Protocol):
    def enqueue_report_request(self, session_id: str) -> dict[str, Any]:
        ...

    def claim_next(self, worker_id: str, lease_seconds: int | None = None) -> dict[str, Any] | None:
        ...

    def mark_completed(self, job_id: str) -> dict[str, Any] | None:
        ...

    def mark_failed(self, job_id: str, error: str) -> dict[str, Any] | None:
        ...

    def mark_retryable_failure(self, job_id: str, error: str) -> dict[str, Any] | None:
        ...

    def repair_orphan_processing_reports(self) -> int:
        ...

    def get_job_by_session(self, session_id: str) -> dict[str, Any] | None:
        ...


@runtime_checkable
class RuntimeEventPublisher(Protocol):
    def publish(self, event: Any) -> None:
        ...
```

- [ ] **Step 5: Create the Local V1 no-op event publisher**

Create `app/services/event_publisher.py`:

```python
from typing import Any


class NoopRuntimeEventPublisher:
    """Local V1 publisher boundary.

    The current runtime performs direct function calls, SSE streaming, and
    polling. Future adapters can replace this with Redis, WebSocket, or another
    event fanout implementation while satisfying RuntimeEventPublisher.
    """

    def publish(self, event: Any) -> None:
        return None
```

- [ ] **Step 6: Run the protocol tests and verify they pass**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_runtime_ports.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add app/ports app/services/event_publisher.py tests/test_runtime_ports.py
git commit -m "feat: define runtime port protocols"
```

---

### Task 3: Add Runtime Event Schemas

**Files:**
- Create: `app/services/runtime_events.py`
- Create: `tests/test_runtime_events.py`
- Modify: `app/api/routes.py`

- [ ] **Step 1: Write failing tests for event serialization**

Create `tests/test_runtime_events.py`:

```python
from app.services.runtime_events import (
    InterviewStreamChunkEvent,
    InterviewStreamDoneEvent,
    InterviewStreamErrorEvent,
    ReportProgressEvent,
)


def test_interview_stream_chunk_event_serializes_for_sse():
    event = InterviewStreamChunkEvent(delta="hello")

    assert event.event == "chunk"
    assert event.model_dump() == {"event": "chunk", "delta": "hello"}
    assert event.to_sse() == 'event: chunk\ndata: {"delta": "hello"}\n\n'


def test_interview_stream_done_event_serializes_without_event_field_in_data():
    payload = {"session_id": "s1", "status": "active", "follow_up": "next"}
    event = InterviewStreamDoneEvent(turn=payload)

    assert event.event == "done"
    assert event.to_sse() == 'event: done\ndata: {"session_id": "s1", "status": "active", "follow_up": "next"}\n\n'


def test_interview_stream_error_event_serializes_detail():
    event = InterviewStreamErrorEvent(detail="failed")

    assert event.event == "error"
    assert event.to_sse() == 'event: error\ndata: {"detail": "failed"}\n\n'


def test_new_sse_events_exactly_match_legacy_sse_strings():
    turn = {
        "session_id": "s1",
        "current_question": None,
        "follow_up": "next",
        "status": "active",
    }

    assert InterviewStreamChunkEvent(delta="abc").to_sse() == _legacy_sse_event("chunk", {"delta": "abc"})
    assert InterviewStreamDoneEvent(turn=turn).to_sse() == _legacy_sse_event("done", turn)
    assert InterviewStreamErrorEvent(detail="failed").to_sse() == _legacy_sse_event("error", {"detail": "failed"})


def test_report_progress_event_uses_current_polling_shape():
    event = ReportProgressEvent(
        session_id="s1",
        status="processing",
        stage="analyzing",
        percent=60,
        message="Analyzing answers.",
        report_job_id="job-1",
        current_question_id="q1",
        events=[{"stage": "analyzing", "message": "Analyzing answers."}],
        rag={"top_k": 5, "source_types": ["theory"], "matched_chunks": None},
    )

    assert event.model_dump()["status"] == "processing"
    assert event.model_dump()["rag"]["top_k"] == 5


def _legacy_sse_event(event: str, payload: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
```

- [ ] **Step 2: Run event tests and verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_runtime_events.py -q
```

Expected: FAIL because `app.services.runtime_events` does not exist.

- [ ] **Step 3: Create runtime event schemas**

Create `app/services/runtime_events.py`:

```python
import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class InterviewStreamChunkEvent(BaseModel):
    event: Literal["chunk"] = "chunk"
    delta: str

    def to_sse(self) -> str:
        payload = self.model_dump()
        event_name = payload.pop("event")
        return _format_sse(event_name, payload)


class InterviewStreamDoneEvent(BaseModel):
    event: Literal["done"] = "done"
    turn: dict[str, Any]

    def to_sse(self) -> str:
        return _format_sse(self.event, self.turn)


class InterviewStreamErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    detail: str

    def to_sse(self) -> str:
        payload = self.model_dump()
        event_name = payload.pop("event")
        return _format_sse(event_name, payload)


class ReportProgressEvent(BaseModel):
    session_id: str
    status: Literal["processing", "completed", "failed"]
    stage: str
    percent: int = Field(ge=0, le=100)
    message: str
    report_job_id: str | None = None
    current_question_id: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    rag: dict[str, Any] = Field(default_factory=dict)


def _format_sse(event_name: str, payload: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
```

- [ ] **Step 4: Run event tests and verify they pass**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_runtime_events.py -q
```

Expected: PASS.

- [ ] **Step 5: Use event schemas in the answer stream route**

Modify imports in `app/api/routes.py`:

```python
from app.services.runtime_events import (
    InterviewStreamChunkEvent,
    InterviewStreamDoneEvent,
    InterviewStreamErrorEvent,
)
```

In `submit_answer_stream()`, replace:

```python
yield _sse_event("chunk", {"delta": chunk})
```

with:

```python
yield InterviewStreamChunkEvent(delta=chunk).to_sse()
```

Replace:

```python
yield _sse_event("done", _turn_to_dict(turn))
```

with:

```python
yield InterviewStreamDoneEvent(turn=_turn_to_dict(turn)).to_sse()
```

Replace:

```python
yield _sse_event("error", {"detail": str(exc)})
```

with:

```python
yield InterviewStreamErrorEvent(detail=str(exc)).to_sse()
```

Then delete the private `_sse_event()` function and remove `import json` if it is unused.

- [ ] **Step 6: Run stream API regression**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_api.py::test_interview_answer_stream_flow tests/test_runtime_events.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add app/services/runtime_events.py app/api/routes.py tests/test_runtime_events.py
git commit -m "feat: add runtime event schemas"
```

---

### Task 4: Move Report Enqueue Logic Behind A Service Boundary

**Files:**
- Create: `app/services/report_enqueue.py`
- Create: `tests/test_report_enqueue.py`
- Modify: `app/api/routes.py`

- [ ] **Step 1: Write failing service-boundary tests**

Create `tests/test_report_enqueue.py`:

```python
from app.services.report import ReportProgress, ReportRecord
from app.services.report_enqueue import enqueue_report_if_needed


class FakeStore:
    def __init__(self, *, existing_record=None, mark_result=True):
        self.existing_record = existing_record
        self.mark_result = mark_result
        self.marked = []

    def get_report_record(self, session_id):
        return self.existing_record

    def mark_report_processing(self, session_id):
        self.marked.append(session_id)
        return self.mark_result


class FakeJobStore:
    def __init__(self, *, error=None):
        self.error = error
        self.enqueued = []

    def enqueue_report_request(self, session_id):
        if self.error is not None:
            raise self.error
        self.enqueued.append(session_id)
        return {"session_id": session_id, "status": "queued"}


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args):
        self.tasks.append((func, args))


def test_enqueue_report_ignores_active_turns():
    store = FakeStore()
    job_store = FakeJobStore()
    background_tasks = FakeBackgroundTasks()

    enqueue_report_if_needed(
        turn_status="active",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=background_tasks,
    )

    assert job_store.enqueued == []
    assert store.marked == []
    assert background_tasks.tasks == []


def test_enqueue_report_ignores_sessions_with_existing_report_record():
    existing = ReportRecord(
        status="processing",
        progress=ReportProgress(stage="retrieving", percent=20, message="Retrieving."),
    )
    store = FakeStore(existing_record=existing)
    job_store = FakeJobStore()

    enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=FakeBackgroundTasks(),
    )

    assert job_store.enqueued == []


def test_enqueue_report_uses_job_store_for_finished_sessions():
    store = FakeStore()
    job_store = FakeJobStore()

    enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=FakeBackgroundTasks(),
    )

    assert job_store.enqueued == ["s1"]


def test_enqueue_report_falls_back_to_background_task_when_queue_unavailable():
    store = FakeStore()
    job_store = FakeJobStore(error=RuntimeError("postgres queue unavailable"))
    background_tasks = FakeBackgroundTasks()

    enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=background_tasks,
    )

    assert store.marked == ["s1"]
    assert len(background_tasks.tasks) == 1
    task_func, task_args = background_tasks.tasks[0]
    assert task_func.__name__ == "generate_report_for_session"
    assert task_args == ("s1", store)


def test_enqueue_report_falls_back_for_database_style_exceptions():
    store = FakeStore()
    job_store = FakeJobStore(error=ConnectionError("database unavailable"))
    background_tasks = FakeBackgroundTasks()

    enqueue_report_if_needed(
        turn_status="finished",
        session_id="s1",
        store=store,
        job_store=job_store,
        background_tasks=background_tasks,
    )

    assert store.marked == ["s1"]
    assert len(background_tasks.tasks) == 1
```

- [ ] **Step 2: Run service-boundary tests and verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_enqueue.py -q
```

Expected: FAIL because `app.services.report_enqueue` does not exist.

- [ ] **Step 3: Create report enqueue service**

Create `app/services/report_enqueue.py`:

```python
from fastapi import BackgroundTasks

from app.ports.runtime import ReportJobQueue, ReportRepository
from app.services.report_tasks import generate_report_for_session


def enqueue_report_if_needed(
    *,
    turn_status: str,
    session_id: str,
    store: ReportRepository,
    job_store: ReportJobQueue,
    background_tasks: BackgroundTasks | None,
) -> None:
    if turn_status != "finished":
        return
    if store.get_report_record(session_id) is not None:
        return
    try:
        job_store.enqueue_report_request(session_id)
    except Exception:
        if background_tasks is not None and store.mark_report_processing(session_id):
            background_tasks.add_task(generate_report_for_session, session_id, store)
```

- [ ] **Step 4: Run service-boundary tests and verify they pass**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_enqueue.py -q
```

Expected: PASS.

- [ ] **Step 5: Confirm the report job dependency provider already exists**

Run:

```powershell
Select-String -Path app/services/runtime.py -Pattern "def get_report_job_store"
Select-String -Path app/api/routes.py -Pattern "get_report_job_store"
```

Expected:

- `app/services/runtime.py` contains `def get_report_job_store`.
- `app/api/routes.py` already imports `get_report_job_store` from `app.services.runtime`.

If either check fails, add `get_report_job_store` to `app/services/runtime.py` before continuing:

```python
def get_report_job_store():
    global _report_job_store
    if _report_job_store is None:
        _report_job_store = build_report_job_store()
    return _report_job_store
```

- [ ] **Step 6: Refactor API routes to use the service boundary**

Modify imports in `app/api/routes.py`.

Remove:

```python
from app.services.report_tasks import generate_report_for_session
```

Add:

```python
from app.services.report_enqueue import enqueue_report_if_needed
from app.services.report_jobs import PostgresReportJobStore
```

Update `submit_answer()` signature:

```python
def submit_answer(
    session_id: str,
    payload: AnswerRequest,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
    job_store: PostgresReportJobStore = Depends(get_report_job_store),
):
```

Replace:

```python
_schedule_report_if_needed(turn.status, session_id, background_tasks, store)
```

with:

```python
enqueue_report_if_needed(
    turn_status=turn.status,
    session_id=session_id,
    store=store,
    job_store=job_store,
    background_tasks=background_tasks,
)
```

Apply the same dependency and replacement in:

- `submit_answer_stream()`
- `finish_interview()`
- `skip_interview_question()`

Delete the private `_schedule_report_if_needed()` function from `app/api/routes.py`.

- [ ] **Step 7: Add a regression that API routes do not import report task execution**

Append this test to `tests/test_report_enqueue.py`:

```python
from pathlib import Path


def test_api_routes_do_not_import_report_task_executor_directly():
    routes_source = Path("app/api/routes.py").read_text(encoding="utf-8")

    assert "generate_report_for_session" not in routes_source
    assert "report_tasks" not in routes_source
    assert "enqueue_report_if_needed" in routes_source
```

- [ ] **Step 8: Run API and enqueue regression tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_enqueue.py tests/test_api.py::test_interview_answer_stream_flow tests/test_report_api.py::test_finish_interview_enqueues_report_job -q
```

Expected: PASS. If `test_finish_interview_enqueues_report_job` has a different exact name, run `F:\python3.11\python.exe -m pytest tests/test_report_api.py -q` and keep all report API tests passing.

- [ ] **Step 9: Commit**

Run:

```powershell
git add app/services/report_enqueue.py app/api/routes.py tests/test_report_enqueue.py
git commit -m "refactor: isolate report enqueue boundary"
```

---

### Task 5: Add Runtime Boundary Diagnostics

**Files:**
- Modify: `app/api/routes.py`
- Create: `tests/test_runtime_boundary_api.py`

- [ ] **Step 1: Write failing runtime boundary API test**

Create `tests/test_runtime_boundary_api.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_runtime_boundary_endpoint_reports_local_v1_components():
    response = client.get("/api/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_store"] in {"postgres", "memory"}
    assert body["session_store"] in {"PostgresInterviewSessionStore", "InterviewSessionStore"}
    assert body["report_job_store"] == "PostgresReportJobStore"
    assert body["report_worker"] == "external_process"
    assert body["event_transport"] == {"interview": "sse", "report_progress": "polling"}
    assert body["capabilities"] == {
        "redis": False,
        "celery": False,
        "websocket": False,
        "langgraph": False,
    }
    assert "postgres:postgres" not in str(body)
```

- [ ] **Step 2: Run runtime boundary API test and verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_runtime_boundary_api.py -q
```

Expected: FAIL because `/api/runtime` does not exist.

- [ ] **Step 3: Add `/api/runtime` endpoint**

Modify imports in `app/api/routes.py`:

```python
from app.services.config import get_runtime_store
```

Add this route after `health()`:

```python
@router.get("/runtime")
def runtime_boundary(
    store: InterviewSessionStore = Depends(get_session_store),
    job_store: PostgresReportJobStore = Depends(get_report_job_store),
):
    return {
        "runtime_store": get_runtime_store(),
        "session_store": type(store).__name__,
        "report_job_store": type(job_store).__name__,
        "report_worker": "external_process",
        "event_transport": {
            "interview": "sse",
            "report_progress": "polling",
        },
        "capabilities": {
            "redis": False,
            "celery": False,
            "websocket": False,
            "langgraph": False,
        },
    }
```

- [ ] **Step 4: Run runtime boundary API test and verify it passes**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_runtime_boundary_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add app/api/routes.py tests/test_runtime_boundary_api.py
git commit -m "feat: expose runtime boundary diagnostics"
```

---

### Task 6: Update Architecture Docs For Stage 26

**Files:**
- Modify: `docs/local-v1-runbook.md`
- Create: `docs/stage-26-runtime-boundary.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Add failing docs tests**

Append this test to `tests/test_local_v1_docs.py`:

```python
def test_stage_26_runtime_boundary_doc_exists_and_names_ports():
    doc = read_doc("stage-26-runtime-boundary.md")

    assert "Stage 26 Runtime Boundary" in doc
    assert "SessionCommandRepository" in doc
    assert "ReportRepository" in doc
    assert "QuestionEvaluationRepository" in doc
    assert "InterviewSessionRepository" in doc
    assert "ReportJobQueue" in doc
    assert "KnowledgeRepository" in doc
    assert "RuntimeEventPublisher" in doc
    assert "InterviewSessionRepository is the current Local V1 aggregate protocol" in doc
    assert "SSE remains the Local V1 interview stream transport" in doc
    assert "Report progress remains polling in Local V1" in doc
    assert "Redis, Celery, WebSocket, and LangGraph are future adapters" in doc
    assert "RuntimeEventPublisher is implemented by NoopRuntimeEventPublisher in Local V1" in doc


def test_runbook_mentions_runtime_boundary_endpoint():
    runbook = read_doc("local-v1-runbook.md")

    assert "GET http://127.0.0.1:8000/api/runtime" in runbook
    assert "Stage 26 exposes runtime boundary diagnostics" in runbook
```

- [ ] **Step 2: Run docs tests and verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_stage_26_runtime_boundary_doc_exists_and_names_ports tests/test_local_v1_docs.py::test_runbook_mentions_runtime_boundary_endpoint -q
```

Expected: FAIL because the Stage 26 doc and runbook text do not exist yet.

- [ ] **Step 3: Create Stage 26 architecture doc**

Create `docs/stage-26-runtime-boundary.md`:

```markdown
# Stage 26 Runtime Boundary

Stage 26 defines explicit Local V1 runtime boundaries without changing the deployed runtime.

## Ports

| Port | Default Local V1 implementation | Responsibility |
| --- | --- | --- |
| `SessionCommandRepository` | `InterviewSessionStore`, `PostgresInterviewSessionStore` | session state, answers, skip, finish, snapshots, and streamed follow-up control |
| `ReportRepository` | `InterviewSessionStore`, `PostgresInterviewSessionStore` | report records, report progress, report success/failure, and report listing |
| `QuestionEvaluationRepository` | `InterviewSessionStore`, `PostgresInterviewSessionStore` | save and list per-question evaluation records |
| `InterviewSessionRepository` | `InterviewSessionStore`, `PostgresInterviewSessionStore` | current Local V1 aggregate protocol over session commands, reports, and question evaluations |
| `ReportJobQueue` | `PostgresReportJobStore` | enqueue, lease, retry, and complete report jobs |
| `KnowledgeRepository` | `PgVectorKnowledgeStore` | retrieve local knowledge chunks for grounded evaluation |
| `RuntimeLLMProvider` | `OpenAIInterviewLLM` | generate follow-ups and report content |
| `RuntimeEventPublisher` | `NoopRuntimeEventPublisher` | future event fanout boundary |

InterviewSessionRepository is the current Local V1 aggregate protocol. Later stages should inject `SessionCommandRepository`, `ReportRepository`, and `QuestionEvaluationRepository` separately when those responsibilities split into different services.

## Event Transport

SSE remains the Local V1 interview stream transport.

Report progress remains polling in Local V1.

The canonical event shapes live in `app/services/runtime_events.py`, so future adapters can reuse the same payloads.

RuntimeEventPublisher is implemented by NoopRuntimeEventPublisher in Local V1 because the current runtime uses direct calls, SSE, and polling rather than a shared event bus.

## API And Worker Boundary

FastAPI owns request validation, page serving, session commands, report status reads, and report enqueue calls.

The report worker owns report execution, RAG-backed evaluation, report persistence, question evaluation persistence, retry handling, and failed job marking.

`app/services/report_enqueue.py` is the API-to-worker boundary for Local V1.

## Future Adapters

Redis, Celery, WebSocket, and LangGraph are future adapters. Stage 26 does not activate them. They must satisfy the Stage 26 ports before replacing the Local V1 defaults.
```

- [ ] **Step 4: Update the runbook**

In `docs/local-v1-runbook.md`, add this paragraph under `## 1.1 Architecture Position`:

```markdown
Stage 26 exposes runtime boundary diagnostics at `GET http://127.0.0.1:8000/api/runtime`. This endpoint identifies the active session store, report queue, worker boundary, and event transports without exposing database credentials.
```

Add this command to `## 5. Automated Smoke`:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/runtime
```

- [ ] **Step 5: Run docs tests and verify they pass**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add docs/stage-26-runtime-boundary.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: describe stage 26 runtime boundary"
```

---

### Task 7: Full Verification

**Files:**
- No source changes unless verification exposes a defect.

- [ ] **Step 1: Run focused Stage 26 tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_runtime_ports.py tests/test_runtime_events.py tests/test_report_enqueue.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run page and frontend contract tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py -q
```

Expected: PASS.

- [ ] **Step 3: Run report and stream regressions**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_api.py::test_interview_answer_stream_flow tests/test_report_api.py tests/test_report_worker.py -q
```

Expected: PASS.

- [ ] **Step 4: Run JavaScript syntax checks**

Run each command separately in PowerShell:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
node --check app/static/report-center.js
```

Expected: all commands exit 0.

- [ ] **Step 5: Run full test suite**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS, with the existing skipped tests unchanged.

- [ ] **Step 6: Verify runtime endpoint against the running server**

Start server and worker with the local defaults if they are not already running:

```powershell
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:PGVECTOR_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second PowerShell window:

```powershell
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:PGVECTOR_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
F:\python3.11\python.exe -m app.services.report_worker
```

In a third PowerShell window:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/health
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/runtime
```

Expected:

- `/api/health` returns `{"status": "ok"}`.
- `/api/runtime` names `PostgresInterviewSessionStore`, `PostgresReportJobStore`, `sse`, and `polling`.
- `/api/runtime` returns `capabilities` with `redis`, `celery`, `websocket`, and `langgraph` all set to `false`.
- `/api/runtime` does not include `postgres:postgres`.

- [ ] **Step 7: Commit verification documentation if any evidence files changed**

If Task 7 only ran commands, do not commit. If you updated docs with command results, run:

```powershell
git add docs/stage-21-browser-e2e-acceptance.md docs/local-v1-runbook.md docs/stage-26-runtime-boundary.md
git commit -m "docs: record stage 26 verification"
```

---

## Self-Review

- Spec coverage: Task 1 closes Stage 25.6 acceptance documentation. Tasks 2-5 create runtime ports, event schemas, API/worker enqueue separation, and runtime diagnostics. Task 6 documents the architecture boundary. Task 7 verifies the result.
- Placeholder scan: The plan contains no banned placeholder markers or unspecified implementation steps. Code snippets and exact commands are included for each implementation task.
- Type consistency: Protocol names used in docs and tests match `app/ports/runtime.py`: `SessionCommandRepository`, `ReportRepository`, `QuestionEvaluationRepository`, `InterviewSessionRepository`, `ReportJobQueue`, `KnowledgeRepository`, `RuntimeLLMProvider`, and `RuntimeEventPublisher`. Event schema names used in tests match `app/services/runtime_events.py`, and Local V1 publisher tests use `NoopRuntimeEventPublisher`.
- Scope check: Redis, Celery, WebSocket, and LangGraph are named only as future adapters and are not implemented in this plan.
