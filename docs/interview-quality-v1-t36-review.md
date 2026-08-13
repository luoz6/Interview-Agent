# Interview Quality V1 T36 automatic review

## Outcome

```text
implementation_revision=c6c83b34318e74992c735e3aebfa8e5dab85e9f3
implementation_tree=41aea9442d03db333d5522e82e9914f239dbb65b
engineering_status=PASS
automated_review=PASS
automated_fixture_quality_status=PASS
quality_status=BLOCKED
provider_smoke_status=BLOCKED_MODEL_VERSION_DRIFT
independent_review_status=PENDING
provider_calls=0
```

T36's Engineering path is complete and regression-clean. The runner implements all
seven evaluation layers: schema/parser, deterministic rules, saved-output replay,
Provider smoke preflight, full golden-set execution, fixed/adaptive comparison, and
sequence-level zero-to-two follow-up replay. Automated fixture metrics pass every
frozen GateConfig threshold, but this is not a T36 Quality PASS.

The real Provider smoke stopped before the first evaluation request because the
authorized `deepseek-chat` model is absent from DeepSeek's official model-list
endpoint and current official pricing table. The endpoint and pricing page both list
only `deepseek-v4-flash` and `deepseek-v4-pro`. Automatic substitution is prohibited,
so the accurate status is `BLOCKED_MODEL_VERSION_DRIFT`. The 100-case independent
blind review also remains pending, with every case still `gate_eligible=false`.

## Implemented evaluation path

- Decision and Generation use separate frozen prompts and output budgets: 300 and
  120 output tokens respectively. Structured Decision usage and Provider model
  metadata remain outside `DecisionContract`.
- Saved response artifacts bind dataset hash, Provider/model identity, per-request
  usage, cache usage, latency, response identity, and capture completeness. A
  hard-stopped partial capture cannot be replayed as complete evidence.
- Replay executes the real `FollowupDecisionExecutionService`, including parser
  failure, timeout, invalid-output, Provider-failure, low-confidence, duplicate-gap,
  generation retry, and duplicate-question safety paths.
- Raw Generation output and user-visible text are separate. A duplicate rejected by
  runtime safety is not counted as displayed, but it still reduces delivery/relevance
  quality because all expected follow-up cases remain in the denominator.
- `fixed_v1` is executed through its deterministic server-owned policy. The runner
  reports fixed/adaptive comparison by train, dev, and blind-test partition. A
  development-purpose command cannot open the blind-test partition.
- Twenty complete two-step sequences are replayed. Ten sequences reach a second
  follow-up; every subsequent transition is deterministic `next_question` with zero
  Provider calls.
- Run directories are immutable by convention: the CLI refuses to reuse a non-empty
  run ID, preventing stale attempt mixing.

## Automated fixture result

The deterministic full-set run evaluated 100 adaptive attempts and 100 fixed-policy
attempts:

| Metric | Result | Frozen Gate |
| --- | ---: | ---: |
| adaptive action accuracy | 1.0000 | >= 0.90 |
| fixed action accuracy | 0.6200 | comparison only |
| maximum-gap type accuracy | 1.0000 | >= 0.85 |
| latest-answer relevance | 0.9667 (58/60) | >= 0.95 |
| unnecessary follow-up on strong answers | 0.0000 | <= 0.20 |
| effective correction | 1.0000 (20/20) | >= 0.90 |
| visible original-question repetition | 0.0000 | <= 0.02 |
| multi-question rate | 0.0000 | <= 0.02 |
| reference-answer leak count | 0 | == 0 |
| structured Decision parse rate | 0.9800 (98/100) | >= 0.98 |
| follow-up count within 0–2 | 1.0000 | == 1.0 |
| saved replay action drift | 0 | == 0 |

Two adversarial Generation fixtures repeatedly returned the original main question.
Both were rejected after the bounded retry path, neither was displayed, and both
ended safely at `next_question`. The invalid structured-output fixtures now correctly
produce a 98% parse rate rather than being hidden behind a valid fallback Decision.

The saved-artifact rerun produced an identical metrics file and zero replay action
drift. It made no Provider calls. Because the source is explicitly
`synthetic_fixture`, these results prove harness and policy behavior only; they do not
claim real-model quality.

## Provider preflight result

The T36 Provider runner verified all non-model boundaries successfully:

- unified authorization ID and hash match;
- dataset and GateConfig hashes match their frozen manifests;
- all selected data is synthetic and the redaction preflight passes;
- credentials are present without appearing in artifacts or logs;
- evidence storage is writable;
- the environment's `deepseek-v4-pro` setting is ignored in favor of the explicit
  authorized identity;
- no fallback or automatic model substitution is enabled.

It then observed:

```text
authorized_model=deepseek-chat
official_model_ids=deepseek-v4-flash,deepseek-v4-pro
official_priced_models=deepseek-v4-flash,deepseek-v4-pro
hard_stop=MODEL_VERSION_DRIFT
first_data_request_sent=false
```

No Provider result, usage, cost, or PASS was fabricated. Unlimited authorization
does not override this model-identity hard stop.

## Automatic review findings closed

1. Invalid Provider output was initially counted as parsed because the fallback
   Decision itself was valid. Parse status now reflects the structured Provider
   response, giving the correct 98/100 boundary result.
2. Relevance originally used only displayed questions as its denominator. It now uses
   all 60 expected-follow-up cases, so rejected or missing Generation output cannot
   improve the metric by reducing the sample.
3. Synthetic fixture invocations were initially easy to confuse with real calls.
   Manifests now separate `provider_invocations_this_run=0` from
   recorded/simulated replay invocations.
4. A response without usage metadata could previously reach an internal retry. The
   live evaluator now stops before another outbound request; the timeout test proves
   one call and no unmetered retry.
5. Complete real saved artifacts now require input/output tokens, latency, and matching
   Provider model metadata for every request. Partial hard-stop captures remain local
   evidence but cannot enter complete replay metrics.
6. Reusing a run ID could mix stale files with new evidence. Non-empty run directories
   are now rejected.

## Verification

```text
focused T36 tests: 40 passed, 0 failed
follow-up wide regression: 181 passed, 2236 deselected, 0 failed
diff check: PASS
secret scan: PASS_NO_SECRET_MARKERS
```

Machine evidence is published in
`docs/interview-quality-v1-t36-evidence.json`. Local detailed attempts remain under
`tmp/interview-quality-v1-provider-runs/` and are intentionally excluded from Git.

## Remaining Quality blockers

1. A new authorization or an authoritative restoration/alias statement for
   `deepseek-chat` is required before T36 can send real Provider evaluation data. The
   existing authorization does not permit silent v4 substitution.
2. The 100-case fixed/adaptive independent blind review must be completed and recorded
   before Quality can pass.

These blockers do not prevent T37/T38 Engineering or later phases that have no real
Provider dependency.
