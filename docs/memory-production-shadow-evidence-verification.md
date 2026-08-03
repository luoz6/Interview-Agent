# Verify the production Shadow evidence handoff

This how-to is for the evidence maintainer and external approver. It builds or
verifies the repository handoff manifest without changing configuration or
production state. Verification is independent of LF versus CRLF checkout
conventions.

## Build from the source revision

Use a clean detached checkout of the intended handoff revision:

```powershell
& 'F:\python3.11\python.exe' `
  -m scripts.memory_production_shadow_evidence_manifest `
  --build
```

Expected output:

```text
MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=BUILT
FILES=<allowlisted count>
APPROVAL_STATUS=PENDING
CHANGE_PREFLIGHT=BLOCKED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

Commit only the generated manifest. Do not add an external approval record,
approver binding, deployment binding, or production secret to the bundle.

## Verify the handoff

From the repository revision containing the manifest:

```powershell
& 'F:\python3.11\python.exe' `
  -m scripts.memory_production_shadow_evidence_manifest `
  --verify docs/memory-production-shadow-evidence-manifest.json
```

Expected output:

```text
MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=VERIFIED
FILES=<verified count>
APPROVAL_STATUS=PENDING
CHANGE_PREFLIGHT=BLOCKED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

Verification checks:

1. manifest schema and Pending/BLOCKED/NOT_RUN state;
2. source revision is an ancestor of the current checkout;
3. manifest paths exactly equal the code allowlist;
4. every path is safe and resolves under `docs/`;
5. every allowlisted artifact is valid UTF-8 text;
6. category, UTF-8/LF canonical SHA-256, canonical byte size, and JSON schema
   match;
7. canonical bundle SHA-256 matches;
8. the manifest contains no private or connection data.

The recorded `content_normalization` must be `utf8-lf-v1`. CRLF and LF
checkouts of the same Git content therefore verify identically. A non-line-
ending edit, invalid UTF-8, unexpected file, or stale bundle hash remains a
blocking failure.

## Handle a failure

Any mismatch produces:

```text
MEMORY_PRODUCTION_SHADOW_EVIDENCE_MANIFEST=BLOCKED
GATE=<stable code>
APPROVAL_STATUS=PENDING
CHANGE_PREFLIGHT=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```

Do not edit the manifest to make a mismatch disappear. Determine whether the
source file changed legitimately, the checkout is wrong, the bundle is
incomplete, or tampering occurred. For a legitimate change:

1. repeat the relevant validation and security gates;
2. create a new clean source revision;
3. rebuild the manifest from that revision;
4. submit the new bundle for review;
5. invalidate the old approval request if its evidence changed.

For an unexplained mismatch, stop the approval process and notify the change
owner and security owner. Do not execute the production change.

## Interpretation

`VERIFIED` means the allowlisted repository evidence matches the manifest. It
does not change `APPROVAL_STATUS=PENDING`, does not satisfy the external
approval-record contract, and does not authorize production Budget Shadow.
