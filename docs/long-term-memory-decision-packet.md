# Build the public long-term-memory decision packet

This offline tool packages the public v0.2 roadmap, Task 0 baseline, Hosted V2 ADR, Production Data-use draft, preflight instructions, external-record schemas, and verifier source for external review.

The packet is deliberately `PENDING`. It is not an approval record and cannot be used as Productization `GO`, Data-use `APPROVED`, production authorization, or evidence that real candidate processing occurred.

## Safety properties

The generator:

- reads only a fixed repository allowlist;
- requires every allowlisted file to be tracked and unchanged from the current HEAD;
- normalizes UTF-8 text to LF before hashing and archiving;
- scans for private-key blocks, API-key patterns, database DSNs, and bearer tokens;
- creates a deterministic manifest with public file digests and byte counts;
- rejects candidate, Principal, Session, fact, Provider payload, approval-record, ticket, deployment-scope, DSN, and secret fields in the manifest;
- refuses to write the packet anywhere inside the repository;
- refuses to overwrite an existing packet;
- never contacts a database, Provider, OIDC system, deployment, or approval system;
- never changes application configuration.

The public documents describe field names such as `principal_id` as architecture contracts; the packet contains no real field values, identity records, external approval records, or deployment bindings.

## Build the packet

Choose a new path outside the repository:

```powershell
python -m scripts.long_term_memory_decision_packet `
  --output <external-output-directory>/long-term-memory-decision-PENDING.zip
```

A successful run prints:

```text
LONG_TERM_MEMORY_DECISION_PACKET=READY_FOR_EXTERNAL_REVIEW
APPROVAL_STATUS=PENDING
CONFIGURATION_CHANGED=false
HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED
PRODUCTION_DATA_USE_SPEC=NOT_APPROVED
REAL_CANDIDATE_PROCESSING=PROHIBITED
TASKS_3_TO_34=BLOCKED_PENDING_EXTERNAL_DECISIONS
```

A blocked run prints only stable gate codes and closed authorization states. It does not print private content or external locators.

## External review flow

1. Transfer the PENDING packet through the approved review channel.
2. Reviewers inspect the ADR, Data-use draft, schemas, and preflight contracts.
3. Productization reviewers create the authoritative `GO`, `NO_GO`, or `REVISE` record outside Git.
4. If Productization is `GO`, Data-use reviewers create a separate authoritative decision outside Git.
5. Keep records, digests, approver references, regions, Provider details, deployment scope, and tickets in the external systems.
6. Use the two read-only preflights to verify the records against an exact repository revision.

Never edit the packet manifest to say `APPROVED`. Approval authority comes only from the independently controlled external records.
