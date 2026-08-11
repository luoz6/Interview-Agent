# Stage 49 Context Budget Acceptance

Stage 49 introduces a model-aware, deterministic authorization layer before
LLM provider calls. It does not authorize production traffic and does not add
LLM-generated semantic compression artifacts.

## Repository Gate

Run:

```powershell
& 'F:\python3.11\python.exe' -m scripts.repository_acceptance stage49
```

Successful repository output is:

```text
READY_FOR_CONTEXT_BUDGET_CANARY
PRODUCTION_OBSERVATION=NOT_RUN
```

This means the deterministic budgeting, privacy, Interview/Knowledge, Review,
runtime classification, and canary contracts passed locally. It is not a
production `PASS`.

## Safety Defaults

The committed defaults remain:

```dotenv
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
CONTEXT_BUDGET_SHADOW_ENABLED=false
CONTEXT_BUDGET_PREP_ENFORCEMENT=false
CONTEXT_BUDGET_INTERVIEW_ENFORCEMENT=false
CONTEXT_BUDGET_REVIEW_ENFORCEMENT=false
CONTEXT_BUDGET_REPORT_ROUTING=false
```

An unknown or custom provider must set `LLM_CONTEXT_WINDOW_TOKENS`. A tokenizer
family such as `cl100k_base` is optional and must be explicitly verified and
configured; otherwise the conservative estimator is used.

## Privacy Boundary

Repository and canary artifacts may contain numeric token usage, counts,
utilization, stable routes, policy versions, and safe digests. They must not
contain prompts, answers, Resume/JD text, Evidence content, message/Evidence
IDs, credentials, DSNs, or provider payloads.

## Production Observation

Production observation remains `NOT_RUN` until separately authorized shadow
measurement and phased Interview/Review enforcement are completed. Stage 50
durable semantic compression must not begin before Stage 49 canary evidence is
accepted.
