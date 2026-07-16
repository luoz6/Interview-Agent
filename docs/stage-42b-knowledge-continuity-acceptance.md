# Stage 42B Knowledge Continuity Acceptance

Status: `PASS`

Date: 2026-07-16

## Scope Status

- Stage 42A: `PASS`; see `docs/stage-42a-knowledge-retrieval-acceptance.md`.
- Task 6 Examiner evidence inheritance: implemented and committed.
- Task 7 Reviewer/report evidence reuse: implemented; production round-review
  state preservation and fallback citation defects were found and fixed during
  Task 10 real-browser verification.
- Task 8 degradation, privacy, and trace behavior: implemented and committed.
- Task 10 deterministic browser, PDF continuity, provider orchestration, and
  release artifact audit: implemented and passing.
- Final Stage 42 status: `PASS`; the fresh post-fix real-model browser run and
  formal artifact audit both passed on 2026-07-16.

## Verified Gates

| Gate | Result |
| --- | --- |
| Full Python suite without opt-in services | `561 passed, 35 skipped` |
| PostgreSQL, report job, and pgvector gate | `47 passed` |
| Targeted Stage 42B regression | `50 passed` |
| Deterministic desktop and mobile Playwright | `8 passed, 2 real-model opt-in skipped` |
| Fresh real-model Playwright | `1 passed` |
| Tailwind CSS build | `PASS` |
| JavaScript syntax checks | `PASS` |
| Stage 42 artifact audit tests | `9 passed` |
| Formal Stage 42 artifact audit | `PASS` |

The deterministic browser flow covers Prep evidence display, persisted evidence
IDs, SSE answer handling, refresh and 409 recovery, report progress knowledge
path, Report Detail citations, PDF citation IDs, explicit degraded completion,
and desktop/mobile layouts.

## Real-Model Findings

The opt-in flow successfully generated plans, completed four answers spanning at
least two questions with follow-ups, and generated a persisted report in one
run. That run exposed two blocking defects:

1. Heuristic report fallback discarded trusted v2 references when the provider
   returned no reference IDs. The backend now attaches only the Prep-bound IDs
   and uses `candidate_summary` rather than chunk content in public excerpts.
2. `build_single_question_review_state()` rebuilt `InterviewPlan` without
   `prep_context`, causing production round reviews to use
   `legacy_semantic_search`. It now preserves the v2 plan snapshot so the route
   remains `get_by_ids=1/search=0`.

Later provider attempts timed out during Prep or report generation. Provider
calls are now configurable and bounded, and the real-browser test owns and
terminates both Uvicorn and the report worker. The fresh post-fix run
`20260716T062331Z-real-model-rc` passed every browser, evidence-continuity,
question-evaluation, report-reference, and PDF assertion in 4.5 minutes.

## Artifact Decision

The formal PASS directory is
`reports/stage42-acceptance/20260716T062331Z-real-model-rc/`.
`scripts/audit_stage42_artifacts.py` verified the whitelist, relative paths,
sizes, SHA-256 hashes, passing metrics, and absence of secrets, DSNs, absolute
paths, email addresses, and phone numbers. The directory contains only the
manifest, metrics, report, one sanitized retrieval case, and two browser
screenshots.
