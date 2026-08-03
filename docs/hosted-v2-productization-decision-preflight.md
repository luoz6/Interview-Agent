# Verify a Hosted V2 productization decision

Use this preflight only after the authorized external decision system contains a current Hosted Multi-user V2 `GO` record. The tool is read-only: it does not change application configuration, deploy code, contact a production service, or authorize Tasks 4–34.

## What the preflight proves

A `PASS` proves that one external record:

- is outside the repository and matches the separately supplied canonical JSON SHA-256;
- binds the current repository revision and current ADR content;
- selects `HOSTED_MULTI_USER_V2` with a deployment-scoped multi-user model;
- preserves Local V1 and keeps the Data-use Spec as a separate gate;
- prohibits automatic account-memory inheritance;
- supplies concrete regions, OIDC Provider class, support/on-call model, and revalidation trigger;
- is current for no more than 180 days;
- contains independent approvals from every required function;
- is evaluated while the Task 0 baseline and disabled defaults remain intact.

A `PASS` unblocks only Task 2 review. It does not authorize OIDC implementation, real candidate processing, Principal Memory Write/Read, C1-A, or production configuration changes.

## Keep the record external

Store the decision record and its expected digest in the authorized decision/change system. Do not place the record, path, digest, approver references, ticket references, regions, Provider contract details, or deployment identifiers in Git.

The JSON object must use schema `hosted-v2-productization-decision-v1` and contain exactly the fields listed in the ADR's external decision record contract, plus:

```text
adr_sha256
expires_at
local_v1_unchanged = true
data_use_spec_still_required = true
account_recovery_model = NO_AUTOMATIC_MEMORY_INHERITANCE
approvals.<role>.decision = APPROVED
approvals.<role>.external_ref
approvals.<role>.decided_at
```

Required roles are Product, Change Owner, Operations, Privacy, Security, Fairness, Accessibility, Interview Quality, and Legal.

The decision record digest is calculated from JSON serialized with sorted keys and compact separators. The ADR digest is calculated from UTF-8 text with line endings normalized to LF. These canonical forms prevent formatting and platform line-ending differences from changing the decision binding.

## Run the verification

From the repository root, run:

```powershell
python -m scripts.hosted_v2_productization_preflight `
  --record <external-record-path> `
  --expected-record-sha256 <digest-from-external-system>
```

Do not paste the external values into a committed script, shell transcript, test fixture, or Markdown file.

## Interpret the result

A successful verification prints only low-sensitivity state:

```text
HOSTED_PRODUCTIZATION_DECISION_PREFLIGHT=PASS
EXTERNAL_PRODUCTIZATION_DECISION=VERIFIED_GO
TASK_2_DATA_USE_REVIEW=UNBLOCKED
TASKS_4_TO_34=BLOCKED_PENDING_DATA_USE_APPROVAL
CONFIGURATION_CHANGED=false
REAL_CANDIDATE_PROCESSING=PROHIBITED
```

A failure prints `BLOCKED`, stable gate codes, and the still-closed authorization states. It never prints raw record fields or approval references. Resolve the external record or repository mismatch and run the preflight again; do not edit the tool to suppress a gate.

## Revalidation

Run the preflight again after any change to the ADR, repository revision, product scope, deployment model, regions, OIDC Provider class, recovery model, ownership, or approval validity. A prior `PASS` is not reusable for the Data-use approval or a production phase.
