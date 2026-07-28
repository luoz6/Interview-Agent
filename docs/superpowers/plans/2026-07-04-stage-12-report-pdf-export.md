# Stage 12 Report PDF Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a downloadable PDF report for completed interview reports in the local single-user deployment.

**Architecture:** Add a small server-side PDF renderer in `app/services/report_pdf.py` using `reportlab`, expose it through a new `GET /api/interviews/{session_id}/report.pdf` route, and wire the existing static report view to trigger download only after the report is complete. Keep the API contract narrow: `404` for missing session, `409` for all "report exists but cannot be downloaded yet" states, and `200 application/pdf` for completed reports.

**Tech Stack:** FastAPI, Pydantic, reportlab, pytest, static HTML/CSS/JavaScript

---

## File Structure

### Create

- `app/services/report_pdf.py`
  - Pure PDF rendering service that converts `InterviewReport` into PDF bytes.
- `tests/test_report_pdf.py`
  - Focused unit tests for PDF byte generation and fallback coverage.

### Modify

- `requirements.txt`
  - Add `reportlab` dependency.
- `app/api/routes.py`
  - Add `GET /api/interviews/{session_id}/report.pdf`.
- `app/static/index.html`
  - Add a download button in the report area.
- `app/static/app.js`
  - Enable/disable the download button with report state and fetch the PDF endpoint.
- `app/static/styles.css`
  - Add small styles for report actions / disabled state if needed.
- `tests/test_report_api.py`
  - Add API contract coverage for the new PDF endpoint.
- `tests/test_static_report_ui.py`
  - Add static assertions for the new button and JS endpoint usage.
- `docs/interface-requirements.md`
  - Move PDF export from pending to implemented once code lands.

## Design Notes

- Use `reportlab` instead of HTML-to-PDF. It is pure Python, easier to install locally, and avoids system browser dependencies.
- Register `STSong-Light` via `UnicodeCIDFont` for Chinese support.
- Render dimension labels with the same Chinese names as `app/static/app.js`: `breadth` -> `知识广度`, `depth` -> `技术深度`, `architecture` -> `系统设计`, `engineering` -> `工程实践`, `communication` -> `表达沟通`.
- Keep the PDF builder independent from FastAPI so it can be unit-tested directly.
- Do not add report-center or login work in this stage.
- Do not persist PDF files to disk. Generate bytes on demand from the existing `InterviewReport`.

## API Contract

### Success

```http
GET /api/interviews/{session_id}/report.pdf
200 OK
Content-Type: application/pdf
Content-Disposition: attachment; filename="interview-report-{session_id}.pdf"
```

### Errors

```json
{"detail":"session not found"}
```

```json
{"detail":"interview is not finished"}
```

```json
{"detail":"report is not ready"}
```

```json
{"detail":"report generation failed"}
```

- Missing session: `404`
- Active interview / processing report / failed report: `409`
- Note: existing JSON report endpoint returns `500` for failed reports. The PDF endpoint intentionally uses `409` because the report exists but cannot be downloaded in that state.

## Task 1: Add a Pure PDF Rendering Service

**Files:**
- Create: `app/services/report_pdf.py`
- Create: `tests/test_report_pdf.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write the failing PDF service tests**

Add `tests/test_report_pdf.py` with focused coverage:

```python
from app.services.report import (
    DimensionScores,
    InterviewFeedback,
    InterviewReport,
)
from app.services.report_pdf import build_report_pdf


def make_dimension_scores(score: int = 81) -> DimensionScores:
    return DimensionScores(
        breadth=score,
        depth=score,
        architecture=score,
        engineering=score,
        communication=score,
    )


def make_report(*, is_fallback: bool = False) -> InterviewReport:
    return InterviewReport(
        session_id="session-pdf-1",
        overall_score=81,
        overall_dimension_scores=make_dimension_scores(81),
        summary="Clear project story with practical tradeoffs.",
        highlights=["Explained cache tradeoffs.", "Used fallback strategy."],
        feedbacks=[
            InterviewFeedback(
                question_id="q1",
                question_text="Introduce a backend project.",
                user_answer="The candidate built a Redis-backed API.",
                score=81,
                dimension_scores=make_dimension_scores(81),
                rationale="The answer linked design choices to the workload.",
                critique="Business outcome metrics were weak.",
                better_answer="I reduced p95 latency with cache-aside Redis.",
                references=[],
            )
        ],
        is_fallback=is_fallback,
    )


def test_build_report_pdf_returns_pdf_bytes():
    pdf_bytes = build_report_pdf(make_report())

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_build_report_pdf_supports_fallback_reports():
    pdf_bytes = build_report_pdf(make_report(is_fallback=True))

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000
```

Keep the local `make_dimension_scores()` helper in this file even though `tests/test_report_api.py` also defines one. The duplication is intentional so the PDF unit tests stay self-contained.

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_pdf.py -q
```

Expected:

- FAIL with `ModuleNotFoundError` for `app.services.report_pdf`

- [ ] **Step 3: Add the dependency and minimal PDF implementation**

Update `requirements.txt`:

```text
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
pytest>=8.0.0
httpx>=0.27.0
pydantic>=2.0.0
langchain>=1.0.0
langchain-openai>=1.0.0
psycopg2-binary>=2.9.9
pgvector>=0.3.5
sentence-transformers>=3.0.0
langchain-huggingface>=0.1.0
reportlab>=4.2.0
```

Create `app/services/report_pdf.py`:

```python
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.report import InterviewFeedback, InterviewReport


_FONT_NAME = "STSong-Light"
_DIMENSION_LABELS = {
    "breadth": "知识广度",
    "depth": "技术深度",
    "architecture": "系统设计",
    "engineering": "工程实践",
    "communication": "表达沟通",
}


def build_report_pdf(report: InterviewReport) -> bytes:
    _register_pdf_fonts()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Interview Report {report.session_id}",
        author="Interview Agent",
    )
    document.build(_build_story(report))
    return buffer.getvalue()


def _register_pdf_fonts() -> None:
    registered = pdfmetrics.getRegisteredFontNames()
    if _FONT_NAME not in registered:
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT_NAME))


def _build_story(report: InterviewReport) -> list:
    styles = _build_styles()
    story = [
        Paragraph("Interview Report", styles["title"]),
        Spacer(1, 6),
        Paragraph(f"Session ID: {report.session_id}", styles["meta"]),
        Spacer(1, 8),
        Paragraph(f"Overall Score: {report.overall_score}", styles["score"]),
        Spacer(1, 8),
        Paragraph("Overall Summary", styles["section"]),
        Paragraph(report.summary, styles["body"]),
        Spacer(1, 8),
        Paragraph("Dimension Scores", styles["section"]),
        _dimension_table(report, styles),
        Spacer(1, 8),
        Paragraph("Highlights", styles["section"]),
    ]
    for highlight in report.highlights:
        story.append(Paragraph(f"- {highlight}", styles["body"]))
    if report.is_fallback:
        story.extend(
            [
                Spacer(1, 8),
                Paragraph("Fallback report: evidence generation was degraded.", styles["warning"]),
            ]
        )
    story.extend([Spacer(1, 10), Paragraph("Question Feedback", styles["section"])])
    for feedback in report.feedbacks:
        story.extend(_feedback_story(feedback, styles))
    return story


def _build_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName=_FONT_NAME),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontName=_FONT_NAME),
        "meta": ParagraphStyle("meta", parent=base["BodyText"], fontName=_FONT_NAME),
        "score": ParagraphStyle("score", parent=base["Heading1"], fontName=_FONT_NAME),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName=_FONT_NAME, leading=16),
        "warning": ParagraphStyle("warning", parent=base["BodyText"], fontName=_FONT_NAME, textColor=colors.darkorange),
    }


def _dimension_table(report: InterviewReport, styles) -> Table:
    rows = [["维度", "分数"]]
    for name, value in report.overall_dimension_scores.model_dump().items():
        rows.append([_DIMENSION_LABELS.get(name, name), str(value)])
    table = Table(rows, colWidths=[70 * mm, 30 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7EEF7")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#AAB7C4")),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _feedback_story(feedback: InterviewFeedback, styles) -> list:
    blocks = [
        Spacer(1, 8),
        Paragraph(feedback.question_text, styles["section"]),
        Paragraph(f"Score: {feedback.score}", styles["body"]),
        Paragraph(f"Answer: {feedback.user_answer}", styles["body"]),
        Paragraph(f"Rationale: {feedback.rationale}", styles["body"]),
        Paragraph(f"Critique: {feedback.critique}", styles["body"]),
        Paragraph(f"Better Answer: {feedback.better_answer}", styles["body"]),
    ]
    for reference in feedback.references:
        blocks.append(
            Paragraph(
                f"Reference: {reference.title} ({reference.source_type}) - {reference.excerpt}",
                styles["body"],
            )
        )
    return blocks
```

- [ ] **Step 4: Run the PDF service tests until they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_pdf.py -q
```

Expected:

- `2 passed`

- [ ] **Step 5: Commit the service slice**

Run:

```powershell
git add requirements.txt app/services/report_pdf.py tests/test_report_pdf.py
git commit -m "feat: add report pdf renderer"
```

## Task 2: Add the PDF Download API Route

**Files:**
- Modify: `app/api/routes.py`
- Modify: `tests/test_report_api.py`

- [ ] **Step 1: Write failing API tests for the new route**

Add these tests to `tests/test_report_api.py`:

```python
def test_report_pdf_endpoint_returns_404_for_unknown_session():
    client, _, _, _ = make_client()

    response = client.get("/api/interviews/missing/report.pdf")

    assert response.status_code == 404
    assert response.json()["detail"] == "session not found"


def test_report_pdf_endpoint_rejects_active_interview():
    client, _, _, _ = make_client()
    session_id = start_interview(client)

    response = client.get(f"/api/interviews/{session_id}/report.pdf")

    assert response.status_code == 409
    assert response.json()["detail"] == "interview is not finished"


def test_report_pdf_endpoint_rejects_processing_report():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.mark_report_processing(session_id)

    response = client.get(f"/api/interviews/{session_id}/report.pdf")

    assert response.status_code == 409
    assert response.json()["detail"] == "report is not ready"


def test_report_pdf_endpoint_rejects_failed_report():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.fail_report(session_id, "report generation failed")

    response = client.get(f"/api/interviews/{session_id}/report.pdf")

    assert response.status_code == 409
    assert response.json()["detail"] == "report generation failed"


def test_report_pdf_endpoint_returns_pdf_for_completed_report():
    client, store, _, _ = make_client()
    session_id = start_interview(client)
    finish_session(store, session_id)
    store.save_report(
        session_id,
        InterviewReport(
            session_id=session_id,
            overall_score=81,
            overall_dimension_scores=make_dimension_scores(81),
            summary="Clear project story with practical tradeoffs.",
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

    response = client.get(f"/api/interviews/{session_id}/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "attachment; filename=" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")
```

- [ ] **Step 2: Run the focused API tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py -q
```

Expected:

- FAIL because `/report.pdf` route does not exist yet

- [ ] **Step 3: Add the route in `app/api/routes.py`**

Import `Response` and the new service:

```python
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.services.report_pdf import build_report_pdf
```

Add the route below `get_interview_report`:

```python
@router.get("/interviews/{session_id}/report.pdf")
def download_interview_report_pdf(
    session_id: str,
    store: InterviewSessionStore = Depends(get_session_store),
):
    try:
        state = store.get(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if state["status"] != "finished":
        raise HTTPException(status_code=409, detail="interview is not finished")

    record = store.get_report_record(session_id)
    if record is None or record.status == "processing":
        raise HTTPException(status_code=409, detail="report is not ready")
    if record.status == "failed":
        raise HTTPException(
            status_code=409,
            detail=record.error,
        )

    pdf_bytes = build_report_pdf(record.report)
    filename = f'interview-report-{session_id}.pdf'
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 4: Run the API tests until they pass**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py tests/test_report_pdf.py -q
```

Expected:

- All new PDF service and API tests pass

- [ ] **Step 5: Commit the API slice**

Run:

```powershell
git add app/api/routes.py tests/test_report_api.py requirements.txt app/services/report_pdf.py tests/test_report_pdf.py
git commit -m "feat: add report pdf download endpoint"
```

## Task 3: Wire the Static Report UI to Download the PDF

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Modify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Add failing static UI tests**

Extend `tests/test_static_report_ui.py`:

```python
def test_static_page_has_report_download_button():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="downloadReportButton"' in html
    assert "下载 PDF" in html


def test_app_js_downloads_report_pdf_and_manages_button_state():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'const downloadReportButton = document.querySelector("#downloadReportButton")' in js
    assert "`/api/interviews/${sessionId}/report.pdf`" in js
    assert "downloadReportButton.disabled = true" in js or "setReportDownloadEnabled(false)" in js
    assert "setReportDownloadEnabled(true)" in js
    assert "URL.createObjectURL(blob)" in js
    assert "showReportDownloadNotice(" in js
    assert "renderReportError(body.detail || \"PDF download failed\")" not in js
```

- [ ] **Step 2: Run the static tests to verify they fail**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

Expected:

- FAIL because the button and JS download flow do not exist

- [ ] **Step 3: Add the button, JS flow, and minimal styles**

Update `app/static/index.html` inside `#reportSection`:

```html
<div class="report-actions">
  <button class="ghost-btn" id="downloadReportButton" type="button" disabled>下载 PDF</button>
</div>
<div id="reportContent" class="report-content" hidden></div>
```

Update `app/static/app.js`:

```javascript
const downloadReportButton = document.querySelector("#downloadReportButton");

downloadReportButton.addEventListener("click", async () => {
  await downloadReportPdf();
});

function setReportDownloadEnabled(enabled) {
  downloadReportButton.disabled = !enabled;
}

function clearReportDownloadNotice() {
  const existing = reportContent.querySelector('[data-report-download-notice="true"]');
  if (existing) {
    existing.remove();
  }
}

function showReportDownloadNotice(message) {
  clearReportDownloadNotice();
  if (reportContent.hidden) {
    return;
  }
  const notice = createEl("p", "report-alert warning", message);
  notice.dataset.reportDownloadNotice = "true";
  reportContent.prepend(notice);
}

async function downloadReportPdf() {
  if (!sessionId) {
    return;
  }

  const response = await fetch(`/api/interviews/${sessionId}/report.pdf`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    showReportDownloadNotice(body.detail || "PDF download failed");
    return;
  }

  clearReportDownloadNotice();
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `interview-report-${sessionId}.pdf`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
```

Patch the existing report lifecycle helpers in `app/static/app.js`. Do not replace the whole function bodies; keep the current rendering logic and only add the lines below at the indicated positions:

```javascript
// In resetReport(), after clearing reportPollTimer:
setReportDownloadEnabled(false);

// In renderReportProcessing(progress), at the top of the function before mutating the report DOM:
setReportDownloadEnabled(false);

// In renderReportError(message), add this near the top before reportContent.innerHTML = "":
setReportDownloadEnabled(false);
clearReportDownloadNotice();

// Keep the existing lines that set:
// - reportStatus.textContent
// - reportProgressBar.style.width
// - setReportSummary(...)
// - setReportAdvice(...)
// - reportContent.innerHTML = ""
// - reportContent.appendChild(createEl("p", "report-alert danger", ...))

// In renderReport(report), add these at the top before reportContent.innerHTML = "":
setReportDownloadEnabled(true);
clearReportDownloadNotice();

// Keep the existing report rendering logic intact:
// - fallback warning rendering
// - renderEvidenceFromReport(report)
// - overview / dimensions / highlights / feedback list creation
```

The intent is:

- `resetReport()` and `renderReportProcessing()` disable download while the report is unavailable
- `renderReportError()` disables download and clears any old download notice, but still preserves the existing error rendering path
- `renderReport(report)` enables download and removes old download notices before rebuilding the report content

Update `app/static/styles.css`:

```css
.report-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  margin-top: 8px;
}

.report-actions .ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
```

- [ ] **Step 4: Run the static checks**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
node --check app/static/app.js
```

Expected:

- Static tests pass
- `node --check` prints no error

- [ ] **Step 5: Commit the frontend slice**

Run:

```powershell
git add app/static/index.html app/static/app.js app/static/styles.css tests/test_static_report_ui.py
git commit -m "feat: add pdf download control to report view"
```

## Task 4: Update the Interface Doc and Run Regression

**Files:**
- Modify: `docs/interface-requirements.md`
- Verify: `tests/test_report_api.py`
- Verify: `tests/test_report_pdf.py`
- Verify: `tests/test_static_report_ui.py`

- [ ] **Step 1: Update the interface doc after implementation**

Move the PDF endpoint from pending to implemented and update the status tables in `docs/interface-requirements.md`:

```markdown
| `GET` | `/api/interviews/{session_id}/report.pdf` | 下载已完成的 PDF 报告 | `app/static/index.html` 复盘区下载按钮 |
```

Update the PDF section to reflect the implemented contract:

~~~markdown
### 7.3 下载 PDF 报告

当前已实现接口：

```http
GET /api/interviews/{session_id}/report.pdf
```

错误语义：

- `404`：`session_id` 不存在
- `409`：面试未结束、报告仍在生成中，或报告生成失败
~~~

- [ ] **Step 2: Run the focused regression suite**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_report_api.py tests/test_report_pdf.py tests/test_static_report_ui.py -q
```

Expected:

- All focused tests pass

- [ ] **Step 3: Run the full regression suite**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
```

Expected:

- Existing suite remains green

- [ ] **Step 4: Do a manual local smoke test**

Run the app locally and verify one full path:

1. Start a session from the static page
2. Finish the interview
3. Wait for the report to complete
4. Click `下载 PDF`
5. Confirm the browser downloads `interview-report-<session_id>.pdf`

- [ ] **Step 5: Commit the documentation and regression slice**

Run:

```powershell
git add docs/interface-requirements.md
git commit -m "docs: mark pdf report export as implemented"
```

## Self-Review

### Spec coverage

- New PDF rendering dependency and service: covered in Task 1
- New `/report.pdf` endpoint and status codes: covered in Task 2
- Static UI download action: covered in Task 3
- Interface doc refresh and regression: covered in Task 4

### Placeholder scan

- No `TODO`, `TBD`, or “implement later” markers remain
- Each task contains exact file paths, code snippets, and commands

### Type consistency

- The renderer consumes `InterviewReport`, matching the existing completed report contract
- The new route returns `application/pdf` and uses the same `session_id` lookup pattern as existing report endpoints
- The front end targets `/api/interviews/${sessionId}/report.pdf`, matching the route path
