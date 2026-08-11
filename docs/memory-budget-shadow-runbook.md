# Memory Budget Shadow Staging Runbook

This how-to guide runs Budget Shadow as a single-axis, synthetic Profile B
observation in the approved isolated Staging boundary. It is for the Memory
Shadow operator and the independent stop owner.

Budget Shadow computes hypothetical selection and budget outcomes. It must not
crop, compress or replace the messages sent to a Provider. The Profile B run
in this guide calls no Provider at all.

## Preconditions

Require all of the following before starting:

- a protected RC Bundle at
  `reports/memory/operational-rc-evidence-v1.json`;
- a protected isolated Staging Bundle at
  `reports/memory/operational-staging-evidence-v1.json`;
- both Bundles issued for the explicitly selected revision and protected by
  the configured HMAC key;
- live PostgreSQL migration and cleanup evidence;
- Knowledge P1 and long-context quality evidence;
- full Python, frontend and browser baselines green;
- durable aggregate metrics available;
- a 24-hour Profile B operation window declaration;
- `memory-shadow-rollback-owner` assigned as stop owner;
- synthetic data only and no real Provider authorization.

The database fingerprint approval remains in force. Keep the DSN and the
approved irreversible fingerprint outside repository artifacts and general
logs.

## Step 1: select the protected prerequisite Bundles

~~~powershell
$validatedRevision = '<approved-revision>'
$rcEvidence = 'reports/memory/operational-rc-evidence-v1.json'
$stagingEvidence = 'reports/memory/operational-staging-evidence-v1.json'
~~~

Do not replace either path with a committed `docs/*.json` record or a Markdown
PASS statement. The Observer verifies both HMAC Receipts, Revision, fixed
Scope, exact Payload type, recomputed Domain Policy, Verification Status,
Promotion Decision and Gate Codes before it reads `POSTGRES_DSN` or opens a
database scope. Any mismatch returns
`GATE=BUDGET_INPUT_EVIDENCE_UNVERIFIED` and writes no output Artifact.

## Step 2: load the approved database target binding

The protected operator environment must contain `POSTGRES_DSN`, the six
`POSTGRES_ACCEPTANCE_*` Approval/target fields, `EVIDENCE_REVISION`,
`EVIDENCE_HMAC_KEY_ID` and `EVIDENCE_HMAC_SECRET_B64`. The approved target
fingerprint binds the full PostgreSQL instance/database identity. Do not write
the DSN, fingerprint, approval Receipt or HMAC secret into the observation.

## Step 3: run the single-axis Profile B observation

The observer creates an immutable in-process Memory configuration with only
Budget mode set to `shadow`. Budget enforcement, compression consumption,
Question Memory consumption, Principal Memory, and the trusted-local Principal
Memory API remain disabled. The configuration is not persisted and the
process returns to disabled by termination.

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.memory_budget_shadow_observe `
  --execute `
  --validated-rc-revision $validatedRevision `
  --staging-preflight-revision $validatedRevision `
  --rc-evidence $rcEvidence `
  --staging-evidence $stagingEvidence `
  --scope-prefix $approvedScopePrefix `
  --output reports/memory/budget-shadow-evidence-v1.json `
  --target-environment isolated-staging `
  --observation-hours 24 `
  --stop-owner-role memory-shadow-rollback-owner `
  --sessions 300
~~~

The deterministic matrix contains exactly 300 synthetic Sessions:

- `zh_hans`: 100;
- `en`: 100;
- `mixed`: 100;
- each of ten scenarios: 30;
- scenarios include long code identifiers, numbers, corrections, negation,
  estimator fallback, long history, current-content preservation, mixed
  structure and replay-shaped input.

No synthetic message content is written to the output artifact. The observer
publishes only allowlisted aggregate metrics to a strict isolated PostgreSQL
prefix, then removes the prefix in a `finally` boundary.

## Step 4: verify zero consumption

The result must report:

~~~text
provider_calls=0
provider_input_change_count=0
mandatory_current_content_losses=0
budget_enforcement=disabled
compression_consumption=disabled
question_memory_consumption=disabled
principal_memory=disabled
configuration_persisted=false
budget_mode_after_observation=disabled
cleanup_residue=0
rollback_verified=true
~~~

`would_select_count`, `would_drop_count`, estimator direction and latency are
hypothetical aggregate observations only. They must not be read back into
context assembly.

## Step 5: interpret latency honestly

Profile B has no Provider call. For a stable denominator it uses a declared
500 ms synthetic end-to-end baseline and adds the measured local Shadow
selection overhead. The record labels this as:

~~~text
latency_source=synthetic_baseline_plus_measured_shadow_overhead
~~~

This is sufficient for the deterministic Profile B gate; it is not production
latency evidence and cannot change `PRODUCTION_OBSERVATION=NOT_RUN`.

## Step 6: close the window and audit the artifact

After the observer exits, confirm all Memory environment modes are disabled.
The observer writes aggregate-only protected Evidence to
`reports/memory/budget-shadow-evidence-v1.json`, evaluates it with the shared
`ShadowEvidencePolicy`, and verifies its signed Receipt after the atomic write.
Its Input Manifest contains logical `operational-rc-evidence` and
`operational-staging-evidence` entries bound to the persisted Bundle bytes and
Receipt digests; local absolute paths are not written to the Artifact.
Do not copy a plain observation JSON into `docs/` or invoke the retired Task 4
acceptance runner as a second source of truth.

The record may contain counts, ratios, latency aggregates, allowlisted
language/scenario dimensions, revision identifiers and stable gate states. It
must not contain a DSN, database fingerprint, generated table prefix, Session,
Principal, Fact, Question, Prompt, Answer, Resume, Excerpt, Source Manifest,
Artifact reference or Provider payload.

## Immediate stop conditions

Stop and restore disabled mode on any of these conditions:

- known-over-budget Provider call greater than 0;
- mandatory current-content loss greater than 0;
- Provider input change greater than 0;
- privacy artifact hit greater than 0;
- Budget/config conflict;
- durable metrics incomplete;
- more than one unavailable observation bucket;
- follow-up error-rate regression greater than 0.5 percentage points at 200+
  samples;
- P95 follow-up latency regression greater than 20% at 200+ samples;
- isolated cleanup residue greater than 0.

For a statistical path with fewer than 200 samples, output
`CONTINUE_OBSERVATION`; do not treat the low sample as either PASS or a
statistical hard stop. Privacy, content-loss, Provider-over-budget and config
hard stops do not receive a low-sample exemption.

## Rollback

1. End the observer process.
2. Confirm Budget mode and all enforcement gates are disabled.
3. Keep compression, Question Memory and Principal Memory disabled.
4. Stop any new observer work; the deterministic Interview path remains
   available.
5. Verify strict-prefix PostgreSQL residue is 0.
6. Preserve only aggregate failure codes and counts.
7. Do not delete migration definitions, graph definitions or immutable
   artifacts.

The trusted-local `GET /api/runtime/memory-budget-shadow` endpoint remains
status-only. It cannot enable Shadow.

## Budget Shadow boundary

Completing this run produces a protected Budget observation. It does not by
itself grant production approval and does not authorize enforcement, Write
Shadow, Read Shadow or production observation.

~~~text
BUDGET_SHADOW_OBSERVATION=RECORDED
BUDGET_ENFORCEMENT=BLOCKED
PRINCIPAL_MEMORY_SHADOW=NOT_RUN
PRODUCTION_OBSERVATION=NOT_RUN
~~~
