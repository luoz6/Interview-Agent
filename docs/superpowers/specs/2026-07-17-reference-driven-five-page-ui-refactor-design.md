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

This is intentionally a reference-layout migration, not a pixel-identical
copy. The production design keeps the reference page composition and
information hierarchy while applying the repository's production UI rules.

#### Visual Degradation Map

| Reference token or selector | Reference value | Production value | Tailwind/source strategy |
| --- | --- | --- | --- |
| `--blue` | `#4169f5` | `#2563eb` | `blue-600`; semantic `--color-primary` |
| `--blue2` | `#7448f3` | `#2563eb` | Fold into the single primary token |
| Primary/logo gradient | `#4f6cf8` to `#7948ee` | `#2563eb` solid | `bg-blue-600`; no gradient utility |
| Primary hover | Gradient/shadow variation | `#1d4ed8` solid | `hover:bg-blue-700` |
| `--bg` | `#f5f7fb` | `#f8fafc` | `slate-50`; page background token |
| `--text` | `#2b3951` | `#1f2937` | `gray-800`; primary text token |
| `--muted` | `#8d99ad` | `#64748b` | `slate-500`; secondary text token |
| `--line` | `#e6ebf3` | `#e2e8f0` | `slate-200`; border token |
| `--radius` | `16px` | `8px` | `rounded-lg` only for cards |
| 10-12px control radii | `10px` to `12px` | `6px` | semantic control radius; `rounded-md` |
| Status/tag pills | mixed radii | pill only when semantically a status/tag | `rounded-full` is not used for cards/buttons |
| Main card shadow | `0 12px 32px rgba(28,43,74,.05)` | `0 1px 2px rgba(15,23,42,.06)` | `shadow-sm` |
| Primary button shadow | `0 8px 18px rgba(77,91,238,.25)` | none by default | border and color carry hierarchy |
| Success | `#18a66b` family | `#16a34a` family | `green-600` plus bounded tint |
| Warning | orange family | `#d97706` family | `amber-600` plus bounded tint |
| Failure | red family | `#dc2626` family | `red-600` plus bounded tint |
| Inline `<style>` block | about 90 lines | no production inline block | semantic classes in `prototype-source.css` |

The Tailwind `--content` scan remains `app/test*.html` and
`app/static/*.js`. Every semantic selector declared inside `@layer
components` must occur literally in one of those scanned files. JavaScript
must use complete literal class strings rather than fragments such as
`bg-${color}-600`. Utilities used by `@apply` are resolved at build time, but
the owning semantic selector still needs a literal production usage so it is
not removed from the generated component layer.

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
- Elapsed and estimated remaining time use the existing public session
  `elapsed_seconds` and `estimated_remaining_seconds` fields; `started_at` is
  retained for timestamp display only.
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

Both repositories return the same internal item shape:

```python
{
    "session_id": session_id,
    "record": report_record,
    "session_summary": {
        "job_title": state["plan"].title,
        "job_tags": list(state["job_tags"]),
        "question_count": len(state["plan"].questions),
        "started_at": state["started_at"],
        "finished_at": state["finished_at"],
    },
}
```

The internal `session_summary` contains only allowlisted values. A full state,
JD, resume, message, or answer object never crosses the repository boundary
for report listing.

The in-memory implementation builds `session_summary` by looking up
`self._states[session_id]` while iterating the already-selected
`self._reports` entries. This is an in-process dictionary lookup, not an API
or repository query.

The PostgreSQL implementation performs one joined query:

```sql
SELECT reports.session_id,
       reports.status,
       reports.progress_json,
       reports.report_json,
       reports.error,
       reports.created_at,
       reports.completed_at,
       reports.failed_at,
       sessions.plan_json,
       sessions.job_tags,
       sessions.started_at,
       sessions.finished_at
FROM interview_reports AS reports
JOIN interview_sessions AS sessions
  ON sessions.session_id = reports.session_id
WHERE (%s IS NULL OR reports.status = %s)
ORDER BY reports.created_at DESC
LIMIT %s
```

The real table names continue to use the configured prefix through
`psycopg2.sql.Identifier`. The implementation may generate the conditional
`WHERE` clause as it does today; the SQL above fixes the required JOIN and
selected columns, not the string-construction mechanism. This avoids an N+1
session lookup.

`duration_seconds` is an integer computed only when both session timestamps
exist: `max(0, finished_at - started_at)`. It is `null` when either timestamp
is missing; it does not use the wall clock and therefore does not drift.

`report_path` comes only from `ReportProgress.metadata["report_path"]`. Its
public enum and labels are:

| Stored value | Public value | UI label |
| --- | --- | --- |
| `microbatch` | `microbatch` | Microbatch reuse |
| `full_session` | `full_session` | Full-session review |
| `full_session_fallback` | `full_session_fallback` | Full-session fallback |
| absent or unrecognized | `null` | Unavailable |

`microbatch_reuse` and `fallback_failed` are reference-demo labels, not domain
values, and must not be emitted by the API.

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

- `ReportJobQueue` in `app/ports/runtime.py` is extended with
  `requeue_failed(self, session_id: str) -> dict[str, Any]` so the existing
  PostgreSQL method is part of the runtime-checked port.
- A missing session or report job returns 404.
- A job that is not `failed` returns 409.
- A queue backend that is not configured returns 503.
- Success delegates to `ReportJobQueue.requeue_failed(session_id)`.
- Requeue resets both report and report-job status through the existing
  atomic PostgreSQL implementation.
- A second requeue request after success returns 409 and does not create a
  second job.

The route first verifies the session, resolves the queue, and reads the job.
It uses the following stable mapping:

| Condition or exception | HTTP | Stable detail |
| --- | --- | --- |
| `store.get(session_id)` reports missing | 404 | `interview session not found` |
| `queue.get_job_by_session(session_id)` returns `None` | 404 | `report job not found` |
| job is `queued`, `retrying`, or `running` | 409 | `report job is already queued or processing` |
| job is `completed` | 409 | `completed report cannot be requeued` |
| any other non-`failed` job state | 409 | `report job is not failed` |
| `requeue_failed()` raises `ValueError` after a status race | 409 | `report job is not failed` |
| queue resolution raises `RuntimeError` | 503 | `report queue is unavailable` |
| `requeue_failed()` returns a queued job | 202 | success payload shown above |

The second requeue observes `queued` and therefore uses the first 409 detail.
Unexpected database/programming failures are not converted to domain errors;
the existing bounded server-error handling applies and no raw exception is
returned.

Stage 43B already provides the safe `GET .../agent-runs` and
`GET .../runtime-events` endpoints and the PostgreSQL/CLI
`requeue_failed()` operation. This design reuses the two read APIs, adds the
missing protocol method, and adds only the HTTP requeue surface; it does not
redesign Stage 43B event replay.

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

### 6.1 Multi-Page Navigation

The reference file's `data-view`, `showView()`, `hashchange`, and shared
in-document variables are prototype-only and are not copied into production.
Production navigation uses full HTTP document loads:

| From | Destination |
| --- | --- |
| Global "Start interview" | `/prep` |
| Global "Report center" | `/reports` |
| Global "Help" | `/help` |
| Preparation start success | `/interview?session_id=<encoded-id>` |
| Interview finish/session completion | `/report-processing?session_id=<encoded-id>` |
| Report-processing completion | `/report-detail?session_id=<encoded-id>` |
| Completed report row | `/report-detail?session_id=<encoded-id>` |
| Processing report row | `/report-processing?session_id=<encoded-id>` |

Only `session_id` is transferred through the URL query string. Interview
state, report state, progress, questions, evaluations, and traces reload from
their public APIs after each document navigation.

Browser-local state is limited to:

- the existing interview draft ID;
- `interviewAnswerDraft:<session_id>:<question_id>` answer text;
- the current focus-mode preference for the active interview page.

No report result, provider response, answer history, or knowledge content is
passed through `localStorage`. Browser Back and refresh therefore reconstruct
the page from the URL plus persisted server state. Each page uses
`shared-ui.showNotice()` for its own status region; the prototype's global
cross-view `toast()` function is removed.

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
- `ReportJobQueue` runtime protocol includes `requeue_failed` and the
  PostgreSQL implementation satisfies it.
- PostgreSQL report listing uses one reports-to-sessions JOIN, while the
  in-memory implementation uses one bounded in-process cross-reference.
- `duration_seconds` is null without both timestamps and otherwise matches the
  finished-minus-started rule.
- `report_path` emits only `microbatch`, `full_session`,
  `full_session_fallback`, or null.
- Production HTML contains none of the demo values enumerated in Appendix A.

### 9.2 JavaScript and Browser Tests

- Text-file import populates the correct textarea and rejects invalid files.
- Focus mode enters, exits, and responds to Escape.
- Answer drafts survive refresh and version conflict, then clear on success.
- Existing prep, SSE answer, skip, finish, report, evidence, and PDF workflow
  remains green.
- Report search, status/date filters, pagination, direct PDF, and requeue work
  against deterministic browser support data.
- Navigation uses `/prep`, `/reports`, `/help`, and encoded `session_id` query
  strings; no production page uses reference hash routing.
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

The migration replaces one existing page at a time; it never routes production
traffic to the single-file hash SPA.

1. Move `app/interview-agent-single-file.html` unchanged to
   `docs/prototypes/interview-agent-single-file.html` and record its SHA-256
   in the implementation acceptance notes. The move freezes the visual source
   before production markup changes.
2. Add the visual-token/component layer to `prototype-source.css` without
   deleting classes used by old pages. Build CSS and prove both old and new
   selectors remain available during the transition.
3. For each page, first update static contract tests with the exact required
   IDs and landmarks, then replace that page's old markup in place with the
   corresponding reference view. Keep its existing production `<script>`
   entry and convert hash links to the HTTP routes in Section 6.1.
4. Audit every `byId()`/query selector in that page's module against the new
   markup before changing behavior. An ID is removed only in the same commit
   that updates all consumers and tests.
5. Complete pages in this order: preparation, live interview, report
   processing, report detail, report center. Each page receives focused static
   and browser tests before the next page starts.
6. Add `ReportJobQueue.requeue_failed`, joined report summaries, and the HTTP
   requeue endpoint before enabling the report-center requeue control.
7. Keep shared CSS additive until all five pages use the new shell. Remove old
   page-specific utilities only after a repository search proves no HTML/JS
   consumer remains.
8. Align `/help`, global navigation, screenshots, geometry checks, privacy
   checks, and full regression gates.

The mandatory DOM audit set is:

| Page module | IDs that must be mapped before replacement |
| --- | --- |
| `prep.js` | `jobDescription`, `resumeText`, `saveDraftButton`, `restoreDraftButton`, `prepButton`, `startButton`, `topicTags`, `planTitle`, `planQuestions`, `prepStatus`, `prepKnowledgeStatus`, `prepContextSummary`, `prepContextTopics`, `prepQuestionHints` |
| `interview.js` | `currentQuestion`, `conversation`, `answerForm`, `answerInput`, `interviewNotice`, `sendAnswerButton`, `skipQuestionButton`, `finishInterviewButton`, `sessionStatus`, `topicTags`, `questionPlan`, `toggleQuestionPlanButton` |
| `report-processing.js` | `reportProgressStatus`, `reportProgressBar`, `reportEvents`, `reportJobId`, `reportRagSummary`, `viewReportButton`, `processingNotice` |
| `report-detail.js` | `reportSummary`, `reportStatus`, `reportScore`, `reportScoreHint`, `reportScoreBadge`, four top dimension score IDs, `dimensionScores`, `reportHighlights`, `feedbackList`, `questionEvaluationStatus`, `questionEvaluationList`, `evidenceList`, `downloadReportButton`, `retryInterviewButton`, `reportCenterButton`, `reportNotice` |
| `report-center.js` | `refreshReportsButton`, `startNewInterviewButton`, `reportsStatus`, `reportsList` plus the new search, filter, pagination, download, and requeue controls |

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

## Appendix A. Reference Demo Cleanup Checklist

| Reference block/value | Production treatment | Source |
| --- | --- | --- |
| Example JD textarea | Start empty or restore the user's draft; retain only generic placeholder text | draft/local input |
| Example resume textarea | Start empty or restore the user's draft; retain only generic placeholder text | draft/local input |
| Hardcoded character counts/file names | Recompute after input or file import | browser input/file |
| Static recognized tags | Clear while idle; render returned job tags | prep/interview APIs |
| `InterviewPlan` title, 12 questions, three rounds | Clear while idle; render title/count/kind groups from the returned plan | prep API |
| `55-70 minutes` | Recompute with the documented four-to-six-minute estimate | returned question count |
| `knowledge base 8 items` and evidence titles | Render only returned bounded evidence references | public `PrepContext` |
| Static interview job/round/time | Render plan title, current question kind, and elapsed session time | session API |
| Static question list and state | Render every returned question and derived answer state | session API |
| Static interviewer/candidate messages | Remove from HTML; render snapshot and SSE messages | session/SSE APIs |
| Static current question/focus tags | Remove from HTML; render current question, focus, job tags, and safe evidence summaries | session API |
| `connection normal` | Show success only after the latest snapshot/command succeeds | request state |
| `answer saved` | Show local draft persistence state | `localStorage` write result |
| `round review 64%` | Delete simulated percent; render completed/failed evaluation counts | evaluations API |
| Static report progress `68%` | Initialize to queued/0 and render actual percent | progress API |
| Static Report Worker/Reviewer/Coach stages | Render only events/stages returned by progress | progress API |
| Static microbatch counts/fallback status | Render only allowlisted metadata when present | progress metadata |
| Static report job ID, job title, timestamps, duration | Render returned safe values or `--` | progress/session APIs |
| Static score `82`, dimension scores, badges | Clear while loading; render report scores | report API |
| `outperformed 78%` | Delete the block and its label entirely | no valid source |
| Static strengths, risks, skill pills | Derive from highlights, dimensions, and scored feedback | report API |
| Static per-question scores `88`/`74`, quotes, reviews | Remove from HTML; render evaluation and feedback records | report/evaluations APIs |
| Static evidence cards | Remove from HTML; render bounded public references | report API |
| Static report counts `8/6/1/1` | Recompute from the loaded report summaries | reports API |
| Every static report table row | Remove from HTML; render returned summaries | reports API |
| Demo `fallback_failed` path | Never emit; render the canonical path enum or unavailable | progress metadata |
| Static database/service health claims | Delete unless a public endpoint returns that exact state | runtime/progress APIs |
| Demo progress timer (`setInterval`) | Delete; polling updates only from server progress | progress API |
| Hash-router `showView()` and `data-view-target` | Delete; use the HTTP routes in Section 6.1 | standard navigation |
| Global demo `toast()` success messages | Replace with response-driven page notices | actual command result |
| `Help center coming soon` | Replace with a real `/help` link | existing page route |
| Fake file upload, focus, PDF, refresh, requeue buttons | Bind to the real behaviors specified in Sections 4 and 5 | browser/API actions |
