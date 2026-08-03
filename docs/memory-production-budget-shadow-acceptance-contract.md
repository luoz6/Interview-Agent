# Production Budget Shadow acceptance contract

This reference defines the deterministic three-state decision made from a
sanitized `memory-production-budget-shadow-observation-v1` artifact. The
evaluator is offline and never changes production configuration.

## Decision priority

```text
hard stop present → BLOCKED
no hard stop but insufficient evidence → CONTINUE_OBSERVATION
all gates satisfied → PASS
```

A blocked or insufficient decision exits non-zero and never emits `=PASS`.

## Immediate hard stops

The evaluator blocks on approval/revision/scope/window mismatch, traffic above
the approved cap, mandatory current-content loss, Provider input mutation,
known over-budget Provider calls, privacy hits, Budget configuration conflict,
another memory axis, incomplete durable metrics, Shadow execution error,
deterministic Interview regression, configuration drift, an unclosed window,
unverified rollback/restoration, post-close Shadow events, active listener
residue, or temporary relation residue.

Two consecutive missing expected minute buckets are incomplete metrics. Hard
stops do not wait for statistical significance.

At 200 or more follow-up samples, an error-rate increase above 0.5 percentage
points or P95 latency above 120% of baseline is also blocking.

## Insufficient evidence

The evaluator returns `CONTINUE_OBSERVATION` for zero observed traffic, warm-up
below 30 minutes or 20 samples, total observation below 24 hours or 200
follow-up samples, missing control/Shadow samples, or missing baseline latency.

The current window must already be closed. More observation requires a new
external approval record and prints:

```text
NEW_APPROVAL_WINDOW_REQUIRED=true
```

## Truthful close state

PASS requires:

```text
OBSERVATION_WINDOW=CLOSED
CONFIGURATION_RESTORED=disabled
```

If either condition is not verified, the output reports `NOT_CLOSED` or
`CONFIGURATION_RESTORED=NOT_VERIFIED` and blocks. It never prints a safe state
that the input evidence does not prove.

## Terminal boundaries

Every result retains:

```text
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```

Production Budget Shadow PASS authorizes no next phase. Principal Write Shadow
requires a new plan, release candidate, evidence bundle, and five-role approval.
