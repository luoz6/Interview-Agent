# Memory Budget Shadow Runbook

This phase prepares Budget Shadow but does not activate it. `MEMORY_BUDGET_SHADOW_ENABLED` remains false. The repository script is validate-only and never mutates environment configuration, deployment state, traffic percentage, Prompt selection, or provider input.

Preflight requires durable aggregate metrics, isolated live PostgreSQL validation, reviewed Knowledge P1 coverage, the deterministic long-context quality gate, green Python and browser baselines, an explicit target environment and observation window, Question Memory consumption disabled, and no Principal Memory consumption path.

Automatic stop conditions are any known-over-budget provider call, mandatory current-content loss, privacy audit hit, budget/config conflict, incomplete metrics, more than one unavailable observation bucket, a follow-up error-rate increase greater than 0.5 percentage points at 200+ samples, or a P95 follow-up latency increase greater than 20% at 200+ samples. Fewer than 200 samples may continue observation but may not expand.

Observation records contain only an observation ID, config digest, time window, language sample status, estimator error direction, route/fallback counts, latency/cost aggregates, and stop-gate status. They must never contain session, principal, Prompt, answer, question, fact, artifact, or source locators.

The trusted-local `GET /api/runtime/memory-budget-shadow` endpoint is status-only. It cannot enable Shadow.
