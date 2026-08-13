# ADR: Provider authorization, budgets, latency, and observability

- Status: Accepted
- Date: 2026-08-05
- Authorization: `interview-quality-v1-20260805-unlimited-01`

## Decision

The machine-readable authorization is
`config/interview_quality_v1_provider_authorization.json`. Every T27, T36, T57, or
T65 real-Provider run validates that immutable manifest before its first request.
Authorization does not need to be requested again while provider, model, task, data,
evidence handling, and stop conditions remain within that manifest.

Unlimited means there is no cumulative currency, request, input-token, output-token,
or per-task authorization ceiling. It does not remove per-operation output limits,
timeouts, retry limits, concurrency controls, retry-amplification gates, metering, or
evidence requirements. Every attempted request, including failures and retries, is
counted.

## Frozen operation gates

The sole numeric source is `config/interview_quality_v1_gate.json`. It freezes the
Decision and follow-up output-token budgets, call-count limits, absolute p95 limits,
retry amplification, degradation rate, minimum samples, and comparable-baseline
rules. Markdown and JSON publications must render loaded rules; this ADR intentionally
does not duplicate their numeric values.

Latency cohorts never mix cold/warm, fixed/adaptive, follow-up/next-question,
first/recovery, schema, question count, or Provider path. Decision latency is an
absolute adaptive-path metric; `fixed_v1` has no independent Decision stage and no
synthetic zero baseline may be created. Follow-up TTFT starts at answer acceptance and
ends at the first follow-up token on the same E2E path. Next-question latency is
reported separately. Missing required baselines produce `INSUFFICIENT_BASELINE`.

## Run manifest and metering

Before any outbound request, a local run manifest records implementation revision,
dataset and GateConfig hashes, Prompt/rubric/policy versions, authorization hash,
provider/model/base host, pricing source and observation date, timeout/retry/concurrency
settings, and redaction result. After each attempt it appends input/output tokens,
request/retry count, latency, and actual or estimated cost. Price is computed from the
frozen snapshot:

```text
cost = input_tokens / 1_000_000 * input_price
     + output_tokens / 1_000_000 * output_price
     + any explicitly documented cache-price components
```

No key, full resume, full prompt, Principal Memory, or unredacted response may enter
logs, Git, or published evidence. Raw responses remain local and redacted; publication
contains only aggregate or reviewed redacted evidence.

## Hard stops

The validator stops before the next request on any manifest-listed stop condition,
including provider/model mismatch, model drift, unapproved fallback, data-policy or
redaction failure, missing credential, unavailable usage metering/evidence storage,
excess retry amplification, repeated provider failure, config/dataset drift, or user
revocation. Currency and cumulative usage alone are not stop conditions.

Changing or adding provider/model, enabling automatic fallback, expanding tasks or
data scope, externalizing raw responses, using production/Hosted/recruiting-decision
paths, or resuming after user revocation requires new authorization. Ordinary progress
to the next already authorized task does not.
