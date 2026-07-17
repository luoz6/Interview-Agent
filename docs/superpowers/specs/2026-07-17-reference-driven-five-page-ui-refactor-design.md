# Reference-Driven Five-Page UI Refactor Design

Status: `APPROVED_FOR_PLANNING`

Date: 2026-07-17

## 1. Objective

Refactor the five production desktop Web pages to follow the layout,
information hierarchy, and visual language of
`app/interview-agent-single-file.html`, while replacing every static demo
value and simulated interaction with real application data and behavior.

The production pages remain:

- `app/test4.html`: interview preparation
- `app/test3.html`: live interview
- `app/test2.html`: report processing
- `app/test1.html`: report detail
- `app/test0.html`: report center

The reference HTML is the visual source of truth. After its five views are
split into production pages, it moves to
`docs/prototypes/interview-agent-single-file.html` as a design artifact and
is not served by a production route.

## 2. Scope

### 2.1 In Scope

- Rebuild all five production pages from the corresponding reference views.
- Preserve existing routes, query parameters, API contracts, DOM IDs, and
  JavaScript module entry points unless this design explicitly extends them.
- Extract the reference CSS into the existing Tailwind source/build flow.
- Keep static HTML plus native ES Modules; do not add React, Vue, or a server
  template engine.
- Implement every visible reference control as a real feature.
- Add a failed-report requeue API and safe report-list metadata required by
  the report center.
- Use Stage 43B public runtime APIs for report execution traces.
- Support desktop Web only, with a minimum width of 1280px and a primary
  acceptance viewport of 1440x1000.

### 2.2 Out of Scope

- Mobile or narrow-tablet layout redesign.
- Authentication, cloud sync, or multi-user ownership controls.
- Candidate population statistics or percentile ranking. The reference
  "outperformed candidates" block is removed because no valid population
  dataset exists.
- New provider, evaluator, report-scoring, or knowledge-retrieval behavior.
- A new frontend framework or bundler.

## 3. Production Architecture

### 3.1 Static Page Boundary

Each route continues to return an independent HTML file through the existing
`FileResponse` route in `app/main.py`. Shared page chrome remains static HTML
in each file. This accepts limited header/navigation duplication in exchange
for rendering a usable page before JavaScript runs and preserving current
route and test behavior.

### 3.2 Styling Boundary

`app/static/prototype-source.css` owns:

- color, typography, spacing, border, and elevation tokens;
- top navigation, workflow navigation, page shell, and content grids;
- buttons, badges, tags, progress indicators, tables, notices, empty states,
  loading states, and focused interview mode;
- page-specific layout components derived from the reference HTML.

`npm run build:prototype-css` continues to compile
`app/static/prototype.css`. Production HTML must not retain the reference
file's large inline `<style>` block.

The reference layout and hierarchy stay recognizable, with these production
constraints:

- card and control radii do not exceed 8px;
- purple-blue gradients become a stable solid primary color;
- success, warning, and failure states use distinct green, amber, and red;
- nested decorative cards are flattened into sections or full-width bands;
- contrast, focus states, disabled states, and text overflow are explicit.

### 3.3 JavaScript Boundary

The existing modules retain their responsibilities:

- `app/static/prep.js`: preparation, file import, drafts, plan, and evidence;
- `app/static/interview.js`: snapshot, SSE answers, conflicts, focus mode, and
  answer drafts;
- `app/static/report-processing.js`: progress polling and completion/failure;
- `app/static/report-detail.js`: report, evaluations, evidence, traces, PDF;
- `app/static/report-center.js`: report loading, filtering, pagination,
  download, and requeue;
- `app/static/shared-ui.js`: reusable safe DOM, status, and formatting helpers;
- `app/static/api.js`: HTTP, query-string, SSE, and download helpers.

`shared-ui.js` does not render the whole application shell. Dynamic user or
provider text is assigned through `textContent` or nodes created by
`createEl`; it is never interpolated into `innerHTML`.

## 4. Page Design

### 4.1 Interview Preparation

The page follows the reference two-column layout: workflow navigation on the
left, JD/resume inputs in the main column, and a sticky plan preview on the
right.

Real behaviors:

- JD and resume file buttons open file inputs accepting `.txt` and `.md`.
- Files are read in the browser with `File.text()`; binary upload is not sent
  to the backend.
- Each file is limited to 1 MiB. Unsupported extensions, unreadable files,
  and oversized files render an inline error beside the affected field.
- Imported content populates the existing textareas and shows the file name
  and character count.
- Save and restore use the existing interview-draft APIs.
- Generate and regenerate use `POST /api/prep`.
- Start interview uses `POST /api/interviews`.
- Tags, question count, question kinds, plan evidence, knowledge status, and
  evidence summaries are rendered from `InterviewPlan` and `PrepContext`.
- Estimated duration is explicitly labeled as an estimate and uses the fixed
  display rule of four to six minutes per generated main question.

### 4.2 Live Interview

The page follows the reference three-column workbench: question plan, live
conversation and answer editor, and context/status sidebar.

Real behaviors:

- Existing SSE submission, skip, finish, command ID, expected version, and
  conflict recovery behavior remains unchanged.
- Focus mode toggles a body state that hides the left and right columns and
  expands the interview column. The button exposes `aria-pressed`; Escape
  exits focus mode.
- The character counter reflects the current textarea value.
- The current answer is saved after a 300ms debounce under
  `interviewAnswerDraft:<session_id>:<question_id>` in `localStorage`.
- A matching draft is restored on refresh. It is cleared only after a
  successful answer, skip, or explicit finish for that question.
- Version-conflict recovery retains the draft while refreshing the session.
- Elapsed time is derived from the public session `started_at` value.
- Question progress and current round come from the session snapshot.
- Connection status means the most recent snapshot or command succeeded; it
  is not a fabricated permanent connectivity claim.
- Answer-save status reflects the local draft operation.
- Round-review status is derived from the count and status of public question
  evaluations, not a simulated percentage.

### 4.3 Report Processing

The page uses the reference workflow navigation, dominant progress panel,
stage timeline, and task summary sidebar.

Real behaviors:

- Status, stage, percent, message, current question, job ID, events, and safe
  metadata come from the report-progress API.
- Microbatch reuse, rerun, fallback, and knowledge-path counts only appear
  when present in safe progress metadata.
- Queue/worker labels describe the actual report job status; the page does
  not claim a worker heartbeat or database health that the API does not
  expose.
- Completed reports redirect to report detail.
- Failed reports stop polling and show the stable failure message plus a link
  back to the report center.
- "Continue in background" navigates to the report center. It does not cancel
  or restart the persisted report job.

### 4.4 Report Detail

The page follows the reference report navigation, summary score band,
dimension bars, strength/risk sections, per-question evaluations, and action
header.

Real behaviors:

- Overall score, summary, highlights, dimension scores, feedback, references,
  and fallback status come from the report API.
- High-score count is the number of feedback items with score at least 80.
- Improvement count is the number of feedback items below 80.
- Strengths come from report highlights. Improvement items come from the
  lowest-scoring feedback critiques and better-answer guidance.
- Per-question status and retrieval path come from question evaluations.
- Evidence cards expose only the already-public evidence ID, title, source
  type, and bounded excerpt.
- Report navigation scrolls to overview, question evaluations, improvement
  guidance, and runtime trace sections.
- Runtime trace loads the existing safe agent-run and runtime-event endpoints.
  It never renders internal `safe_metadata`, event payload JSON, prompts,
  answers, resume text, or knowledge content.
- PDF download uses the existing report-PDF endpoint.
- "Interview again" goes to `/prep`; "Report center" goes to `/reports`.
- The unsupported candidate-percentile block is deleted.

### 4.5 Report Center

The page follows the reference overview/sidebar/table composition.

Real behaviors:

- The page loads up to 100 local report summaries and derives overview counts
  from that bounded result.
- Search matches safe job title, session ID, job tags, summary, and status.
- Status filtering supports all, completed, processing, and failed.
- Date filtering supports all dates and the most recent 30 days.
- Client pagination uses five rows per page and resets to page one whenever a
  search or filter changes.
- Completed rows expose report detail and PDF download.
- Processing rows expose report progress.
- Failed rows expose requeue and interview-again actions.
- Empty and no-match states are visually distinct.
- The help navigation points to the existing `/help` page.

## 5. API Extensions

### 5.1 Safe Report Summary Fields

`GET /api/reports` keeps its existing fields and adds:

```json
{
  "job_title": "Backend Engineer Interview",
  "job_tags": ["Redis", "MySQL"],
  "question_count": 12,
  "started_at": "2026-07-17T08:00:00Z",
  "duration_seconds": 3492,
  "report_path": "microbatch"
}
```

These values are derived from the public plan/session state and safe report
progress metadata. The response must not include job-description text,
resume text, candidate answers, prompts, internal event payloads, or arbitrary
metadata.

The in-memory and PostgreSQL `list_reports` implementations return the bounded
session metadata needed by the route without making one session query per
report row.

### 5.2 Failed Report Requeue

Add:

```text
POST /api/interviews/{session_id}/report/requeue
```

Successful response: HTTP 202

```json
{
  "session_id": "session-id",
  "status": "queued",
  "report_progress_url": "/api/interviews/session-id/report/progress"
}
```

Rules:

- A missing session or report job returns 404.
- A job that is not `failed` returns 409.
- A queue backend that is not configured returns 503.
- Success delegates to `ReportJobQueue.requeue_failed(session_id)`.
- Requeue resets both report and report-job status through the existing
  atomic PostgreSQL implementation.
- A second requeue request after success returns 409 and does not create a
  second job.

## 6. Data Flow

```text
reference HTML view
    -> production page structure and CSS classes
    -> existing page ES Module
    -> existing API plus two bounded API extensions
    -> safe DOM rendering

failed report row
    -> POST report/requeue
    -> ReportJobQueue.requeue_failed
    -> reports + report_jobs atomically return to processing/queued
    -> report center reload
    -> report-processing polling
```

No visual component reads the database directly. The browser continues to
consume only public API responses.

## 7. State and Error Handling

Every command has idle, busy, success, and failure presentation. Busy controls
are disabled until the request settles. A success notice appears only after a
successful response or local operation.

Required error states:

- unsupported, oversized, or unreadable text file;
- missing or invalid JD/resume input;
- draft missing or expired;
- prep/interview request failure;
- SSE interruption with answer restoration;
- optimistic version conflict with snapshot refresh;
- missing session ID or missing session;
- report queued, processing, completed, or failed;
- report/PDF not ready;
- report-list loading failure, empty list, and no filter match;
- failed-report requeue conflict or unavailable queue;
- runtime trace unavailable while the report remains readable.

Errors render beside the affected workflow and expose a valid recovery action.
Raw tracebacks, provider payloads, and internal exception strings do not reach
the page.

## 8. Accessibility and Desktop Layout

- Semantic headings and landmarks identify navigation, main content, tables,
  forms, and status regions.
- All controls are keyboard reachable and have visible focus states.
- Icon-only PDF actions have accessible labels and tooltips.
- Focus mode declares `aria-pressed`.
- Notices use a polite live region; blocking errors use an assertive live
  region.
- Tables preserve headers and readable column widths at 1280px.
- Fixed-format panels use explicit grid tracks and min-width constraints so
  dynamic status text cannot resize the page.
- Text wraps or truncates deliberately; no controls, status labels, or content
  panels overlap at 1280px or 1440px.

## 9. Test Strategy

### 9.1 Python Contract Tests

- Page routes still return the expected five HTML files and script entries.
- Required DOM IDs, landmarks, upload inputs, focus controls, filters, table,
  report navigation, and trace containers exist.
- Safe report summaries contain the new fields and exclude private content.
- Requeue returns 202 for failed jobs and 404/409/503 for invalid states.
- In-memory and PostgreSQL report-list metadata stays behaviorally aligned.

### 9.2 JavaScript and Browser Tests

- Text-file import populates the correct textarea and rejects invalid files.
- Focus mode enters, exits, and responds to Escape.
- Answer drafts survive refresh and version conflict, then clear on success.
- Existing prep, SSE answer, skip, finish, report, evidence, and PDF workflow
  remains green.
- Report search, status/date filters, pagination, direct PDF, and requeue work
  against deterministic browser support data.
- Missing sessions and failed requests expose bounded safe errors.
- Report trace renders safe identifiers/statuses and contains no blocked keys.
- Screenshots at 1440x1000 cover all five pages and major completed/failed
  states.
- Geometry assertions at 1280x800 and 1440x1000 verify no horizontal overflow,
  clipped command buttons, or overlapping columns.

### 9.3 Release Gates

- `npm run build:prototype-css`
- JavaScript syntax checks for all modified modules
- focused Python page/API/repository tests
- deterministic Playwright suite
- full Python regression suite
- `git diff --check`

## 10. Delivery Order

The implementation is split into independently testable increments:

1. freeze the reference artifact and shared visual system;
2. rebuild preparation and implement file import;
3. rebuild live interview and implement focus/drafts/review status;
4. rebuild report processing with truthful progress state;
5. extend report summary and requeue APIs;
6. rebuild report detail and safe runtime trace;
7. rebuild report center and all list controls;
8. align help/navigation and complete visual/browser acceptance.

Each increment preserves a runnable application and receives its own focused
tests and commit.

## 11. Acceptance Criteria

- All five routed pages visibly follow the supplied reference HTML.
- No reference demo value appears before corresponding real data exists.
- Every visible command performs a real local action and reports its outcome.
- Existing interview, report, knowledge-evidence, PDF, and Stage 43B privacy
  behavior remains intact.
- Failed report requeue is idempotent at the job identity level and rejects
  invalid states.
- Candidate percentile text is absent.
- Desktop layouts are usable and non-overlapping at 1280px and 1440px.
- All release gates pass without requiring a real model or local embedding
  download; real-model browser coverage remains opt-in.
