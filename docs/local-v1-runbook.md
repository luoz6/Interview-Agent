# Local V1 Runbook

This runbook verifies the local single-user Interview Agent runtime on Windows.

## 1. Environment

Expected local services:

| Item | Value |
| --- | --- |
| Python | Python 3.11 from an activated virtual environment |
| Node.js | Node.js 20 or 22 LTS |
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

## 1.2 “我的资料”操作路径

“我的资料”是本地 Principal 拥有的文件库，不是维护者发布的全局 Knowledge Corpus。启用资料读取/选择与新资料摄取需要两个独立 capability：

```powershell
$env:USER_MATERIALS_ENABLED="true"
$env:USER_MATERIALS_INGEST_ENABLED="true"
```

`USER_MATERIALS_ENABLED` 控制 `/materials`、准备页选择和运行时检索参与；`USER_MATERIALS_INGEST_ENABLED` 只控制上传、重试和新 Revision。两者都不会授予 Corpus create、activate、publish 或 retire 权限。

本地操作顺序：

1. 打开 `/materials`，上传 UTF-8 Markdown 或 TXT。单个文件上限为 1 MiB；当前不接受 PDF、DOCX、图片或 OCR。
2. 等待状态变为“已就绪”。“处理中”需要等待，“处理失败”可以重试，“已停用”需要先重新启用。只有 `Ready + Enabled` 的资料可供选择。
3. 打开 `/prep`，选择本次允许使用的资料并确认计划。服务端把 Revision、内容摘要和允许用途固化到 Plan；首次 Start 会以同一个 owner 重新验证，再把同一 Scope 固化到 Session。成功启动后的恢复与回放读取冻结绑定，不从当前资料库重新推断选择。
4. 完成面试后，在报告中查看实际 Citation。被选择只表示允许使用；只有同时进入 Final Evidence、业务绑定并被反馈实际消费的资料才显示为“我的资料”。删除资料后，历史报告只显示不含标题和摘录的“已删除资料”。

Source Scope 只是 Semantic 与 Lexical 两个现有检索通道内的候选约束，不是第三个 Fusion 输入。用户资料是非权威的提问、追问和反馈上下文，不会改变 rubric、权重、及格线或数值评分；没有 Citation 也不代表自动扣分。

Local V1 由服务端 Local Principal 提供 owner 边界，不提供账号、登录、租户、团队或 RBAC。本节可用 InMemory/Fake 自动化验证产品契约，但真实 PostgreSQL 的 FK、索引、级联和事务恢复仍需单独授权验收；不得把非保护回归记为该实库验收已经通过。

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

Remote embeddings are explicitly disabled by default:

```text
EMBEDDING_PROVIDER=disabled
```

This mode keeps Prep's degraded knowledge fallback available and does not download a local embedding model.
Before a SiliconFlow run, rotate the SiliconFlow key
and supply `SILICONFLOW_API_KEY` only through a secure local process environment.
Never place the value in `.env`, logs, screenshots, or Git.

## 3. Database Check

Use the existing `interview` PostgreSQL database; do not create a new database
or container. The read-only check validates pgvector, the runtime tables, both
derived knowledge tables, and the active release:

```powershell
python -m scripts.init_local_runtime --check
```

Expected:

- Database is `interview`.
- Current user is `postgres`.
- `vector_extension` is `true`.
- `required_knowledge_tables` contains the versions and releases tables.
- `knowledge_corpus_version` is the active release, or `null` before ingestion.

To activate the Stage 44A corpus, configure the non-secret provider identity,
set the rotated key securely, and run:

```powershell
$env:EMBEDDING_PROVIDER="siliconflow"
$env:EMBEDDING_MODEL_NAME="BAAI/bge-m3"
$env:EMBEDDING_MODEL_REVISION="siliconflow-bge-m3-20260721"
# Set SILICONFLOW_API_KEY through a secure local mechanism without displaying it.
python -m scripts.load_knowledge --corpus-version stage44a-bge-m3-v1
```

For an explicitly approved remote acceptance run, keep the same provider
identity, set `RUN_SILICONFLOW_ACCEPTANCE=1`, and use the unified profile CLI:

```powershell
python -m scripts.knowledge_acceptance stage44a --run-id <run-id> --run-dir <run-dir>
python -m scripts.release_artifact_audit --profile stage44a --run-id <run-id> --run-dir <run-dir>
```

The loader prepares all vectors before the activation transaction. Reviewer `get_by_ids()` makes no embedding call
and resolves bound historical evidence by content hash.

## 4. Start API, Frontend And Report Worker

Start the FastAPI API-only process:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Start the independent Vite/React frontend in a second PowerShell window:

```powershell
npm run dev:frontend
```

Vite serves the client routes on `http://127.0.0.1:5173` and proxies `/api` to the FastAPI process on port `8000`.

Start the report worker in a third PowerShell window. PostgreSQL mode stores report generation requests in `interview_report_jobs`; without this worker, `/report-processing` will remain in progress:

```powershell
python -m app.services.report_worker
```

Optional Stage 26A round-review worker:

```powershell
$env:INTERVIEW_EVENT_BACKEND="celery"
$env:REDIS_URL="redis://127.0.0.1:6379/0"
celery -A app.services.celery_app.celery_app worker --loglevel=info
```

## 5. Automated Smoke

```powershell
python -m pytest tests/acceptance/test_page_routes.py tests/architecture/test_frontend_runtime.py -q
python -m pytest -q
npm run build:frontend
npm run test:browser
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

1. Open `http://127.0.0.1:5173/prep`.
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

Stage 39 browser RC checks:

1. Confirm `/prep`, `/interview`, `/report-processing`, and `/report-detail` show readable Chinese with no mojibake in navigation, buttons, notices, empty states, and report sections.
2. Confirm the browser answer flow still sends `expected_version` and `command_id`.
3. Trigger or simulate a stale `expected_version` and confirm the page shows `会话状态已刷新，请检查最新题目后继续。`.
4. Confirm report-processing shows readable progress metadata and does not show `暂无生成事件。` when metadata details are present.
5. Confirm report-detail shows `逐题评估链路` with at least one question evaluation record.
6. Download the PDF and confirm the browser keeps the report visible after download.

Record the result in `docs/stage-21-browser-e2e-acceptance.md`.

## 7. Stage 40 Scoring Trust Loop

Stage 40 real-model acceptance runs 20 cases twice: **40 target attempts**. `--max-provider-invocations 50` is a separate retry-inclusive provider budget. Generated artifacts must never contain the API key.

```powershell
python -m scripts.evaluate_report_quality --case-id redis-cache-consistency-strong --runs-per-case 2 --max-provider-invocations 4
python -m scripts.evaluate_report_quality --group-id redis-cache-consistency --runs-per-case 2 --max-provider-invocations 12
python -m scripts.evaluate_report_quality --runs-per-case 2 --max-provider-invocations 50
python -m scripts.evaluate_report_quality --resume --run-id <printed-run-id> --max-provider-invocations 50
```

Exit codes are `0` for complete PASS, `1` for complete gate/assertion failure, and `2` when the provider budget is exhausted with attempts pending. Gates: `ranking_accuracy >= 0.85`, `evidence_grounding_rate >= 0.90`, `score_delta <= 8`, and `fallback_rate <= 0.05`. Blocking assertions cover zero-scored negligible answers, forbidden claims, applicable dimensions, aggregate recomputation, and ignored provider-supplied score fields.

Keep the run directory, `metrics.json`, attempt artifacts, traces, hashes, model and version metadata. Record them in `docs/stage-40-real-model-acceptance.md` without secrets. Normal `pytest` remains offline; Stage 40 acceptance requires a complete real-model run returning exit code `0`. The record remains `PENDING` until then.

## 8. Troubleshooting

| Symptom | Check |
| --- | --- |
| Plan falls back to generic questions | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` |
| Report fails with knowledge store unavailable | `POSTGRES_DSN`, pgvector extension, derived tables, and active corpus version |
| Knowledge retrieval is degraded | `EMBEDDING_PROVIDER`, active corpus version, and SiliconFlow availability |
| React page is unavailable | Confirm `npm run dev:frontend` is running on port `5173` |
| Production frontend build fails | Run `npm run build:frontend` and inspect Vite output |
| Browser cannot find session | Confirm URL contains `session_id` and runtime store did not reset |

## 9. Stage 41 Clean-Environment Release Gate

The mandatory H6 support matrix is Windows 11 x64 and Ubuntu 24.04 LTS x64,
Python 3.11.x, Node 22 LTS, PostgreSQL 16.x with the supported pgvector image,
and Playwright 1.61.1 Chromium. Node.js 20 or 22 LTS remains runtime-compatible,
but Node 20 is not a primary H6 acceptance environment. Every other platform or
version is `UNTESTED`.

From a fresh Windows checkout, create and activate a Python 3.11 virtual
environment, then run:

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
python -m scripts.init_local_runtime
python -m scripts.init_local_runtime --check
npm run build:frontend
npm run test:browser
python -m pytest -q
python -m scripts.release_artifact_audit --profile stage40 --run-dir <stage40-run-dir> --run-id <printed-run-id>
```

On Ubuntu 24.04 LTS x64, run the same sequence with the Linux lock:

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
python -m pytest -q
```

`PYTHON_VERSION_UNSUPPORTED` means the selected interpreter is not Python
3.11. `PYTHON_ENVIRONMENT_MISMATCH` means the active virtual environment and
executable do not match, or a clean-environment command was run globally. Stop
before installing or testing and rebuild the environment. The pinned generator,
platform-lock decision, and source/hash binding are recorded in
`docs/local-v1-platform-locks-adr.md` and `requirements.lock.meta.json`.

`MEMORY_PRINCIPAL_TOMBSTONE_LEDGER_PATH` must use a host-native absolute local
path outside the repository: a drive-qualified `.jsonl` path on Windows or a
POSIX-rooted `.jsonl` path on Ubuntu. Relative, workspace-contained, UNC,
symlink, and junction paths fail closed.

`python -m scripts.init_local_runtime --check` is read-only. Use
`python -m scripts.init_local_runtime --seed-knowledge --corpus-version stage44a-bge-m3-v1`
only when the knowledge corpus must be loaded. Use a unique
`INTERVIEW_RUNTIME_TABLE_PREFIX` and
`PGVECTOR_TABLE` for acceptance runs.

The optional Redis/Celery profile requires both the Redis data-path smoke and a
persisted `round_closed` event:

```powershell
$env:REDIS_URL="redis://:your-password@127.0.0.1:6379/0"
python -m scripts.runtime_preflight --profile celery
python -m celery -A app.services.celery_app.celery_app worker --loglevel=info --pool=solo
python -m scripts.celery_acceptance --timeout 150
```

Do not claim Celery support unless both commands pass. The default core profile
uses `INTERVIEW_EVENT_BACKEND=local` and remains usable when Redis is unavailable.
Playwright regression is deterministic and offline; provider smoke is recorded
separately. Saved real-model evidence is valid for 30 days, and can only produce
`PASS_WITH_PROVIDER_RECHECK` after a documented external API failure. A final
release still requires a fresh provider smoke.

## 10. Stage 42 Knowledge Continuity Gate

Run the deterministic browser gate first:

```powershell
npm run test:browser
```

The opt-in real-model browser gate starts its own isolated Uvicorn process and
report worker. It uses the `stage42_real_browser` PostgreSQL table prefix unless
`STAGE42_REAL_BROWSER_PREFIX` is set:

```powershell
$env:RUN_REAL_BROWSER_SMOKE="1"
npx playwright test --config=playwright.real.config.js
```

Compatible-provider RC runs default to a 75-second request timeout, zero SDK
retries, and `raw_only` report output inside the Playwright configuration. Set
`OPENAI_REQUEST_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`, or
`OPENAI_REPORT_OUTPUT_MODE` explicitly to override those RC defaults.

After a passing run, populate only the Stage 42 release whitelist and audit it:

```powershell
python -m scripts.release_artifact_audit --profile stage42 --run-dir reports/stage42-acceptance/<run-id> --run-id <run-id> --write-manifest
```

The accepted directory contains only `manifest.json`, `metrics.json`,
`report.md`, `retrieval-cases/**`, and `browser/**`. Do not create a passing
manifest when the real-model browser gate has not passed.

## 11. Stage 43A Multi-Agent Runtime Gate

Stage 42 must already be PASS before declaring this gate. Enable Agent tracing
for a deterministic or local runtime:

    $env:AGENT_TRACE_DIR="reports-local\agent-traces"
    npm run test:browser
    python -m scripts.audit_agent_runtime $env:AGENT_TRACE_DIR

Audit the single directory named by the persisted plan's prep_run_id, rather
than a root that may contain abandoned Prep previews. Agent traces contain
metadata and IDs only; they must not contain prompts, answers, resume or job
description text, knowledge content, provider responses, secrets, DSNs, or
absolute paths.

Redis and WebSocket are not part of Stage 43A. The default Local event publisher
and the optional Celery publisher must preserve the same runtime-event-v1
envelope. Celery support may be declared only after the authenticated Celery
preflight and persisted event acceptance pass.

### Stage 47.2 Agent Runtime Telemetry Hardening

Run the focused privacy and composition gate:

```powershell
python -m pytest -q `
  tests/unit/test_agent_runtime.py `
  tests/unit/test_agent_runtime_hardening.py `
  tests/unit/test_agent_runtime_composition.py `
  tests/contracts/test_agent_runtime_audit.py `
  tests/contracts/test_agent_runtime_release_contract.py
```

Run the mandatory PostgreSQL gate with the local/test DSN:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
python -m pytest -q `
  tests/integration/postgres/test_agent_recorders.py `
  tests/integration/postgres/test_agent_runtime_metrics_postgres.py
```

Run the machine acceptance:

```powershell
$env:POSTGRES_DSN='postgresql://postgres:postgres@127.0.0.1:5432/interview'
python -m scripts.repository_acceptance stage47_2
```

`completed`, `degraded`, `failed`, and `cancelled` remain the public AgentRun
states. Metadata sanitization or recorder failure never changes the business
result. Safe warnings use `agent_metadata_extraction_failed`,
`agent_outcome_classification_failed`, `agent_metadata_sanitized`, or
`agent_run_emission_failed` and must not contain exception messages.

`safe_metadata` is persisted for internal diagnostics but is intentionally
excluded from public AgentRun APIs and browser pages. Operation aggregates
contain only Agent, operation, counts, rates, latency percentiles, and bounded
timestamps.

Before accepting the gate, verify temporary `test_agent_*` PostgreSQL tables,
trace directories, Playwright artifacts, and owned server processes have
been removed. Production observation remains `NOT_RUN`; committed Interview
and Review rollout defaults remain `0/0`.

## 12. Stage 43B Durable Agent Recovery

PostgreSQL is the source of truth. Redis and Celery are transport and scheduling
only; they are not completion ledgers. Configure bounded leases:

    RUNTIME_OUTBOX_BATCH_SIZE=20
    RUNTIME_OUTBOX_LEASE_SECONDS=60
    RUNTIME_OUTBOX_POLL_SECONDS=0.5
    RUNTIME_OUTBOX_MAX_ATTEMPTS=5
    RUNTIME_RECEIPT_LEASE_SECONDS=300

PostgreSQL plus Local starts one dispatcher from FastAPI lifespan. PostgreSQL
plus Celery requires both workers:

    python -m celery -A app.services.celery_app.celery_app worker --loglevel=info --pool=solo
    python -m app.services.runtime_outbox_worker

Preflight and recovery commands:

    python -m scripts.runtime_preflight --profile runtime
    python -m scripts.runtime_recovery list --status dead_letter
    python -m scripts.runtime_recovery replay-event --event-id <event-id>
    python -m scripts.runtime_recovery requeue-report --session-id <session-id>

Recovery commands expose stable IDs, statuses, attempts, timestamps, and error
codes only. They do not expose event payloads, Agent safe metadata, candidate
text, raw provider errors, leases, paths, or connection configuration.

## 13. Stage 44B1 Chinese Corpus RC

Stage 44B1 preserves `app/data/knowledge/` as the frozen v1 root and loads the
Chinese v2 corpus only from `app/data/knowledge_v2/`. Do not point the v1 loader
at the v2 root or regenerate the v1 manifest. All v2 natural-language corpus
content and runtime retrieval queries must be Chinese. Technical identifiers,
code, and SQL may keep their official spelling.

Corpus authors may use only sources already approved in
`docs/stage-44b1-chinese-source-matrix.md`. A source addition or replacement
requires a new source review before the corresponding corpus content changes.

Use the persistent isolated RC prefix and fixed corpus identity:

```powershell
$env:PGVECTOR_TABLE=knowledge_chunks_stage44b_rc
python -m scripts.load_knowledge_v2 --corpus-version stage44b1-zh-v2
```

The first load into a clean RC prefix is expected to report `embedded=25` and
`reused=0`. An idempotent rerun against the retained RC tables may report
`embedded=0` and `reused=25`. In both cases, `embedded + reused` and the active
chunk count must equal 25.

Run the Stage 44B1 acceptance runner and artifact auditor only after the
deterministic and PostgreSQL gates are green. Keep the RC on
`knowledge_chunks_stage44b_rc`: the runner must never change the production
table prefix or promote `stage44b1-zh-v2` automatically. Production promotion
requires separate explicit operator approval after
`docs/stage-44b1-chinese-corpus-acceptance.md` is complete.

```powershell
python -m scripts.knowledge_acceptance stage44b1 --run-id <run-id> --run-dir <run-dir>
python -m scripts.release_artifact_audit --profile stage44b1 --run-id <run-id> --run-dir <run-dir>
```

# Local V1 Runbook

## Durable Interview Recovery

Use PostgreSQL runtime mode with the LangGraph runtime enabled. Keep rollout
at zero while validating the recovery acceptance record, then increase it
gradually for newly created sessions.

The durable graph persists command identity separately from answer content,
waits on retry timers without blocking a worker, and streams generation chunks
by `(generation_id, attempt_number, sequence)`. A replacement attempt starts
with `generation_reset`; clients must clear the old partial text.

Rollback changes only assignment for new sessions. Workers serving existing
`langgraph-v1` threads and the corresponding graph version must remain
available until those sessions finish or are purged.

Completed generation chunks are retained for 24 hours for reconnect and audit,
then cleanup may remove them. Active and retrying generations are never removed
by retention cleanup.

See `docs/langgraph-interview-recovery-acceptance.md` for the release gates.

## Durable Review Recovery

Durable final-report execution is assigned independently from the interview
workflow. Use these safe initial values:

```text
REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
REPORT_LANGGRAPH_RUNTIME_ENABLED=true
REPORT_LANGGRAPH_VERSION=langgraph-review-v1
REPORT_LANGGRAPH_MAX_PARALLEL_QUESTION_REVIEWS=3
REPORT_LANGGRAPH_MAX_PROVIDER_ATTEMPTS=3
REPORT_LANGGRAPH_MAX_QUALITY_REPAIRS=2
```

Each assigned job uses thread ID `review:{job_id}`. Question evaluations are
reused only when their input, evidence and graph provenance matches. Provider
retry timers are delivered through the runtime outbox; quality repairs are
bounded separately. Reducing rollout to zero affects only new report jobs, so
workers and the `langgraph-review-v1` graph must stay available for already
assigned jobs.

See `docs/langgraph-durable-review-acceptance.md` for the release gates.

## Dual LangGraph Canary and Assignment-Only Rollback

Do not start a deployed canary until all three repository records are ready:

- `docs/langgraph-interview-recovery-acceptance.md` is `PASS`;
- `docs/langgraph-durable-review-acceptance.md` is `PASS`;
- `docs/langgraph-dual-workflow-canary-acceptance.md` is
  `PASS` for the authorized Local V1 synthetic scope;
- `docs/langgraph-stage46-acceptance.md` is
  `READY_FOR_FENCING_CANARY`;
- `docs/langgraph-stage47-fencing-canary-acceptance.md` is
  `READY_FOR_OPERATOR_FENCING_CANARY`.

The separate
`docs/langgraph-stage47-fencing-canary-observation.md` remains `NOT_RUN` until
an operator explicitly authorizes a deployed environment and revision.

The target environment must use PostgreSQL, strict msgpack, both registered
graph versions, healthy Interview/Review consumers, and the durable maintenance
service. Supply `POSTGRES_DSN` through deployment secret management and run:

```powershell
$env:INTERVIEW_RUNTIME_STORE='postgres'
python -m scripts.repository_acceptance stage47
python -m scripts.runtime_preflight --profile core
python -m scripts.langgraph_canary snapshot `
  --phase baseline `
  --since-utc <BASELINE_START_UTC> `
  --window-minutes 60
```

The initial canary sequence is fixed and requires an explicit hold-point
decision between phases:

```text
0/0 -> 1/0 -> 0/0 -> 0/1 -> 0/0 -> 1/1 -> 0/0
```

The first value is `INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT`; the second is
`REPORT_LANGGRAPH_ROLLOUT_PERCENT`. At every phase:

1. run core preflight before applying configuration;
2. apply only the two assignment percentages;
3. capture a sanitized canary snapshot;
4. observe until both the operator-defined minimum duration and sample are met;
5. run `python -m scripts.langgraph_canary evaluate` with the exact phase and
   UTC phase start;
6. explicitly hold, roll back, or continue;
7. return to `0/0` between the independent Interview and Review experiments;
8. prove already assigned durable work still finishes after rollback.

Correctness and privacy stop signals—including acknowledged command loss,
duplicate business projection, public version regression, and unknown graph
version—are supplied only as stable codes:

```powershell
python -m scripts.langgraph_canary evaluate `
  --phase interview `
  --since-utc <PHASE_START_UTC> `
  --window-minutes 60 `
  --minimum-interview-sample <APPROVED_SAMPLE> `
  --minimum-review-sample 0 `
  --output-dir <EMPTY_SANITIZED_PHASE_DIRECTORY> `
  --external-stop-signal acknowledged_command_loss
```

The first Stage 47 phase/rollout pairs are fixed:

```text
baseline         0/0
interview        1/0
interview_drain  0/0
review           0/1
review_drain     0/0
joint            1/1
final_drain      0/0
```

The evaluator rejects a mismatched phase/rollout pair. Interview and Review
sample minima are independent; one workflow cannot satisfy the other
workflow's sample gate. A phase must meet both its approved minimum duration
and its workflow-specific sample requirement.

Correctness/privacy conflicts recommend `ROLL_BACK`. Lock/lease loss, fenced
write rejection, expired ownership, excessive live-owner busy signals,
backlog, or stale work recommend `HOLD`. A running Review effect with a live
claim is informational; an effect claim older than the lease grace is a hold
signal.

Stage 47.1 treats a background heartbeat renewal exception as an inability to
prove ownership. `generation_lease_lost`, `report_lease_lost`, and
`fenced_write_rejected` can therefore mean either that another owner replaced
the worker or that PostgreSQL renewal could not verify the existing owner. In
both cases, hold promotion and investigate in this order:

1. PostgreSQL availability and connection saturation;
2. worker restart or replacement events;
3. expired lease/claim counts after the configured grace period;
4. privacy-safe runtime signal bucket counts;
5. unfinished Outbox and Report Job age;
6. fenced-write rejection counts;
7. active worker topology.

Do not paste a DSN, workflow identifier, lease token, fencing version,
checkpoint content, provider payload, or raw exception message into the
operator observation. Review Effect claim renewal loss is represented by the
catch-compatible `ReviewEffectLeaseLost` subtype and the existing
`fenced_write_rejected` stable code. Under Review v1 this remains terminal for
the current Job; question-level retry is deferred to Review v2.

The command is read-only. It returns `ROLL_BACK`, `HOLD`, or
`ELIGIBLE_TO_CONTINUE`; it never changes deployment configuration.

Rollback sets the affected new-assignment percentage to zero. It must not:

- disable either durable runtime while assigned work exists;
- stop the PostgreSQL saver or runtime consumers;
- unregister `langgraph-v1` or `langgraph-review-v1`;
- rewrite `workflow_engine`, `review_engine`, or graph-version columns;
- requeue Durable work as Legacy;
- purge active checkpoints, generations, commands, chunks, or Review artifacts.

Keep these committed defaults:

```text
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
INTERVIEW_LANGGRAPH_RUNTIME_ENABLED=true
REPORT_LANGGRAPH_RUNTIME_ENABLED=true
DURABLE_WORKFLOW_MAINTENANCE_SECONDS=3600
LANGGRAPH_CANARY_SIGNAL_RETENTION_HOURS=168
```

Repository acceptance does not authorize a deployed 1% canary. Record deployed
observations only after the operator explicitly identifies the environment and
authorizes configuration changes.

## Stage 48 PostgreSQL Connection Ownership and Capacity

Runtime processes use four separately bounded connection domains:

```text
Checkpointer  psycopg3 ConnectionPool / PostgresSaver
Business      psycopg2 bounded transaction pool
Lock          psycopg2 session-exclusive advisory-lock pool
Telemetry     psycopg2 bounded best-effort telemetry pool
```

`PostgresConnectionDomains` is the process-local owner. Stores borrow a
provider and must never close it. A lost advisory-lock session is discarded;
Telemetry exhaustion cannot consume Business capacity. Runtime startup never
runs DDL and `PostgresSaver.setup()` is migration-only.

Before starting a fresh or upgraded runtime, keep Interview and Review rollout
at `0/0`, drain deployed workers for the prefix, and inspect the migration
plan without connecting:

```powershell
python -m scripts.postgres_runtime_migrate
```

Only an operator-authorized maintenance window may apply a deployed migration:

```powershell
python -m scripts.postgres_runtime_migrate --apply
python -m scripts.runtime_preflight --profile core
python -m scripts.postgres_capacity_acceptance
```

Repository acceptance uses an isolated validated prefix for `--apply`. It must
delete only that prefix after the capacity run. Never use an existing business
prefix for repository rehearsal.

The capacity artifact schema is `postgres-capacity-v1`. It contains aggregate
limits, peaks, waits and counts only. It must not contain a DSN, host, database,
user, client address, backend PID, SQL text, workflow identity, token or
payload. `ELIGIBLE_FOR_CAPACITY_CANARY` is repository evidence, not deployed
PASS. Production observation remains `NOT_RUN` until an operator authorizes a
specific environment, revision, capacity budget and observation window.

The accepted capacity run must contain `simultaneous_capacity.verified=true`,
an observed application peak at least equal to the four-domain configured
lease total, and a granted advisory-lock count equal to the Lock pool maximum.
Per-domain peaks collected one after another are diagnostic only and do not
authorize a capacity canary. Runtime schema validation also requires the
latest migration contract row, critical lease/fencing/scheduling columns,
critical indexes, and the complete LangGraph Checkpointer 3.1 migration set.

Pool exhaustion triage order:

1. identify the saturated domain from aggregate pool metrics;
2. verify configured process counts and per-role capacity budget;
3. compare application budget with `max_connections`, reserved connections
   and the external reserve;
4. check for leaked Stage 48 application-name sessions and advisory locks;
5. confirm Checkpointer observed peak is no greater than max plus configured
   overhead;
6. keep rollout at `0/0` until the capacity artifact and recovery/fencing gates
   are healthy.

## Effective memory configuration compatibility

The memory optimization foundation resolves legacy environment variables and
the new `MEMORY_*` paths into one immutable
`memory-runtime-config-v1` policy. During the compatibility window:

- a new value alone is accepted;
- a legacy value alone is adapted and reported as deprecated without logging
  its value;
- equal normalized new and legacy values are accepted;
- conflicting values fail configuration preflight instead of selecting the
  more aggressive rollout, enforcement, or compression mode.

`GET /api/runtime` exposes only safe effective fields under `memory_runtime`:
the schema version, configuration validity, budget/compression modes, durable
graph version and rollout percentage, and whether any legacy variable was
consumed. It never exposes provider URLs, credentials, deployment secrets, or
configured values from deprecation warnings.

The committed defaults remain safe: Interview rollout is `0`, budget mode is `disabled`,
compression mode is `disabled`, all legacy enforcement and
compression gates are `false`, and the structured examples in `.env.example`
remain commented. A `langgraph-v2` rollout requires Interview budget
enforcement. Compression consumption additionally requires an available
Context Artifact store; Evidence consumption requires its parent Interview or
Review compression workflow.

All runtime consumers now read the resolved policy rather than parsing legacy
environment variables independently. This includes Interview graph dispatch,
model context windows and reserves, token-estimator selection, budget
enforcement, compression creation/consumption gates, artifact lease and cleanup
settings, deployment scope, and trusted-local deletion/metrics boundaries. The
legacy variables remain compatibility inputs only; removing them from the
adapter is a later breaking change and must not be done during a rollout.

## Session deletion boundary

Local V1 does not yet have a product user identity or administrator role. The
session deletion API is therefore hidden by default and is exposed only when
`MEMORY_TRUSTED_LOCAL_DELETION_ENABLED=true` is set in an explicitly trusted
local deployment. Do not enable it on a shared or internet-reachable runtime.

`DELETE /api/interviews/{session_id}` first marks the session as `deleting`,
which blocks snapshot, answer, stream, skip, finish, report, evaluation, and
SSE consumption. The response contains only a deletion job identifier, stable
status, timestamps, error code, and aggregate row counts. Repeating the request
returns the same logical job. `GET /api/interviews/{session_id}/deletion`
returns its tombstone after the business session has been physically removed.

PostgreSQL deletion jobs and the session deletion marker are migration-owned;
application runtime stores validate the schema and never create it. Backups may
retain physically deleted rows until the backup retention window expires. If
an older backup is restored, operators must replay every completed deletion
tombstone before opening the restored runtime to interview traffic. Tombstones
must therefore be retained outside the restored backup boundary for at least
the maximum backup retention period. A restore is incomplete until this replay
and a deletion-count audit have succeeded.

## Memory repository acceptance

Run `python -m scripts.memory_system_optimization_acceptance` for the
repository-only gate. The runner captures focused tests internally and emits
only `READY_FOR_MEMORY_SYSTEM_SHADOW` plus
`PRODUCTION_OBSERVATION=NOT_RUN` on success. It does not authorize a migration,
rollout, destructive retention job, real-provider call, or production canary.
### 持久化记忆指标

记忆指标只写入按分钟和小时聚合的 bucket，不保存原始事件，也不允许 session、principal、question、fact、artifact、prompt、回答、摘要或 excerpt 等标识和内容字段。PostgreSQL 正常时，trusted-local 的 `GET /api/runtime/memory-metrics` 返回 `store_kind=postgres_aggregate`、`data_complete=true` 与最新 bucket 时间；数据库不可用时面试业务继续运行，端点明确回退为 `process_local` 且 `data_complete=false`。

默认保留期为 minute 30 天、hour 180 天。缩短可通过经评审的部署策略执行；延长必须由隐私/合规、SRE 和技术负责人共同批准，并同步更新 retention policy version 与验收记录，不能用普通环境变量静默延长。
Budget Shadow is prepared as a validate-only workflow documented in `docs/memory-budget-shadow-runbook.md`. Do not set `MEMORY_BUDGET_SHADOW_ENABLED=true` from this repository phase; the status endpoint is read-only and cannot activate it.
Principal Memory is default-off. Supported repository modes are only `disabled`, `write_shadow`, and `read_shadow`; `MEMORY_LONG_TERM_MODE=consume` is rejected rather than downgraded. Identity must come from an explicit trusted resolver, never from resume text, contact data, browser/device identifiers, network metadata, candidate names, embeddings, or model output. Consent is versioned and checked again for every proposal, storage, and read-shadow operation.
Final phase acceptance is produced by `python -m scripts.memory_validation_foundation_acceptance` only after full Python/browser/build/live-PostgreSQL results have been published as the signed `reports/memory/operational-rc-evidence-v1.json` Bundle. Configure `OPERATIONAL_INPUT_REVISION` and the Evidence HMAC signer before running it. A successful gate still reports `LONG_TERM_MEMORY_CONSUMPTION=BLOCKED` and `PRODUCTION_OBSERVATION=NOT_RUN`.

## Local V1 Principal Memory operations

Local Consume readiness, aggregate-only telemetry, bounded expiry cleanup, and
protected deletion-tombstone replay are defined in
`docs/local-principal-memory-operations.md`. Run
`python -m scripts.local_principal_memory preflight` before
Local Consume. A non-zero exit, any gate code, incomplete durable metrics, or an
unverified PostgreSQL migration keeps consumption blocked. The command does not
change configuration or run migrations. Mutating cleanup and replay commands
require `--execute` and return counts only.

Local V1 is a trusted-local, default-off experiment. Principal Memory may
influence follow-up generation only. Score and report modules have no direct
Principal Memory dependency. No claim is made that changed interview
trajectories are causally equivalent. `learning_goal` and `target_role_family`
may change the follow-up trajectory and therefore may indirectly change later
answers. Do not interpret direct module isolation as proof of equal scores,
equal reports, fairness, candidate safety, production readiness, or Hosted
C1-A equivalence. Real-candidate production use remains prohibited.

## Local V1 hardening v0.4 accepted baseline

The accepted implementation is fixed by both commit and tree identity:

```text
VALIDATED_IMPLEMENTATION_REVISION=e6b8f29d25276f17c874d07cebc15565bad37492
VALIDATED_IMPLEMENTATION_TREE=354d3d0a1ad99bfef57fd51244d1f5358442c79f
EVIDENCE_PUBLICATION_REF=refs/tags/local-v1-hardening-v0.4-accepted
```

The implementation was reproduced on Windows 11 x64 and Ubuntu 24.04 x64 with
Python 3.11, Node 22, PostgreSQL 16, Playwright 1.61.1 and Chromium
149.0.7827.55. The Ubuntu full Python/live-PostgreSQL run passed 2,216 tests;
both Ubuntu and Windows browser runs passed 86 tests. All skips were reviewed:
76 were conditional non-applicable, 5 were optional real-Provider checks that
remain unauthorized, and none were blockers. PostgreSQL test relation residue,
target port residue and target process residue were all zero.

See `docs/local-v1-hardening-acceptance.md` and
`docs/local-v1-hardening-manifest.json` for the complete aggregate evidence and
artifact SHA-256 values.

### Verify the published baseline

Use a clean clone and verify the publication tag before using the runbook:

```powershell
git fetch origin --tags
git rev-parse refs/tags/local-v1-hardening-v0.4-accepted^{}
git merge-base --is-ancestor e6b8f29d25276f17c874d07cebc15565bad37492 refs/tags/local-v1-hardening-v0.4-accepted^{}
python -m pytest tests/contracts/test_memory_publication_evidence.py -q
```

The tag resolves to the documentation-only evidence publication revision. The
implementation revision above must remain in its history. The tracked manifest
does not contain the publication commit identity because a commit cannot
truthfully self-record its own hash.

### Operate within the accepted boundary

The accepted repository default is disabled. Before any trusted-local use:

1. confirm the checkout contains the accepted tag and implementation ancestor;
2. configure only the intended Local V1 mode and its exact capability gates;
3. run the existing Principal Memory preflight;
4. verify the protected ledger and durable replay watermark are current;
5. verify database migrations and aggregate metrics are complete;
6. keep the disable path available throughout the local session;
7. never use real candidate data or a real Provider under this acceptance.

Read Shadow must remain zero-write and zero-injection. Disabled mode must
remain zero activity. Local Consume, when separately enabled by a trusted local
operator, remains bounded to follow-up generation and can indirectly change
later answers by changing the follow-up trajectory.

### Roll back safely

Set the long-term mode and all Write, Read, Local Consume, trusted-local API,
Local Principal and trusted-local metrics gates to disabled, then restart the
local runtime. Retain the protected ledger, tombstones and migrations. Do not
erase durable safety records or legitimate user facts as a rollback shortcut.

### Closure and future work

```text
LOCAL_V1_IMPLEMENTATION=FEATURE_COMPLETE
LOCAL_V1_HARDENING=COMPLETE
LOCAL_V1_FINAL_ACCEPTANCE=PASS
LOCAL_V1_DEFAULT=DISABLED
LOCAL_V1_REAL_CANDIDATE_USE=PROHIBITED
REAL_PROVIDER_EVALUATION=NOT_RUN
NEXT_REQUIRED_TASK=NONE
OPTIONAL_FUTURE_TRACK=HOSTED_PRODUCTIZATION_REDECISION
HOSTED_V2=NO_GO_FOR_NOW
INHERITED_PLAN_EXECUTION_STATE=FROZEN_NON_EXECUTABLE
```

Hosted V2 is not the next automatic task. If it is reconsidered, begin with a
new baseline and a newly approved or formally reopened Productization ADR and
data-use specification. Local facts, Consent records and tombstones must not be
automatically migrated into a hosted identity boundary.

## Phase 5 前端恢复与诊断操作

- `/prep` 的 `PrepPlan` 由服务端保存固定 TTL 与版本。页面提示过期时应重新生成，不能从浏览器缓存恢复成新的权威计划。
- 草稿响应的 `durability=process_memory` 表示服务重启后失效；`durability=persistent` 表示可跨进程恢复，并应同时展示服务端 `expires_at`。浏览器只保存高熵恢复标识。
- 浏览器保存的“上次活跃会话”引用只是恢复入口。继续面试前必须读取服务端权威快照；404/410 且确认不可恢复时才清除，网络错误、超时或 503 时保留。
- 报告生成属于后台任务。关闭生成页不会取消任务；重新进入时从 `/report/progress` 同步，失败记录从报告中心按 API 的 `retryable` 能力恢复。
- 默认产品构建不请求或显示任务 ID、workflow、attempt、heartbeat、Agent runs 或 runtime events。仅在受控本地诊断时设置 `VITE_SHOW_RUNTIME_DIAGNOSTICS=true` 并重新构建前端。
- 前端构建会自动执行 66 KiB JS / 20 KiB CSS gzip 预算及动态路由隔离检查；机器可读结果位于未提交的 `frontend/dist/bundle-summary.json`。
