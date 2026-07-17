# Stage 43A Multi-Agent Runtime Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add versioned multi-Agent execution metadata, end-to-end correlation, sanitized traces, and a versioned round-review event envelope without changing existing Agent business outputs or scoring behavior.

**Architecture:** Keep the five existing domain Agents and deterministic LangGraph routing. Add a shared `AgentExecutionContext`, an execution runner that observes normal/fallback/error/stream cancellation paths, and a trace recorder keyed by the Prep correlation ID; extend `RoundClosedEvent` with a backward-compatible runtime envelope and migrate Agent call sites incrementally.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, LangGraph, Celery, Redis event transport, PostgreSQL, pytest, Playwright.

---

## Execution Preconditions

Do not start implementation until `docs/stage-42b-knowledge-continuity-acceptance.md` records a fresh post-fix real-model `PASS`. Stage 43A must start from a named Stage 42 baseline commit or tag so scoring and evidence-continuity regressions can be attributed accurately.

Use the repository Python 3.11 interpreter for every command:

```powershell
& 'F:\python3.11\python.exe' -m pytest
```

Do not add Redis checkpoints, WebSocket routes, database migrations, new Agent roles, dynamic Agent selection, or LLM-based routing in this plan.

## File Map

**New runtime contract and trace files:**

- `app/services/agent_runtime.py`: versioned context, run record, correlation helpers, and execution runner.
- `app/services/agent_trace.py`: environment-configured, best-effort Agent trace writer.
- `app/services/trace_sanitization.py`: policy-driven recursive privacy sanitizer that preserves the existing Knowledge trace behavior.
- `tests/test_agent_runtime.py`: contract and runner behavior.
- `tests/test_agent_trace.py`: trace paths, serialization, and privacy.
- `scripts/audit_agent_runtime.py`: formal Stage 43A correlation and privacy auditor.
- `tests/test_agent_runtime_audit.py`: auditor gates.
- `docs/stage-43a-multi-agent-runtime-acceptance.md`: acceptance record.

**Existing integration files:**

- `app/services/runtime_domain_events.py`: versioned runtime event envelope.
- `app/services/interview_rounds.py`: envelope creation from committed interview state.
- `app/services/event_publisher.py`: unchanged transport behavior, expanded payload assertions.
- `app/services/prep.py`: create the Prep correlation ID and wrap Knowledge execution.
- `app/services/knowledge_grounding.py`: persist the caller-owned Prep correlation ID.
- `app/agents/examiner.py`: use the runner for sync and streaming fallback paths.
- `app/graphs/interview_graph.py`: build Examiner contexts from state and evidence bindings.
- `app/graphs/orchestrator_graph.py`: allow command correlation metadata.
- `app/agents/orchestrator.py`: trace deterministic phase transitions.
- `app/services/session.py`: pass command IDs into orchestration.
- `app/services/postgres_session.py`: pass command IDs into orchestration.
- `app/services/round_review_runner.py`: trace question-scoped Shadow Reviewer execution.
- `app/services/report_tasks.py`: trace full-session Shadow Reviewer fallback.
- `app/agents/report_coach.py`: trace final report coaching.
- `app/services/report_microbatch.py`: pass Report Coach context and event correlation.
- `app/services/evaluator_ext.py`: pass Report Coach context in full-session evaluation.
- `app/services/knowledge_trace.py`: use the shared sanitizer.
- `.env.example`, `README.md`, `docs/local-v1-runbook.md`: configuration and verification documentation.

## Risk Controls

| Area | Risk | Mandatory control |
| --- | --- | --- |
| Runtime contracts | Low | New Pydantic files first; no Agent business output changes. |
| Trace sanitization | Medium | Preserve Knowledge substring blocking with a characterization test; use a separate exact-key Agent policy; leave Report trace unchanged. |
| Execution runner | Low | Use the verified `app.services.report.utc_now_iso`; test completed, classified degraded, fallback degraded, failed, and cancelled paths. |
| Runtime events | Medium | Assert `InterviewState` fields and API post-commit event values; verify identical Local/Celery JSON envelopes. |
| Knowledge integration | Medium-high | Freeze the complete v1 fallback before replacing `prepare_interview()` exception handling; never fabricate a v2 snapshot. |
| Examiner/Orchestrator | High | Split into two commits; pass current `command_id` ephemerally and keep `_advance_state_metadata()` as the only persistence owner. |
| Reviewer integration | Low | Wrap only evaluator invocation and preserve `_evaluate_full_session()` tuple and retrieval metadata. |
| Report Coach | Medium | Test microbatch and full-session paths as a pair; keep backend score/reference ownership unchanged. |
| Browser acceptance | Medium | Audit one persisted Prep correlation directory, not the trace root; keep deterministic and real-provider runs separate. |

### Task 1: Define Agent Runtime Contracts and Correlation Helpers

**Files:**

- Create: `app/services/agent_runtime.py`
- Create: `tests/test_agent_runtime.py`

- [ ] **Step 1: Write failing contract tests**

Create `tests/test_agent_runtime.py` with tests for stable schema values, unique run IDs, trusted evidence deduplication, correlation lookup, and rejection of raw payload fields:

```python
import pytest
from pydantic import ValidationError

from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentRunRecord,
    correlation_id_from_plan,
)
from app.services.prep import (
    InterviewPlan,
    KnowledgeBindingSnapshot,
    PrepContext,
)


def make_plan(prep_run_id: str | None = "prep-123") -> InterviewPlan:
    context = None
    if prep_run_id is not None:
        context = PrepContext(
            summary='Grounded prep context.',
            schema_version="v2",
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id=prep_run_id,
                corpus_manifest_sha256='manifest-123',
                status="completed",
            ),
        )
    return InterviewPlan(title="Backend interview", questions=[], prep_context=context)


def test_agent_execution_context_has_stable_schema_and_unique_run_id():
    first = AgentExecutionContext(
        correlation_id="prep-123",
        agent="knowledge",
        operation="generate_plan",
        phase="prep",
    )
    second = first.model_copy(update={"run_id": None}, deep=True)
    second = AgentExecutionContext.model_validate(
        {key: value for key, value in second.model_dump().items() if value is not None}
    )

    assert first.schema_version == "agent-runtime-v1"
    assert first.run_id.startswith("agent-")
    assert second.run_id.startswith("agent-")
    assert first.run_id != second.run_id


def test_agent_execution_context_deduplicates_evidence_ids():
    context = AgentExecutionContext(
        correlation_id="prep-123",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id="s1",
        question_id="q1",
        evidence_ids=["redis-1", "redis-1", "mysql-1"],
    )

    assert context.evidence_ids == ["redis-1", "mysql-1"]


def test_agent_execution_context_forbids_unknown_raw_payload_fields():
    with pytest.raises(ValidationError):
        AgentExecutionContext.model_validate(
            {
                "correlation_id": "prep-123",
                "agent": "examiner",
                "operation": "generate_followup",
                "phase": "interview",
                "candidate_answer": "raw answer must not enter runtime metadata",
            }
        )


def test_correlation_id_uses_prep_run_then_session_fallback():
    assert correlation_id_from_plan(make_plan(), session_id="s1") == "prep-123"
    assert correlation_id_from_plan(make_plan(None), session_id="s1") == "s1"


def test_agent_run_record_rejects_negative_latency():
    context = AgentExecutionContext(
        correlation_id="prep-123",
        agent="report_coach",
        operation="generate_report",
        phase="review",
        session_id="s1",
    )

    with pytest.raises(ValidationError):
        AgentRunRecord(
            **context.model_dump(),
            status="completed",
            started_at="2026-07-16T00:00:00Z",
            finished_at="2026-07-16T00:00:01Z",
            latency_ms=-1,
            output_type="InterviewReport",
        )
```

- [ ] **Step 2: Run the tests and confirm the module is missing**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.services.agent_runtime'`.

- [ ] **Step 3: Implement the contracts and helpers**

Create `app/services/agent_runtime.py` with these public types and helpers:

```python
from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


AgentName = Literal[
    "orchestrator",
    "knowledge",
    "examiner",
    "shadow_reviewer",
    "report_coach",
]
AgentPhase = Literal["prep", "interview", "review"]
AgentRunStatus = Literal["completed", "degraded", "failed", "cancelled"]


class AgentExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["agent-runtime-v1"] = "agent-runtime-v1"
    run_id: str = Field(default_factory=lambda: f"agent-{uuid4().hex}")
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None
    agent: AgentName
    operation: str = Field(min_length=1)
    phase: AgentPhase
    session_id: str | None = None
    question_id: str | None = None
    state_version: int | None = Field(default=None, ge=1)
    command_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence_ids")
    @classmethod
    def deduplicate_evidence_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item for item in value if item))


class AgentRunRecord(AgentExecutionContext):
    status: AgentRunStatus
    started_at: str
    finished_at: str
    latency_ms: float = Field(ge=0)
    fallback_reason: str | None = None
    error_code: str | None = None
    output_type: str | None = None
    safe_metadata: dict[str, Any] = Field(default_factory=dict)


def correlation_id_from_plan(plan, *, session_id: str | None = None) -> str:
    prep_context = getattr(plan, "prep_context", None)
    snapshot = getattr(prep_context, "binding_snapshot", None)
    prep_run_id = getattr(snapshot, "prep_run_id", None)
    if isinstance(prep_run_id, str) and prep_run_id:
        return prep_run_id
    if isinstance(session_id, str) and session_id:
        return session_id
    return f"prep-{uuid4().hex}"


def evidence_ids_for_question(plan, question_id: str | None) -> list[str]:
    if not question_id:
        return []
    prep_context = getattr(plan, "prep_context", None)
    hints = getattr(prep_context, "question_hints", []) if prep_context else []
    for hint in hints:
        if hint.question_id == question_id:
            return list(dict.fromkeys(hint.evidence_ids))
    return []
```

Keep `AgentRunRecord.safe_metadata` for counters and route names only. Do not add an arbitrary input or output payload field.

- [ ] **Step 4: Run contract tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit the contracts**

```powershell
git add app/services/agent_runtime.py tests/test_agent_runtime.py
git commit -m "feat: define versioned agent runtime contracts"
```

### Task 2: Add Shared Trace Sanitization and Agent Trace Recording

**Files:**

- Create: `app/services/trace_sanitization.py`
- Create: `app/services/agent_trace.py`
- Create: `tests/test_agent_trace.py`
- Modify: `app/services/knowledge_trace.py`
- Test: `tests/test_knowledge_trace.py`

- [ ] **Step 1: Write failing privacy and path tests**

Create `tests/test_agent_trace.py`:

```python
import json

from app.services.agent_runtime import AgentRunRecord
from app.services.agent_trace import AgentTraceRecorder


def make_record() -> AgentRunRecord:
    return AgentRunRecord(
        correlation_id="prep-123",
        causation_id="cmd-1",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id="s1",
        question_id="q1",
        state_version=2,
        command_id="cmd-1",
        evidence_ids=["redis-1"],
        status="completed",
        started_at="2026-07-16T00:00:00Z",
        finished_at="2026-07-16T00:00:00.100000Z",
        latency_ms=100,
        output_type="str",
        safe_metadata={
            "chunk_count": 2,
            "prompt": "secret prompt",
            "nested": {"provider_response": "secret response"},
        },
    )


def test_agent_trace_writes_under_correlation_directory(tmp_path):
    target = AgentTraceRecorder(tmp_path).record(make_record())

    assert target is not None
    assert target.parent == tmp_path / "prep-123"
    assert "examiner_generate_followup" in target.name
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["correlation_id"] == "prep-123"
    assert payload["evidence_ids"] == ["redis-1"]


def test_agent_trace_removes_sensitive_nested_fields(tmp_path):
    target = AgentTraceRecorder(tmp_path).record(make_record())
    payload = target.read_text(encoding="utf-8")

    assert "secret prompt" not in payload
    assert "secret response" not in payload
    assert '"prompt"' not in payload
    assert '"provider_response"' not in payload


def test_agent_trace_is_disabled_without_directory():
    assert AgentTraceRecorder(None).record(make_record()) is None


def test_agent_trace_correlation_cannot_escape_root(tmp_path):
    record = make_record().model_copy(
        update={
            'correlation_id': '../../outside',
            'run_id': '../run',
            'operation': '../operation',
        }
    )

    target = AgentTraceRecorder(tmp_path).record(record)

    assert target is not None
    assert target.resolve().is_relative_to(tmp_path.resolve())
```

Add this compatibility assertion to `tests/test_knowledge_trace.py` before changing the sanitizer:

```python
def test_knowledge_trace_keeps_legacy_substring_blocking(tmp_path):
    recorder = KnowledgeTraceRecorder(root_dir=tmp_path)

    path = recorder.record(
        prep_run_id="prep-legacy-policy",
        stage="retrieval",
        payload={
            "hit_ids": ["redis-1"],
            "content_sha256": "a" * 64,
            "safe_counter": 1,
        },
    )

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["hit_ids"] == ["redis-1"]
    assert body["safe_counter"] == 1
    assert "content_sha256" not in body
```

Run this one characterization test against the current implementation. Expected: `PASS`; it freezes behavior that the shared sanitizer must retain.

- [ ] **Step 2: Run the new tests and confirm missing modules**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_trace.py -q
```

Expected: collection fails because `agent_trace` does not exist.

- [ ] **Step 3: Implement the shared sanitizer**

Create `app/services/trace_sanitization.py`:

```python
from typing import Any


AGENT_TRACE_BLOCKED_KEYS = {
    "answer",
    "api_key",
    "authorization",
    "candidate_answer",
    "content",
    "dsn",
    "embedding",
    "job_description",
    "password",
    "prompt",
    "provider_response",
    "raw_content",
    "raw_response",
    "resume",
    "resume_text",
    "secret",
    "token",
    "user_answer",
}

KNOWLEDGE_TRACE_BLOCKED_KEY_PARTS = (
    "api_key",
    "authorization",
    "content",
    "dsn",
    "embedding",
    "password",
    "provider_response",
    "raw_response",
    "resume",
    "secret",
    "token",
)


def sanitize_trace_payload(
    value: Any,
    *,
    blocked_keys=frozenset(),
    blocked_key_parts=(),
):
    if isinstance(value, dict):
        return {
            str(key): sanitize_trace_payload(
                item,
                blocked_keys=blocked_keys,
                blocked_key_parts=blocked_key_parts,
            )
            for key, item in value.items()
            if not is_blocked_trace_key(
                str(key),
                blocked_keys=blocked_keys,
                blocked_key_parts=blocked_key_parts,
            )
        }
    if isinstance(value, (list, tuple)):
        return [
            sanitize_trace_payload(
                item,
                blocked_keys=blocked_keys,
                blocked_key_parts=blocked_key_parts,
            )
            for item in value
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def is_blocked_trace_key(key: str, *, blocked_keys, blocked_key_parts) -> bool:
    normalized = key.casefold()
    return normalized in blocked_keys or any(
        part in normalized for part in blocked_key_parts
    )


def safe_trace_path_segment(value: str) -> str:
    normalized = ''.join(
        character if character.isalnum() or character in '._-' else '_'
        for character in value
    )
    return normalized[:128] or 'unknown'
```

- [ ] **Step 4: Implement AgentTraceRecorder**

Create `app/services/agent_trace.py`:

```python
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.services.agent_runtime import AgentRunRecord
from app.services.trace_sanitization import (
    AGENT_TRACE_BLOCKED_KEYS,
    safe_trace_path_segment,
    sanitize_trace_payload,
)


@dataclass
class AgentTraceRecorder:
    root_dir: Path | None

    @classmethod
    def from_env(cls) -> "AgentTraceRecorder":
        raw_dir = os.getenv("AGENT_TRACE_DIR")
        return cls(Path(raw_dir) if raw_dir else None)

    def record(self, record: AgentRunRecord) -> Path | None:
        if self.root_dir is None:
            return None
        target_dir = self.root_dir / safe_trace_path_segment(record.correlation_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        target = target_dir / (
            f"{timestamp}_{safe_trace_path_segment(record.run_id)}_"
            f"{safe_trace_path_segment(record.agent)}_"
            f"{safe_trace_path_segment(record.operation)}.json"
        )
        payload = sanitize_trace_payload(
            record.model_dump(mode="json"),
            blocked_keys=AGENT_TRACE_BLOCKED_KEYS,
        )
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return target
```

- [ ] **Step 5: Move Knowledge trace to the shared legacy policy**

In `app/services/knowledge_trace.py`, import `KNOWLEDGE_TRACE_BLOCKED_KEY_PARTS` and `sanitize_trace_payload`, replace `_sanitize(payload)` with:

```python
sanitize_trace_payload(
    payload,
    blocked_key_parts=KNOWLEDGE_TRACE_BLOCKED_KEY_PARTS,
)
```

Remove only the local `_BLOCKED_KEY_PARTS`, `_sanitize`, and `_blocked_key` definitions after the compatibility test is green. Do not modify `ReportTraceRecorder` in Stage 43A; its `raw_content` debugging contract is outside the Agent trace privacy boundary.

- [ ] **Step 6: Run all trace tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_trace.py tests/test_knowledge_trace.py -q
```

Expected: all tests pass; Knowledge still removes `content_sha256`, while Agent trace removes exact sensitive keys including `raw_content` without hiding safe identifier fields.

- [ ] **Step 7: Commit trace infrastructure**

```powershell
git add app/services/trace_sanitization.py app/services/agent_trace.py app/services/knowledge_trace.py tests/test_agent_trace.py tests/test_knowledge_trace.py
git commit -m "feat: add sanitized agent execution traces"
```

### Task 3: Implement Completed, Degraded, Failed, and Cancelled Execution Paths

**Files:**

- Modify: `app/services/agent_runtime.py`
- Modify: `tests/test_agent_runtime.py`

- [ ] **Step 1: Add failing runner tests**

Append tests that use an in-memory recorder:

```python
from app.services.agent_runtime import (
    AgentExecutionRunner,
    AgentFallback,
    AgentOutcome,
)


class CapturingRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def make_examiner_context() -> AgentExecutionContext:
    return AgentExecutionContext(
        correlation_id="prep-123",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id="s1",
        question_id="q1",
    )


def test_runner_records_completed_call():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    result = runner.run(make_examiner_context(), lambda: "follow up")

    assert result == "follow up"
    assert recorder.records[0].status == "completed"
    assert recorder.records[0].output_type == "str"


def test_runner_records_valid_degraded_output_from_classifier():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    result = runner.run(
        make_examiner_context(),
        lambda: "usable fallback plan",
        classify=lambda output: AgentOutcome(
            status="degraded",
            reason="knowledge_unavailable",
        ),
    )

    assert result == "usable fallback plan"
    assert recorder.records[0].status == "degraded"
    assert recorder.records[0].fallback_reason == "knowledge_unavailable"


def test_runner_records_degraded_fallback_without_exception_text():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    result = runner.run(
        make_examiner_context(),
        lambda: (_ for _ in ()).throw(RuntimeError("provider secret")),
        fallback=lambda exc: AgentFallback("fallback", "provider_error"),
    )

    assert result == "fallback"
    assert recorder.records[0].status == "degraded"
    assert recorder.records[0].fallback_reason == "provider_error"
    assert "provider secret" not in recorder.records[0].model_dump_json()


def test_runner_records_failed_call_and_reraises():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    with pytest.raises(ValueError, match="bad output"):
        runner.run(
            make_examiner_context(),
            lambda: (_ for _ in ()).throw(ValueError("bad output")),
        )

    assert recorder.records[0].status == "failed"
    assert recorder.records[0].error_code == "ValueError"


def test_stream_runner_records_cancelled_consumer():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)
    stream = runner.stream(
        make_examiner_context(),
        lambda: iter(["one", "two"]),
    )

    assert next(stream) == "one"
    stream.close()

    assert recorder.records[0].status == "cancelled"
    assert recorder.records[0].fallback_reason == "client_disconnected"
```

- [ ] **Step 2: Run runner tests and confirm missing symbols**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime.py -q
```

Expected: import fails for `AgentExecutionRunner` and `AgentFallback`.

- [ ] **Step 3: Implement the runner in agent_runtime.py**

Add imports for `dataclass`, `perf_counter`, `Callable`, `Generic`, `Iterable`, `Iterator`, `Protocol`, `TypeVar`, and `utc_now_iso`. Add:

```python
from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Generic, Iterable, Iterator, Protocol, TypeVar

from app.services.report import utc_now_iso


T = TypeVar("T")


class AgentRunRecorder(Protocol):
    def record(self, record: AgentRunRecord):
        pass


@dataclass(frozen=True)
class AgentFallback(Generic[T]):
    output: T
    reason: str


@dataclass(frozen=True)
class AgentOutcome:
    status: Literal["completed", "degraded"] = "completed"
    reason: str | None = None


class AgentExecutionRunner:
    def __init__(self, *, recorder: AgentRunRecorder | None = None) -> None:
        if recorder is None:
            from app.services.agent_trace import AgentTraceRecorder

            recorder = AgentTraceRecorder.from_env()
        self._recorder = recorder

    def run(
        self,
        context: AgentExecutionContext,
        invoke: Callable[[], T],
        *,
        fallback: Callable[[Exception], AgentFallback[T]] | None = None,
        metadata: Callable[[T], dict[str, Any]] | None = None,
        classify: Callable[[T], AgentOutcome] | None = None,
    ) -> T:
        started_at = utc_now_iso()
        started = perf_counter()
        try:
            output = invoke()
        except Exception as exc:
            if fallback is None:
                self._emit(
                    context,
                    status="failed",
                    started_at=started_at,
                    started=started,
                    error_code=type(exc).__name__,
                )
                raise
            resolved = fallback(exc)
            self._emit(
                context,
                status="degraded",
                started_at=started_at,
                started=started,
                fallback_reason=resolved.reason,
                output=resolved.output,
                safe_metadata=metadata(resolved.output) if metadata else {},
            )
            return resolved.output
        outcome = classify(output) if classify else AgentOutcome()
        self._emit(
            context,
            status=outcome.status,
            started_at=started_at,
            started=started,
            fallback_reason=outcome.reason,
            output=output,
            safe_metadata=metadata(output) if metadata else {},
        )
        return output

    def stream(
        self,
        context: AgentExecutionContext,
        invoke: Callable[[], Iterable[T]],
        *,
        fallback: Callable[[Exception], AgentFallback[Iterable[T]]] | None = None,
    ) -> Iterator[T]:
        started_at = utc_now_iso()
        started = perf_counter()
        emitted = 0
        status: AgentRunStatus = "completed"
        fallback_reason = None
        error_code = None
        try:
            try:
                for item in invoke():
                    emitted += 1
                    yield item
            except Exception as exc:
                if fallback is None:
                    status = "failed"
                    error_code = type(exc).__name__
                    raise
                resolved = fallback(exc)
                status = "degraded"
                fallback_reason = resolved.reason
                for item in resolved.output:
                    emitted += 1
                    yield item
        except GeneratorExit:
            status = "cancelled"
            fallback_reason = "client_disconnected"
            raise
        except Exception as exc:
            status = "failed"
            error_code = type(exc).__name__
            raise
        finally:
            self._emit(
                context,
                status=status,
                started_at=started_at,
                started=started,
                fallback_reason=fallback_reason,
                error_code=error_code,
                output_type="stream",
                safe_metadata={"emitted_chunks": emitted},
            )

    def _emit(
        self,
        context: AgentExecutionContext,
        *,
        status: AgentRunStatus,
        started_at: str,
        started: float,
        fallback_reason: str | None = None,
        error_code: str | None = None,
        output: Any = None,
        output_type: str | None = None,
        safe_metadata: dict[str, Any] | None = None,
    ) -> None:
        record = AgentRunRecord(
            **context.model_dump(),
            status=status,
            started_at=started_at,
            finished_at=utc_now_iso(),
            latency_ms=round((perf_counter() - started) * 1000, 3),
            fallback_reason=fallback_reason,
            error_code=error_code,
            output_type=output_type or (type(output).__name__ if output is not None else None),
            safe_metadata=safe_metadata or {},
        )
        try:
            self._recorder.record(record)
        except Exception:
            return
```

The runner deliberately records exception class names but never exception messages.

- [ ] **Step 4: Run runtime and trace tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime.py tests/test_agent_trace.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit execution runner**

```powershell
git add app/services/agent_runtime.py tests/test_agent_runtime.py
git commit -m "feat: trace agent execution outcomes"
```

### Task 4: Version the Round-Closed Runtime Event Envelope

**Files:**

- Modify: `app/services/runtime_domain_events.py`
- Modify: `app/services/interview_rounds.py`
- Modify: `app/services/report_microbatch.py`
- Modify: `tests/test_event_publisher.py`
- Modify: `tests/test_interview_rounds.py`
- Modify: `tests/test_report_microbatch.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Add failing envelope and transport assertions**

In `tests/test_interview_rounds.py`, extend the event-from-transition test to assert:

```python
assert event.schema_version == "runtime-event-v1"
assert event.event_id.startswith("event-")
assert event.correlation_id == state["plan"].prep_context.binding_snapshot.prep_run_id
assert event.causation_id == after_state["last_command_id"]
assert event.state_version == after_state["state_version"]
```

In `tests/test_event_publisher.py`, assert the Local and Celery serialized payloads preserve `schema_version`, `event_id`, `correlation_id`, `causation_id`, and `state_version`.

Extend the existing answer, skip, and streaming `round_closed` tests in `tests/test_api.py`. Assert the published event uses the post-command snapshot: `event.state_version` equals the response/snapshot state version and `event.causation_id` equals the request `command_id`. This verifies the route publishes only after `_advance_state_metadata()` commits both fields.

Add a compatibility test:

```python
def test_round_closed_event_accepts_legacy_payload_defaults():
    event = RoundClosedEvent.model_validate(
        {
            "event_type": "round_closed",
            "session_id": "s1",
            "question_id": "q1",
            "answer_state": "answered",
        }
    )

    assert event.schema_version == "runtime-event-v1"
    assert event.correlation_id == "s1"
    assert event.state_version is None
```

- [ ] **Step 2: Run focused event tests and confirm missing fields**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_rounds.py tests/test_event_publisher.py tests/test_api.py -q -k "round_closed or runtime_event"
```

Expected: assertions fail because the runtime envelope fields do not exist.

- [ ] **Step 3: Implement the backward-compatible envelope**

Replace `app/services/runtime_domain_events.py` with:

```python
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.services.report import utc_now_iso


class RuntimeEventEnvelope(BaseModel):
    schema_version: Literal["runtime-event-v1"] = "runtime-event-v1"
    event_id: str = Field(default_factory=lambda: f"event-{uuid4().hex}")
    session_id: str
    correlation_id: str | None = None
    causation_id: str | None = None
    state_version: int | None = Field(default=None, ge=1)
    emitted_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def default_correlation_to_session(self):
        if not self.correlation_id:
            self.correlation_id = self.session_id
        return self


class RoundClosedEvent(RuntimeEventEnvelope):
    event_type: Literal["round_closed"] = "round_closed"
    question_id: str
    answer_state: Literal["answered", "skipped", "unanswered"]
    job_tags: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Populate envelope fields from interview state**

In `app/services/interview_rounds.py`, import `correlation_id_from_plan` and construct the event with:

```python
return RoundClosedEvent(
    session_id=after_state["session_id"],
    correlation_id=correlation_id_from_plan(
        after_state["plan"],
        session_id=after_state["session_id"],
    ),
    causation_id=after_state.get("last_command_id"),
    state_version=after_state["state_version"],
    question_id=closed_question.id,
    answer_state=_answer_state_for_question(after_state, closed_question.id),
    job_tags=list(after_state["job_tags"]),
)
```

In `app/services/report_microbatch.py`, populate rerun events from the stored state using the same correlation helper, `state_version`, and `last_command_id`.

- [ ] **Step 5: Run event, publisher, and microbatch tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_rounds.py tests/test_event_publisher.py tests/test_report_microbatch.py tests/test_api.py -q -k "round_closed or runtime_event or microbatch"
```

Expected: all tests pass and both publishers carry identical envelope fields.

- [ ] **Step 6: Commit event envelope**

```powershell
git add app/services/runtime_domain_events.py app/services/interview_rounds.py app/services/report_microbatch.py tests/test_event_publisher.py tests/test_interview_rounds.py tests/test_report_microbatch.py tests/test_api.py
git commit -m "feat: version round review runtime events"
```

### Task 5: Correlate and Trace Knowledge Agent Preparation

**Files:**

- Modify: `app/services/prep.py`
- Modify: `app/agents/knowledge.py`
- Modify: `app/services/knowledge_grounding.py`
- Modify: `tests/test_prep_service.py`
- Modify: `tests/test_grounded_knowledge_agent.py`
- Modify: `tests/test_knowledge_trace.py`

- [ ] **Step 1: Freeze fallback behavior, then write failing Prep correlation tests**

Import `fallback_interview_plan` from `app.services.prep` and `make_repository` from `tests.test_grounded_knowledge_agent`. First add and run this characterization test against the unmodified `prepare_interview()` boundary:

```python
def test_prepare_interview_provider_failure_keeps_complete_v1_fallback():
    expected = fallback_interview_plan()

    plan = prepare_interview(
        "Backend role using Redis",
        "Built a Redis API",
        llm=FailingPlanLLM(),
        knowledge_store=make_repository(),
    )

    assert plan.title == expected.title
    assert [question.id for question in plan.questions] == [
        question.id for question in expected.questions
    ]
    assert plan.prep_context is not None
    assert plan.prep_context.schema_version == "v1"
    assert plan.prep_context.binding_snapshot is None
```

Expected before refactoring: `PASS`. This locks the complete fallback plan and prevents the runner migration from returning a partial plan or fabricating a v2 binding snapshot.

Add a `CapturingRecorder` and injected `AgentExecutionRunner` to `tests/test_prep_service.py`. Assert that one Knowledge run uses the same ID later persisted in `binding_snapshot.prep_run_id`:

```python
def test_prepare_interview_correlates_knowledge_run_with_binding_snapshot():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    plan = prepare_interview(
        "Backend role using Redis",
        "Built a Redis API",
        llm=PlanLLM(),
        knowledge_store=make_repository(),
        execution_runner=runner,
    )

    prep_run_id = plan.prep_context.binding_snapshot.prep_run_id
    assert recorder.records[0].agent == "knowledge"
    assert recorder.records[0].operation == "generate_plan"
    assert recorder.records[0].correlation_id == prep_run_id
    assert recorder.records[0].status == "completed"
```

Add a provider-failure test that asserts the fallback plan remains usable and the run is `degraded` with `fallback_reason == "plan_generation_failed"`.

- [ ] **Step 2: Run focused Prep tests and confirm signature failures**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_service.py -q -k "correlates_knowledge or plan_generation_failed"
```

Expected: tests fail because `prepare_interview` does not accept `execution_runner` and the snapshot ID is created too late.

- [ ] **Step 3: Allow the caller to own prep_run_id**

Change `attach_grounded_prep_context()` in `app/services/knowledge_grounding.py` to accept `prep_run_id: str | None = None` and set:

```python
snapshot = KnowledgeBindingSnapshot(
    prep_run_id=prep_run_id or f"prep-{uuid4().hex}",
    corpus_manifest_sha256=result.corpus_manifest_sha256,
    queries=[_query_snapshot(retrieval) for retrieval in result.retrievals],
    status=result.status,
    degraded_reason=result.degraded_reason,
)
```

Change `KnowledgeAgent.generate_plan()` to accept `prep_run_id: str | None = None` and pass it to `attach_grounded_prep_context()`.

- [ ] **Step 4: Wrap prepare_interview with the execution runner**

Add `execution_runner: AgentExecutionRunner | None = None` to `prepare_interview()`. Create the correlation ID before invoking Knowledge:

```python
runner = execution_runner or AgentExecutionRunner()
correlation_id = f"prep-{uuid4().hex}"
context = AgentExecutionContext(
    correlation_id=correlation_id,
    agent="knowledge",
    operation="generate_plan",
    phase="prep",
)
agent = KnowledgeAgent(llm=llm, vector_store=knowledge_store)

return runner.run(
    context,
    lambda: agent.generate_plan(
        job_description=job_description,
        resume_text=resume_text,
        prep_run_id=correlation_id,
    ),
    fallback=lambda exc: AgentFallback(
        attach_prep_context(
            fallback_interview_plan(),
            job_description=job_description,
            resume_text=resume_text,
            job_tags=extract_job_tags(job_description),
        ),
        "plan_generation_failed",
    ),
    metadata=lambda plan: {
        "question_count": len(plan.questions),
        "knowledge_status": (
            plan.prep_context.knowledge_status if plan.prep_context else "legacy"
        ),
    },
    classify=lambda plan: (
        AgentOutcome(
            status="degraded",
            reason=(
                plan.prep_context.binding_snapshot.degraded_reason
                if plan.prep_context
                and plan.prep_context.binding_snapshot
                and plan.prep_context.binding_snapshot.degraded_reason
                else "knowledge_degraded"
            ),
        )
        if plan.prep_context and plan.prep_context.knowledge_status == "degraded"
        else AgentOutcome()
    ),
)
```

Import `AgentOutcome` with the other runtime types. Keep `attach_prep_context()` and the legacy v1 fallback schema unchanged. The degraded Knowledge trace retains its generated Prep correlation ID, while a later session created from a v1 plan explicitly falls back to `session_id` correlation. Remove the outer broad `try/except` only after the runner tests prove equivalent fallback behavior.

- [ ] **Step 5: Run all Prep and knowledge tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_prep_service.py tests/test_grounded_knowledge_agent.py tests/test_knowledge_trace.py -q
```

Expected: all tests pass; existing evidence IDs, hashes, and degradation reasons remain unchanged.

- [ ] **Step 6: Commit Knowledge integration**

```powershell
git add app/services/prep.py app/agents/knowledge.py app/services/knowledge_grounding.py tests/test_prep_service.py tests/test_grounded_knowledge_agent.py tests/test_knowledge_trace.py
git commit -m "feat: correlate knowledge agent preparation"
```

### Task 6: Trace Orchestrator and Examiner Without Changing Interview Semantics

**Files:**

- Modify: `app/graphs/orchestrator_graph.py`
- Modify: `app/agents/orchestrator.py`
- Modify: `app/agents/examiner.py`
- Modify: `app/graphs/interview_graph.py`
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Modify: `tests/test_agents.py`
- Modify: `tests/test_interview_graph.py`
- Modify: `tests/test_orchestrator_graph.py`
- Modify: `tests/test_session_service.py`
- Modify: `tests/test_postgres_session_store.py`

- [ ] **Step 1: Write failing Examiner completion and fallback trace tests**

In `tests/test_agents.py`, inject `AgentExecutionRunner` into `ExaminerAgent`, pass an `AgentExecutionContext`, and assert successful calls record `completed`, provider failures record `degraded/provider_error`, and returned follow-up text remains identical to current behavior. Add the same assertion for an exhausted or failing stream.

Use this construction in every new test:

```python
context = AgentExecutionContext(
    correlation_id="prep-123",
    causation_id="cmd-1",
    agent="examiner",
    operation="generate_followup",
    phase="interview",
    session_id="s1",
    question_id="q1",
    state_version=2,
    command_id="cmd-1",
    evidence_ids=["redis-1"],
)
```

- [ ] **Step 2: Write failing Orchestrator trace and command propagation tests**

In `tests/test_orchestrator_graph.py`, inject a capturing runner and invoke:

```python
updated = agent.apply_command(
    make_state(),
    {
        "kind": "answer",
        "answer": "I used Redis.",
        "command_id": "cmd-1",
    },
)
```

Assert one Orchestrator record has operation `answer`, causation/command ID `cmd-1`, the state version observed before mutation, and the plan Prep correlation ID.

Add a causation-order regression where `state["last_command_id"] = "cmd-previous"` and the new command carries `command_id="cmd-current"`. Assert both the Orchestrator record and nested Examiner record use `cmd-current`; `cmd-previous` must not appear as the current call's causation ID.

Extend `tests/test_session_service.py` and `tests/test_postgres_session_store.py` to assert stores pass the caller command ID into the Orchestrator command while preserving idempotency and version conflict behavior.

- [ ] **Step 3: Run focused tests and confirm runner injection is unsupported**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agents.py tests/test_orchestrator_graph.py tests/test_session_service.py tests/test_postgres_session_store.py -q
```

Expected: new tests fail on unsupported constructor parameters and absent command metadata.

- [ ] **Step 4: Integrate ExaminerAgent with the runner**

Add `execution_runner: AgentExecutionRunner | None = None` to `ExaminerAgent.__init__`. Add optional `execution_context` to `generate_followup()` and `stream_followup()`. When context is absent, build a local context with a generated correlation ID for compatibility tests.

Replace the synchronous `try/except` with:

```python
return self._execution_runner.run(
    execution_context,
    lambda: (self.llm or self._default_llm()).generate_followup(context),
    fallback=lambda exc: AgentFallback(
        fallback_followup(focus),
        "provider_error",
    ),
)
```

Add a private `_EmptyFollowupStream` exception and replace streaming exception handling with:

```python
def provider_stream():
    llm = self.llm or self._default_llm()
    emitted = False
    for chunk in llm.stream_followup(context):
        if not chunk:
            continue
        emitted = True
        yield chunk
    if not emitted:
        raise _EmptyFollowupStream()


yield from self._execution_runner.stream(
    execution_context,
    provider_stream,
    fallback=lambda exc: AgentFallback(
        [fallback_followup(focus)],
        "empty_provider_stream"
        if isinstance(exc, _EmptyFollowupStream)
        else "provider_error",
    ),
)
```

This preserves the current one-message fallback for both empty and failed provider streams while giving the two paths stable trace reasons.

- [ ] **Step 5: Build Examiner context in InterviewGraphRunner**

Add a private helper that derives correlation, question ID, state/command metadata, and evidence IDs:

```python
def _examiner_execution_context(
    state: InterviewState,
    *,
    command_id: str | None = None,
) -> AgentExecutionContext:
    question = get_current_question(state)
    question_id = question.id if question is not None else None
    effective_command_id = (
        command_id if command_id is not None else state.get("last_command_id")
    )
    return AgentExecutionContext(
        correlation_id=correlation_id_from_plan(
            state["plan"],
            session_id=state["session_id"],
        ),
        causation_id=effective_command_id,
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id=state["session_id"],
        question_id=question_id,
        state_version=state["state_version"],
        command_id=effective_command_id,
        evidence_ids=evidence_ids_for_question(state["plan"], question_id),
    )
```

Add optional `command_id` parameters to `InterviewGraphRunner.submit_answer()`, `InterviewGraphRunner.prepare_answer()`, and `brain_node()`. Pass the ephemeral value into `_examiner_execution_context(state, command_id=command_id)` for synchronous follow-up generation. `stream_followup()` runs after prepare-state persistence, so it calls `_examiner_execution_context(state)` and uses the committed `last_command_id`.

- [ ] **Step 5A: Verify and commit the isolated Examiner boundary**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agents.py tests/test_interview_graph.py -q
```

Expected: all Examiner completion, empty-stream fallback, provider-error fallback, cancellation, evidence ID, and explicit command-causation tests pass.

```powershell
git add app/agents/examiner.py app/graphs/interview_graph.py tests/test_agents.py tests/test_interview_graph.py
git commit -m "feat: trace examiner execution"
```

- [ ] **Step 6: Trace OrchestratorAgent and propagate command_id**

Add optional `command_id` to `OrchestratorCommand`. In `_run_interview_phase()`, pass `command.get("command_id")` into `submit_answer()` and `prepare_answer()`; do not write it into `InterviewState`. Add an injected runner to `OrchestratorAgent` and wrap `_graph.invoke()` with an `AgentExecutionContext` whose operation is the command kind and whose causation ID comes from the command dictionary, not `state.last_command_id`.

In both `app/services/session.py` and `app/services/postgres_session.py`, include `command_id` in `answer`, `prepare_stream`, `complete_stream`, `skip`, and `finish` command dictionaries. Keep `_advance_state_metadata()` as the only code that commits `last_command_id` and advances state versions.

- [ ] **Step 7: Run interview, orchestration, and store contract tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agents.py tests/test_interview_graph.py tests/test_orchestrator_graph.py tests/test_session_service.py tests/test_postgres_session_store.py -q
```

Expected: all tests pass, duplicate commands append no duplicate message, stale versions still raise 409-domain conflicts, and fallback text is unchanged.

- [ ] **Step 8: Commit live-path integration**

```powershell
git add app/graphs/orchestrator_graph.py app/agents/orchestrator.py app/services/session.py app/services/postgres_session.py tests/test_orchestrator_graph.py tests/test_session_service.py tests/test_postgres_session_store.py
git commit -m "feat: propagate orchestrator command causation"
```

### Task 7: Trace Question and Full-Session Shadow Reviewer Execution

**Files:**

- Modify: `app/services/round_review_runner.py`
- Modify: `app/services/report_tasks.py`
- Modify: `tests/test_round_review.py`
- Modify: `tests/test_report_tasks.py`
- Modify: `tests/test_report_tasks_microbatch.py`

- [ ] **Step 1: Add failing question-review trace tests**

In `tests/test_round_review.py`, pass a `RoundClosedEvent` containing `correlation_id="prep-123"`, `event_id="event-1"`, and `state_version=3`. Inject an execution runner into `run_round_review_event_from_state()` and assert:

```python
record = recorder.records[0]
assert record.agent == "shadow_reviewer"
assert record.operation == "evaluate_round"
assert record.correlation_id == "prep-123"
assert record.causation_id == "event-1"
assert record.question_id == "q1"
assert record.state_version == 3
assert record.status == "completed"
```

Add a reviewer exception test asserting the trace is `failed` while the existing failed `QuestionEvaluationRecord` is still persisted.

- [ ] **Step 2: Add failing full-session fallback trace tests**

In `tests/test_report_tasks.py`, inject a runner into `_evaluate_full_session()` and destructure its existing tuple as `report, retrieval_metadata`. Assert `operation == "evaluate_full_session"`, correlation comes from the plan, `report` is the evaluator report, and `retrieval_metadata` remains the direct copy of `ShadowReviewerAgent.last_retrieval_by_question`.

- [ ] **Step 3: Run focused tests and confirm injection is unsupported**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_round_review.py tests/test_report_tasks.py -q -k "trace or evaluate_full_session"
```

Expected: tests fail because the execution runner is not accepted.

- [ ] **Step 4: Wrap round-review execution**

Add optional `execution_runner` to `run_round_review_event()`, `run_round_review_event_from_state()`, and payload forwarding. Build context from the event:

```python
context = AgentExecutionContext(
    correlation_id=event.correlation_id or event.session_id,
    causation_id=event.event_id,
    agent="shadow_reviewer",
    operation="evaluate_round",
    phase="review",
    session_id=event.session_id,
    question_id=event.question_id,
    state_version=event.state_version,
    evidence_ids=evidence_ids_for_question(state["plan"], event.question_id),
)
report = runner.run(context, lambda: reviewer.evaluate(review_state))
```

Do not add a fallback to the runner. The existing `except` block must continue to create the failed question record.

- [ ] **Step 5: Wrap full-session review execution**

Add keyword-only `execution_runner: AgentExecutionRunner | None = None` to `_evaluate_full_session()` and build a context from `state`. Wrap only `evaluator.evaluate(...)` without a fallback, then read the public `last_retrieval_by_question` property and return the unchanged `(report, dict(retrieval_metadata))` tuple. Existing callers in both `full_session` and `full_session_fallback` paths continue to destructure two values and require no behavioral branch.

- [ ] **Step 6: Run reviewer and report regressions**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_round_review.py tests/test_report_tasks.py tests/test_report_tasks_microbatch.py tests/test_expert_evaluator.py -q
```

Expected: all tests pass; question records preserve retrieval paths, evidence hashes, and failure states.

- [ ] **Step 7: Commit Reviewer integration**

```powershell
git add app/services/round_review_runner.py app/services/report_tasks.py tests/test_round_review.py tests/test_report_tasks.py tests/test_report_tasks_microbatch.py
git commit -m "feat: trace shadow reviewer execution"
```

### Task 8: Trace Report Coach Aggregation Without Changing Score Ownership

**Files:**

- Modify: `app/agents/report_coach.py`
- Modify: `app/services/report_microbatch.py`
- Modify: `app/services/evaluator_ext.py`
- Modify: `tests/test_agents.py`
- Modify: `tests/test_report_microbatch.py`
- Modify: `tests/test_expert_evaluator.py`

- [ ] **Step 1: Write failing Coach trace tests**

In `tests/test_agents.py`, inject a runner and pass a review-phase context to `ReportCoachAgent.generate_report()`. Assert `completed`, output type `InterviewReport`, and safe metadata contains feedback count but no report summary.

In `tests/test_report_microbatch.py`, assert the Coach context uses:

- Plan Prep correlation ID.
- Session ID.
- Operation `generate_microbatch_report`.
- Evidence IDs from completed question records.
- Safe metadata containing only `question_count` and `report_path`.

Add equivalent full-session assertions in `tests/test_expert_evaluator.py` with operation `generate_full_session_report`. Treat these as a two-path contract: both tests must assert correlation/session/evidence metadata, while their only allowed differences are operation name and `report_path`.

- [ ] **Step 2: Run focused Coach tests and confirm missing context support**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agents.py tests/test_report_microbatch.py tests/test_expert_evaluator.py -q -k "coach or report"
```

Expected: new assertions fail because `ReportCoachAgent` does not accept an execution context or runner.

- [ ] **Step 3: Integrate ReportCoachAgent**

Add `execution_runner` to the constructor and add `execution_context` plus `trace_metadata: dict | None = None` to `generate_report()`. Wrap the existing LLM call:

```python
return self._execution_runner.run(
    execution_context,
    lambda: llm.generate_report(
        plan=plan,
        evaluation_items=evaluation_items,
        session_id=session_id,
    ),
    metadata=lambda report: {
        "feedback_count": len(report.feedbacks),
        **dict(trace_metadata or {}),
    },
)
```

Do not configure a runner fallback. Restrict `trace_metadata` at call sites to integer counters and stable route names; never pass report text or evaluation items. Existing report fallback and quality enforcement remain in their current owners.

- [ ] **Step 4: Pass context from microbatch and full-session callers**

In `report_microbatch.py`, add optional `execution_runner` to `generate_microbatch_report()`, construct `ReportCoachAgent(llm=llm, execution_runner=execution_runner)`, build a Report Coach context with the deduplicated reference IDs present in completed records, and pass `trace_metadata={"question_count": len(records), "report_path": "microbatch"}`.

In `evaluator_ext.py`, add optional `execution_runner` to `ExpertShadowEvaluator.__init__()`, construct `ReportCoachAgent(llm=self._llm, execution_runner=self._execution_runner)`, build a context with operation `generate_full_session_report`, correlation from the plan, and deduplicated evaluator reference IDs, then pass `trace_metadata={"question_count": len(chunks), "report_path": "full_session"}`.

Do not let Report Coach add or replace reference IDs. `finalize_report_with_microbatch_feedback()` and backend score aggregation remain unchanged.

- [ ] **Step 5: Run Coach, scoring, and evidence continuity regressions**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agents.py tests/test_report_microbatch.py tests/test_expert_evaluator.py tests/test_report_rule_score.py tests/test_report_provider_adapter_scoring.py -q
```

Expected: all tests pass; Coach summaries may vary only within existing fixtures, while per-question scores and reference IDs stay backend-owned.

- [ ] **Step 6: Commit Report Coach integration**

```powershell
git add app/agents/report_coach.py app/services/report_microbatch.py app/services/evaluator_ext.py tests/test_agents.py tests/test_report_microbatch.py tests/test_expert_evaluator.py
git commit -m "feat: trace report coach aggregation"
```

### Task 9: Add Formal Agent Runtime Audit and Acceptance Documentation

**Files:**

- Create: `scripts/audit_agent_runtime.py`
- Create: `tests/test_agent_runtime_audit.py`
- Create: `docs/stage-43a-multi-agent-runtime-acceptance.md`
- Modify: `tests/browser_support_app.py`
- Modify: `tests/browser/local-v1.spec.js`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `app/api/routes.py`
- Test: `tests/test_api.py`
- Test: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Write failing auditor tests**

Create `tests/test_agent_runtime_audit.py` with fixtures for one valid chain and failures for missing Agent, mixed correlation IDs, blocked keys, raw candidate text, and absolute paths.

The valid chain must contain exactly these Agent names at least once:

```python
REQUIRED_AGENTS = {
    "knowledge",
    "orchestrator",
    "examiner",
    "shadow_reviewer",
    "report_coach",
}
```

Assert the auditor returns:

```python
{
    "status": "PASS",
    "schema_version": "agent-runtime-v1",
    "correlation_continuity_rate": 1.0,
    "required_agents_present": True,
    "privacy_violations": [],
}
```

- [ ] **Step 2: Run auditor tests and confirm the script is missing**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime_audit.py -q
```

Expected: collection fails because `scripts.audit_agent_runtime` does not exist.

- [ ] **Step 3: Implement the auditor**

Create `scripts/audit_agent_runtime.py` with:

```python
import argparse
import json
from pathlib import Path

from app.services.trace_sanitization import AGENT_TRACE_BLOCKED_KEYS


REQUIRED_AGENTS = {
    "knowledge",
    "orchestrator",
    "examiner",
    "shadow_reviewer",
    "report_coach",
}


def audit_agent_runtime(trace_dir: Path) -> dict:
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(trace_dir.rglob("*.json"))
    ]
    correlations = {
        payload.get("correlation_id") for payload in payloads if payload.get("correlation_id")
    }
    agents = {payload.get("agent") for payload in payloads if payload.get("agent")}
    violations = []
    for payload in payloads:
        _scan(payload, violations, path="$")
    continuity_rate = 1.0 if payloads and len(correlations) == 1 else 0.0
    passed = (
        bool(payloads)
        and REQUIRED_AGENTS.issubset(agents)
        and continuity_rate == 1.0
        and not violations
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "schema_version": "agent-runtime-v1",
        "correlation_continuity_rate": continuity_rate,
        "required_agents_present": REQUIRED_AGENTS.issubset(agents),
        "privacy_violations": sorted(set(violations)),
    }


def _scan(value, violations: list[str], *, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if str(key).casefold() in AGENT_TRACE_BLOCKED_KEYS:
                violations.append(child)
            _scan(item, violations, path=child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan(item, violations, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if ":\\" in value or value.startswith("/"):
            violations.append(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_dir", type=Path)
    args = parser.parse_args()
    result = audit_agent_runtime(args.trace_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add deterministic browser trace acceptance**

Configure `tests/browser_support_app.py` to accept an isolated `AGENT_TRACE_DIR` supplied by the Playwright test process. Extend the seeded browser flow to complete Prep, at least one Examiner follow-up, one question review, and final report generation. Read the persisted session plan's `binding_snapshot.prep_run_id` through the test support boundary and select exactly `<AGENT_TRACE_DIR>/<prep_run_id>` for auditing.

In `tests/browser/local-v1.spec.js`, after Report Detail is visible, call the audit script against that single correlation directory and assert exit code `0`. Do not audit the trace root because a browser run may create an abandoned Prep preview before the persisted interview plan. Keep the real-model smoke separate and opt-in.

- [ ] **Step 5: Expose non-sensitive runtime capability metadata**

In `app/api/routes.py`, add to `/runtime`:

```python
"agent_runtime": {
    "schema_version": "agent-runtime-v1",
    "event_schema_version": "runtime-event-v1",
    "trace_enabled": bool(os.getenv("AGENT_TRACE_DIR")),
},
```

Do not return the trace directory path. Add API tests for enabled and disabled states.

- [ ] **Step 6: Document configuration and acceptance commands**

Add `AGENT_TRACE_DIR=` to `.env.example` with a comment that traces are disabled when unset.

Update `README.md` and `docs/local-v1-runbook.md` with:

```powershell
$env:AGENT_TRACE_DIR="reports-local\agent-traces"
python -m scripts.audit_agent_runtime $env:AGENT_TRACE_DIR
```

State explicitly that Agent traces contain metadata and IDs only, Redis/WebSocket are not part of Stage 43A, and Stage 42 must already be `PASS`.

Create `docs/stage-43a-multi-agent-runtime-acceptance.md` with status `PENDING` and a gate table for Python, Local/Celery event transport, deterministic browser, correlation continuity, privacy audit, Stage 40 scoring, and Stage 42 evidence continuity. Change status to `PASS` only after every command in Step 8 succeeds.

- [ ] **Step 7: Run auditor, API, docs, and deterministic browser tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime_audit.py tests/test_api.py tests/test_local_v1_docs.py -q
npm run test:browser
```

Expected: auditor/API/docs tests pass; deterministic Playwright passes with one five-Agent correlation chain and no privacy violations.

- [ ] **Step 8: Run the full Stage 43A regression gate**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
& 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile core
```

Expected: full Python suite passes with only documented opt-in skips; all JavaScript syntax checks, CSS build, and core preflight pass.

With authenticated Redis/Celery configured, run:

```powershell
& 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile celery
& 'F:\python3.11\python.exe' -m scripts.celery_acceptance --timeout 150
```

Expected: the persisted Celery event retains `runtime-event-v1`, correlation, causation, and state version fields and produces the same question evaluation as the Local publisher.

- [ ] **Step 9: Record PASS and commit acceptance work**

Update `docs/stage-43a-multi-agent-runtime-acceptance.md` with exact test counts, run timestamps, correlation rate, privacy-audit result, and Celery profile result. Do not claim `PASS` if Stage 42 is not already `PASS` or if Celery envelope acceptance was not run.

```powershell
git add scripts/audit_agent_runtime.py tests/test_agent_runtime_audit.py tests/browser_support_app.py tests/browser/local-v1.spec.js app/api/routes.py tests/test_api.py .env.example README.md docs/local-v1-runbook.md docs/stage-43a-multi-agent-runtime-acceptance.md tests/test_local_v1_docs.py
git commit -m "test: gate multi-agent runtime correlation"
```

## Final Review Checklist

- [ ] Every Agent preserves its existing domain output type.
- [ ] All routing remains deterministic and backend-owned.
- [ ] One Prep correlation ID reaches all five Agent trace records.
- [ ] Examiner fallback is visible as degraded and remains non-blocking.
- [ ] Reviewer and Coach failures preserve existing job/question failure behavior.
- [ ] Local and Celery event payloads are schema-identical.
- [ ] No trace contains raw prompts, answers, resumes, JD text, chunk content, provider responses, secrets, DSNs, embeddings, or absolute paths.
- [ ] Stage 40 score ownership and Stage 42 evidence continuity tests remain green.
- [ ] No Redis checkpoint, WebSocket, authentication, voice, or new Agent role entered the change set.
