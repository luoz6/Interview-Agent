# Stage 26A Event-Driven Round Review Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in Redis/Celery-backed `round_closed` review pipeline that evaluates each interview question asynchronously during the interview, persists interim per-question review snapshots by question-id merge semantics, and keeps the existing final-report path authoritative for the completed Local V1 output.

**Architecture:** Keep the current FastAPI, PostgreSQL, pgvector, SSE, polling, `InterviewGraphRunner`, and Postgres final-report worker stable. Add a small event-model layer, an opt-in Celery event publisher, closed-round detection in the API transition path, and a Celery worker that evaluates one question at a time and upserts a single `QuestionEvaluationRecord`, with the event `answer_state` propagated explicitly into the saved row. Stage 26A keeps the Local V1 UI final-report-first, so the new round-review rows are a backend-visible interim trace; the final report worker must overwrite matching question ids by upsert instead of session-wide delete.

**Tech Stack:** Python 3.11, FastAPI, Celery, Redis, PostgreSQL, pgvector, pytest, existing `ShadowReviewerAgent`, existing `QuestionEvaluationRecord`, existing four-page static frontend.

---

## File Structure

- Modify: `requirements.txt`
  - Add `celery` and `redis` runtime dependencies for the new event worker path.

- Modify: `app/services/config.py`
  - Add `INTERVIEW_EVENT_BACKEND` and `REDIS_URL` configuration helpers with conservative defaults.

- Modify: `app/services/runtime.py`
  - Add `build_event_publisher()` / `get_event_publisher()`, extract shared runtime LLM resolution, and keep runtime singletons test-resettable.

- Modify: `app/api/routes.py`
  - Publish `RoundClosedEvent` after `answer`, `answer/stream`, `skip`, and `finish` transitions when a question actually closes.

- Modify: `app/ports/runtime.py`
  - Extend `QuestionEvaluationRepository` with single-record upsert support and keep the runtime publisher boundary explicit.

- Modify: `app/services/event_publisher.py`
  - Keep `NoopRuntimeEventPublisher` and add a Celery-backed publisher implementation.

- Create: `app/services/runtime_domain_events.py`
  - Define typed runtime event payloads for Stage 26A.

- Create: `app/services/celery_app.py`
  - Build the shared Celery application from runtime config.

- Create: `app/services/interview_rounds.py`
  - Detect when a question closes and build the typed `RoundClosedEvent`.

- Create: `app/services/round_review.py`
  - Build a single-question finished state from the persisted interview session so the existing evaluator can review one round at a time.

- Create: `app/services/round_review_tasks.py`
  - Define the Celery task that consumes `RoundClosedEvent`, evaluates the question, and upserts a `QuestionEvaluationRecord`.

- Modify: `app/services/session.py`
  - Add `upsert_question_evaluation()` and make bulk save merge by question id for in-memory storage.

- Modify: `app/services/postgres_session.py`
  - Add `upsert_question_evaluation()` and remove session-wide delete semantics from bulk save.

- Modify: `app/services/question_evaluations.py`
  - Let event-driven callers override `answer_state` when converting `InterviewFeedback` into `QuestionEvaluationRecord`.

- Modify: `app/services/report_tasks.py`
  - Keep the final report worker authoritative while reusing merge-safe question-evaluation persistence and shared LLM resolution.

- Modify: `tests/test_runtime_provider.py`
  - Cover the new config and publisher factory behavior.

- Modify: `tests/test_runtime_boundary_api.py`
  - Cover the runtime boundary response when the event backend is `noop` vs `celery`.

- Create: `tests/test_event_publisher.py`
  - Unit-test `CeleryRuntimeEventPublisher`.

- Create: `tests/test_interview_rounds.py`
  - Unit-test closed-round detection and event payload construction.

- Modify: `tests/test_api.py`
  - Verify API transitions publish `RoundClosedEvent` only when a question closes.

- Modify: `tests/test_question_evaluations.py`
  - Cover explicit `answer_state` override, single-record upsert behavior, and merge-safe bulk save in the in-memory store.

- Modify: `tests/test_postgres_session_store.py`
  - Cover single-record question-evaluation upsert and merge-safe bulk save in PostgreSQL.

- Create: `tests/test_round_review.py`
  - Cover single-question review-state construction, prompt restoration, event `answer_state` propagation, and Celery task behavior.

- Modify: `tests/test_report_tasks.py`
  - Verify the final report worker still persists completed question evaluations correctly after the storage semantics switch from session-replace to merge-by-question-id.

- Modify: `README.md`
  - Document Stage 26A as an opt-in event backend upgrade.

- Modify: `docs/local-v1-runbook.md`
  - Add Redis/Celery worker startup and verification steps for Stage 26A.

---

### Task 1: Add Event Backend Configuration And Runtime Boundary Reporting

**Files:**
- Modify: `requirements.txt`
- Modify: `app/services/config.py`
- Modify: `app/services/runtime.py`
- Modify: `app/api/routes.py`
- Test: `tests/test_runtime_provider.py`
- Test: `tests/test_runtime_boundary_api.py`

- [ ] **Step 1: Write the failing config and runtime-boundary tests**

Add to `tests/test_runtime_provider.py`:

```python
def test_config_exposes_event_backend_and_redis_defaults(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from app.services.config import (
        DEFAULT_REDIS_URL,
        DEFAULT_RUNTIME_EVENT_BACKEND,
        get_redis_url,
        get_runtime_event_backend,
    )

    assert DEFAULT_RUNTIME_EVENT_BACKEND == "noop"
    assert DEFAULT_REDIS_URL == "redis://127.0.0.1:6379/0"
    assert get_runtime_event_backend() == "noop"
    assert get_redis_url() == "redis://127.0.0.1:6379/0"


def test_build_event_publisher_defaults_to_noop(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)

    from app.services.event_publisher import NoopRuntimeEventPublisher
    from app.services.runtime import build_event_publisher

    publisher = build_event_publisher()

    assert isinstance(publisher, NoopRuntimeEventPublisher)
```

Replace `tests/test_runtime_boundary_api.py` with:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_runtime_boundary_endpoint_reports_local_v1_components():
    client = TestClient(app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_store"] in {"memory", "postgres"}
    assert body["session_store"] in {
        "InterviewSessionStore",
        "PostgresInterviewSessionStore",
    }
    assert body["report_job_store"] == "PostgresReportJobStore"
    assert body["report_worker"] == "external_process"
    assert body["event_transport"] == {
        "interview": "sse",
        "report_progress": "polling",
    }
    assert body["event_backend"] == "noop"
    assert body["capabilities"] == {
        "redis": False,
        "celery": False,
        "websocket": False,
        "langgraph": False,
    }
    assert "postgres:postgres" not in str(body)


def test_runtime_boundary_endpoint_reports_celery_round_review_mode(monkeypatch):
    monkeypatch.setenv("INTERVIEW_EVENT_BACKEND", "celery")
    client = TestClient(app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["event_backend"] == "celery"
    assert body["capabilities"] == {
        "redis": True,
        "celery": True,
        "websocket": False,
        "langgraph": False,
    }
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py tests/test_runtime_boundary_api.py -q
```

Expected: FAIL because `config.py` does not expose event-backend helpers, `runtime.py` does not build an event publisher, and `/api/runtime` does not include `event_backend`.

- [ ] **Step 3: Add the new dependencies**

Update `requirements.txt` to:

```text
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
pytest>=8.0.0
httpx>=0.27.0
pydantic>=2.0.0
langchain>=1.0.0
langchain-openai>=1.0.0
psycopg2-binary>=2.9.9
pgvector>=0.3.5
sentence-transformers>=3.0.0
langchain-huggingface>=0.1.0
reportlab>=4.2.0
celery>=5.4.0
redis>=5.0.7
```

- [ ] **Step 4: Add event-backend config helpers**

Update `app/services/config.py` to:

```python
import os


DEFAULT_POSTGRES_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/interview"
DEFAULT_RUNTIME_STORE = "postgres"
DEFAULT_RUNTIME_TABLE_PREFIX = "interview"
DEFAULT_PGVECTOR_TABLE = "knowledge_chunks"
DEFAULT_RUNTIME_EVENT_BACKEND = "noop"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


def get_postgres_dsn() -> str:
    return os.getenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN).strip() or DEFAULT_POSTGRES_DSN


def get_runtime_store() -> str:
    return os.getenv("INTERVIEW_RUNTIME_STORE", DEFAULT_RUNTIME_STORE).strip().lower() or DEFAULT_RUNTIME_STORE


def get_runtime_table_prefix() -> str:
    prefix = os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX") or os.getenv("INTERVIEW_TABLE_PREFIX")
    return prefix.strip() if prefix and prefix.strip() else DEFAULT_RUNTIME_TABLE_PREFIX


def get_pgvector_table() -> str:
    return os.getenv("PGVECTOR_TABLE", DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE


def get_runtime_event_backend() -> str:
    raw = os.getenv("INTERVIEW_EVENT_BACKEND", DEFAULT_RUNTIME_EVENT_BACKEND)
    return raw.strip().lower() or DEFAULT_RUNTIME_EVENT_BACKEND


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", DEFAULT_REDIS_URL).strip() or DEFAULT_REDIS_URL
```

- [ ] **Step 5: Add event-publisher singleton plumbing**

Update the import block and globals in `app/services/runtime.py`:

```python
from app.services.config import (
    DEFAULT_POSTGRES_DSN,
    get_postgres_dsn,
    get_redis_url,
    get_runtime_event_backend,
    get_runtime_store,
    get_runtime_table_prefix,
)
from app.services.drafts import AnonymousDraftStore
from app.services.event_publisher import (
    CeleryRuntimeEventPublisher,
    NoopRuntimeEventPublisher,
)
from app.services.llm import InterviewLLM, OpenAIInterviewLLM
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.report_jobs import PostgresReportJobStore
from app.services.session import InterviewSessionStore
from app.services.vector_store import PgVectorKnowledgeStore, get_knowledge_store
```

Add the singleton:

```python
_event_publisher = None
```

Add the builder and getter:

```python
def build_event_publisher():
    backend = get_runtime_event_backend()
    if backend == "noop":
        return NoopRuntimeEventPublisher()
    if backend == "celery":
        from app.services.celery_app import celery_app

        return CeleryRuntimeEventPublisher(celery_app=celery_app)
    raise RuntimeError(f"unsupported INTERVIEW_EVENT_BACKEND: {backend}")


def get_event_publisher():
    global _event_publisher
    if _event_publisher is None:
        _event_publisher = build_event_publisher()
    return _event_publisher
```

Update `reset_runtime_for_tests()`:

```python
def reset_runtime_for_tests() -> None:
    global _session_store, _report_job_store, _report_executor, _draft_store, _event_publisher
    _session_store = None
    _report_job_store = None
    _report_executor = None
    _draft_store = None
    _event_publisher = None
```

Add the new factory test to `tests/test_runtime_provider.py`:

```python
def test_get_event_publisher_caches_until_reset(monkeypatch):
    from app.services.runtime import get_event_publisher, reset_runtime_for_tests

    created = []

    def fake_builder():
        value = object()
        created.append(value)
        return value

    reset_runtime_for_tests()
    monkeypatch.setattr("app.services.runtime.build_event_publisher", fake_builder)

    first = get_event_publisher()
    second = get_event_publisher()

    assert first is second
    assert len(created) == 1

    reset_runtime_for_tests()
    third = get_event_publisher()

    assert third is not first
    assert len(created) == 2
```

- [ ] **Step 6: Expose the event backend in `/api/runtime`**

Update `app/api/routes.py`:

```python
from app.services.config import get_runtime_event_backend, get_runtime_store
```

Then replace `runtime_boundary()` with:

```python
@router.get("/runtime")
def runtime_boundary():
    runtime_store = get_runtime_store()
    event_backend = get_runtime_event_backend()
    session_store = (
        "PostgresInterviewSessionStore"
        if runtime_store == "postgres"
        else "InterviewSessionStore"
    )
    return {
        "runtime_store": runtime_store,
        "session_store": session_store,
        "report_job_store": "PostgresReportJobStore",
        "report_worker": "external_process",
        "event_transport": {
            "interview": "sse",
            "report_progress": "polling",
        },
        "event_backend": event_backend,
        "capabilities": {
            "redis": event_backend == "celery",
            "celery": event_backend == "celery",
            "websocket": False,
            "langgraph": False,
        },
    }
```

- [ ] **Step 7: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py tests/test_runtime_boundary_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt app/services/config.py app/services/runtime.py app/api/routes.py tests/test_runtime_provider.py tests/test_runtime_boundary_api.py
git commit -m "feat: add stage 26a event backend config"
```

---

### Task 2: Define Typed Runtime Events And A Celery Publisher

**Files:**
- Create: `app/services/runtime_domain_events.py`
- Create: `app/services/celery_app.py`
- Modify: `app/services/event_publisher.py`
- Test: `tests/test_event_publisher.py`

- [ ] **Step 1: Write the failing event-publisher tests**

Create `tests/test_event_publisher.py`:

```python
import pytest

from app.services.event_publisher import CeleryRuntimeEventPublisher, NoopRuntimeEventPublisher
from app.services.runtime_domain_events import RoundClosedEvent


class FakeCeleryApp:
    def __init__(self):
        self.calls = []

    def send_task(self, name: str, args=None, kwargs=None):
        self.calls.append((name, args or [], kwargs or {}))


def test_noop_runtime_event_publisher_still_ignores_events():
    publisher = NoopRuntimeEventPublisher()

    assert publisher.publish({"event": "ignored"}) is None


def test_celery_runtime_event_publisher_routes_round_closed_event():
    app = FakeCeleryApp()
    publisher = CeleryRuntimeEventPublisher(celery_app=app)

    publisher.publish(
        RoundClosedEvent(
            session_id="s1",
            question_id="q1",
            answer_state="answered",
            job_tags=["python", "redis"],
        )
    )

    assert len(app.calls) == 1
    name, args, kwargs = app.calls[0]
    assert name == "app.services.round_review_tasks.run_closed_round_review"
    assert kwargs == {}
    payload = args[0]
    assert payload["event_type"] == "round_closed"
    assert payload["session_id"] == "s1"
    assert payload["question_id"] == "q1"
    assert payload["answer_state"] == "answered"
    assert payload["job_tags"] == ["python", "redis"]
    assert payload["emitted_at"]


def test_celery_runtime_event_publisher_rejects_unknown_event_type():
    app = FakeCeleryApp()
    publisher = CeleryRuntimeEventPublisher(celery_app=app)

    with pytest.raises(ValueError, match="unsupported runtime event"):
        publisher.publish({"event_type": "unknown"})
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_event_publisher.py -q
```

Expected: FAIL because `runtime_domain_events.py` and `CeleryRuntimeEventPublisher` do not exist.

- [ ] **Step 3: Create the typed event model**

Create `app/services/runtime_domain_events.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field

from app.services.report import utc_now_iso


class RoundClosedEvent(BaseModel):
    event_type: Literal["round_closed"] = "round_closed"
    session_id: str
    question_id: str
    answer_state: Literal["answered", "skipped", "unanswered"]
    job_tags: list[str] = Field(default_factory=list)
    emitted_at: str = Field(default_factory=utc_now_iso)
```

- [ ] **Step 4: Create the shared Celery app**

Create `app/services/celery_app.py`:

```python
from celery import Celery

from app.services.config import get_redis_url


celery_app = Celery("interview_agent")
celery_app.conf.update(
    broker_url=get_redis_url(),
    result_backend=get_redis_url(),
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
```

- [ ] **Step 5: Add the Celery publisher implementation**

Replace `app/services/event_publisher.py` with:

```python
from typing import Any

from app.services.runtime_domain_events import RoundClosedEvent


class NoopRuntimeEventPublisher:
    """Local V1 publisher boundary for future event fanout adapters."""

    def publish(self, event: Any) -> None:
        return None


class CeleryRuntimeEventPublisher:
    def __init__(self, *, celery_app) -> None:
        self._celery_app = celery_app

    def publish(self, event: Any) -> None:
        if isinstance(event, RoundClosedEvent):
            self._celery_app.send_task(
                "app.services.round_review_tasks.run_closed_round_review",
                args=[event.model_dump(mode="json")],
            )
            return None
        raise ValueError(f"unsupported runtime event: {type(event).__name__}")
```

- [ ] **Step 6: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_event_publisher.py tests/test_runtime_provider.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/runtime_domain_events.py app/services/celery_app.py app/services/event_publisher.py tests/test_event_publisher.py
git commit -m "feat: add typed runtime event publisher"
```

---

### Task 3: Detect Closed Rounds And Publish Events From API Transitions

**Files:**
- Create: `app/services/interview_rounds.py`
- Modify: `app/services/runtime.py`
- Modify: `app/api/routes.py`
- Test: `tests/test_interview_rounds.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing round-detection tests**

Create `tests/test_interview_rounds.py`:

```python
from copy import deepcopy

from app.graphs.interview_graph import InterviewGraphRunner
from app.services.interview_rounds import round_closed_event_from_transition
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport
from app.services.session import skip_interview_question_state


def make_plan():
    return InterviewPlan(
        title="Backend mock interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Introduce the project.",
                focus="project",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis.",
                focus="Redis",
            ),
        ],
    )


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the cache invalidation strategy."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please explain the cache invalidation strategy."

    def generate_report(self, plan, evaluation_items: list[dict], session_id: str) -> InterviewReport:
        raise AssertionError


def make_start_kwargs():
    return {
        "session_id": "s1",
        "plan": make_plan(),
        "job_description": "Backend role using Python and Redis.",
        "resume_text": "Built a Python API with Redis.",
        "job_tags": ["python", "redis"],
    }


def test_round_closed_event_is_none_for_first_answer_followup_transition():
    runner = InterviewGraphRunner(llm=FakeLLM())
    before = runner.start(**make_start_kwargs())
    after = runner.submit_answer(before, "I improved cache consistency.")

    assert round_closed_event_from_transition(before, after) is None


def test_round_closed_event_is_emitted_when_question_advances():
    runner = InterviewGraphRunner(llm=FakeLLM())
    before = runner.start(**make_start_kwargs())
    followup_state = runner.submit_answer(before, "I improved cache consistency.")
    after = runner.submit_answer(followup_state, "I added delayed double delete.")

    event = round_closed_event_from_transition(followup_state, after)

    assert event is not None
    assert event.session_id == "s1"
    assert event.question_id == "q1"
    assert event.answer_state == "answered"
    assert event.job_tags == ["python", "redis"]


def test_round_closed_event_is_emitted_for_skip():
    runner = InterviewGraphRunner(llm=FakeLLM())
    before = runner.start(**make_start_kwargs())
    after = skip_interview_question_state(deepcopy(before))

    event = round_closed_event_from_transition(before, after)

    assert event is not None
    assert event.question_id == "q1"
    assert event.answer_state == "skipped"
```

Append to `tests/test_api.py`:

```python
def test_answer_route_publishes_round_closed_event_only_when_question_closes(monkeypatch):
    published = []

    class FakePublisher:
        def publish(self, event):
            published.append(event)

    monkeypatch.setattr(route_module, "get_report_job_store", lambda: (_ for _ in ()).throw(RuntimeError("disabled")))
    monkeypatch.setattr(route_module, "get_event_publisher", lambda: FakePublisher())
    client = make_client()

    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    first = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I used Redis to cache hot records."},
    )
    second = client.post(
        f"/api/interviews/{session_id}/answer",
        json={"answer": "I added delayed double delete."},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "answered"


def test_skip_route_publishes_round_closed_event(monkeypatch):
    published = []

    class FakePublisher:
        def publish(self, event):
            published.append(event)

    monkeypatch.setattr(route_module, "get_report_job_store", lambda: (_ for _ in ()).throw(RuntimeError("disabled")))
    monkeypatch.setattr(route_module, "get_event_publisher", lambda: FakePublisher())
    client = make_client()

    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    response = client.post(f"/api/interviews/{session_id}/skip")

    assert response.status_code == 200
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "skipped"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_rounds.py tests/test_api.py::test_answer_route_publishes_round_closed_event_only_when_question_closes tests/test_api.py::test_skip_route_publishes_round_closed_event -q
```

Expected: FAIL because `interview_rounds.py` and `get_event_publisher()` API wiring do not exist.

- [ ] **Step 3: Create the closed-round detector**

Create `app/services/interview_rounds.py`:

```python
from app.graphs.interview_state import count_candidate_answers_for_question, get_current_question
from app.services.runtime_domain_events import RoundClosedEvent


def round_closed_event_from_transition(before_state: dict, after_state: dict) -> RoundClosedEvent | None:
    closed_question = get_current_question(before_state)
    if closed_question is None:
        return None

    after_current = get_current_question(after_state)
    same_question_still_active = (
        after_state["status"] != "finished"
        and after_current is not None
        and after_current.id == closed_question.id
    )
    if same_question_still_active:
        return None

    return RoundClosedEvent(
        session_id=after_state["session_id"],
        question_id=closed_question.id,
        answer_state=_answer_state_for_question(after_state, closed_question.id),
        job_tags=list(after_state["job_tags"]),
    )


def _answer_state_for_question(state: dict, question_id: str) -> str:
    if question_id in state.get("skipped_question_ids", []):
        return "skipped"
    if count_candidate_answers_for_question(state, question_id) > 0:
        return "answered"
    return "unanswered"
```

- [ ] **Step 4: Add the runtime getter export and API dependency**

In `app/services/runtime.py`, make sure `get_event_publisher` is importable alongside the existing runtime getters.

In `app/api/routes.py`, update imports:

```python
from copy import deepcopy

from app.services.interview_rounds import round_closed_event_from_transition
from app.services.runtime import (
    get_draft_store,
    get_event_publisher,
    get_report_job_store,
    get_session_store,
)
```

Update `/interviews/{session_id}/answer`:

```python
@router.post("/interviews/{session_id}/answer")
def submit_answer(
    session_id: str,
    payload: AnswerRequest,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
    publisher=Depends(get_event_publisher),
):
    try:
        before_state = deepcopy(store.get(session_id))
        turn = store.submit_answer(session_id, payload.answer)
        after_state = store.get(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    event = round_closed_event_from_transition(before_state, after_state)
    if event is not None:
        publisher.publish(event)
    enqueue_report_if_needed(
        turn_status=turn.status,
        session_id=session_id,
        store=store,
        job_store_factory=get_report_job_store,
        background_tasks=background_tasks,
    )
    return _turn_to_dict(turn)
```

Update `/interviews/{session_id}/finish` and `/interviews/{session_id}/skip` with the same `before_state` / `after_state` / `publisher.publish(event)` pattern.

Update `/interviews/{session_id}/answer/stream` to accept `publisher=Depends(get_event_publisher)` and capture `before_state` before `store.prepare_streaming_answer(...)`:

```python
    try:
        before_state = deepcopy(store.get(session_id))
        prepared = store.prepare_streaming_answer(session_id, payload.answer)
```

Then use the injected `publisher` inside `event_stream()`:

```python
            finalized_state = store.complete_streaming_answer(
                session_id,
                follow_up_text=follow_up_text,
            )
            event = round_closed_event_from_transition(before_state, finalized_state)
            if event is not None:
                publisher.publish(event)
```

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_rounds.py tests/test_api.py::test_answer_route_publishes_round_closed_event_only_when_question_closes tests/test_api.py::test_skip_route_publishes_round_closed_event -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/interview_rounds.py app/api/routes.py tests/test_interview_rounds.py tests/test_api.py
git commit -m "feat: publish round closed events from interview routes"
```

---

### Task 4: Make Question Evaluation Persistence Merge-Safe

**Files:**
- Modify: `app/services/question_evaluations.py`
- Modify: `app/ports/runtime.py`
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Test: `tests/test_question_evaluations.py`
- Test: `tests/test_postgres_session_store.py`
- Test: `tests/test_report_tasks.py`

- [ ] **Step 1: Write the failing persistence tests**

Append to `tests/test_question_evaluations.py`:

```python
def test_question_evaluation_from_feedback_supports_answer_state_override():
    record = question_evaluation_from_feedback(
        session_id="s1",
        feedback=make_feedback(),
        answer_state="skipped",
    )

    assert record.answer_state == "skipped"


def test_in_memory_session_store_upserts_single_question_evaluation():
    store = InterviewSessionStore()
    turn = store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    first = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback(),
    )
    second_feedback = make_feedback().model_copy(update={"score": 91})
    second = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=second_feedback,
    )

    store.upsert_question_evaluation(turn.session_id, first)
    store.upsert_question_evaluation(turn.session_id, second)

    saved = store.list_question_evaluations(turn.session_id)
    assert len(saved) == 1
    assert saved[0].feedback.score == 91


def test_in_memory_session_store_bulk_save_merges_existing_question_evaluations():
    store = InterviewSessionStore()
    turn = store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    q2_record = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback().model_copy(
            update={
                "question_id": "q2",
                "question_text": "Design the service.",
                "user_answer": "I describe the API, cache, and datastore.",
            }
        ),
    )
    replacement = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=make_feedback().model_copy(update={"score": 91}),
    )

    store.upsert_question_evaluation(turn.session_id, q2_record)
    store.save_question_evaluations(turn.session_id, [replacement])

    saved = sorted(
        store.list_question_evaluations(turn.session_id),
        key=lambda item: item.question_id,
    )
    assert [item.question_id for item in saved] == ["q1", "q2"]
    assert saved[0].feedback.score == 91
```

Append to `tests/test_postgres_session_store.py`:

```python
def test_postgres_store_upserts_single_question_evaluation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["backend"],
    )
    first = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=InterviewFeedback(
            question_id="q1",
            question_text="Describe your backend project.",
            user_answer="I built a FastAPI API.",
            score=80,
            dimension_scores=make_dimension_scores(80),
            rationale="The answer covered the project shape.",
            critique="Needs more failure-mode detail.",
            better_answer="Explain traffic, storage, caching, and failure handling.",
            references=[],
        ),
    )
    second = question_evaluation_from_feedback(
        session_id=turn.session_id,
        feedback=InterviewFeedback(
            question_id="q1",
            question_text="Describe your backend project.",
            user_answer="I built a FastAPI API.",
            score=91,
            dimension_scores=make_dimension_scores(91),
            rationale="The answer covered the project shape and runtime tradeoffs.",
            critique="Metrics can still be sharper.",
            better_answer="Add p95 latency, cache hit ratio, and rollback detail.",
            references=[],
        ),
    )

    store.upsert_question_evaluation(turn.session_id, first)
    store.upsert_question_evaluation(turn.session_id, second)

    saved = store.list_question_evaluations(turn.session_id)
    assert len(saved) == 1
    assert saved[0].feedback.score == 91


def test_postgres_store_bulk_save_merges_existing_question_evaluations():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    store.upsert_question_evaluation(
        turn.session_id,
        question_evaluation_from_feedback(
            session_id=turn.session_id,
            feedback=InterviewFeedback(
                question_id="q2",
                question_text="Design the service.",
                user_answer="I describe the API, cache, and datastore.",
                score=76,
                dimension_scores=make_dimension_scores(76),
                rationale="It covered the service boundaries.",
                critique="Failure-mode detail was thin.",
                better_answer="Add queueing, retries, and degradation paths.",
                references=[],
            ),
        ),
    )
    store.save_question_evaluations(
        turn.session_id,
        [
            question_evaluation_from_feedback(
                session_id=turn.session_id,
                feedback=InterviewFeedback(
                    question_id="q1",
                    question_text="Describe your backend project.",
                    user_answer="I built a FastAPI API.",
                    score=91,
                    dimension_scores=make_dimension_scores(91),
                    rationale="The answer connected the project to runtime tradeoffs.",
                    critique="Metrics can be sharper.",
                    better_answer="Add p95 latency, cache hit ratio, and rollback details.",
                    references=[],
                ),
            )
        ],
    )

    recovered_store = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
    )
    saved = recovered_store.list_question_evaluations(turn.session_id)
    assert [item.question_id for item in saved] == ["q1", "q2"]
    assert next(item for item in saved if item.question_id == "q1").feedback.score == 91
```

Append to `tests/test_report_tasks.py`:

```python
def test_execute_report_generation_overwrites_matching_question_evaluations_without_clearing_other_rows():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore()
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)
    store.upsert_question_evaluation(
        session.session_id,
        question_evaluation_from_feedback(
            session_id=session.session_id,
            feedback=InterviewFeedback(
                question_id="q2",
                question_text="Design the service.",
                user_answer="I describe the API, cache, and datastore.",
                score=76,
                dimension_scores=make_dimension_scores(76),
                rationale="Interim round review recorded service boundaries.",
                critique="Failure handling was thin.",
                better_answer="Add retries, queues, and degradation paths.",
                references=[],
            ),
        ),
    )

    execute_report_generation(
        session_id=session.session_id,
        store=store,
        llm=ReportLLM(),
        vector_store=FakeVectorStore(),
    )

    saved = sorted(
        store.list_question_evaluations(session.session_id),
        key=lambda item: item.question_id,
    )
    assert [item.question_id for item in saved] == ["q1", "q2"]
    assert next(item for item in saved if item.question_id == "q1").feedback.score == 81
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_question_evaluations.py tests/test_postgres_session_store.py::test_postgres_store_upserts_single_question_evaluation tests/test_postgres_session_store.py::test_postgres_store_bulk_save_merges_existing_question_evaluations tests/test_report_tasks.py::test_execute_report_generation_overwrites_matching_question_evaluations_without_clearing_other_rows -q
```

Expected: FAIL because `question_evaluation_from_feedback()` does not accept an explicit `answer_state`, the repositories do not expose `upsert_question_evaluation()`, and bulk save still behaves like session-replace instead of merge-by-question-id.

- [ ] **Step 3: Let feedback conversion accept an explicit `answer_state`**

Update `app/services/question_evaluations.py`:

```python
def question_evaluation_from_feedback(
    *,
    session_id: str,
    feedback: InterviewFeedback,
    answer_state: Literal["answered", "skipped", "unanswered"] | None = None,
) -> QuestionEvaluationRecord:
    return QuestionEvaluationRecord(
        session_id=session_id,
        question_id=feedback.question_id,
        answer_state=answer_state or feedback.answer_state,
        status="completed",
        feedback=feedback,
    )
```

- [ ] **Step 4: Extend the runtime protocol and in-memory store**

Update `app/ports/runtime.py`:

```python
@runtime_checkable
class QuestionEvaluationRepository(Protocol):
    def save_question_evaluations(self, session_id: str, records: list[QuestionEvaluationRecord]) -> None:
        ...

    def upsert_question_evaluation(
        self,
        session_id: str,
        record: QuestionEvaluationRecord,
    ) -> None:
        ...

    def list_question_evaluations(self, session_id: str) -> list[QuestionEvaluationRecord]:
        ...
```

Update `app/services/session.py`:

```python
    def save_question_evaluations(
        self,
        session_id: str,
        records: list[QuestionEvaluationRecord],
    ) -> None:
        self.get(session_id)
        for record in records:
            self.upsert_question_evaluation(session_id, record)

    def upsert_question_evaluation(
        self,
        session_id: str,
        record: QuestionEvaluationRecord,
    ) -> None:
        self.get(session_id)
        existing = {
            item.question_id: item
            for item in self._question_evaluations.get(session_id, [])
        }
        existing[record.question_id] = record
        self._question_evaluations[session_id] = list(existing.values())
```

- [ ] **Step 5: Make PostgreSQL bulk save merge by question id**

Update `app/services/postgres_session.py`:

```python
    def save_question_evaluations(
        self,
        session_id: str,
        records: list[QuestionEvaluationRecord],
    ) -> None:
        self.get(session_id)
        for record in records:
            self.upsert_question_evaluation(session_id, record)

    def upsert_question_evaluation(
        self,
        session_id: str,
        record: QuestionEvaluationRecord,
    ) -> None:
        self.get(session_id)
        psycopg2, sql = self._import_psycopg2()
        row = question_evaluation_record_to_row(record)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {question_evaluations} (
                            session_id, question_id, answer_state, status,
                            feedback_json, error, created_at
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
```

- [ ] **Step 6: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_question_evaluations.py tests/test_postgres_session_store.py::test_postgres_store_upserts_single_question_evaluation tests/test_postgres_session_store.py::test_postgres_store_bulk_save_merges_existing_question_evaluations tests/test_report_tasks.py::test_execute_report_generation_overwrites_matching_question_evaluations_without_clearing_other_rows -q
```

Expected: PASS, with the Postgres tests allowed to SKIP when `POSTGRES_DSN` is unavailable.

- [ ] **Step 7: Commit**

```bash
git add app/services/question_evaluations.py app/ports/runtime.py app/services/session.py app/services/postgres_session.py tests/test_question_evaluations.py tests/test_postgres_session_store.py tests/test_report_tasks.py
git commit -m "feat: make question evaluation persistence merge-safe"
```

---

### Task 5: Add The Celery Round Review Worker

**Files:**
- Create: `app/services/round_review.py`
- Create: `app/services/round_review_tasks.py`
- Modify: `app/services/runtime.py`
- Modify: `app/services/report_tasks.py`
- Test: `tests/test_round_review.py`

- [ ] **Step 1: Write the failing round-review tests**

Create `tests/test_round_review.py`:

```python
from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
from app.services.round_review import build_single_question_review_state
from app.services.round_review_tasks import run_closed_round_review
from app.services.runtime_domain_events import RoundClosedEvent


def make_plan():
    return InterviewPlan(
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
                prompt="Design the service.",
                focus="system design",
            ),
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
    state["messages"].append(
        {
            "role": "interviewer",
            "content": "Explain Redis.",
            "question_id": "q2",
        }
    )
    state["current_index"] = 1
    return state


def test_build_single_question_review_state_filters_other_questions():
    review_state = build_single_question_review_state(make_state(), "q1")

    assert review_state["status"] == "finished"
    assert review_state["current_index"] == 1
    assert len(review_state["plan"].questions) == 1
    assert review_state["plan"].questions[0].id == "q1"
    assert all(message["question_id"] == "q1" for message in review_state["messages"])


def test_build_single_question_review_state_restores_prompt_as_first_message():
    state = make_state()
    state["messages"] = [
        {
            "role": "candidate",
            "content": "I delete cache after the database update.",
            "question_id": "q1",
        },
        {
            "role": "interviewer",
            "content": "How do you handle race conditions?",
            "question_id": "q1",
        },
        {
            "role": "interviewer",
            "content": "Design the service.",
            "question_id": "q2",
        },
    ]

    review_state = build_single_question_review_state(state, "q1")

    assert review_state["messages"][0] == {
        "role": "interviewer",
        "content": "Explain Redis cache invalidation.",
        "question_id": "q1",
    }
    assert all(message["question_id"] == "q1" for message in review_state["messages"])


def test_run_closed_round_review_saves_one_question_evaluation(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.saved = []

        def get(self, session_id: str):
            assert session_id == "s1"
            return make_state()

        def upsert_question_evaluation(self, session_id: str, record):
            self.saved.append((session_id, record))

    class FakeAgent:
        def __init__(self, *, llm, vector_store):
            self.llm = llm
            self.vector_store = vector_store

        def evaluate(self, state, on_progress=None):
            return InterviewReport(
                session_id="s1",
                overall_score=88,
                overall_dimension_scores=DimensionScores(
                    breadth=88,
                    depth=88,
                    architecture=88,
                    engineering=88,
                    communication=88,
                ),
                summary="ignored",
                highlights=["ignored"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Explain Redis cache invalidation.",
                        user_answer="I delete cache after the database update.",
                        answer_state="answered",
                        score=88,
                        dimension_scores=DimensionScores(
                            breadth=88,
                            depth=88,
                            architecture=88,
                            engineering=88,
                            communication=88,
                        ),
                        rationale="回答说明了缓存删除时机。",
                        critique="还需要补充竞争窗口。",
                        better_answer="补充延迟双删和重试。",
                        references=[],
                    )
                ],
            )

    store = FakeStore()
    monkeypatch.setattr("app.services.round_review_tasks.get_session_store", lambda: store)
    monkeypatch.setattr("app.services.round_review_tasks.get_knowledge_store", lambda: object())
    monkeypatch.setattr("app.services.round_review_tasks.resolve_runtime_llm", lambda store: object())
    monkeypatch.setattr("app.services.round_review_tasks.ShadowReviewerAgent", FakeAgent)

    run_closed_round_review(
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "answered",
            "job_tags": ["python", "redis"],
            "emitted_at": "2026-07-08T00:00:00Z",
        }
    )

    assert len(store.saved) == 1
    assert store.saved[0][0] == "s1"
    assert store.saved[0][1].question_id == "q1"
    assert store.saved[0][1].answer_state == "answered"
    assert store.saved[0][1].feedback.score == 88
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_round_review.py -q
```

Expected: FAIL because `round_review.py` and `round_review_tasks.py` do not exist.

- [ ] **Step 3: Extract shared runtime LLM resolution**

Update `app/services/runtime.py`:

```python
def resolve_runtime_llm(
    store: InterviewSessionStore,
    llm: InterviewLLM | None = None,
) -> InterviewLLM:
    return llm or store.llm or OpenAIInterviewLLM()


def build_report_executor(
    *,
    store: InterviewSessionStore | None = None,
    llm: InterviewLLM | None = None,
    vector_store: PgVectorKnowledgeStore | None = None,
) -> ReportExecutor:
    resolved_store = store or get_session_store()
    resolved_llm = resolve_runtime_llm(resolved_store, llm)
    resolved_vector_store = vector_store or get_knowledge_store()
    return ReportExecutor(
        store=resolved_store,
        llm=resolved_llm,
        vector_store=resolved_vector_store,
    )
```

Update `app/services/report_tasks.py`:

```python
from app.services.runtime import resolve_runtime_llm


def generate_report_for_session(
    session_id: str,
    store: InterviewSessionStore,
) -> None:
    try:
        vector_store = get_knowledge_store()
    except Exception as exc:
        store.fail_report(session_id, str(exc))
        return

    run_report_generation(
        session_id=session_id,
        store=store,
        llm=resolve_runtime_llm(store),
        vector_store=vector_store,
    )
```

Delete the obsolete private `_resolve_llm()` helper from `app/services/report_tasks.py` after switching the call site.

- [ ] **Step 4: Build a single-question review state**

Create `app/services/round_review.py`:

```python
from app.services.prep import InterviewPlan


def build_single_question_review_state(state: dict, question_id: str) -> dict:
    question = next(
        question
        for question in state["plan"].questions
        if question.id == question_id
    )
    messages = [
        message
        for message in state["messages"]
        if message["question_id"] == question_id
    ]
    prompt_message = {
        "role": "interviewer",
        "content": question.prompt,
        "question_id": question_id,
    }
    if not messages or messages[0] != prompt_message:
        messages = [prompt_message] + [
            message
            for message in messages
            if message != prompt_message
        ]
    return {
        "session_id": state["session_id"],
        "plan": InterviewPlan(title=state["plan"].title, questions=[question]),
        "current_index": 1,
        "messages": messages,
        "decision": {"action": "finish", "follow_up": None, "reason": "round_closed"},
        "pending_output": None,
        "status": "finished",
        "job_description": state["job_description"],
        "resume_text": state["resume_text"],
        "job_tags": list(state["job_tags"]),
        "skipped_question_ids": [
            question_id
            for question_id in state.get("skipped_question_ids", [])
            if question_id == question.id
        ],
        "started_at": state["started_at"],
        "finished_at": state.get("finished_at") or state["started_at"],
    }
```

- [ ] **Step 5: Add the Celery round-review task**

Create `app/services/round_review_tasks.py`:

```python
from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.celery_app import celery_app
from app.services.question_evaluations import question_evaluation_from_feedback
from app.services.round_review import build_single_question_review_state
from app.services.runtime import get_session_store, resolve_runtime_llm
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.vector_store import get_knowledge_store


@celery_app.task(name="app.services.round_review_tasks.run_closed_round_review")
def run_closed_round_review(payload: dict) -> None:
    event = RoundClosedEvent.model_validate(payload)
    store = get_session_store()
    state = store.get(event.session_id)
    review_state = build_single_question_review_state(state, event.question_id)
    report = ShadowReviewerAgent(
        llm=resolve_runtime_llm(store),
        vector_store=get_knowledge_store(),
    ).evaluate(review_state)
    feedback = report.feedbacks[0]
    store.upsert_question_evaluation(
        event.session_id,
        question_evaluation_from_feedback(
            session_id=event.session_id,
            feedback=feedback,
            answer_state=event.answer_state,
        ),
    )
```

- [ ] **Step 6: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_round_review.py tests/test_event_publisher.py tests/test_report_tasks.py::test_execute_report_generation_saves_question_evaluations -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/runtime.py app/services/report_tasks.py app/services/round_review.py app/services/round_review_tasks.py tests/test_round_review.py
git commit -m "feat: add celery round review worker"
```

---

### Task 6: Document Stage 26A And Verify The New Runtime Path

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Test: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write the failing documentation regression**

Append to `tests/test_local_v1_docs.py`:

```python
def test_docs_describe_stage_26a_event_backend_position():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 26A adds an opt-in Redis/Celery round-review event backend"
    assert expected in readme
    assert expected in runbook
    assert "interim round-review rows are merged by question id" in readme
    assert "Local V1 UI remains final-report-first" in readme
    assert "celery -A app.services.celery_app.celery_app worker --loglevel=info" in runbook
```

- [ ] **Step 2: Run the focused docs test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_26a_event_backend_position -q
```

Expected: FAIL because Stage 26A is not documented yet.

- [ ] **Step 3: Update the README**

Add this paragraph under `## Current Architecture Position` in `README.md` after the Stage 25 paragraph:

```markdown
Stage 26A adds an opt-in Redis/Celery round-review event backend. Closed interview rounds can now be published as `round_closed` events and reviewed asynchronously during the interview. Interim round-review rows are merged by question id instead of session-wide replace, the Postgres final-report worker remains the authoritative completed report path, and the Local V1 UI remains final-report-first.
```

- [ ] **Step 4: Update the runbook**

Add this paragraph under `## 1.1 Architecture Position` in `docs/local-v1-runbook.md`:

```markdown
Stage 26A adds an opt-in Redis/Celery round-review event backend. It does not replace the Postgres final-report worker, does not add WebSocket, does not migrate the interview graph to LangGraph yet, and does not add a live in-interview question-evaluation panel in Local V1.
```

Add this command block under the worker startup section:

```markdown
Optional Stage 26A round-review worker:

```powershell
$env:INTERVIEW_EVENT_BACKEND="celery"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
celery -A app.services.celery_app.celery_app worker --loglevel=info
```
```

- [ ] **Step 5: Run the full docs suite and verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS, including the existing Stage 23 and Stage 25 architecture wording assertions.

- [ ] **Step 6: Run the Stage 26A focused verification sweep**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py tests/test_runtime_boundary_api.py tests/test_event_publisher.py tests/test_interview_rounds.py tests/test_api.py tests/test_question_evaluations.py tests/test_postgres_session_store.py tests/test_round_review.py tests/test_report_tasks.py tests/test_local_v1_docs.py -q
```

Expected: PASS, with Postgres integration tests allowed to skip when their fixture prerequisites are not available.

- [ ] **Step 7: Run the full repository verification sweep**

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

Expected:

- `pytest -q` remains green.
- All Node syntax checks exit `0`.
- Existing report-processing and report-detail pages still load because Stage 26A did not change their contract.

- [ ] **Step 8: Commit**

```bash
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: describe stage 26a event backend"
```

---

## Verification Sweep

After all six tasks are complete, run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py tests/test_runtime_boundary_api.py tests/test_event_publisher.py tests/test_interview_rounds.py tests/test_api.py tests/test_question_evaluations.py tests/test_postgres_session_store.py tests/test_round_review.py tests/test_report_tasks.py tests/test_report_api.py tests/test_local_v1_docs.py -q
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected:

- Stage 26A focused tests pass.
- Existing report generation, report quality, and report detail tests remain green.
- Existing doc wording checks for Stage 23 and Stage 25 remain green.
- The four-page runtime JS syntax stays valid.

## Self-Review

- Spec coverage: This plan now covers the actual next gap between the architecture document and the current codebase: a Redis/Celery event path, typed round-close events, asynchronous per-question review, merge-safe persistence, and docs/runtime boundary updates. It intentionally does not include WebSocket or LangGraph migration.
- Placeholder scan: There are no `TODO`, `TBD`, or “same as previous task” placeholders. Every task names exact files, tests, commands, and concrete code.
- Type consistency: The event payload is consistently named `RoundClosedEvent`, the config key is consistently `INTERVIEW_EVENT_BACKEND`, the repository method added for incremental review persistence is consistently `upsert_question_evaluation`, and the event `answer_state` is treated as authoritative on the round-review path.
