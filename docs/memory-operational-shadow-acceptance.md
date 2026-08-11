# Operational Memory Shadow acceptance

This how-to assembles the release-candidate, isolated Staging, Budget, Principal
Write, proposal quality, Principal Read zero-injection, Consent/deletion,
old-backup restore, durable metrics, privacy, security, fairness, and Knowledge
Firewall evidence into one machine gate.

Passing this gate permits submitting production Shadow approval material. It
does not grant production approval, enable a Shadow mode, authorize a canary, or
authorize Principal Memory consumption.

## Preconditions

Run from a clean detached worktree at the validation revision. Keep all memory
modes and trusted-local mutation APIs disabled. Use only the isolated PostgreSQL
test scope and synthetic/browser fixtures. Do not call real model or embedding
providers.

The regression evidence must record exact results for:

- full Python tests;
- approved live PostgreSQL runtime tests;
- frontend production build;
- full Playwright browser suite;
- compileall and `git diff --check`;
- listener and isolated PostgreSQL relation cleanup.

It must contain counts and states only, with no DSN, database/prefix locator,
subject/object locator, source data, Prompt, answer, resume, report, normalized
fact, or provider payload.

The following eleven inputs must be signed Evidence Bundles. The runner verifies
each Receipt, Revision, Scope, strict domain Payload, recomputed Policy result,
verification status, promotion decision, and gate codes before evaluating the
operational gate:

| Input | Default path | Required scope |
|---|---|---|
| RC | `reports/memory/operational-rc-evidence-v1.json` | `memory.operational-rc.controlled` |
| Regression | `reports/memory/operational-regression-evidence-v1.json` | `memory.operational-regression.controlled` |
| Staging preflight | `reports/memory/operational-staging-evidence-v1.json` | `memory.staging-preflight.controlled` |
| Aggregate status | `reports/memory/operational-status-evidence-v1.json` | `memory.shadow-status.controlled` |
| Security review | `reports/memory/operational-security-evidence-v1.json` | `memory.shadow-security.controlled` |
| Proposal review | `reports/memory/proposal-review-evidence-v1.json` | `memory.proposal-review.controlled` |
| Budget Shadow | `reports/memory/budget-shadow-evidence-v1.json` | `memory.budget-shadow.controlled` |
| Principal Write Shadow | `reports/memory/write-shadow-evidence-v1.json` | `memory.write-shadow.controlled` |
| Principal Read Shadow | `reports/memory/read-shadow-evidence-v1.json` | `memory.read-shadow.controlled` |
| Consent lifecycle drill | `reports/memory/lifecycle-shadow-evidence-v1.json` | `memory.lifecycle-shadow.controlled` |
| Old-backup restore drill | `reports/memory/restore-drill-evidence-v1.json` | `memory.shadow.restore-drill` |

Set `PROPOSAL_REVIEW_REVISION` to the Proposal Review Bundle revision and
`OPERATIONAL_INPUT_REVISION` to the common revision of the other ten Bundles.
Set `EVIDENCE_REVISION` to the new Operational Bundle revision. Configure
`EVIDENCE_HMAC_KEY_ID` and `EVIDENCE_HMAC_SECRET_B64` for Receipt verification
and output signing. A missing input, wrong Revision or Scope, invalid Receipt,
Payload mismatch, or Policy mismatch fails closed with
`OPERATIONAL_INPUT_EVIDENCE_UNVERIFIED`.

RC, Regression, Staging, Status, and Security are published through one Profile
CLI. The source record remains outside the trusted Bundle until its SHA-256 is
provided explicitly; the publisher applies strict field types, a domain-specific
Payload and Policy, synthetic-data attestation, Receipt signing, atomic write,
and post-write verification:

```powershell
$record = 'reports/memory/records/rc-record.json'
$digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $record).Hash.ToLowerInvariant()
python -m scripts.memory_operational_input_evidence rc `
  --input-record $record `
  --expected-input-sha256 $digest `
  --synthetic
```

Use the same command with the `regression`, `staging`, `status`, and `security`
profiles for records with these source schemas:

| Profile | Required source-record schema |
|---|---|
| `rc` | `memory-validation-operational-evidence-v1` |
| `regression` | `memory-operational-regression-evidence-v1` |
| `staging` | `memory-shadow-staging-preflight-v1` in `EXECUTE` mode |
| `status` | `memory-shadow-status-v1` |
| `security` | `memory-shadow-security-review-v1` |

Do not copy these intermediate records into `docs/` or treat them as approval
evidence. Only the signed Bundle under `reports/memory/` is an Operational input.

## Run the final gate

After all eleven protected inputs have been written:

```powershell
python -m scripts.memory_operational_shadow_acceptance
```

The runner validates the committed safe defaults, confirms that `consume` is
rejected, and requires the base RC revision to be an ancestor of the validation
revision. It writes `reports/memory/operational-shadow-evidence-v1.json` only
after all gates pass. Its `input_manifest` contains all eleven verified Bundles.

## Passing output

```text
MEMORY_SHADOW_RC=REPRODUCIBLE
BUDGET_SHADOW_STAGING=PASS
PRINCIPAL_WRITE_SHADOW_STAGING=PASS
PRINCIPAL_READ_SHADOW_ZERO_INJECTION_STAGING=PASS
CONSENT_DELETION_RESTORE_DRILL=PASS
PRODUCTION_SHADOW_APPROVAL_REQUIRED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

`PRODUCTION_SHADOW_APPROVAL_REQUIRED` means the evidence packet may be submitted
to the production change process. It is not an approval result.

## Blocked output

On failure, the runner prints `MEMORY_OPERATIONAL_SHADOW=BLOCKED`, one or more
stable `GATE=<code>` lines, and the final consumption/production states. It must
not print READY or PASS in the same result. Repair the failed gate, rerun the
complete clean-revision regression, and regenerate both evidence records.
