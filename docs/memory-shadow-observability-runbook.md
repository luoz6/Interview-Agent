# Memory Shadow aggregate observability runbook

This status view combines the accepted Budget, Principal Write, proposal
quality/lifecycle, and Principal Read Shadow evidence into one read-only
operator decision surface. It cannot enable or mutate a Shadow mode and does
not authorize production observation or Principal Memory consumption.

## Privacy boundary

The status contains aggregate counts only. It exposes no per-principal,
per-session, per-fact, per-question, source, prompt, answer, resume, report, or
provider-payload drill-down. Runtime displays must merge, delay, or suppress
small buckets below 25 observations. Allowed dimensions are limited to stage,
controlled profile, and coarse language bucket.

The current committed inputs contain synthetic controlled observations. Small
failure-matrix and review-label counts therefore describe named synthetic test
fixtures, not people. The same labels must be suppressed or merged before any
authorized observation using non-synthetic traffic.

## Build status

From the validated revision:

```powershell
python -m scripts.memory_shadow_status --status-only --output docs/memory-shadow-status.json
```

The command reads the five committed aggregate evidence artifacts, validates
that no high-cardinality keys are present, and writes a status-only projection.
It never changes environment variables, configuration files, database state,
worker leases, prompts, or interview behavior.

## Decision behavior

- Insufficient samples produce a `*_SAMPLE_INSUFFICIENT` hold code. They never
  produce PASS or allow expansion, but do not claim a hard runtime failure.
- A hard-stop code sets `expansion_allowed=false`,
  `new_shadow_worker_leasing_allowed=false`, and all target modes to `disabled`.
- The deterministic Interview path remains available for every Shadow stop.
- Privacy-scope or Prompt-isolation failures require both operator and privacy
  notification.
- Minimal aggregate evidence is retained; private triggering content is not.

Representative hard stops include mandatory Budget content loss, over-limit
provider calls, error/latency regression at sufficient sample size, automatic
Principal activation, cross-principal or no-Consent writes/selections, public
Knowledge mutation, unsafe lifecycle races, Prompt mutation, cleanup residue,
and proposal privacy/stale-source failures.

The status runner exits non-zero when a hard stop is active. A status endpoint,
if added later, must remain GET/status-only and must never accept a request that
enables Shadow.
