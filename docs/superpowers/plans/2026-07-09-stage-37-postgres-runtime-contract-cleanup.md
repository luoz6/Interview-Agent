# Stage 37 Postgres Runtime Contract Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing dirty runtime/orchestrator/Postgres changes into coherent, tested commits that make memory and Postgres session behavior share the same versioned command contract.

**Architecture:** Keep Local V1 transport as HTTP plus SSE/polling, but standardize the runtime contract around explicit phase metadata, `state_version`, Local V1 checkpoint metadata, `expected_version`, and `command_id`. Introduce the LangGraph orchestrator as an internal command router for interview/review phase transitions, then make `InterviewSessionStore` and `PostgresInterviewSessionStore` expose the same behavior and persistence semantics. Postgres command writes must guard the version at update time, not only before command execution.

**Tech Stack:** FastAPI, Pydantic, LangGraph, psycopg2/PostgreSQL, pytest, static ES modules.

---

## Current State And Constraints

- The worktree already contains dirty/untracked Stage 37 candidates:
  - `app/agents/orchestrator.py`
  - `app/graphs/interview_transitions.py`
  - `app/graphs/orchestrator_graph.py`
  - `app/services/session_errors.py`
  - modified `app/services/session.py`
  - modified `app/services/postgres_session.py`
  - modified `app/services/session_serialization.py`
  - modified `app/api/routes.py`
  - modified tests for API, session, Postgres, serialization, runtime boundary, and orchestrator graph.
- Do not revert unrelated dirty files. Stage 37 should selectively stage only files named in each task.
- `app/api/routes.py` has pre-existing unrelated route changes from the versioned resume contract. When committing, inspect staged diff before every commit.
- Stage 35 already committed `ReportProgress.metadata`; do not remove those fields.
- In Local V1, `checkpoint_version` intentionally mirrors `state_version`; it is exposed as a future checkpoint boundary but has no independent Redis/LangGraph checkpoint store yet. Documentation and tests must state this instead of implying a separate checkpoint timeline.
- User-facing `command_id` values are only for user commands. Internal report lifecycle updates must not reuse the previous user command id as `last_command_id`.
- `complete_streaming_answer()` is part of the answer mutation lifecycle. It must advance version metadata and have idempotency protection instead of silently mutating state without version movement.

## File Structure

- Create/keep `app/services/session_errors.py`: shared `SessionVersionConflict` exception.
- Create/keep `app/graphs/interview_transitions.py`: pure transition helpers for skip/finish, question state, elapsed time, metadata defaults.
- Create/keep `app/graphs/orchestrator_graph.py`: LangGraph command router for interview/review phase commands.
- Create/keep `app/agents/orchestrator.py`: small agent wrapper around the orchestrator graph.
- Modify `app/agents/__init__.py`: lazy-export `OrchestratorAgent` without eager imports.
- Modify `app/graphs/interview_state.py`: add phase/version/checkpoint fields to `InterviewState` and `build_initial_state()`.
- Modify `requirements.txt`: add `langgraph>=0.2.51`.
- Modify `app/services/session.py`: implement versioned command semantics for memory store.
- Modify `app/ports/runtime.py`: update repository protocol signatures.
- Modify `app/services/session_serialization.py`: round-trip phase/version/checkpoint metadata.
- Modify `app/services/postgres_session.py`: persist metadata columns and mirror memory-store command behavior.
- Modify `app/api/routes.py`: accept `expected_version`/`command_id` and return HTTP 409 for stale commands.
- Modify `tests/test_interview_graph.py`, `tests/test_orchestrator_graph.py`, `tests/test_session_service.py`, `tests/test_session_serialization.py`, `tests/test_postgres_session_store.py`, `tests/test_api.py`, `tests/test_runtime_boundary_api.py`.

---

### Task 1: Orchestrator And Phase Metadata Baseline

**Files:**
- Create: `app/graphs/interview_transitions.py`
- Create: `app/graphs/orchestrator_graph.py`
- Create: `app/agents/orchestrator.py`
- Modify: `app/agents/__init__.py`
- Modify: `app/graphs/interview_state.py`
- Modify: `requirements.txt`
- Test: `tests/test_interview_graph.py`
- Test: `tests/test_orchestrator_graph.py`

- [ ] **Step 1: Verify or add initial-state metadata test**

Ensure `tests/test_interview_graph.py` contains:

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

- [ ] **Step 2: Verify or create orchestrator graph tests**

Ensure `tests/test_orchestrator_graph.py` exists with these tests:

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

- [ ] **Step 3: Run tests before implementation**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_interview_graph.py tests/test_orchestrator_graph.py -q
```

Expected before implementation: tests fail if orchestrator files, metadata fields, or `langgraph` dependency are not available. If they already pass because dirty implementation exists, continue to Step 4 and inspect the staged diff before committing.

- [ ] **Step 4: Implement phase metadata in `app/graphs/interview_state.py`**

Update `InterviewState` with:

```python
phase: Literal["prep", "interview", "review"]
phase_status: Literal["pending", "active", "completed", "failed"]
review_status: Literal["idle", "processing", "completed", "failed"]
state_version: int
checkpoint_version: int
last_checkpoint_at: str | None
last_command_id: str | None
```

Update `build_initial_state()` return payload with:

```python
"phase": "interview",
"phase_status": "active" if first_question else "completed",
"review_status": "idle",
"state_version": 1,
"checkpoint_version": 1,
"last_checkpoint_at": now,
"last_command_id": None,
```

- [ ] **Step 5: Implement pure transition helpers**

Create `app/graphs/interview_transitions.py` with the transition helpers currently present in the dirty worktree:

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
    state.setdefault("phase", "interview")
    state.setdefault("phase_status", "active" if state["status"] == "active" else "completed")
    state.setdefault("review_status", "idle")
    state.setdefault("skipped_question_ids", [])
    state.setdefault("started_at", utc_now_iso())
    state.setdefault("finished_at", None)
    state.setdefault("state_version", 1)
    state.setdefault("checkpoint_version", state["state_version"])
    state.setdefault("last_checkpoint_at", state["started_at"])
    state.setdefault("last_command_id", None)


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

- [ ] **Step 6: Implement LangGraph orchestrator boundary**

Create `app/graphs/orchestrator_graph.py` and `app/agents/orchestrator.py` using the current dirty implementation. Keep `OrchestratorAgent.apply_command(state, command)` as the only public wrapper method. Add `langgraph>=0.2.51` to `requirements.txt`.

- [ ] **Step 7: Lazy-export `OrchestratorAgent`**

Update `app/agents/__init__.py` to include `"OrchestratorAgent"` in `__all__` and to lazily import agent classes through `__getattr__`. This prevents importing LangGraph-heavy modules when callers only need existing agents.

- [ ] **Step 8: Run Task 1 tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_interview_graph.py tests/test_orchestrator_graph.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

Stage only:

```powershell
git add app/graphs/interview_state.py app/graphs/interview_transitions.py app/graphs/orchestrator_graph.py app/agents/orchestrator.py app/agents/__init__.py requirements.txt tests/test_interview_graph.py tests/test_orchestrator_graph.py
git commit -m "feat: add orchestrator phase contract"
```

---

### Task 2: Memory Store Versioned Command Contract

**Files:**
- Create: `app/services/session_errors.py`
- Modify: `app/services/session.py`
- Modify: `app/ports/runtime.py`
- Test: `tests/test_session_service.py`

- [ ] **Step 1: Verify or add memory-store version tests**

Ensure `tests/test_session_service.py` imports `pytest` and `SessionVersionConflict`, then contains:

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

Also add streaming completion and internal report lifecycle tests. `complete_streaming_answer()` must advance versions, but it must preserve the last user command id so a duplicate user streaming command can still be detected:

```python
def test_complete_streaming_answer_advances_version_without_replacing_user_command_id():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.prepare_streaming_answer(
        session.session_id,
        "I used Redis.",
        expected_version=1,
        command_id="cmd-stream",
    )
    finalized = store.complete_streaming_answer(
        session.session_id,
        follow_up_text="Please explain cache invalidation.",
        expected_version=2,
        command_id="cmd-stream",
    )
    snapshot = store.snapshot(session.session_id)

    assert finalized["state_version"] == 3
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-stream"
    assert snapshot["messages"][-1]["role"] == "interviewer"
    assert snapshot["messages"][-1]["content"] == "Please explain cache invalidation."


def test_complete_streaming_answer_is_structurally_idempotent_after_finalization():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.prepare_streaming_answer(
        session.session_id,
        "I used Redis.",
        expected_version=1,
        command_id="cmd-stream",
    )
    first = store.complete_streaming_answer(
        session.session_id,
        follow_up_text="Please explain cache invalidation.",
        expected_version=2,
        command_id="cmd-stream",
    )
    duplicate = store.complete_streaming_answer(
        session.session_id,
        follow_up_text="Please explain cache invalidation.",
        expected_version=2,
        command_id="cmd-stream",
    )
    snapshot = store.snapshot(session.session_id)

    assert duplicate == first
    assert snapshot["state_version"] == 3
    assert len(
        [
            message
            for message in snapshot["messages"]
            if message["role"] == "interviewer"
            and message["content"] == "Please explain cache invalidation."
        ]
    ) == 1
```

Also ensure the review phase tests exist:

```python
def test_mark_report_processing_moves_session_into_review_phase():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)
    store.finish(session.session_id, expected_version=1, command_id="cmd-finish")

    assert store.mark_report_processing(session.session_id) is True

    snapshot = store.snapshot(session.session_id)
    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "active"
    assert snapshot["review_status"] == "processing"
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-finish"
```

This test documents that report lifecycle mutations advance the session version but do not reuse the previous user command id as a new internal command id. `last_command_id` remains the last user-supplied command id.

- [ ] **Step 2: Run memory-store tests before implementation**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_session_service.py -q
```

Expected before implementation: FAIL if `SessionVersionConflict`, version fields, or orchestrator-backed commands are absent. If dirty implementation already passes, continue to Step 3 and verify staged diff.

- [ ] **Step 3: Add shared version conflict exception**

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

- [ ] **Step 4: Update `InterviewSessionStore` command methods**

In `app/services/session.py`, make these user command methods accept keyword-only `expected_version` and `command_id`:

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

Use this sequence in `submit_answer`, `finish`, `skip`, and `prepare_streaming_answer`:

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
self._sessions[session_id] = new_state
```

Use the matching command payload for each method:

```python
{"kind": "finish"}
{"kind": "skip"}
{"kind": "prepare_stream", "answer": answer}
{"kind": "complete_stream", "follow_up_text": follow_up_text}
```

Then update `complete_streaming_answer()` as part of the same contract:

```python
def complete_streaming_answer(
    self,
    session_id: str,
    *,
    follow_up_text: str | None = None,
    expected_version: int | None = None,
    command_id: str | None = None,
) -> InterviewState:
```

Use structural idempotency first so a retry after finalization returns the current state instead of raising on a stale `expected_version`:

```python
state = self.get(session_id)
if _already_finalized_streaming_answer(state):
    return state
_ensure_expected_version(state, expected_version)
finalized_state = self._orchestrator.apply_command(
    state,
    {"kind": "complete_stream", "follow_up_text": follow_up_text},
)
finalized_state = _advance_state_metadata(
    finalized_state,
    command_id=command_id,
    record_command_id=False,
)
self._sessions[session_id] = finalized_state
return finalized_state
```

Do not record the completion as `last_command_id`; the client-visible command id belongs to the original streaming answer command recorded by `prepare_streaming_answer()`.

- [ ] **Step 5: Add session metadata helpers**

Add these helpers near the bottom of `app/services/session.py`:

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
    record_command_id: bool = True,
) -> InterviewState:
    state["state_version"] += 1
    # Local V1 stores checkpoints inline, so checkpoint_version mirrors
    # state_version until an external checkpoint store exists.
    state["checkpoint_version"] = state["state_version"]
    state["last_checkpoint_at"] = utc_now_iso()
    if record_command_id:
        state["last_command_id"] = command_id
    return state
```

- [ ] **Step 6: Update `snapshot()` and report phase transitions**

Ensure `snapshot()` returns:

```python
"phase": state["phase"],
"phase_status": state["phase_status"],
"review_status": state["review_status"],
"state_version": state["state_version"],
"checkpoint_version": state["checkpoint_version"],
"last_checkpoint_at": state["last_checkpoint_at"],
"last_command_id": state["last_command_id"],
```

Ensure `mark_report_processing()`, `save_report()`, and `fail_report()` update phase fields and call:

```python
_advance_state_metadata(state, command_id=None, record_command_id=False)
```

These internal report lifecycle writes must advance `state_version`/`checkpoint_version` without replacing the last user command id.

- [ ] **Step 7: Update runtime protocol signatures**

In `app/ports/runtime.py`, update `submit_answer`, `prepare_streaming_answer`, `complete_streaming_answer`, `skip`, and `finish` to accept:

```python
*,
expected_version: int | None = None,
command_id: str | None = None,
```

- [ ] **Step 8: Run Task 2 tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_session_service.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 2**

```powershell
git add app/services/session_errors.py app/services/session.py app/ports/runtime.py tests/test_session_service.py
git commit -m "feat: add versioned session commands"
```

---

### Task 3: Serialization And Postgres Runtime Contract

**Files:**
- Modify: `app/services/session_serialization.py`
- Modify: `app/services/postgres_session.py`
- Test: `tests/test_session_serialization.py`
- Test: `tests/test_postgres_session_store.py`

- [ ] **Step 1: Verify or add serialization metadata test**

Ensure `tests/test_session_serialization.py` contains:

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

- [ ] **Step 2: Verify or add Postgres contract tests**

Ensure `tests/test_postgres_session_store.py` contains tests for duplicate command id, review phase processing, review completion, and review failure:

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

Also ensure phase tests assert `phase`, `phase_status`, `review_status`, `state_version`, `checkpoint_version`, and `last_command_id` after `mark_report_processing()`, `save_report()`, and `fail_report()`. The report lifecycle methods should preserve the last user command id while advancing the version.

Add a Postgres streaming completion contract test:

```python
def test_complete_streaming_answer_advances_version_after_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    store.prepare_streaming_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-stream",
    )
    recovered = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    finalized = recovered.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Please describe the API boundaries.",
        expected_version=2,
        command_id="cmd-stream",
    )
    duplicate = recovered.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Please describe the API boundaries.",
        expected_version=2,
        command_id="cmd-stream",
    )
    snapshot = recovered.snapshot(turn.session_id)

    assert duplicate == finalized
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-stream"
    assert len(
        [
            message
            for message in snapshot["messages"]
            if message["role"] == "interviewer"
            and message["content"] == "Please describe the API boundaries."
        ]
    ) == 1
```

Add a Postgres stale-write guard test for the private persistence boundary. This protects against the TOCTOU case where two requests both pass the in-memory version check before one overwrites the other:

```python
def test_replace_state_rejects_stale_previous_version():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    stale_state = store.get(turn.session_id)
    store.submit_answer(
        turn.session_id,
        "I built a FastAPI API.",
        expected_version=1,
        command_id="cmd-1",
    )
    stale_state["messages"].append(
        {
            "role": "candidate",
            "content": "This stale write must not win.",
            "question_id": "q1",
        }
    )
    stale_state["state_version"] = 2
    stale_state["checkpoint_version"] = 2

    with pytest.raises(SessionVersionConflict) as exc:
        store._replace_state(stale_state, expected_previous_version=1)

    assert exc.value.expected_version == 1
    assert exc.value.actual_version == 2
```

- [ ] **Step 3: Run tests before implementation**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_session_serialization.py tests/test_postgres_session_store.py -q
```

Expected: PASS if dirty implementation is complete, SKIP Postgres tests if no DSN is configured, or FAIL if metadata columns/serialization are incomplete. A failure must be fixed before committing.

- [ ] **Step 4: Update session serialization**

In `app/services/session_serialization.py`, `session_row_from_state()` must include:

```python
"phase": state["phase"],
"phase_status": state["phase_status"],
"review_status": state["review_status"],
"state_version": state["state_version"],
"checkpoint_version": state["checkpoint_version"],
"last_checkpoint_at": state.get("last_checkpoint_at"),
"last_command_id": state.get("last_command_id"),
```

In `state_from_rows()`, restore with defaults:

```python
"phase": session_row.get("phase", "interview"),
"phase_status": session_row.get("phase_status", "active"),
"review_status": session_row.get("review_status", "idle"),
"state_version": int(session_row.get("state_version", 1)),
"checkpoint_version": int(session_row.get("checkpoint_version", 1)),
"last_checkpoint_at": session_row.get("last_checkpoint_at"),
"last_command_id": session_row.get("last_command_id"),
```

- [ ] **Step 5: Update Postgres schema and row mapping**

In `app/services/postgres_session.py`, the session table must add these columns using the existing migration style with `ADD COLUMN IF NOT EXISTS`:

```sql
phase TEXT NOT NULL DEFAULT 'interview'
phase_status TEXT NOT NULL DEFAULT 'active'
review_status TEXT NOT NULL DEFAULT 'idle'
state_version INTEGER NOT NULL DEFAULT 1
checkpoint_version INTEGER NOT NULL DEFAULT 1
last_checkpoint_at TIMESTAMPTZ
last_command_id TEXT
```

Update SELECT, INSERT, UPDATE, and `_session_row_from_db()` so these fields are always round-tripped.

- [ ] **Step 6: Mirror memory command behavior in Postgres store**

In `PostgresInterviewSessionStore`, update `submit_answer`, `prepare_streaming_answer`, `finish`, and `skip` to use:

```python
if _is_duplicate_command(state, command_id):
    return self._to_turn(state, follow_up=_extract_follow_up(state))
_ensure_expected_version(state, expected_version)
previous_version = state["state_version"]
updated_state = self._orchestrator.apply_command(state, command)
updated_state = _advance_state_metadata(updated_state, command_id=command_id)
self._replace_state(updated_state, expected_previous_version=previous_version)
```

Update `complete_streaming_answer()` with the same signature and semantics as the memory store:

```python
state = self.get(session_id)
if _already_finalized_streaming_answer(state):
    return state
_ensure_expected_version(state, expected_version)
previous_version = state["state_version"]
updated_state = self._orchestrator.apply_command(
    state,
    {"kind": "complete_stream", "follow_up_text": follow_up_text},
)
updated_state = _advance_state_metadata(
    updated_state,
    command_id=command_id,
    record_command_id=False,
)
self._replace_state(updated_state, expected_previous_version=previous_version)
return updated_state
```

Update `mark_report_processing()`, `save_report()`, and `fail_report()` to mutate phase fields, call `_advance_state_metadata(..., command_id=None, record_command_id=False)`, and persist the updated state before writing the report record.

- [ ] **Step 7: Make Postgres state replacement atomic**

Change `_replace_state()` so command methods pass the version they read before computing the update:

```python
def _replace_state(
    self,
    state: InterviewState,
    *,
    expected_previous_version: int | None = None,
) -> None:
```

When `expected_previous_version` is provided, the session row update must include a version predicate:

```sql
UPDATE {sessions_table}
SET <the same full session column assignments already used by _replace_state>
WHERE session_id = %s AND state_version = %s
```

After executing the update, if `cursor.rowcount == 0`, read the current state and raise:

```python
raise SessionVersionConflict(
    expected_version=expected_previous_version,
    actual_version=current_state["state_version"],
)
```

This check is required even though the method already called `_ensure_expected_version()` before applying the command. The pre-check gives a clean local error; the update-time predicate prevents a concurrent request from overwriting an intervening write.

- [ ] **Step 8: Run Task 3 tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_session_serialization.py tests/test_postgres_session_store.py -q
```

Expected: PASS or Postgres-specific SKIP when no DSN is available.

- [ ] **Step 9: Commit Task 3**

```powershell
git add app/services/session_serialization.py app/services/postgres_session.py tests/test_session_serialization.py tests/test_postgres_session_store.py
git commit -m "feat: persist versioned session metadata"
```

---

### Task 4: API And Runtime Boundary Contract

**Files:**
- Modify: `app/api/routes.py`
- Test: `tests/test_api.py`
- Test: `tests/test_runtime_boundary_api.py`

- [ ] **Step 1: Verify or add API 409 test**

Ensure `tests/test_api.py` contains:

```python
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

Also ensure `test_get_interview_session_returns_resume_metadata()` asserts `phase`, `state_version`, `checkpoint_version`, and `last_command_id`.

- [ ] **Step 2: Verify runtime-boundary expectations**

Ensure `tests/test_runtime_boundary_api.py` asserts:

```python
assert body["capabilities"]["langgraph"] is True
assert body["orchestration"] == {
    "engine": "langgraph",
    "phase_aware": True,
    "resume_contract": "versioned_http",
}
```

For `INTERVIEW_EVENT_BACKEND=celery` and `noop`, assert `body["capabilities"]["langgraph"] is True`.

- [ ] **Step 3: Run API tests before implementation**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_api.py tests/test_runtime_boundary_api.py -q
```

Expected: FAIL if route payloads or runtime-boundary response are not implemented. If dirty implementation already passes, continue to Step 4 and inspect staged diff.

- [ ] **Step 4: Update route request models and 409 response**

In `app/api/routes.py`, update request models:

```python
class AnswerRequest(BaseModel):
    answer: str
    expected_version: int | None = None
    command_id: str | None = None


class SessionCommandRequest(BaseModel):
    expected_version: int | None = None
    command_id: str | None = None
```

Import:

```python
from app.services.session_errors import SessionVersionConflict
```

Add helper:

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

- [ ] **Step 5: Pass version payloads to store methods**

In answer, stream answer, skip, and finish routes, pass:

```python
expected_version=payload.expected_version,
command_id=payload.command_id,
```

Catch version conflicts before generic `ValueError`:

```python
except SessionVersionConflict as exc:
    return _version_conflict_response(exc)
```

For skip/finish, accept optional body:

```python
payload: SessionCommandRequest | None = None
payload = payload or SessionCommandRequest()
```

For the stream answer route, apply the version contract to both halves of the lifecycle:

1. `prepare_streaming_answer()` uses the client-provided `expected_version` and `command_id`.
2. `complete_streaming_answer()` uses the version returned after prepare and the same user `command_id`, but the store must preserve `last_command_id` rather than record completion as a new command.

The completion call should be shaped like:

```python
prepared_state = store.snapshot(session_id)
final_state = store.complete_streaming_answer(
    session_id,
    follow_up_text=streamed_text,
    expected_version=prepared_state["state_version"],
    command_id=payload.command_id,
)
```

If `prepare_streaming_answer()` returns an already-finalized duplicate command, do not append or stream a second interviewer follow-up. Return the current snapshot/turn data consistently with the existing SSE contract.

- [ ] **Step 6: Update runtime-boundary response**

In `runtime_boundary()`, set:

```python
"langgraph": True,
```

and add:

```python
"orchestration": {
    "engine": "langgraph",
    "phase_aware": True,
    "resume_contract": "versioned_http",
},
```

- [ ] **Step 7: Run Task 4 tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_api.py tests/test_runtime_boundary_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 4**

`app/api/routes.py` has had unrelated staged-risk changes in previous work. Before committing, run `git diff --cached -- app/api/routes.py` and confirm only versioned command/runtime-boundary hunks are staged.

```powershell
git add app/api/routes.py tests/test_api.py tests/test_runtime_boundary_api.py
git diff --cached -- app/api/routes.py
git commit -m "feat: expose versioned command api contract"
```

---

### Task 5: Documentation, Dirty Worktree Audit, And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `tests/test_local_v1_docs.py`
- No commit for unrelated `.idea`, `.claude`, experimental specs, or plan files unless explicitly required.

- [ ] **Step 1: Add docs test for Stage 37**

Add this test to `tests/test_local_v1_docs.py` after the Stage 35 docs test:

```python
def test_docs_describe_stage_37_postgres_runtime_contract_cleanup():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 37 cleans up the Postgres runtime contract"
    assert expected in readme
    assert expected in runbook
    assert "SessionVersionConflict" in readme
    assert "expected_version" in readme
    assert "command_id" in readme
    assert "state_version" in runbook
    assert "checkpoint_version" in runbook
    assert "checkpoint_version` mirrors `state_version" in runbook
    assert "last user command id" in runbook
    assert "phase_status" in runbook
```

- [ ] **Step 2: Run docs test before docs update**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_37_postgres_runtime_contract_cleanup -q
```

Expected: FAIL because Stage 37 docs do not exist yet.

- [ ] **Step 3: Update README**

Add this paragraph after the Stage 35 paragraph:

```markdown
Stage 37 cleans up the Postgres runtime contract. Memory and Postgres session stores now share the same versioned command behavior: mutating user commands accept `expected_version` plus `command_id`, stale commands raise `SessionVersionConflict` and return HTTP 409, duplicate `command_id` calls are idempotent, and snapshots expose `state_version`, `checkpoint_version`, `phase`, `phase_status`, and `review_status`. Streaming answer completion and report lifecycle updates advance version metadata without replacing the last user command id. The LangGraph orchestrator remains an internal phase router; Local V1 transport is still HTTP/SSE/polling.
```

- [ ] **Step 4: Update runbook**

Add this paragraph after the Stage 35 paragraph:

```markdown
Stage 37 cleans up the Postgres runtime contract. Local verification should compare memory and Postgres behavior for `expected_version`, `command_id`, `state_version`, `checkpoint_version`, `phase_status`, and `review_status`. In Local V1, `checkpoint_version` mirrors `state_version` until an external checkpoint store exists. `last_command_id` is the last user command id; streaming completion and report lifecycle updates advance version metadata without overwriting it. A stale command should return HTTP 409 with the actual version, a duplicate command id should not append duplicate candidate messages, and service restart checks should confirm Postgres preserves version and phase metadata.
```

Add this checklist after the Stage 35 checklist:

```markdown
Stage 37 Postgres runtime contract checks:

1. Start an interview and call `GET /api/interviews/{session_id}`.
2. Confirm the snapshot includes `state_version`, `checkpoint_version`, `phase`, `phase_status`, and `review_status`.
3. Send an answer with stale `expected_version` and confirm HTTP 409.
4. Send the same `command_id` twice and confirm the second call does not duplicate candidate messages.
5. Submit a streaming answer and confirm completion advances `state_version` while preserving the original `last_command_id`.
6. Finish an interview, trigger report processing, and confirm report lifecycle updates do not replace the last user command id.
7. Repeat the version/idempotency checks with `INTERVIEW_RUNTIME_STORE=postgres`.
8. Restart the store or process and confirm Postgres still returns the latest version and phase metadata.
```

- [ ] **Step 5: Run docs tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit docs**

```powershell
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py
git commit -m "docs: describe postgres runtime contract cleanup"
```

- [ ] **Step 7: Run focused Stage 37 tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_interview_graph.py tests/test_orchestrator_graph.py tests/test_session_service.py tests/test_session_serialization.py tests/test_api.py tests/test_runtime_boundary_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Run Postgres-focused tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_postgres_session_store.py -q
```

Expected: PASS when local Postgres DSN is available; otherwise SKIP only for DSN-gated tests.

- [ ] **Step 9: Run related regression tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_tasks.py tests/test_report_api.py tests/test_report_worker.py tests/test_static_report_ui.py tests/test_runtime_provider.py -q
```

Expected: PASS.

- [ ] **Step 10: Run full pytest and static JS syntax checks**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: pytest PASS and all Node checks exit 0.

- [ ] **Step 11: Audit remaining dirty worktree**

Run:

```powershell
git status --short
git log --oneline -12
```

Expected after Stage 37 commits:

- Runtime/session/Postgres contract files are no longer dirty.
- Remaining dirty/untracked files, if any, are unrelated editor files, plan/spec drafts, or future-stage experiments.
- Do not delete or revert remaining files unless the user explicitly asks.

---

## Self-Review

- Spec coverage: The plan covers dirty worktree cleanup, orchestrator phase metadata, memory session versioning, streaming completion version advancement, internal report lifecycle metadata, Postgres atomic version-guarded persistence, API 409 behavior, runtime-boundary documentation, docs, and final verification.
- Marker scan: No task contains fill-in markers or unspecified implementation steps.
- Type consistency: `SessionVersionConflict`, `expected_version`, `command_id`, `state_version`, `checkpoint_version`, `last_checkpoint_at`, `last_command_id`, `phase`, `phase_status`, and `review_status` are used consistently across store, API, serialization, Postgres, tests, and docs. The plan explicitly states that Local V1 `checkpoint_version` mirrors `state_version`, and that `last_command_id` tracks the last user command id rather than internal lifecycle writes.
