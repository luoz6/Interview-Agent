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

Record the result in `docs/stage-21-browser-e2e-acceptance.md`.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Plan falls back to generic questions | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` |
| Report fails with knowledge store unavailable | `POSTGRES_DSN`, pgvector extension, and `knowledge_chunks` count |
| First report generation is slow | SentenceTransformer model loading and embedding cache warm-up |
| Static page is unstyled | Run `npm run build:prototype-css` |
| Browser cannot find session | Confirm URL contains `session_id` and runtime store did not reset |
