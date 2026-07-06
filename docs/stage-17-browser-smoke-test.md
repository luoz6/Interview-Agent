# Stage 17 Browser Smoke Test

## Preconditions

Run the app locally:

```powershell
F:\python3.11\python.exe -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

## Sample JD

```text
后端开发工程师，负责核心交易系统和缓存平台建设。
要求熟悉 Python、FastAPI、Redis、MySQL、消息队列、Docker 和 Linux。
需要具备高并发系统设计、接口幂等、缓存一致性、慢查询优化和工程化交付经验。
```

## Sample Resume

```text
5 年 Python 后端开发经验，负责过电商订单、库存、支付对账和用户增长系统。
使用 FastAPI、MySQL、Redis、Kafka、Docker 构建服务。
主导过 Redis 缓存改造、MySQL 慢查询优化、消息队列削峰和接口幂等治理。
熟悉 Linux 部署、日志排障和服务监控。
```

## Four-Page Flow Checklist

| Step | Expected |
| --- | --- |
| Open `/` | Shows prep page from `app/test4.html` |
| Click `生成面试计划` | Tags and question plan render from `/api/prep` |
| Click `保存草稿` | Status shows saved and `localStorage.interviewDraftId` exists |
| Click `开始面试` | Browser navigates to `/interview?session_id=...` |
| Submit one answer | Streamed text or next turn appears; question navigation refreshes |
| Click `下一题` | Current question changes or status remains finished if last question |
| Click `结束面试` | Browser navigates to `/report-processing?session_id=...` |
| Wait for report | Progress page updates until redirecting to `/report-detail?session_id=...` |
| Click `下载报告 (PDF)` | PDF downloads and report page remains visible |

## Error-State Checklist

| URL | Expected |
| --- | --- |
| `/interview` | Shows missing `session_id` error and disables controls |
| `/report-processing` | Shows missing `session_id` error |
| `/report-detail` | Shows missing `session_id` error |
| `/report-detail?session_id=bad` | Shows API error without clearing the page shell |

## Real-LLM Notes

Use one complete run with the sample JD and resume. Record:

| Item | Result |
| --- | --- |
| Plan quality | Pass if questions match JD/resume topics |
| Follow-up quality | Pass if follow-up asks for deeper technical detail |
| Report quality | Pass if summary is coherent and not only tag text |
| Evidence quality | Pass if references are shown or unavailable state is explicit |
| PDF quality | Pass if PDF contains Chinese labels and readable feedback |

## Acceptance Result

Stage 18 records the executed result in `docs/stage-18-acceptance-log.md`.
