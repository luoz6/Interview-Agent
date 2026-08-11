# Interview Agent

Local V1 is a single-machine interview assistant for generating technical interview plans, running a mock interview, producing a RAG-backed evaluation report, and downloading a PDF report.

This project is designed for local single-user deployment. It does not include login, account isolation, or cross-device synchronization.

Memory, context-budget, compression, artifact, retention, and Interview graph
runtime settings are resolved through the immutable
`memory-runtime-config-v1` model. Prefer the structured `MEMORY_*` names shown
in `.env.example`. Legacy `INTERVIEW_LANGGRAPH_*`, `LLM_CONTEXT_*`,
`CONTEXT_BUDGET_*`, `CONTEXT_COMPRESSION_*`, and `CONTEXT_ARTIFACT_*` names are
accepted only by the compatibility adapter. Equal old/new values are allowed;
conflicting normalized values fail preflight. Rollout, budget enforcement, and
compression consumption remain disabled by default.

Repository-only memory acceptance is documented in
`docs/memory-system-optimization-acceptance.md` and can be run with
`python -m scripts.memory_system_optimization_acceptance`. Its successful
status is shadow readiness only; production observation remains `NOT_RUN`.

Files under `docs/superpowers/plans/` and `docs/superpowers/specs/` are frozen
implementation and design history, not current runbooks or command references.
Read the `README.md` in the corresponding archive before using an archived
plan or design snapshot.

## What Works

- Independent Vite/React frontend at `http://127.0.0.1:5173` with six routes:
  - `/prep`
  - `/interview?session_id=...`
  - `/report-processing?session_id=...`
  - `/report-detail?session_id=...`
  - `/reports`
  - `/help`
- FastAPI is API-only at `http://127.0.0.1:8000`; Vite proxies `/api` during development.
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

Stage 33 turns round_closed events into local asynchronous round review microbatches. The default `INTERVIEW_EVENT_BACKEND=local` uses `LocalRoundReviewEventPublisher` to schedule each closed question for Shadow Reviewer evaluation outside the direct answer response path, then persists a `QuestionEvaluationRecord` through the existing session store. `INTERVIEW_EVENT_BACKEND=noop` remains available for disabling runtime events, and `INTERVIEW_EVENT_BACKEND=celery` remains the external worker path. This stage does not add WebSocket or Redis checkpoints.

Stage 34 makes final report generation reuse completed round review microbatches. The report worker now loads completed `QuestionEvaluationRecord` rows in plan order, re-runs missing or failed question reviews before final aggregation, and sends microbatch feedback into Report Coach as report input. Report Coach does not overwrite Shadow Reviewer question scores; the final report keeps Report Coach summary/highlights while locking per-question feedbacks and scores to the microbatch records. If the microbatch set cannot be completed, `MicrobatchReportUnavailable` triggers fallback and the worker falls back to the full-session ShadowReviewerAgent path, so the final report remains authoritative.

Stage 35 makes the review pipeline observable. Report progress now carries `metadata` such as `report_path`, `microbatch_reused_questions`, `microbatch_rerun_questions`, and fallback reason fields so `/report-processing` can show whether the final report reused round-review microbatches or used `full_session_fallback`. Report trace files written through `REPORT_TRACE_DIR` record the same path choice for offline debugging, while existing `LocalRoundReviewEventPublisher.shutdown` lifecycle coverage protects local async review tasks during runtime shutdown.

Stage 37 cleans up the Postgres runtime contract. Memory and Postgres session stores now share the same versioned command behavior: mutating user commands accept `expected_version` plus `command_id`, stale commands raise `SessionVersionConflict` and return HTTP 409, duplicate `command_id` calls are idempotent, and snapshots expose `state_version`, `checkpoint_version`, `phase`, `phase_status`, and `review_status`. Streaming answer completion and report lifecycle updates advance version metadata without replacing the last user command id. The LangGraph orchestrator remains an internal phase router; Local V1 transport is still HTTP/SSE/polling.

## Durable Interview Recovery

The opt-in `langgraph-v1` engine applies only to newly assigned sessions.
PostgreSQL checkpoints own its workflow cursor; command inbox rows and runtime
outbox events provide resumable ingress and retry timers; generation chunks are
attempt-scoped and replayable over SSE.

Keep `INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0` until the recovery acceptance
record is complete. Reducing rollout later changes assignment for new sessions
only. Existing v1 graph definitions and workers must remain available for
already-created v1 sessions.

Final-report generation has an independent durable rollout. Keep
`REPORT_LANGGRAPH_ROLLOUT_PERCENT=0` until
`docs/langgraph-durable-review-acceptance.md` passes. New report jobs record an
immutable `legacy` or `langgraph-review-v1` assignment; requeue never changes
that assignment. The durable review graph reuses matching question projections,
bounds question fan-out and quality repair, and resumes provider retries from
the PostgreSQL checkpointer.

The two durable engines are released independently. Repository acceptance uses
the fixed `0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0` matrix to prove
assignment and rollback, but it leaves both committed rollout defaults at
zero. Rollback affects only new assignment; already assigned Interview and
Review threads keep their graph version, saver, consumers, and retry support.
Use `python -m scripts.langgraph_canary snapshot` for an aggregate read-only
view and `python -m scripts.langgraph_canary evaluate` for stable hold/rollback
reasons. See `docs/langgraph-dual-workflow-canary-acceptance.md` and the Dual
LangGraph Canary section of `docs/local-v1-runbook.md` before any deployed
canary.

Stage 47 upgrades this operator gate to `langgraph-canary-v2`. A deployed
evaluation requires an explicit phase and UTC phase start, validates the fixed
1% rollout pair, keeps Interview and Review sample minima independent, and
separates command-version conflicts from true projection divergence. Its
privacy-safe runtime signal buckets contain only minute, workflow category,
stable allowlisted code, and aggregate count. The CLI is read-only and never
changes deployment configuration.

## Prerequisites

- Python 3.11
- Node.js for static asset checks and Tailwind CSS build
- PostgreSQL on `127.0.0.1:5432`
- Database: `interview`
- PostgreSQL credentials supplied through the local process environment
- pgvector extension installed in the `interview` database

## Configure

PostgreSQL connection credentials are required configuration and are not built
into the code. Configure them in the local process or Windows user environment:

- `POSTGRES_DSN=postgresql://<user>:<password>@127.0.0.1:5432/interview`
- `PGVECTOR_TABLE=knowledge_chunks`
- `INTERVIEW_RUNTIME_STORE=postgres`
- `INTERVIEW_RUNTIME_TABLE_PREFIX=interview`

Set the connection value and any desired overrides before starting the runtime:

```powershell
$env:POSTGRES_DSN="postgresql://<user>:<password>@127.0.0.1:5432/interview"
$env:PGVECTOR_TABLE="knowledge_chunks"
$env:INTERVIEW_RUNTIME_STORE="postgres"
$env:INTERVIEW_RUNTIME_TABLE_PREFIX="interview"
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
$env:LLM_CONTEXT_WINDOW_TOKENS="128000"
```

The code reads `OPENAI_API_KEY` even when the provider is DeepSeek-compatible. For DeepSeek, get the key from `platform.deepseek.com` and put that value in `OPENAI_API_KEY`. Do not store real keys in git.

Remote embeddings are opt-in. `EMBEDDING_PROVIDER=disabled` is the default, so
Prep uses its existing degraded knowledge path and does not download a local embedding model.
Before enabling SiliconFlow after any credential exposure,
rotate the SiliconFlow key and set `SILICONFLOW_API_KEY` only through a secure
local process environment. The provider uses `BAAI/bge-m3`; the key must never
be written to `.env`, logs, screenshots, or Git.

## Install

```powershell
python -m scripts.reproducibility_preflight --python-only
python -m pip install --require-hashes -r requirements-windows.lock.txt
npm ci
```

## Load Knowledge

Use the existing `interview` PostgreSQL database and its pgvector extension;
do not create another database or container. Enable SiliconFlow explicitly,
set a release-specific model revision, and run versioned ingestion:
the module entry point is implemented in `scripts/load_knowledge.py`.

```powershell
$env:EMBEDDING_PROVIDER="siliconflow"
$env:EMBEDDING_MODEL_NAME="BAAI/bge-m3"
$env:EMBEDDING_MODEL_REVISION="siliconflow-bge-m3-20260721"
# Set SILICONFLOW_API_KEY through a secure local mechanism without displaying it.
python -m scripts.load_knowledge --corpus-version stage44a-bge-m3-v1
```

Expected result: one complete 25-unit release becomes active in
`knowledge_chunks_versions`/`knowledge_chunks_releases`. Activation is atomic;
provider or validation failure leaves the previous active release unchanged.
Reviewer `get_by_ids()` makes no embedding call and can replay retained evidence
by its bound content hash.

### Stage 44B1 Chinese Corpus RC

Stage 44B1 keeps the frozen v1 corpus root at `app/data/knowledge/` and the
Chinese v2 corpus root at `app/data/knowledge_v2/`. The roots, manifests, and
loaders remain isolated even though stable chunk IDs may be shared. All v2
natural-language corpus content and runtime retrieval queries are Chinese;
technical identifiers, code, and SQL may retain their official spelling.

The v2 corpus may use only the Chinese sources approved in
`docs/stage-44b1-chinese-source-matrix.md`. Its release candidate is loaded as
corpus `stage44b1-zh-v2` under the persistent isolated table prefix
`knowledge_chunks_stage44b_rc`. A clean RC load is expected to report
`embedded=25` and `reused=0`; an idempotent rerun may report `embedded=0` and
`reused=25`.

Stage 44B1 acceptance never promotes the RC to the production table prefix
automatically. Production promotion requires a separate explicit operator
approval after the acceptance record is complete.

## Start

Start the FastAPI API process:

```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
$env:LLM_CONTEXT_WINDOW_TOKENS="128000"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start the independent Vite/React frontend in a second PowerShell window:

```powershell
npm run dev:frontend
```

Start the report worker in a third PowerShell window. PostgreSQL mode queues report jobs, so `/report-processing` will stay in progress until this worker is running:

```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-chat"
$env:LLM_CONTEXT_WINDOW_TOKENS="128000"
python -m app.services.report_worker
```

Open:

```text
http://127.0.0.1:5173/prep
```

## Verify

On Windows, set `STAGE41_PYTHON` to the Python 3.11+ executable that has the
locked project dependencies installed before running Playwright. Keep the value
machine-local; do not hard-code a developer-specific path in the repository.
`npm run test:browser` uses the repository runner to own the Uvicorn child
process directly, so the server is also terminated reliably after the suite.

```powershell
$env:STAGE41_PYTHON="C:\path\to\python.exe"
```

```powershell
python -m pytest -q
npm run build:frontend
npm run test:browser
```

## Stage 41 Reproducible Release Checks

The mandatory H6 matrix is Windows 11 x64 or Ubuntu 24.04 LTS x64, Python
3.11.x, Node 22 LTS, PostgreSQL 16.x with the supported pgvector image, and the
Chromium revision selected by Playwright 1.61.1. Node.js 20 or 22 LTS remains
runtime-compatible, but Node 20 is not the primary H6 acceptance matrix. Other
operating systems, Python versions, and Node versions are `UNTESTED` and must
not be described as supported.

Activate a clean Python 3.11 virtual environment first. A wrong interpreter
fails with `PYTHON_VERSION_UNSUPPORTED`; an active-environment/executable
mismatch fails with `PYTHON_ENVIRONMENT_MISMATCH`. Platform locks and their
generation decision are documented in
`docs/local-v1-platform-locks-adr.md`. Every command below intentionally uses
the environment-independent `python`, `npm`, or `npx` executable name.

```powershell
python -m scripts.reproducibility_preflight --python-only
python -m pip install --require-hashes -r requirements-windows.lock.txt
python -m pip check
npm ci
npm --prefix frontend ci
npx playwright install chromium
python -m scripts.reproducibility_preflight --require-venv
npm run test:browser:preflight
python -m scripts.runtime_preflight --profile core
python -m scripts.init_local_runtime --check
npm run test:browser
python -m scripts.release_artifact_audit --profile stage40
```

On Ubuntu 24.04 LTS x64, use the Linux lock instead:

```bash
python -m scripts.reproducibility_preflight --python-only
python -m pip install --require-hashes -r requirements-linux.lock.txt
python -m pip check
npm ci
npm --prefix frontend ci
npx playwright install chromium
python -m scripts.reproducibility_preflight --require-venv
npm run test:browser:preflight
npm run build:frontend
npm run test:browser
```

`MEMORY_PRINCIPAL_TOMBSTONE_LEDGER_PATH` must be a host-native absolute local
`.jsonl` path outside the repository workspace. Use a drive-qualified path on
Windows and a POSIX-rooted path on Ubuntu. Relative paths, workspace paths,
UNC paths, and any symlink/junction traversal are rejected.

The default `local` event backend does not require Redis. To declare the optional
Celery profile healthy, configure an authenticated `REDIS_URL`, start the worker,
and run the persisted event acceptance:

```powershell
python -m celery -A app.services.celery_app.celery_app worker --loglevel=info --pool=solo
python -m scripts.runtime_preflight --profile celery
python -m scripts.celery_acceptance --timeout 150
```

Playwright uses deterministic test doubles for repeatable browser regression.
A fresh provider smoke remains a separate release gate; the saved Stage 40 real
model evidence may only support `PASS_WITH_PROVIDER_RECHECK` under the documented
external-provider failure policy.

Run real browser acceptance with `docs/local-v1-runbook.md` and record the result in `docs/stage-21-browser-e2e-acceptance.md`.

## Stage 43A Multi-Agent Runtime Audit

Stage 43A requires the Stage 42 knowledge continuity gate to already be PASS.
Enable sanitized Agent metadata traces and audit one correlation directory:

    $env:AGENT_TRACE_DIR="reports-local\agent-traces"
    python -m scripts.audit_agent_runtime $env:AGENT_TRACE_DIR

Agent traces contain metadata and IDs only. They do not contain prompts,
candidate answers, resumes, job descriptions, provider responses, secrets, or
absolute paths. Redis and WebSocket are not part of Stage 43A; Local event
delivery remains the default, while Celery is an optional acceptance profile.

## Stage 43B Durable Agent Recovery

PostgreSQL is the source of truth for runtime events, consumer receipts, Agent
run metadata, and report jobs. Redis and Celery provide transport and scheduling
only. In PostgreSQL Local mode the FastAPI lifespan runs one leased outbox
dispatcher. In Celery mode start the dispatcher separately:

    python -m app.services.runtime_outbox_worker

Inspect dead-letter work without exposing payloads or raw errors:

    python -m scripts.runtime_recovery list --status dead_letter
    python -m scripts.runtime_recovery replay-event --event-id <event-id>
    python -m scripts.runtime_recovery requeue-report --session-id <session-id>

The runtime control APIs are read-only. Recovery remains CLI-only.

## Current Non-Scope

- 不包含登录。
- 不包含多用户权限隔离。
- 不包含公网部署安全设计。
- 不包含 Docker Compose。
- 不包含知识库管理 UI。
Memory validation and Principal Memory foundation are implemented behind safe defaults. Long-term modes default to `disabled`; write/read shadow require explicit gates and consent; `consume` is rejected. This is repository shadow readiness only, not production rollout authorization.

## Local V1 Principal Memory evidence boundary

Local V1 is a trusted-local, default-off experiment. Principal Memory may
influence follow-up generation only. Score and report modules have no direct
Principal Memory dependency. No claim is made that changed interview
trajectories are causally equivalent. In particular, `learning_goal` and
`target_role_family` can change which follow-up is asked, and the resulting
later candidate answers can differ. This boundary is not evidence of fairness,
candidate safety, production readiness, or Hosted C1-A equivalence. Real-
candidate production use remains prohibited.

## Local V1 long-term-memory hardening closure

Local V1 hardening v0.4 is complete for the trusted-local, single-user,
default-off boundary. The immutable implementation accepted by the full
Windows/Ubuntu, PostgreSQL, frontend and browser matrix is revision
`e6b8f29d25276f17c874d07cebc15565bad37492`, tree
`354d3d0a1ad99bfef57fd51244d1f5358442c79f`.

The complete sanitized evidence is in
`docs/local-v1-hardening-acceptance.md`; the machine-readable publication
record is `docs/local-v1-hardening-manifest.json`; and the frozen Hosted V2
handoff is `docs/local-v1-hardening-handoff.md`.

```text
LOCAL_V1_IMPLEMENTATION=FEATURE_COMPLETE
LOCAL_V1_HARDENING=COMPLETE
LOCAL_V1_FINAL_ACCEPTANCE=PASS
LOCAL_V1_DEFAULT=DISABLED
LOCAL_V1_REAL_CANDIDATE_USE=PROHIBITED
REAL_PROVIDER_EVALUATION=NOT_RUN
HOSTED_V2=NO_GO_FOR_NOW
NEXT_REQUIRED_TASK=NONE
OPTIONAL_FUTURE_TRACK=HOSTED_PRODUCTIZATION_REDECISION
```

This status does not enable memory in committed configuration and does not
authorize production Shadow, a production canary, Hosted V2, real-candidate
processing, or real Provider evaluation. Safe rollback is to disable the mode
and all capability gates while retaining the ledger, tombstones and migrations.
