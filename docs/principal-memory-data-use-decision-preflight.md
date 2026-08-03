# Verify the Principal Memory Data-use decision

Use this read-only preflight only after the external systems contain both a current Hosted V2 Productization `GO` record and a current Principal Memory Production Data-use `APPROVED` record.

The preflight verifies Task 1 again before evaluating Task 2. A Productization record cannot be omitted, replaced by repository permission, or bypassed with a Data-use approval.

## What a pass means

A `PASS` verifies:

- both records resolve outside the repository and match separately supplied canonical JSON digests;
- both records bind the current repository revision and their current canonical Markdown documents;
- Productization is a current `GO` under the required identity, recovery, region, and ownership model;
- Data-use approval binds one deployment-scope digest and exactly four independent purposes;
- regions match the Productization decision;
- taxonomy, candidate notice, retention, Provider/subprocessor, transfer, review, deletion/export, and disable contracts are concrete;
- Product, Privacy, Security, Legal, Fairness, and Operations independently approve;
- Accessibility and Interview Quality complete candidate-copy reviews;
- the Task 0 baseline and disabled production defaults remain intact.

A pass removes only the two decision gates. Budget observation and repository-only Control Foundation work may then follow their own plans. Write Shadow, Read Shadow, C1-A, and every production configuration change still require later independent gates.

## Keep all decision records external

Do not store either record, either digest, the deployment-scope digest, regions, Provider/subprocessor details, approver references, reviewer references, or external record paths in Git.

The Data-use record must use schema `principal-memory-production-data-use-decision-v1` and exactly the fields enforced by the preflight. Its purposes must be:

```text
proposal_write
fact_storage
read_shadow
assist_c1a
```

The approved deletion/export SLO is `24_HOURS`; the approved disable SLO is `NEXT_ASSEMBLY_MAX_60_SECONDS`. A different value requires a revised specification rather than a preflight exception.

## Run the verification

```powershell
python -m scripts.principal_memory_data_use_preflight `
  --productization-record <external-productization-record> `
  --expected-productization-sha256 <external-productization-digest> `
  --data-use-record <external-data-use-record> `
  --expected-data-use-sha256 <external-data-use-digest> `
  --expected-deployment-scope-sha256 <external-scope-digest>
```

Do not paste these values into a committed transcript, fixture, environment example, or document.

## Interpret the result

A pass prints:

```text
PRINCIPAL_MEMORY_DATA_USE_PREFLIGHT=PASS
HOSTED_PRODUCTIZATION_DECISION=VERIFIED_GO
PRODUCTION_DATA_USE_SPEC=VERIFIED_APPROVED
BUDGET_AND_CONTROL_FOUNDATION_DECISION_GATES=UNBLOCKED
CONFIGURATION_CHANGED=false
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
```

A failure prints `BLOCKED` and stable content-free gate codes. It does not print decision fields or references. Resolve the external or repository mismatch; never suppress or weaken a gate to obtain `PASS`.
