# Principal Memory Write Shadow Staging Runbook

This how-to runs Task 5 with synthetic Principals in the approved isolated
Staging boundary. It creates only unconfirmed proposals and never injects a
fact into interview context, scoring, reports or public knowledge.

## Preconditions

- `BUDGET_SHADOW_STAGING=PASS` from Task 4;
- validated application RC `a982b1f` and isolated Staging gate `5280c9d`;
- synthetic data only; no real Provider authorization;
- explicit test Identity and operation-time Consent;
- strict isolated PostgreSQL prefix and externally protected approval Receipt;
- deletion path and independent stop owner available.

Keep Budget enforcement, compression consumption, Question Memory consumption,
Read Shadow and the trusted-local Principal API disabled.

## Execute the synthetic matrix

Load the approved target binding and Evidence signing key from the protected
operator environment. `POSTGRES_ACCEPTANCE_APPROVED_FINGERPRINT` is the full
Owned Scope target fingerprint, not the former short CLI fingerprint. Then run:

~~~powershell
& 'F:\python3.11\python.exe' -m scripts.principal_memory_write_shadow `
  --execute `
  --scope-prefix $approvedScopePrefix `
  --output reports/memory/write-shadow-evidence-v1.json `
  --samples 300
~~~

Required protected environment names are `POSTGRES_DSN`,
`POSTGRES_ACCEPTANCE_APPROVAL_ID`,
`POSTGRES_ACCEPTANCE_APPROVAL_RECEIPT_SHA256`,
`POSTGRES_ACCEPTANCE_APPROVED_FINGERPRINT`,
`POSTGRES_ACCEPTANCE_DATABASE_ALLOWLIST`,
`POSTGRES_ACCEPTANCE_APPROVAL_EXPIRES_AT`, `EVIDENCE_REVISION`,
`EVIDENCE_HMAC_KEY_ID` and `EVIDENCE_HMAC_SECRET_B64`.

The process enables only an immutable in-process `write_shadow` configuration.
It uses a fixed structured extractor and makes no Provider call. Each operation
rechecks current Identity, current `proposal_write` Consent, finished/deletion
state, source state version, authoritative message/excerpt and taxonomy.

The 300 positive cases use separate synthetic Principals and Sessions. The
fault matrix additionally covers:

- Identity unavailable and changed;
- Consent revoked after enqueue;
- Session deletion after enqueue;
- source version changed;
- source excerpt mismatch;
- non-allowlisted taxonomy;
- inferred accessibility preference;
- extractor failure;
- event replay and eight concurrent workers.

## Required invariants

Accept the observation only when every value under `hard_invariants` is zero,
including proposals without Consent, cross-Principal writes, source mismatch,
free-text fact values, automatic activation/confirmation, inferred
accessibility, writes during deletion, public Knowledge writes, interview
behavior changes and privacy artifact hits.

The positive result must also report:

~~~text
authority=model_proposed
final_status=proposed
proposal_created_count=300
proposed_fact_count=300
duplicate_fact_count=0
provider_calls=0
read_shadow=disabled
trusted_local_api=disabled
configuration_persisted=false
cleanup_residue=0
rollback_verified=true
production_observation=NOT_RUN
~~~

## Stop and rollback

On any invariant, Consent, isolation, migration, storage or cleanup failure:

1. end the Write Shadow process;
2. keep long-term mode disabled for subsequent processes;
3. stop new worker leasing;
4. retain only aggregate gate codes and counts;
5. allow `OwnedPostgresScope` to verify Ownership, purge the exact approved
   synthetic prefix and require a zero-residue Cleanup Receipt;
6. leave the deterministic Interview path available;
7. do not activate, confirm or restore any proposal;
8. do not modify public Knowledge, migration definitions or immutable
   artifacts.

## Evidence boundary

The observation may store aggregate proposal outcomes, rejection categories,
taxonomy-category counts, concurrency/replay counts, latency and invariant
counts. It must not store a DSN, database fingerprint, exact prefix, Principal,
Session, Fact or Question ID, normalized value, source digest, Prompt, Answer,
Excerpt, Resume or Provider payload.

Task 5 completion records only:

~~~text
PRINCIPAL_WRITE_SHADOW_OBSERVATION=RECORDED
AUTOMATIC_ACTIVE=0
PRINCIPAL_READ_SHADOW=NOT_RUN
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
~~~

Task 6 must independently review proposal quality before Read Shadow can run.
