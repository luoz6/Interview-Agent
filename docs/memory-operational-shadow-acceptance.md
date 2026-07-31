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

## Run the final gate

After writing the clean-revision regression record to
`docs/memory-operational-regression-evidence.json`:

```powershell
python -m scripts.memory_operational_shadow_acceptance
```

The runner validates the committed safe defaults, confirms that `consume` is
rejected, and requires the base RC revision to be an ancestor of the validation
revision. It writes `docs/memory-operational-shadow-evidence.json` only after all
gates pass.

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
