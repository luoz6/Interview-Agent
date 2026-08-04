# Local V1 long-term memory RC handoff

## Review outcome

The Local V1 single-user long-term-memory implementation has passed the
post-review hardening gate. All nine review findings were fixed, and the
automatic review also found and fixed a tombstone-replay service-construction
defect before publication. The capability remains disabled in committed
configuration. Hosted V2 and real-candidate production processing remain
prohibited.

The machine-readable scope and hashes are in
`docs/local-v1-long-term-memory-rc-manifest.json`. Task 13 evidence and all 26
Definition-of-Done states are in
`docs/local-v1-long-term-memory-acceptance.md` and its JSON companion.

## What reviewers should verify

1. Confirm the manifest subject and all critical file SHA-256 values.
2. Review the explicit Local Principal resolver and default Null resolver.
3. Review canonical fact validation and atomic exclusive correction.
4. Review operation-time Consent, global disable, and session-ignore checks.
5. Review safe refs, safe export, full deletion and protected-ledger replay.
6. Review the one bounded follow-up-only assistance block and both pre-provider
   authorization reads.
7. Confirm prep, scoring, review, reports, PDFs and Knowledge have no Principal
   Memory consumer.
8. Review aggregate-only metrics, fail-closed preflight and expiry cleanup.
9. Exercise the Memory Center on desktop and mobile with reduced motion.
10. Confirm all environment defaults remain disabled.
11. Confirm trusted-local routes validate the actual loopback peer and ignore
    forwarded-address spoofing.
12. Confirm deletion fencing, multi-cycle tombstones, external ledger capture,
    complete exports and session-control purge remain intact.

## Reproduce the final checks

Use an isolated checkout of the pushed branch. Provide only a local test
PostgreSQL DSN; never use a production database or real candidate data.

```powershell
python -m pytest -q
npm run build:frontend
npm run test:browser
python -m scripts.local_principal_memory preflight
```

The first command must execute PostgreSQL-marked tests when a reachable local
test DSN is configured. The preflight is expected to exit non-zero under
committed defaults and include `LOCAL_CONSUME_MODE_DISABLED`; that is the safe
default, not a release failure.

## Private runtime enablement

Repository acceptance does not turn Local Consume on. A trusted local operator
must use the private configuration documented in
`docs/local-principal-memory-operations.md`, run the fail-closed preflight, and
retain the ability to disable memory immediately. Never copy private DSNs,
tombstone ledgers, identifiers, facts, prompts, answers, résumés or exports into
Git, CI artifacts, tickets or review comments.

## Rollback

Set long-term mode, Write Shadow, Read Shadow, Local Consume, trusted-local API,
Local Principal and trusted-local metrics back to disabled, then restart the
local application. Do not delete migrations, tombstones or retained facts as a
rollback shortcut. User-requested deletion remains a separate explicit right.

## Non-claims

- This RC is not Hosted Multi-user V2.
- It provides no OIDC, organization tenant or account-recovery boundary.
- It does not authorize production Shadows or a provider canary.
- It does not authorize memory use for scoring, review, reports or hiring.
- It does not authorize real-candidate production processing.

## Publication sequence

1. Commit this manifest and handoff with their verification test.
2. Run the full Python/PostgreSQL, frontend build and browser matrix on that
   exact clean commit.
3. Confirm zero listeners, zero isolated test relations, zero secret findings
   and no change to the main user worktree.
4. Push `codex/local-v1-long-term-memory` and verify the remote hash.
5. Publish the final promotion record without changing implementation code.
6. Re-run exact-revision acceptance, push, and verify the final remote hash.

The hardened implementation sequence through step 4 completed at
`3d4dccbb38afcf9792f368b0a2ff4a3146f0d1be`. The commit containing this updated
evidence record must complete steps 5 and 6 without any implementation change.
