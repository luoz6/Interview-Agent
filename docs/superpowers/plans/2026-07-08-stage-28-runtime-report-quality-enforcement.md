# Stage 28 Runtime Report Quality Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce Stage 27 report-quality rules in the real report-generation runtime so structurally valid but user-visible low-quality reports are not silently persisted as completed grounded reports.

**Architecture:** Keep the current Local V1 persistence model and API contract stable. Add a thin runtime quality-enforcement layer inside `report_tasks` that classifies report-quality issues, fails the report on blocking issues, and optionally records non-blocking quality warnings into the existing trace pipeline without introducing new database schema or UI changes in this stage.

**Tech Stack:** Python 3.11, FastAPI, pytest, existing `InterviewReport` models, existing `ReportTraceRecorder`, PostgreSQL/in-memory session stores, current report worker job loop.

---

## File Structure

- Create: `app/services/report_runtime_quality.py`
  - Owns runtime-specific report-quality policy: classify quality issues into blocking vs warning, and expose a small validation result object.

- Modify: `app/services/report_tasks.py`
  - Applies runtime quality enforcement after evaluator output and before `save_report()` / `save_question_evaluations()`.

- Modify: `app/services/report.py`
  - Add a typed runtime failure for blocking report-quality violations, reusing existing worker failure flow without changing `ReportRecord` schema.

- Modify: `tests/test_report_tasks.py`
  - Covers blocking quality failures, warning-only passes, fallback-report behavior, and question-evaluation persistence boundaries.

- Modify: `tests/test_report_worker.py`
  - Verifies worker behavior for runtime quality failures stays terminal/non-retryable and still marks the job/report failed.

- Create: `tests/test_report_runtime_quality.py`
  - Unit tests for the new runtime policy helper.

- Modify: `tests/test_report_api.py`
  - Verifies failed report records produced by quality enforcement are surfaced through the existing report endpoint error path.

- Modify: `tests/test_report_models.py`
  - Covers the new typed runtime quality failure class if needed at the report-model layer.

---

### Task 1: Add Runtime Quality Policy Helper

**Files:**
- Create: `app/services/report_runtime_quality.py`
- Modify: `app/services/report.py`
- Test: `tests/test_report_runtime_quality.py`
- Test: `tests/test_report_models.py`

- [ ] **Step 1: Write the failing runtime-quality policy tests**

Create `tests/test_report_runtime_quality.py`:

```python
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport, ReportQualityFailed
from app.services.report_runtime_quality import (
    RuntimeReportQualityResult,
    evaluate_runtime_report_quality,
)


def make_feedback(
    *,
    answer_state: str = "answered",
    score: int = 82,
    rationale: str = "回答说明了缓存失效主路径，并补充了竞争窗口和回退策略。",
    critique: str = "还可以补充监控闭环和量化收益。",
    better_answer: str = "建议补充双删、回退读取、监控指标和风险缓解。",
    user_answer: str = "我会在数据库提交后删除缓存，并补充回退读取和 p95 监控。",
) -> InterviewFeedback:
    return InterviewFeedback(
        question_id="q1",
        question_text="Explain Redis cache invalidation.",
        user_answer=user_answer,
        answer_state=answer_state,
        score=score,
        dimension_scores=DimensionScores(
            breadth=score,
            depth=score,
            architecture=score,
            engineering=score,
            communication=score,
        ),
        rationale=rationale,
        critique=critique,
        better_answer=better_answer,
        references=[],
    )


def make_report(*, summary: str, feedbacks: list[InterviewFeedback], is_fallback: bool = False) -> InterviewReport:
    score = feedbacks[0].score if feedbacks else 0
    return InterviewReport(
        session_id="s1",
        overall_score=score,
        overall_dimension_scores=DimensionScores(
            breadth=score,
            depth=score,
            architecture=score,
            engineering=score,
            communication=score,
        ),
        summary=summary,
        highlights=["回答覆盖了主流程。"],
        feedbacks=feedbacks,
        is_fallback=is_fallback,
    )


def test_runtime_report_quality_allows_clean_grounded_report():
    result = evaluate_runtime_report_quality(
        make_report(
            summary="回答主线完整，并解释了回退策略与一致性取舍。",
            feedbacks=[make_feedback()],
        ),
        expected_question_count=1,
    )

    assert result == RuntimeReportQualityResult(blocking_issues=[], warning_issues=[])


def test_runtime_report_quality_blocks_grounded_report_with_stage27_issues():
    result = evaluate_runtime_report_quality(
        make_report(
            summary="Strong answer with room for stronger metrics.",
            feedbacks=[
                make_feedback(
                    rationale="Good answer.",
                    critique="Needs more details.",
                    better_answer="Add more details.",
                )
            ],
        ),
        expected_question_count=1,
    )

    assert "summary must include Simplified Chinese text" in result.blocking_issues
    assert "feedback[q1].rationale must not be placeholder text" in result.blocking_issues
    assert result.warning_issues == []


def test_runtime_report_quality_does_not_block_fallback_report():
    result = evaluate_runtime_report_quality(
        make_report(
            summary="Evidence was insufficient for a grounded expert report.",
            feedbacks=[
                make_feedback(
                    rationale="Fallback report generated because grounded evidence was insufficient.",
                    critique="Needs sharper metrics.",
                    better_answer="I reduced p95 latency with Redis and fallback.",
                )
            ],
            is_fallback=True,
        ),
        expected_question_count=1,
    )

    assert result.blocking_issues == []
    assert "fallback report bypassed runtime quality enforcement" in result.warning_issues
```

Extend `tests/test_report_models.py`:

```python
from app.services.report import ReportGenerationFailed, ReportQualityFailed


def test_report_quality_failed_is_a_report_generation_failure():
    error = ReportQualityFailed("summary must include Simplified Chinese text")

    assert isinstance(error, ReportGenerationFailed)
    assert "summary must include Simplified Chinese text" in str(error)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_runtime_quality.py tests/test_report_models.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.report_runtime_quality'` and/or `ImportError` for `ReportQualityFailed`.

- [ ] **Step 3: Implement the minimal runtime-quality policy**

Create `app/services/report_runtime_quality.py`:

```python
from dataclasses import dataclass

from app.services.report import InterviewReport
from app.services.report_quality import collect_report_quality_issues


@dataclass(frozen=True)
class RuntimeReportQualityResult:
    blocking_issues: list[str]
    warning_issues: list[str]


def evaluate_runtime_report_quality(
    report: InterviewReport,
    *,
    expected_question_count: int,
) -> RuntimeReportQualityResult:
    if report.is_fallback:
        return RuntimeReportQualityResult(
            blocking_issues=[],
            warning_issues=["fallback report bypassed runtime quality enforcement"],
        )
    return RuntimeReportQualityResult(
        blocking_issues=collect_report_quality_issues(
            report,
            expected_question_count=expected_question_count,
        ),
        warning_issues=[],
    )
```

Add to `app/services/report.py`:

```python
class ReportQualityFailed(ReportGenerationFailed):
    """Raised when a generated report violates blocking runtime quality rules."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_runtime_quality.py tests/test_report_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/report.py app/services/report_runtime_quality.py tests/test_report_runtime_quality.py tests/test_report_models.py
git commit -m "feat: add runtime report quality policy"
```

---

### Task 2: Enforce Blocking Quality Failures In Report Tasks

**Files:**
- Modify: `app/services/report_tasks.py`
- Modify: `tests/test_report_tasks.py`
- Test: `tests/test_report_tasks.py`

- [ ] **Step 1: Write the failing report-task enforcement tests**

Add these tests to `tests/test_report_tasks.py`:

```python
def test_execute_report_generation_raises_report_quality_failed_for_invalid_grounded_report():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    class InvalidGroundedReportLLM(ReportLLM):
        def stream_followup(self, context: list[dict[str, str]]):
            return iter(())

        def generate_report(
            self,
            plan: InterviewPlan,
            evaluation_items: list[dict],
            session_id: str,
        ) -> InterviewReport:
            return InterviewReport(
                session_id=session_id,
                overall_score=81,
                overall_dimension_scores=make_dimension_scores(81),
                summary="Strong backend fundamentals.",
                highlights=["Explained tradeoffs"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Introduce a project.",
                        user_answer="I built a cache service.",
                        score=81,
                        dimension_scores=make_dimension_scores(81),
                        rationale="Good answer.",
                        critique="Needs more details.",
                        better_answer="Add more details.",
                        references=[],
                    )
                ],
            )

    invalid_llm = InvalidGroundedReportLLM()
    store = InterviewSessionStore(llm=invalid_llm)
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    with pytest.raises(ReportQualityFailed, match="runtime report quality check failed"):
        execute_report_generation(
            session_id=session.session_id,
            store=store,
            llm=invalid_llm,
            vector_store=FakeVectorStore(),
        )

    assert store.get_report_record(session.session_id).status == "processing"
    assert store.list_question_evaluations(session.session_id) == []


def test_run_report_generation_marks_failed_for_invalid_grounded_report():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    class InvalidGroundedReportLLM(ReportLLM):
        def stream_followup(self, context: list[dict[str, str]]):
            return iter(())

        def generate_report(
            self,
            plan: InterviewPlan,
            evaluation_items: list[dict],
            session_id: str,
        ) -> InterviewReport:
            return InterviewReport(
                session_id=session_id,
                overall_score=81,
                overall_dimension_scores=make_dimension_scores(81),
                summary="Strong backend fundamentals.",
                highlights=["Explained tradeoffs"],
                feedbacks=[
                    InterviewFeedback(
                        question_id="q1",
                        question_text="Introduce a project.",
                        user_answer="I built a cache service.",
                        score=81,
                        dimension_scores=make_dimension_scores(81),
                        rationale="Good answer.",
                        critique="Needs more details.",
                        better_answer="Add more details.",
                        references=[],
                    )
                ],
            )

    invalid_llm = InvalidGroundedReportLLM()
    store = InterviewSessionStore(llm=invalid_llm)
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = run_report_generation(
        session_id=session.session_id,
        store=store,
        llm=invalid_llm,
        vector_store=FakeVectorStore(),
    )

    assert report is None
    record = store.get_report_record(session.session_id)
    assert record.status == "failed"
    assert "runtime report quality check failed" in record.error
    assert record.report is None
    assert store.list_question_evaluations(session.session_id) == []


def test_generate_report_for_session_keeps_completed_fallback_report_when_quality_text_is_english():
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    import app.services.report_tasks as report_tasks

    report_tasks.get_knowledge_store = lambda: FakeVectorStore()
    store = InterviewSessionStore(llm=FallbackReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    generate_report_for_session(session.session_id, store)

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report is not None
    assert record.report.is_fallback is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py -q
```

Expected: FAIL because the first two invalid-grounded tests still save completed reports; the fallback-bypass regression may already PASS.

- [ ] **Step 3: Implement runtime enforcement in report_tasks**

Update `app/services/report_tasks.py`:

```python
from app.services.report import ReportGenerationFailed, ReportGenerationTimeout, ReportQualityFailed
from app.services.report_runtime_quality import evaluate_runtime_report_quality
```

Inside `execute_report_generation()` after `report = evaluator.evaluate(...)` and before `store.save_report(...)`, add:

```python
    quality = evaluate_runtime_report_quality(
        report,
        expected_question_count=len(state["plan"].questions),
    )
    if quality.blocking_issues:
        raise ReportQualityFailed(
            "runtime report quality check failed: " + "; ".join(quality.blocking_issues)
        )
```

Do not save the report or question evaluations when blocking issues exist.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/report_tasks.py tests/test_report_tasks.py
git commit -m "feat: enforce runtime report quality checks"
```

---

### Task 3: Keep Worker Failure Semantics Stable

**Files:**
- Modify: `tests/test_report_worker.py`
- Test: `tests/test_report_worker.py`

This task is a subtype-specific regression guard. It is intentionally independent of Task 2's integration path, so the new test may already pass on the current worker implementation.

- [ ] **Step 1: Write the failing worker regression**

Add to `tests/test_report_worker.py`:

```python
def test_run_one_job_marks_terminal_failure_for_runtime_quality_failure(monkeypatch):
    def raise_quality_failure(**kwargs):
        raise ReportQualityFailed(
            "runtime report quality check failed: summary must include Simplified Chinese text"
        )

    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        raise_quality_failure,
    )
    job_store = FakeJobStore(claimed_job={"job_id": "job-1", "session_id": "s1"})
    store = FakeStore()

    result = run_one_job(
        job_store=job_store,
        executor=make_executor(store),
        worker_id="worker-1",
    )

    assert result["status"] == "failed"
    assert job_store.retry_calls == []
    assert job_store.failed_calls == [
        ("job-1", "runtime report quality check failed: summary must include Simplified Chinese text")
    ]
    assert store.failed_reports == [
        ("s1", "runtime report quality check failed: summary must include Simplified Chinese text")
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_worker.py -q
```

Expected: This may already PASS on the current worker code. If it fails, the regression is that `ReportQualityFailed` is being treated as retryable or is not being persisted consistently.

- [ ] **Step 3: Adjust retryability classification if needed**

Keep `RETRYABLE_FAILURE_MESSAGES` in `app/services/report_worker.py` unchanged:

```python
RETRYABLE_FAILURE_MESSAGES = {
    "pgvector knowledge store is unavailable",
}
```

Do not add runtime quality failures to the retryable set. If the new test still fails, update only the exact branch conditions needed so `ReportQualityFailed` follows the same terminal-failure path as other non-retryable `ReportGenerationFailed` errors.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_worker.py tests/test_report_tasks.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_report_worker.py
git commit -m "test: cover worker handling for report quality failures"
```

---

### Task 4: Surface Quality-Triggered Failures Through Existing API Contracts

**Files:**
- Modify: `tests/test_report_api.py`
- Test: `tests/test_report_api.py`

- [ ] **Step 1: Write the failing API regression**

Add to `tests/test_report_api.py`:

```python
def test_report_endpoint_returns_quality_failure_detail():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)
    store.fail_report(
        session_id,
        "runtime report quality check failed: summary must include Simplified Chinese text",
    )

    response = client.get(f"/api/interviews/{session_id}/report")

    assert response.status_code == 500
    assert (
        response.json()["detail"]
        == "runtime report quality check failed: summary must include Simplified Chinese text"
    )
```

- [ ] **Step 2: Run tests to verify they pass or expose hidden API coupling**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py -q
```

Expected: PASS if the current API already forwards failed-report errors unchanged. If it fails, update only the existing error-forwarding path rather than adding a new response shape.

- [ ] **Step 3: Commit if code changed**

If no application code changed, commit only the test:

```bash
git add tests/test_report_api.py
git commit -m "test: cover api quality failure detail"
```

If application code also changed, include the touched file paths in `git add`.

---

### Task 5: Record Non-Blocking Runtime Quality Warnings In Trace Artifacts

**Files:**
- Modify: `app/services/report_tasks.py`
- Modify: `tests/test_report_tasks.py`
- Test: `tests/test_report_tasks.py`

- [ ] **Step 1: Write the failing trace-warning regression**

Add to `tests/test_report_tasks.py`:

```python
def test_execute_report_generation_records_warning_for_fallback_quality_bypass(tmp_path, monkeypatch):
    class FakeVectorStore:
        def search(self, query_text: str, *, job_tags: list[str], source_types=None, limit=5):
            return []

    monkeypatch.setenv("REPORT_TRACE_DIR", str(tmp_path))
    store = InterviewSessionStore(llm=FallbackReportLLM())
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = execute_report_generation(
        session_id=session.session_id,
        store=store,
        llm=store.llm,
        vector_store=FakeVectorStore(),
    )

    trace_files = sorted((tmp_path / session.session_id).glob("*_runtime_quality.json"))
    assert report.is_fallback is True
    assert trace_files
    assert "fallback report bypassed runtime quality enforcement" in trace_files[0].read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py::test_execute_report_generation_records_warning_for_fallback_quality_bypass -q
```

Expected: FAIL because `report_tasks.py` does not currently write runtime-quality traces.

- [ ] **Step 3: Write the minimal trace hook**

In `app/services/report_tasks.py`, add a small helper near the bottom:

```python
def _record_runtime_quality_warning(session_id: str, warning_issues: list[str]) -> None:
    if not warning_issues:
        return
    from app.services.report_trace import ReportTraceRecorder

    ReportTraceRecorder.from_env().record(
        session_id=session_id,
        stage="runtime_quality",
        payload={"warning_issues": warning_issues},
    )
```

Then call it inside `execute_report_generation()` after `quality = evaluate_runtime_report_quality(...)`:

```python
    _record_runtime_quality_warning(session_id, quality.warning_issues)
```

Do not persist warnings into `ReportRecord`, PostgreSQL tables, or API responses in this stage.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/report_tasks.py tests/test_report_tasks.py
git commit -m "feat: trace runtime report quality warnings"
```

---

## Verification Sweep

After all five tasks are complete, run the full Stage 28 regression sweep:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_runtime_quality.py tests/test_report_tasks.py tests/test_report_worker.py tests/test_report_api.py tests/test_report_models.py -q
```

If the branch already includes the Stage 27 files listed below, run the Stage 27 compatibility sweep as well:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_quality.py tests/test_golden_dataset.py tests/test_report_replay_quality.py tests/test_eval_snapshots.py tests/test_real_llm_eval.py tests/test_report_contract.py tests/test_report_evaluator.py tests/test_llm_report_service.py -q
& 'F:\python3.11\python.exe' scripts/replay_report_payloads.py tests/fixtures/report_payloads --strict
```

Then run the full repository sweep:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected:

- Stage 28 runtime-enforcement tests pass.
- If Stage 27 files are present on the branch, the Stage 27 evaluation harness remains green and replay strict mode still exits `0`.
- Full `pytest` remains green.

## Self-Review

- Spec coverage: This plan covers runtime enforcement, worker failure semantics, API-visible failure behavior, and trace-only warning recording without expanding schema or UI surface.
- Placeholder scan: No `TODO`, `TBD`, or “same as previous task” placeholders remain; each task includes concrete tests, commands, and expected failures/passes.
- Type consistency: The plan reuses existing `ReportGenerationFailed` flow, adds a dedicated `ReportQualityFailed` subtype, keeps `ReportRecord` unchanged, and confines runtime policy logic to a new helper module plus `report_tasks`.
