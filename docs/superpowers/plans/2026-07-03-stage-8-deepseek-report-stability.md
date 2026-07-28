# Stage 8 DeepSeek Report Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make real-model interview reports reliably produce grounded non-fallback `InterviewReport` objects under DeepSeek-compatible APIs, while preserving fallback behavior only for genuine output-format failures.

**Architecture:** Split provider/runtime failures from output-format failures. `OpenAIInterviewLLM.generate_report()` will become a three-stage pipeline: structured output attempt, raw-JSON prompt fallback, and strict Pydantic validation. Evaluators will only build fallback reports for a new typed format error, while provider failures continue to propagate into the report worker retry/fail state machine.

**Tech Stack:** Python 3.11, FastAPI, LangChain `ChatOpenAI`, Pydantic v2, pytest, PostgreSQL runtime store, DeepSeek OpenAI-compatible API.

---

## File Structure

- Modify: `app/services/report.py`
  - Add a dedicated typed exception for invalid report output format so fallback decisions are explicit.

- Modify: `app/services/llm.py`
  - Refactor report generation into focused helpers:
    - report prompt builder
    - structured-output attempt
    - raw JSON prompt fallback
    - JSON extraction and validation
    - provider-vs-format failure classification
  - Add logging for structured-output degradation and raw JSON validation failures.

- Modify: `app/services/evaluator.py`
  - Fallback only on typed output-format failures, not on generic `ValueError`.

- Modify: `app/services/evaluator_ext.py`
  - Same fallback boundary as `ShadowEvaluator`.
  - Log fallback reason with `session_id`.

- Modify: `app/services/report_worker.py`
  - Log when a job completes with a fallback report versus a grounded report.

- Modify: `tests/test_llm_report_service.py`
  - Add DeepSeek-style raw JSON parsing, schema validation, and provider-failure classification tests.

- Modify: `tests/test_report_evaluator.py`
  - Update fallback tests to use the new typed format exception and add a propagation test for provider failures.

- Modify: `tests/test_report_tasks.py`
  - Add integration tests that run `OpenAIInterviewLLM(chat_model=...)` through `run_report_generation(...)`.

- Modify: `tests/test_report_worker.py`
  - Add a log-level behavior test for fallback-vs-grounded completion.

---

### Task 1: Introduce A Typed Report Output Failure Boundary

**Files:**
- Modify: `tests/test_report_evaluator.py`
- Modify: `app/services/report.py`
- Modify: `app/services/evaluator.py`
- Modify: `app/services/evaluator_ext.py`

- [ ] **Step 1: Write the failing evaluator tests**

Update `tests/test_report_evaluator.py` by replacing the generic `ValueError` fallback fixture with a typed format error fixture and by adding a provider-failure propagation test. Keep the historical `FailingReportLLM` test double name if you want, but change its raised exception to `ReportOutputFormatError`; otherwise the existing fallback tests will stop covering the intended branch after the evaluator narrows to `except ReportOutputFormatError`:

```python
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
)


class FailingReportLLM(FakeReportLLM):
    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise ReportOutputFormatError("invalid structured output")


class ProviderErrorReportLLM(FakeReportLLM):
    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise ReportGenerationFailed("report provider returned 502")


def test_evaluator_returns_fallback_completed_report_when_output_format_is_invalid():
    evaluator = ShadowEvaluator(llm=FailingReportLLM())

    report = evaluator.evaluate(make_finished_state())

    assert report.status == "completed"
    assert report.is_fallback is True
    assert report.overall_score == 60


def test_evaluator_propagates_provider_failures_for_worker_retry_logic():
    evaluator = ShadowEvaluator(llm=ProviderErrorReportLLM())

    with pytest.raises(ReportGenerationFailed, match="report provider returned 502"):
        evaluator.evaluate(make_finished_state())
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_evaluator.py -q
```

Expected: FAIL because `ReportOutputFormatError` does not exist yet and the evaluators still fall back on generic `ValueError`.

- [ ] **Step 3: Add the typed exception**

Modify `app/services/report.py`:

```python
class ReportGenerationFailed(RuntimeError):
    """Raised when report generation should be marked as failed."""


class ReportGenerationTimeout(ReportGenerationFailed):
    """Raised when report generation times out."""


class ReportOutputFormatError(ValueError):
    """Raised when a provider response cannot be validated as InterviewReport."""
```

- [ ] **Step 4: Narrow fallback behavior in both evaluators**

Modify `app/services/evaluator.py`:

```python
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
    ReportGenerationFailed,
    ReportGenerationTimeout,
    ReportOutputFormatError,
)


class ShadowEvaluator:
    def evaluate(self, state: InterviewState) -> InterviewReport:
        chunks = build_evaluation_chunks(state)
        try:
            if self._llm is None:
                raise ReportGenerationFailed("report llm is not configured")
            return self._llm.generate_report(
                plan=state["plan"],
                evaluation_items=[chunk.model_dump() for chunk in chunks],
                session_id=state["session_id"],
            )
        except ReportGenerationTimeout:
            raise
        except ReportGenerationFailed:
            raise
        except ReportOutputFormatError:
            return build_fallback_report(state, chunks)
```

Modify `app/services/evaluator_ext.py`:

```python
import logging

from app.services.report import (
    InterviewReport,
    ReportGenerationFailed,
    ReportOutputFormatError,
    ReportProgress,
)


logger = logging.getLogger(__name__)


class ExpertShadowEvaluator:
    def evaluate(...):
        ...
        try:
            report = self._llm.generate_report(
                plan=state["plan"],
                evaluation_items=evaluation_items,
                session_id=state["session_id"],
            )
        except ReportOutputFormatError as exc:
            logger.warning(
                "Falling back to heuristic interview report",
                extra={"session_id": state["session_id"], "reason": str(exc)},
            )
            report = build_fallback_report(state, chunks)
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_evaluator.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/services/report.py app/services/evaluator.py app/services/evaluator_ext.py tests/test_report_evaluator.py
git commit -m "refactor: distinguish report output format failures"
```

---

### Task 2: Harden `OpenAIInterviewLLM.generate_report()` For DeepSeek-Compatible Output

**Files:**
- Modify: `tests/test_llm_report_service.py`
- Modify: `app/services/llm.py`

- [ ] **Step 1: Write the failing LLM report tests**

Extend `tests/test_llm_report_service.py` with two new fake chat models and three tests:

```python
import pytest

from app.services.llm import OpenAIInterviewLLM
from app.services.report import InterviewReport, ReportGenerationFailed, ReportOutputFormatError


class ProseWrappedJsonChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            '''
            Here is the final report:

            ```json
            {
              "session_id": "s1",
              "overall_score": 84,
              "overall_dimension_scores": {
                "breadth": 84,
                "depth": 84,
                "architecture": 84,
                "engineering": 84,
                "communication": 84
              },
              "summary": "Strong technical basics.",
              "highlights": ["Explained Redis fallback"],
              "feedbacks": [
                {
                  "question_id": "q1",
                  "question_text": "Please introduce a backend project.",
                  "user_answer": "The candidate described FastAPI and Redis.",
                  "score": 84,
                  "dimension_scores": {
                    "breadth": 84,
                    "depth": 84,
                    "architecture": 84,
                    "engineering": 84,
                    "communication": 84
                  },
                  "rationale": "The answer covered the main cache strategy.",
                  "critique": "The answer needs clearer metrics.",
                  "better_answer": "I built a FastAPI API with Redis cache and measured p95 latency.",
                  "references": []
                }
              ],
              "status": "completed",
              "is_fallback": false
            }
            ```
            '''
        )


class InvalidSchemaJsonChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            {
              "session_id": "s1",
              "overall_score": 84,
              "summary": "Missing fields on purpose"
            }
            """
        )


class ProviderFailureChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        raise RuntimeError("upstream provider returned 502")


def test_generate_report_parses_json_wrapped_in_prose_and_code_fences():
    llm = OpenAIInterviewLLM(chat_model=ProseWrappedJsonChatModel())
    report = llm.generate_report(plan=make_plan(), evaluation_items=make_items(), session_id="s1")

    assert isinstance(report, InterviewReport)
    assert report.is_fallback is False
    assert report.feedbacks[0].question_id == "q1"


def test_generate_report_raises_typed_format_error_for_schema_invalid_json():
    llm = OpenAIInterviewLLM(chat_model=InvalidSchemaJsonChatModel())

    with pytest.raises(ReportOutputFormatError, match="schema validation"):
        llm.generate_report(plan=make_plan(), evaluation_items=make_items(), session_id="s1")


def test_generate_report_raises_report_generation_failed_for_provider_failure():
    llm = OpenAIInterviewLLM(chat_model=ProviderFailureChatModel())

    with pytest.raises(ReportGenerationFailed, match="upstream provider returned 502"):
        llm.generate_report(plan=make_plan(), evaluation_items=make_items(), session_id="s1")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_report_service.py -q
```

Expected: FAIL because `generate_report()` still collapses all fallback behavior into one `except Exception` block and does not distinguish schema-invalid JSON from provider failures.

- [ ] **Step 3: Refactor report generation into explicit helper stages**

Modify `app/services/llm.py`:

```python
import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, Protocol

from pydantic import ValidationError

from app.services.report import ReportGenerationFailed, ReportOutputFormatError


logger = logging.getLogger(__name__)


class OpenAIInterviewLLM:
    def generate_report(
        self,
        plan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> "InterviewReport":
        from app.services.report import InterviewReport

        prompt = self._build_report_prompt(
            plan=plan,
            evaluation_items=evaluation_items,
            session_id=session_id,
        )
        structured_error = None
        try:
            return self._invoke_structured_report(prompt, InterviewReport)
        except ReportOutputFormatError as exc:
            structured_error = exc
            logger.warning("Structured report output was invalid", extra={"session_id": session_id})
        except Exception as exc:
            structured_error = exc
            logger.warning("Structured report output failed, trying raw JSON path", extra={"session_id": session_id})

        try:
            return self._invoke_raw_json_report(prompt, InterviewReport)
        except ReportOutputFormatError:
            raise
        except Exception as exc:
            raise self._classify_report_failure(exc, structured_error)

    def _invoke_structured_report(self, prompt: str, schema):
        structured_model = self.chat_model.with_structured_output(schema, method="json_schema")
        result = structured_model.invoke(prompt)
        return self._coerce_report_result(result, schema)

    def _invoke_raw_json_report(self, prompt: str, schema):
        fallback_prompt = (
            f"{prompt}\n\n"
            "Return valid JSON only. The JSON must match the InterviewReport schema exactly. "
            "Do not wrap the JSON in markdown code fences."
        )
        message = self.chat_model.invoke(fallback_prompt)
        content = str(getattr(message, "content", message)).strip()
        return self._validate_report_json_content(content, schema)

    def _coerce_report_result(self, result, schema):
        if isinstance(result, schema):
            return result
        try:
            return schema.model_validate(result)
        except ValidationError as exc:
            raise ReportOutputFormatError(f"structured output schema validation failed: {exc}") from exc

    def _validate_report_json_content(self, content: str, schema):
        try:
            return schema.model_validate_json(_extract_json_object(content))
        except (ValidationError, ValueError) as exc:
            raise ReportOutputFormatError(f"raw report JSON schema validation failed: {exc}") from exc

    def _classify_report_failure(self, exc: Exception, prior_error: Exception | None):
        message = str(exc)
        if prior_error is not None:
            message = f"{message}; structured_error={prior_error}"
        return ReportGenerationFailed(message)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_llm_report_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/llm.py tests/test_llm_report_service.py
git commit -m "feat: harden deepseek report json generation"
```

---

### Task 3: Cover The Real Fallback Boundary Through `run_report_generation(...)`

**Files:**
- Modify: `tests/test_report_tasks.py`
- Modify: `app/services/evaluator_ext.py`

- [ ] **Step 1: Write the failing report-task integration tests**

Add two tests to `tests/test_report_tasks.py`:

```python
from app.services.llm import OpenAIInterviewLLM


class WrappedJsonFallbackChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage(
            """
            Final answer:
            {
              "session_id": "s1",
              "overall_score": 88,
              "overall_dimension_scores": {
                "breadth": 88,
                "depth": 88,
                "architecture": 88,
                "engineering": 88,
                "communication": 88
              },
              "summary": "Clear backend tradeoff explanation.",
              "highlights": ["Explained Redis consistency"],
              "feedbacks": [
                {
                  "question_id": "q1",
                  "question_text": "Introduce a project.",
                  "user_answer": "I built a cache service.",
                  "score": 88,
                  "dimension_scores": {
                    "breadth": 88,
                    "depth": 88,
                    "architecture": 88,
                    "engineering": 88,
                    "communication": 88
                  },
                  "rationale": "The answer showed practical implementation detail.",
                  "critique": "Needs sharper metrics.",
                  "better_answer": "I reduced p95 latency with Redis and fallback.",
                  "references": []
                }
              ],
              "status": "completed",
              "is_fallback": false
            }
            """
        )


class InvalidJsonFallbackChatModel:
    def with_structured_output(self, schema, method=None):
        return FailingStructuredModel()

    def invoke(self, prompt: str):
        return FakeJsonMessage('{"session_id":"s1","overall_score":"bad"}')


def test_run_report_generation_saves_grounded_report_when_raw_json_path_is_valid():
    store = InterviewSessionStore(llm=OpenAIInterviewLLM(chat_model=WrappedJsonFallbackChatModel()))
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    report = run_report_generation(
        session_id=session.session_id,
        store=store,
        llm=store.llm,
        vector_store=FakeVectorStore(),
    )

    assert report is not None
    assert report.is_fallback is False
    assert store.get_report_record(session.session_id).status == "completed"


def test_run_report_generation_saves_fallback_completed_report_when_raw_json_is_invalid():
    store = InterviewSessionStore(llm=OpenAIInterviewLLM(chat_model=InvalidJsonFallbackChatModel()))
    session = start_session(store)
    finish_session(store, session.session_id)
    store.mark_report_processing(session.session_id)

    run_report_generation(
        session_id=session.session_id,
        store=store,
        llm=store.llm,
        vector_store=FakeVectorStore(),
    )

    record = store.get_report_record(session.session_id)
    assert record.status == "completed"
    assert record.report is not None
    assert record.report.is_fallback is True
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py -q
```

Expected: FAIL because typed format failures are not yet converted into evaluator fallback through the real `OpenAIInterviewLLM` path.

- [ ] **Step 3: Keep evaluator fallback local and observable**

Modify `app/services/evaluator_ext.py` so the format failure log includes session and question count without changing the fallback result:

```python
from app.services.report import (
    InterviewReport,
    ReportGenerationFailed,
    ReportOutputFormatError,
    ReportProgress,
)


logger.warning(
    "Falling back to heuristic interview report",
    extra={
        "session_id": state["session_id"],
        "reason": str(exc),
        "question_count": len(chunks),
    },
)
report = build_fallback_report(state, chunks)
```

No `report_tasks.py` logic change should be required if Task 1 and Task 2 were implemented correctly.

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_tasks.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/evaluator_ext.py tests/test_report_tasks.py
git commit -m "test: cover llm json fallback through report tasks"
```

---

### Task 4: Surface Fallback-Vs-Grounded Completion In Worker Logs

**Files:**
- Modify: `tests/test_report_worker.py`
- Modify: `app/services/report_worker.py`

- [ ] **Step 1: Write the failing worker log test**

Add one `caplog`-based test to `tests/test_report_worker.py`:

```python
import logging


def test_run_one_job_logs_when_completion_uses_fallback_report(monkeypatch, caplog):
    def complete_with_fallback(**kwargs):
        report = make_report(kwargs["session_id"])
        report.is_fallback = True
        return report

    monkeypatch.setattr(
        "app.services.report_worker.execute_report_generation",
        complete_with_fallback,
    )
    job_store = FakeJobStore(claimed_job={"job_id": "job-1", "session_id": "s1"})

    with caplog.at_level(logging.WARNING):
        result = run_one_job(
            job_store=job_store,
            executor=make_executor(),
            worker_id="worker-1",
        )

    assert result["status"] == "completed"
    assert "fallback report" in caplog.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_worker.py -q
```

Expected: FAIL because the worker currently marks jobs completed without any distinction in logs.

- [ ] **Step 3: Add completion logging in the worker**

Modify `app/services/report_worker.py`:

```python
import logging
import os
import socket
import time


logger = logging.getLogger(__name__)


def run_one_job(*, job_store, executor, worker_id: str):
    ...
    try:
        report = execute_report_generation(
            session_id=job["session_id"],
            store=executor.store,
            llm=executor.llm,
            vector_store=executor.vector_store,
        )
        assert report is not None
        if report.is_fallback:
            logger.warning(
                "Report job completed with fallback report",
                extra={"job_id": job["job_id"], "session_id": job["session_id"]},
            )
        else:
            logger.info(
                "Report job completed with grounded report",
                extra={"job_id": job["job_id"], "session_id": job["session_id"]},
            )
        return job_store.mark_completed(job["job_id"])
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_worker.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/report_worker.py tests/test_report_worker.py
git commit -m "feat: log fallback versus grounded report completion"
```

---

### Task 5: Verify With Real DeepSeek-Compatible Durable Mode

**Files:**
- Modify as needed only if verification reveals a bug.

- [ ] **Step 1: Run focused regression tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_evaluator.py tests/test_llm_report_service.py tests/test_report_tasks.py tests/test_report_worker.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the full suite**

Run:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run the durable-mode smoke with real provider settings**

Run API:

```powershell
$env:INTERVIEW_RUNTIME_STORE='postgres'
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
$env:OPENAI_API_KEY='<your-deepseek-key>'
$env:OPENAI_BASE_URL='https://api.deepseek.com'
$env:OPENAI_MODEL='deepseek-v4-pro'
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Run worker:

```powershell
$env:INTERVIEW_RUNTIME_STORE='postgres'
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
$env:OPENAI_API_KEY='<your-deepseek-key>'
$env:OPENAI_BASE_URL='https://api.deepseek.com'
$env:OPENAI_MODEL='deepseek-v4-pro'
& 'F:\python3.11\python.exe' -m app.services.report_worker
```

Success criteria:

```text
- Finish a real interview through HTTP
- /report transitions from 202 to 200
- report.is_fallback is false
- report.feedbacks[*].references is non-empty for at least one question
- worker logs do not contain "fallback report" for the successful session
```

- [ ] **Step 4: If the smoke still falls back, inspect the exact raw provider output**

Check the worker and API logs for:

```text
- structured output failure message
- raw JSON schema validation failure message
- truncated provider response body
```

Only if the smoke still fails, patch the parser or prompt and rerun Step 3 before moving on.

- [ ] **Step 5: Commit verification fixes**

```powershell
git add app/services app/api tests
git commit -m "test: verify deepseek report stability in durable mode"
```

---

## Self-Review

Spec coverage:

- typed fallback boundary is covered in Task 1
- DeepSeek-compatible raw JSON fallback and schema validation are covered in Task 2
- real `run_report_generation(...)` fallback semantics are covered in Task 3
- worker observability is covered in Task 4
- real durable-mode validation is covered in Task 5

Placeholder scan:

- every task contains named files, concrete tests, commands, and code snippets
- no `TODO`, `TBD`, or “similar to previous task” placeholders remain

Type consistency:

- `ReportOutputFormatError` is introduced once in `app/services/report.py`
- `tests/test_report_evaluator.py` keeps the familiar `FailingReportLLM` test double name, but its raised exception changes from `ValueError` to `ReportOutputFormatError`
- evaluators fallback only on `ReportOutputFormatError`
- provider/runtime problems stay on `ReportGenerationFailed`
- worker logs key off `report.is_fallback`, not ad hoc string matching, and `run_one_job()` asserts that the worker-only execution path returned a real report object
