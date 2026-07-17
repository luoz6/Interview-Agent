# Stage 18 Acceptance Log

Date: 2026-07-06

## Environment

| Item | Value |
| --- | --- |
| OS | Windows local development |
| Python | `F:\python3.11\python.exe` |
| Server command | `F:\python3.11\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765` |
| Browser | GUI browser not run in this tool session |
| Backend storage | In-memory runtime for local smoke; PostgreSQL/pgvector smoke via `postgresql://postgres:postgres@127.0.0.1:5432/interview` |
| LLM mode | DeepSeek-compatible real LLM smoke executed for plan/follow-up/report paths |

## Automated Verification

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe -m pytest tests/test_page_routes.py tests/test_static_report_ui.py -q` | Pass: 19 passed, 1 warning |
| `node --check app/static/api.js ... app/static/report-detail.js` | Pass |
| `npm run build:prototype-css` | Pass |
| `F:\python3.11\python.exe -m pytest -q` | Pass: 222 passed, 20 skipped, 1 warning |

## Browser Flow Result

| Step | Result | Notes |
| --- | --- | --- |
| Open `/` | Pass via HTTP smoke | GUI browser not opened; page route returned prep runtime hook |
| Generate interview plan | Pass via HTTP smoke | `POST /api/prep` returned questions and job tags |
| Save draft | Pass via HTTP smoke | Draft save and restore endpoints passed |
| Start interview | Pass via HTTP smoke | `POST /api/interviews` returned `session_id` |
| Submit streamed answer | Pass via HTTP smoke | `/answer/stream` returned `event: done` |
| Skip question | Pass via HTTP smoke | `/skip` returned active session state |
| Finish interview | Pass via HTTP smoke | `/finish` returned finished session state |
| Report processing progress | Pass via HTTP smoke | `/report/progress` returned `completed` after fake report worker saved report |
| Report detail rendering | Pass via HTTP smoke | `/report` returned full report with `references[].excerpt` |
| PDF download | Pass via HTTP smoke | `/report.pdf` returned `application/pdf`, 3823 bytes |

## Error-State Result

| URL | Result | Notes |
| --- | --- | --- |
| `/interview` | Pass via HTTP smoke | Page shell renders without `session_id` |
| `/report-processing` | Pass via HTTP smoke | Page shell renders without `session_id` |
| `/report-detail` | Pass via HTTP smoke | Page shell renders without `session_id` |
| `/report-detail?session_id=bad` | Pass via HTTP/API smoke | Page shell renders; bad report API returns 404 |

## Real-LLM Result

| Item | Result | Notes |
| --- | --- | --- |
| Plan quality | Pass via real LLM smoke | Real DeepSeek-compatible call rejected structured `response_format`, then raw JSON fallback returned a valid 3-question interview plan |
| Follow-up quality | Pass via real LLM smoke | `generate_followup()` returned a relevant cache consistency follow-up question |
| Report quality | Pass via real LLM + RAG smoke | Structured report output failed with the same provider limitation, then raw JSON fallback succeeded with a non-fallback report: score 69, 3 feedback items |
| Evidence quality | Pass via PostgreSQL/pgvector smoke | `knowledge_chunks` contained 10 chunks, vector dimension 1024; generated report included reference counts `[1, 3, 3]` |
| PDF quality | Pass via service smoke | Real report-shaped PDF generation returned `%PDF-` bytes; fake-report HTTP smoke already verified the PDF endpoint |

## Defect Log

| ID | Severity | Page/File | Symptom | Fix | Regression Test |
| --- | --- | --- | --- | --- | --- |
| S18-1 | Medium | `app/services/report_tasks.py` | Local smoke without `POSTGRES_DSN` raised `KeyError` from `get_knowledge_store()` before report failure could be recorded | Catch knowledge-store construction errors in `generate_report_for_session()` and save a failed report record | `tests/test_report_tasks.py::test_generate_report_for_session_saves_failed_record_when_knowledge_store_is_unconfigured` |
| S18-2 | High | `app/services/llm.py` | Real DeepSeek-compatible `generate_plan()` call failed because the provider does not currently support the structured `response_format` path used by `with_structured_output()`; report structured output has the same limitation but already recovers through raw JSON fallback | Added raw JSON fallback for plan generation and validated it with a real DeepSeek-compatible smoke call | `tests/test_llm_service.py::test_openai_interview_llm_falls_back_to_json_for_plan_when_structured_output_fails` |

## Final Status

Accepted for automated local HTTP smoke. Real plan generation, follow-up, RAG report, evidence, and PDF service smokes passed after configuring local PostgreSQL. Manual GUI browser acceptance is still pending.
