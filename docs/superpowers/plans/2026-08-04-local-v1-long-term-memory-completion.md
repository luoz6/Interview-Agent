# Local V1 Single-user Long-term Memory Completion Plan

**Version:** 1.0-draft
**Date:** 2026-08-04
**Audience:** Interview Agent maintainers, QA, privacy reviewers, and local operators
**Document type:** Implementation how-to with status reference
**Target branch:** `codex/local-v1-long-term-memory`

## 1. Outcome

Complete a safe, useful, persistent long-term-memory capability for the existing
single-user Local V1 deployment without representing it as Hosted Multi-user V2.

The finished local capability must let the local user:

- explicitly enable or disable long-term memory;
- create only approved, structured facts;
- inspect, correct, reject, revoke, export, and delete those facts;
- run Write and Read Shadow without changing model input;
- separately enable bounded local consumption;
- ignore memory immediately for a session or globally;
- prove that scoring, reports, knowledge retrieval, and public APIs do not treat
  personal facts as assessment evidence;
- delete all local memory and replay a deletion tombstone after a backup restore.

This plan is complete only when every task and every automatic review passes.

## 2. Current status and Hosted disposition

The Hosted Multi-user V2 production route is `NO_GO_FOR_NOW`. GitHub Issue #1 is
closed as `not_planned`; this is a scope disposition, not an external approval
record. The production roadmap Tasks 3-34 remain unexecuted and unauthorized.

Repository implementation already provides:

- canonical JSON fact taxonomy;
- Principal Memory fact and Consent contracts;
- in-memory and PostgreSQL stores;
- proposal, lifecycle, retrieval, deletion, and Shadow services;
- trusted-local management APIs hidden by default;
- zero-injection Read Shadow evidence and tests;
- separation from scoring, report, and public Knowledge paths.

The current runtime still constructs `NullPrincipalIdentityResolver` and
`NullPrincipalMemoryExtractor`. The trusted-local API can be enabled in tests,
but a normal Local V1 process cannot obtain a stable Principal. Consumption is
intentionally rejected. There is no Memory Center, direct user-declared write,
safe export, temporary disable, session ignore, or restore tombstone replay.

## 3. Scope

### 3.1 Included

- one local installation;
- one explicit local Principal;
- deployment scope exactly `single-tenant-local` by default;
- in-memory stores for tests and ephemeral preview;
- PostgreSQL stores for durable local use;
- controlled taxonomy facts only;
- local management API and Memory Center UI;
- Write Shadow, Read Shadow, and separately gated Local Consume;
- local deletion, export, and restore replay;
- content-free aggregate observability;
- automatic review after every task.

### 3.2 Explicitly excluded

- Hosted Multi-user V2;
- tenant administration or cross-tenant access;
- OIDC, email login, account recovery, or organization accounts;
- identity inferred from name, email, phone, IP, device, browser, resume,
  answer text, embeddings, or model output;
- storing free-text candidate answers as Principal Memory;
- public Knowledge ingestion or vector embedding of Principal Memory;
- using Principal Memory as scoring, hiring, evidence, or report input;
- automatic confirmation or activation of model proposals;
- production Write Shadow, Read Shadow, or canary claims;
- real candidate processing authorization;
- background cloud synchronization;
- silently upgrading `read_shadow` to consumption;
- using a GitHub comment as a production approval record.

## 4. Fixed implementation decisions

### Decision 1: local identity is explicit and stable

The resolver is enabled only by `MEMORY_LOCAL_PRINCIPAL_ENABLED=true`. It binds
the configured deployment to a fixed local Principal ID. The default ID is a
non-personal identifier and contains no email, name, device, or resume data.
When disabled, the runtime continues to use `NullPrincipalIdentityResolver`.

### Decision 2: every feature is default-off

The default remains:

```text
MEMORY_LONG_TERM_MODE=disabled
MEMORY_LOCAL_PRINCIPAL_ENABLED=false
MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED=false
MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED=false
MEMORY_LONG_TERM_READ_SHADOW_ENABLED=false
MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED=false
```

No compatibility alias may turn these gates on.

### Decision 3: Local Consume is a distinct mode

`MEMORY_LONG_TERM_MODE=local_consume` is the only consumption mode. The old
value `consume` remains rejected so a production-looking configuration cannot
be silently reinterpreted as local use.

Local Consume requires all of the following:

- local Principal enabled with `trusted_local` assurance;
- deployment scope is explicitly local;
- trusted-local management API enabled;
- Write and Read Shadow gates enabled;
- a dedicated local-consumption gate enabled;
- current `local_consume` Consent;
- current global and session controls allow memory;
- durable PostgreSQL runtime outside tests;
- absolute fact and token caps.

### Decision 4: user declaration is the only direct activation path

A user-created fact is canonicalized, validated against the versioned taxonomy,
created as a proposal, and activated in one service operation. Model proposals
remain `proposed` until the user explicitly confirms them.

### Decision 5: correction supersedes by taxonomy key

Correcting an exclusive fact, such as `interview_language`, activates the new
value and supersedes the prior active value atomically. Non-exclusive skills
and goals may coexist. Concurrent corrections must result in at most one active
fact for each exclusive key.

### Decision 6: temporary disable does not delete

Global disable and per-session ignore take effect on the next context assembly,
within 60 seconds, and retain facts. Revoke ends Consent. Delete removes facts,
Consent, effects, controls, exports, caches, and bindings, then writes an
operator-held tombstone suitable for restore replay.

### Decision 7: export is safe and short-lived

Export contains the safe fact payload, Consent state, control state, schema
versions, and timestamps. It excludes internal fact IDs, Principal IDs, Session
IDs, source locators, source digests, Prompts, answers, resumes, reports, DSNs,
and secrets. A generated export expires after 24 hours.

### Decision 8: Read Shadow remains zero-injection

Read Shadow computes selection and aggregate metrics but must preserve the
exact Provider Context digest. It must not create proposals or require a model
extractor.

### Decision 9: consumption is isolated to follow-up assistance

Local Consume may add one bounded, clearly labelled system context block to the
follow-up-generation path. It must never flow to evaluators, scoring, evidence,
reports, PDFs, public Knowledge, preparation, or review agents.

### Decision 10: accessibility facts are not inferred

Accessibility preferences may be stored only through direct user declaration.
They are never inferred from timing, language, device behavior, or answers and
never affect assessment scores.

### Decision 11: no raw personal content in telemetry

Metrics contain only mode, outcome, reason code, bounded counts, latency, and
token estimates. Fact values, IDs, source digests, Prompts, answers, and user
identifiers are forbidden.

### Decision 12: automatic review is a task gate

Every Task below ends with an Auto-review. A failing review blocks the next
Task. Reviews include focused tests, contract/source scans, `git diff --check`,
and an exact-path dirty audit. Commits are task-scoped and self-contained.

## 5. Architecture

```text
LocalPrincipalIdentityResolver
  -> LocalPrincipalMemoryControlStore
  -> PrincipalMemoryConsentService
  -> PrincipalMemoryLifecycleService
  -> PrincipalMemoryFactStore (memory or PostgreSQL)
  -> PrincipalMemoryRetriever
       -> Read Shadow (digest equality, zero injection)
       -> Local Consumer (bounded follow-up-only block)
  -> Memory Center API/UI
       -> declare/correct/reject/revoke
       -> global disable/session ignore
       -> export/delete
  -> deletion tombstone ledger -> restore replay
```

Operation-time order is fixed:

```text
resolve explicit local Principal
  -> read current global/session control
  -> read current purpose-specific Consent
  -> read current active facts
  -> reject deleted/stale/conflicting facts
  -> apply absolute caps
  -> shadow OR build one local-assistance block
  -> recheck control and Consent before Provider call
```

## 6. Public local API contract

All routes remain hidden with `404` unless the local Principal and trusted-local
API gates are enabled.

```text
GET    /api/runtime/principal-memory/status
PUT    /api/runtime/principal-memory/consent
POST   /api/runtime/principal-memory/disable
POST   /api/runtime/principal-memory/enable
GET    /api/runtime/principal-memory/facts
POST   /api/runtime/principal-memory/facts
PUT    /api/runtime/principal-memory/facts/{safe_ref}
POST   /api/runtime/principal-memory/facts/{safe_ref}/reject
POST   /api/runtime/principal-memory/facts/{safe_ref}/revoke
POST   /api/runtime/principal-memory/sessions/{session_id}/ignore
DELETE /api/runtime/principal-memory/sessions/{session_id}/ignore
POST   /api/runtime/principal-memory/export
DELETE /api/runtime/principal-memory
```

The UI/API uses an opaque, short-lived `safe_ref` rather than returning the
durable fact ID. A stale `safe_ref` or version returns `409`.

## 7. Automatic review contract

Every task runs, at minimum:

```text
focused pytest suite
python compile check for changed Python modules
plan/API/privacy contract scans
git diff --check
exact changed-path inventory
forbidden-content scan
```

Additional reviews are required when applicable:

- PostgreSQL migration and transaction tests;
- concurrency and race tests;
- browser tests for Memory Center;
- Provider Context digest comparison;
- scoring/report/Knowledge source isolation scan;
- deletion restore replay and failure injection;
- export content scan;
- full repository regression.

Review output must never print raw facts, identifiers, source data, or secrets.

## 8. Task dependency graph

```text
Task 0 -> Task 1 -> Task 2 -> Task 3
Task 3 -> Task 4 -> Task 5
Task 3 -> Task 6
Task 4 + Task 5 + Task 6 -> Task 7
Task 7 -> Task 8 -> Task 9
Task 4 + Task 6 -> Task 10
Task 8 + Task 9 + Task 10 -> Task 11
Task 11 -> Task 12 -> Task 13 -> Task 14
```

## 9. Implementation tasks

### Task 0: freeze Local V1 baseline

Record exact revision, branch, remote distance, test baseline, user-owned dirty
paths, and Hosted `NO_GO_FOR_NOW` disposition. Work only in the isolated
`codex/local-v1-long-term-memory` worktree.

**Auto-review:** verify isolated worktree is clean, main dirty paths are
unchanged, Issue #1 is closed `not_planned`, and no external approval artifact
was committed.

**Exit gate:** `LOCAL_MEMORY_BASELINE=FROZEN`.

### Task 1: add plan and contract tests

Commit this document and tests that pin Local-only scope, default-off behavior,
fixed decisions, contiguous Tasks 0-14, Auto-review sections, rollback, and DoD.

**Auto-review:** plan contract tests, Markdown source scan, `git diff --check`.

**Exit gate:** `LOCAL_MEMORY_PLAN_CONTRACT=PASS`.

### Task 2: implement explicit Local Principal and configuration

Add Local Principal settings, `local_consume` mode, local-only validation, and
runtime resolver construction. Preserve Null identity by default and reject
`consume`.

**Auto-review:** unit/config/runtime tests; environment conflict tests; identity
inference source scan; default-off readiness snapshot.

**Exit gate:** `LOCAL_PRINCIPAL_BOUNDARY=PASS`.

### Task 3: implement current control and purpose-specific Consent

Add `local_consume` purpose, global enabled/disabled control, per-session ignore,
operation-time checks, and durable memory/PostgreSQL stores. Revoke and disable
must be distinct.

**Auto-review:** grant/revoke/disable/enable/session-ignore tests; restart
persistence; races between disable/delete and selection; privacy scan.

**Exit gate:** `LOCAL_MEMORY_CONTROL=PASS`.

### Task 4: implement direct declaration and atomic correction

Allow canonical user-declared facts, safe references, optimistic concurrency,
and atomic supersede by exclusive taxonomy key. Keep model proposals manual.

**Auto-review:** taxonomy negatives, canonicalization, stale versions, duplicate
requests, concurrent corrections, PostgreSQL transaction rollback, no free text.

**Exit gate:** `LOCAL_FACT_LIFECYCLE=PASS`.

### Task 5: complete proposal and lifecycle management

Support list, confirm, reject, revoke, expire, supersede, and safe display for
both user-declared facts and future model proposals. Align retention defaults to
7 days for proposals and 180 days for active local facts.

**Auto-review:** complete transition matrix; source-deletion behavior; retention
clock boundaries; safe-payload locator scan.

**Exit gate:** `LOCAL_FACT_MANAGEMENT=PASS`.

### Task 6: complete Read Shadow for local facts

Allow manually declared facts to participate in bounded Read Shadow without
enabling Write Shadow or proposal extraction. Preserve Provider Context digest.

**Auto-review:** 300-case synthetic matrix, conflict/freshness/taxonomy tests,
zero Prompt mutation, zero proposal creation, aggregate-only metrics.

**Exit gate:** `LOCAL_READ_SHADOW=PASS`.

### Task 7: implement safe export and full deletion

Create short-lived safe export, expand purge coverage, add a durable operator
tombstone, and implement idempotent restore replay.

**Auto-review:** export forbidden-field scan; delete failure injection at every
stage; retry/idempotency; backup restore replay; zero residue.

**Exit gate:** `LOCAL_MEMORY_RIGHTS=PASS`.

### Task 8: implement Memory Center API

Expose status, Consent, enable/disable, facts, correction, rejection, revocation,
session ignore, export, and delete using safe references and stable errors.

**Auto-review:** route-hidden defaults; authorization; schema tests; CSRF/local
origin policy; stale refs; no locator leakage; OpenAPI contract.

**Exit gate:** `LOCAL_MEMORY_CENTER_API=PASS`.

### Task 9: implement Memory Center UI

Add a local-only Memory Center page with clear status, consent checkboxes,
structured fact forms, edit/revoke/delete controls, export, immediate disable,
session ignore explanation, error states, and reduced-motion support.

**Auto-review:** browser functional tests, keyboard navigation, accessible names,
focus order, reduced motion, destructive confirmation, API-disabled behavior.

**Exit gate:** `LOCAL_MEMORY_CENTER_UI=PASS`.

### Task 10: implement bounded Local Consume

Build one canonical assistance block from selected facts and inject it only into
follow-up generation. Recheck current control and Consent immediately before use.

**Auto-review:** prompt golden tests; hard token/fact caps; disable/revoke/delete
races; session ignore; scoring/report/Knowledge isolation; no fallback to consume.

**Exit gate:** `LOCAL_MEMORY_CONSUMPTION=PASS`.

### Task 11: integrate durable PostgreSQL runtime

Add forward-only migrations for controls, safe-ref state if durable, export jobs,
and tombstones. Validate existing fact/Consent schemas and transaction semantics.

**Auto-review:** live PostgreSQL migrations on empty/current/dirty schemas;
upgrade and rollback drill; concurrent operations; restart persistence.

**Exit gate:** `LOCAL_MEMORY_POSTGRES=PASS`.

### Task 12: add observability and operational tooling

Publish content-free metrics, readiness, local operator status, cleanup, expiry,
export expiry, deletion replay, and a fail-closed preflight for local consume.

**Auto-review:** metric allowlist/denylist; disabled-mode zero activity; failure
codes; readiness truth table; secret and identifier scan.

**Exit gate:** `LOCAL_MEMORY_OPERATIONS=PASS`.

### Task 13: execute full acceptance matrix

Run unit, integration, PostgreSQL, browser, concurrency, failure injection,
privacy, Knowledge firewall, prompt isolation, long-context, and full regression
tests on the exact revision.

**Auto-review:** independent requirement-to-evidence table; no unverified item may
be marked complete; verify main user-owned files remain untouched.

**Exit gate:** `LOCAL_MEMORY_ACCEPTANCE=PASS`.

### Task 14: publish RC and handoff

Create task-scoped commits, a clean RC manifest, sanitized evidence, runbook,
configuration example, rollback procedure, and final status. Push the isolated
branch and create a reviewable PR when authentication permits.

**Auto-review:** clean exact revision reproduction; full regression; manifest
hash verification; public artifact privacy scan; remote branch verification.

**Exit gate:** `LOCAL_V1_LONG_TERM_MEMORY=COMPLETE`.

## 10. Rollback

| Failure | Immediate action | Data handling | Resume condition |
|---|---|---|---|
| identity unavailable | disable all local modes | retain facts, no reads/writes | resolver tests pass |
| unexpected deployment scope | startup fail-closed | no operation | correct explicit scope |
| Consent unavailable | stop operation | retain facts | current Consent restored |
| global/session disable race | suppress memory block | retain facts | race test passes |
| duplicate active exclusive fact | disable consume | retain and repair transactionally | invariant restored |
| Prompt cap exceeded | deterministic no-memory fallback | retain facts | cap logic fixed |
| scoring/report/Knowledge access | disable consume and investigate | quarantine release | isolation audit passes |
| export leakage | disable export | delete exports | privacy review passes |
| deletion partial failure | keep tombstone pending and retry | no new memory | replay reaches zero residue |
| PostgreSQL migration failure | stop startup before mutation | preserve existing tables | migration drill passes |
| UI/API mismatch | hide Memory Center | backend gates remain | contract/browser tests pass |
| any unknown failure | set mode disabled | preserve facts unless deleting | focused and regression tests pass |

Rollback never deletes migration history or weakens validation. Disabling
consumption must not implicitly delete facts; deletion remains an explicit user
right.

## 11. Definition of Done

All of the following must be proven on one exact revision:

1. Hosted Issue #1 is closed `NO_GO_FOR_NOW` and no production authorization is claimed.
2. Local identity is explicit, stable, local-only, and default-off.
3. Null identity remains the default.
4. `consume` is still rejected.
5. `local_consume` requires every dedicated gate.
6. Consent is purpose-specific and checked at operation time.
7. Temporary disable and delete are distinct.
8. Session ignore takes effect on the next context assembly.
9. Only canonical taxonomy facts can be stored.
10. User declarations may activate; model proposals may not auto-activate.
11. Exclusive corrections are atomic.
12. Safe APIs reveal no internal locators.
13. Read Shadow proves Provider Context equality.
14. Local Consume is bounded and follow-up-only.
15. Scoring, evidence, reports, PDFs, review, preparation, and Knowledge remain isolated.
16. Export contains no forbidden fields and expires.
17. Delete reaches zero residue or returns an explicit retryable failure.
18. Tombstone replay prevents backup resurrection.
19. PostgreSQL upgrade and restart persistence pass.
20. Memory Center keyboard and reduced-motion tests pass.
21. Disabled mode performs zero memory work.
22. Telemetry is aggregate and content-free.
23. Every Task Auto-review passed.
24. Full repository regression passed.
25. Main-worktree user changes are untouched.
26. RC branch is clean, pushed, and reviewable.

## 12. Final status contract

Before completion:

```text
HOSTED_V2=NO_GO_FOR_NOW
LOCAL_V1_LONG_TERM_MEMORY=IN_PROGRESS
LOCAL_MEMORY_CONSUMPTION=DISABLED
REAL_CANDIDATE_PRODUCTION_PROCESSING=PROHIBITED
```

After every DoD item is proven:

```text
HOSTED_V2=NO_GO_FOR_NOW
LOCAL_V1_LONG_TERM_MEMORY=COMPLETE
LOCAL_MEMORY_DEFAULT=DISABLED
LOCAL_MEMORY_WRITE=USER_CONTROLLED
LOCAL_MEMORY_READ_SHADOW=AVAILABLE
LOCAL_MEMORY_CONSUMPTION=AVAILABLE_BUT_DEFAULT_OFF
SCORING_AND_REPORT_USE=PROHIBITED
REAL_CANDIDATE_PRODUCTION_PROCESSING=PROHIBITED
```

The identifiers and obligations in this plan are implementation-local. This
document does not invent or amend normative `MEM-*` requirement IDs.
