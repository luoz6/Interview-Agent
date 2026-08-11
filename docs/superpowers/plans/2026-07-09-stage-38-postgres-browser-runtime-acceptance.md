# Stage 38 Postgres Browser Runtime Acceptance Implementation Plan

> Historical implementation plan. Its `--write-json`/`tmp/*.json` commands and local cleanup design are retired. Use `python -m scripts.stage38_postgres_runtime_acceptance --execute ... --output reports/acceptance/stage38-postgres-runtime-evidence-v1.json` with explicit PostgreSQL scope approval and Evidence signing configuration. Current behavior is defined by the production script and `docs/stage-21-browser-e2e-acceptance.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the Stage 37 versioned runtime contract against a real PostgreSQL store and record the Local V1 browser/API acceptance state.

**Architecture:** Keep Local V1 on HTTP plus SSE/polling. Add a deterministic Postgres acceptance script and DSN-gated pytest coverage so the runtime contract can be verified without calling an external LLM. Update the existing acceptance record with Stage 38 results, explicitly separating automated API/Postgres verification from manual GUI-browser verification.

**Tech Stack:** FastAPI TestClient, PostgreSQL/psycopg2, pytest markers, static ES modules, existing in-repo session/report services.

---

## Current State And Constraints

- Stage 37 is committed. The latest relevant commits are:
  - `8cbefe7 feat: add orchestrator phase contract`
  - `e0ec40e feat: add versioned session commands`
  - `06a505f feat: persist versioned session metadata`
  - `cec4dc4 feat: expose versioned command api contract`
  - `518aa58 docs: describe postgres runtime contract cleanup`
  - `a61c911 fix: preserve session transition helper imports`
- Full pytest after Stage 37 passed with `393 passed, 29 skipped, 1 warning`.
- `tests/test_postgres_session_store.py` skipped without `POSTGRES_DSN`; Stage 38 must run those tests against a real local PostgreSQL DSN.
- `docs/stage-21-browser-e2e-acceptance.md` currently says manual GUI browser acceptance remains blocked. Stage 38 may keep that status if no GUI browser automation is available, but must record API/Postgres runtime acceptance results.
- Existing dirty files such as `.idea/misc.xml`, `tests/test_report_tasks.py`, `.claude/`, and old untracked plan/spec drafts are not part of Stage 38. Do not revert or stage them.
- Use an isolated `INTERVIEW_RUNTIME_TABLE_PREFIX` for every real PostgreSQL acceptance run so the default `interview_*` tables are not mutated by smoke tests.

## File Structure

- Create `tests/stage38_fakes.py`: shared deterministic plan/report/fake LLM fixtures for Stage 38 script and tests.
- Create `scripts/stage38_postgres_runtime_acceptance.py`: deterministic real-Postgres acceptance runner that verifies Stage 37 contracts, writes optional JSON evidence, and drops isolated tables by default.
- Create `tests/test_stage38_postgres_api_contract.py`: DSN-gated FastAPI/TestClient coverage using a real `PostgresInterviewSessionStore`, a matching `PostgresReportJobStore`, and shared fake LLM.
- Modify `tests/test_local_v1_docs.py`: assert Stage 38 docs and acceptance evidence are discoverable.
- Modify `docs/stage-21-browser-e2e-acceptance.md`: append Stage 38 acceptance notes and automated verification table.
- Do not commit generated evidence JSON. Use `tmp/stage-38-postgres-runtime-acceptance.json` as a disposable run artifact and summarize the result in the acceptance markdown.

---

### Task 1: Shared Stage 38 Fixtures And Deterministic Postgres Acceptance Script

**Files:**
- Create: `tests/stage38_fakes.py`
- Create: `scripts/stage38_postgres_runtime_acceptance.py`
- No test file in this task; the script is verified by running it against a real `POSTGRES_DSN`.

- [ ] **Step 1: Create shared deterministic Stage 38 fixtures**

Create `tests/stage38_fakes.py`:

```python
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport


class FakeStage38InterviewLLM:
    def generate_plan(self, job_description: str, resume_text: str):
        return make_stage38_plan()

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        return "Please explain how the cache failure path protects PostgreSQL."

    def stream_followup(self, context: list[dict[str, str]]):
        yield "Please explain how "
        yield "the cache failure path protects PostgreSQL."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        return make_stage38_report(session_id)


def make_stage38_plan() -> InterviewPlan:
    return InterviewPlan(
        title="Stage 38 backend interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="project",
                prompt="Describe your backend project.",
                focus="backend project",
            ),
            InterviewQuestion(
                id="q2",
                kind="technical",
                prompt="Explain Redis cache invalidation.",
                focus="redis",
            ),
        ],
    )


def make_stage38_scores(score: int = 82) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_stage38_report(session_id: str) -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=82,
        overall_dimension_scores=make_stage38_scores(),
        summary="Stage 38 deterministic report.",
        highlights=["Explained backend context."],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Describe your backend project.",
                user_answer="I built a FastAPI API with Redis.",
                score=82,
                dimension_scores=make_stage38_scores(),
                rationale="The answer covered API and cache behavior.",
                critique="More production incident detail would help.",
                better_answer="Mention traffic, latency, failure handling, and data recovery.",
                references=[],
            )
        ],
    )
```

- [ ] **Step 2: Create the acceptance script**

Create `scripts/stage38_postgres_runtime_acceptance.py`:

```python
import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.session_errors import SessionVersionConflict
from tests.stage38_fakes import (
    FakeStage38InterviewLLM,
    make_stage38_plan,
    make_stage38_report,
)


DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/interview"


@dataclass(frozen=True)
class AcceptanceCheck:
    name: str
    status: str
    detail: str


def make_store(dsn: str, table_prefix: str) -> PostgresInterviewSessionStore:
    return PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
        llm=FakeStage38InterviewLLM(),
    )


def start_session(store: PostgresInterviewSessionStore):
    return store.start(
        make_stage38_plan(),
        job_description="Backend role using FastAPI, Redis, and PostgreSQL.",
        resume_text="Built FastAPI services with Redis cache-aside and PostgreSQL.",
        job_tags=["python", "fastapi", "redis", "postgresql"],
    )


def count_messages(snapshot: dict, role: str) -> int:
    return len([message for message in snapshot["messages"] if message["role"] == role])


def run_acceptance(*, dsn: str, table_prefix: str) -> dict:
    checks: list[AcceptanceCheck] = []
    store = make_store(dsn, table_prefix)
    tables = store.list_runtime_tables()
    checks.append(
        AcceptanceCheck(
            name="schema_initializes_isolated_tables",
            status="pass",
            detail=",".join(tables),
        )
    )

    stale_session = start_session(store)
    try:
        store.submit_answer(
            stale_session.session_id,
            "I used Redis cache-aside.",
            expected_version=0,
            command_id="cmd-stale",
        )
        raise AssertionError("stale command unexpectedly succeeded")
    except SessionVersionConflict as exc:
        assert exc.expected_version == 0
        assert exc.actual_version == 1
    checks.append(
        AcceptanceCheck(
            name="stale_expected_version_rejected",
            status="pass",
            detail="expected=0 actual=1",
        )
    )

    duplicate_session = start_session(store)
    first_turn = store.submit_answer(
        duplicate_session.session_id,
        "I built a FastAPI API with Redis.",
        expected_version=1,
        command_id="cmd-answer",
    )
    duplicate_turn = store.submit_answer(
        duplicate_session.session_id,
        "I built a FastAPI API with Redis.",
        expected_version=1,
        command_id="cmd-answer",
    )
    duplicate_snapshot = store.snapshot(duplicate_session.session_id)
    assert duplicate_turn.follow_up == first_turn.follow_up
    assert duplicate_snapshot["state_version"] == 2
    assert duplicate_snapshot["checkpoint_version"] == 2
    assert duplicate_snapshot["last_command_id"] == "cmd-answer"
    assert count_messages(duplicate_snapshot, "candidate") == 1
    checks.append(
        AcceptanceCheck(
            name="duplicate_command_id_is_idempotent",
            status="pass",
            detail="state_version=2 candidate_messages=1",
        )
    )

    stream_session = start_session(store)
    prepared = store.prepare_streaming_answer(
        stream_session.session_id,
        "I protected PostgreSQL during cache misses.",
        expected_version=1,
        command_id="cmd-stream",
    )
    assert prepared.stream_follow_up is True
    finalized = store.complete_streaming_answer(
        stream_session.session_id,
        follow_up_text="Please explain cache miss protection.",
        expected_version=2,
        command_id="cmd-stream",
    )
    duplicate_finalized = store.complete_streaming_answer(
        stream_session.session_id,
        follow_up_text="Please explain cache miss protection.",
        expected_version=2,
        command_id="cmd-stream",
    )
    stream_snapshot = store.snapshot(stream_session.session_id)
    assert duplicate_finalized == finalized
    assert stream_snapshot["state_version"] == 3
    assert stream_snapshot["checkpoint_version"] == 3
    assert stream_snapshot["last_command_id"] == "cmd-stream"
    assert count_messages(stream_snapshot, "candidate") == 1
    checks.append(
        AcceptanceCheck(
            name="stream_completion_advances_version_once",
            status="pass",
            detail="state_version=3 last_command_id=cmd-stream",
        )
    )

    report_session = start_session(store)
    store.finish(
        report_session.session_id,
        expected_version=1,
        command_id="cmd-finish",
    )
    assert store.mark_report_processing(report_session.session_id) is True
    processing_snapshot = store.snapshot(report_session.session_id)
    assert processing_snapshot["phase"] == "review"
    assert processing_snapshot["phase_status"] == "active"
    assert processing_snapshot["review_status"] == "processing"
    assert processing_snapshot["state_version"] == 3
    assert processing_snapshot["last_command_id"] == "cmd-finish"
    store.save_report(
        report_session.session_id,
        make_stage38_report(report_session.session_id),
    )
    completed_snapshot = store.snapshot(report_session.session_id)
    assert completed_snapshot["phase_status"] == "completed"
    assert completed_snapshot["review_status"] == "completed"
    assert completed_snapshot["state_version"] == 4
    assert completed_snapshot["last_command_id"] == "cmd-finish"
    checks.append(
        AcceptanceCheck(
            name="report_lifecycle_preserves_user_command_id",
            status="pass",
            detail="processing_version=3 completed_version=4 last_command_id=cmd-finish",
        )
    )

    recovered = make_store(dsn, table_prefix)
    recovered_snapshot = recovered.snapshot(report_session.session_id)
    recovered_record = recovered.get_report_record(report_session.session_id)
    assert recovered_snapshot["state_version"] == 4
    assert recovered_snapshot["last_command_id"] == "cmd-finish"
    assert recovered_record is not None
    assert recovered_record.status == "completed"
    checks.append(
        AcceptanceCheck(
            name="postgres_reinstantiation_preserves_state",
            status="pass",
            detail=f"session_id={report_session.session_id}",
        )
    )

    return {
        "stage": "Stage 38 Postgres Runtime Acceptance",
        "status": "pass",
        "dsn": dsn,
        "table_prefix": table_prefix,
        "checks": [asdict(check) for check in checks],
    }


def drop_isolated_tables(*, dsn: str, table_prefix: str) -> None:
    import psycopg2
    from psycopg2 import sql

    table_names = [
        f"{table_prefix}_question_evaluations",
        f"{table_prefix}_reports",
        f"{table_prefix}_messages",
        f"{table_prefix}_sessions",
    ]
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table_name in table_names:
                cursor.execute(
                    sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        sql.Identifier(table_name)
                    )
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dsn",
        default=os.getenv("POSTGRES_DSN", DEFAULT_DSN),
        help="PostgreSQL DSN used for the acceptance run.",
    )
    parser.add_argument(
        "--table-prefix",
        default=f"stage38_{uuid4().hex[:10]}",
        help="Isolated runtime table prefix for this run.",
    )
    parser.add_argument(
        "--write-json",
        default=None,
        help="Optional disposable output path for acceptance evidence JSON.",
    )
    parser.add_argument(
        "--keep-tables",
        action="store_true",
        help="Keep isolated stage38 tables for manual database inspection.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = run_acceptance(dsn=args.dsn, table_prefix=args.table_prefix)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        print(rendered)
        if args.write_json:
            output_path = Path(args.write_json)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
    finally:
        if not args.keep_tables:
            drop_isolated_tables(dsn=args.dsn, table_prefix=args.table_prefix)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run syntax checks before real PostgreSQL execution**

Run:

```powershell
F:\python3.11\python.exe -m py_compile scripts/stage38_postgres_runtime_acceptance.py tests/stage38_fakes.py
```

Expected: command exits 0.

- [ ] **Step 4: Run the script against real PostgreSQL**

Run:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
F:\python3.11\python.exe scripts/stage38_postgres_runtime_acceptance.py --table-prefix stage38_acceptance --write-json tmp/stage-38-postgres-runtime-acceptance.json
```

Expected: JSON output with `"status": "pass"` and these check names:

```text
schema_initializes_isolated_tables
stale_expected_version_rejected
duplicate_command_id_is_idempotent
stream_completion_advances_version_once
report_lifecycle_preserves_user_command_id
postgres_reinstantiation_preserves_state
```

The script drops `stage38_acceptance_*` tables in a `finally` block unless `--keep-tables` is supplied. The JSON evidence file under `tmp/` is a disposable run artifact and must not be committed.

- [ ] **Step 5: Commit Task 1**

Only commit the script and shared fixture after the real PostgreSQL run passes:

```powershell
git add scripts/stage38_postgres_runtime_acceptance.py tests/stage38_fakes.py
git commit -m "test: add stage 38 postgres acceptance smoke"
```

---

### Task 2: Real Postgres API Contract Test

**Files:**
- Create: `tests/test_stage38_postgres_api_contract.py`

- [ ] **Step 1: Add DSN-gated API test**

Create `tests/test_stage38_postgres_api_contract.py`:

```python
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.routes as route_module
from app.main import app
from app.services.report_jobs import PostgresReportJobStore
from app.services.postgres_session import PostgresInterviewSessionStore
from app.services.runtime import reset_runtime_for_tests
from tests.stage38_fakes import FakeStage38InterviewLLM


pytestmark = pytest.mark.pg_runtime


def require_dsn() -> str:
    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        pytest.skip("POSTGRES_DSN is required for Stage 38 Postgres API tests")
    return dsn


class FakePublisher:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


@pytest.fixture
def postgres_api_client():
    reset_runtime_for_tests()
    dsn = require_dsn()
    table_prefix = "stage38_api_" + uuid4().hex[:10]
    store = PostgresInterviewSessionStore(
        dsn=dsn,
        table_prefix=table_prefix,
        llm=FakeStage38InterviewLLM(),
    )
    job_store = PostgresReportJobStore(
        dsn=dsn,
        table_prefix=table_prefix,
        lease_seconds=300,
    )
    publisher = FakePublisher()
    app.dependency_overrides[route_module.get_session_store] = lambda: store
    app.dependency_overrides[route_module.get_report_job_store] = lambda: job_store
    app.dependency_overrides[route_module.get_event_publisher] = lambda: publisher
    try:
        yield TestClient(app), store, job_store, publisher
    finally:
        app.dependency_overrides.clear()
        reset_runtime_for_tests()


def test_stage38_postgres_api_versioned_stream_contract(postgres_api_client):
    client, store, _job_store, publisher = postgres_api_client
    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role with FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built FastAPI services with Redis cache-aside.",
        },
    ).json()
    session_id = started["session_id"]

    stale = client.post(
        f"/api/interviews/{session_id}/answer",
        json={
            "answer": "I used Redis.",
            "expected_version": 0,
            "command_id": "cmd-stale",
        },
    )

    assert stale.status_code == 409
    assert stale.json()["actual_version"] == 1

    with client.stream(
        "POST",
        f"/api/interviews/{session_id}/answer/stream",
        json={
            "answer": "I protected PostgreSQL during cache misses.",
            "expected_version": 1,
            "command_id": "cmd-stream",
        },
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    snapshot = client.get(f"/api/interviews/{session_id}").json()
    recovered = PostgresInterviewSessionStore(
        dsn=store.dsn,
        table_prefix=store.table_prefix,
        llm=FakeStage38InterviewLLM(),
    ).snapshot(session_id)

    assert "event: done" in body
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-stream"
    assert recovered["state_version"] == 3
    assert recovered["last_command_id"] == "cmd-stream"
    assert len([m for m in snapshot["messages"] if m["role"] == "candidate"]) == 1
    assert publisher.events == []


def test_stage38_postgres_api_finish_preserves_command_id_through_report_processing(
    postgres_api_client,
):
    client, store, job_store, _publisher = postgres_api_client
    started = client.post(
        "/api/interviews",
        json={
            "job_description": "Backend role with FastAPI, Redis, and PostgreSQL.",
            "resume_text": "Built FastAPI services with Redis cache-aside.",
        },
    ).json()
    session_id = started["session_id"]

    finish = client.post(
        f"/api/interviews/{session_id}/finish",
        json={
            "expected_version": 1,
            "command_id": "cmd-finish",
        },
    )
    assert finish.status_code == 200

    # The finish route enqueues report processing through the existing boundary.
    snapshot = store.snapshot(session_id)
    assert snapshot["status"] == "finished"
    assert snapshot["phase"] == "review"
    assert snapshot["last_command_id"] == "cmd-finish"
    assert snapshot["state_version"] >= 2
    assert job_store.get_job_by_session(session_id) is not None
```

- [ ] **Step 2: Run without DSN to verify skip behavior**

Run:

```powershell
Remove-Item Env:POSTGRES_DSN -ErrorAction SilentlyContinue
F:\python3.11\python.exe -m pytest tests/test_stage38_postgres_api_contract.py -q
```

Expected: `2 skipped`.

- [ ] **Step 3: Run against real PostgreSQL**

Run:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
F:\python3.11\python.exe -m pytest tests/test_stage38_postgres_api_contract.py tests/test_postgres_session_store.py -q
```

Expected: PASS. The exact count should include the two Stage 38 API tests plus all DSN-gated `tests/test_postgres_session_store.py` tests.

- [ ] **Step 4: Commit Task 2**

```powershell
git add tests/test_stage38_postgres_api_contract.py
git commit -m "test: verify stage 38 postgres api contract"
```

---

### Task 3: Acceptance Documentation And Evidence Checks

**Files:**
- Modify: `tests/test_local_v1_docs.py`
- Modify: `docs/stage-21-browser-e2e-acceptance.md`

- [ ] **Step 1: Add docs test for Stage 38 acceptance**

Add this test to `tests/test_local_v1_docs.py` after `test_docs_describe_stage_37_postgres_runtime_contract_cleanup`:

```python
def test_docs_describe_stage_38_postgres_runtime_acceptance():
    record = read_text("docs/stage-21-browser-e2e-acceptance.md")

    assert "## Stage 38 Postgres Runtime Acceptance" in record
    assert "scripts/stage38_postgres_runtime_acceptance.py" in record
    assert "tmp/stage-38-postgres-runtime-acceptance.json" in record
    assert "disposable run artifact" in record
    assert "stale_expected_version_rejected" in record
    assert "duplicate_command_id_is_idempotent" in record
    assert "stream_completion_advances_version_once" in record
    assert "report_lifecycle_preserves_user_command_id" in record
    assert "manual GUI browser acceptance remains blocked" in record
```

- [ ] **Step 2: Run docs test before documentation update**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py::test_docs_describe_stage_38_postgres_runtime_acceptance -q
```

Expected: FAIL because the Stage 38 acceptance section does not exist yet.

- [ ] **Step 3: Append Stage 38 section to acceptance record**

Append this section above `## Final Status` in `docs/stage-21-browser-e2e-acceptance.md` after the Stage 25.5 notes:

```markdown
## Stage 38 Postgres Runtime Acceptance

| Item | Value |
| --- | --- |
| Execution date | 2026-07-09 |
| Runtime store | PostgreSQL with isolated Stage 38 table prefixes |
| Acceptance script | `scripts/stage38_postgres_runtime_acceptance.py` |
| Evidence JSON | `tmp/stage-38-postgres-runtime-acceptance.json` disposable run artifact; not committed |
| Browser status | manual GUI browser acceptance remains blocked in this tool session |

### Stage 38 Automated Contract Results

| Check | Result | Notes |
| --- | --- | --- |
| `schema_initializes_isolated_tables` | Pass | Isolated Postgres runtime tables were created for the Stage 38 prefix |
| `stale_expected_version_rejected` | Pass | Stale `expected_version=0` raised `SessionVersionConflict` with actual version `1` |
| `duplicate_command_id_is_idempotent` | Pass | Repeating `cmd-answer` did not append a duplicate candidate message |
| `stream_completion_advances_version_once` | Pass | Streaming prepare plus completion advanced `state_version` to `3` and preserved `last_command_id=cmd-stream` |
| `report_lifecycle_preserves_user_command_id` | Pass | Report processing/completion advanced versions without replacing `last_command_id=cmd-finish` |
| `postgres_reinstantiation_preserves_state` | Pass | A new Postgres store instance loaded the completed report state and metadata |

### Stage 38 Verification Commands

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe scripts/stage38_postgres_runtime_acceptance.py --table-prefix stage38_acceptance --write-json tmp/stage-38-postgres-runtime-acceptance.json` | Pass |
| `F:\python3.11\python.exe -m pytest tests/test_stage38_postgres_api_contract.py tests/test_postgres_session_store.py -q` | Pass with `POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/interview` |
```

- [ ] **Step 4: Run docs tests**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_local_v1_docs.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add docs/stage-21-browser-e2e-acceptance.md tests/test_local_v1_docs.py
git commit -m "docs: record stage 38 postgres acceptance"
```

---

### Task 4: Real Postgres Verification Sweep

**Files:**
- No code edits expected.

- [ ] **Step 1: Confirm PostgreSQL DSN and pgvector prerequisites**

Run:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
@'
import psycopg2
conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5432/interview")
cur = conn.cursor()
cur.execute("select current_database(), current_user")
print(cur.fetchone())
cur.execute("select extname from pg_extension where extname='vector'")
print(cur.fetchone())
cur.execute("select count(*) from knowledge_chunks")
print(cur.fetchone())
conn.close()
'@ | F:\python3.11\python.exe -
```

Expected:

```text
('interview', 'postgres')
('vector',)
(<non-negative integer>,)
```

- [ ] **Step 2: Run DSN-gated persistence suites**

Run:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
F:\python3.11\python.exe -m pytest tests/test_postgres_session_store.py tests/test_report_jobs.py tests/test_report_worker.py::test_run_one_job_completes_postgres_job_and_report -q
```

Expected: PASS. No Postgres tests should skip because `POSTGRES_DSN` is set.

- [ ] **Step 3: Run Stage 38 acceptance script again with a fresh prefix**

Run:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
F:\python3.11\python.exe scripts/stage38_postgres_runtime_acceptance.py --table-prefix stage38_verify
```

Expected: `"status": "pass"`.

- [ ] **Step 4: Confirm acceptance script cleaned isolated tables**

Run:

```powershell
@'
import psycopg2
conn = psycopg2.connect("postgresql://postgres:postgres@127.0.0.1:5432/interview")
cur = conn.cursor()
cur.execute("""
    select count(*)
    from information_schema.tables
    where table_schema = 'public'
      and table_name like 'stage38_verify_%'
""")
print(cur.fetchone())
conn.close()
'@ | F:\python3.11\python.exe -
```

Expected:

```text
(0,)
```

- [ ] **Step 5: Run focused Stage 38 plus Stage 37 regression**

Run:

```powershell
F:\python3.11\python.exe -m pytest tests/test_stage38_postgres_api_contract.py tests/test_session_service.py tests/test_api.py tests/test_runtime_boundary_api.py -q
```

Expected: PASS.

---

### Task 5: Final Verification And Worktree Audit

**Files:**
- No code edits expected unless verification exposes a defect.

- [ ] **Step 1: Run full pytest**

Run:

```powershell
F:\python3.11\python.exe -m pytest -q
```

Expected: PASS. DSN-gated tests may increase the total pass count when `POSTGRES_DSN` is set.

- [ ] **Step 2: Run static JS syntax checks**

Run each command:

```powershell
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
```

Expected: all commands exit 0.

- [ ] **Step 3: Audit git state**

Run:

```powershell
git status --short
git log --oneline -12
```

Expected:

- Stage 38 files are committed.
- Remaining dirty files are pre-existing unrelated editor files, old plan/spec drafts, or known unrelated dirty tests.
- Do not delete or revert unrelated dirty files.

---

## Self-Review

- Spec coverage: The plan covers real PostgreSQL verification, stale-version rejection, duplicate command id idempotency, streaming completion version advancement, report lifecycle `last_command_id` preservation, Postgres store reinstantiation, API-level real-Postgres smoke coverage, acceptance evidence, and final verification.
- Marker scan: No task contains open-ended markers, unspecified implementation steps, or references to missing functions. The JSON evidence file is a temporary run artifact under `tmp/` and is not committed.
- Type consistency: `expected_version`, `command_id`, `state_version`, `checkpoint_version`, `last_command_id`, `phase_status`, `SessionVersionConflict`, and `PostgresInterviewSessionStore` match the Stage 37 implementation.
