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
| Open `/prep` | Prep page renders with JD/resume inputs and empty tags | Pending |  |
| Generate plan | `/api/prep` returns questions and tags render | Pending |  |
| Save draft | Draft saves and browser keeps `interviewDraftId` | Pending |  |
| Restore draft | JD/resume/tags restore from anonymous draft | Pending |  |
| Start interview | Browser navigates to `/interview?session_id=...` | Pending |  |
| Submit streamed answer | SSE chunks or final turn render; question navigation refreshes | Pending |  |
| Skip question | Question state changes to skipped or session finishes | Pending |  |
| Finish interview | Browser navigates to `/report-processing?session_id=...` | Pending |  |
| Report processing | Progress/status/RAG summary render until report is available | Pending |  |
| Report detail | Score, summary, five dimensions, feedbacks, evidence render | Pending |  |
| PDF download | PDF downloads and visible report content remains on screen | Pending |  |

## Error-State Checklist

| URL or action | Expected result | Result | Notes |
| --- | --- | --- | --- |
| `/interview` without `session_id` | Shows missing-session error and disables answer controls | Pending |  |
| `/report-processing` without `session_id` | Shows missing-session error and disables view-report button | Pending |  |
| `/report-detail` without `session_id` | Shows missing-session error and disables PDF button | Pending |  |
| `/report-detail?session_id=bad` | Shows API error without breaking page shell | Pending |  |
| PDF download failure | Shows local notice and does not clear rendered report | Pending |  |
| Report generation failure | Shows report unavailable/failure notice on processing page | Pending |  |

## Automated Verification

| Command | Result |
| --- | --- |
| `F:\python3.11\python.exe -m pytest tests/test_static_report_ui.py tests/test_page_routes.py tests/test_local_v1_docs.py -q` | Pass: 35 passed |
| `node --check app/static/api.js` | Pass |
| `node --check app/static/shared-ui.js` | Pass |
| `node --check app/static/prep.js` | Pass |
| `node --check app/static/interview.js` | Pass |
| `node --check app/static/report-processing.js` | Pass |
| `node --check app/static/report-detail.js` | Pass |
| `npm run build:prototype-css` | Pass |
| `F:\python3.11\python.exe -m pytest -q` | Pending |

## Stage 24 Execution Notes

| Item | Value |
| --- | --- |
| Execution date | 2026-07-07 |
| Browser | Pending manual execution |
| Server URL | `http://127.0.0.1:8000` |
| Runtime store | PostgreSQL |
| LLM provider | DeepSeek-compatible OpenAI API |
| Knowledge chunks | Pending database check |
| Question evaluation UI | Pending manual execution |

## Stage 24 Defect Log

| ID | Severity | Page/API | Symptom | Fix commit | Verification |
| --- | --- | --- | --- | --- | --- |
| None | - | - | No Stage 24 browser defects recorded; manual browser execution is still pending | - | Automated static checks passed |

## Final Status

Pending manual browser execution.
