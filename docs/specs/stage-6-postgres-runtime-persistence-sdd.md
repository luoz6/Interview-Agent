# Stage 6: PostgreSQL Runtime Persistence SDD

## 1. Goal

Stage 6 moves the interview runtime from process-local memory to PostgreSQL while keeping the current API and frontend behavior stable.

The current system already has interview planning, graph-driven answer handling, streaming follow-ups, asynchronous report generation, RAG-backed expert evaluation, and report progress polling. The main remaining structural risk is that session state and report records live in memory. A process restart loses active interviews, completed transcripts, report progress, and final reports.

The goal of this stage is to make the existing runtime recoverable:

- Persist interview sessions, message history, report progress, final reports, and report failures in PostgreSQL.
- Reconstruct the current `InterviewState` from database rows without changing `InterviewGraphRunner`.
- Keep `knowledge_chunks` in the same PostgreSQL instance and continue using `pgvector` for RAG.
- Preserve existing API routes and frontend request contracts.
- Use SDD for design and TDD red-green-refactor for implementation.

## 2. Scope

### 2.1 Included

- Add PostgreSQL-backed runtime storage for interview sessions.
- Add PostgreSQL-backed runtime storage for interview messages.
- Add PostgreSQL-backed runtime storage for report records.
- Add schema initialization for runtime tables.
- Add state serialization and hydration between Pydantic/TyperDict models and database rows.
- Update runtime wiring so routes and background tasks can use the persistent store.
- Keep the existing in-memory store for fast unit tests and fallback development.
- Add focused tests for persistence, recovery, report state, and streaming completion consistency.

### 2.2 Explicitly Excluded

- No frontend history page.
- No user accounts, tenants, auth, or access control.
- No Celery, Redis, or external job queue.
- No WebSocket migration.
- No full LangGraph streaming rewrite.
- No Alembic migration framework in this stage.
- No automatic background task recovery loop on process start.
- No change to the report schema fields unless required for serialization.

## 3. Current Code Context

The current runtime is centered around these modules:

- `app/api/routes.py`
  - Holds the global `session_store`.
  - Exposes `POST /api/interviews`, `POST /api/interviews/{session_id}/answer`, `POST /api/interviews/{session_id}/answer/stream`, and `GET /api/interviews/{session_id}/report`.

- `app/services/session.py`
  - `InterviewSessionStore` keeps `_sessions` and `_reports` in dictionaries.
  - It orchestrates graph runner calls and report record updates.

- `app/graphs/interview_graph.py`
  - Pure state-machine logic for answer submission, follow-up decisions, and question advancement.
  - Should remain database-agnostic.

- `app/graphs/interview_state.py`
  - Defines the `InterviewState` shape that must be persisted and hydrated.

- `app/services/report.py`
  - Defines `ReportProgress`, `ReportRecord`, and final report models.

- `app/services/report_tasks.py`
  - Reads finished state, runs `ExpertShadowEvaluator`, and saves report state through the store.

- `app/services/vector_store.py`
  - Already uses PostgreSQL and `pgvector` for knowledge chunks.

Stage 6 should avoid broad refactors in these modules. The main change is replacing in-memory state storage with a PostgreSQL-backed implementation that presents the same behavioral surface.

## 4. Architecture

### 4.1 Recommended Approach

Use a thin persistent adapter:

- Keep the current `InterviewSessionStore` behavior as the contract.
- Add `PostgresInterviewSessionStore` with the same public methods.
- Keep `InterviewGraphRunner` pure and reuse it for state transitions.
- Keep `ExpertShadowEvaluator` and report tasks unchanged except for store construction/wiring.
- Add a lightweight runtime factory that chooses memory or PostgreSQL based on environment configuration.

This minimizes implementation risk while creating a path to cleaner repository separation later.

### 4.2 Runtime Wiring

Add a small runtime module:

```text
app/services/runtime.py
```

Responsibilities:

- Build or reuse the LLM service.
- Build or reuse the knowledge store.
- Build either:
  - `InterviewSessionStore` for in-memory mode.
  - `PostgresInterviewSessionStore` when `POSTGRES_DSN` is configured and runtime persistence is enabled.
- Expose `get_session_store()` for routes.

The API layer should depend on this runtime provider instead of creating the store directly in `routes.py`.

### 4.3 Store Contract

The persistent store must support the same public methods used by routes and tasks:

```python
start(plan, *, job_description, resume_text, job_tags) -> InterviewTurn
get(session_id) -> InterviewState
submit_answer(session_id, answer) -> InterviewTurn
prepare_streaming_answer(session_id, answer) -> PreparedInterviewTurn
stream_followup(session_id) -> Iterator[str]
complete_streaming_answer(session_id, *, follow_up_text=None) -> InterviewState
mark_report_processing(session_id) -> bool
update_report_progress(session_id, progress) -> None
save_report(session_id, report) -> None
fail_report(session_id, error) -> None
get_report_record(session_id) -> ReportRecord | None
```

The API should not need to know which implementation is active.

## 5. Database Schema

Stage 6 uses the same PostgreSQL instance as `knowledge_chunks`.

### 5.1 `interview_sessions`

Stores session-level state.

```sql
CREATE TABLE IF NOT EXISTS interview_sessions (
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
);
```

### 5.2 `interview_messages`

Stores ordered transcript messages.

```sql
CREATE TABLE IF NOT EXISTS interview_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES interview_sessions(session_id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('interviewer', 'candidate')),
    content TEXT NOT NULL,
    question_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, sequence_no)
);
```

Indexes:

```sql
CREATE INDEX IF NOT EXISTS interview_messages_session_idx
ON interview_messages (session_id, sequence_no);
```

### 5.3 `interview_reports`

Stores report lifecycle state.

```sql
CREATE TABLE IF NOT EXISTS interview_reports (
    session_id TEXT PRIMARY KEY REFERENCES interview_sessions(session_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    progress_json JSONB,
    report_json JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ
);
```

Validity rules enforced in application code:

- `processing`: `progress_json` must exist, `report_json` and `error` must be empty.
- `completed`: `report_json` must exist.
- `failed`: `error` must exist.

## 6. State Hydration

### 6.1 Session Hydration

`PostgresInterviewSessionStore.get(session_id)` reconstructs `InterviewState` from:

- `interview_sessions`
- `interview_messages ORDER BY sequence_no`

Mapping:

```text
InterviewState.session_id      <- interview_sessions.session_id
InterviewState.plan            <- InterviewPlan.model_validate(plan_json)
InterviewState.current_index   <- current_index
InterviewState.messages        <- interview_messages rows
InterviewState.decision        <- decision_json
InterviewState.pending_output  <- pending_output
InterviewState.status          <- status
InterviewState.job_description <- job_description
InterviewState.resume_text     <- resume_text
InterviewState.job_tags        <- job_tags
```

### 6.2 Report Hydration

`get_report_record(session_id)` reconstructs `ReportRecord`:

- `progress_json` -> `ReportProgress.model_validate(...)`
- `report_json` -> `InterviewReport.model_validate(...)`
- `error` -> string

If no row exists, return `None`, matching the current in-memory behavior.

## 7. Write Semantics

### 7.1 `start()`

In one transaction:

- Generate `session_id`.
- Build initial state through `InterviewGraphRunner.start(...)`.
- Insert `interview_sessions`.
- Insert initial interviewer message with `sequence_no = 1`.
- Return `InterviewTurn`.

### 7.2 `submit_answer()`

In one transaction:

- Lock the session row with `SELECT ... FOR UPDATE`.
- Hydrate current state.
- Run `InterviewGraphRunner.submit_answer(state, answer)`.
- Append only newly added messages.
- Update session fields:
  - `current_index`
  - `status`
  - `decision_json`
  - `pending_output`
  - `updated_at`
  - `finished_at` when status becomes finished
- Return `InterviewTurn`.

### 7.3 `prepare_streaming_answer()`

In one transaction:

- Lock the session row.
- Hydrate current state.
- Run `InterviewGraphRunner.prepare_answer(state, answer)`.
- Append the new candidate message.
- Update decision fields.
- Commit before model streaming starts.

This keeps long model streaming outside the database transaction.

### 7.4 `complete_streaming_answer()`

In one transaction:

- Lock the session row.
- Hydrate prepared state.
- If `follow_up_text` is supplied, update the decision follow-up.
- Run `InterviewGraphRunner.finalize_prepared_answer(...)`.
- Append only the new interviewer message.
- Update session fields.
- Commit.

This method must avoid duplicate interviewer messages if the same completion is retried. The minimum acceptable rule for Stage 6 is:

- If the last persisted interviewer message for the same `question_id` already has the same `content`, do not append it again.

### 7.5 Report Writes

`mark_report_processing()`:

- Validate the session exists and is finished.
- Insert a processing row if no report row exists.
- Return `False` if a report row already exists.

`update_report_progress()`:

- Require an existing processing row.
- Update `progress_json` and `updated_at`.

`save_report()`:

- Upsert status `completed`, set `report_json`, clear `error`, set `completed_at`.

`fail_report()`:

- Upsert status `failed`, set `error`, clear `report_json`, set `failed_at`.

## 8. Failure And Recovery Rules

### 8.1 Process Restart

After restart:

- Active sessions can continue receiving answers if the client still has `session_id`.
- Finished sessions can still report `404 interview is not finished` or report states correctly based on DB state.
- Completed reports remain available.
- Failed reports remain failed.

### 8.2 Stale Processing Reports

Stage 6 does not implement automatic retry. If a report remains `processing` after a restart, `GET /report` may continue returning `202` until a later stage adds task recovery.

To avoid silent indefinite hangs, the implementation may expose a clear error for processing records older than a configured threshold. This is optional in Stage 6 and should not block the core persistence work.

### 8.3 Database Unavailable

If PostgreSQL runtime persistence is enabled and the database is unavailable:

- Startup or first request should fail clearly with a database configuration/runtime error.
- The app should not silently downgrade to memory mode after a persistent store has been requested.

For local development without persistence, memory mode remains available.

## 9. Testing Strategy

Stage 6 must be implemented with TDD.

### 9.1 Unit Tests

Use fake connection/store boundaries where possible for:

- Serialization of `InterviewState`.
- Hydration of `InterviewState`.
- Serialization of `ReportRecord`.
- Idempotent append logic for streaming completion.

### 9.2 Integration Tests

PostgreSQL integration tests should be marked, for example:

```python
pytestmark = pytest.mark.pg_runtime
```

They should run only when `POSTGRES_DSN` is configured.

Required integration cases:

- Start session persists session and first interviewer message.
- A new store instance can recover the session.
- Submit answer persists candidate answer and interviewer follow-up or next question.
- Streaming prepare and complete persist exactly one candidate answer and one final interviewer message.
- Report processing/progress/completed/failed lifecycle survives a new store instance.

### 9.3 API Tests

Existing API tests should continue to pass with memory store injection.

Add focused tests for runtime provider behavior:

- Memory mode returns `InterviewSessionStore`.
- PostgreSQL mode returns `PostgresInterviewSessionStore` when enabled.

### 9.4 Regression Tests

The full suite should continue to pass:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

PostgreSQL runtime tests:

```powershell
$env:POSTGRES_DSN = "postgresql://<user>:<pass>@<host>:<port>/<db>"
& 'F:\python3.11\python.exe' -m pytest -q -m pg_runtime
```

## 10. Implementation Order

Implementation must follow red-green-refactor:

1. Add failing tests for state/report serialization.
2. Implement serialization helpers.
3. Add failing tests for PostgreSQL store schema initialization.
4. Implement schema creation.
5. Add failing tests for session start/recovery.
6. Implement persistent session start/get.
7. Add failing tests for answer submission persistence.
8. Implement persistent submit.
9. Add failing tests for streaming prepare/complete idempotency.
10. Implement persistent streaming path.
11. Add failing tests for report lifecycle recovery.
12. Implement persistent report lifecycle.
13. Add failing tests for runtime wiring.
14. Implement runtime provider and route wiring.
15. Run focused tests, then full suite.

## 11. Acceptance Criteria

Stage 6 is complete when:

- Runtime tables can be initialized in PostgreSQL.
- A session started through `PostgresInterviewSessionStore` survives store re-instantiation.
- Message order is stable and recoverable.
- Report lifecycle state survives store re-instantiation.
- Existing API and frontend contracts are unchanged.
- Existing memory-backed tests still pass.
- PostgreSQL runtime tests pass when `POSTGRES_DSN` is configured.
- No secrets are committed to the repository.

## 12. Risks

- PostgreSQL integration tests add environment dependency. Mitigation: mark them and skip unless `POSTGRES_DSN` exists.
- JSON serialization can drift from Pydantic model shapes. Mitigation: use `model_dump(mode="json")` and `model_validate`.
- Streaming completion can duplicate messages on retry. Mitigation: explicitly test idempotency for repeated completion.
- Store grows too large. Mitigation: keep this stage thin and defer repository splitting until persistence behavior is stable.
