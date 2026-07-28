# Stage 13 Skipped Question Session Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make skipped and unanswered questions explicit in session snapshots, report generation, UI, and PDF output for local-only interview usage.

**Architecture:** Store skip and timing metadata on `InterviewState`, serialize it through memory and Postgres stores, and derive question state from actual candidate answers plus `skipped_question_ids`. Report generation keeps the LLM for answered questions but deterministically overrides skipped/unanswered feedback so reports do not invent evaluations for missing answers.

**Tech Stack:** FastAPI, Pydantic, in-memory session store, PostgreSQL session store, vanilla HTML/CSS/JS, pytest, reportlab.

---

## File Structure

- Modify `app/graphs/interview_state.py`: add timing fields and `skipped_question_ids` to the typed state and initial-state builder.
- Modify `app/services/session.py`: mark skipped questions, compute answered/skipped/unanswered snapshot states, expose timing counters.
- Modify `app/services/session_serialization.py`: preserve new state fields across Postgres row serialization.
- Modify `app/services/postgres_session.py`: add JSONB/timestamp columns and make old local tables upgrade in place.
- Modify `app/services/report.py`: add explicit `answer_state` to `InterviewFeedback` with a backward-compatible default.
- Modify `app/services/evaluator.py`: add `answer_state` to evaluation items and override skipped/unanswered feedback deterministically.
- Modify `app/services/report_provider_adapter.py`: make provider fallback `user_answer` understand `answer_state`.
- Modify `app/services/report_pdf.py`: render skipped/unanswered feedback using `feedback.answer_state`.
- Modify `app/static/app.js`: render `answered`, `skipped`, and `unanswered` question states and use backend timing when present.
- Modify `app/static/styles.css`: add visual states for skipped/unanswered questions.
- Modify tests in `tests/test_session_service.py`, `tests/test_api.py`, `tests/test_session_serialization.py`, `tests/test_postgres_session_store.py`, `tests/test_report_evaluator.py`, `tests/test_report_provider_adapter.py`, `tests/test_report_pdf.py`, and `tests/test_static_report_ui.py`.

---

### Task 1: Add Session State Metadata Tests

**Files:**
- Modify: `tests/test_session_service.py`
- Modify: `tests/test_api.py`
- Test: `tests/test_session_service.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing memory-store tests for timing and skip states**

Add these tests to `tests/test_session_service.py`:

```python
def test_start_session_records_timing_and_empty_skip_list():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = start_session(store)

    state = store.get(session.session_id)
    assert state["started_at"]
    assert state["finished_at"] is None
    assert state["skipped_question_ids"] == []


def test_skip_unanswered_question_marks_snapshot_skipped():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.skip(session.session_id)

    state = store.get(session.session_id)
    snapshot = store.snapshot(session.session_id)
    assert state["skipped_question_ids"] == ["q1"]
    assert snapshot["completed_questions"] == 1
    assert snapshot["answered_questions"] == 0
    assert snapshot["skipped_questions"] == 1
    assert snapshot["unanswered_questions"] == 2
    assert snapshot["questions"][0]["state"] == "skipped"
    assert snapshot["questions"][1]["state"] == "current"


def test_skip_after_answer_does_not_mark_question_skipped():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(session.session_id, "I built a Redis cache service.")
    store.skip(session.session_id)

    snapshot = store.snapshot(session.session_id)
    assert store.get(session.session_id)["skipped_question_ids"] == []
    assert snapshot["answered_questions"] == 1
    assert snapshot["skipped_questions"] == 0
    assert snapshot["questions"][0]["state"] == "answered"
    assert snapshot["questions"][1]["state"] == "current"


def test_skip_last_unanswered_question_records_skip_before_finish():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.skip(session.session_id)
    store.skip(session.session_id)
    final_turn = store.skip(session.session_id)

    state = store.get(session.session_id)
    snapshot = store.snapshot(session.session_id)
    assert final_turn.status == "finished"
    assert state["skipped_question_ids"] == ["q1", "q2", "q3"]
    assert state["finished_at"]
    assert snapshot["completed_questions"] == 3
    assert snapshot["answered_questions"] == 0
    assert snapshot["skipped_questions"] == 3
    assert snapshot["unanswered_questions"] == 0
    assert [question["state"] for question in snapshot["questions"]] == [
        "skipped",
        "skipped",
        "skipped",
    ]


def test_finish_without_answer_marks_remaining_questions_unanswered():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.finish(session.session_id)

    snapshot = store.snapshot(session.session_id)
    assert snapshot["status"] == "finished"
    assert snapshot["completed_questions"] == 0
    assert snapshot["answered_questions"] == 0
    assert snapshot["skipped_questions"] == 0
    assert snapshot["unanswered_questions"] == 3
    assert [question["state"] for question in snapshot["questions"]] == [
        "unanswered",
        "unanswered",
        "unanswered",
    ]
```

- [ ] **Step 2: Update existing service snapshot tests to new state names**

In `tests/test_session_service.py`, replace assertions expecting `"completed"` for answered questions with `"answered"`:

```python
assert snapshot["questions"][0]["state"] == "answered"
```

For `test_session_snapshot_marks_finished_session_without_current_question`, replace the old completed-all assertion with:

```python
assert snapshot["completed_questions"] == 0
assert snapshot["unanswered_questions"] == 3
assert [question["state"] for question in snapshot["questions"]] == [
    "unanswered",
    "unanswered",
    "unanswered",
]
```

- [ ] **Step 3: Write failing API snapshot test coverage**

Add this to `tests/test_api.py`:

```python
def test_get_interview_session_returns_skip_and_timing_metadata():
    client = make_client()
    start_response = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role using Python and Redis.",
            "resume_text": "Built a Python API with Redis.",
        },
    )
    session_id = start_response.json()["session_id"]

    client.post(f"/api/interviews/{session_id}/skip")
    response = client.get(f"/api/interviews/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["started_at"]
    assert body["finished_at"] is None
    assert isinstance(body["elapsed_seconds"], int)
    assert body["answered_questions"] == 0
    assert body["skipped_questions"] == 1
    assert body["unanswered_questions"] == 2
    assert body["questions"][0]["state"] == "skipped"
    assert body["questions"][1]["state"] == "current"
```

- [ ] **Step 4: Run tests to verify failure**

Run:

```bash
pytest tests/test_session_service.py tests/test_api.py -q
```

Expected: FAIL with missing `started_at`, `skipped_question_ids`, or new snapshot keys.

---

### Task 2: Implement State Metadata in Memory Store

**Files:**
- Modify: `app/graphs/interview_state.py`
- Modify: `app/services/session.py`
- Test: `tests/test_session_service.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add state fields and UTC helper**

In `app/graphs/interview_state.py`, add imports:

```python
from datetime import datetime, timezone
```

Extend `InterviewState`:

```python
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
    skipped_question_ids: list[str]
    started_at: str
    finished_at: str | None
```

Add helper:

```python
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
```

Update `build_initial_state()` return value:

```python
    now = utc_now_iso()
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
        "skipped_question_ids": [],
        "started_at": now,
        "finished_at": now if first_question is None else None,
    }
```

- [ ] **Step 2: Implement compatibility helpers in session service**

In `app/services/session.py`, import:

```python
from datetime import datetime, timezone
from app.graphs.interview_state import (
    InterviewState,
    count_candidate_answers_for_question,
    get_current_question,
    utc_now_iso,
)
```

Add helpers near `_extract_follow_up`:

```python
def _ensure_state_metadata(state: InterviewState) -> None:
    state.setdefault("skipped_question_ids", [])
    state.setdefault("started_at", utc_now_iso())
    state.setdefault("finished_at", None)


def _parse_state_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _elapsed_seconds(state: InterviewState) -> int:
    started = _parse_state_timestamp(state.get("started_at"))
    if started is None:
        return 0
    finished = _parse_state_timestamp(state.get("finished_at")) or datetime.now(timezone.utc)
    return max(0, int((finished - started).total_seconds()))


def _record_skip_if_unanswered(state: InterviewState) -> None:
    _ensure_state_metadata(state)
    question = get_current_question(state)
    if question is None:
        return
    if count_candidate_answers_for_question(state, question.id) > 0:
        return
    if question.id not in state["skipped_question_ids"]:
        state["skipped_question_ids"].append(question.id)
```

- [ ] **Step 3: Update finish and skip transitions**

At the start of `finish_interview_state()` after the finished guard:

```python
    _ensure_state_metadata(state)
```

Before returning from `finish_interview_state()`, set:

```python
    state["finished_at"] = state["finished_at"] or utc_now_iso()
```

In `skip_interview_question_state()`, add metadata and record the skipped current question before computing the next state:

```python
def skip_interview_question_state(state: InterviewState) -> InterviewState:
    if state["status"] == "finished":
        return state

    _ensure_state_metadata(state)
    _record_skip_if_unanswered(state)

    next_index = state["current_index"] + 1
    if next_index >= len(state["plan"].questions):
        return finish_interview_state(state)
```

- [ ] **Step 4: Update snapshot calculation**

In `InterviewSessionStore.snapshot()`, call `_ensure_state_metadata(state)` and replace the returned dict counters with:

```python
        answer_counts = _question_answer_counts(state)
        return {
            "session_id": state["session_id"],
            "status": state["status"],
            "current_index": state["current_index"],
            "total_questions": len(state["plan"].questions),
            "completed_questions": answer_counts["answered"] + answer_counts["skipped"],
            "answered_questions": answer_counts["answered"],
            "skipped_questions": answer_counts["skipped"],
            "unanswered_questions": answer_counts["unanswered"],
            "started_at": state["started_at"],
            "finished_at": state["finished_at"],
            "elapsed_seconds": _elapsed_seconds(state),
            "estimated_remaining_seconds": answer_counts["pending_or_current"] * 6 * 60,
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

Replace `_completed_question_count()` and `_question_state()` with:

```python
def _question_answer_counts(state: InterviewState) -> dict[str, int]:
    counts = {"answered": 0, "skipped": 0, "unanswered": 0, "pending_or_current": 0}
    for index, _ in enumerate(state["plan"].questions):
        question_state = _question_state(state, index)
        if question_state in ("answered", "skipped", "unanswered"):
            counts[question_state] += 1
        else:
            counts["pending_or_current"] += 1
    return counts


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
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_session_service.py tests/test_api.py -q
```

Expected: PASS for memory-store and API snapshot tests.

- [ ] **Step 6: Commit**

```bash
git add app/graphs/interview_state.py app/services/session.py tests/test_session_service.py tests/test_api.py
git commit -m "feat: track skipped questions and session timing"
```

---

### Task 3: Persist New State Fields Through Serialization and Postgres

**Files:**
- Modify: `app/services/session_serialization.py`
- Modify: `app/services/postgres_session.py`
- Modify: `tests/test_session_serialization.py`
- Modify: `tests/test_postgres_session_store.py`

- [ ] **Step 1: Write serialization tests**

Add to `tests/test_session_serialization.py`:

```python
def test_session_serialization_preserves_skip_and_timing_metadata():
    state = build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Backend role",
        resume_text="Backend resume",
        job_tags=["python"],
    )
    state["skipped_question_ids"] = ["q1"]
    state["finished_at"] = "2026-07-04T10:00:00Z"

    row = session_row_from_state(state)
    restored = state_from_rows(row, [])

    assert row["skipped_question_ids"] == ["q1"]
    assert row["started_at"] == state["started_at"]
    assert row["finished_at"] == "2026-07-04T10:00:00Z"
    assert restored["skipped_question_ids"] == ["q1"]
    assert restored["started_at"] == state["started_at"]
    assert restored["finished_at"] == "2026-07-04T10:00:00Z"
```

- [ ] **Step 2: Update serialization functions**

In `app/services/session_serialization.py`, add keys in `session_row_from_state()`:

```python
        "skipped_question_ids": list(state.get("skipped_question_ids", [])),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
```

Add keys in `state_from_rows()`:

```python
        "skipped_question_ids": list(session_row.get("skipped_question_ids") or []),
        "started_at": session_row.get("started_at") or "",
        "finished_at": session_row.get("finished_at"),
```

- [ ] **Step 3: Add Postgres snapshot test**

Add this to `tests/test_postgres_session_store.py`:

```python
def test_skip_metadata_survives_store_reinstantiation():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)

    turn = store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built Redis APIs.",
        job_tags=["python", "redis"],
    )
    store.skip(turn.session_id)

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    state = recovered_store.get(turn.session_id)
    snapshot = recovered_store.snapshot(turn.session_id)

    assert state["skipped_question_ids"] == ["q1"]
    assert state["started_at"]
    assert state["finished_at"] is not None
    assert snapshot["questions"][0]["state"] == "skipped"
    assert snapshot["skipped_questions"] == 1
```

Also update the existing `test_skip_persists_next_question_snapshot()` expectation because skipped questions are no longer reported as `"completed"`:

```python
    assert snapshot["questions"][0]["state"] == "skipped"
```

- [ ] **Step 4: Update Postgres schema and row mapping**

In `_ensure_schema()`, add the new columns to the sessions table definition. Keep the existing `finished_at TIMESTAMPTZ` column as-is; do not add a second `finished_at` column.

```sql
skipped_question_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

After `CREATE TABLE IF NOT EXISTS`, add upgrade-safe statements:

```python
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS skipped_question_ids JSONB NOT NULL DEFAULT '[]'::jsonb"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        "ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
```

Update `get()` SELECT:

```sql
SELECT session_id, plan_json, current_index, status,
       job_description, resume_text, job_tags,
       decision_json, pending_output, skipped_question_ids,
       started_at, finished_at
```

Update `_session_row_from_db()`:

```python
            "skipped_question_ids": row[9],
            "started_at": row[10].isoformat().replace("+00:00", "Z") if row[10] else "",
            "finished_at": row[11].isoformat().replace("+00:00", "Z") if row[11] else None,
```

Replace the first session `cursor.execute(...)` inside `_insert_state()` with the complete INSERT below. This removes the old `CASE WHEN %s = 'finished' THEN NOW() ELSE NULL END` expression and persists the state timestamps directly:

```python
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {sessions} (
                            session_id, plan_json, current_index, status,
                            job_description, resume_text, job_tags,
                            decision_json, pending_output, skipped_question_ids,
                            started_at, finished_at
                        )
                        VALUES (
                            %s, %s::jsonb, %s, %s,
                            %s, %s, %s::jsonb,
                            %s::jsonb, %s, %s::jsonb,
                            %s, %s
                        )
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (
                        session_row["session_id"],
                        json.dumps(session_row["plan_json"], ensure_ascii=False),
                        session_row["current_index"],
                        session_row["status"],
                        session_row["job_description"],
                        session_row["resume_text"],
                        json.dumps(session_row["job_tags"], ensure_ascii=False),
                        json.dumps(session_row["decision_json"], ensure_ascii=False)
                        if session_row["decision_json"] is not None
                        else None,
                        session_row["pending_output"],
                        json.dumps(
                            session_row["skipped_question_ids"],
                            ensure_ascii=False,
                        ),
                        session_row["started_at"],
                        session_row["finished_at"],
                    ),
                )
```

Replace the final session `cursor.execute(...)` inside `_replace_state()` with the complete SQL and argument list below:

```python
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {sessions}
                        SET plan_json = %s::jsonb,
                            current_index = %s,
                            status = %s,
                            job_description = %s,
                            resume_text = %s,
                            job_tags = %s::jsonb,
                            decision_json = %s::jsonb,
                            pending_output = %s,
                            skipped_question_ids = %s::jsonb,
                            started_at = %s,
                            updated_at = NOW(),
                            finished_at = CASE
                                WHEN %s = 'finished' THEN COALESCE(finished_at, %s)
                                ELSE finished_at
                            END
                        WHERE session_id = %s
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (
                        json.dumps(session_row["plan_json"], ensure_ascii=False),
                        session_row["current_index"],
                        session_row["status"],
                        session_row["job_description"],
                        session_row["resume_text"],
                        json.dumps(session_row["job_tags"], ensure_ascii=False),
                        json.dumps(session_row["decision_json"], ensure_ascii=False)
                        if session_row["decision_json"] is not None
                        else None,
                        session_row["pending_output"],
                        json.dumps(
                            session_row["skipped_question_ids"],
                            ensure_ascii=False,
                        ),
                        session_row["started_at"],
                        session_row["status"],
                        session_row["finished_at"],
                        session_row["session_id"],
                    ),
                )
```

- [ ] **Step 5: Run serialization and Postgres tests**

Run:

```bash
pytest tests/test_session_serialization.py tests/test_postgres_session_store.py -q
```

Expected: PASS. If `POSTGRES_DSN` is not set, Postgres tests should be skipped and serialization tests should pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/session_serialization.py app/services/postgres_session.py tests/test_session_serialization.py tests/test_postgres_session_store.py
git commit -m "feat: persist session skip metadata"
```

---

### Task 4: Make Report Evaluation Respect Skipped and Unanswered Questions

**Files:**
- Modify: `app/services/report.py`
- Modify: `app/services/evaluator.py`
- Modify: `app/services/report_provider_adapter.py`
- Modify: `tests/test_report_models.py`
- Modify: `tests/test_report_evaluator.py`
- Modify: `tests/test_report_provider_adapter.py`

- [ ] **Step 1: Add an explicit answer state to feedback model**

In `app/services/report.py`, add this field to `InterviewFeedback` after `user_answer`:

```python
    answer_state: Literal["answered", "skipped", "unanswered"] = "answered"
```

Add this assertion to the existing report model test that builds a normal feedback in `tests/test_report_models.py`:

```python
    assert report.feedbacks[0].answer_state == "answered"
```

- [ ] **Step 2: Write evaluator tests for answer states**

Add to `tests/test_report_evaluator.py`:

```python
def test_evaluator_marks_skipped_question_in_evaluation_items():
    state = make_finished_state()
    state["skipped_question_ids"] = ["q2"]
    state["messages"] = [
        message
        for message in state["messages"]
        if message["question_id"] != "q2" or message["role"] != "candidate"
    ]
    llm = FakeReportLLM()
    evaluator = ShadowEvaluator(llm=llm)

    report = evaluator.evaluate(state)

    q2_item = next(item for item in llm.last_evaluation_items if item["question_id"] == "q2")
    q2_feedback = next(feedback for feedback in report.feedbacks if feedback.question_id == "q2")
    assert q2_item["answer_state"] == "skipped"
    assert q2_feedback.answer_state == "skipped"
    assert q2_feedback.score == 0
    assert q2_feedback.user_answer == "Question was skipped by the candidate."
    assert q2_feedback.references == []
    assert report.overall_score == 41


def test_evaluator_marks_finished_missing_answer_as_unanswered():
    state = make_finished_state()
    state["messages"] = [
        message
        for message in state["messages"]
        if message["question_id"] != "q2" or message["role"] != "candidate"
    ]
    llm = FakeReportLLM()
    evaluator = ShadowEvaluator(llm=llm)

    report = evaluator.evaluate(state)

    q2_item = next(item for item in llm.last_evaluation_items if item["question_id"] == "q2")
    q2_feedback = next(feedback for feedback in report.feedbacks if feedback.question_id == "q2")
    assert q2_item["answer_state"] == "unanswered"
    assert q2_feedback.answer_state == "unanswered"
    assert q2_feedback.score == 0
    assert q2_feedback.user_answer == "No candidate answer was recorded for this question."
    assert q2_feedback.critique == "No answer was available to evaluate."
```

- [ ] **Step 3: Add answer state to chunks**

In `app/services/evaluator.py`, update `EvaluationChunk`:

```python
class EvaluationChunk(BaseModel):
    question_id: str
    question_text: str
    focus: str
    answer_state: str
    messages: list[dict[str, str]]
```

Update `build_evaluation_chunks()`:

```python
        EvaluationChunk(
            question_id=question.id,
            question_text=question.prompt,
            focus=question.focus,
            answer_state=_answer_state_for_question(state, question),
            messages=_messages_for_question(state, question),
        )
```

Add helper:

```python
def _answer_state_for_question(
    state: InterviewState,
    question: InterviewQuestion,
) -> str:
    if question.id in state.get("skipped_question_ids", []):
        return "skipped"
    has_answer = any(
        message["role"] == "candidate"
        and message["question_id"] == question.id
        and message["content"].strip()
        for message in state["messages"]
    )
    if has_answer:
        return "answered"
    return "unanswered"
```

- [ ] **Step 4: Override skipped/unanswered feedback after LLM and fallback reports**

In `ShadowEvaluator.evaluate()`, replace the direct return:

```python
            report = self._llm.generate_report(
                plan=state["plan"],
                evaluation_items=[chunk.model_dump() for chunk in chunks],
                session_id=state["session_id"],
            )
            return _apply_answer_state_overrides(report, chunks)
```

Add helpers:

```python
def _apply_answer_state_overrides(
    report: InterviewReport,
    chunks: list[EvaluationChunk],
) -> InterviewReport:
    chunk_by_id = {chunk.question_id: chunk for chunk in chunks}
    feedbacks = []
    for feedback in report.feedbacks:
        chunk = chunk_by_id.get(feedback.question_id)
        if chunk is None or chunk.answer_state == "answered":
            feedbacks.append(feedback)
            continue
        feedbacks.append(_empty_answer_feedback(chunk))
    return report.model_copy(
        update={
            "feedbacks": feedbacks,
            "overall_score": _average_score(feedbacks),
            "overall_dimension_scores": _average_dimension_scores(feedbacks),
        }
    )


def _empty_answer_feedback(chunk: EvaluationChunk) -> InterviewFeedback:
    skipped = chunk.answer_state == "skipped"
    return InterviewFeedback(
        question_id=chunk.question_id,
        question_text=chunk.question_text,
        user_answer=(
            "Question was skipped by the candidate."
            if skipped
            else "No candidate answer was recorded for this question."
        ),
        answer_state=chunk.answer_state,
        score=0,
        dimension_scores=_default_dimension_scores(0),
        rationale=(
            "The candidate skipped this question."
            if skipped
            else "No candidate answer was recorded for this question."
        ),
        critique="No answer was available to evaluate.",
        better_answer="Answer the question with context, action, tradeoffs, and measurable results.",
        references=[],
    )


def _average_score(feedbacks: list[InterviewFeedback]) -> int:
    if not feedbacks:
        return 0
    return round(sum(feedback.score for feedback in feedbacks) / len(feedbacks))


def _average_dimension_scores(feedbacks: list[InterviewFeedback]) -> DimensionScores:
    if not feedbacks:
        return _default_dimension_scores(0)
    fields = DimensionScores.model_fields.keys()
    values = {
        field: round(
            sum(getattr(feedback.dimension_scores, field) for feedback in feedbacks)
            / len(feedbacks)
        )
        for field in fields
    }
    return DimensionScores(**values)
```

In the `except ReportOutputFormatError:` branch of `ShadowEvaluator.evaluate()`, wrap fallback output with the same override:

```python
            fallback = build_fallback_report(state, chunks)
            return _apply_answer_state_overrides(fallback, chunks)
```

In `build_fallback_report()`, set the feedback state explicitly:

```python
                answer_state=chunk.answer_state,
```

- [ ] **Step 5: Update fallback answer summary**

In `_summarize_candidate_answers()`:

```python
    if chunk.answer_state == "skipped":
        return "Question was skipped by the candidate."
```

Keep the existing no-answer fallback after the skipped check. This keeps `test_evaluator_includes_unanswered_questions_in_fallback()` valid while changing the unanswered feedback score to `0` through `_apply_answer_state_overrides()`.

- [ ] **Step 6: Update provider adapter user-answer fallback**

In `app/services/report_provider_adapter.py`, update `_build_user_answer()`:

```python
def _build_user_answer(evaluation_item: dict[str, Any]) -> str:
    if evaluation_item.get("answer_state") == "skipped":
        return "Question was skipped by the candidate."
    messages = evaluation_item.get("messages", [])
    answers = [
        str(message.get("content", "")).strip()
        for message in messages
        if message.get("role") == "candidate" and str(message.get("content", "")).strip()
    ]
    if answers:
        return " ".join(answers)
    return "No candidate answer was recorded for this question."
```

- [ ] **Step 7: Run report tests**

Run:

```bash
pytest tests/test_report_models.py tests/test_report_evaluator.py tests/test_report_provider_adapter.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/services/report.py app/services/evaluator.py app/services/report_provider_adapter.py tests/test_report_models.py tests/test_report_evaluator.py tests/test_report_provider_adapter.py
git commit -m "feat: mark skipped questions in reports"
```

---

### Task 5: Show Skipped and Unanswered States in UI and PDF

**Files:**
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Modify: `app/services/report_pdf.py`
- Modify: `tests/test_static_report_ui.py`
- Modify: `tests/test_report_pdf.py`

- [ ] **Step 1: Write static UI tests**

Add to `tests/test_static_report_ui.py`:

```python
def test_app_js_renders_skipped_and_unanswered_question_states():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "answered:" in js
    assert "skipped:" in js
    assert "unanswered:" in js
    assert "snapshot.elapsed_seconds" in js
    assert "snapshot.estimated_remaining_seconds" in js


def test_styles_include_skipped_and_unanswered_question_states():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".question-answered" in css
    assert ".question-completed" not in css
    assert ".question-skipped" in css
    assert ".question-unanswered" in css
```

- [ ] **Step 2: Update frontend state labels and timing**

In `renderQuestionPlanFromSnapshot()`, replace `stateLabels` with:

```javascript
  const stateLabels = {
    answered: "已回答",
    skipped: "已跳过",
    unanswered: "未回答",
    current: "当前题",
    pending: "待进行",
  };
```

In `renderSessionSnapshot(snapshot)`, after `planCoverage.textContent = ...`, add:

```javascript
  if (typeof snapshot.elapsed_seconds === "number") {
    startedAt = Date.now() - snapshot.elapsed_seconds * 1000;
  }
  if (typeof snapshot.estimated_remaining_seconds === "number") {
    planDuration.textContent = String(Math.ceil(snapshot.estimated_remaining_seconds / 60));
  }
```

- [ ] **Step 3: Rename answered CSS state and add skipped/unanswered states**

In `app/static/styles.css`, rename the existing `.question-completed .step` selector to `.question-answered .step`:

```css
.question-answered .step {
  color: #d9fff2;
  border-color: rgba(33, 211, 155, 0.78);
  background: rgba(33, 211, 155, 0.16);
}
```

Then add skipped and unanswered styles near the same question row block:

```css
.question-skipped .step,
.question-unanswered .step {
  background: #f3e7d3;
  color: #7a4a12;
}

.question-skipped .question-box,
.question-unanswered .question-box {
  border-color: rgba(150, 94, 25, 0.35);
  opacity: 0.82;
}
```

- [ ] **Step 4: Write PDF test**

Add to `tests/test_report_pdf.py`:

```python
def test_report_pdf_contains_skipped_answer_marker():
    report = make_report()
    skipped_feedback = report.feedbacks[0].model_copy(
        update={
            "user_answer": "Question was skipped by the candidate.",
            "answer_state": "skipped",
            "score": 0,
        }
    )
    report = report.model_copy(update={"feedbacks": [skipped_feedback]})

    pdf = build_report_pdf(report)

    assert b"%PDF" in pdf[:20]
    assert len(pdf) > 1000
```

- [ ] **Step 5: Update PDF feedback rendering**

In `app/services/report_pdf.py`, add helper:

```python
def _answer_status_label(feedback: InterviewFeedback) -> str | None:
    if feedback.answer_state == "skipped":
        return "Status: skipped"
    if feedback.answer_state == "unanswered":
        return "Status: unanswered"
    return None
```

In `_feedback_story()`, insert the status after score:

```python
    status_label = _answer_status_label(feedback)
    if status_label:
        blocks.append(Paragraph(status_label, styles["warning"]))
```

- [ ] **Step 6: Run UI and PDF tests**

Run:

```bash
node --check app/static/app.js
pytest tests/test_static_report_ui.py tests/test_report_pdf.py -q
```

Expected: JS syntax check passes and tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/static/app.js app/static/styles.css app/services/report_pdf.py tests/test_static_report_ui.py tests/test_report_pdf.py
git commit -m "feat: render skipped question states"
```

---

### Task 6: Final Regression

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run full test suite**

Run:

```bash
pytest -q
```

Expected: all tests pass; Postgres tests may be skipped when `POSTGRES_DSN` is not configured.

- [ ] **Step 2: Check git status**

Run:

```bash
git status --short
```

Expected: only unrelated local IDE/cache/docs files remain if they existed before this stage.

- [ ] **Step 3: Manual local smoke test**

Run the app using the project’s normal local command, then:

1. Generate a plan.
2. Start an interview.
3. Skip the first question.
4. Answer the second question.
5. Finish the interview.
6. Confirm the side plan shows `已跳过`, `已回答`, and `未回答` correctly.
7. Confirm the generated report and PDF show skipped/unanswered questions without invented candidate answers.

- [ ] **Step 4: Stop for review if regression finds new behavior**

If Step 1 or Step 3 finds a behavioral issue, fix it in the smallest affected task area, rerun the failing command, then create a focused follow-up commit with an explicit file list. Do not stage unrelated docs, IDE files, caches, logs, `.venv`, or prototype HTML files.

---

## Self-Review

**Spec coverage:** This plan covers local-only usage, no login/account work, skipped question semantics, unanswered question reporting, session timing metadata, UI question state labels, Postgres persistence, and PDF output.

**Placeholder scan:** The plan contains no deferred implementation placeholders. Each code-changing task includes concrete snippets and exact test commands.

**Type consistency:** The new fields are consistently named `skipped_question_ids`, `started_at`, `finished_at`, `answered_questions`, `skipped_questions`, `unanswered_questions`, `elapsed_seconds`, and `estimated_remaining_seconds` across state, snapshot, API tests, UI, and persistence.
