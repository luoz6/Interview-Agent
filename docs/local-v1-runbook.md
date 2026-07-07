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

Record the result in `docs/stage-21-browser-e2e-acceptance.md`.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Plan falls back to generic questions | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` |
| Report fails with knowledge store unavailable | `POSTGRES_DSN`, pgvector extension, and `knowledge_chunks` count |
| First report generation is slow | SentenceTransformer model loading and embedding cache warm-up |
| Static page is unstyled | Run `npm run build:prototype-css` |
| Browser cannot find session | Confirm URL contains `session_id` and runtime store did not reset |
