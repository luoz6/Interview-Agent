# Stage 6 PostgreSQL Runtime Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist interview runtime state, transcript messages, report progress, final reports, and report failures in PostgreSQL while preserving the existing API and frontend contracts.

**Architecture:** Add a PostgreSQL-backed store that implements the current `InterviewSessionStore` behavior while keeping the graph runner and evaluator database-agnostic. Runtime wiring selects memory mode by default and PostgreSQL mode only when explicitly enabled.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, PostgreSQL, psycopg2-binary, pytest, current HTML/CSS/JavaScript frontend.

---

## File Structure

- Create: `app/services/session_serialization.py`
  - Converts `InterviewState`, `InterviewMessage`, `ReportRecord`, `ReportProgress`, and `InterviewReport` to and from JSON-safe dictionaries.

- Create: `app/services/postgres_session.py`
  - Implements `PostgresInterviewSessionStore`.
  - Owns runtime table schema initialization and SQL persistence.

- Create: `app/services/runtime.py`
  - Builds the active session store and keeps route dependency wiring out of `routes.py`.

- Modify: `app/api/routes.py`
  - Replace direct global `InterviewSessionStore()` creation with runtime provider.

- Modify: `pytest.ini`
  - Register `pg_runtime` marker.

- Create: `tests/test_session_serialization.py`
  - Fast unit tests for state/report serialization.

- Create: `tests/test_postgres_session_store.py`
  - PostgreSQL integration tests, skipped unless `POSTGRES_DSN` exists.

- Create: `tests/test_runtime_provider.py`
  - Unit tests for memory vs PostgreSQL runtime selection.

- Modify as needed: existing API/session tests
  - Keep memory-store injection behavior intact.

Unified test commands:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

PostgreSQL runtime tests:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest -q -m pg_runtime
```

---

### Task 1: Add Session And Report Serialization Helpers

**Files:**
- Create: `tests/test_session_serialization.py`
- Create: `app/services/session_serialization.py`

- [ ] **Step 1: Write failing serialization tests**

Create `tests/test_session_serialization.py`:

```python
from app.graphs.interview_state import build_initial_state
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import (
    DimensionScores,
    FeedbackReference,
    InterviewFeedback,
    InterviewReport,
    ReportProgress,
    ReportRecord,
)
from app.services.session_serialization import (
    message_to_row,
    report_record_from_row,
    report_record_to_row,
    session_row_from_state,
    state_from_rows,
)


def make_plan():
    return InterviewPlan(
        title="Backend Interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Describe your backend project.",
                focus="Project depth",
            )
        ],
    )


def make_state():
    return build_initial_state(
        session_id="s1",
        plan=make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )


def make_report_record():
    report = InterviewReport(
        session_id="s1",
        overall_score=80,
        overall_dimension_scores=DimensionScores(
            breadth=80,
            depth=78,
            architecture=75,
            engineering=82,
            communication=84,
        ),
        summary="Solid backend project explanation.",
        highlights=["Clear project context"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Describe your backend project.",
                user_answer="I built a FastAPI service.",
                score=80,
                dimension_scores=DimensionScores(
                    breadth=80,
                    depth=78,
                    architecture=75,
                    engineering=82,
                    communication=84,
                ),
                rationale="The answer covered project context and implementation.",
                critique="Failure modes need more detail.",
                better_answer="Explain traffic, storage, cache, failure handling, and tradeoffs.",
                references=[
                    FeedbackReference(
                        chunk_id="fastapi_backend",
                        title="FastAPI Backend",
                        source_type="expert_benchmark",
                        excerpt="High quality answers include API boundaries and failure handling.",
                    )
                ],
            )
        ],
    )
    return ReportRecord(status="completed", report=report)


def test_state_round_trips_from_session_and_message_rows():
    state = make_state()
    session_row = session_row_from_state(state)
    message_rows = [
        message_to_row("s1", index + 1, message)
        for index, message in enumerate(state["messages"])
    ]

    restored = state_from_rows(session_row, message_rows)

    assert restored["session_id"] == "s1"
    assert restored["plan"].questions[0].prompt == "Describe your backend project."
    assert restored["messages"] == state["messages"]
    assert restored["job_tags"] == ["python", "fastapi"]


def test_report_record_round_trips_from_row():
    record = make_report_record()
    row = report_record_to_row(record)

    restored = report_record_from_row(row)

    assert restored.status == "completed"
    assert restored.report is not None
    assert restored.report.overall_score == 80
    assert restored.report.feedbacks[0].references[0].chunk_id == "fastapi_backend"


def test_processing_report_record_round_trips_from_row():
    record = ReportRecord(
        status="processing",
        progress=ReportProgress(
            stage="retrieving",
            percent=20,
            message="Retrieving references.",
        ),
    )
    row = report_record_to_row(record)

    restored = report_record_from_row(row)

    assert restored.status == "processing"
    assert restored.progress is not None
    assert restored.progress.percent == 20
```

- [ ] **Step 2: Run tests and verify red**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_serialization.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.session_serialization'`.

- [ ] **Step 3: Implement serialization helpers**

Create `app/services/session_serialization.py`:

```python
from typing import Any

from app.graphs.interview_state import InterviewMessage, InterviewState
from app.services.prep import InterviewPlan
from app.services.report import ReportProgress, ReportRecord, InterviewReport


def session_row_from_state(state: InterviewState) -> dict[str, Any]:
    return {
        "session_id": state["session_id"],
        "plan_json": state["plan"].model_dump(mode="json"),
        "current_index": state["current_index"],
        "status": state["status"],
        "job_description": state["job_description"],
        "resume_text": state["resume_text"],
        "job_tags": list(state["job_tags"]),
        "decision_json": state["decision"],
        "pending_output": state["pending_output"],
    }


def message_to_row(
    session_id: str,
    sequence_no: int,
    message: InterviewMessage,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "sequence_no": sequence_no,
        "role": message["role"],
        "content": message["content"],
        "question_id": message["question_id"],
    }


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
        "job_description": session_row["job_description"],
        "resume_text": session_row["resume_text"],
        "job_tags": list(session_row["job_tags"]),
    }


def report_record_to_row(record: ReportRecord) -> dict[str, Any]:
    return {
        "status": record.status,
        "progress_json": record.progress.model_dump(mode="json")
        if record.progress is not None
        else None,
        "report_json": record.report.model_dump(mode="json")
        if record.report is not None
        else None,
        "error": record.error,
    }


def report_record_from_row(row: dict[str, Any]) -> ReportRecord:
    progress = (
        ReportProgress.model_validate(row["progress_json"])
        if row.get("progress_json") is not None
        else None
    )
    report = (
        InterviewReport.model_validate(row["report_json"])
        if row.get("report_json") is not None
        else None
    )
    return ReportRecord(
        status=row["status"],
        progress=progress,
        report=report,
        error=row.get("error"),
    )
```

- [ ] **Step 4: Run tests and verify green**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_serialization.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/session_serialization.py tests/test_session_serialization.py
git commit -m "feat: add interview runtime serialization helpers"
```

---

### Task 2: Add PostgreSQL Store Schema Initialization

**Files:**
- Modify: `pytest.ini`
- Create: `tests/test_postgres_session_store.py`
- Create: `app/services/postgres_session.py`

- [ ] **Step 1: Register pytest marker**

Modify `pytest.ini`:

```ini
[pytest]
markers =
    pgvector: requires PostgreSQL with pgvector extension
    pg_runtime: requires PostgreSQL runtime persistence database
```

- [ ] **Step 2: Write failing schema test**

Create `tests/test_postgres_session_store.py`:

```python
import os
from uuid import uuid4

import pytest

from app.services.postgres_session import PostgresInterviewSessionStore


pytestmark = pytest.mark.pg_runtime


def require_dsn():
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for pg_runtime tests")
    return dsn


def make_table_prefix():
    return "test_runtime_" + uuid4().hex[:12]


def test_schema_initializes_runtime_tables():
    store = PostgresInterviewSessionStore(
        dsn=require_dsn(),
        table_prefix=make_table_prefix(),
    )

    tables = store.list_runtime_tables()

    assert set(tables) == {
        store.sessions_table,
        store.messages_table,
        store.reports_table,
    }
```

- [ ] **Step 3: Run test and verify red**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_schema_initializes_runtime_tables -q -m pg_runtime
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.postgres_session'`.

- [ ] **Step 4: Implement schema initialization**

Create `app/services/postgres_session.py`:

```python
from app.services.llm import InterviewLLM
from app.services.session import InterviewSessionStore


class PostgresInterviewSessionStore(InterviewSessionStore):
    def __init__(
        self,
        *,
        dsn: str,
        table_prefix: str = "interview",
        llm: InterviewLLM | None = None,
    ) -> None:
        super().__init__(llm=llm)
        self.dsn = dsn
        self.table_prefix = table_prefix
        self.sessions_table = f"{table_prefix}_sessions"
        self.messages_table = f"{table_prefix}_messages"
        self.reports_table = f"{table_prefix}_reports"
        self._ensure_schema()

    def list_runtime_tables(self) -> list[str]:
        psycopg2, _ = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = ANY(%s)
                    ORDER BY table_name
                    """,
                    ([self.sessions_table, self.messages_table, self.reports_table],),
                )
                return [row[0] for row in cursor.fetchall()]

    def _ensure_schema(self) -> None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {sessions} (
                            session_id TEXT PRIMARY KEY,
                            plan_json JSONB NOT NULL,
                            current_index INTEGER NOT NULL DEFAULT 0,
                            status TEXT NOT NULL CHECK (status IN ('active', 'finished')),
                            job_description TEXT NOT NULL,
                            resume_text TEXT NOT NULL,
                            job_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                            decision_json JSONB,
                            pending_output TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            finished_at TIMESTAMPTZ
                        )
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table))
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {messages} (
                            id BIGSERIAL PRIMARY KEY,
                            session_id TEXT NOT NULL REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                            sequence_no INTEGER NOT NULL,
                            role TEXT NOT NULL CHECK (role IN ('interviewer', 'candidate')),
                            content TEXT NOT NULL,
                            question_id TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            UNIQUE (session_id, sequence_no)
                        )
                        """
                    ).format(
                        messages=sql.Identifier(self.messages_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE INDEX IF NOT EXISTS {index_name}
                        ON {messages} (session_id, sequence_no)
                        """
                    ).format(
                        index_name=sql.Identifier(f"{self.messages_table}_session_idx"),
                        messages=sql.Identifier(self.messages_table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {reports} (
                            session_id TEXT PRIMARY KEY REFERENCES {sessions}(session_id) ON DELETE CASCADE,
                            status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
                            progress_json JSONB,
                            report_json JSONB,
                            error TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            completed_at TIMESTAMPTZ,
                            failed_at TIMESTAMPTZ
                        )
                        """
                    ).format(
                        reports=sql.Identifier(self.reports_table),
                        sessions=sql.Identifier(self.sessions_table),
                    )
                )

    @staticmethod
    def _import_psycopg2():
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise RuntimeError("psycopg2-binary is required") from exc
        return psycopg2, sql
```

- [ ] **Step 5: Run test and verify green**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_schema_initializes_runtime_tables -q -m pg_runtime
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add pytest.ini app/services/postgres_session.py tests/test_postgres_session_store.py
git commit -m "feat: initialize postgres runtime tables"
```

---

### Task 3: Persist And Recover Started Sessions

**Files:**
- Modify: `tests/test_postgres_session_store.py`
- Modify: `app/services/postgres_session.py`

- [ ] **Step 1: Add failing start/recovery test**

Append to `tests/test_postgres_session_store.py`:

```python
from app.services.prep import InterviewPlan, InterviewQuestion


def make_plan():
    return InterviewPlan(
        title="Backend Interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Describe your backend project.",
                focus="Project depth",
            )
        ],
    )


def test_started_session_survives_store_reinstantiation():
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
    state = recovered_store.get(turn.session_id)

    assert state["session_id"] == turn.session_id
    assert state["plan"].title == "Backend Interview"
    assert state["messages"][0]["role"] == "interviewer"
    assert state["messages"][0]["content"] == "Describe your backend project."
    assert state["job_tags"] == ["python", "fastapi"]
```

- [ ] **Step 2: Run test and verify red**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_started_session_survives_store_reinstantiation -q -m pg_runtime
```

Expected: FAIL because inherited memory implementation does not persist rows.

- [ ] **Step 3: Implement `start()` and `get()` persistence**

Modify `app/services/postgres_session.py` by adding imports:

```python
import json
from uuid import uuid4

from app.graphs.interview_state import InterviewState
from app.services.prep import InterviewPlan
from app.services.session import InterviewTurn
from app.services.session_serialization import (
    message_to_row,
    session_row_from_state,
    state_from_rows,
)
```

Add methods inside `PostgresInterviewSessionStore`:

```python
    def start(
        self,
        plan: InterviewPlan,
        *,
        job_description: str,
        resume_text: str,
        job_tags: list[str],
    ) -> InterviewTurn:
        session_id = str(uuid4())
        state = self._runner.start(
            session_id=session_id,
            plan=plan,
            job_description=job_description,
            resume_text=resume_text,
            job_tags=job_tags,
        )
        self._insert_state(state)
        return self._to_turn(state, follow_up=None)

    def get(self, session_id: str) -> InterviewState:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT session_id, plan_json, current_index, status,
                               job_description, resume_text, job_tags,
                               decision_json, pending_output
                        FROM {sessions}
                        WHERE session_id = %s
                        """
                    ).format(sessions=sql.Identifier(self.sessions_table)),
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("session not found")
                session_row = self._session_row_from_db(row)
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT sequence_no, role, content, question_id
                        FROM {messages}
                        WHERE session_id = %s
                        ORDER BY sequence_no
                        """
                    ).format(messages=sql.Identifier(self.messages_table)),
                    (session_id,),
                )
                message_rows = [
                    {
                        "sequence_no": item[0],
                        "role": item[1],
                        "content": item[2],
                        "question_id": item[3],
                    }
                    for item in cursor.fetchall()
                ]
        return state_from_rows(session_row, message_rows)

    def _insert_state(self, state: InterviewState) -> None:
        psycopg2, sql = self._import_psycopg2()
        session_row = session_row_from_state(state)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {sessions} (
                            session_id, plan_json, current_index, status,
                            job_description, resume_text, job_tags,
                            decision_json, pending_output, finished_at
                        )
                        VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
                                CASE WHEN %s = 'finished' THEN NOW() ELSE NULL END)
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
                        session_row["status"],
                    ),
                )
                for index, message in enumerate(state["messages"], start=1):
                    message_row = message_to_row(state["session_id"], index, message)
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {messages} (
                                session_id, sequence_no, role, content, question_id
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """
                        ).format(messages=sql.Identifier(self.messages_table)),
                        (
                            message_row["session_id"],
                            message_row["sequence_no"],
                            message_row["role"],
                            message_row["content"],
                            message_row["question_id"],
                        ),
                    )

    @staticmethod
    def _session_row_from_db(row) -> dict:
        return {
            "session_id": row[0],
            "plan_json": row[1],
            "current_index": row[2],
            "status": row[3],
            "job_description": row[4],
            "resume_text": row[5],
            "job_tags": row[6],
            "decision_json": row[7],
            "pending_output": row[8],
        }
```

- [ ] **Step 4: Run test and verify green**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_started_session_survives_store_reinstantiation -q -m pg_runtime
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/postgres_session.py tests/test_postgres_session_store.py
git commit -m "feat: persist started interview sessions"
```

---

### Task 4: Persist Answer Submission State Transitions

**Files:**
- Modify: `tests/test_postgres_session_store.py`
- Modify: `app/services/postgres_session.py`

- [ ] **Step 1: Add failing submit persistence test**

Append to `tests/test_postgres_session_store.py`:

```python
def test_submit_answer_persists_candidate_and_followup_messages():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    answered = store.submit_answer(turn.session_id, "I built a FastAPI API.")

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    state = recovered_store.get(turn.session_id)
    assert answered.follow_up is not None
    assert [message["role"] for message in state["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert state["messages"][1]["content"] == "I built a FastAPI API."
    assert state["messages"][2]["content"] == answered.follow_up
```

- [ ] **Step 2: Run test and verify red**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_submit_answer_persists_candidate_and_followup_messages -q -m pg_runtime
```

Expected: FAIL because inherited memory `submit_answer()` does not update PostgreSQL.

- [ ] **Step 3: Implement persisted `submit_answer()`**

Add methods to `PostgresInterviewSessionStore`:

```python
    def submit_answer(self, session_id: str, answer: str) -> InterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")
        state = self.get(session_id)
        new_state = self._runner.submit_answer(state, answer)
        self._replace_state(new_state)
        return self._to_turn(new_state, follow_up=self._extract_follow_up(new_state))

    def _replace_state(self, state: InterviewState) -> None:
        psycopg2, sql = self._import_psycopg2()
        session_row = session_row_from_state(state)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DELETE FROM {messages} WHERE session_id = %s").format(
                        messages=sql.Identifier(self.messages_table)
                    ),
                    (state["session_id"],),
                )
                for index, message in enumerate(state["messages"], start=1):
                    message_row = message_to_row(state["session_id"], index, message)
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {messages} (
                                session_id, sequence_no, role, content, question_id
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            """
                        ).format(messages=sql.Identifier(self.messages_table)),
                        (
                            message_row["session_id"],
                            message_row["sequence_no"],
                            message_row["role"],
                            message_row["content"],
                            message_row["question_id"],
                        ),
                    )
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
                            updated_at = NOW(),
                            finished_at = CASE WHEN %s = 'finished' THEN COALESCE(finished_at, NOW()) ELSE finished_at END
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
                        session_row["status"],
                        session_row["session_id"],
                    ),
                )

    @staticmethod
    def _extract_follow_up(state: InterviewState) -> str | None:
        decision = state["decision"]
        if decision and decision["action"] == "follow_up":
            return state["pending_output"]
        if state["status"] == "finished":
            return state["pending_output"]
        return None
```

- [ ] **Step 4: Run test and verify green**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_submit_answer_persists_candidate_and_followup_messages -q -m pg_runtime
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/postgres_session.py tests/test_postgres_session_store.py
git commit -m "feat: persist interview answer transitions"
```

---

### Task 5: Persist Streaming Prepare And Completion Idempotently

**Files:**
- Modify: `tests/test_postgres_session_store.py`
- Modify: `app/services/postgres_session.py`

- [ ] **Step 1: Add failing streaming persistence test**

Append to `tests/test_postgres_session_store.py`:

```python
def test_streaming_prepare_and_complete_are_persisted_once():
    dsn = require_dsn()
    table_prefix = make_table_prefix()
    store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    turn = store.start(
        make_plan(),
        job_description="Python backend role",
        resume_text="Built FastAPI services",
        job_tags=["python", "fastapi"],
    )

    prepared = store.prepare_streaming_answer(turn.session_id, "I built APIs.")
    assert prepared.stream_follow_up is True

    store.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Which failure mode did you handle?",
    )
    store.complete_streaming_answer(
        turn.session_id,
        follow_up_text="Which failure mode did you handle?",
    )

    recovered = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
    ).get(turn.session_id)

    assert [message["role"] for message in recovered["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert recovered["messages"][-1]["content"] == "Which failure mode did you handle?"
```

- [ ] **Step 2: Run test and verify red**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_streaming_prepare_and_complete_are_persisted_once -q -m pg_runtime
```

Expected: FAIL because inherited streaming methods do not persist to PostgreSQL.

- [ ] **Step 3: Implement persisted streaming methods**

Add imports:

```python
from app.services.session import PreparedInterviewTurn
```

Add methods:

```python
    def prepare_streaming_answer(self, session_id: str, answer: str) -> PreparedInterviewTurn:
        if not answer or not answer.strip():
            raise ValueError("answer is required")
        state = self.get(session_id)
        prepared_state = self._runner.prepare_answer(state, answer)
        self._replace_state(prepared_state)
        decision = prepared_state["decision"]
        should_stream = bool(decision and decision["action"] == "follow_up")
        return PreparedInterviewTurn(
            state=prepared_state,
            stream_follow_up=should_stream,
        )

    def complete_streaming_answer(
        self,
        session_id: str,
        *,
        follow_up_text: str | None = None,
    ) -> InterviewState:
        prepared_state = self.get(session_id)
        if self._already_completed_streaming_followup(prepared_state, follow_up_text):
            return prepared_state
        finalized_state = self._runner.finalize_prepared_answer(
            prepared_state,
            follow_up=follow_up_text,
        )
        self._replace_state(finalized_state)
        return finalized_state

    @staticmethod
    def _already_completed_streaming_followup(
        state: InterviewState,
        follow_up_text: str | None,
    ) -> bool:
        if not follow_up_text or not state["messages"]:
            return False
        last = state["messages"][-1]
        return last["role"] == "interviewer" and last["content"] == follow_up_text
```

- [ ] **Step 4: Run test and verify green**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_streaming_prepare_and_complete_are_persisted_once -q -m pg_runtime
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/postgres_session.py tests/test_postgres_session_store.py
git commit -m "feat: persist streaming interview transitions"
```

---

### Task 6: Persist Report Lifecycle

**Files:**
- Modify: `tests/test_postgres_session_store.py`
- Modify: `app/services/postgres_session.py`

- [ ] **Step 1: Add failing report lifecycle test**

Append to `tests/test_postgres_session_store.py`:

```python
from app.services.report import ReportProgress


def finish_session(store, session_id):
    store.submit_answer(session_id, "First answer.")
    store.submit_answer(session_id, "Second answer.")


def test_report_lifecycle_survives_store_reinstantiation():
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

    assert store.mark_report_processing(turn.session_id) is True
    store.update_report_progress(
        turn.session_id,
        ReportProgress(
            stage="analyzing",
            percent=60,
            message="Analyzing answers.",
            current_question_id="q1",
        ),
    )

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    record = recovered_store.get_report_record(turn.session_id)

    assert record is not None
    assert record.status == "processing"
    assert record.progress is not None
    assert record.progress.percent == 60

    recovered_store.fail_report(turn.session_id, "retrieval unavailable")
    failed = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
    ).get_report_record(turn.session_id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "retrieval unavailable"
```

- [ ] **Step 2: Run test and verify red**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_report_lifecycle_survives_store_reinstantiation -q -m pg_runtime
```

Expected: FAIL because inherited report methods use memory.

- [ ] **Step 3: Implement report persistence**

Add imports:

```python
from app.services.report import InterviewReport, ReportProgress, ReportRecord
from app.services.session_serialization import report_record_from_row, report_record_to_row
```

Add methods:

```python
    def mark_report_processing(self, session_id: str) -> bool:
        state = self.get(session_id)
        if state["status"] != "finished":
            raise ValueError("interview is not finished")
        if self.get_report_record(session_id) is not None:
            return False
        record = ReportRecord(
            status="processing",
            progress=ReportProgress(
                stage="retrieving",
                percent=20,
                message="Retrieving role-specific knowledge references.",
            ),
        )
        self._upsert_report_record(session_id, record)
        return True

    def update_report_progress(
        self,
        session_id: str,
        progress: ReportProgress,
    ) -> None:
        record = self.get_report_record(session_id)
        if record is None:
            raise ValueError("report record not found")
        if record.status != "processing":
            raise ValueError("report is not processing")
        self._upsert_report_record(
            session_id,
            ReportRecord(status="processing", progress=progress),
        )

    def save_report(self, session_id: str, report: InterviewReport) -> None:
        self.get(session_id)
        self._upsert_report_record(
            session_id,
            ReportRecord(status="completed", report=report),
        )

    def fail_report(self, session_id: str, error: str) -> None:
        self.get(session_id)
        self._upsert_report_record(
            session_id,
            ReportRecord(status="failed", error=error),
        )

    def get_report_record(self, session_id: str) -> ReportRecord | None:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT status, progress_json, report_json, error
                        FROM {reports}
                        WHERE session_id = %s
                        """
                    ).format(reports=sql.Identifier(self.reports_table)),
                    (session_id,),
                )
                row = cursor.fetchone()
        if row is None:
            self.get(session_id)
            return None
        return report_record_from_row(
            {
                "status": row[0],
                "progress_json": row[1],
                "report_json": row[2],
                "error": row[3],
            }
        )

    def _upsert_report_record(self, session_id: str, record: ReportRecord) -> None:
        psycopg2, sql = self._import_psycopg2()
        row = report_record_to_row(record)
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {reports} (
                            session_id, status, progress_json, report_json, error,
                            completed_at, failed_at
                        )
                        VALUES (
                            %s, %s, %s::jsonb, %s::jsonb, %s,
                            CASE WHEN %s = 'completed' THEN NOW() ELSE NULL END,
                            CASE WHEN %s = 'failed' THEN NOW() ELSE NULL END
                        )
                        ON CONFLICT (session_id) DO UPDATE
                        SET status = EXCLUDED.status,
                            progress_json = EXCLUDED.progress_json,
                            report_json = EXCLUDED.report_json,
                            error = EXCLUDED.error,
                            updated_at = NOW(),
                            completed_at = CASE WHEN EXCLUDED.status = 'completed' THEN NOW() ELSE {reports}.completed_at END,
                            failed_at = CASE WHEN EXCLUDED.status = 'failed' THEN NOW() ELSE {reports}.failed_at END
                        """
                    ).format(reports=sql.Identifier(self.reports_table)),
                    (
                        session_id,
                        row["status"],
                        json.dumps(row["progress_json"], ensure_ascii=False)
                        if row["progress_json"] is not None
                        else None,
                        json.dumps(row["report_json"], ensure_ascii=False)
                        if row["report_json"] is not None
                        else None,
                        row["error"],
                        row["status"],
                        row["status"],
                    ),
                )
```

- [ ] **Step 4: Run test and verify green**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py::test_report_lifecycle_survives_store_reinstantiation -q -m pg_runtime
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/postgres_session.py tests/test_postgres_session_store.py
git commit -m "feat: persist interview report lifecycle"
```

---

### Task 7: Add Runtime Provider And Route Wiring

**Files:**
- Create: `tests/test_runtime_provider.py`
- Create: `app/services/runtime.py`
- Modify: `app/api/routes.py`

- [ ] **Step 1: Write failing runtime provider tests**

Create `tests/test_runtime_provider.py`:

```python
from app.services.runtime import build_session_store
from app.services.session import InterviewSessionStore


def test_build_session_store_defaults_to_memory(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.delenv("INTERVIEW_RUNTIME_STORE", raising=False)

    store = build_session_store()

    assert isinstance(store, InterviewSessionStore)


def test_build_session_store_uses_postgres_when_enabled(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_STORE", "postgres")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:pass@localhost/db")

    created = {}

    class FakePostgresStore:
        def __init__(self, *, dsn, table_prefix="interview", llm=None):
            created["dsn"] = dsn
            created["table_prefix"] = table_prefix
            created["llm"] = llm

    monkeypatch.setattr(
        "app.services.runtime.PostgresInterviewSessionStore",
        FakePostgresStore,
    )

    store = build_session_store()

    assert isinstance(store, FakePostgresStore)
    assert created["dsn"] == "postgresql://user:pass@localhost/db"
    assert created["table_prefix"] == "interview"
```

- [ ] **Step 2: Run test and verify red**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.runtime'`.

- [ ] **Step 3: Implement runtime provider**

Create `app/services/runtime.py`:

```python
import os

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.session import InterviewSessionStore


_session_store = None


def build_session_store(llm=None):
    store_kind = os.getenv("INTERVIEW_RUNTIME_STORE", "memory").strip().lower()
    if store_kind == "postgres":
        dsn = os.getenv("POSTGRES_DSN")
        if not dsn:
            raise RuntimeError("POSTGRES_DSN is required when INTERVIEW_RUNTIME_STORE=postgres")
        return PostgresInterviewSessionStore(
            dsn=dsn,
            table_prefix=os.getenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "interview"),
            llm=llm,
        )
    if store_kind != "memory":
        raise RuntimeError(f"unsupported INTERVIEW_RUNTIME_STORE: {store_kind}")
    return InterviewSessionStore(llm=llm)


def get_session_store():
    global _session_store
    if _session_store is None:
        _session_store = build_session_store()
    return _session_store


def reset_runtime_for_tests() -> None:
    global _session_store
    _session_store = None
```

- [ ] **Step 4: Update route wiring**

Modify `app/api/routes.py`:

Remove:

```python
from app.services.session import InterviewSessionStore
```

Replace it with:

```python
from app.services.runtime import get_session_store
from app.services.session import InterviewSessionStore
```

Remove:

```python
session_store = InterviewSessionStore()


def get_session_store() -> InterviewSessionStore:
    return session_store
```

Keep the dependency signatures using `InterviewSessionStore`.

- [ ] **Step 5: Run runtime provider and API tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_runtime_provider.py tests/test_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/services/runtime.py app/api/routes.py tests/test_runtime_provider.py
git commit -m "feat: wire runtime session store provider"
```

---

### Task 8: Full Regression And PostgreSQL Runtime Verification

**Files:**
- No code files expected unless tests reveal defects.

- [ ] **Step 1: Run focused non-database tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_session_serialization.py tests/test_runtime_provider.py tests/test_api.py tests/test_session_service.py -q
```

Expected: PASS.

- [ ] **Step 2: Run PostgreSQL runtime tests**

Run:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest tests/test_postgres_session_store.py -q -m pg_runtime
```

Expected: PASS when PostgreSQL is reachable. If `POSTGRES_DSN` is absent, tests should skip.

- [ ] **Step 3: Run full suite**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected: PASS, with PostgreSQL tests skipped unless environment is configured.

- [ ] **Step 4: Manual runtime smoke test in memory mode**

Run:

```powershell
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

Expected:

- Start interview works.
- Submit answer works.
- Existing frontend behavior remains unchanged.

- [ ] **Step 5: Manual runtime smoke test in PostgreSQL mode**

Run:

```powershell
$env:INTERVIEW_RUNTIME_STORE = "postgres"
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8003
```

Open:

```text
http://127.0.0.1:8003/
```

Expected:

- Start interview works.
- Submit answer works.
- Restart the server.
- Continue using the same `session_id` through API calls and confirm state still exists.

- [ ] **Step 6: Commit final verification fixes if needed**

Only if the previous steps required small fixes:

```powershell
git add app tests pytest.ini
git commit -m "test: verify postgres runtime persistence"
```

---

## Self-Review

Spec coverage:

- Runtime persistence tables are covered by Task 2.
- Session recovery is covered by Task 3.
- Answer transition persistence is covered by Task 4.
- Streaming prepare/complete persistence and idempotency are covered by Task 5.
- Report lifecycle persistence is covered by Task 6.
- Runtime wiring is covered by Task 7.
- Full regression and manual verification are covered by Task 8.

Placeholder scan:

- The only angle-bracket values are command examples for local credentials. They are intentionally not hardcoded because secrets and database credentials must not be committed.
- No implementation step relies on undefined helper behavior without providing code.

Type consistency:

- Store method names match the current `InterviewSessionStore` public surface.
- Serialization functions use existing model names from `app.graphs.interview_state`, `app.services.prep`, and `app.services.report`.
- PostgreSQL store subclasses the current memory store to reuse `_runner`, `_llm`, `stream_followup()`, and `_to_turn()`.
