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

The current protected inputs contain synthetic controlled observations. Small
failure-matrix and review-label counts therefore describe named synthetic test
fixtures, not people. The same labels must be suppressed or merged before any
authorized observation using non-synthetic traffic.

The status builder accepts only these protected Bundles:

| Input | Default path | Required Scope |
|---|---|---|
| Budget | `reports/memory/budget-shadow-evidence-v1.json` | `memory.budget-shadow.controlled` |
| Principal Write | `reports/memory/write-shadow-evidence-v1.json` | `memory.write-shadow.controlled` |
| Proposal Review | `reports/memory/proposal-review-evidence-v1.json` | `memory.proposal-review.controlled` |
| Lifecycle | `reports/memory/lifecycle-shadow-evidence-v1.json` | `memory.lifecycle-shadow.controlled` |
| Principal Read | `reports/memory/read-shadow-evidence-v1.json` | `memory.read-shadow.controlled` |

## Build status

From the validated revision:

```powershell
$inputRevision = '<validated-revision>'
& 'F:\python3.11\python.exe' -m scripts.memory_shadow_status `
  --status-only `
  --input-revision $inputRevision `
  --proposal-review-revision $inputRevision `
  --output reports/memory/records/shadow-status-record.json
$record = 'reports/memory/records/shadow-status-record.json'
$digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $record).Hash.ToLowerInvariant()
& 'F:\python3.11\python.exe' -m scripts.memory_operational_input_evidence status `
  --input-record $record `
  --expected-input-sha256 $digest `
  --synthetic `
  --output-revision $inputRevision
```

The first command verifies each HMAC Receipt, Revision, fixed Scope, exact
Payload type, recomputed Domain Policy, Verification Status, Promotion Decision
and Gate Codes. The three status panels are projected only from protected,
low-cardinality Payload fields. The builder no longer reads the former Budget,
Write, Proposal, Lifecycle or Read `docs/*.json` observations. Missing metrics,
wrong Receipt/Revision/Scope/Payload, or a mismatched Policy state returns
`GATE=STATUS_INPUT_EVIDENCE_UNVERIFIED` and writes no status record.

The command never changes environment variables, configuration files, database
state, worker leases, prompts, or interview behavior.

The status JSON is an intermediate aggregate record. Operational Shadow accepts
only the strict, signed `reports/memory/operational-status-evidence-v1.json`
Bundle created by the second command; it never reads the intermediate record or
the former committed `docs/memory-shadow-status.json` file.

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

## Production Budget Shadow aggregate export

An approved Production Budget Shadow window uses a separate offline export
contract. The trusted metrics system may export only booleans, non-negative
counts/rates, the public approved Git revision, approved/observed traffic,
window duration, and fixed low-cardinality language/path buckets. The export
must remain outside the repository until it is sanitized.

The export must not contain the external approval record or its path, approval,
deployment, ticket, or approver digests, production locators, DSNs, credentials,
session/principal/fact/question/message/artifact IDs, Prompt, answer, resume,
report, source excerpt, Provider payload, or free-text metric labels.

Use `scripts.memory_production_budget_shadow_observation` to create the
sanitized artifact and
`scripts.memory_production_budget_shadow_acceptance` to make the three-state
decision. These scripts do not query the metrics backend. Buckets below 30
production observations are marked insufficient for bucket-specific claims.
