# Stage 33 Round Review Microbatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the document/code gap for Shadow Reviewer microbatch scoring by turning each `round_closed` event into a persisted per-question evaluation in the default Local V1 runtime, while keeping the interview response path non-blocking.

**Architecture:** Keep the current HTTP + SSE Local V1 boundary. Do not add WebSocket, Redis checkpoints, or a new queue system in this stage. Add a reusable round-review runner shared by Celery and local execution. Change the default event backend from `noop` to a local asynchronous publisher that schedules `round_closed` reviews on a small thread pool. Keep `noop` as an explicit opt-out backend and keep `celery` as the external worker backend.

**Tech Stack:** Python 3.11, FastAPI, existing LangGraph interview state, existing `ShadowReviewerAgent`, existing `QuestionEvaluationRecord`, in-memory/PostgreSQL session stores, pytest.

---

## Execution Notes

- The worktree currently contains pre-existing dirty files from earlier stages. Before each commit, inspect `git diff -- <files>` and stage only the files listed in that task.
- Avoid modifying `tests/test_api.py` unless a route-level regression forces it. The selected publisher design preserves the existing `publish(event)` API call and should not require API route signature changes.
- Keep `INTERVIEW_EVENT_BACKEND=noop` supported for tests and demos that intentionally disable runtime events.
- This stage does not make final reports consume per-question evaluation records. That should be a later stage after microbatch records are reliably produced.
- Existing `tests/test_round_review.py` Celery task tests currently monkeypatch `app.services.round_review_tasks.*`. After extracting the runner, those monkeypatches must move to `app.services.round_review_runner.*`; otherwise the tests will call the real session store and fail on missing session `s1`.
- Local thread-pool round review is best-effort, not a durable queue. Add an explicit `shutdown()` boundary and wire FastAPI shutdown to drain the cached publisher, but use `INTERVIEW_EVENT_BACKEND=celery` for durable worker semantics across hard process termination.
- The runner should select feedback by matching `event.question_id` before falling back to `report.feedbacks[0]`. This intentionally improves the old Celery task behavior while keeping the one-question review path behaviorally equivalent when `build_single_question_review_state()` returns a single feedback.

---

## File Structure

- Create: `app/services/round_review_runner.py`
  - Reusable runner for one `RoundClosedEvent`.
  - Saves `completed` records when review succeeds.
  - Saves `failed` records when reviewer evaluation fails after the session can be loaded.

- Modify: `app/services/round_review_tasks.py`
  - Make the Celery task delegate to the reusable runner.

- Modify: `app/services/event_publisher.py`
  - Add `LocalRoundReviewEventPublisher`.
  - Add publisher `shutdown()` methods so cached thread-pool work can be drained on graceful shutdown.
  - Keep `NoopRuntimeEventPublisher`.
  - Keep `CeleryRuntimeEventPublisher`.

- Modify: `app/services/config.py`
  - Change `DEFAULT_RUNTIME_EVENT_BACKEND` from `"noop"` to `"local"`.

- Modify: `app/services/runtime.py`
  - Build `LocalRoundReviewEventPublisher` for `INTERVIEW_EVENT_BACKEND=local`.
  - Add runtime shutdown/reset handling for cached publishers that expose `shutdown()`.
  - Keep `noop` and `celery`.

- Modify: `app/api/routes.py`
  - Update `/api/runtime` capability reporting for `local`, `noop`, and `celery`.

- Modify: `app/main.py`
  - Drain cached runtime publishers during FastAPI shutdown.

- Modify: `tests/test_round_review.py`
  - Add runner success/failure coverage.
  - Update existing Celery task monkeypatch targets from `round_review_tasks` to `round_review_runner`.
  - Update Celery task test to prove it delegates to the runner.

- Modify: `tests/test_event_publisher.py`
  - Cover local publisher scheduling.
  - Cover local publisher unsupported event rejection.
  - Keep noop/celery coverage.

- Modify: `tests/test_runtime_provider.py`
  - Update default event backend expectations to `local`.
  - Cover explicit `noop`.

- Modify: `tests/test_runtime_boundary_api.py`
  - Update default `/api/runtime` event backend to `local`.
  - Keep celery mode coverage.
  - Add explicit noop mode coverage.

- Modify: `README.md`
  - Document Stage 33 local microbatch behavior.

- Modify: `docs/local-v1-runbook.md`
  - Add local verification notes for per-question evaluations.

- Modify: `tests/test_local_v1_docs.py`
  - Add docs coverage for Stage 33.

---

### Task 1: Extract A Reusable Round Review Runner

**Files:**
- Create: `app/services/round_review_runner.py`
- Modify: `app/services/round_review_tasks.py`
- Modify: `tests/test_round_review.py`

- [ ] **Step 1: Write failing runner tests**

Append these imports to `tests/test_round_review.py`:

```python
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.round_review_runner import run_round_review_event
from app.services.runtime_domain_events import RoundClosedEvent
```

Append these tests near the existing `run_closed_round_review` tests:

```python
def make_round_closed_event(answer_state: str = "answered") -> RoundClosedEvent:
    return RoundClosedEvent(
        session_id="s1",
        question_id="q1",
        answer_state=answer_state,
        job_tags=["python", "redis"],
    )


def test_run_round_review_event_saves_completed_question_evaluation(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.llm = object()
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
                overall_score=90,
                overall_dimension_scores=make_dimension_scores(90),
                summary="ignored",
                highlights=["ignored"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Explain Redis cache invalidation.",
                        user_answer="I delete cache after the database update.",
                        answer_state="answered",
                        score=90,
                        dimension_scores=make_dimension_scores(90),
                        rationale="Good invalidation sequence.",
                        critique="Needs race-condition handling.",
                        better_answer="Mention delayed double delete and retry.",
                        references=[],
                    )
                ],
            )

    store = FakeStore()

    record = run_round_review_event(
        make_round_closed_event(),
        store=store,
        llm=store.llm,
        vector_store=object(),
        reviewer_factory=FakeAgent,
    )

    assert record.status == "completed"
    assert record.question_id == "q1"
    assert record.feedback.score == 90
    assert store.saved == [("s1", record)]


def test_run_round_review_event_saves_failed_record_when_reviewer_raises():
    class FakeStore:
        def __init__(self):
            self.llm = object()
            self.saved = []

        def get(self, session_id: str):
            return make_state()

        def upsert_question_evaluation(self, session_id: str, record):
            self.saved.append((session_id, record))

    class FailingAgent:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            raise RuntimeError("review model unavailable")

    store = FakeStore()

    record = run_round_review_event(
        make_round_closed_event(answer_state="skipped"),
        store=store,
        llm=store.llm,
        vector_store=object(),
        reviewer_factory=FailingAgent,
    )

    assert record == store.saved[0][1]
    assert record.status == "failed"
    assert record.session_id == "s1"
    assert record.question_id == "q1"
    assert record.answer_state == "skipped"
    assert record.feedback is None
    assert "review model unavailable" in record.error


def test_run_round_review_event_selects_matching_feedback_when_report_has_extra_feedback():
    class FakeStore:
        def __init__(self):
            self.llm = object()
            self.saved = []

        def get(self, session_id: str):
            return make_state()

        def upsert_question_evaluation(self, session_id: str, record):
            self.saved.append((session_id, record))

    class FakeAgent:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            return InterviewReport(
                session_id="s1",
                overall_score=82,
                overall_dimension_scores=make_dimension_scores(82),
                summary="ignored",
                highlights=["ignored"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q2",
                        question_text="Design the service.",
                        user_answer="Wrong feedback first.",
                        answer_state="answered",
                        score=10,
                        dimension_scores=make_dimension_scores(10),
                        rationale="Wrong question.",
                        critique="Wrong question.",
                        better_answer="Wrong question.",
                        references=[],
                    ),
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Explain Redis cache invalidation.",
                        user_answer="I delete cache after the database update.",
                        answer_state="answered",
                        score=82,
                        dimension_scores=make_dimension_scores(82),
                        rationale="Matched the closed round.",
                        critique="Needs race-condition detail.",
                        better_answer="Add delayed double delete and retry details.",
                        references=[],
                    ),
                ],
            )

    store = FakeStore()

    record = run_round_review_event(
        make_round_closed_event(),
        store=store,
        llm=store.llm,
        vector_store=object(),
        reviewer_factory=FakeAgent,
    )

    assert record.question_id == "q1"
    assert record.feedback.score == 82
    assert record.feedback.rationale == "Matched the closed round."
```

Add one delegation test for the Celery task:

```python
def test_run_closed_round_review_delegates_to_runner(monkeypatch):
    calls = []

    def fake_runner(payload):
        calls.append(payload)
        return QuestionEvaluationRecord(
            session_id=payload["session_id"],
            question_id=payload["question_id"],
            answer_state=payload["answer_state"],
            status="failed",
            error="delegated",
        )

    monkeypatch.setattr(
        "app.services.round_review_tasks.run_round_review_event_payload",
        fake_runner,
    )

    run_closed_round_review(
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "answered",
            "job_tags": ["python", "redis"],
            "emitted_at": "2026-07-09T00:00:00Z",
        }
    )

    assert calls == [
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "answered",
            "job_tags": ["python", "redis"],
            "emitted_at": "2026-07-09T00:00:00Z",
        }
    ]
```

- [ ] **Step 2: Update existing Celery task tests to patch the runner module**

In the existing `test_run_closed_round_review_saves_one_question_evaluation()` and `test_run_closed_round_review_uses_event_answer_state()` tests, replace the old monkeypatch targets:

```python
monkeypatch.setattr(
    "app.services.round_review_tasks.get_session_store",
    lambda: store,
)
monkeypatch.setattr(
    "app.services.round_review_tasks.get_knowledge_store",
    lambda: object(),
)
monkeypatch.setattr(
    "app.services.round_review_tasks.resolve_runtime_llm",
    lambda store: store.llm,
)
monkeypatch.setattr("app.services.round_review_tasks.ShadowReviewerAgent", FakeAgent)
```

with:

```python
monkeypatch.setattr(
    "app.services.round_review_runner.get_session_store",
    lambda: store,
)
monkeypatch.setattr(
    "app.services.round_review_runner.get_knowledge_store",
    lambda: object(),
)
monkeypatch.setattr(
    "app.services.round_review_runner.resolve_runtime_llm",
    lambda store: store.llm,
)
monkeypatch.setattr("app.services.round_review_runner.ShadowReviewerAgent", FakeAgent)
```

This migration is required because `round_review_tasks.py` becomes a thin Celery wrapper and no longer owns the runtime dependencies being patched.

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_round_review.py::test_run_round_review_event_saves_completed_question_evaluation tests/test_round_review.py::test_run_round_review_event_saves_failed_record_when_reviewer_raises tests/test_round_review.py::test_run_round_review_event_selects_matching_feedback_when_report_has_extra_feedback tests/test_round_review.py::test_run_closed_round_review_delegates_to_runner -q
```

Expected: FAIL because `app.services.round_review_runner` does not exist and the Celery task has not been delegated.

- [ ] **Step 4: Add runner implementation**

Create `app/services/round_review_runner.py`:

```python
from collections.abc import Callable
from typing import Literal

from app.agents.shadow_reviewer import ShadowReviewerAgent
from app.services.question_evaluations import (
    QuestionEvaluationRecord,
    question_evaluation_from_feedback,
)
from app.services.round_review import build_single_question_review_state
from app.services.runtime import get_session_store, resolve_runtime_llm
from app.services.runtime_domain_events import RoundClosedEvent
from app.services.vector_store import get_knowledge_store


def run_round_review_event_payload(payload: dict) -> QuestionEvaluationRecord:
    event = RoundClosedEvent.model_validate(payload)
    store = get_session_store()
    return run_round_review_event(
        event,
        store=store,
        llm=resolve_runtime_llm(store),
        vector_store=get_knowledge_store(),
    )


def run_round_review_event(
    event: RoundClosedEvent,
    *,
    store,
    llm,
    vector_store,
    reviewer_factory: Callable | None = None,
) -> QuestionEvaluationRecord:
    state = store.get(event.session_id)
    try:
        review_state = build_single_question_review_state(state, event.question_id)
        reviewer = (reviewer_factory or ShadowReviewerAgent)(
            llm=llm,
            vector_store=vector_store,
        )
        report = reviewer.evaluate(review_state)
        feedback = _select_feedback(report.feedbacks, event.question_id)
        record = question_evaluation_from_feedback(
            session_id=event.session_id,
            feedback=feedback,
            answer_state=event.answer_state,
        )
    except Exception as exc:
        record = _failed_question_evaluation(
            session_id=event.session_id,
            question_id=event.question_id,
            answer_state=event.answer_state,
            error=str(exc),
        )

    store.upsert_question_evaluation(event.session_id, record)
    return record


def _select_feedback(feedbacks, question_id: str):
    for feedback in feedbacks:
        if feedback.question_id == question_id:
            return feedback
    if not feedbacks:
        raise ValueError("round review returned no feedback")
    return feedbacks[0]


def _failed_question_evaluation(
    *,
    session_id: str,
    question_id: str,
    answer_state: Literal["answered", "skipped", "unanswered"],
    error: str,
) -> QuestionEvaluationRecord:
    return QuestionEvaluationRecord(
        session_id=session_id,
        question_id=question_id,
        answer_state=answer_state,
        status="failed",
        error=error or "round review failed",
    )
```

- [ ] **Step 5: Delegate the Celery task**

Replace the body and imports in `app/services/round_review_tasks.py` with:

```python
from app.services.celery_app import celery_app
from app.services.round_review_runner import run_round_review_event_payload


@celery_app.task(name="app.services.round_review_tasks.run_closed_round_review")
def run_closed_round_review(payload: dict) -> None:
    run_round_review_event_payload(payload)
```

- [ ] **Step 6: Run round review tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_round_review.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/round_review_runner.py app/services/round_review_tasks.py tests/test_round_review.py
git commit -m "feat: extract round review runner"
```

---

### Task 2: Add The Local Async Event Publisher

**Files:**
- Modify: `app/services/event_publisher.py`
- Modify: `tests/test_event_publisher.py`

- [ ] **Step 1: Write failing publisher tests**

Update the import in `tests/test_event_publisher.py`:

```python
from app.services.event_publisher import (
    CeleryRuntimeEventPublisher,
    LocalRoundReviewEventPublisher,
    NoopRuntimeEventPublisher,
)
```

Append:

```python
class FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, payload):
        self.calls.append((fn, payload))
        return object()

    def shutdown(self, *, wait=True):
        self.shutdown_wait = wait


def test_local_round_review_event_publisher_schedules_round_closed_event():
    executor = FakeExecutor()
    publisher = LocalRoundReviewEventPublisher(executor=executor)

    publisher.publish(
        RoundClosedEvent(
            session_id="s1",
            question_id="q1",
            answer_state="answered",
            job_tags=["python", "redis"],
        )
    )

    assert len(executor.calls) == 1
    fn, payload = executor.calls[0]
    assert fn.__name__ == "run_round_review_event_payload"
    assert payload["event_type"] == "round_closed"
    assert payload["session_id"] == "s1"
    assert payload["question_id"] == "q1"
    assert payload["answer_state"] == "answered"


def test_local_round_review_event_publisher_rejects_unknown_event_type():
    publisher = LocalRoundReviewEventPublisher(executor=FakeExecutor())

    with pytest.raises(ValueError, match="unsupported runtime event"):
        publisher.publish({"event_type": "unknown"})


def test_local_round_review_event_publisher_shutdown_drains_executor():
    executor = FakeExecutor()
    publisher = LocalRoundReviewEventPublisher(executor=executor)

    publisher.shutdown(wait=False)

    assert executor.shutdown_wait is False
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_event_publisher.py::test_local_round_review_event_publisher_schedules_round_closed_event tests/test_event_publisher.py::test_local_round_review_event_publisher_rejects_unknown_event_type tests/test_event_publisher.py::test_local_round_review_event_publisher_shutdown_drains_executor -q
```

Expected: FAIL because `LocalRoundReviewEventPublisher` does not exist.

- [ ] **Step 3: Implement the local publisher**

Modify `app/services/event_publisher.py`:

```python
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.services.round_review_runner import run_round_review_event_payload
from app.services.runtime_domain_events import RoundClosedEvent


class NoopRuntimeEventPublisher:
    """Publisher boundary for intentionally disabled runtime events."""

    def publish(self, event: Any) -> None:
        return None

    def shutdown(self, *, wait: bool = True) -> None:
        return None


class LocalRoundReviewEventPublisher:
    """Local V1 async publisher for round review microbatches."""

    def __init__(self, *, executor=None) -> None:
        self._executor = executor or ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="round-review",
        )

    def publish(self, event: Any) -> None:
        if isinstance(event, RoundClosedEvent):
            self._executor.submit(
                run_round_review_event_payload,
                event.model_dump(mode="json"),
            )
            return None
        raise ValueError(f"unsupported runtime event: {type(event).__name__}")

    def shutdown(self, *, wait: bool = True) -> None:
        shutdown = getattr(self._executor, "shutdown", None)
        if shutdown is not None:
            shutdown(wait=wait)


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

    def shutdown(self, *, wait: bool = True) -> None:
        return None
```

- [ ] **Step 4: Run publisher tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_event_publisher.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/event_publisher.py tests/test_event_publisher.py
git commit -m "feat: add local round review publisher"
```

---

### Task 3: Make Local Microbatch The Default Runtime Event Backend

**Files:**
- Modify: `app/services/config.py`
- Modify: `app/services/runtime.py`
- Modify: `app/main.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_runtime_provider.py`
- Modify: `tests/test_runtime_boundary_api.py`

- [ ] **Step 1: Write failing runtime tests**

In `tests/test_runtime_provider.py`, update `test_config_exposes_event_backend_and_redis_defaults()` expected values:

```python
assert DEFAULT_RUNTIME_EVENT_BACKEND == "local"
assert get_runtime_event_backend() == "local"
```

In the import list at the top of `tests/test_runtime_provider.py`, add `shutdown_runtime`:

```python
from app.services.runtime import (
    DEFAULT_POSTGRES_DSN,
    build_report_executor,
    build_event_publisher,
    build_report_job_store,
    build_session_store,
    get_draft_store,
    get_event_publisher,
    get_report_executor,
    get_report_job_store,
    reset_runtime_for_tests,
    shutdown_runtime,
)
```

Rename `test_build_event_publisher_defaults_to_noop` to `test_build_event_publisher_defaults_to_local_round_review` and update it:

```python
def test_build_event_publisher_defaults_to_local_round_review(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)

    from app.services.event_publisher import LocalRoundReviewEventPublisher

    publisher = build_event_publisher()

    assert isinstance(publisher, LocalRoundReviewEventPublisher)
```

Add explicit noop coverage:

```python
def test_build_event_publisher_supports_explicit_noop(monkeypatch):
    monkeypatch.setenv("INTERVIEW_EVENT_BACKEND", "noop")

    from app.services.event_publisher import NoopRuntimeEventPublisher

    publisher = build_event_publisher()

    assert isinstance(publisher, NoopRuntimeEventPublisher)
```

Add runtime shutdown coverage:

```python
def test_shutdown_runtime_drains_cached_event_publisher(monkeypatch):
    closed = []

    class FakePublisher:
        def shutdown(self, *, wait=True):
            closed.append(wait)

    reset_runtime_for_tests()
    monkeypatch.setattr("app.services.runtime.build_event_publisher", lambda: FakePublisher())

    get_event_publisher()
    shutdown_runtime(wait=True)

    assert closed == [True]


def test_reset_runtime_for_tests_shuts_down_cached_event_publisher(monkeypatch):
    closed = []

    class FakePublisher:
        def shutdown(self, *, wait=True):
            closed.append(wait)

    reset_runtime_for_tests()
    monkeypatch.setattr("app.services.runtime.build_event_publisher", lambda: FakePublisher())

    get_event_publisher()
    reset_runtime_for_tests()

    assert closed == [False]
```

In `tests/test_runtime_boundary_api.py`, update the default test:

```python
assert body["event_backend"] == "local"
assert body["capabilities"] == {
    "redis": False,
    "celery": False,
    "websocket": False,
    "langgraph": True,
}
```

Add explicit noop endpoint coverage:

```python
def test_runtime_boundary_endpoint_reports_noop_event_mode(monkeypatch):
    monkeypatch.setenv("INTERVIEW_EVENT_BACKEND", "noop")
    client = TestClient(app)

    response = client.get("/api/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["event_backend"] == "noop"
    assert body["capabilities"] == {
        "redis": False,
        "celery": False,
        "websocket": False,
        "langgraph": True,
    }
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py::test_config_exposes_event_backend_and_redis_defaults tests/test_runtime_provider.py::test_build_event_publisher_defaults_to_local_round_review tests/test_runtime_provider.py::test_build_event_publisher_supports_explicit_noop tests/test_runtime_provider.py::test_shutdown_runtime_drains_cached_event_publisher tests/test_runtime_provider.py::test_reset_runtime_for_tests_shuts_down_cached_event_publisher tests/test_runtime_boundary_api.py -q
```

Expected: FAIL because the default backend is still `noop` and runtime does not build the local publisher.

- [ ] **Step 3: Update config default**

In `app/services/config.py`, change:

```python
DEFAULT_RUNTIME_EVENT_BACKEND = "noop"
```

to:

```python
DEFAULT_RUNTIME_EVENT_BACKEND = "local"
```

- [ ] **Step 4: Update runtime provider**

In `app/services/runtime.py`, replace `build_event_publisher()` with:

```python
def build_event_publisher():
    from app.services.event_publisher import (
        LocalRoundReviewEventPublisher,
        NoopRuntimeEventPublisher,
    )

    backend = get_runtime_event_backend()
    if backend == "local":
        return LocalRoundReviewEventPublisher()
    if backend == "noop":
        return NoopRuntimeEventPublisher()
    if backend == "celery":
        try:
            from app.services.celery_app import celery_app
            from app.services.event_publisher import CeleryRuntimeEventPublisher
        except ImportError as exc:
            raise RuntimeError(
                "INTERVIEW_EVENT_BACKEND=celery requires runtime event components"
            ) from exc
        return CeleryRuntimeEventPublisher(celery_app=celery_app)
    raise RuntimeError(f"unsupported INTERVIEW_EVENT_BACKEND: {backend}")
```

In the same file, replace `reset_runtime_for_tests()` with `shutdown_runtime()` plus reset behavior:

```python
def shutdown_runtime(*, wait: bool = True) -> None:
    global _session_store, _report_job_store, _report_executor, _draft_store, _event_publisher
    _shutdown_cached_publisher(_event_publisher, wait=wait)
    _session_store = None
    _report_job_store = None
    _report_executor = None
    _draft_store = None
    _event_publisher = None


def reset_runtime_for_tests() -> None:
    shutdown_runtime(wait=False)


def _shutdown_cached_publisher(publisher, *, wait: bool) -> None:
    if publisher is None:
        return
    shutdown = getattr(publisher, "shutdown", None)
    if shutdown is not None:
        shutdown(wait=wait)
```

- [ ] **Step 5: Drain local publisher during FastAPI shutdown**

In `app/main.py`, add these imports near the existing app imports:

```python
from contextlib import asynccontextmanager
from app.services.runtime import shutdown_runtime
```

Then replace:

```python
app = FastAPI(title="Interview Agent MVP")
```

with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        shutdown_runtime()


app = FastAPI(title="Interview Agent MVP", lifespan=lifespan)
```

- [ ] **Step 6: Update runtime boundary capabilities**

In `app/api/routes.py`, keep the current response shape and compute capabilities from the backend:

```python
        "capabilities": {
            "redis": event_backend == "celery",
            "celery": event_backend == "celery",
            "websocket": False,
            "langgraph": True,
        },
```

No behavior change is needed for `local` versus `noop` capabilities; both remain Redis/Celery/WebSocket false.

- [ ] **Step 7: Run runtime tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py tests/test_runtime_boundary_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/services/config.py app/services/runtime.py app/main.py app/api/routes.py tests/test_runtime_provider.py tests/test_runtime_boundary_api.py
git commit -m "feat: default to local round review events"
```

---

### Task 4: Verify API-Level Evaluation Retrieval Still Uses Existing Contract

**Files:**
- Modify only if needed: `tests/test_api.py`

- [ ] **Step 1: Inspect existing API tests**

Run:

```powershell
rg -n "question-evaluations|round_closed|FakePublisher|get_event_publisher" tests/test_api.py
```

Expected: Existing tests cover event emission and the `GET /api/interviews/{session_id}/question-evaluations` endpoint.

- [ ] **Step 2: Run focused API tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_answer_route_publishes_round_closed_event_only_when_question_closes tests/test_api.py::test_skip_route_publishes_round_closed_event tests/test_api.py::test_answer_route_succeeds_when_round_closed_publish_fails tests/test_api.py::test_answer_stream_returns_done_when_round_closed_publish_fails tests/test_api.py::test_answer_stream_publishes_round_closed_event_when_streamed_answer_closes_question -q
```

Expected: PASS. If test names have drifted, use the names found in Step 1.

- [ ] **Step 3: Only if focused API tests fail**

If any fake publisher test fails because the event publisher contract changed, stop and re-check Task 2. The intended design keeps `publish(event)` unchanged, so API test changes should not be necessary.

- [ ] **Step 4: No commit expected**

Expected: No changes from this task. If a real API contract fix is required, stage only that exact hunk and commit:

```bash
git add tests/test_api.py
git commit -m "test: preserve round review api event contract"
```

---

### Task 5: Document Stage 33

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write failing docs test**

Append to `tests/test_local_v1_docs.py`:

```python
def test_docs_describe_stage_33_round_review_microbatch():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 33 turns round_closed events into local asynchronous round review microbatches"
    assert expected in readme
    assert expected in runbook
    assert "LocalRoundReviewEventPublisher" in readme
    assert "QuestionEvaluationRecord" in readme
    assert "INTERVIEW_EVENT_BACKEND=noop" in runbook
    assert "does not add WebSocket or Redis checkpoints" in readme
```

- [ ] **Step 2: Run docs test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_33_round_review_microbatch -q
```

Expected: FAIL because Stage 33 docs do not exist yet.

- [ ] **Step 3: Update README**

Add this paragraph under the current architecture position section:

```markdown
Stage 33 turns round_closed events into local asynchronous round review microbatches. The default `INTERVIEW_EVENT_BACKEND=local` uses `LocalRoundReviewEventPublisher` to schedule each closed question for Shadow Reviewer evaluation outside the direct answer response path, then persists a `QuestionEvaluationRecord` through the existing session store. `INTERVIEW_EVENT_BACKEND=noop` remains available for disabling runtime events, and `INTERVIEW_EVENT_BACKEND=celery` remains the external worker path. This stage does not add WebSocket or Redis checkpoints.
```

- [ ] **Step 4: Update runbook**

Add this paragraph under the runtime architecture section:

```markdown
Stage 33 turns round_closed events into local asynchronous round review microbatches. In the default local mode, a closed question should eventually appear from `GET /api/interviews/{session_id}/question-evaluations` as a `QuestionEvaluationRecord`. Use `INTERVIEW_EVENT_BACKEND=noop` only when runtime event side effects should be disabled, and use `INTERVIEW_EVENT_BACKEND=celery` when validating the external worker path.
```

Add this verification checklist near the local acceptance checklist:

```markdown
Stage 33 round review checks:

1. Start an interview with the default `INTERVIEW_EVENT_BACKEND=local`.
2. Answer or skip enough turns to close one question.
3. Poll `GET /api/interviews/{session_id}/question-evaluations`.
4. Confirm the closed question eventually has one `QuestionEvaluationRecord`.
5. Confirm failed Shadow Reviewer execution is recorded as `status="failed"` instead of breaking the answer response.
```

- [ ] **Step 5: Run docs test**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_33_round_review_microbatch -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: describe stage 33 round review microbatch"
```

---

### Task 6: Verification Sweep

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused Stage 33 tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_round_review.py tests/test_event_publisher.py tests/test_runtime_provider.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run API event smoke tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_answer_route_publishes_round_closed_event_only_when_question_closes tests/test_api.py::test_skip_route_publishes_round_closed_event tests/test_api.py::test_answer_route_succeeds_when_round_closed_publish_fails tests/test_api.py::test_answer_stream_returns_done_when_round_closed_publish_fails tests/test_api.py::test_answer_stream_publishes_round_closed_event_when_streamed_answer_closes_question -q
```

Expected: PASS. If test names have drifted, run the focused names from `rg -n "round_closed" tests/test_api.py`.

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

Expected:

- Pytest remains green, with PostgreSQL-specific tests allowed to skip when fixture prerequisites are unavailable.
- Static JavaScript syntax remains valid.

- [ ] **Step 4: Inspect changed files and recent commits**

Run:

```bash
git status --short
git log --oneline -8
```

Expected:

- Latest commits include the Stage 33 runner, publisher, runtime default, and docs commits.
- Any remaining dirty files are pre-existing unrelated worktree changes or explicitly identified Stage 33 files that still need attention.

---

## Verification Sweep

After all tasks are complete, run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_round_review.py tests/test_event_publisher.py tests/test_runtime_provider.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py -q
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected:

- `run_round_review_event()` writes a completed `QuestionEvaluationRecord` on success.
- `run_round_review_event()` writes a failed `QuestionEvaluationRecord` when reviewer evaluation fails after the session is loaded.
- Celery task delegates to the shared runner.
- Local default event backend schedules round review work asynchronously.
- Explicit `noop` still disables runtime event side effects.
- Explicit `celery` still sends `run_closed_round_review` tasks.
- `/api/runtime` reports `event_backend="local"` by default and keeps Redis/Celery/WebSocket capabilities false.
- Stage 33 docs describe the runtime behavior and verification path.

## Self-Review

- Spec coverage: The plan implements the architecture document's Shadow Reviewer microbatch loop for Local V1 without adding WebSocket, Redis, or a new queue system.
- Risk control: The plan keeps API route call sites stable, preserves explicit noop mode, and shares one runner between local and Celery execution.
- Test coverage: Unit tests cover success, failure, local scheduling, Celery delegation, runtime defaults, API runtime reporting, and documentation.
- Placeholder scan: No TBD/TODO/fill-in-later placeholders remain; each task includes exact files, commands, and expected results.
- Handoff: Execute with `superpowers:executing-plans`, commit after each task, and stage only the files listed in that task.
