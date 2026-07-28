# Stage 10 Interface Completion And UI Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the core runtime interfaces from `docs/interface-requirements.md` so the web UI can recover interview state, skip questions, show useful report progress, and render backend report fields with user-facing labels.

**Architecture:** Keep API routes thin and push reusable state derivation into `InterviewSessionStore`, with `PostgresInterviewSessionStore` persisting through the same `_replace_state(...)` path used by answer submission. Add focused response builders for session snapshots and report progress in `app/api/routes.py` first; split later only if the file grows further. Frontend changes stay inside the existing no-build static app and consume the new endpoints without introducing a framework.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest, current in-memory and PostgreSQL session stores, static HTML/CSS/JavaScript.

---

## Scope

Included in Stage 10:

- `GET /api/interviews/{session_id}` session detail endpoint.
- `POST /api/interviews/{session_id}/skip` skip current question endpoint.
- `GET /api/interviews/{session_id}/report/progress` report progress detail endpoint.
- `job_tags` and interview progress data exposed through session detail.
- Chinese report dimension labels in `app/static/app.js`.
- Running page integration for session refresh, skip, and progress endpoint.
- Tests for in-memory behavior and route contracts.

Excluded from Stage 10:

- `POST /api/interview-drafts`.
- `GET /api/interviews/{session_id}/report.pdf`.
- `GET /api/reports`.
- User authentication and per-user authorization.
- Replacing the four static prototype HTML files with a routed frontend.

These excluded items should become Stage 11+ because they are independent product surfaces and do not block the core interview runtime.

---

## File Structure

- Modify: `app/services/session.py`
  - Add a session snapshot builder.
  - Add skip behavior for the in-memory store.
  - Keep active/finished state transitions centralized.

- Modify: `app/services/postgres_session.py`
  - Persist skip behavior through `_replace_state(...)`.
  - Reuse the session snapshot builder inherited from `InterviewSessionStore`.

- Modify: `app/services/report_jobs.py`
  - Reuse existing `get_job_by_session(...)` for report progress `report_job_id`.
  - Do not add `job_id` to `ReportRecord`; job identity belongs to the job store.

- Modify: `app/api/routes.py`
  - Add `GET /api/interviews/{session_id}`.
  - Add `POST /api/interviews/{session_id}/skip`.
  - Add `GET /api/interviews/{session_id}/report/progress`.
  - Add local response builders for report progress detail.

- Modify: `app/static/index.html`
  - Add a skip button to the existing answer action area.
  - Give the submit button an explicit `id` so JS does not accidentally bind to the skip button.
  - Optionally add lightweight IDs for progress labels if needed by JS.

- Modify: `app/static/app.js`
  - Wire skip button.
  - Add `loadSessionSnapshot()` helper for state refresh.
  - Refresh session snapshot after start, answer, stream answer, skip, and finish.
  - Render question progress, question states, session status, and job tags from session detail.
  - Use `/report/progress` only when `/report` returns `202`, avoiding duplicate progress rendering.
  - Map report dimension field names to Chinese labels.

- Modify: `tests/test_session_service.py`
  - Unit tests for snapshot and skip behavior.

- Modify: `tests/test_postgres_session_store.py`
  - PostgreSQL runtime tests for snapshot serialization and skip persistence, skipped when `POSTGRES_DSN` is absent.

- Modify: `tests/test_api.py`
  - API tests for session detail, missing session status, and skip behavior.

- Modify: `tests/test_report_api.py`
  - API tests for report progress detail in processing, completed, and failed states.

- Modify: `tests/test_static_report_ui.py`
  - Static tests for skip button wiring, report progress endpoint polling, and dimension label mapping.

- Modify: `docs/interface-requirements.md`
  - Move `GET /api/interviews/{session_id}`, `POST /skip`, and `GET /report/progress` from pending requirements to implemented interfaces after code lands.

Unified verification command:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

---

### Task 1: Add Session Snapshot Support

**Files:**
- Modify: `tests/test_session_service.py`
- Modify: `tests/test_postgres_session_store.py`
- Modify: `app/services/session.py`

- [ ] **Step 1: Write failing tests for session snapshots**

Append to `tests/test_session_service.py`:

```python
def test_session_snapshot_includes_progress_tags_questions_and_messages():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    snapshot = store.snapshot(session.session_id)

    assert snapshot["session_id"] == session.session_id
    assert snapshot["status"] == "active"
    assert snapshot["current_index"] == 0
    assert snapshot["total_questions"] == 3
    assert snapshot["completed_questions"] == 0
    assert snapshot["current_question"]["id"] == "q1"
    assert snapshot["questions"][0]["state"] == "current"
    assert snapshot["questions"][1]["state"] == "pending"
    assert snapshot["messages"][0]["role"] == "interviewer"
    assert snapshot["job_tags"] == ["python", "redis"]


def test_session_snapshot_marks_completed_question_after_advance():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(session.session_id, "I used Redis cache-aside.")
    store.submit_answer(session.session_id, "I handled misses with fallback.")

    snapshot = store.snapshot(session.session_id)

    assert snapshot["current_question"]["id"] == "q2"
    assert snapshot["completed_questions"] == 1
    assert snapshot["questions"][0]["state"] == "completed"
    assert snapshot["questions"][1]["state"] == "current"


def test_session_snapshot_marks_finished_session_without_current_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.finish(session.session_id)

    snapshot = store.snapshot(session.session_id)

    assert snapshot["status"] == "finished"
    assert snapshot["current_question"] is None
    assert snapshot["completed_questions"] == 3
    assert [question["state"] for question in snapshot["questions"]] == [
        "completed",
        "completed",
        "completed",
    ]
```

- [ ] **Step 2: Write PostgreSQL snapshot serialization test**

Append to `tests/test_postgres_session_store.py`:

```python
def test_snapshot_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert snapshot["session_id"] == turn.session_id
    assert snapshot["status"] == "active"
    assert snapshot["job_tags"] == ["python", "fastapi"]
    assert snapshot["current_question"]["id"] == "q1"
    assert snapshot["questions"][0]["state"] == "current"
    assert snapshot["messages"][0]["content"] == "Describe your backend project."
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py::test_session_snapshot_includes_progress_tags_questions_and_messages tests/test_session_service.py::test_session_snapshot_marks_completed_question_after_advance tests/test_session_service.py::test_session_snapshot_marks_finished_session_without_current_question -q
```

Expected: FAIL with `AttributeError: 'InterviewSessionStore' object has no attribute 'snapshot'`.

- [ ] **Step 4: Implement session snapshot**

In `app/services/session.py`, add imports:

```python
from typing import Any, Dict, Iterator, Optional
```

Then add this method to `InterviewSessionStore`:

```python
    def snapshot(self, session_id: str) -> dict[str, Any]:
        state = self.get(session_id)
        current_question = None if state["status"] == "finished" else get_current_question(state)
        questions = [
            {
                **question.model_dump(),
                "state": _question_state(state, index),
            }
            for index, question in enumerate(state["plan"].questions)
        ]
        return {
            "session_id": state["session_id"],
            "status": state["status"],
            "current_index": state["current_index"],
            "total_questions": len(state["plan"].questions),
            "completed_questions": _completed_question_count(state),
            "job_tags": list(state["job_tags"]),
            "current_question": current_question.model_dump() if current_question else None,
            "questions": questions,
            "messages": [
                {
                    "role": message["role"],
                    "content": message["content"],
                    "question_id": message["question_id"],
                }
                for message in state["messages"]
            ],
        }
```

Add helper functions near `finish_interview_state(...)`:

```python
def _completed_question_count(state: InterviewState) -> int:
    if state["status"] == "finished":
        return len(state["plan"].questions)
    return max(0, min(state["current_index"], len(state["plan"].questions)))


def _question_state(state: InterviewState, index: int) -> str:
    if state["status"] == "finished":
        return "completed"
    if index < state["current_index"]:
        return "completed"
    if index == state["current_index"]:
        return "current"
    return "pending"
```

- [ ] **Step 5: Run snapshot tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py::test_session_snapshot_includes_progress_tags_questions_and_messages tests/test_session_service.py::test_session_snapshot_marks_completed_question_after_advance tests/test_session_service.py::test_session_snapshot_marks_finished_session_without_current_question -q
```

Expected: PASS.

- [ ] **Step 6: Run PostgreSQL snapshot test when `POSTGRES_DSN` is configured**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_snapshot_survives_store_reinstantiation -q
```

Expected: PASS when `POSTGRES_DSN` is configured, otherwise SKIPPED with `POSTGRES_DSN is required for pg_runtime tests`.

- [ ] **Step 7: Commit**

```powershell
git add app/services/session.py tests/test_session_service.py tests/test_postgres_session_store.py
git commit -m "feat: add interview session snapshot"
```

---

### Task 2: Add Session Detail API

**Files:**
- Modify: `tests/test_api.py`
- Modify: `app/api/routes.py`

- [ ] **Step 1: Write failing API tests for session detail**

Append to `tests/test_api.py`:

```python
def test_get_interview_session_detail_returns_snapshot():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    response = client.get(f"/api/interviews/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["status"] == "active"
    assert body["current_question"]["id"] == "q1"
    assert body["total_questions"] == 3
    assert body["questions"][0]["state"] == "current"
    assert body["messages"][0]["role"] == "interviewer"


def test_get_interview_session_detail_returns_404_for_missing_session():
    client = make_client()

    response = client.get("/api/interviews/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_get_interview_session_detail_returns_snapshot tests/test_api.py::test_get_interview_session_detail_returns_404_for_missing_session -q
```

Expected: FAIL with status `405` or `404` because the route is missing.

- [ ] **Step 3: Implement route**

In `app/api/routes.py`, add this route after `start_interview(...)`:

```python
@router.get("/interviews/{session_id}")
def get_interview_session(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        return store.snapshot(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
```

- [ ] **Step 4: Run API tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py::test_get_interview_session_detail_returns_snapshot tests/test_api.py::test_get_interview_session_detail_returns_404_for_missing_session -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api/routes.py tests/test_api.py
git commit -m "feat: add interview session detail endpoint"
```

---

### Task 3: Add Skip Current Question Behavior

**Files:**
- Modify: `tests/test_session_service.py`
- Modify: `tests/test_postgres_session_store.py`
- Modify: `tests/test_api.py`
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/api/routes.py`

- [ ] **Step 1: Write failing store tests for skip**

Append to `tests/test_session_service.py`:

```python
def test_skip_advances_to_next_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    skipped = store.skip(session.session_id)

    assert skipped.status == "active"
    assert skipped.current_question.id == "q2"
    assert skipped.follow_up is None
    snapshot = store.snapshot(session.session_id)
    assert snapshot["questions"][0]["state"] == "completed"
    assert snapshot["questions"][1]["state"] == "current"


def test_skip_last_question_finishes_session():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.skip(session.session_id)
    store.skip(session.session_id)
    final_turn = store.skip(session.session_id)

    assert final_turn.status == "finished"
    assert final_turn.current_question is None
    assert final_turn.follow_up == "本次模拟面试已结束。"
    snapshot = store.snapshot(session.session_id)
    assert snapshot["status"] == "finished"
    assert snapshot["current_question"] is None


def test_skip_finished_session_is_idempotent():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.finish(session.session_id)
    skipped = store.skip(session.session_id)

    assert skipped.status == "finished"
    assert skipped.current_question is None
    assert skipped.follow_up == "本次模拟面试已结束。"
    assert store.snapshot(session.session_id)["status"] == "finished"
```

- [ ] **Step 2: Write failing PostgreSQL skip persistence test**

Append to `tests/test_postgres_session_store.py`:

```python
def test_skip_persists_next_question_snapshot():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        InterviewPlan(
            title="Two question interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Describe a backend project.",
                    focus="project",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis consistency.",
                    focus="redis",
                ),
            ],
        ),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "redis"],
    )

    store.skip(turn.session_id)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert snapshot["status"] == "active"
    assert snapshot["current_question"]["id"] == "q2"
    assert snapshot["questions"][0]["state"] == "completed"
    assert snapshot["questions"][1]["state"] == "current"
    assert snapshot["messages"][-1]["content"] == "Explain Redis consistency."
```

- [ ] **Step 3: Write failing API test for skip**

Append to `tests/test_api.py`:

```python
def test_skip_endpoint_advances_question():
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
    body = response.json()
    assert body["status"] == "active"
    assert body["current_question"]["id"] == "q2"
    assert body["follow_up"] is None
```

- [ ] **Step 4: Run tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py::test_skip_advances_to_next_question tests/test_session_service.py::test_skip_last_question_finishes_session tests/test_session_service.py::test_skip_finished_session_is_idempotent tests/test_api.py::test_skip_endpoint_advances_question -q
```

Expected: FAIL because `skip` does not exist.

- [ ] **Step 5: Implement skip state helper and store method**

In `app/services/session.py`, add method to `InterviewSessionStore`:

```python
    def skip(self, session_id: str) -> InterviewTurn:
        state = self.get(session_id)
        skipped_state = skip_interview_question_state(state)
        self._sessions[session_id] = skipped_state
        return self._to_turn(skipped_state, follow_up=_extract_follow_up(skipped_state))
```

Add helper near `finish_interview_state(...)`:

```python
def skip_interview_question_state(state: InterviewState) -> InterviewState:
    if state["status"] == "finished":
        return state

    next_index = state["current_index"] + 1
    if next_index >= len(state["plan"].questions):
        return finish_interview_state(state)

    next_question = state["plan"].questions[next_index]
    state["current_index"] = next_index
    state["decision"] = {
        "action": "next_question",
        "follow_up": None,
        "reason": "user_skipped_question",
    }
    state["pending_output"] = next_question.prompt
    state["messages"].append(
        {
            "role": "interviewer",
            "content": next_question.prompt,
            "question_id": next_question.id,
        }
    )
    return state
```

Update import in `app/services/postgres_session.py`:

```python
from app.services.session import (
    InterviewSessionStore,
    InterviewTurn,
    PreparedInterviewTurn,
    finish_interview_state,
    skip_interview_question_state,
)
```

Add method to `PostgresInterviewSessionStore`:

```python
    def skip(self, session_id: str) -> InterviewTurn:
        state = self.get(session_id)
        skipped_state = skip_interview_question_state(state)
        self._replace_state(skipped_state)
        return self._to_turn(
            skipped_state,
            follow_up=self._extract_follow_up(skipped_state),
        )
```

- [ ] **Step 6: Implement skip route**

In `app/api/routes.py`, add after `finish_interview(...)`:

```python
@router.post("/interviews/{session_id}/skip")
def skip_interview_question(
    session_id: str,
    background_tasks: BackgroundTasks,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        turn = store.skip(session_id)
    except ValueError as exc:
        _raise_value_error(exc)
    _schedule_report_if_needed(turn.status, session_id, background_tasks, store)
    return _turn_to_dict(turn)
```

- [ ] **Step 7: Run skip tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py::test_skip_advances_to_next_question tests/test_session_service.py::test_skip_last_question_finishes_session tests/test_session_service.py::test_skip_finished_session_is_idempotent tests/test_api.py::test_skip_endpoint_advances_question -q
```

Expected: PASS.

- [ ] **Step 8: Run PostgreSQL skip persistence test when `POSTGRES_DSN` is configured**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_skip_persists_next_question_snapshot -q
```

Expected: PASS when `POSTGRES_DSN` is configured, otherwise SKIPPED with `POSTGRES_DSN is required for pg_runtime tests`.

- [ ] **Step 9: Commit**

```powershell
git add app/services/session.py app/services/postgres_session.py app/api/routes.py tests/test_session_service.py tests/test_postgres_session_store.py tests/test_api.py
git commit -m "feat: add interview question skip endpoint"
```

---

### Task 4: Add Report Progress Detail API

**Files:**
- Modify: `tests/test_report_api.py`
- Modify: `app/api/routes.py`

- [ ] **Step 1: Extend report API fake job store for job lookup**

In `tests/test_report_api.py`, extend `FakeReportJobStore` inside `make_client()` with:

```python
        def get_job_by_session(self, session_id: str) -> dict | None:
            if session_id not in self.enqueue_calls:
                return None
            index = self.enqueue_calls.index(session_id) + 1
            return {
                "job_id": f"job-{index}",
                "session_id": session_id,
                "status": "queued",
            }
```

- [ ] **Step 2: Write failing tests for progress endpoint**

Append to `tests/test_report_api.py`:

```python
def test_report_progress_endpoint_returns_queued_detail_before_report_record_exists():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["status"] == "processing"
    assert body["stage"] == "queued"
    assert body["percent"] == 0
    assert body["report_job_id"] is None


def test_report_progress_endpoint_returns_processing_detail():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["status"] == "processing"
    assert body["stage"] == "retrieving"
    assert body["percent"] == 20
    assert body["events"][0]["stage"] == "retrieving"
    assert body["rag"]["top_k"] == 5
    assert body["rag"]["source_types"] == ["theory", "expert_benchmark"]


def test_report_progress_endpoint_returns_report_job_id_after_finish_enqueue():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    finish_response = client.post(f"/api/interviews/{session_id}/finish")
    assert finish_response.status_code == 200

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    assert response.json()["report_job_id"] == "job-1"


def test_report_progress_endpoint_returns_completed_detail():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.save_report(
        session_id,
        InterviewReport(
            session_id=session_id,
            overall_score=81,
            overall_dimension_scores=make_dimension_scores(81),
            summary="Clear project story.",
            highlights=["Explained tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a backend project.",
                    user_answer="The candidate built a backend cache service.",
                    score=81,
                    dimension_scores=make_dimension_scores(81),
                    rationale="The answer covered tradeoffs.",
                    critique="Needs stronger metrics.",
                    better_answer="I reduced p95 latency using Redis.",
                    references=[],
                )
            ],
        ),
    )

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["stage"] == "completed"
    assert response.json()["percent"] == 100


def test_report_progress_endpoint_rejects_active_interview():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 404
    assert response.json()["detail"] == "interview is not finished"
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py::test_report_progress_endpoint_returns_queued_detail_before_report_record_exists tests/test_report_api.py::test_report_progress_endpoint_returns_processing_detail tests/test_report_api.py::test_report_progress_endpoint_returns_report_job_id_after_finish_enqueue tests/test_report_api.py::test_report_progress_endpoint_returns_completed_detail tests/test_report_api.py::test_report_progress_endpoint_rejects_active_interview -q
```

Expected: FAIL with missing route.

- [ ] **Step 4: Implement progress route and response helpers**

In `app/api/routes.py`, add after `get_interview_report(...)`:

```python
@router.get("/interviews/{session_id}/report/progress")
def get_interview_report_progress(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
    except ValueError as exc:
        _raise_value_error(exc)

    if state["status"] != "finished":
        raise HTTPException(status_code=404, detail="interview is not finished")

    record = store.get_report_record(session_id)
    return _report_progress_detail(
        session_id,
        record,
        report_job_id=_report_job_id_for_session(session_id),
    )
```

Add helper functions near `_turn_to_dict(...)`:

```python
def _report_job_id_for_session(session_id: str) -> str | None:
    try:
        job = get_report_job_store().get_job_by_session(session_id)
    except (AttributeError, RuntimeError):
        return None
    if not job:
        return None
    return job["job_id"]


def _report_progress_detail(session_id: str, record, *, report_job_id: str | None):
    if record is None:
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "processing",
            "stage": "queued",
            "percent": 0,
            "message": "Waiting for report generation to start.",
            "events": [],
            "rag": _rag_progress_defaults(),
        }

    if record.status == "completed":
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "completed",
            "stage": "completed",
            "percent": 100,
            "message": "Report completed.",
            "events": [{"stage": "completed", "message": "Report completed."}],
            "rag": _rag_progress_defaults(),
        }

    if record.status == "failed":
        return {
            "session_id": session_id,
            "report_job_id": report_job_id,
            "status": "failed",
            "stage": "failed",
            "percent": 100,
            "message": record.error or "Report generation failed.",
            "events": [
                {
                    "stage": "failed",
                    "message": record.error or "Report generation failed.",
                }
            ],
            "rag": _rag_progress_defaults(),
        }

    progress = record.progress
    if progress is None:
        stage = "retrieving"
        percent = 0
        message = "Report generation is processing."
        current_question_id = None
    else:
        stage = progress.stage
        percent = progress.percent
        message = progress.message
        current_question_id = progress.current_question_id

    return {
        "session_id": session_id,
        "report_job_id": report_job_id,
        "status": "processing",
        "stage": stage,
        "percent": percent,
        "message": message,
        "current_question_id": current_question_id,
        "events": [{"stage": stage, "message": message}],
        "rag": _rag_progress_defaults(),
    }


def _rag_progress_defaults() -> dict:
    return {
        "top_k": 5,
        "source_types": ["theory", "expert_benchmark"],
        "matched_chunks": None,
    }
```

- [ ] **Step 5: Run progress tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py::test_report_progress_endpoint_returns_queued_detail_before_report_record_exists tests/test_report_api.py::test_report_progress_endpoint_returns_processing_detail tests/test_report_api.py::test_report_progress_endpoint_returns_report_job_id_after_finish_enqueue tests/test_report_api.py::test_report_progress_endpoint_returns_completed_detail tests/test_report_api.py::test_report_progress_endpoint_rejects_active_interview -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/api/routes.py tests/test_report_api.py
git commit -m "feat: add report progress detail endpoint"
```

---

### Task 5: Wire Static UI To New Runtime Endpoints

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write failing static UI tests**

Append to `tests/test_static_report_ui.py`:

```python
def test_static_page_has_skip_button():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="skipQuestionButton"' in html


def test_app_js_calls_session_detail_skip_and_report_progress_endpoints():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "`/api/interviews/${sessionId}`" in js
    assert "`/api/interviews/${sessionId}/skip`" in js
    assert "`/api/interviews/${sessionId}/report/progress`" in js
    assert "renderSessionSnapshot(" in js
    assert "renderQuestionPlanFromSnapshot(" in js
    assert "renderJobTags(" in js
    assert "await loadSessionSnapshot();" in js
    assert "renderReportProcessing(progressBody || body.progress || null);" in js
    assert "renderReportProcessing(body.progress || null);" not in js


def test_app_js_targets_submit_button_not_skip_button():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'answerForm.querySelector("button[type=\\"submit\\"]")' in js
    assert "skipQuestionButton.disabled = !enabled" in js


def test_app_js_maps_dimension_labels_to_chinese():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "dimensionLabels" in js
    assert "知识广度" in js
    assert "技术深度" in js
    assert "系统设计" in js
    assert "工程实践" in js
    assert "表达沟通" in js
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_static_page_has_skip_button tests/test_static_report_ui.py::test_app_js_calls_session_detail_skip_and_report_progress_endpoints tests/test_static_report_ui.py::test_app_js_targets_submit_button_not_skip_button tests/test_static_report_ui.py::test_app_js_maps_dimension_labels_to_chinese -q
```

Expected: FAIL because button and JS calls are missing.

- [ ] **Step 3: Add skip button to static page**

In `app/static/index.html`, inside `<form id="answerForm" class="input-wrap">`, add a skip button before the send button:

```html
<button class="send secondary" id="skipQuestionButton" type="button">跳过</button>
<button class="send" id="sendAnswerButton" type="submit">发送 ✈</button>
```

If `.send.secondary` is not styled, add the smallest compatible CSS in `app/static/styles.css`:

```css
.send.secondary {
  background: rgba(255, 255, 255, 0.08);
  color: var(--text);
  border: 1px solid rgba(255, 255, 255, 0.12);
}
```

Add `app/static/styles.css` to this task only if the class does not render acceptably with existing button styles.

- [ ] **Step 4: Wire JS endpoints and dimension labels**

In `app/static/app.js`, add DOM reference near existing buttons:

```javascript
const topicTags = document.querySelector("#topicTags");
const answerButton = answerForm.querySelector("button[type=\"submit\"]");
const skipQuestionButton = document.querySelector("#skipQuestionButton");
```

Replace the existing `const answerButton = answerForm.querySelector("button");` line with the submit-button selector above. This prevents the newly inserted skip button from becoming the button controlled by `setAnswerEnabled(...)`.

In the start handler, after `renderTurn(turn);`, add:

```javascript
  await loadSessionSnapshot();
```

In the answer submit handler, after `renderStreamedTurn(turn);`, add:

```javascript
    await loadSessionSnapshot();
```

In the finish handler, after `renderTurn(turn);`, add:

```javascript
    await loadSessionSnapshot();
```

Add skip handling after the finish handler:

```javascript
skipQuestionButton.addEventListener("click", async () => {
  if (!sessionId) {
    return;
  }
  setAnswerEnabled(false);
  try {
    const turn = await postJson(`/api/interviews/${sessionId}/skip`, {});
    renderTurn(turn);
    await loadSessionSnapshot();
  } catch (error) {
    setAnswerEnabled(true);
    addMessage("agent", "跳过题目失败，请稍后重试。");
    console.error(error);
  }
});
```

Add helper:

```javascript
async function loadSessionSnapshot() {
  if (!sessionId) {
    return null;
  }
  const response = await fetch(`/api/interviews/${sessionId}`);
  if (!response.ok) {
    return null;
  }
  const snapshot = await response.json();
  renderSessionSnapshot(snapshot);
  return snapshot;
}
```

Add renderer:

```javascript
function renderSessionSnapshot(snapshot) {
  if (!snapshot) {
    return;
  }
  setInterviewState(snapshot.status === "finished" ? "finished" : "in_progress");
  planQuestionCount.textContent = String(snapshot.total_questions || 0);
  planCoverage.textContent = String(new Set((snapshot.questions || []).map((question) => question.kind)).size);
  renderJobTags(snapshot.job_tags || []);
  renderQuestionPlanFromSnapshot(snapshot.questions || []);
}

function renderJobTags(tags) {
  topicTags.innerHTML = "";
  if (!tags.length) {
    topicTags.appendChild(createEl("span", "tag muted", "等待岗位标签"));
    return;
  }
  tags.forEach((tag) => {
    topicTags.appendChild(createEl("span", "tag", tag));
  });
}

function renderQuestionPlanFromSnapshot(questions) {
  planEl.innerHTML = "";
  planStatus.textContent = questions.length ? "已生成计划" : "待生成";
  if (!questions.length) {
    planEl.innerHTML = `<div class="empty-state">点击“生成题目计划”后，会在这里展示面试路线。</div>`;
    return;
  }

  const stateLabels = {
    completed: "已完成",
    current: "当前题",
    pending: "待进行",
  };
  questions.forEach((question, index) => {
    const row = createEl("div", `question-row question-${question.state || "pending"}`);
    row.appendChild(createEl("div", "step", String(index + 1)));

    const box = createEl("div", "question-box");
    box.appendChild(createEl("strong", "", question.prompt));
    const meta = createEl("div", "meta");
    meta.appendChild(createEl("span", "", toQuestionLabel(question)));
    meta.appendChild(createEl("span", "", stateLabels[question.state] || "待进行"));
    box.appendChild(meta);
    row.appendChild(box);
    planEl.appendChild(row);
  });
}
```

Add a progress helper:

```javascript
async function loadReportProgress() {
  const progressResponse = await fetch(`/api/interviews/${sessionId}/report/progress`);
  if (!progressResponse.ok) {
    return null;
  }
  return progressResponse.json();
}
```

In `pollReport()`, replace the `response.status === 202` branch with:

```javascript
if (response.status === 202) {
  const progressBody = await loadReportProgress();
  renderReportProcessing(progressBody || body.progress || null);
  reportPollTimer = setTimeout(pollReport, 3000);
  return;
}
```

In `renderReport(report)`, replace direct dimension name rendering with a reusable mapper:

```javascript
const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};

function toDimensionLabel(name) {
  return dimensionLabels[name] || name;
}

Object.entries(report.overall_dimension_scores).forEach(([name, value]) => {
  const row = createEl("div", "dimension-row");
  row.appendChild(createEl("span", "dimension-name", toDimensionLabel(name)));
  row.appendChild(createEl("span", "dimension-value", String(value)));
  dimensions.appendChild(row);
});
```

In `renderFeedback(feedback)`, replace feedback dimension rendering with:

```javascript
Object.entries(feedback.dimension_scores).forEach(([name, value]) => {
  dimensions.appendChild(createEl("span", "feedback-dimension", `${toDimensionLabel(name)}: ${value}`));
});
```

In `setAnswerEnabled(enabled)`, disable both answer controls:

```javascript
function setAnswerEnabled(enabled) {
  answerInput.disabled = !enabled;
  answerButton.disabled = !enabled;
  skipQuestionButton.disabled = !enabled;
}
```

- [ ] **Step 5: Run static tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py::test_static_page_has_skip_button tests/test_static_report_ui.py::test_app_js_calls_session_detail_skip_and_report_progress_endpoints tests/test_static_report_ui.py::test_app_js_targets_submit_button_not_skip_button tests/test_static_report_ui.py::test_app_js_maps_dimension_labels_to_chinese -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/static/index.html app/static/app.js app/static/styles.css tests/test_static_report_ui.py
git commit -m "feat: wire UI to session and progress contracts"
```

---

### Task 6: Update Interface Documentation

**Files:**
- Modify: `docs/interface-requirements.md`

- [ ] **Step 1: Move implemented endpoints to the implemented table**

Update the "当前已实现接口" table so it includes:

```markdown
| `GET` | `/api/interviews/{session_id}` | 查询会话详情、进度、题目导航和消息记录 | `test1.html` 进度区和题目导航 |
| `POST` | `/api/interviews/{session_id}/skip` | 跳到下一题 | `test1.html` 下一题按钮 |
| `GET` | `/api/interviews/{session_id}/report/progress` | 查询更详细报告任务进度、时间线和 `report_job_id` | `test2.html` 报告生成页 |
```

Remove those rows from the "当前未实现但 HTML 原型需要的接口" table.

- [ ] **Step 2: Add implemented interface detail sections**

Add sections:

```markdown
### 5.8 GET `/api/interviews/{session_id}`

用途：查询面试会话快照，用于刷新恢复、进度展示和题目导航。

成功响应：包含 `session_id`、`status`、`current_index`、`total_questions`、`completed_questions`、`job_tags`、`current_question`、`questions`、`messages`。

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `404` | 会话不存在 | `{"detail":"session not found"}` |

### 5.9 POST `/api/interviews/{session_id}/skip`

用途：跳过当前题，进入下一题；如果当前题是最后一题，则结束面试并触发报告生成。

成功响应：`InterviewTurn`

错误响应：

| 状态码 | 场景 | 响应 |
| --- | --- | --- |
| `404` | 会话不存在 | `{"detail":"session not found"}` |

### 5.10 GET `/api/interviews/{session_id}/report/progress`

用途：查询报告生成详情，供报告生成页展示阶段、百分比、事件、RAG 配置摘要和后台任务 ID。

成功响应：包含 `session_id`、`report_job_id`、`status`、`stage`、`percent`、`message`、`events`、`rag`。

`stage` 是进度端点的响应层字段，允许 `queued`、`retrieving`、`analyzing`、`aggregating`、`completed`、`failed`。其中 `queued` 仅表示报告已等待生成但尚无 `ReportRecord.progress`，不写入 `ReportProgress` 模型。

`report_job_id` 从报告任务表按 `session_id` 查询；如果当前运行模式没有配置任务表，返回 `null`。
```

- [ ] **Step 3: Update acceptance criteria and priorities**

Update "当前已实现接口验收" with:

```markdown
| A10 | `GET /api/interviews/{session_id}` 返回会话快照 |
| A11 | `POST /api/interviews/{session_id}/skip` 返回下一题或结束状态 |
| A12 | `GET /api/interviews/{session_id}/report/progress` 返回报告进度详情 |
```

Remove `GET /api/interviews/{session_id}`、`POST /skip`、`GET /report/progress` from the P0/P1 pending priority table.

- [ ] **Step 4: Run documentation consistency scan**

Run:

```powershell
rg -n "当前未实现|/skip|/report/progress|GET /api/interviews/\\{session_id\\}" docs/interface-requirements.md
```

Expected: implemented endpoints appear in implemented sections and no longer appear as pending rows.

- [ ] **Step 5: Commit**

```powershell
git add docs/interface-requirements.md
git commit -m "docs: update interface requirements for stage 10"
```

---

### Task 7: Final Regression

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_session_service.py tests/test_report_api.py tests/test_static_report_ui.py tests/test_postgres_session_store.py -q
```

Expected: PASS, with PostgreSQL tests SKIPPED when `POSTGRES_DSN` is not configured.

- [ ] **Step 2: Run full test suite**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: all non-skipped tests pass. Existing environment-dependent PostgreSQL or pgvector tests may remain skipped when their DSN is not configured.

- [ ] **Step 3: Inspect working tree**

Run:

```powershell
git status --short
```

Expected: only Stage 10 files changed, plus any pre-existing unrelated dirty files that were already present before the plan execution.

- [ ] **Step 4: Commit final documentation or cleanup changes**

If any final cleanup is needed:

```powershell
git add app/services/session.py app/services/postgres_session.py app/api/routes.py app/static/index.html app/static/app.js app/static/styles.css tests/test_session_service.py tests/test_api.py tests/test_report_api.py tests/test_static_report_ui.py docs/interface-requirements.md
git commit -m "chore: finalize stage 10 interface completion"
```

Skip this commit if every task already committed its own complete changes.

---

## Self-Review

Spec coverage:

- `GET /api/interviews/{session_id}` is covered by Tasks 1 and 2.
- `POST /api/interviews/{session_id}/skip` is covered by Task 3.
- `GET /api/interviews/{session_id}/report/progress` is covered by Task 4.
- Frontend use of session detail, answer/stream/finish/skip snapshot refresh, job tags, progress, and dimension labels is covered by Task 5.
- PostgreSQL snapshot and skip persistence are covered by Tasks 1 and 3 with `POSTGRES_DSN`-guarded tests.
- Interface documentation update is covered by Task 6.

Explicitly deferred requirements:

- Draft APIs are deferred because they require persistence semantics and, eventually, user ownership.
- PDF export is deferred because it introduces document generation and binary response testing.
- Report center list is deferred because useful filtering and authorization depend on user identity.

Placeholder scan:

- No steps contain placeholder markers or generic test instructions.
- Every code-changing step names exact files and includes concrete code or exact assertions.

Type consistency:

- Session snapshot fields match `docs/interface-requirements.md`: `session_id`, `status`, `current_index`, `total_questions`, `completed_questions`, `job_tags`, `current_question`, `questions`, `messages`.
- `skip(...)` returns the existing `InterviewTurn`, matching `/answer` and `/finish`.
- Report progress detail uses `session_id`, `report_job_id`, `status`, `stage`, `percent`, `message`, `events`, `rag`.
- `stage: "queued"` exists only in the `/report/progress` response when no `ReportRecord` exists; it is not added to the persisted `ReportProgress` model.
- `report_job_id` is read from `get_report_job_store().get_job_by_session(session_id)` and gracefully falls back to `null` when no job store is configured.
