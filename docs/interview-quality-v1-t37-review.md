# Interview Quality V1 T37 automatic review

## Outcome

```text
implementation_revision=67b7144b1af89b3db1804c1a7a8d443f87b840a8
implementation_tree=c642bc9cf2cf7d78849cb8cd22c6bd5840b8c863
engineering_status=PASS
automated_review=PASS
automated_fixture_gate_status=PASS
quality_status=BLOCKED
quality_reason=BLOCKED_NOT_RUN_REAL_PROVIDER
overall_status=BLOCKED
provider_calls=0
```

T37 Engineering is complete and regression-clean. Decision latency, Generation
TTFT/complete latency, follow-up and next-question end-to-end latency, total turn
latency, SSE resume latency, Provider calls, retries, fallback, input/output/cache
tokens, and estimated cost now have one strict evaluation contract. The evaluator
loads every numeric rule from the frozen GateConfig and refuses cohort-dimension
drift.

The deterministic fixture run passes the Engineering gates, but its timing values are
explicitly synthetic and cannot establish Provider performance. T36's real Provider
preflight remains stopped before the first data request because the authorized
`deepseek-chat` identity is not present in the current official model list or pricing
table. T37 therefore reports `quality_status=BLOCKED`, not PASS, and continues to T38
Engineering without requesting another ordinary authorization.

## Implemented measurement contract

- `fixed_v1` has no Decision-stage latency or Decision output-token field. The schema
  rejects zero or any other fabricated Decision baseline.
- `adaptive_v1` first execution records Decision separately from follow-up or
  next-question E2E timing. A follow-up sample requires Generation TTFT and complete
  latency; a next-question sample forbids Generation metrics.
- cold/warm, fixed/adaptive, follow-up/next, first/recovery, schema version, question
  count, and Provider path form the exact cohort identity. Latency p95 is computed by
  nearest rank within one cohort only.
- adaptive follow-up p95 can pass only when an exact fixed-policy cohort has the
  frozen minimum sample size. A missing or undersized match returns
  `INSUFFICIENT_BASELINE`.
- recovery samples record only SSE resume time. They cannot duplicate Provider calls,
  tokens, retries, fallback, first-execution latency, or cost.
- second-follow-up terminal guards must choose `next_question`, perform zero Provider
  calls, and carry no fabricated Decision output or latency.
- request, retry, stage-output, total-output, cache-token, and fallback counts are
  internally reconciled before Gate evaluation.
- session usage is normalized by question count and actual follow-up count; recovery
  observations are excluded from usage totals.
- p50, p95, maximum, maximum-case IDs, and threshold failures are preserved for every
  measured latency family.

## Runtime instrumentation

- streaming Agent runs record `first_item_latency_ms` exactly once when the first
  Provider stream item is observed;
- empty streams and deterministic fallback output do not fabricate Provider TTFT;
- existing Agent-run duration remains the Generation-complete measurement;
- Provider usage normalization records input, output, total, cached-input tokens, and
  the returned Provider model;
- DeepSeek `prompt_cache_hit_tokens`, generic `input_token_details.cache_read`, and
  `prompt_tokens_details.cached_tokens` shapes are normalized;
- Provider model identity is retained even when usage is absent, allowing the next
  request to fail closed for unmetered output;
- the trace sanitizer permits only the new bounded numeric/model fields and continues
  to reject prompts, answers, raw responses, credentials, DSNs, and content.

## SSE interruption and recovery

The test harness interrupts the production cursor-based
`InterviewEventStreamService` after two events, resumes from
`generation_id:attempt:sequence`, observes `generation_reset` before replacement
chunks, and verifies:

```text
last_event_id_before_disconnect=generation-1:1:2
first_resumed_event_id=generation-1:2:0
duplicate_event_count=0
chunk_payload_in_measurement=false
```

The 360-sample deterministic run contains 30 observations in every recovery cohort.
The synthetic p95 values are 0.22 seconds for warm cohorts and 0.32 seconds for cold
cohorts, below the frozen 5-second Engineering fixture Gate. They are not claimed as
network or Provider capacity evidence.

## Automated fixture Gate result

| Metric | Automated result | Frozen Gate | Sample |
| --- | ---: | ---: | ---: |
| Decision output tokens, maximum | 30 | <= 300 | 120 |
| Follow-up output tokens, maximum | 45 | <= 120 | 120 |
| Provider calls per answer, maximum | 2 | <= 2 | 360 |
| Provider calls per main question, maximum | 4 | <= 4 | 360 |
| Calls after second follow-up, maximum | 0 | == 0 | 60 |
| Retry amplification | 1.00 | <= 1.15 | 240 |
| Decision degradation rate | 0.00 | <= 0.02 | 120 |
| Adaptive Decision p95, cold | 0.57 s | <= 3.0 s | 30/path |
| Adaptive Decision p95, warm | 0.47 s | <= 3.0 s | 30/path |
| Adaptive follow-up E2E TTFT p95, cold | 1.09 s | <= min(3.0, fixed x 1.20) = 1.404 s | 30 |
| Adaptive follow-up E2E TTFT p95, warm | 0.89 s | <= min(3.0, fixed x 1.20) = 1.284 s | 30 |
| Adaptive next-question p95, cold | 0.62 s | <= 3.0 s | 60 |
| Adaptive next-question p95, warm | 0.52 s | <= 3.0 s | 60 |
| SSE resume p95, cold | 0.32 s | <= 5.0 s | 30/policy |
| SSE resume p95, warm | 0.22 s | <= 5.0 s | 30/policy |

Session input tokens, output tokens, and estimated cost are record-only because the
unified authorization has no cumulative budget ceiling. The run records 96,000 input
tokens, 9,000 output tokens, and 0.192 synthetic pricing units across 240 synthetic
session identities. Those values verify aggregation and normalization only.

## Real-evidence integrity

A complete saved/live Provider performance artifact now requires all of the following
before it can be Quality-eligible:

- Provider and exact model identity matching the unified authorization;
- capture run ID and SHA-256 of the local redacted source capture;
- one unique safe trace ID for every attempted Provider request;
- complete input/output/cache Token accounting and per-sample latency;
- a frozen official pricing snapshot with observation time and currency;
- estimated cost equal to a recomputation from the frozen prices and recorded Token
  counts;
- a source-capture file supplied to replay whose bytes match the declared SHA-256.

Hard-stopped or incomplete captures remain useful blocker evidence but cannot become a
Quality PASS. The CLI refuses a synthetic artifact in `saved-replay` mode, refuses a
non-empty run directory, and persists a provider/model/source-capture mismatch before
evaluation without sending a Provider request.

## Automatic review findings closed

1. Initial observability changes did not directly test successful-stream TTFT, empty
   stream behavior, cache aliases, or model-without-usage behavior. Dedicated tests
   now cover each case.
2. A numeric performance artifact could initially claim real origin without binding
   the underlying saved capture. Real artifacts now require capture provenance,
   per-request trace IDs, and replay-time SHA verification.
3. Estimated cost initially had no proof that it came from the recorded Token split.
   Complete real artifacts now carry a frozen pricing snapshot and costs are
   recomputed, including cached input.
4. Identifier fields initially accepted arbitrary strings and could have smuggled
   candidate text into otherwise safe artifacts. They now use a bounded machine-ID
   alphabet; payload fields remain forbidden.
5. Retry, fallback, output-token, and zero-call values could initially be internally
   inconsistent even if the aggregate Gate happened to pass. Model validation now
   rejects those contradictions before metrics are calculated.
6. SSE recovery timing was initially represented only as fixture values. A helper now
   interrupts and resumes the actual cursor-based event iterator and exports IDs and
   timing only, never chunk text.

## Verification

```text
focused T37 and instrumentation tests: 49 passed, 0 failed
follow-up/durable/SSE/telemetry regression: 141 passed, 0 failed
compileall: PASS
diff check: PASS
secret scan: PASS_NO_SECRET_MARKERS
fixture CLI exit: 0
fixture run provider calls: 0
implementation worktree dirty at evidence run: false
```

Machine evidence is published in
`docs/interview-quality-v1-t37-evidence.json`. Detailed synthetic observations remain
under `tmp/interview-quality-v1-provider-runs/t37-offline-full-20260805-v1/` and are
excluded from Git.

## Remaining Quality blocker

T37 has no complete real Provider timing capture because T36 stopped before its first
data request on `MODEL_VERSION_DRIFT`. Automatic model substitution remains forbidden.
Until an authorized model is available and a complete real run supplies exact
fixed/adaptive same-path cohorts, T37 Quality remains BLOCKED. This does not block T38
or later Engineering work without external dependencies.
