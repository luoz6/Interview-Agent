# Verify the production Shadow evidence handoff

This how-to is for the evidence maintainer and external approver. It assembles
six already protected production-shadow Bundles into one signed manifest. It
does not change configuration, authorize production observation, or enable
Principal Memory consumption.

## Required inputs

The manifest runner verifies the Receipt, Revision, Scope, strict Payload,
recomputed Policy result, promotion decision, and gate codes of:

1. Production Shadow Approval Request;
2. Production Budget Readiness;
3. Production Shadow Change Preflight;
4. Production Budget Observation;
5. Production Budget Window Decision;
6. Production Budget Acceptance.

The default input and output paths are under `reports/memory/`. Do not provide
the former `docs/memory-production-shadow-evidence-manifest.json`; it was a
static pre-Receipt artifact and is not accepted by the current CLI.

## Build the protected manifest

From the validated repository revision, configure the HMAC signer and each
input revision:

```powershell
$env:EVIDENCE_HMAC_KEY_ID = '<external key id>'
$env:EVIDENCE_HMAC_SECRET_B64 = '<external base64 secret>'
$env:APPROVAL_REQUEST_REVISION = '<approval request revision>'
$env:READINESS_EVIDENCE_REVISION = '<readiness revision>'
$env:PREFLIGHT_EVIDENCE_REVISION = '<change preflight revision>'
$env:OBSERVATION_EVIDENCE_REVISION = '<observation revision>'
$env:WINDOW_EVIDENCE_REVISION = '<window revision>'
$env:ACCEPTANCE_EVIDENCE_REVISION = '<acceptance revision>'
$env:EVIDENCE_REVISION = '<manifest revision>'

& 'F:\python3.11\python.exe' -m scripts.memory_production_shadow_evidence_manifest
```

The runner writes
`reports/memory/production-shadow-evidence-manifest-v1.json` through an atomic
writer and immediately verifies the persisted Bundle. Its Input Manifest binds
all six source Bundles.

## Passing and blocked states

A valid synthetic or non-final chain may have `VerificationStatus=PASS` while
its `PromotionDecision` remains `HOLD`. This is expected and must not be
reinterpreted as production approval.

Any missing input, Receipt failure, wrong Revision or Scope, Payload mismatch,
Policy mismatch, privacy violation, or broken chain returns:

```text
MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=BLOCKED
GATE=<stable code>
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

Repair the failed source, regenerate every downstream Bundle whose Input
Manifest changes, and rerun the manifest command. Never edit a signed Bundle or
its Receipt in place.
