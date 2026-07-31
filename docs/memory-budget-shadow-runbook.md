# Memory Budget Shadow Staging Runbook

This how-to guide runs Budget Shadow as a single-axis, synthetic Profile B
observation in the approved isolated Staging boundary. It is for the Memory
Shadow operator and the independent stop owner.

Budget Shadow computes hypothetical selection and budget outcomes. It must not
crop, compress or replace the messages sent to a Provider. The Profile B run
in this guide calls no Provider at all.

## Preconditions

Require all of the following before starting:

- validated application RC `a982b1f`;
- Task 1 evidence `fb33894`;
- isolated Staging preflight implementation `5280c9d`;
- Task 2 acceptance `STAGING_PREFLIGHT=PASS`;
- live PostgreSQL migration and cleanup evidence;
- Knowledge P1 and long-context quality evidence;
- full Python, frontend and browser baselines green;
- durable aggregate metrics available;
- a 24-hour Profile B operation window declaration;
- `memory-shadow-rollback-owner` assigned as stop owner;
- synthetic data only and no real Provider authorization.

Task 2's database fingerprint approval remains in force. Keep the DSN and the
approved irreversible fingerprint outside repository artifacts and general
logs.

## Step 1: run validate-only preflight while Shadow is disabled

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.memory_budget_shadow `
  --validate-only `
  --target-environment isolated-staging `
  --observation-hours 24 `
  --durable-metrics-ready `
  --postgres-validation-record docs/memory-validation-operational-evidence.json `
  --quality-record docs/memory-validation-operational-evidence.json `
  --knowledge-p1-ready `
  --python-baseline-passed `
  --browser-baseline-passed `
  --staging-preflight-passed `
  --principal-memory-disabled `
  --operation-window-approved `
  --stop-owner-role memory-shadow-rollback-owner
~~~

Accept only:

~~~text
mode=VALIDATE_ONLY
ready=true
configuration_changed=false
~~~

The validation fails if the Staging gate, durable metrics, Knowledge, quality,
Python or browser evidence is missing; if the stop owner/window is absent; if
Question Memory or Principal Memory consumption exists; or if Budget Shadow is
already enabled before validation.

## Step 2: inspect the approved database target

With the approved Staging connection available only in the process
environment, run:

~~~powershell
$inspection = & 'F:\python3.11\python.exe' `
  -m scripts.memory_shadow_staging_preflight `
  --inspect-database-fingerprint | ConvertFrom-Json
$approvedFingerprint = '<separately approved irreversible fingerprint>'
if ($inspection.database_fingerprint -ne $approvedFingerprint) {
  throw 'approved Staging database fingerprint mismatch'
}
~~~

Do not proceed on mismatch. Do not write either the DSN or fingerprint into
the observation artifact.

## Step 3: run the single-axis Profile B observation

The observer creates an immutable in-process Memory configuration with only
Budget mode set to `shadow`. Budget enforcement, compression consumption,
Question Memory consumption, Principal Memory, and the trusted-local Principal
Memory API remain disabled. The configuration is not persisted and the
process returns to disabled by termination.

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.memory_budget_shadow_observe `
  --execute `
  --validated-rc-revision a982b1f `
  --staging-preflight-revision 5280c9d `
  --expected-database-fingerprint $approvedFingerprint `
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
Store only the one-line aggregate JSON as
`docs/memory-budget-shadow-observation.json`, then run the repository artifact
audit and the Task 4 acceptance runner.

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

## Task 3 boundary

Completing this run produces an observation record for Task 4. It does not by
itself grant the Task 4 PASS state and does not authorize enforcement, Write
Shadow, Read Shadow or production observation.

~~~text
BUDGET_SHADOW_OBSERVATION=RECORDED
BUDGET_ENFORCEMENT=BLOCKED
PRINCIPAL_MEMORY_SHADOW=NOT_RUN
PRODUCTION_OBSERVATION=NOT_RUN
~~~
