# Interview Quality V1 Gate 3 automatic review

## Outcome

```text
implementation_revision=77f70d0ccfafb31e422c12086481d0d7eb1a3060
implementation_tree=0e69d5e9a1c39eaef63b4b09f01dfd232f68f35e
engineering_status=PASS
quality_status=BLOCKED
overall_status=BLOCKED
automatic_review=PASS
open_findings=0
provider_calls=0
```

Gate 3 Engineering is complete. T28–T35, the Engineering portions of T36 and
T37, and T38 are implemented and regression-clean. Decision and Generation remain
separate durable stages; follow-up count is bounded to 0–2; retries, fallback,
fencing, cursor recovery, call accounting, and synthetic performance gates retain
their previously published passing evidence. T38 now presents those states
truthfully without exposing chain-of-thought, internal gap reasoning, confidence, or
Decision reasons.

Gate 3 Quality is deliberately not declared PASS. The unified authorization permits
DeepSeek `deepseek-chat` with unlimited budget, requests, and tokens, but that exact
model is absent from the observed official model list and pricing table. Automatic
substitution to `deepseek-v4-pro` or `deepseek-v4-flash` is prohibited. The Provider
preflight therefore stopped before the first data request. The 100-case follow-up
dataset also remains pending independent review, and no eligible fixed/adaptive human
blind review or real same-path latency capture exists. These conditions block only
Quality; they do not stop Phase 4 Engineering.

## T38 truthful UI and recovery contract

- Adaptive submissions first show `正在分析这次回答`; fixed policy never presents a
  dynamic Decision stage.
- Once follow-up generation is selected but before the first token, the UI shows
  `正在组织追问`; the first chunk transitions to `追问生成中`.
- Next-question and degraded completion use `回答已记录，进入下一题` and
  `本题将继续到下一题` respectively.
- A refreshed active command shows `正在恢复上一条追问` and keeps that recovery state
  stable while replayed status, reset, and chunk events arrive.
- The main-question index is independent of the separately bounded `追问 0 / 2`,
  `追问 1 / 2`, and `追问 2 / 2` progress.
- The detailed turn-state region is atomic and polite. The conversation log is no
  longer a live region, so recovered chunks are not repeatedly announced.
- SSE emits a bounded `generation_pending` status before chunks. Its payload contains
  only the stage and generation identifier, never gap, confidence, reason, prompt,
  answer, or raw response data.

## Recovery finding closed by automatic review

The first implementation returned an active stream URL only after a Generation row
existed. A browser refreshing during the earlier Decision stage could display a
pending state but could not reconnect to observe completion. The event stream already
supports waiting for a Generation to appear, so the public snapshot now exposes the
command stream whenever an active command exists, including when
`active_generation_id` is null. A direct service test proves the Decision-stage
snapshot is recoverable and remains free of internal reasoning fields.

Browser recovery also verifies that replacement attempts erase abandoned partial
text, a persisted recovered follow-up appears once, Last-Event-ID resumes after the
cursor without duplicate chunks, and legacy sessions retain the legacy contract.

## Automatic fixture and performance evidence

T36's deterministic 100-case fixture retains:

- adaptive action accuracy 1.00;
- latest-answer relevance approximately 0.9667;
- unnecessary strong-answer follow-up rate 0.00;
- effective correction rate 1.00;
- repeated-original-question, multi-question, and reference-leak rates 0.00;
- Decision parse rate 0.98;
- every sequence within 0–2 follow-ups and zero Provider calls after the second
  follow-up.

T37's 360-observation synthetic measurement run retains all 22 automated results as
19 PASS and 3 RECORDED. Decision and follow-up token bounds, calls per answer and main
question, retry amplification, degradation rate, exact fixed/adaptive cohort
comparison, cold/warm latency, and SSE resume timing pass their frozen Engineering
gates. These are synthetic Engineering fixtures, not claims about real Provider
capacity, cost, or latency.

## Verification on the frozen implementation revision

```text
backend targeted regression: 122 passed, 0 failed
frontend Vitest/RTL: 20 passed, 0 failed
browser recovery and interview UI: 13 passed, 0 failed
Vite production build: PASS (4592 modules)
compileall: PASS
node syntax checks: PASS
diff check: PASS
secret scan: PASS_NO_SECRET_MARKERS
screenshots captured: 0
```

The browser suite ran on isolated ports 8111/4273 because another test suite from the
original workspace occupied the defaults. The test launcher now accepts optional
backend/frontend ports while retaining 8011/4173 as defaults. Browser fixtures were
also updated to start interviews from an immutable Plan Revision and matching hash,
instead of the obsolete pre-revision API payload.

The repository exposes an `npm run check` script but does not install an `eslint`
binary in the frozen lockfile, so that non-Gate command is recorded as
`NOT_RUN_TOOL_NOT_INSTALLED`. No dependency was downloaded or lockfile changed merely
to manufacture a lint result. Required Vitest/RTL, production build, browser, Python,
and syntax checks all passed.

## Remaining Quality blockers and continuation

1. `MODEL_VERSION_DRIFT`: authorized `deepseek-chat` is unavailable in the current
   observed official model/pricing lists; no automatic replacement is allowed.
2. `PENDING_INDEPENDENT_REVIEW`: the 100-case follow-up dataset is not yet eligible
   for fixed/adaptive human blind acceptance.
3. `BLOCKED_NOT_RUN_REAL_PROVIDER`: there is no real same-path Provider performance
   capture for T37.

Accordingly, `engineering_status=PASS`, `quality_status=BLOCKED`, and
`overall_status=BLOCKED`. Per the frozen plan, Phase 4 Engineering continues without
waiting for these external Quality dependencies.

Machine-readable evidence is in
`docs/interview-quality-v1-gate-3-evidence.json`.
