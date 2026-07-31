# Production Budget Shadow observation runbook

This how-to is for the named production operator and independent rollback owner
after an explicit, revision-bound approval record exists. It must not be used
while `APPROVAL_STATUS=PENDING`.

The runbook changes one axis only: Budget Shadow. It never enables Budget
enforcement, Context Compression consumption, Question Memory consumption,
Principal Write/Read Shadow, or Principal Memory consumption.

## 1. Hold point: verify approval without changing production

Run the repository packet builder from the exact approved revision:

```powershell
& 'F:\python3.11\python.exe' -m scripts.memory_production_shadow_approval_packet
```

Before approval, the expected output includes:

```text
APPROVAL_STATUS=PENDING
PRODUCTION_OBSERVATION=NOT_RUN
```

That output is a hard hold point. Do not continue. A separate change-management
record must identify the approvers, deployment, immutable revision, start/end
time, traffic percentage, metric destination, rollback owner, and incident
channel. The approval record must not be synthesized by this runner or committed
as if a human decision had occurred.

## 2. Preflight after explicit approval

The operator must verify all of the following in the deployment system and
change record:

- deployed revision equals the approved revision;
- production target identity was independently verified and is not taken from
  repository evidence;
- durable aggregate metrics are reachable and their retention is approved;
- deterministic Interview health is green;
- rollback owner and privacy/security contacts are actively reachable;
- Budget, Compression, Question Memory, and Principal Memory are currently
  disabled;
- no legacy/new configuration conflict exists;
- the canary selector is sticky and capped at 1%;
- no production migration is included;
- the window has a scheduled end and an automatic stop mechanism.

Record only the boolean/count outcome of these checks. Keep deployment locators,
connections, fingerprints, credentials, and private data in the approved secret
and change-management systems, not repository artifacts.

## 3. Approved configuration delta

Only after the hold point is signed may the deployment configuration change the
following canonical keys:

```text
MEMORY_BUDGET_MODE=shadow
MEMORY_BUDGET_SHADOW_ENABLED=true
```

The same change must explicitly retain:

```text
MEMORY_BUDGET_ENFORCEMENT_PREP=false
MEMORY_BUDGET_ENFORCEMENT_INTERVIEW=false
MEMORY_BUDGET_ENFORCEMENT_REVIEW=false
MEMORY_BUDGET_ENFORCEMENT_REPORT=false
MEMORY_COMPRESSION_MODE=disabled
MEMORY_COMPRESSION_SHADOW_ENABLED=false
MEMORY_LONG_TERM_MODE=disabled
MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false
MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false
MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false
```

Do not mix canonical `MEMORY_*` keys with legacy `CONTEXT_*` keys. Preflight
must fail on disagreement. Apply the change through the approved deployment
system; do not edit `.env.example` or commit production secrets.

## 4. Observe one axis

Budget Shadow may compute and persist aggregate hypothetical outcomes only:

- estimator direction/error where provider counts are available;
- would-select, would-drop, and fallback counts;
- mandatory-content-loss and over-limit-call counts;
- Provider input mutation count;
- error-rate and latency deltas;
- coarse language bucket and sample sufficiency;
- durable metrics completeness and cleanup/worker health.

It must not crop, truncate, compress, replace, or reorder the Provider request.
The deterministic Interview path remains authoritative.

Review the first samples immediately, then at the approved cadence. A minimum
24-hour window is required, but time alone is insufficient. Error/latency
promotion decisions require at least 200 follow-up samples. Low-volume paths
remain `CONTINUE_OBSERVATION` until their sample requirement is met.

## 5. Automatic stop

Stop immediately, without waiting for statistical significance, on:

- mandatory current-content loss greater than 0;
- Provider input mutation greater than 0;
- known over-limit Provider call greater than 0;
- privacy artifact hit greater than 0;
- another memory axis or enforcement gate enabled;
- missing/incomplete durable metrics beyond the approved tolerance;
- private data or high-cardinality locator in evidence;
- deterministic Interview regression attributable to Shadow.

At 200 or more samples, also stop when error-rate delta exceeds 0.5 percentage
points or P95 latency delta exceeds 20%.

The operator initiating stop does not need expansion approval. Notify the change
owner immediately; notify privacy/security owners for any privacy, identity,
artifact, or input-mutation event.

## 6. Roll back

Use the deployment system to apply the safe target:

```text
MEMORY_BUDGET_MODE=disabled
MEMORY_BUDGET_SHADOW_ENABLED=false
```

Confirm all enforcement, Compression, Question Memory, and Principal Memory
keys remain disabled. Stop new Shadow worker leasing, keep deterministic
Interview traffic available, and do not delete migrations, tombstones, or
immutable evidence required for investigation.

After rollback, verify:

- new Budget Shadow events stop;
- deterministic Interview health is green;
- active test/temporary listeners are 0;
- isolated/temporary relation residue is 0;
- only aggregate failure code/count evidence remains;
- committed repository defaults are unchanged.

## 7. Close the window

At the scheduled end, restore disabled mode even if the observation is healthy.
Produce an aggregate production observation record bound to the approved
revision and window. It must distinguish PASS, BLOCKED, and
`CONTINUE_OBSERVATION`; it must not claim Write Shadow or Read Shadow approval.

Any next phase requires a new approval packet. In particular:

```text
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
```
