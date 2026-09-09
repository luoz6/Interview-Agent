# A2A-V1 Runtime Baseline

## Status

- Phase: `T-1A — Code / Runtime Baseline Identity`
- Plan version: `v0.2`
- Recorded date: `2026-09-09`
- Recorder: Codex root agent

## Repository Identity

| Item | Value |
|---|---|
| Current branch | `master` |
| Closure baseline commit | `3e0a6a7` |
| Relation to `origin/master` | ahead by `1` |
| Working tree | dirty |
| Dirty entries | `129` (`git status --short` lines, includes modified and untracked) |

The working tree is intentionally dirty because it contains pre-existing
JIT-main-question / browser / frontend work that has not yet been committed.
The only A2A-V1 preparation commit is `a155fc1`.

## Runtime Versions

| Dependency | Version |
|---|---|
| Python | `3.11.3` |
| FastAPI | `0.138.2` |
| Pydantic | `2.13.4` |
| HTTPX | `0.28.1` |
| a2a-sdk | `1.1.2` |
| LangGraph | `1.2.7` |
| LangChain | `1.3.11` |
| Uvicorn | `0.49.0` |

## Configured Runtime Modes

The checked-in `.env.example` describes the dependency-free preview defaults.
Code-level default `INTERVIEW_RUNTIME_STORE` is `postgres`, but the preview
profile intentionally selects in-memory/static components.

| Setting | `.env.example` value | Meaning |
|---|---|---|
| `INTERVIEW_RUNTIME_STORE` | `memory` | preview in-memory session store |
| `REPORT_RUNTIME_PROFILE` | `preview` | preview profile |
| `REPORT_JOB_STORE` | `memory` | in-memory report jobs |
| `REPORT_WORKER` | `in_process` | report generation inside API process |
| `KNOWLEDGE_STORE` | `static` | static knowledge, no pgvector |
| `EMBEDDING_PROVIDER` | `disabled` | no embedding provider |
| `INTERVIEW_LANGGRAPH_RUNTIME_ENABLED` | `true` | graph registration enabled |
| `INTERVIEW_LANGGRAPH_VERSION` | `langgraph-v1` | configured graph version |
| `INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT` | `0` | no durable graph rollout in preview |
| `INTERVIEW_JIT_MAIN_QUESTION_ENABLED` | `false` | JIT main question disabled |
| `REPORT_LANGGRAPH_VERSION` | `langgraph-review-v1` | review graph version |
| `REPORT_LANGGRAPH_ROLLOUT_PERCENT` | `0` | no durable review rollout in preview |
| `REPORT_LANGGRAPH_RUNTIME_ENABLED` | `true` | review graph registration enabled |
| `OPENAI_BASE_URL` | `https://api.deepseek.com` | custom provider |
| `OPENAI_MODEL` | `deepseek-chat` | example model |
| `LLM_CONTEXT_WINDOW_TOKENS` | `128000` | explicit context window |

## Observed Current Business Path

During local preview execution, API responses reported:

```text
workflow_engine: legacy
```

and report job records used:

```text
review_engine: legacy
```

Therefore the currently observed, reproducible local preview business path is:

```text
FastAPI
  -> legacy InterviewSessionStore / InterviewGraphRunner
  -> legacy round review / report job path
```

The durable LangGraph paths are present and tested, but are not the active
default path under the preview profile because rollout percentages are `0`.

## Candidate Durable Paths

| Path | Setting |
|---|---|
| Interview durable graph | `INTERVIEW_LANGGRAPH_VERSION=langgraph-v1` |
| Interview durable v2 | code supports `langgraph-v2` |
| Interview JIT v3 | requires `INTERVIEW_JIT_MAIN_QUESTION_ENABLED=true` and PostgreSQL |
| Review durable graph | `REPORT_LANGGRAPH_VERSION=langgraph-review-v1` |

## Feature Flags Present

Current transport flags:

```text
AGENT_TRANSPORT
REVIEWER_TRANSPORT
```

Transport matrix:

| Path | Value |
|---|---|
| Local internal path | `AGENT_TRANSPORT=local` |
| Internal A2A path | `AGENT_TRANSPORT=a2a` |
| Reviewer default | `REVIEWER_TRANSPORT=legacy_microbatch` |
| Reviewer A2A | `REVIEWER_TRANSPORT=a2a` |

## Gate -1A

Status: `PARTIAL`

This baseline is reproducible from commit `a155fc1` and the checked-in
`.env.example`. The primary observed local path is the legacy preview path.
Durable paths are candidates but not active in preview mode. The working tree
is dirty, so repository-level reproducibility is not yet locked.
