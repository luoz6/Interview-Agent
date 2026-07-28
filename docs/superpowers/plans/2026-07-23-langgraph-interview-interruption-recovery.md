# LangGraph Interview Interruption Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PostgreSQL-backed interviews recover from browser, network, API, worker, provider, and mid-stream failures with one LangGraph workflow authority and replayable message-level streaming.

**Architecture:** New sessions are deterministically assigned to a versioned `langgraph-v1` engine while existing sessions remain on the legacy store. A PostgreSQL checkpointer owns workflow state, a transactional command inbox plus the existing runtime outbox owns durable ingress and timers, business tables are idempotent read projections, and generation attempts/chunks are independent of SSE connections.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, LangGraph 1.2, langgraph-checkpoint-postgres 3.1, psycopg 3, existing psycopg2 repositories, PostgreSQL, Celery, pytest, Playwright, JavaScript, SSE.

---

## Execution Preconditions

- Use `F:\python3.11\python.exe` for every Python command.
- Use the approved design at `docs/superpowers/specs/2026-07-22-langgraph-interview-interruption-recovery-design.md`.
- Keep the default rollout percentage at zero until the PostgreSQL recovery gate passes.
- Do not migrate legacy sessions, replace report jobs, add WebSocket, or add dynamic Agent delegation.
- Do not expose JD, resume, evidence text, answer text, provider payloads, DSNs, leases, or checkpoint internals through runtime diagnostics.
- Use `POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/interview` for local PostgreSQL gates.
- The official PostgreSQL checkpointer uses fixed checkpoint tables. Isolate tests by globally unique thread IDs and delete test threads after each test.

## File Map

New production modules:

- `app/graphs/durable_interview_state.py`: versioned checkpoint state and plan snapshot contracts.
- `app/graphs/durable_interview_graph.py`: interrupt nodes, deterministic routing, projection nodes, generation loop, and graph builder.
- `app/services/langgraph_runtime.py`: PostgreSQL saver lifecycle and versioned compiled-graph registry.
- `app/services/interview_workflow_store.py`: command inbox, command results, projection writes, and retry event transactions.
- `app/services/interview_generation_store.py`: generation, attempt, chunk, lease, replay, and cleanup storage.
- `app/services/interview_workflow.py`: legacy/durable engine selection and application-level command facade.
- `app/services/interview_workflow_consumer.py`: command-ready and retry-due outbox consumption.
- `app/services/interview_workflow_tasks.py`: Celery entry point for durable interview events.
- `app/services/interview_event_stream.py`: command status plus replayable generation SSE iterator.
- `scripts/langgraph_recovery_acceptance.py`: PostgreSQL crash/restart acceptance runner.

New focused tests:

- `tests/test_langgraph_runtime_contract.py`
- `tests/test_durable_interview_state.py`
- `tests/test_durable_interview_graph.py`
- `tests/test_interview_workflow_store.py`
- `tests/test_interview_generation_store.py`
- `tests/test_interview_workflow_consumer.py`
- `tests/test_interview_event_stream.py`
- `tests/test_langgraph_recovery_postgres.py`
- `tests/test_langgraph_recovery_acceptance.py`
- `tests/browser/langgraph-recovery.spec.js`

Primary integration files:

- `requirements.txt`
- `.env.example`
- `app/graphs/interview_state.py`
- `app/agents/examiner.py`
- `app/services/agent_runtime.py`
- `app/services/agent_recorders.py`
- `app/services/config.py`
- `app/services/postgres_session.py`
- `app/services/postgres_runtime_control.py`
- `app/services/runtime_domain_events.py`
- `app/services/runtime_outbox_dispatcher.py`
- `app/services/runtime.py`
- `app/services/session_serialization.py`
- `app/services/runtime_events.py`
- `app/ports/runtime.py`
- `app/api/routes.py`
- `app/static/api.js`
- `app/static/interview.js`
- `tests/browser_support_app.py`
- `README.md`
- `docs/local-v1-runbook.md`
- `docs/langgraph-interview-recovery-acceptance.md`

### Task 1: Pin LangGraph and Define Rollout Configuration

**Files:**

- Create: `tests/test_langgraph_runtime_contract.py`
- Modify: `requirements.txt`
- Modify: `app/services/config.py`
- Modify: `tests/test_runtime_provider.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing dependency and configuration tests**

```python
from importlib.metadata import version

import pytest

from app.services.config import (
    get_interview_langgraph_rollout_percent,
    get_interview_langgraph_runtime_enabled,
    get_interview_langgraph_version,
)


def test_supported_langgraph_packages_are_installed():
    assert version("langgraph").startswith("1.2.")
    assert version("langgraph-checkpoint-postgres").startswith("3.1.")


def test_rollout_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", raising=False)
    monkeypatch.delenv("INTERVIEW_LANGGRAPH_RUNTIME_ENABLED", raising=False)
    monkeypatch.delenv("INTERVIEW_LANGGRAPH_VERSION", raising=False)
    assert get_interview_langgraph_rollout_percent() == 0
    assert get_interview_langgraph_runtime_enabled() is True
    assert get_interview_langgraph_version() == "langgraph-v1"


@pytest.mark.parametrize("value", ["-1", "101", "abc"])
def test_rollout_rejects_invalid_percentage(monkeypatch, value):
    monkeypatch.setenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", value)
    with pytest.raises(ValueError, match="between 0 and 100"):
        get_interview_langgraph_rollout_percent()
```

- [ ] **Step 2: Run the focused tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_langgraph_runtime_contract.py tests/test_runtime_provider.py -q
```

Expected: FAIL because the PostgreSQL checkpointer and new config accessors do not exist.

- [ ] **Step 3: Pin compatible packages**

Replace the open LangGraph requirement and add the PostgreSQL saver:

```text
langgraph>=1.2.7,<1.3
langgraph-checkpoint-postgres>=3.1.0,<3.2
psycopg[binary]>=3.2,<4
```

Retain `psycopg2-binary`; existing application repositories continue using
psycopg2 while the official checkpointer uses psycopg 3. These are separate
drivers with independent pools and lifecycle management. Never pass a
psycopg2 connection to the saver. Size PostgreSQL `max_connections`, the
existing repository pool, the saver pool, API process count, and worker
concurrency together before rollout so the second pool does not silently
exhaust the connection budget.

- [ ] **Step 4: Implement bounded rollout accessors**

```python
def get_interview_langgraph_rollout_percent() -> int:
    raw = os.getenv("INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT", "0")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT must be between 0 and 100"
        ) from exc
    if not 0 <= value <= 100:
        raise ValueError(
            "INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT must be between 0 and 100"
        )
    return value


def get_interview_langgraph_version() -> str:
    value = os.getenv(
        "INTERVIEW_LANGGRAPH_VERSION", "langgraph-v1"
    ).strip()
    if value != "langgraph-v1":
        raise ValueError("unsupported INTERVIEW_LANGGRAPH_VERSION")
    return value


def get_interview_langgraph_runtime_enabled() -> bool:
    value = os.getenv(
        "INTERVIEW_LANGGRAPH_RUNTIME_ENABLED", "true"
    ).strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(
            "INTERVIEW_LANGGRAPH_RUNTIME_ENABLED must be true or false"
        )
    return value == "true"
```

Add these safe defaults:

```dotenv
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
INTERVIEW_LANGGRAPH_RUNTIME_ENABLED=true
INTERVIEW_LANGGRAPH_VERSION=langgraph-v1
LANGGRAPH_STRICT_MSGPACK=true
```

- [ ] **Step 5: Install dependencies and verify**

```powershell
& 'F:\python3.11\python.exe' -m pip install -r requirements.txt
& 'F:\python3.11\python.exe' -m pytest tests/test_langgraph_runtime_contract.py tests/test_runtime_provider.py -q
```

Expected: PASS and installed versions remain within the pinned minor ranges.

- [ ] **Step 6: Commit**

```powershell
git add requirements.txt .env.example app/services/config.py tests/test_langgraph_runtime_contract.py tests/test_runtime_provider.py
git commit -m "build: pin durable langgraph runtime"
```

### Task 2: Add Immutable Engine and Graph Version Metadata

**Files:**

- Create: `tests/test_durable_interview_state.py`
- Modify: `app/graphs/interview_state.py`
- Modify: `app/services/session_serialization.py`
- Modify: `app/services/postgres_session.py`
- Modify: `tests/test_postgres_session_store.py`
- Modify: `tests/test_session_serialization.py`

- [ ] **Step 1: Write failing engine metadata tests**

```python
def test_legacy_session_defaults_are_explicit():
    state = build_initial_state(**make_start_kwargs())
    assert state["workflow_engine"] == "legacy"
    assert state["graph_schema_version"] is None


def test_postgres_schema_backfills_existing_sessions_as_legacy(pg_store):
    turn = pg_store.start(
        make_plan(),
        job_description="Backend role",
        resume_text="Built APIs",
        job_tags=["python"],
    )
    recovered = pg_store.get(turn.session_id)
    assert recovered["workflow_engine"] == "legacy"
    assert recovered["graph_schema_version"] is None
```

Add a deterministic assignment test:

```python
def test_engine_assignment_is_stable_for_one_session():
    values = {
        choose_workflow_engine(
            "session-fixed",
            runtime_store="postgres",
            runtime_enabled=True,
            rollout_percent=25,
        )
        for _ in range(10)
    }
    assert len(values) == 1
    assert choose_workflow_engine(
        "session-fixed",
        runtime_store="memory",
        runtime_enabled=True,
        rollout_percent=100,
    ) == "legacy"
    assert choose_workflow_engine(
        "session-fixed",
        runtime_store="postgres",
        runtime_enabled=False,
        rollout_percent=100,
    ) == "legacy"
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_interview_state.py tests/test_session_serialization.py tests/test_postgres_session_store.py -q -k "engine or graph_schema"
```

Expected: missing fields, selector, or database columns.

- [ ] **Step 3: Add state fields and deterministic selector**

```python
WorkflowEngine = Literal["legacy", "langgraph-v1"]


class InterviewState(TypedDict):
    # existing fields remain
    workflow_engine: WorkflowEngine
    graph_schema_version: str | None


def choose_workflow_engine(
    session_id: str,
    *,
    runtime_store: str,
    runtime_enabled: bool,
    rollout_percent: int,
) -> WorkflowEngine:
    if (
        runtime_store != "postgres"
        or not runtime_enabled
        or rollout_percent == 0
    ):
        return "legacy"
    bucket = int(
        hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8], 16
    ) % 100
    return "langgraph-v1" if bucket < rollout_percent else "legacy"
```

`build_initial_state` sets `legacy` and `None`. Serialization round-trips both fields.

- [ ] **Step 4: Add backward-compatible PostgreSQL columns**

```sql
ALTER TABLE {sessions}
ADD COLUMN IF NOT EXISTS workflow_engine TEXT NOT NULL DEFAULT 'legacy'
CHECK (workflow_engine IN ('legacy', 'langgraph-v1'));

ALTER TABLE {sessions}
ADD COLUMN IF NOT EXISTS graph_schema_version TEXT;

ALTER TABLE {sessions}
ADD COLUMN IF NOT EXISTS projection_sha256 TEXT;
```

Include all three columns in session SELECT, INSERT, UPDATE, `_session_row_from_db`, `session_row_from_state`, and `state_from_rows`. Existing rows remain legacy without data migration.

- [ ] **Step 5: Verify all legacy persistence tests**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_interview_state.py tests/test_session_serialization.py tests/test_postgres_session_store.py tests/test_session_service.py -q
```

Expected: PASS; all pre-existing sessions still use legacy behavior.

- [ ] **Step 6: Commit**

```powershell
git add app/graphs/interview_state.py app/services/session_serialization.py app/services/postgres_session.py tests/test_durable_interview_state.py tests/test_postgres_session_store.py tests/test_session_serialization.py
git commit -m "feat: version interview workflow engines"
```

### Task 3: Add PostgreSQL Checkpointer Lifecycle

**Files:**

- Create: `app/services/langgraph_runtime.py`
- Modify: `tests/test_langgraph_runtime_contract.py`
- Modify: `app/services/runtime.py`
- Modify: `tests/test_runtime_lifecycle.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
class FakeSaver:
    def __init__(self):
        self.setup_calls = 0
        self.deleted = []

    def setup(self):
        self.setup_calls += 1

    def delete_thread(self, thread_id):
        self.deleted.append(thread_id)


def test_checkpointer_starts_once_and_closes(monkeypatch):
    context = FakeSaverContext()
    runtime = PostgresCheckpointerRuntime(
        "postgresql://postgres:postgres@127.0.0.1:5432/interview",
        saver_factory=lambda dsn: context,
    )
    assert runtime.start() is context.saver
    assert runtime.start() is context.saver
    assert context.saver.setup_calls == 1
    runtime.shutdown()
    assert context.exits == 1


def test_strict_msgpack_is_enabled_before_saver_creation(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_STRICT_MSGPACK", raising=False)
    PostgresCheckpointerRuntime("dsn", saver_factory=FakeSaverContext).start()
    assert os.environ["LANGGRAPH_STRICT_MSGPACK"] == "true"


def test_graph_registry_never_falls_back_across_versions():
    registry = VersionedInterviewGraphRegistry()
    graph = object()
    registry.register("langgraph-v1", graph)
    assert registry.get("langgraph-v1") is graph
    with pytest.raises(ValueError, match="unsupported graph version"):
        registry.get("langgraph-v2")
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_langgraph_runtime_contract.py tests/test_runtime_lifecycle.py -q
```

Expected: missing `PostgresCheckpointerRuntime` and runtime lifecycle wiring.

- [ ] **Step 3: Implement an explicit saver lifecycle**

```python
from contextlib import AbstractContextManager
import os

from langgraph.checkpoint.postgres import PostgresSaver


class PostgresCheckpointerRuntime:
    def __init__(self, dsn: str, *, saver_factory=None) -> None:
        self.dsn = dsn
        self._factory = saver_factory or PostgresSaver.from_conn_string
        self._context: AbstractContextManager | None = None
        self._saver = None

    def start(self):
        if self._saver is not None:
            return self._saver
        os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
        self._context = self._factory(self.dsn)
        self._saver = self._context.__enter__()
        self._saver.setup()
        return self._saver

    @property
    def saver(self):
        if self._saver is None:
            raise RuntimeError("LangGraph checkpointer is not started")
        return self._saver

    def delete_thread(self, session_id: str) -> None:
        self.saver.delete_thread(session_id)

    def shutdown(self) -> None:
        context, self._context = self._context, None
        self._saver = None
        if context is not None:
            context.__exit__(None, None, None)
```

Add a `VersionedInterviewGraphRegistry` in the same module. It registers
compiled graphs by exact `graph_schema_version`, rejects duplicate
registration, and fails closed for an unknown version. Session resume always
loads the graph named on that session row; it never silently upgrades or falls
back to the current default.

- [ ] **Step 4: Cache it only for PostgreSQL mode**

Add `get_langgraph_checkpointer_runtime()` to `app/services/runtime.py`. `start_runtime()` starts it before the outbox dispatcher whenever the store is PostgreSQL and `INTERVIEW_LANGGRAPH_RUNTIME_ENABLED=true`. This is deliberately independent of the new-session rollout percentage: setting rollout to zero must not strand existing langgraph-v1 threads. `shutdown_runtime()` stops the dispatcher first, then closes the checkpointer. Memory mode never imports or starts the PostgreSQL saver.

- [ ] **Step 5: Run unit and PostgreSQL smoke tests**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_langgraph_runtime_contract.py tests/test_runtime_lifecycle.py tests/test_runtime_provider.py -q
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py -q -k schema
```

Expected: PASS and official checkpoint tables are created once.

- [ ] **Step 6: Commit**

```powershell
git add app/services/langgraph_runtime.py app/services/runtime.py tests/test_langgraph_runtime_contract.py tests/test_runtime_lifecycle.py
git commit -m "feat: manage postgres langgraph checkpoints"
```

### Task 4: Add Transactional Command Inbox and Timer Events

**Files:**

- Create: `app/services/interview_workflow_store.py`
- Create: `tests/test_interview_workflow_store.py`
- Modify: `app/services/runtime_domain_events.py`
- Modify: `app/services/postgres_runtime_control.py`
- Modify: `tests/test_postgres_runtime_control.py`

- [ ] **Step 1: Write failing model and PostgreSQL tests**

```python
def test_command_event_contains_no_answer():
    event = InterviewCommandReadyEvent(
        event_id="command-event-1",
        session_id="s1",
        command_id="cmd-1",
    )
    payload = event.model_dump(mode="json")
    assert payload["event_type"] == "interview_command_ready"
    assert payload["command_id"] == "cmd-1"
    assert "answer" not in str(payload).lower()


def test_enqueue_command_commits_inbox_and_outbox_atomically(workflow_store):
    command = workflow_store.enqueue_command(
        session_id=workflow_store.session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="I used cache-aside.",
    )
    assert command.status == "pending"
    assert workflow_store.get_command(
        workflow_store.session_id, "cmd-1"
    ).answer_text == "I used cache-aside."
    event = workflow_store.control.list_outbox(
        session_id=workflow_store.session_id
    )[0]
    assert event["event_type"] == "interview_command_ready"
    assert "cache-aside" not in str(event["payload"])


def test_duplicate_command_with_changed_payload_is_rejected(workflow_store):
    workflow_store.enqueue_command(
        session_id=workflow_store.session_id,
        command_id="cmd-1",
        command_type="answer",
        expected_version=1,
        answer_text="first",
    )
    with pytest.raises(CommandPayloadConflict):
        workflow_store.enqueue_command(
            session_id=workflow_store.session_id,
            command_id="cmd-1",
            command_type="answer",
            expected_version=1,
            answer_text="changed",
        )
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_workflow_store.py tests/test_postgres_runtime_control.py -q
```

Expected: missing events, command models, tables, and store.

- [ ] **Step 3: Define safe scheduling envelopes**

```python
class InterviewCommandReadyEvent(RuntimeEventEnvelope):
    event_type: Literal["interview_command_ready"] = (
        "interview_command_ready"
    )
    command_id: str


class InterviewRetryDueEvent(RuntimeEventEnvelope):
    event_type: Literal["interview_retry_due"] = "interview_retry_due"
    generation_id: str
    next_attempt_number: int = Field(ge=2, le=3)
```

Change `PostgresRuntimeControlStore.enqueue_event` to accept `RuntimeEventEnvelope`, while preserving existing `RoundClosedEvent` behavior.

- [ ] **Step 4: Create exact command inbox schema**

```sql
CREATE TABLE IF NOT EXISTS {commands} (
    session_id TEXT NOT NULL
        REFERENCES {sessions}(session_id) ON DELETE CASCADE,
    command_id TEXT NOT NULL,
    command_type TEXT NOT NULL
        CHECK (command_type IN ('answer', 'skip', 'finish')),
    expected_version INTEGER NOT NULL CHECK (expected_version >= 1),
    answer_text TEXT,
    payload_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'applied', 'conflict', 'failed')),
    result_state_version INTEGER,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, command_id),
    CHECK (
        (
            command_type = 'answer'
            AND (answer_text IS NOT NULL OR status = 'applied')
        )
        OR (command_type <> 'answer' AND answer_text IS NULL)
    )
)
```

`PostgresInterviewWorkflowStore` provides four concrete command operations:
`enqueue_command` returns the inserted or matching existing
`InterviewCommandRecord`; `get_command` loads by the composite key;
`mark_command_applied` stores `result_state_version`, clears error state, and
sets completed timestamps; `mark_command_conflict` stores
`state_version_conflict` without changing the session projection. Every status
update checks the expected previous status so stale workers cannot overwrite a
terminal command.

- [ ] **Step 5: Make inbox and outbox one transaction**

```python
with self.connection() as connection:
    with connection.cursor() as cursor:
        inserted = self._insert_command(cursor, command)
        if inserted:
            self.control.enqueue_event(
                cursor,
                InterviewCommandReadyEvent(
                    event_id=f"interview-command-{session_id}-{command_id}",
                    session_id=session_id,
                    causation_id=command_id,
                    command_id=command_id,
                ),
            )
        else:
            existing = self._get_command(cursor, session_id, command_id)
            if existing.payload_sha256 != command.payload_sha256:
                raise CommandPayloadConflict(command_id)
            return existing
```

Hash the normalized tuple `(command_type, expected_version, answer_text or "")`. Do not copy answer text into outbox JSON. A forced outbox insert failure must roll back the inbox insert.

- [ ] **Step 6: Verify and commit**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_workflow_store.py tests/test_postgres_runtime_control.py tests/test_runtime_outbox_dispatcher.py -q
git add app/services/interview_workflow_store.py app/services/runtime_domain_events.py app/services/postgres_runtime_control.py tests/test_interview_workflow_store.py tests/test_postgres_runtime_control.py
git commit -m "feat: persist durable interview commands"
```

### Task 5: Build the Versioned Interrupt Graph Skeleton

**Files:**

- Create: `app/graphs/durable_interview_state.py`
- Create: `app/graphs/durable_interview_graph.py`
- Create: `tests/test_durable_interview_graph.py`
- Modify: `app/services/prep.py`

- [ ] **Step 1: Write failing state and interrupt tests**

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


def test_graph_initializes_then_waits_for_answer():
    graph, config = make_graph(InMemorySaver())
    result = graph.invoke(make_initial_input(), config=config)
    assert result["interview_status"] == "active"
    snapshot = graph.get_state(config)
    assert snapshot.next == ("wait_for_answer",)
    assert snapshot.tasks[0].interrupts


def test_answer_resume_stores_only_command_identity():
    graph, config, deps = make_graph(InMemorySaver())
    deps.workflow_store.seed_command("cmd-1", status="applied")
    graph.invoke(make_initial_input(), config=config)
    graph.invoke(
        Command(
            resume={"kind": "answer_command", "command_id": "cmd-1"}
        ),
        config=config,
    )
    assert deps.workflow_store.loaded_commands == [("s1", "cmd-1")]
    assert graph.get_state(config).next == ("wait_for_answer",)


def test_state_has_no_pending_action_or_raw_source_documents():
    state = make_durable_initial_state(make_plan())
    assert "pending_action" not in state
    serialized = json.dumps(state, ensure_ascii=False)
    assert "job_description" not in serialized
    assert "resume_text" not in serialized
    assert "knowledge_evidence" not in serialized
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_interview_state.py tests/test_durable_interview_graph.py -q
```

Expected: missing durable state and graph builder.

- [ ] **Step 3: Define the self-contained bounded state**

```python
class DurableQuestionSnapshot(BaseModel):
    id: str
    kind: str
    prompt: str
    focus: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_sha256: dict[str, str] = Field(default_factory=dict)


class DurablePlanSnapshot(BaseModel):
    title: str
    corpus_manifest_sha256: str | None = None
    questions: list[DurableQuestionSnapshot]

    @classmethod
    def from_plan(cls, plan: InterviewPlan) -> "DurablePlanSnapshot":
        context = plan.prep_context
        references = (
            {
                reference.evidence_id: reference.content_sha256
                for reference in context.evidence_refs
            }
            if context is not None
            else {}
        )
        evidence_ids_by_question = (
            {
                hint.question_id: list(hint.evidence_ids)
                for hint in context.question_hints
            }
            if context is not None
            else {}
        )
        manifest_sha256 = (
            context.binding_snapshot.corpus_manifest_sha256
            if context is not None and context.binding_snapshot is not None
            else None
        )
        questions = []
        for question in plan.questions:
            evidence_ids = evidence_ids_by_question.get(question.id, [])
            questions.append(
                DurableQuestionSnapshot(
                    id=question.id,
                    kind=question.kind,
                    prompt=question.prompt,
                    focus=question.focus,
                    evidence_ids=evidence_ids,
                    evidence_sha256={
                        evidence_id: references[evidence_id]
                        for evidence_id in evidence_ids
                        if evidence_id in references
                    },
                )
            )
        return cls(
            title=plan.title,
            corpus_manifest_sha256=manifest_sha256,
            questions=questions,
        )


class DurableInterviewState(TypedDict):
    session_id: str
    workflow_engine: Literal["langgraph-v1"]
    graph_schema_version: Literal["langgraph-v1"]
    plan_snapshot: dict
    current_index: int
    messages: list[InterviewMessage]
    skipped_question_ids: list[str]
    interview_status: Literal["active", "finished"]
    state_version: int
    last_command_id: str | None
    active_command_id: str | None
    generation_id: str | None
    generation_attempt: int
    expected_retry_attempt: int | None
    retry_resume_attempt: int | None
    retry_validation: Literal["accepted", "stale"] | None
    next_retry_at: str | None
    last_error_code: str | None
    command_type: Literal["answer", "skip", "finish"] | None
    command_outcome: Literal[
        "accepted", "duplicate", "conflict", "completed"
    ] | None
    generation_outcome: Literal[
        "completed", "retryable", "terminal"
    ] | None
    generated_text: str | None
```

`DurablePlanSnapshot.from_plan` intentionally reads only questions,
`question_hints[].evidence_ids`, `evidence_refs[].content_sha256`, and the
binding snapshot's corpus manifest hash. A referenced ID with no matching hash
is preserved so the runtime resolver can degrade with
`invalid_evidence_reference` instead of silently changing the binding. The
conversion excludes JD, resume, role-profile resume signals, topic summaries,
candidate summaries, and evidence content. Add serialization tests that assert
those excluded strings do not occur in the checkpoint payload.

- [ ] **Step 4: Implement pure wait and command validation nodes**

```python
def wait_for_answer(state: DurableInterviewState) -> dict:
    payload = interrupt(
        {
            "kind": "answer_command",
            "session_id": state["session_id"],
            "state_version": state["state_version"],
        }
    )
    return {"active_command_id": payload["command_id"]}


def validate_command(state, deps) -> dict:
    command = deps.workflow_store.get_command(
        state["session_id"], state["active_command_id"]
    )
    if command.status == "applied":
        return {"active_command_id": None, "command_outcome": "duplicate"}
    if command.expected_version != state["state_version"]:
        deps.workflow_store.mark_command_conflict(
            state["session_id"],
            command.command_id,
            state["state_version"],
        )
        return {"active_command_id": None, "command_outcome": "conflict"}
    return {
        "command_type": command.command_type,
        "command_outcome": "accepted",
    }
```

The validation route sends duplicate and conflict outcomes directly back to `wait_for_answer`. Accepted answer, skip, and finish commands route to their deterministic transition nodes.

- [ ] **Step 5: Build and compile the v1 graph**

```python
def build_durable_interview_graph(
    deps: DurableInterviewGraphDependencies,
    *,
    checkpointer,
):
    builder = StateGraph(DurableInterviewState)
    builder.add_node("initialize_session", initialize_session)
    builder.add_node("project_state", deps.project_state)
    builder.add_node("wait_for_answer", wait_for_answer)
    builder.add_node("validate_command", partial(validate_command, deps=deps))
    builder.add_node("append_candidate_answer", append_candidate_answer)
    builder.add_node("apply_skip", apply_skip)
    builder.add_node("apply_finish", apply_finish)
    builder.add_node("decide_next_action", decide_next_action)
    # Generation nodes and their routes are added in Task 9.
    builder.add_edge(START, "initialize_session")
    builder.add_edge("initialize_session", "project_state")
    builder.add_conditional_edges("project_state", route_after_projection)
    builder.add_edge("wait_for_answer", "validate_command")
    builder.add_conditional_edges("validate_command", route_validated_command)
    return builder.compile(checkpointer=checkpointer)
```

- [ ] **Step 6: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_interview_state.py tests/test_durable_interview_graph.py tests/test_interview_graph.py -q
git add app/graphs/durable_interview_state.py app/graphs/durable_interview_graph.py app/services/prep.py tests/test_durable_interview_state.py tests/test_durable_interview_graph.py
git commit -m "feat: add durable interview graph skeleton"
```

### Task 6: Add Idempotent Projection and Public Version Ownership

**Files:**

- Modify: `app/services/interview_workflow_store.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/services/session_serialization.py`
- Modify: `app/graphs/durable_interview_graph.py`
- Modify: `tests/test_interview_workflow_store.py`
- Modify: `tests/test_postgres_session_store.py`
- Modify: `tests/test_durable_interview_graph.py`

- [ ] **Step 1: Write failing projection replay tests**

```python
def test_projection_advances_one_public_version(workflow_store):
    state = make_state(state_version=1)
    result = workflow_store.project_state(state)
    assert result.state_version == 2
    assert workflow_store.session_snapshot()["state_version"] == 2


def test_projection_replay_reuses_same_version(workflow_store):
    state = make_state(state_version=1)
    first = workflow_store.project_state(state)
    second = workflow_store.project_state(state)
    assert first == second
    assert second.state_version == 2
    assert workflow_store.count_messages() == len(state["messages"])


def test_projection_rejects_same_version_with_changed_payload(workflow_store):
    state = make_state(state_version=1)
    workflow_store.project_state(state)
    changed = deepcopy(state)
    changed["messages"][-1]["content"] = "changed"
    with pytest.raises(ProjectionConflict):
        workflow_store.project_state(changed)
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_workflow_store.py tests/test_durable_interview_graph.py -q -k projection
```

Expected: missing projection API and version behavior.

- [ ] **Step 3: Insert a durable session shell at version zero**

Add `PostgresInterviewSessionStore.insert_durable_session_shell`:

```python
def insert_durable_session_shell(
    self,
    *,
    session_id: str,
    plan: InterviewPlan,
    job_description: str,
    resume_text: str,
    job_tags: list[str],
) -> None:
    state = build_initial_state(
        session_id=session_id,
        plan=plan,
        job_description=job_description,
        resume_text=resume_text,
        job_tags=job_tags,
    )
    state["workflow_engine"] = "langgraph-v1"
    state["graph_schema_version"] = "langgraph-v1"
    state["messages"] = []
    state["state_version"] = 0
    self._insert_state(state)
```

The graph's first `project_state` creates public version 1 and inserts the first interviewer message. Legacy `start` remains unchanged.

- [ ] **Step 4: Implement deterministic conditional projection**

```python
def project_state(self, state: DurableInterviewState) -> ProjectionResult:
    next_version = state["state_version"] + 1
    payload = projection_payload(state, state_version=next_version)
    digest = sha256_json(payload)
    with self.connection() as connection:
        with connection.cursor() as cursor:
            current = self._lock_session(cursor, state["session_id"])
            if current.state_version > next_version:
                return ProjectionResult(current.state_version, current.digest)
            if current.state_version == next_version:
                if current.projection_sha256 != digest:
                    raise ProjectionConflict(state["session_id"])
                self._verify_messages(cursor, state["messages"])
                return ProjectionResult(next_version, digest)
            if current.state_version != state["state_version"]:
                raise ProjectionConflict(state["session_id"])
            self._append_messages_idempotently(cursor, state["messages"])
            self._update_projection(
                cursor, payload, projection_sha256=digest
            )
            self._enqueue_round_event_if_closed(cursor, state, next_version)
    return ProjectionResult(next_version, digest)
```

Message rows use `(session_id, sequence_no)`; an existing row must match role, content, and question ID. Do not delete a divergent suffix for langgraph-v1 sessions.

- [ ] **Step 5: Make the graph node return the projected version**

```python
def project_state_node(state, deps) -> dict:
    projection = deps.workflow_store.project_state(state)
    updates = {
        "state_version": projection.state_version,
        "command_outcome": None,
        "generation_outcome": None,
        "generated_text": None,
        "retry_resume_attempt": None,
        "retry_validation": None,
    }
    if state["command_outcome"] == "completed":
        updates["active_command_id"] = None
        updates["command_type"] = None
    return updates
```

`project_state_node` clears every field used only as an adjacent-node routing
signal after the projector has consumed it. It preserves durable business facts
such as `last_error_code`, `next_retry_at`, and the expected retry fence
until their own transition clears them. At this task boundary,
`route_after_projection` handles finished to END and active to
`wait_for_answer`. Task 9 extends it with the uncommitted-generation route
after all generation nodes exist.

- [ ] **Step 6: Simulate checkpoint failure after successful projection**

Use a checkpointer test double that raises once from `put` after the projection node. Rebuild the graph with the same state and assert the second projection returns the same version, message count, and deterministic round event ID.

- [ ] **Step 7: Verify and commit**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_workflow_store.py tests/test_postgres_session_store.py tests/test_durable_interview_graph.py -q
git add app/services/interview_workflow_store.py app/services/postgres_session.py app/services/session_serialization.py app/graphs/durable_interview_graph.py tests/test_interview_workflow_store.py tests/test_postgres_session_store.py tests/test_durable_interview_graph.py
git commit -m "feat: project durable interview state"
```

### Task 7: Add Raising Examiner Attempts and Parent Run Links

**Files:**

- Modify: `app/services/agent_runtime.py`
- Modify: `app/agents/examiner.py`
- Modify: `app/services/postgres_runtime_control.py`
- Modify: `app/services/agent_recorders.py`
- Modify: `tests/test_agent_runtime.py`
- Modify: `tests/test_agents.py`
- Modify: `tests/test_agent_recorders.py`

- [ ] **Step 1: Write failing attempt-boundary tests**

```python
def test_stream_followup_attempt_propagates_provider_failure():
    agent = ExaminerAgent(llm=FailingLLM())
    with pytest.raises(RuntimeError, match="provider failed"):
        list(
            agent.stream_followup_attempt(
                context=[{"role": "candidate", "content": "answer"}],
                execution_context=make_context(),
            )
        )


def test_legacy_stream_still_falls_back():
    agent = ExaminerAgent(llm=FailingLLM())
    assert list(
        agent.stream_followup(
            context=[],
            focus="Redis",
            execution_context=make_context(),
        )
    ) == [fallback_followup("Redis")]


def test_child_run_persists_parent_run_id(pg_control):
    record = make_record(parent_run_id="agent-parent")
    pg_control.record_agent_run(record)
    assert pg_control.list_agent_runs(session_id="s1")[0][
        "parent_run_id"
    ] == "agent-parent"
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime.py tests/test_agents.py tests/test_agent_recorders.py -q
```

Expected: missing method, field, and database column.

- [ ] **Step 3: Extend execution context and ledger**

```python
class AgentExecutionContext(BaseModel):
    # existing fields remain
    parent_run_id: str | None = None
```

Add `parent_run_id TEXT` through `ALTER TABLE IF NOT EXISTS`, INSERT, SELECT, and public safe run projections. Keep `causation_id=command_id`; `parent_run_id` represents the actual nested invocation.

- [ ] **Step 4: Add a no-fallback streaming method**

```python
def stream_followup_attempt(
    self,
    *,
    context: list[dict[str, str]],
    execution_context: AgentExecutionContext,
) -> Iterator[str]:
    def provider_stream():
        emitted = False
        for chunk in (self.llm or self._default_llm()).stream_followup(
            context
        ):
            if chunk:
                emitted = True
                yield chunk
        if not emitted:
            raise _EmptyFollowupStream()

    yield from self._execution_runner.stream(
        execution_context,
        provider_stream,
        fallback=None,
    )
```

Do not change legacy `generate_followup` or `stream_followup`. The durable graph calls only `stream_followup_attempt`; its fallback node calls the shared deterministic `fallback_followup` function directly.
No synchronous `generate_followup_attempt` is added: the durable graph owns
chunk accumulation and final-text assembly, so a second raising API would be
unused and would create a second failure boundary to maintain.

- [ ] **Step 5: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime.py tests/test_agents.py tests/test_agent_recorders.py tests/test_interview_graph.py -q
git add app/services/agent_runtime.py app/agents/examiner.py app/services/postgres_runtime_control.py app/services/agent_recorders.py tests/test_agent_runtime.py tests/test_agents.py tests/test_agent_recorders.py
git commit -m "feat: expose raising examiner attempts"
```

### Task 8: Persist Generation Attempts and Replayable Chunks

**Files:**

- Create: `app/services/interview_generation_store.py`
- Create: `tests/test_interview_generation_store.py`
- Modify: `app/services/interview_workflow_store.py`

- [ ] **Step 1: Write failing generation contract tests**

```python
def test_generation_is_idempotent_per_source_command(store):
    first = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="cmd-1",
        question_id="q1",
    )
    second = store.prepare_generation(
        session_id=store.session_id,
        source_command_id="cmd-1",
        question_id="q1",
    )
    assert first.generation_id == second.generation_id
    assert first.active_attempt == 1


def test_chunks_are_ordered_and_attempt_scoped(store):
    generation = seed_generation(store)
    store.append_chunk(generation.generation_id, 1, 1, "first ")
    store.append_chunk(generation.generation_id, 1, 2, "attempt")
    store.abandon_attempt(generation.generation_id, 1, "worker_lost")
    store.start_attempt(generation.generation_id, 2)
    store.append_chunk(generation.generation_id, 2, 1, "replacement")
    replay = store.list_events(generation.generation_id)
    assert [(item.attempt_number, item.sequence) for item in replay] == [
        (1, 1), (1, 2), (2, 0), (2, 1)
    ]
    assert replay[2].event_type == "generation_reset"


def test_completed_attempt_is_not_replaced(store):
    generation = seed_generation(store)
    store.complete_attempt(generation.generation_id, 1, "complete")
    with pytest.raises(GenerationAlreadyCompleted):
        store.start_attempt(generation.generation_id, 2)
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_generation_store.py -q
```

Expected: missing generation store and tables.

- [ ] **Step 3: Create exact generation tables**

```sql
CREATE TABLE IF NOT EXISTS {generations} (
    generation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL
        REFERENCES {sessions}(session_id) ON DELETE CASCADE,
    source_command_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('pending','running','completed','failed')),
    active_attempt INTEGER NOT NULL DEFAULT 1,
    final_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, source_command_id)
);

CREATE TABLE IF NOT EXISTS {generation_attempts} (
    generation_id TEXT NOT NULL
        REFERENCES {generations}(generation_id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 1 AND 3),
    status TEXT NOT NULL
        CHECK (status IN ('pending','running','completed','failed','abandoned')),
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (generation_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS {generation_chunks} (
    generation_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    event_type TEXT NOT NULL
        CHECK (event_type IN ('chunk','generation_reset')),
    delta TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (generation_id, attempt_number, sequence),
    FOREIGN KEY (generation_id, attempt_number)
        REFERENCES {generation_attempts}(generation_id, attempt_number)
        ON DELETE CASCADE
)
```

- [ ] **Step 4: Implement guarded leases and completion**

`PostgresInterviewGenerationStore.start_attempt` inserts or reclaims exactly
one `(generation_id, attempt_number)` row, sets owner and expiry, and returns a
`GenerationAttempt`. `heartbeat_attempt` extends only the matching running
owner. `append_chunk` uses `ON CONFLICT DO NOTHING` and verifies that an
existing sequence has the same event type and delta. `complete_attempt`
atomically marks the attempt and parent generation completed with the full
text. `start_or_reclaim_attempt` returns an existing running lease owned by the
same worker, reclaims an expired lease into the next bounded attempt, and
rejects active leases owned by another worker.

All updates require the expected active attempt and status. Reclaim marks the
old attempt abandoned, creates the replacement attempt, inserts
`generation_reset` at sequence zero, and never mutates completed output.

- [ ] **Step 5: Coalesce provider chunks before persistence**

```python
class ChunkCoalescer:
    def __init__(self, *, max_interval_seconds: float = 0.2) -> None:
        self.max_interval_seconds = max_interval_seconds
        self._parts: list[str] = []
        self._started = monotonic()

    def add(self, value: str) -> str | None:
        self._parts.append(value)
        if monotonic() - self._started < self.max_interval_seconds:
            return None
        return self.flush()

    def flush(self) -> str | None:
        if not self._parts:
            return None
        value = "".join(self._parts)
        self._parts.clear()
        self._started = monotonic()
        return value
```

Unit-test deterministic flushing by injecting a clock; do not use real sleeps.

- [ ] **Step 6: Verify and commit**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_generation_store.py tests/test_interview_workflow_store.py -q
git add app/services/interview_generation_store.py app/services/interview_workflow_store.py tests/test_interview_generation_store.py
git commit -m "feat: persist replayable interview generations"
```

### Task 9: Complete the Generation, Retry, and Fallback Graph Loop

**Files:**

- Modify: `app/graphs/durable_interview_state.py`
- Modify: `app/graphs/durable_interview_graph.py`
- Modify: `app/services/interview_workflow_store.py`
- Modify: `app/services/knowledge_binding.py`
- Modify: `tests/test_durable_interview_graph.py`
- Modify: `tests/test_interview_workflow_store.py`
- Modify: `tests/test_knowledge_binding_resolver.py`

- [ ] **Step 1: Write failing successful, retry, and fallback tests**

```python
def test_successful_generation_commits_one_complete_message(graph_fixture):
    graph_fixture.resume_answer("cmd-1")
    state = graph_fixture.state()
    assert state["messages"][-1]["role"] == "interviewer"
    assert state["messages"][-1]["content"] == "Generated follow-up."
    assert state["generation_id"] is None
    assert state["state_version"] == 3


def test_retry_interrupt_waits_until_due_event(graph_fixture):
    graph_fixture.examiner.fail_with(ProviderUnavailable())
    graph_fixture.resume_answer("cmd-1")
    snapshot = graph_fixture.snapshot()
    assert snapshot.next == ("wait_for_retry",)
    event = graph_fixture.workflow_store.retry_events[0]
    assert event.next_attempt_number == 2
    assert event.available_at > event.created_at


def test_duplicate_due_retry_cannot_restart_completed_generation(graph_fixture):
    graph_fixture.examiner.fail_once(ProviderUnavailable())
    graph_fixture.resume_answer("cmd-1")
    graph_fixture.resume_retry(attempt=2)
    before = graph_fixture.state()
    graph_fixture.resume_retry(attempt=2)
    assert graph_fixture.state()["messages"] == before["messages"]
    assert graph_fixture.examiner.attempt_count == 2


def test_mismatched_due_retry_returns_to_wait(graph_fixture):
    graph_fixture.examiner.fail_with(ProviderUnavailable())
    graph_fixture.resume_answer("cmd-1")
    graph_fixture.resume_retry(attempt=3)
    assert graph_fixture.snapshot().next == ("wait_for_retry",)
    assert graph_fixture.state()["generation_attempt"] == 1


def test_third_failure_commits_template_fallback(graph_fixture):
    graph_fixture.examiner.always_fail(ProviderUnavailable())
    graph_fixture.resume_answer("cmd-1")
    graph_fixture.resume_retry(attempt=2)
    graph_fixture.resume_retry(attempt=3)
    state = graph_fixture.state()
    assert state["messages"][-1]["content"] == fallback_followup("Project")
    assert state["last_error_code"] == "provider_unavailable"


def test_finish_enqueues_one_report_job_on_replay(graph_fixture):
    graph_fixture.resume_finish("cmd-finish")
    graph_fixture.replay_last_node()
    assert graph_fixture.report_jobs.count("s1") == 1


def test_finish_projects_before_report_job_enqueue(graph_fixture):
    graph_fixture.resume_finish("cmd-finish")
    assert graph_fixture.report_jobs.observed_state_version("s1") == 2
    assert graph_fixture.workflow_store.session_snapshot()["status"] == "finished"


def test_project_state_clears_transient_routes(graph_fixture):
    graph_fixture.resume_answer("cmd-1")
    state = graph_fixture.state()
    assert state["command_outcome"] is None
    assert state["generation_outcome"] is None
    assert state["retry_resume_attempt"] is None


def test_retry_history_records_nodes_not_public_versions(graph_fixture):
    graph_fixture.examiner.fail_once(ProviderUnavailable())
    graph_fixture.resume_answer("cmd-1")
    graph_fixture.resume_retry(attempt=2)
    history = list(graph_fixture.graph.get_state_history(graph_fixture.config))
    assert any(snapshot.next == ("wait_for_retry",) for snapshot in history)
    assert graph_fixture.state()["state_version"] == 3
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_interview_graph.py tests/test_interview_workflow_store.py -q -k "generation or retry or fallback"
```

Expected: missing generation nodes and retry interrupt.

- [ ] **Step 3: Implement one observable provider attempt**

Before calling Examiner, reuse the evidence-verification path already owned by
`KnowledgeBindingResolver`; do not add another direct `get_by_ids` call in
the graph. Extract the common evidence-only operation from the resolver so
legacy plans retain their existing guidance behavior while durable snapshots
get the same hash, manifest, missing-reference, and unavailable-repository
handling:

```python
def resolve_evidence_by_ids(
    repository: KnowledgeRepository,
    *,
    evidence_ids: list[str],
    expected_hashes: dict[str, str],
    expected_manifest_sha256: str | None,
) -> KnowledgeBindingResolution:
    return KnowledgeBindingResolver(repository).resolve_bound_evidence(
        evidence_ids=evidence_ids,
        expected_hashes=expected_hashes,
        expected_manifest_sha256=expected_manifest_sha256,
    )


def build_examiner_context(state, repository) -> list[dict[str, str]]:
    question = current_question_snapshot(state)
    resolution = resolve_evidence_by_ids(
        repository,
        evidence_ids=question.evidence_ids,
        expected_hashes=question.evidence_sha256,
        expected_manifest_sha256=state["plan_snapshot"].get(
            "corpus_manifest_sha256"
        ),
    )
    if resolution.retrieval_path != "bound_evidence_ids":
        return recent_conversation_messages(state)
    return [*recent_conversation_messages(state), *resolution.messages]
```

Move the existing validation currently inside `KnowledgeBindingResolver.resolve`
into `resolve_bound_evidence`, then have `resolve` call that helper after it
has derived the legacy plan's evidence IDs and hashes. The helper returns
`bound_evidence_ids` only when every ID, content hash, and manifest hash
matches. It returns a degraded path and stable reason otherwise. This preserves
evidence validation without checkpointing evidence text. Repository failure
degrades to conversation-only context and records that path; it does not fail
answer recovery.

```python
def generate_followup(state, deps) -> dict:
    coalescer = deps.coalescer_factory()
    attempt = deps.generation_store.start_or_reclaim_attempt(
        state["generation_id"],
        state["generation_attempt"],
        worker_id=deps.worker_id,
        lease_seconds=deps.generation_lease_seconds,
    )
    chunks: list[str] = []
    sequence = 0
    try:
        for chunk in deps.examiner.stream_followup_attempt(
            context=deps.context_builder(state),
            execution_context=deps.execution_context(state, attempt),
        ):
            chunks.append(chunk)
            persisted = coalescer.add(chunk)
            if persisted:
                sequence += 1
                deps.generation_store.append_chunk(
                    attempt.generation_id,
                    attempt.attempt_number,
                    sequence,
                    persisted,
                )
                deps.generation_store.heartbeat_attempt(
                    attempt.generation_id,
                    attempt.attempt_number,
                    deps.worker_id,
                )
        final_chunk = coalescer.flush()
        if final_chunk:
            sequence += 1
            deps.generation_store.append_chunk(
                attempt.generation_id,
                attempt.attempt_number,
                sequence,
                final_chunk,
            )
        final_text = "".join(chunks).strip()
        deps.generation_store.complete_attempt(
            attempt.generation_id, attempt.attempt_number, final_text
        )
        return {"generation_outcome": "completed", "generated_text": final_text}
    except deps.retryable_provider_errors as exc:
        code = classify_runtime_failure(exc).code
        deps.generation_store.fail_attempt(
            attempt.generation_id, attempt.attempt_number, code
        )
        return {"generation_outcome": "retryable", "last_error_code": code}
    except deps.terminal_provider_errors as exc:
        code = classify_runtime_failure(exc).code
        deps.generation_store.fail_attempt(
            attempt.generation_id, attempt.attempt_number, code
        )
        return {"generation_outcome": "terminal", "last_error_code": code}
```

Invariant failures and unknown state errors propagate; only classified provider failures become graph outcomes.

- [ ] **Step 4: Add explicit timer scheduling and interrupt**

```python
def enqueue_retry(state, deps) -> dict:
    next_attempt = state["generation_attempt"] + 1
    scheduled = deps.workflow_store.enqueue_retry(
        session_id=state["session_id"],
        generation_id=state["generation_id"],
        next_attempt_number=next_attempt,
        delay_seconds=retry_delay_seconds(state["generation_attempt"]),
    )
    return {
        "expected_retry_attempt": next_attempt,
        "next_retry_at": scheduled.available_at.isoformat(),
    }


def wait_for_retry(state) -> dict:
    payload = interrupt(
        {
            "kind": "retry_timer",
            "generation_id": state["generation_id"],
            "next_attempt_number": state["generation_attempt"] + 1,
        }
    )
    return {"retry_resume_attempt": payload["next_attempt_number"]}


def validate_retry(state) -> dict:
    if state["retry_resume_attempt"] != state["expected_retry_attempt"]:
        return {
            "retry_resume_attempt": None,
            "retry_validation": "stale",
        }
    return {
        "generation_attempt": state["expected_retry_attempt"],
        "expected_retry_attempt": None,
        "retry_resume_attempt": None,
        "retry_validation": "accepted",
        "next_retry_at": None,
    }
```

The outbox event ID is deterministic from `generation_id` and next attempt.
`enqueue_retry` computes `available_at` inside PostgreSQL with
`NOW() + delay_seconds * INTERVAL '1 second'` and returns that server timestamp,
so the scheduler does not depend on application-server clock skew. No graph
node sleeps.

Add `InterviewWorkflowStore.enqueue_retry(session_id, generation_id,
next_attempt_number, delay_seconds) -> RetrySchedule`. Its one transaction
inserts the deterministic runtime-outbox event with `ON CONFLICT DO NOTHING`,
then reads and returns the canonical event's `available_at`. The event payload
contains only session ID, generation ID, and attempt number; it never contains
candidate text or model output.

Wire `wait_for_retry -> validate_retry` and route
`retry_validation == "accepted"` to `prepare_retry`; route
`retry_validation == "stale"` back to `wait_for_retry`. `prepare_retry`
clears `retry_validation` before it starts the next attempt. The graph remains
authoritative even when a dispatcher precheck is bypassed.

```python
def route_validated_retry(state) -> str:
    if state["retry_validation"] == "accepted":
        return "prepare_retry"
    return "wait_for_retry"


builder.add_node("enqueue_retry", partial(enqueue_retry, deps=deps))
builder.add_node("wait_for_retry", wait_for_retry)
builder.add_node("validate_retry", validate_retry)
builder.add_node("prepare_retry", prepare_retry)
builder.add_edge("enqueue_retry", "wait_for_retry")
builder.add_edge("wait_for_retry", "validate_retry")
builder.add_conditional_edges("validate_retry", route_validated_retry)
builder.add_edge("prepare_retry", "generate_followup")
```

- [ ] **Step 5: Add completion and fallback routes**

```python
def route_generation(state) -> str:
    if state["generation_outcome"] == "completed":
        return "commit_interviewer_message"
    if (
        state["generation_outcome"] == "retryable"
        and state["generation_attempt"] < 3
    ):
        return "enqueue_retry"
    return "fallback_followup"
```

Both commit nodes append exactly one interviewer message, clear `generation_id`,
clear transient generation fields, set `command_outcome="completed"`, and route
to `project_state`. Extend the projector transaction so a completed command is
marked applied with that same `next_state_version`; only after the transaction
commits does the graph node clear `active_command_id`. This prevents a command
from reporting applied before its final projection exists.

Task 9 also extends `route_after_projection` to route in this exact order:
finished to `emit_report_event`, uncommitted generation ID to
`generate_followup`, otherwise active to `wait_for_answer`. Retry
checkpoints do not call `project_state` and do not advance public
`state_version`.

Add `emit_report_event` after the final finished projection:

```python
def emit_report_event(state, deps) -> dict:
    deps.report_job_queue.enqueue_report_request(state["session_id"])
    return {"interview_status": "finished"}
```

Register `emit_report_event` as a graph node and add its only outgoing edge to
`END`. The `apply_finish` edge remains `apply_finish -> project_state`;
`route_after_projection` supplies the conditional
`finished -> emit_report_event` edge.

The report enqueue and final projection are intentionally separate idempotent
node transactions, not a distributed transaction. `apply_finish` sets the
finished state, `project_state` makes that state visible, then
`emit_report_event` enqueues the report request. If enqueue fails after the
projection succeeds, node replay retries the same session-unique job; if a
checkpoint fails after enqueue, the duplicate enqueue is harmless. Report
evaluation and report status transitions remain outside LangGraph.

- [ ] **Step 6: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_interview_graph.py tests/test_interview_workflow_store.py tests/test_agents.py -q
git add app/graphs/durable_interview_state.py app/graphs/durable_interview_graph.py app/services/interview_workflow_store.py tests/test_durable_interview_graph.py tests/test_interview_workflow_store.py
git commit -m "feat: recover durable examiner generation"
```

### Task 10: Route Outbox Work into Versioned Graph Threads

**Files:**

- Create: `app/services/interview_workflow_consumer.py`
- Create: `app/services/interview_workflow_tasks.py`
- Create: `tests/test_interview_workflow_consumer.py`
- Modify: `app/services/runtime_outbox_dispatcher.py`
- Modify: `app/services/postgres_runtime_control.py`
- Modify: `app/services/runtime.py`
- Modify: `app/services/celery_app.py`
- Modify: `tests/test_runtime_outbox_dispatcher.py`
- Modify: `tests/test_runtime_lifecycle.py`

- [ ] **Step 1: Write failing consumer and lease tests**

```python
def test_command_event_resumes_answer_interrupt(consumer):
    outcome = consumer.consume(
        InterviewCommandReadyEvent(
            session_id="s1", command_id="cmd-1"
        ).model_dump()
    )
    assert outcome.status == "completed"
    assert consumer.graph.invocations == [
        Command(resume={"kind": "answer_command", "command_id": "cmd-1"})
    ]


def test_retry_event_resumes_timer_interrupt(consumer):
    outcome = consumer.consume(
        InterviewRetryDueEvent(
            session_id="s1",
            generation_id="gen-1",
            next_attempt_number=2,
        ).model_dump()
    )
    assert outcome.status == "completed"
    assert consumer.graph.invocations[0].resume["kind"] == "retry_timer"


def test_duplicate_retry_event_is_discarded_before_graph_invoke(consumer):
    consumer.graph.snapshot.next = ("wait_for_answer",)
    outcome = consumer.consume(
        InterviewRetryDueEvent(
            session_id="s1",
            generation_id="gen-1",
            next_attempt_number=2,
        ).model_dump()
    )
    assert outcome.status == "discarded_stale_retry"
    assert consumer.graph.invocations == []


def test_dispatcher_heartbeats_long_running_sink():
    repository = HeartbeatRepository(make_claim("event-1"))
    RuntimeOutboxDispatcher(
        repository,
        BlockingSink(repository.release),
        lease_seconds=3,
        heartbeat_seconds=1,
    ).run_once("worker-1")
    assert repository.heartbeats >= 1
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_workflow_consumer.py tests/test_runtime_outbox_dispatcher.py tests/test_runtime_lifecycle.py -q
```

Expected: missing consumer, event routing, and heartbeat.

- [ ] **Step 3: Implement graph event consumption**

```python
class InterviewWorkflowConsumer:
    def __init__(self, workflow) -> None:
        self.workflow = workflow

    def consume(self, payload: dict) -> ConsumerOutcome:
        event_type = payload["event_type"]
        config = {
            "configurable": {"thread_id": payload["session_id"]}
        }
        graph = self.workflow.graph_for_session(payload["session_id"])
        if event_type == "interview_command_ready":
            event = InterviewCommandReadyEvent.model_validate(payload)
            resume = {
                "kind": "answer_command",
                "command_id": event.command_id,
            }
        elif event_type == "interview_retry_due":
            event = InterviewRetryDueEvent.model_validate(payload)
            snapshot = graph.get_state(config)
            state = snapshot.values
            if (
                snapshot.next != ("wait_for_retry",)
                or state.get("generation_id") != event.generation_id
                or state.get("expected_retry_attempt")
                != event.next_attempt_number
            ):
                return ConsumerOutcome("discarded_stale_retry")
            resume = {
                "kind": "retry_timer",
                "generation_id": event.generation_id,
                "next_attempt_number": event.next_attempt_number,
            }
        else:
            raise ValueError("unsupported interview workflow event")
        graph.invoke(
            Command(resume=resume), config=config
        )
        return ConsumerOutcome("completed")
```

- [ ] **Step 4: Route Local and Celery sinks by event type**

```python
INTERVIEW_EVENT_TYPES = {
    "interview_command_ready",
    "interview_retry_due",
}


def publish(self, payload):
    if payload["event_type"] in INTERVIEW_EVENT_TYPES:
        self.interview_consumer.consume(payload)
        return
    consume_round_review_event_payload(
        payload,
        control_store=self.control_store,
        worker_id=self.worker_id,
        store=self.store,
    )
```

`CeleryRuntimeEventSink` maps interview event types to `app.services.interview_workflow_tasks.run_interview_workflow_event`; round_closed retains its existing task.

- [ ] **Step 5: Add guarded outbox heartbeats**

`PostgresRuntimeControlStore.extend_outbox_lease(event_id, worker_id, lease_seconds)` updates only the matching running lease. `RuntimeOutboxDispatcher` starts a short heartbeat helper around `sink.publish`, stops it in `finally`, and treats lost lease as a retryable failure. Do not mark an event published when its lease was lost.

- [ ] **Step 6: Wire runtime startup and shutdown order**

```text
start_runtime:
  1. start PostgreSQL checkpointer
  2. build versioned durable graph registry
  3. start local outbox dispatcher when configured

shutdown_runtime:
  1. stop accepting new outbox claims
  2. wait for in-flight sink calls
  3. close graph/checkpointer runtime
```

- [ ] **Step 7: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_interview_workflow_consumer.py tests/test_runtime_outbox_dispatcher.py tests/test_runtime_lifecycle.py tests/test_runtime_event_consumer.py -q
git add app/services/interview_workflow_consumer.py app/services/interview_workflow_tasks.py app/services/runtime_outbox_dispatcher.py app/services/postgres_runtime_control.py app/services/runtime.py app/services/celery_app.py tests/test_interview_workflow_consumer.py tests/test_runtime_outbox_dispatcher.py tests/test_runtime_lifecycle.py
git commit -m "feat: dispatch durable interview workflow events"
```

### Task 11: Add Engine Facade, API Commands, and Replayable SSE

**Files:**

- Create: `app/services/interview_workflow.py`
- Create: `app/services/interview_event_stream.py`
- Create: `tests/test_interview_event_stream.py`
- Modify: `app/ports/runtime.py`
- Modify: `app/services/runtime_events.py`
- Modify: `app/services/runtime.py`
- Modify: `app/services/postgres_session.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_runtime_events.py`
- Modify: `tests/test_runtime_boundary_api.py`

- [ ] **Step 1: Write failing API contract tests**

```python
def test_durable_answer_returns_accepted_command(durable_client):
    response = durable_client.post(
        "/api/interviews/s1/answer",
        json={
            "answer": "I used cache-aside.",
            "expected_version": 1,
            "command_id": "cmd-1",
        },
    )
    assert response.status_code == 202
    assert response.json() == {
        "session_id": "s1",
        "command_id": "cmd-1",
        "status": "pending",
        "workflow_engine": "langgraph-v1",
        "stream_url": "/api/interviews/s1/commands/cmd-1/stream",
    }


def test_legacy_answer_contract_is_unchanged(legacy_client):
    response = legacy_client.post(
        "/api/interviews/s1/answer",
        json={"answer": "answer", "expected_version": 1, "command_id": "c1"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert "stream_url" not in response.json()


def test_snapshot_derives_pending_action_from_graph_cursor(durable_client):
    durable_client.workflow.snapshot.next = ("wait_for_retry",)
    response = durable_client.get("/api/interviews/s1")
    assert response.json()["pending_action"] == "waiting_for_retry"
    assert "checkpoint_id" not in response.text
```

- [ ] **Step 2: Write failing stream event tests**

```python
def test_chunk_sse_has_replay_cursor():
    event = InterviewGenerationChunkEvent(
        generation_id="gen-1",
        attempt_number=2,
        sequence=3,
        delta="hello",
    )
    assert event.to_sse().startswith(
        "id: gen-1:2:3\nevent: chunk\n"
    )


def test_reset_event_precedes_replacement_chunks(stream_service):
    events = list(
        stream_service.iter_command_events(
            "s1", "cmd-1", after_event_id="gen-1:1:2"
        )
    )
    assert events[0].event == "generation_reset"
    assert events[0].attempt_number == 2
```

- [ ] **Step 3: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_runtime_events.py tests/test_interview_event_stream.py tests/test_runtime_boundary_api.py -q -k "durable or replay or pending_action or generation"
```

Expected: missing facade, accepted response, cursor events, and graph-derived pending action.

- [ ] **Step 4: Implement the application facade**

`InterviewSessionStore.start` and `PostgresInterviewSessionStore.start` gain
an optional keyword-only `session_id`. They continue generating a UUID when it
is absent, so every existing caller and test remains compatible. The facade
generates the ID before engine assignment and passes it into the selected start
path.

```python
class InterviewWorkflowService:
    def start(
        self,
        plan: InterviewPlan,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
    ) -> InterviewTurn:
        session_id = str(uuid4())
        engine = choose_workflow_engine(
            session_id,
            runtime_store=self.runtime_store,
            runtime_enabled=self.runtime_enabled,
            rollout_percent=self.rollout_percent,
        )
        if engine == "legacy":
            return self.legacy_store.start(
                plan,
                job_description=job_description,
                resume_text=resume_text,
                job_tags=job_tags,
                session_id=session_id,
            )
        self.legacy_store.insert_durable_session_shell(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )
        self.graph_for_version("langgraph-v1").invoke(
            make_durable_initial_state(session_id, plan),
            config={"configurable": {"thread_id": session_id}},
        )
        return self.legacy_store._to_turn(
            self.legacy_store.get(session_id), follow_up=None
        )
```

`submit_command` calls legacy store methods for legacy sessions and `enqueue_command` for durable sessions. Skip and finish use the same command inbox.
`graph_for_version(version)` performs an exact registry lookup;
`graph_for_session(session_id)` loads the immutable session
`graph_schema_version` and delegates to that lookup. Neither method falls back
to the configured default for an existing session.

- [ ] **Step 5: Add accepted command and cursor-aware SSE models**

```python
class AcceptedInterviewCommand(BaseModel):
    session_id: str
    command_id: str
    status: Literal["pending"] = "pending"
    workflow_engine: Literal["langgraph-v1"] = "langgraph-v1"
    stream_url: str


class InterviewGenerationChunkEvent(BaseModel):
    event: Literal["chunk"] = "chunk"
    generation_id: str
    attempt_number: int
    sequence: int
    delta: str

    def to_sse(self) -> str:
        event_id = (
            f"{self.generation_id}:{self.attempt_number}:{self.sequence}"
        )
        return _format_sse(self.event, self.model_dump(exclude={"event"}), event_id)
```

Add `command`, `generation_reset`, `chunk`, `done`, `conflict`, and `error` events. Stable errors contain codes only.

- [ ] **Step 6: Implement replay service and routes**

```python
@router.get("/interviews/{session_id}/commands/{command_id}/stream")
def stream_interview_command(
    session_id: str,
    command_id: str,
    request: Request,
    workflow=Depends(get_interview_workflow_service),
):
    last_event_id = request.headers.get("Last-Event-ID")
    return StreamingResponse(
        workflow.event_stream.iter_sse(
            session_id, command_id, after_event_id=last_event_id
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

`POST /answer`, `/skip`, and `/finish` return HTTP 202 for durable commands and preserve existing legacy responses. The existing POST `/answer/stream` keeps its legacy implementation; for durable sessions it enqueues idempotently and delegates to the same replay iterator so reposting the same command ID can reconnect safely.

- [ ] **Step 7: Derive pending action without storing it**

```python
PENDING_ACTION_BY_NODE = {
    "wait_for_answer": "waiting_for_answer",
    "generate_followup": "generating_followup",
    "wait_for_retry": "waiting_for_retry",
    "project_state": "committing_state",
}
```

For langgraph-v1 GET, call `graph.get_state({"configurable": {"thread_id": session_id}})`, map `snapshot.next` and interrupts, and merge only the public pending action plus active command/generation metadata. Never return checkpoint IDs, namespaces, tasks, leases, or raw interrupt payloads.

- [ ] **Step 8: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_api.py tests/test_runtime_events.py tests/test_interview_event_stream.py tests/test_runtime_boundary_api.py tests/test_session_service.py -q
git add app/services/interview_workflow.py app/services/interview_event_stream.py app/ports/runtime.py app/services/runtime_events.py app/services/runtime.py app/services/postgres_session.py app/api/routes.py tests/test_api.py tests/test_runtime_events.py tests/test_interview_event_stream.py tests/test_runtime_boundary_api.py
git commit -m "feat: expose replayable interview commands"
```

### Task 12: Reconnect the Browser Without Concatenating Attempts

**Files:**

- Modify: `app/static/api.js`
- Modify: `app/static/interview.js`
- Modify: `tests/test_static_report_ui.py`
- Modify: `tests/browser_support_app.py`
- Create: `tests/browser/langgraph-recovery.spec.js`

- [ ] **Step 1: Write failing static client tests**

```python
def test_sse_parser_preserves_event_id():
    js = Path("app/static/api.js").read_text(encoding="utf-8")
    assert 'if (line.startsWith("id:"))' in js
    assert "event.id =" in js


def test_interview_client_handles_generation_reset():
    js = Path("app/static/interview.js").read_text(encoding="utf-8")
    assert "generation_reset(data)" in js
    assert "activeAttemptNumber" in js
    assert "resumePendingGeneration(snapshot)" in js
```

- [ ] **Step 2: Write failing Playwright recovery cases**

```javascript
test("refresh replays persisted chunks and finishes once", async ({ page }) => {
  await startDurableInterview(page);
  await submitAnswer(page, "I used cache-aside.");
  await expect(page.locator(".message-assistant").last()).toContainText("first");
  await page.reload();
  await expect(page.locator(".message-assistant").last()).toContainText(
    "first complete"
  );
  await expect(page.locator(".message-assistant")).toHaveCount(2);
});

test("replacement attempt clears abandoned partial text", async ({ page }) => {
  await startDurableInterview(page, { failFirstAttempt: true });
  await submitAnswer(page, "I used cache-aside.");
  await expect(page.locator(".message-assistant").last()).toContainText(
    "abandoned"
  );
  await expect(page.locator(".message-assistant").last()).toHaveText(
    "replacement complete"
  );
});

test("duplicate command after reconnect creates one candidate message", async ({ page }) => {
  await startDurableInterview(page);
  const commandId = "browser-fixed-command";
  await submitAnswer(page, "same answer", { commandId, disconnect: true });
  await submitAnswer(page, "same answer", { commandId });
  await expect(page.locator(".message-candidate")).toHaveCount(1);
});
```

- [ ] **Step 3: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q -k "sse_parser or generation_reset"
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm run test:browser -- --grep "refresh replays|replacement attempt|duplicate command"
```

Expected: missing cursor parsing and durable browser endpoints.

- [ ] **Step 4: Preserve SSE IDs in the shared parser**

```javascript
function parseSseEvent(rawEvent) {
  const event = { id: null, event: "message", data: {} };
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("id:")) {
      event.id = line.slice("id:".length).trim();
    } else if (line.startsWith("event:")) {
      event.event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      event.data = JSON.parse(line.slice("data:".length).trim());
    }
  }
  return event;
}
```

`readSse` passes `event.id` as the second handler argument and returns the last observed ID.

- [ ] **Step 5: Implement attempt replacement and reconnect**

```javascript
let activeCommandId = null;
let activeGenerationId = null;
let activeAttemptNumber = 0;
let lastGenerationEventId = null;
let activeStreamingBubble = null;

function applyGenerationReset(data) {
  if (data.attempt_number <= activeAttemptNumber) return;
  activeAttemptNumber = data.attempt_number;
  lastGenerationEventId = data.event_id || null;
  activeStreamingBubble.textContent = "";
}

async function resumeCommandStream(streamUrl) {
  const headers = lastGenerationEventId
    ? { "Last-Event-ID": lastGenerationEventId }
    : {};
  const response = await fetch(streamUrl, { headers });
  await readSse(response, {
    generation_reset(data, id) {
      applyGenerationReset(
        Object.assign({}, data, { event_id: id })
      );
    },
    chunk(data, id) {
      if (data.attempt_number < activeAttemptNumber) return;
      activeAttemptNumber = data.attempt_number;
      lastGenerationEventId = id;
      activeStreamingBubble.textContent += data.delta || "";
    },
    conflict() {
      throw new HttpError("Interview state changed", { status: 409 });
    },
    done() {
      activeCommandId = null;
      activeGenerationId = null;
      activeStreamingBubble = null;
    },
  });
}
```

`renderSnapshot` calls `resumePendingGeneration(snapshot)` when a durable
snapshot exposes an active command stream. Browser refresh reconstructs the
committed messages first, assigns one newly created bubble to
`activeStreamingBubble`, then replays after the server-provided cursor. The
normal submit path assigns the same variable before opening the stream. Never
append replacement-attempt chunks to abandoned text.

- [ ] **Step 6: Extend browser support with deterministic streams**

`tests/browser_support_app.py` stores command status, attempts, and chunks in
process memory for deterministic UI tests. Give it a small
`FakeGenerationStore` with the production-facing methods below, backed by a
dictionary keyed by `generation_id` and then attempt number:

```python
class FakeGenerationStore:
    def __init__(self) -> None:
        self.generations: dict[str, dict] = {}
        self.next_event_id = 0

    def prepare_generation(self, generation_id: str) -> dict:
        return self.generations.setdefault(
            generation_id, {"attempts": {}, "events": []}
        )

    def start_attempt(self, generation_id: str, attempt_number: int) -> dict:
        generation = self.prepare_generation(generation_id)
        return generation["attempts"].setdefault(
            attempt_number, {"chunks": [], "status": "running"}
        )

    def append_chunk(
        self, generation_id: str, attempt_number: int, sequence: int, text: str
    ) -> None:
        attempt = self.start_attempt(generation_id, attempt_number)
        attempt["chunks"].append((sequence, text))
        self.next_event_id += 1
        self.prepare_generation(generation_id)["events"].append(
            {
                "id": self.next_event_id,
                "kind": "chunk",
                "attempt_number": attempt_number,
                "sequence": sequence,
                "text": text,
            }
        )

    def complete_attempt(
        self, generation_id: str, attempt_number: int, final_text: str
    ) -> None:
        attempt = self.start_attempt(generation_id, attempt_number)
        attempt["status"] = "completed"
        attempt["final_text"] = final_text
        self.next_event_id += 1
        self.prepare_generation(generation_id)["events"].append(
            {
                "id": self.next_event_id,
                "kind": "completed",
                "attempt_number": attempt_number,
            }
        )

    def list_events(self, generation_id: str, after_id: int) -> list[dict]:
        return [
            event
            for event in self.prepare_generation(generation_id)["events"]
            if event["id"] > after_id
        ]
```

The support app's command stream honors `Last-Event-ID`, emits a reset before
replacement chunks, and deduplicates fixed command IDs. This support app does
not claim process-loss durability; PostgreSQL recovery is Task 14.

- [ ] **Step 7: Verify and commit**

```powershell
node --check app/static/api.js
node --check app/static/interview.js
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm run test:browser -- --grep "refresh replays|replacement attempt|duplicate command"
git add app/static/api.js app/static/interview.js tests/test_static_report_ui.py tests/browser_support_app.py tests/browser/langgraph-recovery.spec.js
git commit -m "feat: reconnect durable interview streams"
```

### Task 13: Add Retention, Privacy, Runtime Metadata, and Operations Docs

**Files:**

- Modify: `app/services/interview_workflow_store.py`
- Modify: `app/services/interview_generation_store.py`
- Modify: `app/services/langgraph_runtime.py`
- Modify: `app/services/runtime.py`
- Modify: `app/api/routes.py`
- Modify: `scripts/audit_agent_runtime.py`
- Modify: `scripts/runtime_preflight.py`
- Modify: `tests/test_agent_runtime_audit.py`
- Modify: `tests/test_runtime_preflight.py`
- Modify: `tests/test_runtime_boundary_api.py`
- Modify: `tests/test_local_v1_docs.py`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Create: `docs/langgraph-interview-recovery-acceptance.md`

- [ ] **Step 1: Write failing privacy and cleanup tests**

```python
def test_control_exports_exclude_interview_text(runtime_export):
    serialized = json.dumps(runtime_export, ensure_ascii=False)
    for forbidden in (
        "job_description",
        "resume_text",
        "answer_text",
        "provider_payload",
        "lease_owner",
        "checkpoint_id",
    ):
        assert forbidden not in serialized


def test_applied_command_payload_can_be_cleared(workflow_store):
    seed_applied_answer(workflow_store, answer="private answer")
    assert workflow_store.clear_applied_command_payloads(
        older_than=utc_now() + timedelta(seconds=1)
    ) == 1
    command = workflow_store.get_command(
        workflow_store.session_id, "cmd-1"
    )
    assert command.answer_text is None
    assert command.payload_sha256


def test_session_purge_deletes_checkpoints_and_chunks(workflow_runtime):
    workflow_runtime.purge_session("s1")
    assert workflow_runtime.checkpointer.get(
        {"configurable": {"thread_id": "s1"}}
    ) is None
    assert workflow_runtime.generation_store.count_session_rows("s1") == 0
```

- [ ] **Step 2: Run tests and confirm failure**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime_audit.py tests/test_runtime_preflight.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py -q
```

Expected: missing cleanup, privacy allowlist, and runtime metadata.

- [ ] **Step 3: Implement bounded cleanup**

```python
def cleanup_completed_chunks(self, *, older_than: datetime) -> int:
    _, sql = self._import_psycopg2()
    with self.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
                    DELETE FROM {chunks}
                    USING {generations}
                    WHERE {chunks}.generation_id =
                          {generations}.generation_id
                      AND {generations}.status = 'completed'
                      AND {generations}.completed_at < %s
                    """
                ).format(
                    chunks=sql.Identifier(self.chunks_table),
                    generations=sql.Identifier(self.generations_table),
                ),
                (older_than,),
            )
            return cursor.rowcount


def purge_session(self, session_id: str) -> None:
    self.checkpointer.delete_thread(session_id)
    self.workflow_store.delete_session_control_rows(session_id)
    self.generation_store.delete_session_rows(session_id)
```

Implement the SQL fully in the production module: clear applied inbox answer text after successful checkpoint incorporation, delete completed chunks after 24 hours, and delete all workflow rows plus the LangGraph thread on explicit session purge. Cleanup must not delete active or retrying generations.

- [ ] **Step 4: Enforce metadata allowlists**

Runtime diagnostics expose:

```python
"orchestration": {
    "engine": "versioned",
    "default_engine": "legacy",
    "langgraph_version": "langgraph-v1",
    "langgraph_runtime_enabled": runtime_enabled,
    "langgraph_rollout_percent": rollout_percent,
    "checkpoint_backend": (
        "postgres" if runtime_enabled and runtime_store == "postgres"
        else "disabled"
    ),
    "resume_contract": "checkpointed_http_sse",
}
```

Do not expose saver table names, checkpoint IDs, namespaces, thread tasks, DSNs, paths, payload JSON, command answer text, chunk text, or leases.

- [ ] **Step 5: Extend preflight and privacy audit**

`runtime_preflight --profile core` verifies:

- rollout greater than zero requires PostgreSQL and an enabled LangGraph runtime;
- PostgreSQL saver setup succeeds;
- strict msgpack is enabled;
- command, generation, attempt, and chunk tables exist;
- indexes cover command status, outbox available time, generation source command, and chunk replay order;
- cleanup retention is positive;
- a generated diagnostic export passes the blocked-key and blocked-value scan.

- [ ] **Step 6: Document rollout and recovery**

Document exact local commands:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
$env:INTERVIEW_RUNTIME_STORE='postgres'
$env:INTERVIEW_EVENT_BACKEND='local'
$env:INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT='1'
$env:LANGGRAPH_STRICT_MSGPACK='true'
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Explain message-level recovery, reset semantics, 24-hour chunk retention, v1 graph retention, rollout rollback by assignment only, and why active v1 workers cannot be removed.

- [ ] **Step 7: Create a pending acceptance record**

`docs/langgraph-interview-recovery-acceptance.md` starts with `Status: PENDING_RECOVERY_ACCEPTANCE` and lists the exact Task 14 and Task 15 gates. It contains no credentials, DSNs, raw conversation, provider output, absolute paths, or checkpoint IDs.

- [ ] **Step 8: Verify and commit**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime_audit.py tests/test_runtime_preflight.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py -q
git add app/services/interview_workflow_store.py app/services/interview_generation_store.py app/services/langgraph_runtime.py app/services/runtime.py app/api/routes.py scripts/audit_agent_runtime.py scripts/runtime_preflight.py tests/test_agent_runtime_audit.py tests/test_runtime_preflight.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py README.md docs/local-v1-runbook.md docs/langgraph-interview-recovery-acceptance.md
git commit -m "docs: define langgraph recovery operations"
```

### Task 14: Prove PostgreSQL Crash and Restart Recovery

**Files:**

- Create: `tests/test_langgraph_recovery_postgres.py`
- Create: `scripts/langgraph_recovery_acceptance.py`
- Create: `tests/test_langgraph_recovery_acceptance.py`
- Modify: `docs/langgraph-interview-recovery-acceptance.md`
- Modify: `pytest.ini`

- [ ] **Step 1: Register marker and write failing fault matrix**

Add:

```ini
langgraph_recovery: tests requiring PostgreSQL LangGraph crash recovery
```

```python
@pytest.mark.langgraph_recovery
@pytest.mark.parametrize(
    "fault_point",
    [
        "after_command_commit",
        "after_candidate_projection",
        "after_generation_prepare",
        "after_partial_chunks",
        "after_generation_complete",
        "after_projection_write",
        "after_report_enqueue",
    ],
)
def test_restart_recovers_without_duplicate_business_output(
    postgres_runtime_factory, fault_point
):
    first = postgres_runtime_factory(fault_point=fault_point)
    session_id = first.start_session()
    command_id = first.enqueue_answer(session_id, "answer")
    with pytest.raises(InjectedProcessLoss):
        first.run_until_fault()
    first.close_without_cleanup()

    recovered = postgres_runtime_factory()
    recovered.reclaim_expired_work()
    recovered.run_pending()
    snapshot = recovered.snapshot(session_id)
    assert snapshot["messages"].count(
        {"role": "candidate", "content": "answer", "question_id": "q1"}
    ) == 1
    assert len(
        [item for item in snapshot["messages"] if item["role"] == "interviewer"]
    ) == 2
    assert recovered.command(command_id).status == "applied"
    assert recovered.report_job_count(session_id) <= 1
```

- [ ] **Step 2: Add focused invariants**

```python
def test_projection_failure_reuses_public_version(runtime):
    runtime.inject("after_projection_write")
    runtime.enqueue_answer("s1", "answer")
    with pytest.raises(InjectedProcessLoss):
        runtime.run_pending()
    assert runtime.session_row("s1").state_version == 2
    runtime.restart().run_pending()
    assert runtime.session_row("s1").state_version == 3
    assert runtime.command("cmd-1").result_state_version == 3
    assert runtime.message_count("s1", role="candidate") == 1


def test_mid_stream_restart_emits_reset_before_replacement(runtime):
    runtime.inject("after_partial_chunks")
    runtime.enqueue_answer("s1", "answer")
    with pytest.raises(InjectedProcessLoss):
        runtime.run_pending()
    runtime.expire_generation_lease("s1")
    runtime.restart().run_pending()
    events = runtime.generation_events("s1")
    reset_index = next(i for i, item in enumerate(events) if item.event_type == "generation_reset")
    assert events[reset_index + 1].attempt_number == 2
    assert runtime.final_message("s1") == "replacement complete"


def test_due_retry_does_not_run_early(runtime):
    retry = runtime.seed_retry(available_in_seconds=30)
    assert runtime.dispatch_once() == 0
    runtime.advance_database_clock(seconds=31)
    assert runtime.dispatch_once() == 1
```

- [ ] **Step 3: Run and confirm failures**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest tests/test_langgraph_recovery_postgres.py tests/test_langgraph_recovery_acceptance.py -q
```

Expected: missing acceptance harness or uncovered recovery defects.

- [ ] **Step 4: Implement the acceptance runner**

```python
CHECKS = (
    "command_commit_rpo_zero",
    "candidate_projection_idempotent",
    "generation_prepare_recovery",
    "partial_stream_reset",
    "completed_generation_reuse",
    "projection_version_reuse",
    "report_enqueue_idempotent",
    "retry_timer_not_early",
    "duplicate_command_one_message",
    "privacy_allowlist",
)
```

The runner uses a unique session UUID namespace, owns only processes it starts, closes saver contexts in `finally`, deletes its test threads and prefixed application tables, and emits sanitized JSON plus Markdown summaries under `tmp/langgraph-recovery-acceptance`.

- [ ] **Step 5: Verify recovery time objective**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m scripts.langgraph_recovery_acceptance --timeout 30
```

Expected: all ten checks PASS, acknowledged answer RPO is zero, normal restart recovery completes within 30 seconds, and no duplicate committed messages exist.

- [ ] **Step 6: Record evidence and commit**

Update the acceptance record with timestamp, commit ID, check names, durations, retry counts, and privacy result only.

```powershell
git add tests/test_langgraph_recovery_postgres.py scripts/langgraph_recovery_acceptance.py tests/test_langgraph_recovery_acceptance.py docs/langgraph-interview-recovery-acceptance.md pytest.ini
git commit -m "test: prove langgraph interview recovery"
```

### Task 15: Run Browser, Compatibility, and Full Release Gates

**Files:**

- Modify: `docs/langgraph-interview-recovery-acceptance.md`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`

- [ ] **Step 1: Run focused Python contracts**

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_durable_interview_state.py tests/test_durable_interview_graph.py tests/test_interview_workflow_store.py tests/test_interview_generation_store.py tests/test_interview_workflow_consumer.py tests/test_interview_event_stream.py tests/test_api.py tests/test_runtime_events.py tests/test_runtime_lifecycle.py -q
```

Expected: PASS.

- [ ] **Step 2: Run PostgreSQL recovery contracts**

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
& 'F:\python3.11\python.exe' -m pytest -m "pg_runtime or pg_control or langgraph_recovery" -q
& 'F:\python3.11\python.exe' -m scripts.langgraph_recovery_acceptance --timeout 30
```

Expected: PASS with only unrelated opt-in markers deselected.

- [ ] **Step 3: Run frontend syntax and deterministic browser recovery**

```powershell
node --check app/static/api.js
node --check app/static/interview.js
npm run build:prototype-css
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm run test:browser
```

Expected: desktop and mobile projects pass refresh replay, disconnect replay, generation reset, duplicate command, version conflict, legacy resume, and existing five-page flows.

- [ ] **Step 4: Run core privacy and operational gates**

```powershell
& 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile core
& 'F:\python3.11\python.exe' -m pytest tests/test_agent_runtime_audit.py tests/test_runtime_preflight.py tests/test_runtime_boundary_api.py tests/test_local_v1_docs.py -q
```

Expected: PASS with no blocked values in checkpoint metadata, runtime exports, logs, or acceptance artifacts.

- [ ] **Step 5: Run the full regression suite**

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: PASS; only documented external-service and real-model tests may skip.

- [ ] **Step 6: Exercise rollout and rollback assignment**

```powershell
$env:INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT='1'
& 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile core
$env:INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT='0'
& 'F:\python3.11\python.exe' -m scripts.runtime_preflight --profile core
```

Expected: new assignment changes, existing langgraph-v1 threads remain resumable in both runs, and legacy sessions are never migrated.

- [ ] **Step 7: Mark the acceptance record PASS and commit**

Set `Status: PASS` only after every command above passes. Record exact test counts, browser project counts, PostgreSQL recovery durations, rollout values, and the final commit ID. Do not record credentials, DSNs, raw interview text, paths, checkpoint IDs, or generation chunks.

```powershell
git add docs/langgraph-interview-recovery-acceptance.md README.md docs/local-v1-runbook.md
git commit -m "docs: accept langgraph interview recovery"
```

## Final Review Checklist

- [ ] New sessions alone can be assigned to langgraph-v1; legacy sessions never migrate.
- [ ] Memory runtime remains legacy and makes no process-recovery claim.
- [ ] PostgreSQL saver setup runs once with strict msgpack enabled.
- [ ] Graph state has no `pending_action`; GET derives it from StateSnapshot.
- [ ] Plan snapshot and messages are bounded and self-contained; JD, resume, and evidence text stay outside checkpoints.
- [ ] Only `project_state` advances public `state_version`.
- [ ] Projection replay after checkpoint failure reuses the same next version and payload hash.
- [ ] Projection clears transient command, generation, and retry route fields.
- [ ] Inbox and outbox commit together; outbox never stores answer text.
- [ ] Duplicate command IDs with changed payloads fail closed.
- [ ] Durable Examiner attempts propagate errors; legacy methods retain immediate fallback.
- [ ] Provider retries use `enqueue_retry -> wait_for_retry -> validate_retry -> retry_due`; no worker sleeps for backoff.
- [ ] A retry timer can advance only the checkpointed expected attempt; duplicate and stale events are discarded.
- [ ] The database clock determines retry visibility; due-retry tests advance that clock rather than sleeping.
- [ ] The final finished projection commits before the idempotent report enqueue becomes visible.
- [ ] Every long-running outbox sink heartbeats its lease.
- [ ] Generation chunks are attempt-scoped and replacement attempts emit reset before chunks.
- [ ] SSE disconnect never cancels graph execution.
- [ ] Browser refresh replays from Last-Event-ID without duplicate messages.
- [ ] Finish emits at most one report job and existing report ownership remains unchanged.
- [ ] Session purge removes checkpoint thread, inbox, outbox, attempts, chunks, and projections.
- [ ] Diagnostics and acceptance artifacts contain no raw interview text or infrastructure secrets.
- [ ] PostgreSQL fault matrix passes with RPO zero and restart recovery within 30 seconds.
- [ ] Rollback disables new assignment but retains v1 runtime for existing threads.
