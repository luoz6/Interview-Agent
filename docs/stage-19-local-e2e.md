# Stage 19 Local V1 E2E Acceptance

Use `docs/local-v1-runbook.md` as the procedure for this acceptance pass.

Date: 2026-07-06

## Environment

| Item | Value |
| --- | --- |
| Server | `http://127.0.0.1:8000` |
| Python | `F:\python3.11\python.exe` |
| PostgreSQL | `127.0.0.1:5432/interview` |
| Runtime store | `postgres` |
| LLM | DeepSeek-compatible OpenAI API |
| Browser | Manual local browser |

## Preflight

| Check | Result | Notes |
| --- | --- | --- |
| `POSTGRES_DSN` configured | Not run |  |
| `knowledge_chunks` count > 0 | Not run |  |
| `OPENAI_API_KEY` configured | Not run | Do not paste the key |
| Server starts | Not run |  |
| Static CSS built | Not run |  |

## Browser Flow

| Step | Result | Notes |
| --- | --- | --- |
| Open `/prep` | Not run |  |
| Generate plan | Not run |  |
| Verify job tags | Not run |  |
| Save draft | Not run |  |
| Restore draft after refresh | Not run |  |
| Start interview | Not run |  |
| Submit streamed answer | Not run |  |
| Skip question | Not run |  |
| Finish interview | Not run |  |
| Report-processing page shows progress | Not run |  |
| Report-detail page renders score/dimensions/feedback | Not run |  |
| Evidence excerpts render | Not run |  |
| PDF downloads and opens | Not run |  |

## Defects

| ID | Severity | Symptom | Fix | Retest |
| --- | --- | --- | --- | --- |

## Final Status

Not run.
