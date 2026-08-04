# Local V1 long-term-memory hardening acceptance v0.4

## Outcome

Local V1 hardening is accepted for the repository's trusted-local,
single-user, default-off boundary. The implementation was frozen and validated
before this publication-only revision was created.

```text
LOCAL_V1_IMPLEMENTATION=FEATURE_COMPLETE
LOCAL_V1_HARDENING=COMPLETE
LOCAL_V1_FINAL_ACCEPTANCE=PASS
LOCAL_V1_DEFAULT=DISABLED
LOCAL_V1_REAL_CANDIDATE_USE=PROHIBITED
REAL_PROVIDER_EVALUATION=NOT_RUN
HOSTED_V2=NO_GO_FOR_NOW
NEXT_REQUIRED_TASK=NONE
OPTIONAL_FUTURE_TRACK=HOSTED_PRODUCTIZATION_REDECISION
```

The accepted implementation identity is:

```text
validated_implementation_revision=e6b8f29d25276f17c874d07cebc15565bad37492
validated_implementation_tree=354d3d0a1ad99bfef57fd51244d1f5358442c79f
```

This document is evidence publication, not an implementation revision. The
publication commit intentionally does not record its own commit identity.
External verification is anchored by the immutable ref
`refs/tags/local-v1-hardening-v0.4-accepted`.

## What was accepted

The accepted Local V1 boundary includes all of the following:

- Read Shadow is a strict read-only axis: it creates no proposal, calls no
  extractor, writes no outbox item, and does not alter Provider input.
- Disabled mode performs no Principal Memory identity resolution, selection,
  shadow observation, injection, digest work, durable effect, or metric work.
- Exclusive taxonomy facts have a database-enforced single-active invariant,
  including concurrent writes, migration repair, restart, and conflict scans.
- Local Consume readiness requires a protected, writable and recoverable
  operator tombstone ledger with a durable replay watermark.
- Deletion and backup-restore replay fail closed when the ledger is missing,
  behind, ahead, divergent, corrupt, unwritable, or lock-contended.
- Local Consume remains bounded to trusted-local follow-up generation. Its
  causal boundary is explicit: a changed follow-up can indirectly change later
  answers, so direct dependency isolation is not fairness or outcome parity.
- Windows 11 and Ubuntu 24.04 dependency locks, Python 3.11, Node 22,
  PostgreSQL 16, Playwright and Chromium were reproduced on the same frozen
  implementation tree.

## Platform and test evidence

| Platform | Validation family | Passed | Failed | Skipped | Required family executed |
|---|---|---:|---:|---:|---:|
| Ubuntu 24.04 x64 | Full Python and live PostgreSQL | 2,216 | 0 | 3 | yes |
| Ubuntu 24.04 x64 | Full browser | 86 | 0 | 38 | yes |
| Windows 11 x64 | Platform, lock, path and CLI contracts | 88 | 0 | 2 | yes |
| Windows 11 x64 | Full browser | 86 | 0 | 38 | yes |

The production frontend build passed on both platforms, with 4,591 modules
transformed in each run. Hash-required dependency installation, dependency
consistency checks, reproducibility preflight and repository diff checks also
passed on both mandatory platforms.

Tool versions used for acceptance:

| Tool | Ubuntu | Windows |
|---|---|---|
| Python | 3.11.15 | 3.11.3 |
| Node | 22.23.2 | 22.21.0 |
| Playwright | 1.61.1 | 1.61.1 |
| Chromium | 149.0.7827.55 | 149.0.7827.55 |
| PostgreSQL | 16.13 | 16.13 |
| Git | 2.43.0 | 2.53.0.windows.3 |

## Skip classification

All 81 reported skips were reviewed against the H7 policy:

| Classification | Count | Effect on acceptance |
|---|---:|---|
| `CONDITIONAL_NON_APPLICABLE` | 76 | Non-blocking; the owning platform, project or viewport executed |
| `OPTIONAL_NOT_AUTHORIZED` | 5 | Non-blocking; real Provider evaluation remains prohibited |
| `BLOCKER` | 0 | No blocker |
| Required test skipped | 0 | No blocker |

The optional skips do not become a quality claim. They preserve the explicit
state `REAL_PROVIDER_EVALUATION=NOT_RUN`.

## Mandatory family coverage

The full run executed the required families for configuration conflicts,
Read Shadow zero-write behavior, disabled zero activity, bounded Local Consume,
taxonomy and lifecycle contracts, current Consent and controls, safe reference
handling, exclusive-key live PostgreSQL concurrency, migrations and restart,
deletion fencing, ledger failure, restore replay, safe export, Memory Center
browser and accessibility behavior, Knowledge and decision firewalls,
cross-platform reproducibility, and the full repository regression.

These results establish repository behavior only. They do not authorize a
deployed observation window.

## Cleanup and immutability

Final cleanup and immutability checks produced:

```text
POSTGRES_TEST_RELATION_RESIDUE=0
WINDOWS_TARGET_PORT_RESIDUE=0
UBUNTU_TARGET_PORT_RESIDUE=0
WINDOWS_TARGET_PROCESS_RESIDUE=0
UBUNTU_TARGET_PROCESS_RESIDUE=0
CANDIDATE_WORKTREE_CLEAN=true
UBUNTU_CLEAN_CLONE_WORKTREE_CLEAN=true
PROTECTED_MAIN_USER_OWNED_ENTRIES_PRESERVED=14
```

The candidate revision and tree were re-read after all tests and remained
unchanged. The main worktree's pre-existing user-owned frontend changes were
not staged, restored, reformatted or committed.

## Evidence handling

Raw JUnit, browser, install, cleanup and skip-classification artifacts remain
outside Git under restricted local access. The tracked manifest records only
aggregate counts, tool versions and SHA-256 digests. A sanitizer dry-run found
no real credential, real database connection string, private locator,
candidate content, Prompt, Provider payload or machine path in the publication
projection.

Credential-shaped values present in raw JUnit node names were fixed negative
test fixtures for the repository's own secret-audit tests. They remain outside
Git and are not copied into this publication.

## Operational state and rollback

Local V1 remains disabled in committed configuration. Repository acceptance
does not automatically enable Write Shadow, Read Shadow or Local Consume. A
trusted local operator must use the existing runbook, pass preflight, and keep
the kill path available.

Safe rollback remains:

```text
mode=disabled
retain ledger/tombstones/migrations
```

Disabling the capability must not delete migrations, retained tombstones or
legitimate facts. User-requested deletion remains an explicit lifecycle action.

## Non-claims and authorization boundary

This acceptance does not prove or authorize:

- real-candidate processing;
- real Provider quality or retention behavior;
- production Write Shadow or Read Shadow;
- a production canary or general availability;
- hosted identity, recovery, Consent, tenancy or cross-user isolation;
- memory use in scoring, reports, recommendations, rankings or hiring;
- fairness, candidate safety, outcome parity or causal equivalence;
- automatic migration of local facts, Consent or tombstones to a hosted system.

Hosted V2 remains a retained but frozen roadmap. Restarting it requires a new
baseline, a newly approved or formally reopened Productization ADR, an approved
data-use specification, and new authorization.

## Publication verification

The machine-readable record is
`docs/local-v1-hardening-manifest.json`. Publication is complete only when the
remote branch contains this documentation-only revision, the annotated tag
`local-v1-hardening-v0.4-accepted` resolves exactly to it, and its history
contains the validated implementation revision above.
