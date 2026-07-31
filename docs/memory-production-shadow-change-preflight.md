# Validate an external production Budget Shadow approval

This how-to is for the operator at the start of an independently approved
Budget Shadow window. It validates an external approval record and repository
gates. It does not change configuration, start workers, deploy a revision, or
begin production observation.

## Inputs from trusted systems

Obtain all of these independently of the repository:

1. the approved JSON record exported to a temporary path outside the workspace;
2. the expected canonical record SHA-256 from the change-management system;
3. the expected deployment scope SHA-256 from approved deployment inventory;
4. the exact deployed Git revision;
5. the current timezone-aware time from the operator environment.

Do not derive the expected digest from the same local record being verified.
Do not copy the approved record into `docs/`, logs, CI artifacts, or the Git
working tree.

## Run the preflight

```powershell
$record = '<external temporary path>'
$expectedRecordSha256 = '<from trusted change system>'
$expectedDeploymentSha256 = '<from approved deployment inventory>'
$deployedRevision = '<exact immutable Git revision>'

& 'F:\python3.11\python.exe' `
  -m scripts.memory_production_shadow_change_preflight `
  --approval-record $record `
  --expected-record-sha256 $expectedRecordSha256 `
  --expected-deployment-scope-sha256 $expectedDeploymentSha256 `
  --current-revision $deployedRevision
```

The command compares the supplied expected record SHA-256 with the canonical
digest of the external record. It verifies phase, revision, deployment digest,
traffic, window, expiry, five approvals, current safe defaults, operational
packet readiness, aggregate hard-stop state, consumption rejection, and
production `NOT_RUN` state.

## Passing result

```text
PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=PASS
EXTERNAL_APPROVAL_RECORD=VERIFIED
REQUESTED_PHASE=BUDGET_SHADOW_ONLY
CONFIGURATION_CHANGED=false
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

PASS means the operator may proceed to the separately approved deployment
change described in `memory-production-budget-shadow-runbook.md`. It does not
mean the validator changed production.

## Current repository-template result

Running the preflight against the repository example must fail with at least:

```text
PRODUCTION_BUDGET_SHADOW_CHANGE_PREFLIGHT=BLOCKED
GATE=APPROVAL_RECORD_NOT_EXTERNAL
GATE=APPROVAL_STATUS_NOT_APPROVED
CONFIGURATION_CHANGED=false
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

No PASS line may appear in that result.

## Other blocking codes

The preflight blocks on:

- record hash mismatch;
- record schema or phase mismatch;
- deployed revision mismatch;
- deployment scope mismatch;
- missing change-ticket binding;
- traffic at 0 or above 1%;
- a window shorter than 24 or longer than 168 hours;
- current time outside the approved window;
- expired approval or expiry beyond the window;
- missing, extra, pending, rejected, malformed, or late approval roles;
- Operational packet not ready;
- safe defaults changed or `consume` accepted;
- production observation already started;
- aggregate Shadow hard stop active;
- any configuration change occurring before preflight completion.

## Aggregate evidence

When an evidence output path is explicitly supplied, the runner writes only
aggregate validation state. It never writes the external record, its path, or
any digest. A BLOCKED record may be committed only when it contains stable gate
codes and no private data.

Delete the temporary external record according to the change system's handling
policy after validation. Do not retain it in the repository.
