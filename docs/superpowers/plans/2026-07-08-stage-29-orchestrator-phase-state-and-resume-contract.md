# Stage 29 Orchestrator, Phase State, And Resume Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a LangGraph-powered orchestration layer, phase-aware persisted session state, and a versioned HTTP resume contract so the current Local V1 runtime starts matching the architecture document's control-plane design.

**Architecture:** Keep the current FastAPI pages, SSE streaming, PostgreSQL session store, Postgres report jobs, and Celery round-review worker stable. Add an explicit `OrchestratorAgent` over the existing interview runner, expand persisted `InterviewState` with phase/version/review metadata, and make session commands optimistic-concurrency-safe with `expected_version` plus idempotent `command_id` handling. Stage 29 does not add WebSocket transport, Redis checkpoints, voice, or frontend redesign.

**Tech Stack:** Python 3.11, FastAPI, LangGraph, PostgreSQL, Pydantic v2, pytest, current Celery round-review path.

---

## File Structure

- Modify: `requirements.txt`
  - Add the `langgraph` runtime dependency.

- Create: `app/agents/orchestrator.py`
  - Define the explicit orchestration boundary that owns phase-aware command execution.

- Create: `app/graphs/orchestrator_graph.py`
  - Build the LangGraph-based orchestration graph and typed command payloads.

- Create: `app/graphs/interview_transitions.py`
  - Hold pure interview-state transition helpers shared by the session store and orchestrator graph.

- Modify: `app/agents/__init__.py`
  - Export `OrchestratorAgent`.

- Modify: `app/graphs/interview_state.py`
  - Expand the persisted session state with phase, review, and version metadata.

- Modify: `app/graphs/interview_graph.py`
  - Keep the interview-only fast path focused on question flow and let the orchestrator manage outer phases.

- Create: `app/services/session_errors.py`
  - Hold the typed version-conflict exception used by store and API layers.

- Modify: `app/services/session.py`
  - Route command execution through `OrchestratorAgent`, enforce `expected_version`, support idempotent `command_id`, and expose richer resume metadata in snapshots.

- Modify: `app/services/postgres_session.py`
  - Persist the new orchestration metadata in PostgreSQL and preserve idempotent behavior after process restart.

- Modify: `app/services/session_serialization.py`
  - Serialize and deserialize the new phase/version fields.

- Modify: `app/ports/runtime.py`
  - Extend the session-command protocol to accept versioned/idempotent command arguments.

- Modify: `app/api/routes.py`
  - Accept `expected_version` and `command_id` in command requests, expose the resume contract through `GET /api/interviews/{session_id}`, and map version conflicts to `409`.

- Modify: `app/services/report_tasks.py`
  - Reuse the new state metadata so a completed report moves the session into `review/completed`.

- Modify: `app/services/report_enqueue.py`
  - Keep the enqueue boundary stable while allowing the session state to enter `review/processing`.

- Modify: `README.md`
  - Document Stage 29 architecture position and the versioned HTTP resume contract.

- Modify: `docs/local-v1-runbook.md`
  - Document how to resume a session and how `expected_version` / `command_id` should be used in local verification.

- Test: `tests/test_runtime_boundary_api.py`
- Test: `tests/test_interview_graph.py`
- Test: `tests/test_orchestrator_graph.py`
- Test: `tests/test_session_serialization.py`
- Test: `tests/test_session_service.py`
- Test: `tests/test_postgres_session_store.py`
- Test: `tests/test_api.py`
- Test: `tests/test_report_tasks.py`
- Test: `tests/test_local_v1_docs.py`

---

### Task 1: Add LangGraph And Report The Stage 29 Runtime Boundary

**Files:**
- Modify: `requirements.txt`
- Modify: `app/api/routes.py`
- Test: `tests/test_runtime_boundary_api.py`

- [ ] **Step 1: Write the failing runtime-boundary test**

Replace the assertions in `tests/test_runtime_boundary_api.py` with:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_runtime_boundary_endpoint_reports_stage_29_components(monkeypatch):
    monkeypatch.delenv("INTERVIEW_EVENT_BACKEND", raising=False)
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
        "langgraph": True,
    }
    assert body["orchestration"] == {
        "engine": "langgraph",
        "phase_aware": True,
        "resume_contract": "versioned_http",
    }


def test_runtime_boundary_endpoint_reports_stage_29_celery_mode(monkeypatch):
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
        "langgraph": True,
    }
    assert body["orchestration"]["engine"] == "langgraph"
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_boundary_api.py -q
```

Expected: FAIL because `/api/runtime` still reports `langgraph=False` and does not return an `orchestration` section.

- [ ] **Step 3: Add the LangGraph dependency**

Update `requirements.txt` to:

```text
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
pytest>=8.0.0
httpx>=0.27.0
pydantic>=2.0.0
langchain>=1.0.0
langchain-openai>=1.0.0
langgraph>=0.2.51
psycopg2-binary>=2.9.9
pgvector>=0.3.5
sentence-transformers>=3.0.0
langchain-huggingface>=0.1.0
reportlab>=4.2.0
celery>=5.4.0
redis>=5.0.7
```

- [ ] **Step 4: Update the runtime boundary response**

Replace `runtime_boundary()` in `app/api/routes.py` with:

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
            "langgraph": True,
        },
        "orchestration": {
            "engine": "langgraph",
            "phase_aware": True,
            "resume_contract": "versioned_http",
        },
    }
```

- [ ] **Step 5: Run the focused test and verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_boundary_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt app/api/routes.py tests/test_runtime_boundary_api.py
git commit -m "feat: report stage 29 langgraph runtime boundary"
```

---

### Task 2: Expand The Persisted Session State With Phase And Version Metadata

**Files:**
- Modify: `app/graphs/interview_state.py`
- Modify: `app/services/session_serialization.py`
- Test: `tests/test_interview_graph.py`
- Test: `tests/test_session_serialization.py`

- [ ] **Step 1: Write the failing state-metadata tests**

Append to `tests/test_interview_graph.py`:

```python
def test_build_initial_state_records_phase_review_and_version_metadata():
    state = build_initial_state(**make_start_kwargs())

    assert state["phase"] == "interview"
    assert state["phase_status"] == "active"
    assert state["review_status"] == "idle"
    assert state["state_version"] == 1
    assert state["checkpoint_version"] == 1
    assert state["last_checkpoint_at"] == state["started_at"]
    assert state["last_command_id"] is None
```

Append to `tests/test_session_serialization.py`:

```python
def test_session_serialization_round_trips_orchestration_metadata():
    state = make_state()
    state["phase"] = "review"
    state["phase_status"] = "completed"
    state["review_status"] = "completed"
    state["state_version"] = 6
    state["checkpoint_version"] = 6
    state["last_checkpoint_at"] = "2026-07-08T10:00:00Z"
    state["last_command_id"] = "cmd-2"

    row = session_row_from_state(state)
    restored = state_from_rows(row, [])

    assert row["phase"] == "review"
    assert row["phase_status"] == "completed"
    assert row["review_status"] == "completed"
    assert row["state_version"] == 6
    assert row["checkpoint_version"] == 6
    assert row["last_checkpoint_at"] == "2026-07-08T10:00:00Z"
    assert row["last_command_id"] == "cmd-2"
    assert restored["phase"] == "review"
    assert restored["phase_status"] == "completed"
    assert restored["review_status"] == "completed"
    assert restored["state_version"] == 6
    assert restored["checkpoint_version"] == 6
    assert restored["last_checkpoint_at"] == "2026-07-08T10:00:00Z"
    assert restored["last_command_id"] == "cmd-2"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py tests/test_session_serialization.py -q
```

Expected: FAIL because `InterviewState` and the row serializers do not expose the new fields.

- [ ] **Step 3: Expand the typed state contract**

Update `app/graphs/interview_state.py` so `InterviewState` becomes:

```python
class InterviewState(TypedDict):
    session_id: str
    plan: InterviewPlan
    current_index: int
    messages: list[InterviewMessage]
    decision: InterviewDecision | None
    pending_output: str | None
    status: Literal["active", "finished"]
    phase: Literal["prep", "interview", "review"]
    phase_status: Literal["pending", "active", "completed", "failed"]
    review_status: Literal["idle", "processing", "completed", "failed"]
    job_description: str
    resume_text: str
    job_tags: list[str]
    skipped_question_ids: list[str]
    started_at: str
    finished_at: str | None
    state_version: int
    checkpoint_version: int
    last_checkpoint_at: str | None
    last_command_id: str | None
```

Then update `build_initial_state()` to initialize:

```python
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
        "phase": "interview",
        "phase_status": "active" if first_question else "completed",
        "review_status": "idle",
        "job_description": job_description,
        "resume_text": resume_text,
        "job_tags": job_tags,
        "skipped_question_ids": [],
        "started_at": now,
        "finished_at": now if first_question is None else None,
        "state_version": 1,
        "checkpoint_version": 1,
        "last_checkpoint_at": now,
        "last_command_id": None,
    }
```

- [ ] **Step 4: Serialize and deserialize the new metadata**

Update `app/services/session_serialization.py`:

```python
def session_row_from_state(state: InterviewState) -> dict[str, Any]:
    return {
        "session_id": state["session_id"],
        "plan_json": state["plan"].model_dump(mode="json"),
        "current_index": state["current_index"],
        "status": state["status"],
        "phase": state["phase"],
        "phase_status": state["phase_status"],
        "review_status": state["review_status"],
        "job_description": state["job_description"],
        "resume_text": state["resume_text"],
        "job_tags": list(state["job_tags"]),
        "decision_json": state["decision"],
        "pending_output": state["pending_output"],
        "skipped_question_ids": list(state.get("skipped_question_ids", [])),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "state_version": state["state_version"],
        "checkpoint_version": state["checkpoint_version"],
        "last_checkpoint_at": state.get("last_checkpoint_at"),
        "last_command_id": state.get("last_command_id"),
    }
```

```python
def state_from_rows(
    session_row: dict[str, Any],
    message_rows: list[dict[str, Any]],
) -> InterviewState:
    return {
        "session_id": session_row["session_id"],
        "plan": InterviewPlan.model_validate(session_row["plan_json"]),
        "current_index": int(session_row["current_index"]),
        "messages": [
            {
                "role": row["role"],
                "content": row["content"],
                "question_id": row["question_id"],
            }
            for row in sorted(message_rows, key=lambda row: int(row["sequence_no"]))
        ],
        "decision": session_row.get("decision_json"),
        "pending_output": session_row.get("pending_output"),
        "status": session_row["status"],
        "phase": session_row.get("phase", "interview"),
        "phase_status": session_row.get("phase_status", "active"),
        "review_status": session_row.get("review_status", "idle"),
        "job_description": session_row["job_description"],
        "resume_text": session_row["resume_text"],
        "job_tags": list(session_row["job_tags"]),
        "skipped_question_ids": list(session_row.get("skipped_question_ids") or []),
        "started_at": session_row.get("started_at") or "",
        "finished_at": session_row.get("finished_at"),
        "state_version": int(session_row.get("state_version", 1)),
        "checkpoint_version": int(session_row.get("checkpoint_version", 1)),
        "last_checkpoint_at": session_row.get("last_checkpoint_at"),
        "last_command_id": session_row.get("last_command_id"),
    }
```

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_graph.py tests/test_session_serialization.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/graphs/interview_state.py app/services/session_serialization.py tests/test_interview_graph.py tests/test_session_serialization.py
git commit -m "feat: add phase and version metadata to session state"
```

---

### Task 3: Introduce The LangGraph Orchestrator Agent

**Files:**
- Create: `app/graphs/orchestrator_graph.py`
- Create: `app/graphs/interview_transitions.py`
- Create: `app/agents/orchestrator.py`
- Modify: `app/agents/__init__.py`
- Modify: `app/services/session.py`
- Test: `tests/test_orchestrator_graph.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing orchestrator tests**

Create `tests/test_orchestrator_graph.py`:

```python
from app.agents.orchestrator import OrchestratorAgent
from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport


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
                focus="redis",
            ),
        ],
    )


def make_state():
    return build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )


class FakeLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        raise AssertionError("not used")

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain the cache invalidation strategy."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please explain the cache invalidation strategy."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("not used")


def test_orchestrator_answer_delegates_to_interview_phase():
    agent = OrchestratorAgent(llm=FakeLLM())

    updated = agent.apply_command(
        make_state(),
        {"kind": "answer", "answer": "I used Redis to cache hot records."},
    )

    assert updated["phase"] == "interview"
    assert updated["pending_output"] == "Please explain the cache invalidation strategy."
    assert updated["state_version"] == 1


def test_orchestrator_finish_promotes_review_phase():
    agent = OrchestratorAgent(llm=FakeLLM())

    finished = agent.apply_command(make_state(), {"kind": "finish"})

    assert finished["status"] == "finished"
    assert finished["phase"] == "review"
    assert finished["phase_status"] == "active"
    assert finished["review_status"] == "processing"


def test_orchestrator_sync_review_completed_marks_phase_complete():
    agent = OrchestratorAgent(llm=FakeLLM())
    finished = agent.apply_command(make_state(), {"kind": "finish"})

    completed = agent.apply_command(
        finished,
        {"kind": "sync_review", "report_status": "completed"},
    )

    assert completed["phase"] == "review"
    assert completed["phase_status"] == "completed"
    assert completed["review_status"] == "completed"
```

Append to `tests/test_api.py`:

```python
def test_answer_stream_publishes_round_closed_event_when_streamed_answer_closes_question():
    published = []

    class FakePublisher:
        def publish(self, event):
            published.append(event)

    app.dependency_overrides[route_module.get_event_publisher] = lambda: FakePublisher()
    client = make_client()

    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    ).json()

    client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={"answer": "I used Redis to cache hot records."},
    )
    with client.stream(
        "POST",
        f"/api/interviews/{started['session_id']}/answer/stream",
        json={"answer": "I added delayed double delete."},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: done" in body
    assert len(published) == 1
    assert published[0].question_id == "q1"
    assert published[0].answer_state == "answered"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_orchestrator_graph.py tests/test_api.py::test_answer_stream_publishes_round_closed_event_when_streamed_answer_closes_question -q
```

Expected: FAIL with `ModuleNotFoundError` because the orchestrator files do not exist yet.

- [ ] **Step 3: Create the orchestration graph**

Create `app/graphs/orchestrator_graph.py`:

```python
from copy import deepcopy
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import InterviewState


class OrchestratorCommand(TypedDict, total=False):
    kind: Literal[
        "answer",
        "prepare_stream",
        "complete_stream",
        "skip",
        "finish",
        "sync_review",
    ]
    answer: str
    follow_up_text: str | None
    report_status: Literal["idle", "processing", "completed", "failed"]


class OrchestratorGraphState(TypedDict):
    state: InterviewState
    command: OrchestratorCommand


def build_orchestrator_graph(*, interview_runner: InterviewGraphRunner):
    graph = StateGraph(OrchestratorGraphState)
    graph.add_node("interview_phase", lambda payload: _run_interview_phase(payload, interview_runner))
    graph.add_node("review_phase", _run_review_phase)
    graph.add_conditional_edges(
        START,
        _route_command,
        {
            "interview_phase": "interview_phase",
            "review_phase": "review_phase",
        },
    )
    graph.add_edge("interview_phase", END)
    graph.add_edge("review_phase", END)
    return graph.compile()


def _route_command(payload: OrchestratorGraphState) -> str:
    if payload["command"]["kind"] == "sync_review":
        return "review_phase"
    return "interview_phase"
```

Append the node helpers in the same file:

```python
def _run_interview_phase(
    payload: OrchestratorGraphState,
    interview_runner: InterviewGraphRunner,
) -> OrchestratorGraphState:
    state = deepcopy(payload["state"])
    command = payload["command"]
    kind = command["kind"]

    if kind == "answer":
        next_state = interview_runner.submit_answer(state, command["answer"])
    elif kind == "prepare_stream":
        next_state = interview_runner.prepare_answer(state, command["answer"])
    elif kind == "complete_stream":
        next_state = interview_runner.finalize_prepared_answer(
            state,
            follow_up=command.get("follow_up_text"),
        )
    elif kind == "skip":
        from app.graphs.interview_transitions import skip_interview_question_state

        next_state = skip_interview_question_state(state)
    elif kind == "finish":
        from app.graphs.interview_transitions import finish_interview_state

        next_state = finish_interview_state(state)
    else:
        raise RuntimeError(f"unsupported orchestrator command: {kind}")

    if next_state["status"] == "finished":
        next_state["phase"] = "review"
        next_state["phase_status"] = "active"
        next_state["review_status"] = "processing"
    return {"state": next_state, "command": command}


def _run_review_phase(payload: OrchestratorGraphState) -> OrchestratorGraphState:
    state = deepcopy(payload["state"])
    report_status = payload["command"]["report_status"]
    state["phase"] = "review"
    state["review_status"] = report_status
    if report_status == "completed":
        state["phase_status"] = "completed"
    elif report_status == "failed":
        state["phase_status"] = "failed"
    else:
        state["phase_status"] = "active"
    return {"state": state, "command": payload["command"]}
```

- [ ] **Step 4: Move the shared finish/skip transition helpers out of the session store**

Create `app/graphs/interview_transitions.py`:

```python
from datetime import datetime, timezone

from app.graphs.interview_graph import INTERVIEW_FINISHED_MESSAGE
from app.graphs.interview_state import (
    InterviewState,
    count_candidate_answers_for_question,
    get_current_question,
    utc_now_iso,
)


def finish_interview_state(state: InterviewState) -> InterviewState:
    if state["status"] == "finished":
        return state

    _ensure_state_metadata(state)
    state["current_index"] = len(state["plan"].questions)
    state["decision"] = {
        "action": "finish",
        "follow_up": None,
        "reason": "user_finished_interview",
    }
    state["pending_output"] = INTERVIEW_FINISHED_MESSAGE
    state["status"] = "finished"
    if not _has_terminal_message(state):
        state["messages"].append(
            {
                "role": "interviewer",
                "content": INTERVIEW_FINISHED_MESSAGE,
                "question_id": None,
            }
        )
    state["finished_at"] = state["finished_at"] or utc_now_iso()
    return state


def skip_interview_question_state(state: InterviewState) -> InterviewState:
    if state["status"] == "finished":
        return state

    _ensure_state_metadata(state)
    _record_skip_if_unanswered(state)
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

def _elapsed_seconds(state: InterviewState) -> int:
    started = _parse_state_timestamp(state.get("started_at"))
    if started is None:
        return 0
    finished = _parse_state_timestamp(state.get("finished_at")) or datetime.now(timezone.utc)
    return max(0, int((finished - started).total_seconds()))


def _question_state(state: InterviewState, index: int) -> str:
    _ensure_state_metadata(state)
    question = state["plan"].questions[index]
    if question.id in state["skipped_question_ids"]:
        return "skipped"
    if count_candidate_answers_for_question(state, question.id) > 0:
        return "answered"
    if state["status"] == "finished":
        return "unanswered"
    if index == state["current_index"]:
        return "current"
    return "pending"


def _question_answer_counts(state: InterviewState) -> dict[str, int]:
    counts = {"answered": 0, "skipped": 0, "unanswered": 0, "pending_or_current": 0}
    for index, _ in enumerate(state["plan"].questions):
        value = _question_state(state, index)
        if value in ("answered", "skipped", "unanswered"):
            counts[value] += 1
        else:
            counts["unanswered"] += 1
            counts["pending_or_current"] += 1
    return counts


def _ensure_state_metadata(state: InterviewState) -> None:
    state.setdefault("skipped_question_ids", [])
    state.setdefault("started_at", utc_now_iso())
    state.setdefault("finished_at", None)


def _record_skip_if_unanswered(state: InterviewState) -> None:
    question = get_current_question(state)
    if question is None:
        return
    if count_candidate_answers_for_question(state, question.id) > 0:
        return
    if question.id not in state["skipped_question_ids"]:
        state["skipped_question_ids"].append(question.id)


def _parse_state_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _has_terminal_message(state: InterviewState) -> bool:
    return bool(
        state["messages"]
        and state["messages"][-1]["role"] == "interviewer"
        and state["messages"][-1]["content"] == INTERVIEW_FINISHED_MESSAGE
        and state["messages"][-1]["question_id"] is None
    )
```

- [ ] **Step 5: Add the explicit orchestration boundary and wire the session store through it**

Create `app/agents/orchestrator.py`:

```python
from app.graphs.interview_graph import InterviewGraphRunner
from app.graphs.interview_state import InterviewState
from app.graphs.orchestrator_graph import OrchestratorCommand, build_orchestrator_graph
from app.services.llm import InterviewLLM


class OrchestratorAgent:
    def __init__(
        self,
        *,
        llm: InterviewLLM | None = None,
        interview_runner: InterviewGraphRunner | None = None,
    ) -> None:
        self._interview_runner = interview_runner or InterviewGraphRunner(llm=llm)
        self._graph = build_orchestrator_graph(
            interview_runner=self._interview_runner,
        )

    def apply_command(
        self,
        state: InterviewState,
        command: OrchestratorCommand,
    ) -> InterviewState:
        result = self._graph.invoke({"state": state, "command": command})
        return result["state"]
```

Update `app/agents/__init__.py`:

```python
from app.agents.examiner import ExaminerAgent
from app.agents.knowledge import KnowledgeAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.report_coach import ReportCoachAgent
from app.agents.shadow_reviewer import ShadowReviewerAgent

__all__ = [
    "ExaminerAgent",
    "KnowledgeAgent",
    "OrchestratorAgent",
    "ReportCoachAgent",
    "ShadowReviewerAgent",
]
```

Update `app/services/session.py` constructor and command paths:

```python
from app.agents.orchestrator import OrchestratorAgent
from app.graphs.interview_transitions import (
    finish_interview_state,
    skip_interview_question_state,
    _elapsed_seconds,
    _ensure_state_metadata,
    _question_answer_counts,
    _question_state,
)
```

```python
class InterviewSessionStore:
    def __init__(self, llm: InterviewLLM | None = None) -> None:
        self._sessions: Dict[str, InterviewState] = {}
        self._reports: Dict[str, ReportRecord] = {}
        self._question_evaluations: Dict[str, list[QuestionEvaluationRecord]] = {}
        self._llm = llm
        self._runner = InterviewGraphRunner(llm=llm)
        self._orchestrator = OrchestratorAgent(
            llm=llm,
            interview_runner=self._runner,
        )
```

Then replace the state transitions and shared metadata helpers:

```python
        new_state = self._orchestrator.apply_command(
            state,
            {"kind": "answer", "answer": answer},
        )
```

```python
        prepared_state = self._orchestrator.apply_command(
            state,
            {"kind": "prepare_stream", "answer": answer},
        )
```

```python
        finalized_state = self._orchestrator.apply_command(
            prepared_state,
            {
                "kind": "complete_stream",
                "follow_up_text": follow_up_text,
            },
        )
```

```python
        skipped_state = self._orchestrator.apply_command(state, {"kind": "skip"})
```

```python
        finished_state = self._orchestrator.apply_command(state, {"kind": "finish"})
```

Also replace the local helper calls in `snapshot()`:

```python
        _ensure_state_metadata(state)
        current_question = None if state["status"] == "finished" else get_current_question(state)
```

```python
        answer_counts = _question_answer_counts(state)
```

```python
                "state": _question_state(state, index),
```

```python
            "elapsed_seconds": _elapsed_seconds(state),
```

Delete the now-duplicated local helpers from `app/services/session.py` after importing the shared versions:

- `_ensure_state_metadata`
- `_parse_state_timestamp`
- `_elapsed_seconds`
- `_record_skip_if_unanswered`
- `finish_interview_state`
- `skip_interview_question_state`
- `_question_answer_counts`
- `_question_state`
- `_has_terminal_message`
- `_build_followup_context`

- [ ] **Step 6: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_orchestrator_graph.py tests/test_session_service.py tests/test_interview_graph.py tests/test_api.py::test_answer_stream_publishes_round_closed_event_when_streamed_answer_closes_question -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/graphs/orchestrator_graph.py app/graphs/interview_transitions.py app/agents/orchestrator.py app/agents/__init__.py app/services/session.py tests/test_orchestrator_graph.py tests/test_api.py
git commit -m "feat: add langgraph orchestrator agent"
```

---

### Task 4: Add Versioned Commands And The HTTP Resume Contract

**Files:**
- Create: `app/services/session_errors.py`
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/ports/runtime.py`
- Modify: `app/api/routes.py`
- Test: `tests/test_session_service.py`
- Test: `tests/test_api.py`
- Test: `tests/test_postgres_session_store.py`

- [ ] **Step 1: Write the failing version-contract tests**

Append to `tests/test_session_service.py`:

```python
import pytest

from app.services.session_errors import SessionVersionConflict
```

```python
def test_submit_answer_rejects_stale_expected_version():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    with pytest.raises(SessionVersionConflict) as exc:
        store.submit_answer(
            session.session_id,
            "I used Redis.",
            expected_version=0,
            command_id="cmd-1",
        )

    assert exc.value.expected_version == 0
    assert exc.value.actual_version == 1


def test_submit_answer_is_idempotent_for_duplicate_command_id():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    first = store.submit_answer(
        session.session_id,
        "I used Redis.",
        expected_version=1,
        command_id="cmd-1",
    )
    duplicate = store.submit_answer(
        session.session_id,
        "I used Redis.",
        expected_version=1,
        command_id="cmd-1",
    )
    snapshot = store.snapshot(session.session_id)

    assert duplicate.follow_up == first.follow_up
    assert snapshot["state_version"] == 2
    assert len([m for m in snapshot["messages"] if m["role"] == "candidate"]) == 1
    assert snapshot["last_command_id"] == "cmd-1"
```

Append to `tests/test_api.py`:

```python
def test_get_interview_session_returns_resume_metadata():
    client = make_client()
    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    ).json()

    response = client.get(f"/api/interviews/{started['session_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["phase"] == "interview"
    assert body["phase_status"] == "active"
    assert body["review_status"] == "idle"
    assert body["state_version"] == 1
    assert body["checkpoint_version"] == 1
    assert body["last_checkpoint_at"]
    assert body["last_command_id"] is None


def test_answer_route_returns_409_for_version_conflict():
    client = make_client()
    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    ).json()

    response = client.post(
        f"/api/interviews/{started['session_id']}/answer",
        json={
            "answer": "I used Redis.",
            "expected_version": 0,
            "command_id": "cmd-1",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "session version conflict",
        "expected_version": 0,
        "actual_version": 1,
    }
```

Append to `tests/test_postgres_session_store.py`:

```python
def test_duplicate_command_id_is_idempotent_after_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    first = store.submit_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-1",
    )

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    duplicate = recovered.submit_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-1",
    )
    snapshot = recovered.snapshot(turn.session_id)

    assert duplicate.follow_up == first.follow_up
    assert snapshot["state_version"] == 2
    assert snapshot["checkpoint_version"] == 2
    assert snapshot["last_command_id"] == "cmd-1"
    assert len([m for m in snapshot["messages"] if m["role"] == "candidate"]) == 1
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py tests/test_api.py tests/test_postgres_session_store.py::test_duplicate_command_id_is_idempotent_after_store_reinstantiation -q
```

Expected: FAIL because the store and API do not yet understand `expected_version`, `command_id`, or version conflicts.

- [ ] **Step 3: Add a typed version-conflict error**

Create `app/services/session_errors.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionVersionConflict(Exception):
    expected_version: int
    actual_version: int

    def __str__(self) -> str:
        return (
            "session version conflict: "
            f"expected {self.expected_version}, actual {self.actual_version}"
        )
```

- [ ] **Step 4: Add optimistic-concurrency and idempotency helpers to the session store**

Update method signatures in `app/services/session.py`:

```python
    def submit_answer(
        self,
        session_id: str,
        answer: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
```

Apply the same keyword-only arguments to `prepare_streaming_answer()`, `skip()`, and `finish()`.

Add the helper functions near the bottom of `app/services/session.py`:

```python
from app.services.session_errors import SessionVersionConflict
```

```python
def _ensure_expected_version(
    state: InterviewState,
    expected_version: int | None,
) -> None:
    if expected_version is None:
        return
    if expected_version != state["state_version"]:
        raise SessionVersionConflict(
            expected_version=expected_version,
            actual_version=state["state_version"],
        )


def _is_duplicate_command(state: InterviewState, command_id: str | None) -> bool:
    return bool(command_id and state.get("last_command_id") == command_id)


def _advance_state_metadata(
    state: InterviewState,
    *,
    command_id: str | None,
) -> InterviewState:
    state["state_version"] += 1
    state["checkpoint_version"] = state["state_version"]
    state["last_checkpoint_at"] = utc_now_iso()
    state["last_command_id"] = command_id
    return state
```

Use them in `submit_answer()`:

```python
        state = self.get(session_id)
        if _is_duplicate_command(state, command_id):
            return self._to_turn(state, follow_up=_extract_follow_up(state))
        _ensure_expected_version(state, expected_version)
        new_state = self._orchestrator.apply_command(
            state,
            {"kind": "answer", "answer": answer},
        )
        new_state = _advance_state_metadata(new_state, command_id=command_id)
```

Apply the same pattern to `prepare_streaming_answer()`, `skip()`, `finish()`, and `complete_streaming_answer()`.

Extend `snapshot()` to return:

```python
            "phase": state["phase"],
            "phase_status": state["phase_status"],
            "review_status": state["review_status"],
            "state_version": state["state_version"],
            "checkpoint_version": state["checkpoint_version"],
            "last_checkpoint_at": state["last_checkpoint_at"],
            "last_command_id": state["last_command_id"],
```

- [ ] **Step 5: Extend the runtime protocol and API request models**

Update `app/ports/runtime.py` signatures to match the new store methods:

```python
    def submit_answer(
        self,
        session_id: str,
        answer: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
        ...
```

Apply the same keyword-only arguments to `prepare_streaming_answer()`, `skip()`, and `finish()`.

Update `app/api/routes.py` models:

```python
class AnswerRequest(BaseModel):
    answer: str
    expected_version: int | None = None
    command_id: str | None = None


class SessionCommandRequest(BaseModel):
    expected_version: int | None = None
    command_id: str | None = None
```

Wire them into the routes:

```python
        turn = store.submit_answer(
            session_id,
            payload.answer,
            expected_version=payload.expected_version,
            command_id=payload.command_id,
        )
```

```python
        prepared = store.prepare_streaming_answer(
            session_id,
            payload.answer,
            expected_version=payload.expected_version,
            command_id=payload.command_id,
        )
```

Change `finish_interview()` and `skip_interview_question()` to accept an optional body:

```python
def finish_interview(
    session_id: str,
    background_tasks: BackgroundTasks,
    payload: SessionCommandRequest | None = None,
    store: InterviewSessionStore = Depends(get_session_store),
    publisher=Depends(get_event_publisher),
):
    payload = payload or SessionCommandRequest()
```

Then pass `expected_version` and `command_id` into `store.finish()` / `store.skip()`.

Map version conflicts to `409`:

```python
from app.services.session_errors import SessionVersionConflict
```

```python
def _version_conflict_response(exc: SessionVersionConflict) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "session version conflict",
            "expected_version": exc.expected_version,
            "actual_version": exc.actual_version,
        },
    )
```

Catch it in the command routes:

```python
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
```

- [ ] **Step 6: Apply the same version/idempotency semantics to PostgreSQL-backed commands**

Update the method signatures in `app/services/postgres_session.py` to match the in-memory store:

```python
    def submit_answer(
        self,
        session_id: str,
        answer: str,
        *,
        expected_version: int | None = None,
        command_id: str | None = None,
    ) -> InterviewTurn:
```

Apply the same keyword-only arguments to `prepare_streaming_answer()`, `skip()`, and `finish()`.

Persist the Stage 29 version/idempotency metadata in the PostgreSQL session table in this same task so retries remain safe across process restarts. Add these columns in `_ensure_schema()`:

```python
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT 'interview'"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS phase_status TEXT NOT NULL DEFAULT 'active'"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'idle'"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS state_version INTEGER NOT NULL DEFAULT 1"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS checkpoint_version INTEGER NOT NULL DEFAULT 1"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS last_checkpoint_at TIMESTAMPTZ"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS last_command_id TEXT"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
```

Update the `SELECT` list in `get()` and `_session_row_from_db()` to round-trip these fields:

```python
                        SELECT session_id, plan_json, current_index, status,
                               phase, phase_status, review_status,
                               job_description, resume_text, job_tags,
                               decision_json, pending_output, skipped_question_ids,
                               started_at, finished_at, state_version,
                               checkpoint_version, last_checkpoint_at, last_command_id
                        FROM {sessions}
                        WHERE session_id = %s
```

Also update `_insert_state()` and `_replace_state()` so they write:

```python
phase, phase_status, review_status,
state_version, checkpoint_version, last_checkpoint_at, last_command_id
```

Mirror the in-memory logic at the start of each mutating method:

```python
        state = self.get(session_id)
        if _is_duplicate_command(state, command_id):
            return self._to_turn(state, follow_up=_extract_follow_up(state))
        _ensure_expected_version(state, expected_version)
```

Then advance the metadata before `_replace_state()`:

```python
        new_state = self._orchestrator.apply_command(
            state,
            {"kind": "answer", "answer": answer},
        )
        new_state = _advance_state_metadata(new_state, command_id=command_id)
        self._replace_state(new_state)
```

Import the shared helpers from `app.services.session` rather than duplicating them:

```python
from app.services.session import (
    InterviewSessionStore,
    InterviewTurn,
    PreparedInterviewTurn,
    _advance_state_metadata,
    _ensure_expected_version,
    _extract_follow_up,
    _is_duplicate_command,
)
```

Delete the now-duplicated `PostgresInterviewSessionStore._extract_follow_up()` staticmethod after switching callers to the shared helper from `app.services.session`.

Keep `complete_streaming_answer()` idempotent after persistence restart by advancing state metadata only when the streaming answer is first finalized, and keep the duplicate-command check ahead of the expected-version check so a replayed request with the original `expected_version` still succeeds.

- [ ] **Step 7: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py tests/test_api.py tests/test_postgres_session_store.py::test_duplicate_command_id_is_idempotent_after_store_reinstantiation -q
```

Expected: PASS, with the PostgreSQL-specific test allowed to skip when `POSTGRES_DSN` is unavailable.

- [ ] **Step 8: Commit**

```bash
git add app/services/session_errors.py app/services/session.py app/services/postgres_session.py app/ports/runtime.py app/api/routes.py tests/test_session_service.py tests/test_api.py tests/test_postgres_session_store.py
git commit -m "feat: add versioned session command contract"
```

---

### Task 5: Persist Review-Phase Metadata And Sync It With Report Lifecycle

**Files:**
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Test: `tests/test_session_service.py`
- Test: `tests/test_postgres_session_store.py`
- Test: `tests/test_report_tasks.py`

- [ ] **Step 1: Write the failing review-phase lifecycle tests**

Append to `tests/test_session_service.py`:

```python
def test_mark_report_processing_moves_session_into_review_phase():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)
    store.finish(session.session_id)

    assert store.mark_report_processing(session.session_id) is True

    snapshot = store.snapshot(session.session_id)
    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "active"
    assert snapshot["review_status"] == "processing"
    assert snapshot["state_version"] == 3
```

Append to `tests/test_postgres_session_store.py`:

```python
def test_phase_metadata_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    finish_session(store, turn.session_id)
    store.mark_report_processing(turn.session_id)

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered.snapshot(turn.session_id)

    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "active"
    assert snapshot["review_status"] == "processing"
    assert snapshot["state_version"] >= 4


def test_postgres_save_report_updates_review_phase_completed():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    finish_session(store, turn.session_id)
    store.mark_report_processing(turn.session_id)
    store.save_report(turn.session_id, make_report(turn.session_id))

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered.snapshot(turn.session_id)

    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "completed"
    assert snapshot["review_status"] == "completed"


def test_postgres_fail_report_updates_review_phase_failed():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )
    finish_session(store, turn.session_id)
    store.mark_report_processing(turn.session_id)
    store.fail_report(turn.session_id, "retrieval unavailable")

    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    snapshot = recovered.snapshot(turn.session_id)

    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "failed"
    assert snapshot["review_status"] == "failed"
```

Append to `tests/test_report_tasks.py`:

```python
def test_execute_report_generation_marks_review_phase_completed():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    store = InterviewSessionStore(llm=ReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    execute_report_generation(
        session_id=session.session_id,
        store=store,
        llm=ReportLLM(),
        vector_store=FakeVectorStore(),
    )

    snapshot = store.snapshot(session.session_id)
    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "completed"
    assert snapshot["review_status"] == "completed"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py tests/test_postgres_session_store.py tests/test_report_tasks.py -q
```

Expected: FAIL because report lifecycle methods do not yet update session phase/version metadata consistently across the in-memory and PostgreSQL stores.

- [ ] **Step 3: Update in-memory report lifecycle transitions**

In `app/services/session.py`, update `mark_report_processing()`:

```python
        if session_id in self._reports:
            return False
        state["phase"] = "review"
        state["phase_status"] = "active"
        state["review_status"] = "processing"
        self._sessions[session_id] = _advance_state_metadata(
            state,
            command_id=state.get("last_command_id"),
        )
        self._reports[session_id] = ReportRecord(
            status="processing",
            progress=ReportProgress(
                stage="retrieving",
                percent=20,
                message="Retrieving role-specific knowledge references.",
            ),
        )
```

Update `save_report()`:

```python
        state = self.get(session_id)
        state["phase"] = "review"
        state["phase_status"] = "completed"
        state["review_status"] = "completed"
        self._sessions[session_id] = _advance_state_metadata(
            state,
            command_id=state.get("last_command_id"),
        )
```

Update `fail_report()`:

```python
        state = self.get(session_id)
        state["phase"] = "review"
        state["phase_status"] = "failed"
        state["review_status"] = "failed"
        self._sessions[session_id] = _advance_state_metadata(
            state,
            command_id=state.get("last_command_id"),
        )
```

- [ ] **Step 4: Update PostgreSQL review-phase lifecycle methods to mirror the in-memory store**

The schema and base row persistence for `phase`, `phase_status`, `review_status`, `state_version`, `checkpoint_version`, `last_checkpoint_at`, and `last_command_id` were added in Task 4. In this task, keep the PostgreSQL report lifecycle behavior aligned with the in-memory store by updating the session state as well as the `ReportRecord`.

Replace `mark_report_processing()` in `app/services/postgres_session.py` with:

```python
    def mark_report_processing(self, session_id: str) -> bool:
        state = self.get(session_id)
        if state["status"] != "finished":
            raise ValueError("interview is not finished")
        if self.get_report_record(session_id) is not None:
            return False
        state["phase"] = "review"
        state["phase_status"] = "active"
        state["review_status"] = "processing"
        state = _advance_state_metadata(
            state,
            command_id=state.get("last_command_id"),
        )
        self._replace_state(state)
        self._upsert_report_record(
            session_id,
            ReportRecord(
                status="processing",
                progress=ReportProgress(
                    stage="retrieving",
                    percent=20,
                    message="Retrieving role-specific knowledge references.",
                ),
            ),
        )
        return True
```

Replace `save_report()` with:

```python
    def save_report(self, session_id: str, report: InterviewReport) -> None:
        state = self.get(session_id)
        existing = self.get_report_record(session_id)
        created_at = existing.created_at if existing is not None else report_utc_now_iso()
        state["phase"] = "review"
        state["phase_status"] = "completed"
        state["review_status"] = "completed"
        state = _advance_state_metadata(
            state,
            command_id=state.get("last_command_id"),
        )
        self._replace_state(state)
        self._upsert_report_record(
            session_id,
            ReportRecord(
                status="completed",
                report=report,
                created_at=created_at,
                finished_at=report_utc_now_iso(),
            ),
        )
```

Replace `fail_report()` with:

```python
    def fail_report(self, session_id: str, error: str) -> None:
        state = self.get(session_id)
        existing = self.get_report_record(session_id)
        created_at = existing.created_at if existing is not None else report_utc_now_iso()
        state["phase"] = "review"
        state["phase_status"] = "failed"
        state["review_status"] = "failed"
        state = _advance_state_metadata(
            state,
            command_id=state.get("last_command_id"),
        )
        self._replace_state(state)
        self._upsert_report_record(
            session_id,
            ReportRecord(
                status="failed",
                error=error,
                created_at=created_at,
                finished_at=report_utc_now_iso(),
            ),
        )
```

- [ ] **Step 5: Keep report generation behavior stable while syncing the richer session state**

No new public behavior is needed in `app/services/report_tasks.py`; keep the existing `store.save_report()` / `store.fail_report()` calls in place and let the session store own the review-phase transition. This step is verification-only and should not create a new diff in `app/services/report_tasks.py`.

Run the focused report task test after the store changes so `execute_report_generation()` proves the new phase metadata is being updated indirectly.

- [ ] **Step 6: Run the focused tests and verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_service.py tests/test_postgres_session_store.py tests/test_report_tasks.py -q
```

Expected: PASS, with PostgreSQL-specific tests allowed to skip when `POSTGRES_DSN` is unavailable.

- [ ] **Step 7: Commit**

```bash
git add app/services/session.py app/services/postgres_session.py tests/test_session_service.py tests/test_postgres_session_store.py tests/test_report_tasks.py
git commit -m "feat: persist review phase session metadata"
```

---

### Task 6: Document The Stage 29 Resume Contract And Verify The Full Runtime

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Test: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write the failing documentation regression**

Append to `tests/test_local_v1_docs.py`:

```python
def test_docs_describe_stage_29_orchestrator_resume_contract():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 29 adds a LangGraph-powered orchestrator and a versioned HTTP resume contract"
    assert expected in readme
    assert expected in runbook
    assert "expected_version" in runbook
    assert "command_id" in runbook
```

- [ ] **Step 2: Run the focused docs test and verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_29_orchestrator_resume_contract -q
```

Expected: FAIL because Stage 29 wording is not documented yet.

- [ ] **Step 3: Update the README**

Add this paragraph under `## Current Architecture Position` in `README.md` after the Stage 26A paragraph:

```markdown
Stage 29 adds a LangGraph-powered orchestrator and a versioned HTTP resume contract. The runtime now tracks explicit phase metadata (`interview` / `review`), persists `state_version` plus `checkpoint_version`, accepts `expected_version` and `command_id` on mutating interview commands, and uses `GET /api/interviews/{session_id}` as the HTTP resume handshake. Transport remains SSE plus polling in Local V1; Stage 29 still does not add WebSocket or Redis checkpoints.
```

- [ ] **Step 4: Update the runbook**

Add this paragraph under `## 1.1 Architecture Position` in `docs/local-v1-runbook.md`:

```markdown
Stage 29 adds a LangGraph-powered orchestrator and a versioned HTTP resume contract. Local verification should now treat `GET /api/interviews/{session_id}` as the resume handshake and should pass `expected_version` plus a caller-generated `command_id` when retry-safe command behavior needs to be validated.
```

Add this example near the API verification section:

````markdown
Example versioned answer request:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/interviews/<session_id>/answer" `
  -ContentType "application/json" `
  -Body '{"answer":"I used Redis cache-aside.","expected_version":1,"command_id":"cmd-001"}'
```
````

- [ ] **Step 5: Run the docs tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Run the Stage 29 focused verification sweep**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_boundary_api.py tests/test_interview_graph.py tests/test_interview_rounds.py tests/test_orchestrator_graph.py tests/test_session_serialization.py tests/test_session_service.py tests/test_postgres_session_store.py tests/test_api.py tests/test_report_tasks.py tests/test_local_v1_docs.py -q
```

Expected: PASS, with PostgreSQL tests allowed to skip when the fixture prerequisites are unavailable.

- [ ] **Step 7: Run the full repository sweep**

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
- All static JS syntax checks exit `0`.
- No frontend contract changes are needed beyond the new optional request fields and extra snapshot metadata.

- [ ] **Step 8: Commit**

```bash
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: describe stage 29 resume contract"
```

---

## Verification Sweep

After all six tasks are complete, run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_boundary_api.py tests/test_interview_graph.py tests/test_interview_rounds.py tests/test_orchestrator_graph.py tests/test_session_serialization.py tests/test_session_service.py tests/test_postgres_session_store.py tests/test_api.py tests/test_report_tasks.py tests/test_local_v1_docs.py -q
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected:

- The Stage 29 focused suite passes.
- Full `pytest` remains green.
- Existing four-page runtime JS files still parse cleanly.

## Self-Review

- Spec coverage: the plan covers the actual next gap between the architecture document and the current branch: explicit orchestration, phase-aware persisted state, optimistic concurrency, idempotent command replay, and review-phase synchronization.
- Placeholder scan: there are no `TODO`, `TBD`, or “same as previous task” placeholders; each task includes concrete files, tests, commands, and implementation snippets.
- Type consistency: the plan uses `phase`, `phase_status`, `review_status`, `state_version`, `checkpoint_version`, `last_checkpoint_at`, `last_command_id`, `expected_version`, and `command_id` consistently across state, store, API, and docs tasks.
