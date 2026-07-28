# Stage 35 Review Pipeline Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the round-review-to-final-report pipeline observable, debuggable, and regression-tested without adding a new runtime dependency.

**Architecture:** Extend the existing polling/report-progress boundary with structured metadata, then emit microbatch reuse statistics from `report_microbatch` into `report_tasks` trace/progress events. Keep Local V1 transport as HTTP polling and keep `QuestionEvaluationRecord` as the authoritative per-question score source.

**Tech Stack:** FastAPI, Pydantic models, in-memory/Postgres session stores, static ES modules, pytest, Node `--check`.

---

## Current State And Constraints

- Stage 34 is complete: final report generation first tries `generate_microbatch_report()`, preserves Shadow Reviewer question scores, and falls back to full-session `ShadowReviewerAgent`.
- `LocalRoundReviewEventPublisher.shutdown()` and `shutdown_runtime()` already exist. Stage 35 should add regression coverage and docs, not re-implement lifecycle plumbing.
- `app/static/interview.js` already avoids `renderSnapshot(data)` in the SSE `done()` callback. Stage 35 should keep this as a static regression check.
- The worktree has unrelated dirty and untracked files. During execution, only stage files named in each task.

## File Structure

- Modify `app/services/report.py`: add optional `metadata` to `ReportProgress`.
- Modify `app/api/routes.py`: include `metadata` in `/report/progress` responses and completed/failed progress details.
- Modify `app/services/runtime_events.py`: allow `ReportProgressEvent` to carry the same metadata shape for future event fanout.
- Modify `app/services/report_microbatch.py`: introduce `ReportMicrobatchStats`, collect reused/rerun/failed/missing counts, and expose stats through a callback.
- Modify `app/services/report_tasks.py`: record report-path trace files and attach microbatch/fallback metadata to progress updates.
- Modify `app/static/report-processing.js`: render report path and microbatch counters from progress metadata.
- Modify `README.md` and `docs/local-v1-runbook.md`: document Stage 35 observability checks.
- Modify tests:
  - `tests/test_report_models.py`
  - `tests/test_report_api.py`
  - `tests/test_runtime_events.py`
  - `tests/test_report_microbatch.py`
  - `tests/test_report_tasks_microbatch.py`
  - `tests/test_static_report_ui.py`
  - `tests/test_local_v1_docs.py`
  - optional focused checks in `tests/test_event_publisher.py` and `tests/test_runtime_provider.py` if missing after inspection.

---

### Task 1: Add Structured Report Progress Metadata

**Files:**
- Modify: `app/services/report.py`
- Modify: `app/api/routes.py`
- Modify: `app/services/runtime_events.py`
- Test: `tests/test_report_models.py`
- Test: `tests/test_report_api.py`
- Test: `tests/test_runtime_events.py`

- [ ] **Step 1: Write failing model tests for progress metadata**

Add this test to `tests/test_report_models.py` near `test_report_progress_validates_percent_and_stage`:

```python
def test_report_progress_accepts_metadata_for_observability():
    progress = ReportProgress(
        stage="analyzing",
        percent=60,
        message="Reusing question reviews.",
        current_question_id="q1",
        metadata={
            "report_path": "microbatch",
            "microbatch_total_questions": 2,
            "microbatch_reused_questions": 1,
            "microbatch_rerun_questions": 1,
            "microbatch_failed_questions": 0,
        },
    )

    assert progress.metadata["report_path"] == "microbatch"
    assert progress.metadata["microbatch_rerun_questions"] == 1
    assert progress.model_dump()["metadata"]["microbatch_total_questions"] == 2
```

- [ ] **Step 2: Write failing API progress tests**

Add this test to `tests/test_report_api.py` near `test_report_progress_endpoint_returns_processing_detail`:

```python
def test_report_progress_endpoint_includes_progress_metadata():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.update_report_progress(
        session_id,
        ReportProgress(
            stage="analyzing",
            percent=60,
            message="Reusing question-level review scores.",
            current_question_id="q1",
            metadata={
                "report_path": "microbatch",
                "microbatch_total_questions": 2,
                "microbatch_reused_questions": 1,
                "microbatch_rerun_questions": 1,
                "microbatch_failed_questions": 0,
            },
        ),
    )

    response = client.get(f"/api/interviews/{session_id}/report/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["report_path"] == "microbatch"
    assert body["metadata"]["microbatch_rerun_questions"] == 1
```

If `ReportProgress` is not already imported in `tests/test_report_api.py`, extend the import from `app.services.report`:

```python
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
)
```

- [ ] **Step 3: Write failing runtime event metadata test**

Update `test_report_progress_event_uses_current_polling_shape` in `tests/test_runtime_events.py` to pass and assert metadata:

```python
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
    metadata={"report_path": "microbatch"},
)

assert event.model_dump()["status"] == "processing"
assert event.model_dump()["rag"]["top_k"] == 5
assert event.model_dump()["metadata"]["report_path"] == "microbatch"
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_models.py tests/test_report_api.py tests/test_runtime_events.py -q
```

Expected: FAIL because `ReportProgress` and `ReportProgressEvent` do not yet expose `metadata`, and `/report/progress` does not serialize it.

- [ ] **Step 5: Implement `ReportProgress.metadata`**

In `app/services/report.py`, change the import:

```python
from typing import Any, Literal
```

Then update `ReportProgress`:

```python
class ReportProgress(BaseModel):
    stage: Literal["retrieving", "analyzing", "aggregating", "completed"]
    percent: int = Field(ge=0, le=100)
    message: str
    current_question_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 6: Include metadata in API progress responses**

In `app/api/routes.py`, update `_report_progress_detail()` so every returned dict includes `metadata`.

For `record is None`:

```python
"metadata": {},
```

For `record.status == "completed"`:

```python
"metadata": {},
```

For `record.status == "failed"`:

```python
"metadata": {},
```

For processing records, after `current_question_id` is resolved:

```python
metadata = progress.metadata if progress is not None else {}
```

Then include it in the return payload:

```python
"metadata": metadata,
```

- [ ] **Step 7: Extend `ReportProgressEvent`**

In `app/services/runtime_events.py`, add this field to `ReportProgressEvent`:

```python
metadata: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 8: Run tests to verify Task 1 passes**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_models.py tests/test_report_api.py tests/test_runtime_events.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

Stage only these files:

```powershell
git add app/services/report.py app/api/routes.py app/services/runtime_events.py tests/test_report_models.py tests/test_report_api.py tests/test_runtime_events.py
git commit -m "feat: add report progress metadata"
```

---

### Task 2: Emit Microbatch Reuse Stats And Fallback Trace

**Files:**
- Modify: `app/services/report_microbatch.py`
- Modify: `app/services/report_tasks.py`
- Test: `tests/test_report_microbatch.py`
- Test: `tests/test_report_tasks_microbatch.py`

- [ ] **Step 1: Write failing microbatch stats test**

Add this test to `tests/test_report_microbatch.py`:

```python
def test_generate_microbatch_report_reports_reuse_stats():
    state = make_state()
    store = FakeStore(
        state,
        [completed_record("s1", "q1", 78)],
    )
    llm = CapturingReportLLM()
    captured_stats = []
    FakeReviewer.calls = []

    report = generate_microbatch_report(
        state,
        store=store,
        llm=llm,
        vector_store=object(),
        on_progress=lambda progress: None,
        on_microbatch_stats=captured_stats.append,
        reviewer_factory=FakeReviewer,
    )

    assert report.overall_score == 80
    assert FakeReviewer.calls == ["q2"]
    assert len(captured_stats) == 1
    stats = captured_stats[0]
    assert stats.total_questions == 2
    assert stats.reused_questions == 1
    assert stats.rerun_questions == 1
    assert stats.failed_questions == 0
    assert stats.question_ids == ["q1", "q2"]
    assert stats.rerun_question_ids == ["q2"]
    assert stats.to_metadata()["report_path"] == "microbatch"
    assert stats.to_metadata()["microbatch_rerun_questions"] == 1
```

- [ ] **Step 2: Write failing stats callback test for unavailable microbatch path**

Add this test to `tests/test_report_microbatch.py`:

```python
def test_generate_microbatch_report_reports_failed_stats_before_raising():
    class FailingReviewer:
        def __init__(self, *, llm, vector_store):
            pass

        def evaluate(self, state, on_progress=None):
            raise RuntimeError("round review still unavailable")

    state = make_state()
    store = FakeStore(state, [completed_record("s1", "q1", 78)])
    captured_stats = []

    with pytest.raises(MicrobatchReportUnavailable, match="q2"):
        generate_microbatch_report(
            state,
            store=store,
            llm=CapturingReportLLM(),
            vector_store=object(),
            on_microbatch_stats=captured_stats.append,
            reviewer_factory=FailingReviewer,
        )

    assert len(captured_stats) == 1
    stats = captured_stats[0]
    assert stats.total_questions == 2
    assert stats.reused_questions == 1
    assert stats.rerun_questions == 1
    assert stats.failed_questions == 1
    assert stats.rerun_question_ids == ["q2"]
    assert stats.failed_question_ids == ["q2"]
```

This test is required because failed reruns raise `MicrobatchReportUnavailable`; without an exception-safe callback, `failed_questions` is never observable by `report_tasks.py`.

- [ ] **Step 3: Write failing worker trace tests**

Add this test to `tests/test_report_tasks_microbatch.py`:

```python
def test_execute_report_generation_records_microbatch_trace(monkeypatch, tmp_path):
    store = InterviewSessionStore()
    turn = start_finished_session(store)
    store.upsert_question_evaluation(
        turn.session_id,
        question_evaluation_from_feedback(
            session_id=turn.session_id,
            feedback=make_feedback(question_id="q1", score=84),
        ),
    )
    monkeypatch.setenv("REPORT_TRACE_DIR", str(tmp_path))

    report = execute_report_generation(
        session_id=turn.session_id,
        store=store,
        llm=CapturingReportLLM(),
        vector_store=object(),
    )

    assert report.overall_score == 84
    trace_files = sorted((tmp_path / turn.session_id).glob("*_report_path.json"))
    assert trace_files
    payload = trace_files[0].read_text(encoding="utf-8")
    assert '"report_path": "microbatch"' in payload
    assert '"microbatch_reused_questions": 1' in payload
```

Add a fallback trace test:

```python
def test_execute_report_generation_records_fallback_trace_with_microbatch_stats(monkeypatch, tmp_path):
    import app.services.report_tasks as report_tasks

    store = InterviewSessionStore()
    turn = start_finished_session(store)
    from app.services.report_microbatch import ReportMicrobatchStats

    stats = ReportMicrobatchStats(
        total_questions=1,
        reused_questions=0,
        rerun_questions=1,
        failed_questions=1,
        question_ids=["q1"],
        rerun_question_ids=["q1"],
        failed_question_ids=["q1"],
    )

    def raise_microbatch_unavailable(*args, **kwargs):
        on_microbatch_stats = kwargs.get("on_microbatch_stats")
        if on_microbatch_stats is not None:
            on_microbatch_stats(stats)
        raise MicrobatchReportUnavailable("missing q1")

    monkeypatch.setenv("REPORT_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(
        report_tasks,
        "generate_microbatch_report",
        raise_microbatch_unavailable,
    )
    monkeypatch.setattr(report_tasks, "ShadowReviewerAgent", FullSessionReviewer)

    report = execute_report_generation(
        session_id=turn.session_id,
        store=store,
        llm=CapturingReportLLM(),
        vector_store=object(),
    )

    assert report.overall_score == 71
    trace_files = sorted((tmp_path / turn.session_id).glob("*_report_path.json"))
    assert trace_files
    payload = trace_files[0].read_text(encoding="utf-8")
    assert '"report_path": "full_session_fallback"' in payload
    assert '"fallback_reason": "missing q1"' in payload
    assert '"microbatch_failed_questions": 1' in payload
    assert '"failed_question_ids": [' in payload
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_microbatch.py tests/test_report_tasks_microbatch.py -q
```

Expected: FAIL because `on_microbatch_stats` and report-path trace do not exist yet.

- [ ] **Step 5: Add `ReportMicrobatchStats` and callback support**

In `app/services/report_microbatch.py`, import `BaseModel` and `Field`:

```python
from pydantic import BaseModel, Field
```

Add this model before `MicrobatchReportUnavailable`:

```python
class ReportMicrobatchStats(BaseModel):
    report_path: str = "microbatch"
    total_questions: int = 0
    reused_questions: int = 0
    rerun_questions: int = 0
    failed_questions: int = 0
    question_ids: list[str] = Field(default_factory=list)
    reused_question_ids: list[str] = Field(default_factory=list)
    rerun_question_ids: list[str] = Field(default_factory=list)
    failed_question_ids: list[str] = Field(default_factory=list)

    def to_metadata(self) -> dict:
        return {
            "report_path": self.report_path,
            "microbatch_total_questions": self.total_questions,
            "microbatch_reused_questions": self.reused_questions,
            "microbatch_rerun_questions": self.rerun_questions,
            "microbatch_failed_questions": self.failed_questions,
            "question_ids": list(self.question_ids),
            "reused_question_ids": list(self.reused_question_ids),
            "rerun_question_ids": list(self.rerun_question_ids),
            "failed_question_ids": list(self.failed_question_ids),
        }
```

Then replace `MicrobatchReportUnavailable` with a stats-aware exception:

```python
class MicrobatchReportUnavailable(RuntimeError):
    """Raised when question-level microbatches cannot produce a complete report input."""

    def __init__(
        self,
        message: str,
        *,
        stats: ReportMicrobatchStats | None = None,
    ) -> None:
        super().__init__(message)
        self.stats = stats
```

Change `generate_microbatch_report()` signature:

```python
def generate_microbatch_report(
    state,
    *,
    store,
    llm,
    vector_store,
    on_progress=None,
    on_microbatch_stats=None,
    reviewer_factory=None,
):
```

Change the call to `ensure_completed_question_evaluations_for_report()`:

```python
stats = ReportMicrobatchStats()
try:
    records = ensure_completed_question_evaluations_for_report(
        state,
        store=store,
        llm=llm,
        vector_store=vector_store,
        reviewer_factory=reviewer_factory,
        stats=stats,
    )
except MicrobatchReportUnavailable as exc:
    if exc.stats is None:
        exc.stats = stats
    if on_microbatch_stats is not None:
        on_microbatch_stats(stats)
    raise
else:
    if on_microbatch_stats is not None:
        on_microbatch_stats(stats)
```

Do not use a bare `finally` callback here. The explicit `except`/`else` structure prevents accidental double-callbacks if later code adds more exception handling after records are loaded.

When publishing the `analyzing` progress event, add metadata:

```python
metadata=stats.to_metadata(),
```

- [ ] **Step 6: Update `ensure_completed_question_evaluations_for_report()` to fill stats**

Change the signature:

```python
def ensure_completed_question_evaluations_for_report(
    state,
    *,
    store,
    llm,
    vector_store,
    reviewer_factory=None,
    stats: ReportMicrobatchStats | None = None,
) -> list[QuestionEvaluationRecord]:
```

After `chunks = build_evaluation_chunks(state)`, initialize stats:

```python
if stats is not None:
    stats.total_questions = len(chunks)
    stats.question_ids = [chunk.question_id for chunk in chunks]
```

Inside the completed-record branch:

```python
if stats is not None:
    stats.reused_questions += 1
    stats.reused_question_ids.append(chunk.question_id)
```

Before `run_round_review_event_from_state(...)`:

```python
if stats is not None:
    stats.rerun_questions += 1
    stats.rerun_question_ids.append(chunk.question_id)
```

When a reviewed record is not completed, before raising:

```python
if stats is not None:
    stats.failed_questions += 1
    stats.failed_question_ids.append(chunk.question_id)
raise MicrobatchReportUnavailable(
    f"question review unavailable for {chunk.question_id}: {reason}",
    stats=stats,
)
```

This means `rerun_questions` counts attempted reruns, while `failed_questions` is the failed subset of those reruns. A successful-rerun count can be derived as `rerun_questions - failed_questions`.

- [ ] **Step 7: Record report-path trace in `report_tasks.py`**

In `execute_report_generation()`, add local variable before the microbatch branch:

```python
microbatch_stats = None
```

Add a local callback:

```python
def capture_microbatch_stats(stats):
    nonlocal microbatch_stats
    microbatch_stats = stats
```

Pass it to `generate_microbatch_report()`:

```python
on_microbatch_stats=capture_microbatch_stats,
```

After `generate_microbatch_report()` returns, record trace:

```python
_record_report_path_trace(
    session_id,
    {
        "report_path": "microbatch",
        **(microbatch_stats.to_metadata() if microbatch_stats is not None else {}),
    },
)
```

In the exception branch, capture the exception as `exc`:

```python
except (MicrobatchReportUnavailable, ReportOutputFormatError) as exc:
```

Then record fallback trace before `_evaluate_full_session(...)`:

```python
if microbatch_stats is None:
    microbatch_stats = getattr(exc, "stats", None)
fallback_payload = {
    "report_path": "full_session_fallback",
    "fallback_reason": str(exc),
}
if microbatch_stats is not None:
    fallback_payload.update(microbatch_stats.to_metadata())
    fallback_payload["report_path"] = "full_session_fallback"
_record_report_path_trace(
    session_id,
    fallback_payload,
)
```

Add helper near `_record_runtime_quality_warning()`:

```python
def _record_report_path_trace(session_id: str, payload: dict) -> None:
    from app.services.report_trace import ReportTraceRecorder

    ReportTraceRecorder.from_env().record(
        session_id=session_id,
        stage="report_path",
        payload=payload,
    )
```

- [ ] **Step 8: Run Task 2 tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_microbatch.py tests/test_report_tasks_microbatch.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 2**

```powershell
git add app/services/report_microbatch.py app/services/report_tasks.py tests/test_report_microbatch.py tests/test_report_tasks_microbatch.py
git commit -m "feat: trace report microbatch reuse"
```

---

### Task 3: Surface Report Path Metadata In Report Processing UI

**Files:**
- Modify: `app/static/report-processing.js`
- Test: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write failing static UI test**

Add this test to `tests/test_static_report_ui.py` near the other report-processing tests:

```python
def test_report_processing_page_renders_report_path_metadata():
    js = read_static_file("report-processing.js")

    assert "function renderReportMetadata(progress)" in js
    assert "progress.metadata || {}" in js
    assert "const metadataDetails = renderReportMetadata(progress)" in js
    assert "const eventItems = progress.events || []" in js
    assert "if (!eventItems.length && !metadataDetails.length)" in js
    assert "report_path" in js
    assert "microbatch_reused_questions" in js
    assert "microbatch_rerun_questions" in js
    assert "full_session_fallback" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py::test_report_processing_page_renders_report_path_metadata -q
```

Expected: FAIL because `renderReportMetadata()` does not exist.

- [ ] **Step 3: Implement metadata rendering**

In `app/static/report-processing.js`, add this helper after `renderProgress(progress)` or before it:

```javascript
function renderReportMetadata(progress) {
  const metadata = progress.metadata || {};
  const details = [];
  if (metadata.report_path === "microbatch") {
    details.push(`path: microbatch reuse`);
    details.push(`reused: ${metadata.microbatch_reused_questions ?? 0}`);
    details.push(`rerun attempted: ${metadata.microbatch_rerun_questions ?? 0}`);
    details.push(`rerun failed: ${metadata.microbatch_failed_questions ?? 0}`);
  } else if (metadata.report_path === "full_session_fallback") {
    details.push(`path: full_session_fallback`);
    details.push(`reason: ${metadata.fallback_reason || "unknown"}`);
  }
  return details;
}
```

In `renderProgress(progress)`, replace the existing event rendering block:

```javascript
clear(reportEvents);
if (!progress.events || !progress.events.length) {
  renderEmptyState(reportEvents, "暂无生成事件。");
}
for (const event of progress.events || []) {
  reportEvents.appendChild(createEl("p", "text-[13px] text-gray-700 mb-2", `${event.stage}: ${event.message}`));
}
```

with:

```javascript
clear(reportEvents);
const eventItems = progress.events || [];
const metadataDetails = renderReportMetadata(progress);
if (!eventItems.length && !metadataDetails.length) {
  renderEmptyState(reportEvents, "暂无生成事件。");
}
for (const event of eventItems) {
  reportEvents.appendChild(createEl("p", "text-[13px] text-gray-700 mb-2", `${event.stage}: ${event.message}`));
}
```

Then append metadata details:

```javascript
for (const detail of metadataDetails) {
  reportEvents.appendChild(createEl("p", "text-[12px] text-gray-500 mb-1", detail));
}
```

- [ ] **Step 4: Run UI static and syntax checks**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py -q
node --check app/static/report-processing.js
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add app/static/report-processing.js tests/test_static_report_ui.py
git commit -m "feat: show report pipeline metadata"
```

---

### Task 4: Add Lifecycle And Snapshot Regression Coverage Plus Docs

**Files:**
- Modify: `tests/test_event_publisher.py`
- Modify: `tests/test_runtime_provider.py`
- Modify: `tests/test_static_report_ui.py`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Confirm existing lifecycle tests before editing**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_event_publisher.py tests/test_runtime_provider.py -q
```

Expected: PASS. If either file already covers the exact assertions below, do not duplicate the tests.

- [ ] **Step 2: Add or keep local publisher shutdown assertion**

If `tests/test_event_publisher.py` does not already assert the executor receives `wait=False`, add:

```python
def test_local_round_review_event_publisher_shutdown_drains_executor():
    executor = FakeExecutor()
    publisher = LocalRoundReviewEventPublisher(executor=executor)

    publisher.shutdown(wait=False)

    assert executor.shutdown_wait is False
```

The current codebase already has this test. During execution, leave it unchanged if present.

- [ ] **Step 3: Add or keep runtime cache shutdown assertion**

If `tests/test_runtime_provider.py` does not already assert `shutdown_runtime(wait=True)` closes the cached publisher, add:

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
```

The current codebase already has this test. During execution, leave it unchanged if present.

- [ ] **Step 4: Keep interview SSE snapshot regression test**

Verify this test still exists in `tests/test_static_report_ui.py`:

```python
def test_interview_page_does_not_render_partial_turn_payload_after_sse_done():
    js = read_static_file("interview.js")

    assert "renderSnapshot(data)" not in js
    assert "SSE done payload is an InterviewTurn" in js
    assert "await loadSnapshot();" in js
```

If it exists, do not edit it. If it was removed, restore it exactly.

- [ ] **Step 5: Add failing docs test for Stage 35**

Add this test to `tests/test_local_v1_docs.py` after the Stage 34 docs test:

```python
def test_docs_describe_stage_35_review_pipeline_observability():
    readme = read_text("README.md")
    runbook = read_text("docs/local-v1-runbook.md")

    expected = "Stage 35 makes the review pipeline observable"
    assert expected in readme
    assert expected in runbook
    assert "report_path" in readme
    assert "microbatch_reused_questions" in readme
    assert "REPORT_TRACE_DIR" in runbook
    assert "full_session_fallback" in runbook
    assert "LocalRoundReviewEventPublisher.shutdown" in runbook
```

- [ ] **Step 6: Run docs test to verify it fails**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_35_review_pipeline_observability -q
```

Expected: FAIL because README and runbook do not yet describe Stage 35.

- [ ] **Step 7: Update README**

Add this paragraph after the Stage 34 paragraph in `README.md`:

```markdown
Stage 35 makes the review pipeline observable. Report progress now carries `metadata` such as `report_path`, `microbatch_reused_questions`, `microbatch_rerun_questions`, and fallback reason fields so `/report-processing` can show whether the final report reused round-review microbatches or used `full_session_fallback`. Report trace files written through `REPORT_TRACE_DIR` record the same path choice for offline debugging, while existing `LocalRoundReviewEventPublisher.shutdown` lifecycle coverage protects local async review tasks during runtime shutdown.
```

- [ ] **Step 8: Update runbook**

Add this paragraph after the Stage 34 paragraph in `docs/local-v1-runbook.md`:

```markdown
Stage 35 makes the review pipeline observable. When `REPORT_TRACE_DIR` is set, local verification should confirm a `report_path` trace file is written for final report generation and includes either microbatch reuse counters or `full_session_fallback` with a fallback reason. The report-processing page should show the same metadata from `/api/interviews/{session_id}/report/progress`, and shutdown coverage should continue to call `LocalRoundReviewEventPublisher.shutdown` through FastAPI lifespan/runtime reset paths.
```

Add this checklist after the Stage 34 final report microbatch reuse checks:

```markdown
Stage 35 review pipeline observability checks:

1. Set `REPORT_TRACE_DIR` to a temporary directory.
2. Finish an interview that already has at least one completed `QuestionEvaluationRecord`.
3. Poll `/api/interviews/{session_id}/report/progress` and confirm `metadata.report_path` is `microbatch`.
4. Confirm the progress metadata includes `microbatch_reused_questions` and `microbatch_rerun_questions`.
5. Force or simulate a microbatch-unavailable path and confirm progress or trace metadata records `full_session_fallback`.
6. Stop the FastAPI process and confirm runtime shutdown does not leave local round-review executor errors in logs.
```

- [ ] **Step 9: Run docs and lifecycle/static tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py tests/test_event_publisher.py tests/test_runtime_provider.py tests/test_static_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit Task 4**

```powershell
git add README.md docs/local-v1-runbook.md tests/test_local_v1_docs.py tests/test_event_publisher.py tests/test_runtime_provider.py tests/test_static_report_ui.py
git commit -m "docs: describe review pipeline observability"
```

If `tests/test_event_publisher.py` and `tests/test_runtime_provider.py` were not modified because the required tests already existed, omit them from `git add`.

---

### Task 5: Final Verification

**Files:**
- No implementation files unless a verification failure requires a targeted fix.

- [ ] **Step 1: Run focused Stage 35 tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_models.py tests/test_report_api.py tests/test_runtime_events.py tests/test_report_microbatch.py tests/test_report_tasks_microbatch.py tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 2: Run related report/runtime regressions**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_report_tasks.py tests/test_report_worker.py tests/test_report_contract.py tests/test_report_provider_adapter.py tests/test_event_publisher.py tests/test_runtime_provider.py -q
```

Expected: PASS.

- [ ] **Step 3: Run static JavaScript syntax checks**

Run:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: all commands exit 0.

- [ ] **Step 4: Run full pytest**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS. The current baseline after Stage 34 was `382 passed, 27 skipped, 1 warning`; the exact count may increase after Stage 35 tests.

- [ ] **Step 5: Inspect worktree and commits**

Run:

```powershell
git status --short
git log --oneline -10
```

Expected: Stage 35 files are committed. Pre-existing unrelated dirty/untracked files may remain; do not revert them.

---

## Self-Review

- Spec coverage: The plan covers report progress metadata, microbatch reuse counters, fallback trace, report-processing visibility, runtime shutdown regression coverage, SSE snapshot regression coverage, docs, and final verification.
- Placeholder scan: No step uses placeholder markers or fill-in language. Existing-code conditional steps name exact tests and exact snippets to keep or restore.
- Type consistency: `ReportProgress.metadata`, `ReportProgressEvent.metadata`, `ReportMicrobatchStats.to_metadata()`, `report_path`, `microbatch_reused_questions`, `microbatch_rerun_questions`, `microbatch_failed_questions`, and `full_session_fallback` use the same names across backend, frontend, tests, and docs.
