# Stage 14 Local Report Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only report center that lists historical reports, opens completed report details, and links to existing PDF downloads without introducing login or account concepts.

**Architecture:** Report lifecycle timestamps live on `ReportRecord`, not on interview sessions. `InterviewSessionStore` and `PostgresInterviewSessionStore` expose `list_reports(...)`; the API formats local report summaries and constructs `report_pdf_url` at response time. The frontend adds an in-page report center panel and reuses the existing `renderReport(report)` renderer for report details.

**Tech Stack:** FastAPI, Pydantic, in-memory session store, PostgreSQL session store, vanilla HTML/CSS/JS, pytest.

---

## File Structure

- Modify `app/services/report.py`: add report lifecycle timestamp fields to `ReportRecord`.
- Modify `app/services/session.py`: preserve `ReportRecord.created_at` across processing/progress/completed/failed transitions and add `list_reports(...)`.
- Modify `app/services/session_serialization.py`: serialize/deserialize report lifecycle timestamps.
- Modify `app/services/postgres_session.py`: read PostgreSQL report timestamps, preserve them in `_upsert_report_record(...)`, and implement `list_reports(...)` by parsing `report_json` in Python.
- Modify `app/api/routes.py`: add `GET /api/reports`, response formatting, `limit/status` query params, and runtime `report_pdf_url`.
- Modify `app/static/index.html`: add a report center button/panel on the existing page.
- Modify `app/static/app.js`: fetch report summaries, render the list, open completed reports through existing detail endpoint, and download PDFs through the existing PDF endpoint.
- Modify `app/static/styles.css`: add report center layout and responsive styles.
- Modify tests in `tests/test_report_models.py`, `tests/test_session_report_store.py`, `tests/test_session_serialization.py`, `tests/test_postgres_session_store.py`, `tests/test_report_api.py`, and `tests/test_static_report_ui.py`.
- Do not modify login/auth/user models; this stage remains local-only.

---

### Task 1: Add ReportRecord Lifecycle Timestamps

**Files:**
- Modify: `app/services/report.py`
- Modify: `app/services/session.py`
- Modify: `tests/test_report_models.py`
- Modify: `tests/test_session_report_store.py`

- [ ] **Step 1: Write failing model and memory-store timestamp tests**

Add this import to `tests/test_report_models.py`:

```python
from datetime import datetime
```

Add these tests to `tests/test_report_models.py`:

```python
def test_report_record_defaults_created_at_and_open_finished_at():
    record = ReportRecord(
        status="processing",
        progress=ReportProgress(stage="retrieving", percent=20, message="Loading"),
    )

    assert record.created_at
    assert record.finished_at is None
    datetime.fromisoformat(record.created_at.replace("Z", "+00:00"))


def test_report_record_completed_accepts_finished_at():
    record = ReportRecord(
        status="completed",
        report=InterviewReport(
            session_id="s1",
            overall_score=82,
            overall_dimension_scores=make_dimension_scores(),
            summary="Solid answer.",
            highlights=["Clear context"],
            feedbacks=[make_feedback()],
        ),
        created_at="2026-07-04T10:00:00Z",
        finished_at="2026-07-04T10:01:00Z",
    )

    assert record.created_at == "2026-07-04T10:00:00Z"
    assert record.finished_at == "2026-07-04T10:01:00Z"
```

Add this assertion to `test_mark_report_processing_is_idempotent_after_first_success()` in `tests/test_session_report_store.py`:

```python
    assert record.created_at
    assert record.finished_at is None
```

Add this assertion to `test_store_saves_completed_report_record()`:

```python
    assert record.created_at
    assert record.finished_at
```

Add this assertion to `test_store_saves_failed_report_record()`:

```python
    assert record.created_at
    assert record.finished_at
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_report_models.py tests/test_session_report_store.py -q
```

Expected: FAIL because `ReportRecord` does not expose `created_at` or `finished_at`.

- [ ] **Step 3: Add timestamp fields to ReportRecord**

In `app/services/report.py`, add:

```python
from datetime import datetime, timezone
```

Add this helper above `ReportRecord`:

```python
def utc_now_iso() -> str:
    # Kept local to report models on purpose; interview_state has the same helper
    # but importing graph-state code here would invert the dependency direction.
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
```

Add fields to `ReportRecord`:

```python
    created_at: str = Field(default_factory=utc_now_iso)
    finished_at: str | None = None
```

Keep the existing `validate_state(...)` rules unchanged; a processing record can have `finished_at is None`, while completed/failed records should be created by store methods with a `finished_at`.

- [ ] **Step 4: Preserve timestamps in memory store transitions**

In `app/services/session.py`, import `utc_now_iso` from `app.services.report`:

```python
from app.services.report import InterviewReport, ReportProgress, ReportRecord, utc_now_iso
```

Update `update_report_progress(...)` so it preserves the existing creation time:

```python
        self._reports[session_id] = ReportRecord(
            status="processing",
            progress=progress,
            created_at=record.created_at,
            finished_at=record.finished_at,
        )
```

Update `save_report(...)`:

```python
    def save_report(self, session_id: str, report: InterviewReport) -> None:
        self.get(session_id)
        existing = self._reports.get(session_id)
        created_at = existing.created_at if existing is not None else utc_now_iso()
        self._reports[session_id] = ReportRecord(
            status="completed",
            report=report,
            created_at=created_at,
            finished_at=utc_now_iso(),
        )
```

Update `fail_report(...)`:

```python
    def fail_report(self, session_id: str, error: str) -> None:
        self.get(session_id)
        existing = self._reports.get(session_id)
        created_at = existing.created_at if existing is not None else utc_now_iso()
        self._reports[session_id] = ReportRecord(
            status="failed",
            error=error,
            created_at=created_at,
            finished_at=utc_now_iso(),
        )
```

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/test_report_models.py tests/test_session_report_store.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/report.py app/services/session.py tests/test_report_models.py tests/test_session_report_store.py
git commit -m "feat: track report lifecycle timestamps"
```

---

### Task 2: Serialize Report Timestamps and Add Store Listing

**Files:**
- Modify: `app/services/session_serialization.py`
- Modify: `app/services/session.py`
- Modify: `app/services/postgres_session.py`
- Modify: `tests/test_session_serialization.py`
- Modify: `tests/test_session_report_store.py`
- Modify: `tests/test_postgres_session_store.py`

- [ ] **Step 1: Write failing serialization and memory list tests**

Add to `tests/test_session_serialization.py`:

```python
def test_report_record_round_trips_lifecycle_timestamps():
    report = make_report_record()
    record = ReportRecord(
        status="completed",
        report=report.report,
        created_at="2026-07-04T10:00:00Z",
        finished_at="2026-07-04T10:02:00Z",
    )

    row = report_record_to_row(record)
    restored = report_record_from_row(row)

    assert row["created_at"] == "2026-07-04T10:00:00Z"
    assert row["finished_at"] == "2026-07-04T10:02:00Z"
    assert restored.created_at == "2026-07-04T10:00:00Z"
    assert restored.finished_at == "2026-07-04T10:02:00Z"
```

Add to `tests/test_session_report_store.py`:

```python
def test_list_reports_returns_completed_failed_and_processing_records():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    first = start_session(store)
    second = start_session(store)
    third = start_session(store)
    finish_session(store, first.session_id)
    finish_session(store, second.session_id)
    finish_session(store, third.session_id)

    store.mark_report_processing(first.session_id)
    store.save_report(first.session_id, make_report(first.session_id))
    store.mark_report_processing(second.session_id)
    store.fail_report(second.session_id, "llm timeout")
    store.mark_report_processing(third.session_id)

    reports = store.list_reports()

    assert [item["session_id"] for item in reports] == [
        third.session_id,
        second.session_id,
        first.session_id,
    ]
    assert [item["record"].status for item in reports] == [
        "processing",
        "failed",
        "completed",
    ]


def test_list_reports_filters_status_and_limit():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    first = start_session(store)
    second = start_session(store)
    finish_session(store, first.session_id)
    finish_session(store, second.session_id)
    store.mark_report_processing(first.session_id)
    store.save_report(first.session_id, make_report(first.session_id))
    store.mark_report_processing(second.session_id)

    reports = store.list_reports(status="completed", limit=1)

    assert len(reports) == 1
    assert reports[0]["session_id"] == first.session_id
    assert reports[0]["record"].status == "completed"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_session_serialization.py tests/test_session_report_store.py -q
```

Expected: FAIL because timestamp serialization and `list_reports(...)` do not exist.

- [ ] **Step 3: Serialize report lifecycle timestamps**

In `app/services/session_serialization.py`, update `report_record_to_row(...)`:

```python
        "created_at": record.created_at,
        "finished_at": record.finished_at,
```

Update `report_record_from_row(...)`. Because `ReportRecord.created_at` has a default factory, do not pass `created_at=None`. Keep the construction explicit for readability:

```python
    if row.get("created_at"):
        return ReportRecord(
            status=row["status"],
            progress=progress,
            report=report,
            error=row.get("error"),
            created_at=row["created_at"],
            finished_at=row.get("finished_at"),
        )
    return ReportRecord(
        status=row["status"],
        progress=progress,
        report=report,
        error=row.get("error"),
        finished_at=row.get("finished_at"),
    )
```

- [ ] **Step 4: Add `list_reports(...)` to memory store**

In `app/services/session.py`, add this method to `InterviewSessionStore` after `get_report_record(...)`:

```python
    def list_reports(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for session_id, record in self._reports.items():
            if status is not None and record.status != status:
                continue
            items.append({"session_id": session_id, "record": record})
        items.sort(key=lambda item: item["record"].created_at, reverse=True)
        return items[:limit]
```

- [ ] **Step 5: Implement PostgreSQL timestamp mapping and list_reports**

First update `PostgresInterviewSessionStore.update_report_progress(...)` so it preserves the existing record lifecycle timestamps, matching the memory store:

```python
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
            ReportRecord(
                status="processing",
                progress=progress,
                created_at=record.created_at,
                finished_at=record.finished_at,
            ),
        )
```

In `app/services/postgres_session.py`, update `get_report_record(...)` SELECT:

```sql
SELECT status, progress_json, report_json, error,
       created_at, completed_at, failed_at
```

Update the row dict passed to `report_record_from_row(...)`:

```python
                "created_at": row[4].isoformat().replace("+00:00", "Z") if row[4] else None,
                "finished_at": (
                    row[5].isoformat().replace("+00:00", "Z")
                    if row[5]
                    else row[6].isoformat().replace("+00:00", "Z")
                    if row[6]
                    else None
                ),
```

Update `_upsert_report_record(...)` to preserve `record.created_at` and use `record.finished_at` instead of SQL `NOW()` for completed/failed lifecycle timestamps:

```sql
INSERT INTO {reports} (
    session_id, status, progress_json, report_json, error,
    created_at, completed_at, failed_at
)
VALUES (
    %s, %s, %s::jsonb, %s::jsonb, %s,
    %s,
    CASE WHEN %s = 'completed' THEN %s ELSE NULL END,
    CASE WHEN %s = 'failed' THEN %s ELSE NULL END
)
ON CONFLICT (session_id) DO UPDATE
SET status = EXCLUDED.status,
    progress_json = EXCLUDED.progress_json,
    report_json = EXCLUDED.report_json,
    error = EXCLUDED.error,
    updated_at = NOW(),
    completed_at = CASE
        WHEN EXCLUDED.status = 'completed' THEN EXCLUDED.completed_at
        ELSE {reports}.completed_at
    END,
    failed_at = CASE
        WHEN EXCLUDED.status = 'failed' THEN EXCLUDED.failed_at
        ELSE {reports}.failed_at
    END
```

Use named local variables before the SQL call so the repeated CASE parameters are readable:

```python
completed_finished_at = row["finished_at"] if row["status"] == "completed" else None
failed_finished_at = row["finished_at"] if row["status"] == "failed" else None
```

Then use this argument order:

```python
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
    row["created_at"],
    row["status"],
    completed_finished_at,
    row["status"],
    failed_finished_at,
)
```

Add this method to `PostgresInterviewSessionStore` after `get_report_record(...)`. It parses `report_json` in Python instead of SQL JSON-path extraction:

```python
    def list_reports(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        psycopg2, sql = self._import_psycopg2()
        with psycopg2.connect(self.dsn) as connection:
            with connection.cursor() as cursor:
                if status is None:
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT session_id, status, progress_json, report_json, error,
                                   created_at, completed_at, failed_at
                            FROM {reports}
                            ORDER BY created_at DESC
                            LIMIT %s
                            """
                        ).format(reports=sql.Identifier(self.reports_table)),
                        (limit,),
                    )
                else:
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT session_id, status, progress_json, report_json, error,
                                   created_at, completed_at, failed_at
                            FROM {reports}
                            WHERE status = %s
                            ORDER BY created_at DESC
                            LIMIT %s
                            """
                        ).format(reports=sql.Identifier(self.reports_table)),
                        (status, limit),
                    )
                rows = cursor.fetchall()
        return [
            {
                "session_id": row[0],
                "record": report_record_from_row(
                    {
                        "status": row[1],
                        "progress_json": row[2],
                        "report_json": row[3],
                        "error": row[4],
                        "created_at": row[5].isoformat().replace("+00:00", "Z") if row[5] else None,
                        "finished_at": (
                            row[6].isoformat().replace("+00:00", "Z")
                            if row[6]
                            else row[7].isoformat().replace("+00:00", "Z")
                            if row[7]
                            else None
                        ),
                    }
                ),
            }
            for row in rows
        ]
```

- [ ] **Step 6: Add PostgreSQL list test**

Add these imports to `tests/test_postgres_session_store.py` if they are not already present:

```python
from app.services.report import DimensionScores, InterviewFeedback, InterviewReport
```

Add this helper near the existing `make_plan()` helper:

```python
def make_dimension_scores(score: int = 80) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_report(session_id: str) -> InterviewReport:
    return InterviewReport(
        session_id=session_id,
        overall_score=80,
        overall_dimension_scores=make_dimension_scores(),
        summary="Solid backend project explanation.",
        highlights=["Clear project context"],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Describe your backend project.",
                user_answer="I built a FastAPI service.",
                score=80,
                dimension_scores=make_dimension_scores(),
                rationale="The answer covered project context and implementation.",
                critique="Failure modes need more detail.",
                better_answer="Explain traffic, storage, cache, failure handling, and tradeoffs.",
                references=[],
            )
        ],
    )
```

Add this to `tests/test_postgres_session_store.py`:

```python
def test_list_reports_survives_store_reinstantiation():
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
    store.mark_report_processing(turn.session_id)
    store.save_report(turn.session_id, make_report(turn.session_id))

    recovered_store = PostgresInterviewSessionStore(dsn=dsn, table_prefix=table_prefix)
    reports = recovered_store.list_reports(status="completed", limit=5)

    assert len(reports) == 1
    assert reports[0]["session_id"] == turn.session_id
    assert reports[0]["record"].status == "completed"
    assert reports[0]["record"].report.overall_score == 80
    assert reports[0]["record"].created_at
    assert reports[0]["record"].finished_at
```

- [ ] **Step 7: Run tests**

Run:

```bash
pytest tests/test_session_serialization.py tests/test_session_report_store.py tests/test_postgres_session_store.py -q
```

Expected: PASS. PostgreSQL tests may be skipped when `POSTGRES_DSN` is not set.

- [ ] **Step 8: Commit**

```bash
git add app/services/session_serialization.py app/services/session.py app/services/postgres_session.py tests/test_session_serialization.py tests/test_session_report_store.py tests/test_postgres_session_store.py
git commit -m "feat: list local report records"
```

---

### Task 3: Add GET /api/reports

**Files:**
- Modify: `app/api/routes.py`
- Modify: `tests/test_report_api.py`

- [ ] **Step 1: Write failing API tests**

Add to `tests/test_report_api.py`:

```python
def test_reports_endpoint_lists_completed_failed_and_processing_reports():
    client, store, _, _ = make_client()
    completed_session_id = start_interview(client)
    finish_session(store, completed_session_id)
    store.mark_report_processing(completed_session_id)
    store.save_report(
        completed_session_id,
        InterviewReport(
            session_id=completed_session_id,
            overall_score=81,
            overall_dimension_scores=make_dimension_scores(81),
            summary="Clear project story.",
            highlights=["Explained tradeoffs"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a backend project.",
                    user_answer="The candidate built a backend cache service.",
                    score=81,
                    dimension_scores=make_dimension_scores(81),
                    rationale="The answer covered implementation tradeoffs clearly.",
                    critique="Needs stronger business metrics.",
                    better_answer="I reduced p95 latency using cache-aside Redis.",
                    references=[],
                )
            ],
        ),
    )
    failed_session_id = start_interview(client)
    finish_session(store, failed_session_id)
    store.mark_report_processing(failed_session_id)
    store.fail_report(failed_session_id, "llm timeout")
    processing_session_id = start_interview(client)
    finish_session(store, processing_session_id)
    store.mark_report_processing(processing_session_id)

    response = client.get("/api/reports")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["status"] for item in body["items"]] == [
        "processing",
        "failed",
        "completed",
    ]
    completed = next(item for item in body["items"] if item["status"] == "completed")
    assert completed["session_id"] == completed_session_id
    assert completed["overall_score"] == 81
    assert completed["summary"] == "Clear project story."
    assert completed["report_pdf_url"] == f"/api/interviews/{completed_session_id}/report.pdf"
    assert completed["created_at"]
    assert completed["finished_at"]


def test_reports_endpoint_filters_status_and_limit():
    client, store, _, _ = make_client()
    completed_session_id = start_interview(client)
    finish_session(store, completed_session_id)
    store.mark_report_processing(completed_session_id)
    store.save_report(
        completed_session_id,
        InterviewReport(
            session_id=completed_session_id,
            overall_score=80,
            overall_dimension_scores=make_dimension_scores(80),
            summary="Completed report.",
            highlights=["Clear context"],
            feedbacks=[
                InterviewFeedback(
                    question_id="q1",
                    question_text="Introduce a backend project.",
                    user_answer="The candidate built a backend cache service.",
                    score=80,
                    dimension_scores=make_dimension_scores(80),
                    rationale="Solid answer.",
                    critique="Needs metrics.",
                    better_answer="Add p95 latency impact.",
                    references=[],
                )
            ],
        ),
    )
    processing_session_id = start_interview(client)
    finish_session(store, processing_session_id)
    store.mark_report_processing(processing_session_id)

    response = client.get("/api/reports?status=completed&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["session_id"] == completed_session_id
    assert body["items"][0]["status"] == "completed"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_report_api.py::test_reports_endpoint_lists_completed_failed_and_processing_reports tests/test_report_api.py::test_reports_endpoint_filters_status_and_limit -q
```

Expected: FAIL with 404 because `/api/reports` is not implemented.

- [ ] **Step 3: Add query model and endpoint**

In `app/api/routes.py`, add this route before `/interviews/{session_id}` routes or after report routes; method/path uniqueness avoids conflict:

```python
@router.get("/reports")
def list_reports(
    status: str | None = None,
    limit: int = 20,
    store: InterviewSessionStore = Depends(get_session_store),
):
    if status not in (None, "processing", "completed", "failed"):
        raise HTTPException(status_code=422, detail="invalid status")
    safe_limit = max(1, min(limit, 100))
    reports = store.list_reports(status=status, limit=safe_limit)
    items = [_report_summary_to_dict(item["session_id"], item["record"]) for item in reports]
    return {"items": items, "total": len(items)}
```

Add helper near `_report_progress_detail(...)`:

```python
def _report_summary_to_dict(session_id: str, record) -> dict:
    report = record.report
    return {
        "session_id": session_id,
        "status": record.status,
        "created_at": record.created_at,
        "finished_at": record.finished_at,
        "overall_score": report.overall_score if report is not None else None,
        "summary": report.summary if report is not None else None,
        "is_fallback": report.is_fallback if report is not None else False,
        "error": record.error,
        "report_url": f"/api/interviews/{session_id}/report",
        "report_pdf_url": f"/api/interviews/{session_id}/report.pdf"
        if record.status == "completed"
        else None,
    }
```

`report_pdf_url` is constructed at response time and is not stored.

- [ ] **Step 4: Run API tests**

Run:

```bash
pytest tests/test_report_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/routes.py tests/test_report_api.py
git commit -m "feat: expose local report list api"
```

---

### Task 4: Add In-Page Report Center UI

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Write failing static UI tests**

Add to `tests/test_static_report_ui.py`:

```python
def test_static_page_has_report_center_controls():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="reportCenterButton"' in html
    assert 'id="reportCenterSection"' in html
    assert 'id="reportList"' in html
    assert 'id="backToInterviewButton"' in html


def test_app_js_loads_report_center_and_reuses_report_renderer():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "`/api/reports?status=${encodeURIComponent(status)}&limit=20`" in js
    assert "function loadReportCenter(" in js
    assert "function renderReportList(" in js
    assert "async function openReportFromCenter(" in js
    assert "async function downloadReportPdfFromUrl(" in js
    assert "renderReport(report)" in js
    assert "report_pdf_url" in js


def test_styles_include_report_center_layout():
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".report-center" in css
    assert ".report-list" in css
    assert ".report-list-item" in css
```

- [ ] **Step 2: Add report center markup**

In `app/static/index.html`, add a sidebar/nav button near the existing nav items:

```html
<button class="nav-button" id="reportCenterButton" type="button"><span class="icon">▣</span><span>报告中心</span></button>
```

If `.nav` only contains `<a>` elements, add the button inside the same `<nav class="nav">`.

Inside `<main class="workspace">`, before the existing `.workspace-head`, add:

```html
<section class="report-center" id="reportCenterSection" hidden>
  <div class="report-center-head">
    <div>
      <h2>报告中心</h2>
      <p>本机历史面试报告，仅来自当前部署环境。</p>
    </div>
    <div class="report-center-actions">
      <select id="reportCenterStatusFilter" aria-label="报告状态筛选">
        <option value="">全部状态</option>
        <option value="completed">已完成</option>
        <option value="processing">生成中</option>
        <option value="failed">失败</option>
      </select>
      <button class="ghost-btn" id="refreshReportsButton" type="button">刷新</button>
      <button class="ghost-btn" id="backToInterviewButton" type="button">返回面试</button>
    </div>
  </div>
  <div class="report-list" id="reportList"></div>
</section>
```

Wrap the existing workspace interview content in a container:

```html
<section id="interviewWorkspace">
  ...existing workspace-head and main-grid...
</section>
```

Do not introduce a frontend router.

- [ ] **Step 3: Add JS selectors and event listeners**

In `app/static/app.js`, add selectors near the existing constants:

```javascript
const reportCenterButton = document.querySelector("#reportCenterButton");
const reportCenterSection = document.querySelector("#reportCenterSection");
const reportCenterStatusFilter = document.querySelector("#reportCenterStatusFilter");
const refreshReportsButton = document.querySelector("#refreshReportsButton");
const backToInterviewButton = document.querySelector("#backToInterviewButton");
const reportList = document.querySelector("#reportList");
const interviewWorkspace = document.querySelector("#interviewWorkspace");
```

Add listeners near existing button listeners:

```javascript
reportCenterButton.addEventListener("click", async () => {
  await loadReportCenter();
});

refreshReportsButton.addEventListener("click", async () => {
  await loadReportCenter(reportCenterStatusFilter.value);
});

reportCenterStatusFilter.addEventListener("change", async () => {
  await loadReportCenter(reportCenterStatusFilter.value);
});

backToInterviewButton.addEventListener("click", () => {
  showInterviewWorkspace();
});
```

- [ ] **Step 4: Add report center JS functions**

Add these functions before `resetWorkspace()`:

```javascript
async function downloadReportPdfFromUrl(url, filename) {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "PDF download failed");
  }

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

async function loadReportCenter(status = "") {
  const url = status
    ? `/api/reports?status=${encodeURIComponent(status)}&limit=20`
    : "/api/reports?limit=20";
  reportList.innerHTML = "";
  reportList.appendChild(createEl("div", "empty-state", "正在加载本机报告..."));
  showReportCenter();

  try {
    const response = await fetch(url);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || "加载报告中心失败");
    }
    const body = await response.json();
    renderReportList(body.items || []);
  } catch (error) {
    reportList.innerHTML = "";
    reportList.appendChild(createEl("div", "empty-state", error.message || "加载报告中心失败"));
  }
}

function showReportCenter() {
  interviewWorkspace.hidden = true;
  reportCenterSection.hidden = false;
}

function showInterviewWorkspace() {
  reportCenterSection.hidden = true;
  interviewWorkspace.hidden = false;
}

function renderReportList(items) {
  reportList.innerHTML = "";
  if (!items.length) {
    reportList.appendChild(createEl("div", "empty-state", "暂无本机报告"));
    return;
  }

  items.forEach((item) => {
    const row = createEl("article", `report-list-item report-${item.status}`);
    const main = createEl("div", "report-list-main");
    main.appendChild(createEl("strong", "", item.summary || `报告 ${item.session_id}`));
    main.appendChild(
      createEl(
        "span",
        "meta",
        `${item.status} · ${item.overall_score ?? "--"} 分 · ${formatReportTime(item.created_at)}`
      )
    );
    row.appendChild(main);

    const actions = createEl("div", "report-list-actions");
    const openButton = createEl("button", "ghost-btn", "查看");
    openButton.type = "button";
    openButton.disabled = item.status !== "completed";
    openButton.addEventListener("click", async () => {
      await openReportFromCenter(item.session_id);
    });
    actions.appendChild(openButton);

    if (item.report_pdf_url) {
      const pdfButton = createEl("button", "ghost-btn", "PDF");
      pdfButton.type = "button";
      pdfButton.addEventListener("click", async () => {
        try {
          await downloadReportPdfFromUrl(
            item.report_pdf_url,
            `interview-report-${item.session_id}.pdf`
          );
        } catch (error) {
          reportList.prepend(
            createEl("p", "report-alert danger", error.message || "PDF download failed")
          );
        }
      });
      actions.appendChild(pdfButton);
    }

    row.appendChild(actions);
    reportList.appendChild(row);
  });
}

async function openReportFromCenter(reportSessionId) {
  const response = await fetch(`/api/interviews/${reportSessionId}/report`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    reportList.prepend(createEl("p", "report-alert danger", body.detail || "报告不可用"));
    return;
  }
  const report = await response.json();
  sessionId = reportSessionId;
  showInterviewWorkspace();
  renderReport(report);
}

function formatReportTime(value) {
  if (!value) {
    return "未知时间";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
```

Then refactor the existing `downloadReportPdf()` body to reuse the same blob helper while preserving its current non-destructive error notice behavior:

```javascript
async function downloadReportPdf() {
  if (!sessionId) {
    return;
  }

  try {
    await downloadReportPdfFromUrl(
      `/api/interviews/${sessionId}/report.pdf`,
      `interview-report-${sessionId}.pdf`
    );
    clearReportDownloadNotice();
  } catch (error) {
    showReportDownloadNotice(error.message || "PDF download failed");
  }
}
```

- [ ] **Step 5: Add CSS**

In `app/static/styles.css`, add:

```css
.nav-button {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 12px;
  border: 1px solid transparent;
  border-radius: 10px;
  color: var(--soft);
  background: transparent;
  text-align: left;
}

.report-center {
  padding: 24px;
}

.report-center-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 18px;
}

.report-center-head h2 {
  margin: 0;
  font-size: 24px;
}

.report-center-head p {
  margin: 4px 0 0;
  color: var(--muted);
}

.report-center-actions,
.report-list-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.report-list {
  display: grid;
  gap: 12px;
}

.report-list-item {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: rgba(255, 255, 255, 0.045);
}

.report-list-main {
  display: grid;
  gap: 4px;
}

.report-list-main .meta {
  color: var(--muted);
  font-size: 12px;
}

.report-failed {
  border-color: rgba(255, 109, 109, 0.35);
}

.report-processing {
  border-color: rgba(246, 200, 95, 0.35);
}
```

Inside the existing mobile media query, add:

```css
  .report-center-head,
  .report-list-item {
    flex-direction: column;
  }
```

- [ ] **Step 6: Run static tests**

Run:

```bash
node --check app/static/app.js
pytest tests/test_static_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/styles.css tests/test_static_report_ui.py
git commit -m "feat: add local report center ui"
```

---

### Task 5: Final Regression and Docs

**Files:**
- Modify: `docs/interface-requirements.md`

- [ ] **Step 1: Update interface documentation**

In `docs/interface-requirements.md`:

1. Move `GET /api/reports` from the unimplemented table to implemented interfaces.
2. Document that it is local-only and has no login/user filtering.
3. Document query params:

```text
status: optional, one of processing | completed | failed
limit: optional, 1-100, default 20
```

4. Document response shape:

```json
{
  "items": [
    {
      "session_id": "string",
      "status": "completed",
      "created_at": "2026-07-04T10:00:00Z",
      "finished_at": "2026-07-04T10:02:00Z",
      "overall_score": 81,
      "summary": "Clear project story.",
      "is_fallback": false,
      "error": null,
      "report_url": "/api/interviews/{session_id}/report",
      "report_pdf_url": "/api/interviews/{session_id}/report.pdf"
    }
  ],
  "total": 1
}
```

5. Explicitly state `report_pdf_url` is constructed by the API and not stored.

- [ ] **Step 2: Run focused tests**

Run:

```bash
node --check app/static/app.js
pytest tests/test_report_models.py tests/test_session_serialization.py tests/test_session_report_store.py tests/test_postgres_session_store.py tests/test_report_api.py tests/test_static_report_ui.py -q
```

Expected: PASS. PostgreSQL tests may be skipped when `POSTGRES_DSN` is not set.

- [ ] **Step 3: Run full test suite**

Run:

```bash
pytest -q
```

Expected: PASS. PostgreSQL tests may be skipped when `POSTGRES_DSN` is not set.

- [ ] **Step 4: Check git status**

Run:

```bash
git status --short
```

Expected: only unrelated local IDE/cache/docs files remain if they existed before this stage. The Stage 14 code should be committed; documentation may remain uncommitted if the user asks not to submit docs.

- [ ] **Step 5: Commit docs only if requested**

If documentation changes should be committed:

```bash
git add docs/interface-requirements.md
git commit -m "docs: document local report center api"
```

If documentation should not be committed, leave `docs/interface-requirements.md` unstaged and mention it in the final status.

---

## Self-Review

**Spec coverage:** The plan implements local report history without login, adds report lifecycle timestamps to `ReportRecord`, constructs `report_pdf_url` at API response time, lists Postgres reports by parsing `report_json` in Python, and adds an in-page report center UI that reuses `renderReport(report)`.

**Placeholder scan:** The plan contains no `TBD`, `TODO`, vague error handling, or missing test instructions. Every code-changing task includes concrete snippets and exact commands.

**Type consistency:** Timestamp fields are consistently named `created_at` and `finished_at`; list entries consistently use `{"session_id": ..., "record": ReportRecord}` at the store layer; API output consistently uses `report_url` and `report_pdf_url`.
