# A2A-V1 Code Freeze Closure

Status: `CODE_FROZEN`

E2E: `DEFERRED`

V2 Spec / Plan: `AUTHORIZED`

V2 Implementation: `AUTHORIZED`

Final Acceptance: `PASS_FREEZE`

## Summary

This document records the final V1 code-level closure after the last
hotfix/freeze-verification round. C09 cancellation and C10 idempotency are now
machine-verified across the required lifecycle matrix, and the official A2A
executor has late-result cancellation fencing.

## Fixed in Final Hotfix

- unsupported skill now stores the failed task under `task.task_id`, not an
  unbound `working`.
- official executor fences late provider artifacts after cancellation.
- official invocation context falls back to `context_id` for stable session
  identity, closing cross-session idempotency collision.
- generic retry exception writes the failure record back under the logical
  `working.task_id`.
- C09/C10 freeze matrix now covers working replay, retry lineage, terminal
  failure replay, canceled replay, new command keys, duplicate cancel, and
  terminal-state cancel no-ops.
- canonicalization test now exercises the generic `request` hash path.
- professional agent cards expose REST and JSON-RPC interfaces with protocol
  version and URL.

## Freeze Test Receipt

See [13-v1-freeze-test-receipt.md](./13-v1-freeze-test-receipt.md).

Local A2A verification: `42 passed`.
