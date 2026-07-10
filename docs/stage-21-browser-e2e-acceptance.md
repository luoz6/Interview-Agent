# Stage 21 Browser E2E Acceptance

Date: 2026-07-06

## Scope

This acceptance record covers the four-page local runtime:

| Page | Route | Source |
| --- | --- | --- |
| Prep | `/` or `/prep` | `app/test4.html` |
| Interview | `/interview?session_id=...` | `app/test3.html` |
| Report processing | `/report-processing?session_id=...` | `app/test2.html` |
| Report detail | `/report-detail?session_id=...` | `app/test1.html` |

Out of scope: user login, account isolation, startup scripts, Playwright/browser automation.

## Environment

| Item | Value |
| --- | --- |
| Deployment mode | Local single-user |
| PostgreSQL | `postgresql://postgres:postgres@127.0.0.1:5432/interview` |
| Frontend | Four HTML pages served by FastAPI |
| LLM | DeepSeek-compatible OpenAI API through `OPENAI_BASE_URL` and `OPENAI_API_KEY` |

## Manual Browser Checklist

| Step | Expected result | Result | Notes |
| --- | --- | --- | --- |
| Open `/prep` | Prep page renders with JD/resume inputs and empty tags | Blocked | GUI browser not available in this tool session; page shell was verified by HTTP/static checks |
| Generate plan | `/api/prep` returns questions and tags render | Pass via API | Real LLM `/api/prep` returned 4 questions and tags `python`, `fastapi`, `redis`, `postgresql` |
| Save draft | Draft saves and browser keeps `interviewDraftId` | Pass via API | `/api/interview-drafts` saved draft `draft_f370c8a46256`; browser localStorage was not observable |
| Restore draft | JD/resume/tags restore from anonymous draft | Pass via API | Draft restore endpoint returned saved payload |
| Start interview | Browser navigates to `/interview?session_id=...` | Pass via API | Isolated session `26c8c528-10c0-4b1e-9e9f-13efbafb681f` created |
| Submit streamed answer | SSE chunks or final turn render; question navigation refreshes | Not run | API flow used non-streaming `/answer`; browser SSE UX still needs manual verification |
| Skip question | Question state changes to skipped or session finishes | Pass via API | `/skip` returned `200` in the isolated flow |
| Finish interview | Browser navigates to `/report-processing?session_id=...` | Pass via API | `/finish` returned `200` and queued report generation |
| Report processing | Progress/status/RAG summary render until report is available | Pass via API | Polling observed `retrieving`, `analyzing`, and completed report states |
| Report detail | Score, summary, five dimensions, feedbacks, evidence render | Pass via API/HTML shell | Report API returned score 31 and 5 feedbacks; `/report-detail?session_id=...` returned HTML shell |
| PDF download | PDF downloads and visible report content remains on screen | Pass via API | `/report.pdf` returned `application/pdf`, 14894 bytes; browser download behavior was not observable |

## Error-State Checklist

| URL or action | Expected result | Result | Notes |
| --- | --- | --- | --- |
| `/interview` without `session_id` | Shows missing-session error and disables answer controls | Pass via HTTP shell | Route returned `200 text/html`; browser control state still needs manual observation |
| `/report-processing` without `session_id` | Shows missing-session error and disables view-report button | Pass via HTTP shell | Route returned `200 text/html`; browser control state still needs manual observation |
| `/report-detail` without `session_id` | Shows missing-session error and disables PDF button | Pass via HTTP shell | Route returned `200 text/html`; browser control state still needs manual observation |
| `/report-detail?session_id=bad` | Shows API error without breaking page shell | Pass via HTTP shell | Route returned `200 text/html`; browser error rendering still needs manual observation |
| PDF download failure | Shows local notice and does not clear rendered report | Not run | Requires browser-side failure injection |
| Report generation failure | Shows report unavailable/failure notice on processing page | Not run | Requires forced report failure in a browser session |

## Automated Verification

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_local_v1_docs.py -q` | Pass: 37 passed |
| `node --check app/static/api.js` | Pass |
| `node --check app/static/shared-ui.js` | Pass |
| `node --check app/static/prep.js` | Pass |
| `node --check app/static/interview.js` | Pass |
| `node --check app/static/report-processing.js` | Pass |
| `node --check app/static/report-detail.js` | Pass |
| `npm run build:prototype-css` | Pass |
| `F:\python3.11\python.exe -m pytest -q` | Pass: 256 passed, 21 skipped |

## Stage 24 Carry-Forward

Stage 24 acceptance is superseded by Stage 25 RC acceptance. The Stage 25 run covers the same browser path plus built-in PostgreSQL defaults, worker-delayed report completion, service restart persistence, question evaluation trace, and PDF download.

## Stage 25 RC Execution Notes

| Item | Value |
| --- | --- |
| Execution date | 2026-07-07 |
| Browser | Blocked: no GUI browser/control available in this tool session |
| Server URL | `http://127.0.0.1:8000` |
| Runtime store | PostgreSQL with built-in local PostgreSQL defaults; isolated run used `INTERVIEW_RUNTIME_TABLE_PREFIX=stage25_rc_0707` |
| Database | `postgresql://postgres:postgres@127.0.0.1:5432/interview` |
| LLM provider | DeepSeek-compatible OpenAI API |
| Knowledge chunks | 10 |
| Report worker | Pass via isolated worker process |
| Question evaluation trace | Pass via API: `evaluations_total=5` |
| PDF download | Pass via API: `application/pdf`, 14894 bytes |

## Stage 25 RC Resilience Checklist

| Step | Expected result | Result | Notes |
| --- | --- | --- | --- |
| Built-in local PostgreSQL defaults | Clearing `POSTGRES_DSN`, `INTERVIEW_RUNTIME_STORE`, `INTERVIEW_RUNTIME_TABLE_PREFIX`, and `PGVECTOR_TABLE` still resolves runtime stores to `postgresql://postgres:postgres@127.0.0.1:5432/interview` | Pass | Session store, report job store, and knowledge store resolved to the built-in DSN |
| Worker-delayed report completion | Finishing an interview while the report worker is stopped leaves processing visible; starting the worker completes the report | Pass | Worker stopped for 30 seconds: report stayed `202 processing`; restarted worker completed session `5541309c-dce3-44dd-ae85-801f67385af5` |
| Service restart persistence | Restarting FastAPI after report completion still loads `/report-detail?session_id=...` from PostgreSQL | Pass | Restarted isolated FastAPI and reloaded report API, question evaluations, and report-detail HTML shell |
| Question evaluation trace | `/report-detail?session_id=...` shows saved question evaluation records loaded from `/api/interviews/{session_id}/question-evaluations` | Pass via API | Main isolated session returned `evaluations_total=5`; delayed worker session returned `evaluations_total=4` |

## Stage 25 RC Defect Log

| ID | Severity | Page/API | Symptom | Fix commit | Verification |
| --- | --- | --- | --- | --- | --- |
| S25-ENV-1 | Blocking | Browser acceptance environment | Current tool session has no controllable GUI browser, no installed Playwright/Puppeteer/Selenium, and no browser command on PATH, so manual browser UI acceptance cannot be completed here | - | API/worker/resilience checks passed; manual GUI browser run still required |
| S25-ENV-2 | Medium | Local process environment | Closed in Stage 25.5: stale 8000 listener was PID `35980` split across two lines in `netstat` output; the process was stopped and current FastAPI now owns `http://127.0.0.1:8000` | - | `/openapi.json` on 8000 includes `question-evaluations`; default runtime tables verified |

## Stage 25.5 Attempt Notes

| Item | Value |
| --- | --- |
| Attempt date | 2026-07-07 |
| Port 8000 health | Pass: `/api/health` returned `ok` |
| Port 8000 current-route check | Pass: `/openapi.json` includes `/api/interviews/{session_id}/question-evaluations` |
| Stale listener PID | `35980`; earlier `netstat` wrapped it as `3598` plus `0` on the next line |
| Stop attempt | Pass: `Stop-Process -Id 35980 -Force` released port 8000 |
| Result | Stage 25.5 Task 1 unblocked; current FastAPI and report worker are running on the default runtime |

## Stage 38 Postgres Runtime Acceptance

| Item | Value |
| --- | --- |
| Execution date | 2026-07-09 |
| Runtime store | PostgreSQL with isolated Stage 38 table prefixes |
| Acceptance script | `scripts/stage38_postgres_runtime_acceptance.py` |
| Evidence JSON | `tmp/stage-38-postgres-runtime-acceptance.json` disposable run artifact; not committed |
| Browser status | manual GUI browser acceptance remains blocked in this tool session |

### Stage 38 Automated Contract Results

| Check | Result | Notes |
| --- | --- | --- |
| `schema_initializes_isolated_tables` | Pass | Isolated Postgres runtime tables were created for the Stage 38 prefix |
| `stale_expected_version_rejected` | Pass | Stale `expected_version=0` raised `SessionVersionConflict` with actual version `1` |
| `duplicate_command_id_is_idempotent` | Pass | Repeating `cmd-answer` did not append a duplicate candidate message |
| `stream_completion_advances_version_once` | Pass | Streaming prepare plus completion advanced `state_version` to `3` and preserved `last_command_id=cmd-stream` |
| `report_lifecycle_preserves_user_command_id` | Pass | Report processing/completion advanced versions without replacing `last_command_id=cmd-finish` |
| `postgres_reinstantiation_preserves_state` | Pass | A new Postgres store instance loaded the completed report state and metadata |

### Stage 38 Verification Commands

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe scripts/stage38_postgres_runtime_acceptance.py --table-prefix stage38_acceptance --write-json tmp/stage-38-postgres-runtime-acceptance.json` | Pass |
| `F:\python3.11\python.exe -m pytest tests/test_stage38_postgres_api_contract.py tests/test_postgres_session_store.py -q` | Pass with `POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5432/interview` |

## Stage 39 Browser RC Acceptance

| Item | Value |
| --- | --- |
| Execution date | 2026-07-10 |
| Scope | UTF-8 guardrail plus Local V1 browser RC validation |
| Runtime store | PostgreSQL local runtime after readiness check |
| UTF-8 guardrail | `tests/test_utf8_text_contract.py` |
| Browser status | Not run yet in this stage |

### Stage 39 Automated Results

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe -m pytest tests/test_utf8_text_contract.py -q` | Not run |
| `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_local_v1_docs.py -q` | Not run |
| `node --check app/static/api.js` | Not run |
| `node --check app/static/shared-ui.js` | Not run |
| `node --check app/static/prep.js` | Not run |
| `node --check app/static/interview.js` | Not run |
| `node --check app/static/report-processing.js` | Not run |
| `node --check app/static/report-detail.js` | Not run |

### Stage 39 Browser RC Checklist

| Step | Expected result | Result | Notes |
| --- | --- | --- | --- |
| PostgreSQL readiness | `interview` database accepts connections, `vector` extension exists, `knowledge_chunks` count is greater than zero | Not run |  |
| Open `/prep` | Page renders readable Chinese navigation, labels, draft buttons, Knowledge Agent section, and no mojibake | Not run |  |
| Generate plan | `/api/prep` returns questions, tags, and prep context; page text remains readable | Not run |  |
| Save and restore draft | Draft saves to localStorage-backed `interviewDraftId` and restores JD/resume | Not run |  |
| Start interview | Browser navigates to `/interview?session_id=...`; interview shell has readable Chinese | Not run |  |
| Submit streamed answer | SSE answer flow renders candidate answer plus streamed assistant text; latest snapshot reloads cleanly | Not run |  |
| Version conflict recovery | Stale command shows `会话状态已刷新，请检查最新题目后继续。` and keeps typed answer available for retry | Not run |  |
| Skip question | Skip uses versioned command payload and reloads readable question state | Not run |  |
| Finish interview | Browser navigates to `/report-processing?session_id=...` | Not run |  |
| Report processing | Progress, metadata, events, and unavailable states are readable Chinese | Not run |  |
| Report detail | Score, dimensions, feedback, evidence, and `逐题评估链路` render readable Chinese | Not run |  |
| PDF download | PDF downloads and report page remains visible | Not run |  |

## Final Status

Not accepted as Local V1 RC. API, worker, PostgreSQL, LLM, question-evaluation persistence, PDF generation, worker-delayed completion, and service restart persistence passed in an isolated run, but blocking manual GUI browser acceptance remains.
