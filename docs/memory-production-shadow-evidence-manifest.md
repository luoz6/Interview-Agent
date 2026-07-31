# Production Shadow evidence manifest reference

The production Shadow evidence manifest is a tamper-evident inventory of the
repository materials submitted for external review. It binds an explicit
allowlist of machine evidence and review references to file SHA-256 values,
byte sizes, JSON schema versions, and a source Git revision.

It is not a digital signature. It does not prove human approval, deployment
identity, legal authority, authorship, or that a production change occurred.
Approval remains `PENDING`; the production change preflight remains `BLOCKED`.

## Purpose

The manifest lets an external approver or operator detect whether an evidence
file was replaced, edited, removed, added under a misleading name, or moved
through an unsafe path after the handoff bundle was assembled.

The manifest contains no evidence file content. It stores only:

- a repository-relative allowlisted path;
- a category (`machine_evidence` or `review_reference`);
- raw-file SHA-256;
- raw byte size;
- JSON `schema_version`, when present;
- source Git revision;
- an overall canonical bundle SHA-256;
- the unchanged Pending/BLOCKED/NOT_RUN boundary.

## Allowlist boundary

Only paths hard-coded in
`scripts/memory_production_shadow_evidence_manifest.py` may appear. Every path
must:

- be POSIX-style and relative;
- begin with `docs/`;
- contain no empty, `.`, `..`, or backslash segment;
- resolve inside the repository root;
- exist as a regular file;
- have the expected category.

Verification requires the manifest file set to equal the allowlist exactly.
Missing, duplicate, unknown, absolute, or traversing paths are blocked.

The external approval record is excluded from the manifest. So is the
repository Pending example. Approver, change-ticket, record, and deployment
bindings remain in the trusted external change-management system and must not
be copied into the repository bundle.

## Included machine evidence

The allowlist covers the machine records for:

- base operational validation;
- Budget, Principal Write, proposal quality, Principal Read, and lifecycle
  observations;
- old-backup restore/tombstone replay;
- aggregate status and hard-stop state;
- security/privacy/fairness/Knowledge Firewall review;
- final regression and Operational Shadow acceptance;
- production approval packet readiness;
- current production change preflight BLOCKED state.

## Included review references

The allowlist also covers the operator/reviewer documents for:

- restore replay;
- aggregate observability;
- threat model and security review;
- Operational Shadow acceptance;
- production Budget Shadow approval and rollback;
- external approval-record contract and change preflight;
- Principal Memory consumption draft and risk review.

## Hash contracts

Each file SHA-256 is calculated over its raw checked-out bytes. Byte size is the
exact raw length. JSON schema is read from the top-level `schema_version` when
present.

The bundle SHA-256 is calculated over compact, sorted-key canonical JSON that
contains:

- manifest schema;
- source revision;
- approval/production boundary fields;
- file count;
- the ordered file-entry list.

The bundle hash does not include itself.

## Security and privacy boundary

The manifest and verifier reject private or operationally sensitive keys,
including subject/object locators, source data, Prompt/answer/resume/report,
approval-record bindings, deployment digests, DSNs, database fingerprints,
table prefixes, and Provider payload.

The manifest proves only that the allowlisted repository files match the
recorded bytes. Reviewers must still evaluate their meaning and independently
verify the external approval record.

## Stable state

Every manifest created before external approval must retain:

```text
APPROVAL_STATUS=PENDING
CHANGE_PREFLIGHT=BLOCKED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```
