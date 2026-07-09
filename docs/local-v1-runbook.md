# Local V1 Runbook

This runbook verifies the local single-user Interview Agent runtime on Windows.

## 1. Environment

Expected local services:

| Item | Value |
| --- | --- |
| Python | `F:\python3.11\python.exe` |
| PostgreSQL | `127.0.0.1:5432` |
| Database | `interview` |
| User/password | `postgres` / `postgres` |
| pgvector table | `knowledge_chunks` |
| LLM provider | DeepSeek-compatible OpenAI API |

## 1.1 Architecture Position

Stage 23 keeps Postgres report jobs as the Local V1 async boundary while adding explicit agent boundaries and per-question evaluation records. This runbook continues to verify the local single-user runtime, not the future Redis/Celery/WebSocket/LangGraph deployment shape.

Report Detail shows per-question evaluation trace records. The visible trace chain is: `Report Worker -> ShadowReviewerAgent -> ReportCoachAgent -> QuestionEvaluationRecord -> Report Detail`.

Stage 25 Local V1 RC acceptance is the release gate before Stage 26 architecture work. It verifies the built-in local PostgreSQL defaults, worker-delayed report completion, service restart persistence, and the Report Detail question evaluation trace with the real browser flow.

Stage 26A adds an opt-in Redis/Celery round-review event backend. Closed interview rounds can be published as `round_closed` events and reviewed asynchronously during the interview. Interim round-review rows are merged by question id instead of session-wide replace, the Postgres final-report worker remains authoritative for the completed report, and the Local V1 UI remains final-report-first.

Stage 29 adds a LangGraph-powered orchestrator and a versioned HTTP resume contract. Local verification should now treat `GET /api/interviews/{session_id}` as the resume handshake and should pass `expected_version` plus a caller-generated `command_id` when retry-safe command behavior needs to be validated.

Stage 30 wires the browser interview page into the versioned HTTP resume contract. The frontend should read `state_version` from `GET /api/interviews/{session_id}`, send `expected_version` plus a browser-generated `command_id` on answer, skip, and finish commands, and recover from `409` conflicts by reloading the session snapshot instead of leaving stale UI state on screen.

Stage 31 makes Knowledge Agent preheat visible during interview preparation. Local verification should confirm `/api/prep` returns `prep_context.summary`, `prep_context.topics`, and `prep_context.question_hints`, and that the prep page renders those fields before the interview starts.

Stage 32 uses prep_context to guide follow-up generation. Local verification should confirm the first follow-up request can include a `knowledge_agent` context entry derived from `prep_context.question_hints`, while interviews without `prep_context` continue to use the plain transcript-only follow-up path.

Stage 33 turns round_closed events into local asynchronous round review microbatches. In the default local mode, a closed question should eventually appear from `GET /api/interviews/{session_id}/question-evaluations` as a `QuestionEvaluationRecord`. Use `INTERVIEW_EVENT_BACKEND=noop` only when runtime event side effects should be disabled, and use `INTERVIEW_EVENT_BACKEND=celery` when validating the external worker path.

Stage 34 makes final report generation reuse completed round review microbatches. Local verification should confirm completed `QuestionEvaluationRecord` rows from `GET /api/interviews/{session_id}/question-evaluations` are consumed by the final report worker, while missing or failed rows are re-reviewed before report completion. The final report keeps Report Coach summary/highlights but preserves Shadow Reviewer question scores from the microbatch rows. If microbatch reuse cannot complete, the worker falls back to the full-session ShadowReviewerAgent path.

Stage 35 makes the review pipeline observable. When `REPORT_TRACE_DIR` is set, local verification should confirm a `report_path` trace file is written for final report generation and includes either microbatch reuse counters or `full_session_fallback` with a fallback reason. The report-processing page should show the same metadata from `/api/interviews/{session_id}/report/progress`, and shutdown coverage should continue to call `LocalRoundReviewEventPublisher.shutdown` through FastAPI lifespan/runtime reset paths.

Stage 37 cleans up the Postgres runtime contract. Local verification should compare memory and Postgres behavior for `expected_version`, `command_id`, `state_version`, `checkpoint_version`, `phase_status`, and `review_status`. In Local V1, `checkpoint_version` mirrors `state_version` until an external checkpoint store exists. `last_command_id` is the last user command id; streaming completion and report lifecycle updates advance version metadata without overwriting it. A stale command should return HTTP 409 with the actual version, a duplicate command id should not append duplicate candidate messages, and service restart checks should confirm Postgres preserves version and phase metadata.

## 2. PowerShell Setup

Local PostgreSQL defaults are built into the code. Set these variables only when overriding the local defaults or providing the LLM key:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:INTERVIEW_RUNTIME_TABLE_PREFIX="interview"
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

## 3. Database Check

```powershell
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

- Database is `interview`.
- Current user is `postgres`.
- Extension row is `('vector',)`.
- `knowledge_chunks` count is greater than zero.

If `knowledge_chunks` is empty, run:

```powershell
F:\python3.11\python.exe scripts/load_knowledge.py
```

## 4. Start Server And Report Worker

Start the FastAPI web process:

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start the report worker in a second PowerShell window. PostgreSQL mode stores report generation requests in `interview_report_jobs`; without this worker, `/report-processing` will remain in progress:

```powershell
F:\python3.11\python.exe -m app.services.report_worker
```

Optional Stage 26A round-review worker:

```powershell
$env:INTERVIEW_EVENT_BACKEND="celery"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
celery -A app.services.celery_app.celery_app worker --loglevel=info
```

## 5. Automated Smoke

```powershell
F:\python3.11\python.exe -m pytest tests/test_page_routes.py tests/test_static_report_ui.py tests/test_local_v1_docs.py -q
F:\python3.11\python.exe -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

PowerShell 5.1 note: run each command separately instead of joining commands with `&&`.

Example versioned answer request:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/interviews/<session_id>/answer" `
  -ContentType "application/json" `
  -Body '{"answer":"I used Redis cache-aside.","expected_version":1,"command_id":"cmd-001"}'
```

## 6. 真实浏览器验收

1. Open `http://127.0.0.1:8000/prep`.
2. Enter a backend JD that mentions FastAPI, Redis, PostgreSQL, and system design.
3. Enter a resume that mentions a FastAPI service, Redis cache-aside, PostgreSQL indexes, and production troubleshooting.
4. Click generate plan.
5. Confirm job tags render and 3 to 5 questions render.
6. Save draft.
7. Refresh the page.
8. Restore draft and confirm JD/resume return.
9. Start interview.
10. Confirm the interview page loads with the first question.
11. Submit a streamed answer.
12. Confirm a follow-up or next question renders.
13. Skip one question.
14. Finish interview.
15. Confirm report-processing page shows progress.
16. Wait for report-detail page.
17. Confirm total score, five dimensions, feedback, and evidence excerpts render.
18. Confirm the `逐题评估链路` section renders at least one question evaluation record after report completion.
19. Download PDF and confirm the file opens.

Stage 30 versioned resume checks:

1. Open `/prep`, create an interview, and land on `/interview?session_id=...`.
2. Confirm `GET /api/interviews/{session_id}` returns `state_version`.
3. Submit a streamed answer and confirm the request payload includes `expected_version` and `command_id`.
4. Refresh `/interview?session_id=...` and confirm the latest messages and question state are restored.
5. Continue the interview after refresh and confirm the next mutating request uses the refreshed `state_version`.
6. Simulate or trigger a stale request that returns `409`, then confirm the page reloads `GET /api/interviews/{session_id}` and keeps the user's typed answer available for retry. Do not expect the page to auto-retry `skip` or `finish`; the intended behavior is refresh plus user retry.

Stage 31 Knowledge Agent preheat checks:

1. Generate a plan from a JD and resume that mention Redis, MySQL/PostgreSQL, FastAPI, and system design.
2. Confirm `/api/prep` returns `prep_context.summary`, at least one topic, and at least one question hint.
3. Confirm the prep page renders Knowledge Agent preheat topics and per-question follow-up hints.
4. Confirm starting the interview still works without requiring Redis, WebSocket, or a new persistence service.

Stage 32 knowledge-guided follow-up checks:

1. Generate a prep plan whose `prep_context.question_hints` includes Redis or FastAPI follow-up hints.
2. Start the interview and answer the matching question with a partial answer.
3. Confirm the follow-up remains grounded in the user's answer while targeting the preheated topic.
4. Confirm a session created from a plan without `prep_context` still produces a normal fallback or LLM follow-up.

Stage 33 round review checks:

1. Start an interview with the default `INTERVIEW_EVENT_BACKEND=local`.
2. Answer or skip enough turns to close one question.
3. Poll `GET /api/interviews/{session_id}/question-evaluations`.
4. Confirm the closed question eventually has one `QuestionEvaluationRecord`.
5. Confirm failed Shadow Reviewer execution is recorded as `status="failed"` instead of breaking the answer response.

Stage 34 final report microbatch reuse checks:

1. Start an interview with `INTERVIEW_EVENT_BACKEND=local`.
2. Answer or skip enough turns to close at least one question.
3. Poll `GET /api/interviews/{session_id}/question-evaluations` until a completed row appears.
4. Finish the interview and run the report worker.
5. Confirm the final report completes and the question evaluation rows remain available.
6. Confirm a session with a failed or missing microbatch row still completes by re-reviewing the question or falling back to the full-session ShadowReviewerAgent path.

Stage 35 review pipeline observability checks:

1. Set `REPORT_TRACE_DIR` to a temporary directory.
2. Finish an interview that already has at least one completed `QuestionEvaluationRecord`.
3. Poll `/api/interviews/{session_id}/report/progress` and confirm `metadata.report_path` is `microbatch`.
4. Confirm the progress metadata includes `microbatch_reused_questions` and `microbatch_rerun_questions`.
5. Force or simulate a microbatch-unavailable path and confirm progress or trace metadata records `full_session_fallback`.
6. Stop the FastAPI process and confirm runtime shutdown does not leave local round-review executor errors in logs.

Stage 37 Postgres runtime contract checks:

1. Start an interview and call `GET /api/interviews/{session_id}`.
2. Confirm the snapshot includes `state_version`, `checkpoint_version`, `phase`, `phase_status`, and `review_status`.
3. Send an answer with stale `expected_version` and confirm HTTP 409.
4. Send the same `command_id` twice and confirm the second call does not duplicate candidate messages.
5. Submit a streaming answer and confirm completion advances `state_version` while preserving the original `last_command_id`.
6. Finish an interview, trigger report processing, and confirm report lifecycle updates do not replace the last user command id.
7. Repeat the version/idempotency checks with `INTERVIEW_RUNTIME_STORE=postgres`.
8. Restart the store or process and confirm Postgres still returns the latest version and phase metadata.

Record the result in `docs/stage-21-browser-e2e-acceptance.md`.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Plan falls back to generic questions | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` |
| Report fails with knowledge store unavailable | `POSTGRES_DSN`, pgvector extension, and `knowledge_chunks` count |
| First report generation is slow | SentenceTransformer model loading and embedding cache warm-up |
| Static page is unstyled | Run `npm run build:prototype-css` |
| Browser cannot find session | Confirm URL contains `session_id` and runtime store did not reset |
