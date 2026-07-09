# Interview Agent

Local V1 is a single-machine interview assistant for generating technical interview plans, running a mock interview, producing a RAG-backed evaluation report, and downloading a PDF report.

This project is designed for local single-user deployment. It does not include login, account isolation, or cross-device synchronization.

## What Works

- Four runtime pages:
  - `http://127.0.0.1:8000/prep`
  - `http://127.0.0.1:8000/interview?session_id=...`
  - `http://127.0.0.1:8000/report-processing?session_id=...`
  - `http://127.0.0.1:8000/report-detail?session_id=...`
- DeepSeek/OpenAI-compatible plan generation, follow-up generation, and report generation.
- Structured-output first, raw JSON fallback for DeepSeek-compatible providers that reject `response_format`.
- PostgreSQL runtime persistence for sessions, report jobs, and reports.
- pgvector knowledge retrieval through `knowledge_chunks`.
- PDF report download.

## Current Architecture Position

Stage 23 keeps Postgres report jobs as the Local V1 async boundary while adding explicit agent boundaries and per-question evaluation records. Redis, Celery, WebSocket, and LangGraph remain future architecture upgrades rather than Local V1 runtime dependencies.

Report Detail shows per-question evaluation trace records. The visible trace chain is: `Report Worker -> ShadowReviewerAgent -> ReportCoachAgent -> QuestionEvaluationRecord -> Report Detail`.

Stage 25 Local V1 RC acceptance is the release gate before Stage 26 architecture work. It verifies the built-in local PostgreSQL defaults, worker-delayed report completion, service restart persistence, and the Report Detail question evaluation trace with the real browser flow.

Stage 26A adds an opt-in Redis/Celery round-review event backend. Closed interview rounds can be published as `round_closed` events and reviewed asynchronously during the interview. Interim round-review rows are merged by question id instead of session-wide replace, the Postgres final-report worker remains authoritative for the completed report, and the Local V1 UI remains final-report-first.

Stage 29 adds a LangGraph-powered orchestrator and a versioned HTTP resume contract. The runtime now tracks explicit phase metadata (`interview` / `review`), persists `state_version` plus `checkpoint_version`, accepts `expected_version` and `command_id` on mutating interview commands, and uses `GET /api/interviews/{session_id}` as the HTTP resume handshake. Transport remains SSE plus polling in Local V1; Stage 29 still does not add WebSocket or Redis checkpoints.

Stage 31 makes Knowledge Agent preheat visible during interview preparation. `/api/prep` now returns an optional `prep_context` with deterministic role topics, per-question follow-up hints, and evidence summaries derived from the JD, resume, and generated plan. This stage improves explainability of question selection and prepares a future Examiner hint path, but it does not add WebSocket or Redis checkpoints.

Stage 32 uses prep_context to guide follow-up generation. The interview graph now converts the current question's `prep_context.question_hints` into a `knowledge_agent` context message before calling the Examiner/LLM follow-up boundary, so generated follow-ups can target the role topics and evidence prepared during `/api/prep`. This improves continuity between preparation and live interview behavior, but it does not add WebSocket, Redis checkpoints, or a new persistence table.

## Prerequisites

- Python 3.11
- Node.js for static asset checks and Tailwind CSS build
- PostgreSQL on `127.0.0.1:5432`
- Database: `interview`
- PostgreSQL user/password: `postgres` / `postgres`
- pgvector extension installed in the `interview` database

## Configure

The local PostgreSQL defaults are built into the code:

- `POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/interview`
- `PGVECTOR_TABLE=knowledge_chunks`
- `INTERVIEW_RUNTIME_STORE=postgres`
- `INTERVIEW_RUNTIME_TABLE_PREFIX=interview`

Set environment variables only when you need to override those defaults or provide the LLM key:

```powershell
$env:POSTGRES_DSN="postgresql://postgres:postgres@127.0.0.1:5432/interview"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:INTERVIEW_RUNTIME_TABLE_PREFIX="interview"
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
```

The code reads `OPENAI_API_KEY` even when the provider is DeepSeek-compatible. For DeepSeek, get the key from `platform.deepseek.com` and put that value in `OPENAI_API_KEY`. Do not store real keys in git.

## Install

```powershell
& 'F:\python3.11\python.exe' -m pip install -r requirements.txt
npm install
```

## Load Knowledge

```powershell
& 'F:\python3.11\python.exe' scripts/load_knowledge.py
```

Expected result: `knowledge_chunks` contains theory and expert benchmark chunks, with 1024-dimension embeddings.

## Start

Start the FastAPI web process:

```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start the report worker in a second PowerShell window. PostgreSQL mode queues report jobs, so `/report-processing` will stay in progress until this worker is running:

```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
F:\python3.11\python.exe -m app.services.report_worker
```

Open:

```text
http://127.0.0.1:8000/prep
```

## Verify

```powershell
F:\python3.11\python.exe -m pytest -q
node --check app/static/api.js
node --check app/static/shared-ui.js
node --check app/static/prep.js
node --check app/static/interview.js
node --check app/static/report-processing.js
node --check app/static/report-detail.js
npm run build:prototype-css
```

Run real browser acceptance with `docs/local-v1-runbook.md` and record the result in `docs/stage-21-browser-e2e-acceptance.md`.

## Current Non-Scope

- 不包含登录。
- 不包含多用户权限隔离。
- 不包含公网部署安全设计。
- 不包含 Docker Compose。
- 不包含知识库管理 UI。
