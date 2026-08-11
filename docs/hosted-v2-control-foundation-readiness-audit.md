# Hosted V2 Control Foundation readiness audit

**Audit scope:** Tasks 4–10 of the v0.2 revised long-term-memory roadmap
**Audit type:** read-only repository evidence and implementation readiness
**Repository revision reviewed:** `1c630e1346d5fe82ac1ce43a127704a37daebb9f`
**Implementation state:** `IMPLEMENTATION=NOT_AUTHORIZED`
**Entry gates:** Productization `GO` and Production Data-use `APPROVED`

## Outcome

```text
CONTROL_FOUNDATION_READINESS_AUDIT=COMPLETE
HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED
PRODUCTION_DATA_USE_SPEC=NOT_APPROVED
AUTHENTICATION_RUNTIME=NOT_IMPLEMENTED
STABLE_PRINCIPAL_MAPPING=NOT_IMPLEMENTED
PRINCIPAL_ASYNC_BINDING=NOT_IMPLEMENTED
CONSENT_LEDGER_V2=NOT_IMPLEMENTED
AUTHENTICATED_MEMORY_CENTER=NOT_IMPLEMENTED
CONTROL_FOUNDATION_IMPLEMENTATION=NOT_AUTHORIZED
LOCAL_V1=UNCHANGED
```

This audit does not execute Tasks 4–10 and does not change runtime code, schema, configuration, or production state. It identifies the exact reusable foundations, blocking gaps, file ownership, migration order, and acceptance evidence required after both external decision gates pass.

## Executive assessment

The repository already contains a safe local Principal Memory foundation:

- frozen `PrincipalIdentity` values with deployment and Principal scope;
- canonical JSON fact taxonomy and model-proposed-only state;
- in-memory and PostgreSQL fact and Consent stores;
- proposal outbox events with retry and source-version checks;
- confirm, reject, revoke, list, and coarse delete lifecycle operations;
- Read Shadow selection with Provider-context digest restoration;
- trusted-local routes hidden behind a default-off gate;
- unit, isolation, lifecycle, PostgreSQL, privacy, and zero-injection tests.

These components are not a Hosted authentication or account-control system. The current runtime has no OIDC boundary, no stable subject-alias mapping, no request-scoped Principal, no immutable Session owner, no purpose-specific append-only Consent ledger, no authenticated self-service API, and no Candidate Memory Center.

The existing code must be evolved behind additive schemas and fail-closed feature gates. It must not be relabeled as production-ready and must not be made reachable by simply enabling the trusted-local API.

## Evidence inventory

### Reusable foundations

| Foundation | Current evidence | Safe reuse boundary |
|---|---|---|
| Frozen identity value | `app/services/principal_identity.py:9-15` defines frozen deployment/Principal/assurance/timestamp fields | Reuse the value object after authenticated mapping; do not reuse constructor-supplied identity as an HTTP authenticator |
| Identity protocol | `app/ports/principal_identity.py:7-8` exposes `resolve()` | Replace with a request-context contract; do not store the current candidate in a process-global resolver |
| Canonical taxonomy | `app/services/principal_memory_contracts.py:14-71` rejects unknown/free-text values and uses canonical JSON | Narrow the production taxonomy to the approved Data-use version before Write |
| Proposed-only model output | `app/services/principal_memory_tasks.py:82-99` creates `authority=model_proposed`, `status=proposed`, `user_confirmed=false` | Retain this invariant after production extractor wiring |
| Source and operation rechecks | `app/services/principal_memory_tasks.py:29-65` rechecks identity, Consent, Session state, deletion, and source version | Rebind through immutable Session owner rather than re-resolving a global identity |
| PostgreSQL fact isolation | `app/services/postgres_principal_memory.py:224-249` keys facts by deployment/Principal/fact and checks active facts are user-confirmed | Add exclusive-key constraints, owner bindings, and migration-owned v2 schema |
| Read zero-mutation guard | `app/services/principal_memory_shadow.py:26-69` digests and restores Provider context | Retain as defense-in-depth; add question/API/evaluator/score/report equality tests |
| Default-off trusted-local API | `app/api/routes.py:87-94` returns not-found unless the local gate and explicit identity exist | Keep for tests only; never promote this helper into Hosted authentication |
| Runtime migration registry | `app/services/postgres_runtime_migrations.py:62-307` provides checksum, advisory-lock, transaction, and idempotency ownership | Add new immutable migrations; never rewrite `principal_memory_v1` history |
| Local regression baseline | Task 0 and the current full suite establish Local V1 compatibility | Re-run after every Task 4–10 commit and on live PostgreSQL/browser environments |

### Confirmed blockers

| Blocker | Current evidence | Required resolution |
|---|---|---|
| No OIDC runtime | No auth/OIDC/JWKS module or route exists; `README.md:3-5` still defines local single-user/no-login scope | Task 4 must implement the complete authorization-code boundary, application Session, CSRF, logout, re-auth, revocation, and threat tests |
| Null runtime identity | `app/services/runtime.py:105-111` globally constructs `NullPrincipalIdentityResolver` | Hosted request handling must resolve an authenticated Principal per request; production memory modes must reject Null/test/trusted-local identity |
| No subject alias mapping | `ExplicitPrincipalIdentityResolver` accepts a caller-provided Principal ID; no alias/key-version store exists | Task 5 must create stable random internal IDs and versioned deployment/issuer/subject aliases |
| No immutable Session owner | Session serialization and PostgreSQL Session schema contain no owner deployment/Principal fields | Task 6 must add paired immutable owner fields and make Hosted Sessions require them |
| Async identity drift | Proposal events carry Principal locators, but `PrincipalMemoryProposalProcessor` re-resolves the current runtime identity | Workers must load the Session owner binding, compare the event binding, and re-read current Consent/control/deletion state |
| Aggregated Consent v1 | `PrincipalMemoryConsent` stores one `allowed_purposes` list and one revoke timestamp | Task 7 must use append-only per-purpose, per-policy, per-version decisions |
| Missing `assist_c1a` | `PrincipalMemoryPurpose` and API request types contain only proposal, storage, and Read purposes | Add the fourth purpose only in Consent v2 after Data-use approval; keep assist execution disabled until Task 24/25 |
| In-place Consent overwrite | In-memory and PostgreSQL `grant()` replace the current aggregate record; PostgreSQL key is only deployment/Principal | Introduce a new v2 ledger; do not translate v1 grants into v2 authorization |
| Trusted-local API only | `app/api/routes.py:303-397` exposes local Consent/fact/delete routes without Hosted auth, CSRF, re-auth, or rate limiting | Task 8 must create a separate authenticated Principal-scoped API surface |
| No Memory Center | `frontend/src/App.jsx` has no account or memory route and no Memory Center page exists | Add a candidate-visible accessible route only after authenticated API contracts pass |
| Incomplete rights | Current routes provide list/confirm/reject/revoke/delete, but no correct, export, ignore-for-session, or disable-now | Task 8/9 must implement every right with idempotency and coarse non-enumerating responses |
| Confirm is not atomic | `PrincipalMemoryLifecycleService.confirm()` supersedes a predecessor, then activates the proposal in two store calls | Task 9 must provide one PostgreSQL transaction and one in-memory critical section for both transitions |
| No exclusive active constraint | Current fact table has no partial unique constraint by exclusive taxonomy key | Add an explicit fact key and database-enforced single-active invariant |
| Delete is incomplete | `PrincipalMemoryDeletionService` purges only facts and Consent; it does not own proposal effects, outbox work, aliases, controls, caches, exports, or tombstones | Build a durable deletion job with tombstone replay and residue evidence |
| Retention mismatch | Current defaults are 30-day proposals and 365-day active facts; the draft Data-use maxima are 7 and 180 days | Approval must bind the final schedule, then config/migrations/jobs must enforce it |
| Write/Read coupling | `build_proposal_event_if_eligible()` allows both `write_shadow` and `read_shadow`; config requires both Write and Read gates for Read | Task 18 owns the later Read single-axis correction; Control Foundation must not weaken the current disabled default |
| Null extractor | `app/services/runtime.py:156-175` wires `NullPrincipalMemoryExtractor` | Task 11, after Budget and Control PASS, owns the approved production Provider adapter |

## Task-by-task readiness

### Task 4: complete OIDC Authentication Runtime

**Current readiness:** `NOT_IMPLEMENTED`.

No production authentication boundary exists. The implementation must be a new subsystem rather than additional behavior in `_require_trusted_local_principal_memory()`.

Required components after authorization:

1. OIDC configuration with exact issuer/audience allowlists and discovery/JWKS cache policy;
2. authorization request service with state, nonce, PKCE, redirect binding, and short TTL;
3. callback validation for code exchange, issuer, audience, signature, expiry, nonce, state, and replay;
4. opaque application Session with rotation, secure/HttpOnly/SameSite cookie policy, expiry, logout, and incident revocation;
5. request authentication dependency that never accepts identity from candidate-controlled headers;
6. CSRF protection for all state-changing routes and re-authentication for correction, export, deletion, recovery, and Consent changes;
7. safe login/logout/error UI and Local V1 route compatibility;
8. forged-token, JWKS rotation, issuer/audience confusion, nonce/state replay, fixation, CSRF, expiry, logout, re-auth, and revocation tests.

Recommended new ownership:

```text
app/ports/authentication.py
app/services/oidc_authentication.py
app/services/application_sessions.py
app/services/postgres_application_sessions.py
app/api/auth_routes.py
tests/acceptance/test_oidc_authentication.py
tests/acceptance/test_application_sessions.py
tests/integration/postgres/test_postgres_application_sessions.py
tests/unit/test_authenticated_request_context.py
```

Do not put Provider client secrets, issuer lists, redirect URIs, or cookie signing material in Git.

### Task 5: Stable Principal Mapping, Rotation, and Recovery

**Current readiness:** `NOT_IMPLEMENTED`; the frozen `PrincipalIdentity` value is reusable.

The mapping store should use two additive tables:

```text
principal_accounts
  deployment_id
  principal_id (stable random ID)
  status
  created_at / disabled_at / deleted_at

principal_subject_aliases
  deployment_id
  issuer_key
  subject_hmac
  hmac_key_version
  principal_id
  status
  created_at / retired_at
```

Raw issuer and subject do not enter these tables. `issuer_key` must be an approved non-PII deployment configuration identifier, not the raw token claim. Alias uniqueness is deployment + issuer key + key version + HMAC. Principal IDs remain stable through dual-read/single-write HMAC rotation.

Recovery requires explicit old/new proof or an equivalently governed external process. Ambiguous recovery freezes access. The implementation must never find a Principal by email, name, resume, device, IP, or similarity and must never auto-merge.

### Task 6: request-scoped Principal and async owner binding

**Current readiness:** `NOT_IMPLEMENTED`; existing Session/outbox infrastructure is reusable.

Add nullable paired owner columns for backward-compatible Local V1 rows:

```text
owner_deployment_id
owner_principal_id
```

A database check requires both null or both non-null. Hosted Session creation requires both; Local V1 Sessions remain null-owned. Ordinary Session update paths must not change ownership.

Proposal/outbox payloads should carry an opaque immutable owner-binding version, not a raw OIDC subject. The worker loads the authoritative Session owner and rejects owner mismatch before touching messages or Providers. It then rechecks purpose Consent, ignore/disable, deletion, and source state at operation time.

A singleton service factory is acceptable; a singleton current Principal is not. Principal resolution must be request- or task-context input.

### Task 7: Purpose-specific Consent Ledger v2

**Current readiness:** `V1_FOUNDATION_ONLY`.

Create a new append-only ledger rather than altering the aggregate v1 row:

```text
deployment_id
principal_id
purpose
policy_version
consent_version
decision = granted | revoked
effective_at
revoked_at
authority
policy_copy_ref
created_at
```

The current decision is derived deterministically by purpose and policy. V1 grants do not become V2 grants; every purpose starts off. A policy upgrade requires re-Consent. `fact_storage` revoke atomically makes facts ineligible and enqueues purge/tombstone work. Concurrent revoke/enqueue/select tests must prove no operation passes after the effective revoke boundary.

### Task 8: Authenticated self-service API and Candidate Memory Center

**Current readiness:** `TRUSTED_LOCAL_TEST_API_ONLY`.

Build new authenticated routes with request-scoped Principal authorization. Do not widen `/runtime/principal-memory/*` or reuse its feature flag for Hosted traffic.

Required API contracts:

```text
GET    candidate memory summary/list
POST   purpose grant/revoke
POST   proposal confirm/reject
POST   active fact correct/revoke
POST   ignore for this Session
POST   disable now
POST   export request/status/download
DELETE Principal Memory
```

All resources use opaque handles, uniform not-found responses, CSRF, re-auth where required, idempotency, rate limits, and audit-safe coarse status. The browser UI must support mobile, keyboard, screen reader, reduced motion, understandable purpose copy, no dark patterns, and no-penalty messaging.

### Task 9: Runtime controls and complete lifecycle

**Current readiness:** `PARTIAL_LOCAL_FOUNDATION`.

Reuse lifecycle validation and safe payload shaping, but replace two-step confirm/correct with store-owned atomic transactions. Add an explicit taxonomy `fact_key` and a partial unique index for active exclusive keys.

Required control state:

```text
session_memory_ignore (sticky to one Session)
principal_memory_disabled_at / control_version
export_jobs with expiring artifact
principal_deletion_jobs
operator_principal_tombstones
```

Disable must block the next assembly and all new leasing within 60 seconds. Delete/export must complete or explicitly fail within 24 hours. Deletion owns facts, proposals, effects, outbox work, bindings, caches, exports, aliases as approved, and derived references. Old-backup restore remains isolated until tombstone replay proves residue zero.

### Task 10: Control Foundation Acceptance

**Current readiness:** `NOT_RUN`.

Acceptance requires more than the existing unit suite:

- auth and recovery threat model;
- OIDC adversarial and application-Session tests;
- cross-deployment, cross-issuer, request reuse, worker retry/delay, and owner-transfer tests;
- live PostgreSQL migration, constraint, concurrency, rollback, and residue tests;
- Consent comprehension and policy-version re-Consent review;
- authenticated API authorization, CSRF, re-auth, enumeration, idempotency, and rate-limit tests;
- browser/mobile/keyboard/screen-reader/no-dark-pattern acceptance;
- ignore/disable/correct/revoke/delete/export concurrency and SLO drills;
- backup restore plus operator tombstone replay residue zero;
- no-penalty score/evidence/report parity;
- privacy artifact scan and full Local V1 regression.

Only the following output closes Task 10:

```text
PRINCIPAL_MEMORY_CONTROL_FOUNDATION=PASS
AUTHENTICATED_PRINCIPAL=PASS
CONSENT_USER_RIGHTS=PASS
WRITE_PRODUCTION_APPROVAL_REQUIRED
```

The audit itself must never emit that output.

## Safe migration sequence after authorization

The implementation must use additive, migration-owned steps. Each step must compile and pass focused tests before the next step.

1. Add authentication and application-Session contracts with all Hosted flags disabled.
2. Add Principal account and subject-alias tables; test stable random ID, isolation, collision, and key rotation.
3. Add nullable paired Session owner columns and immutable owner checks; do not backfill Local V1 Sessions into Principals.
4. Add async owner-binding version and worker lookup; keep proposal production gates disabled.
5. Add Consent v2 ledger and deterministic current-decision query; migrate no grants from v1.
6. Add control state, atomic fact-key constraints, export jobs, deletion jobs, and operator tombstones.
7. Add authenticated API routes behind a new default-off flag; keep trusted-local routes test-only.
8. Add Candidate Memory Center and accessibility/no-penalty UX behind authenticated capability discovery.
9. Run live PostgreSQL, browser, threat, concurrency, privacy, deletion, restore, and full regression acceptance.
10. Produce Control Foundation evidence while Write, Read, Assist, and trusted-local production gates remain disabled.

Do not combine OIDC, mapping, Consent, lifecycle, UI, and acceptance into one non-bisectable migration or commit.

## File ownership and collision map

| Area | Expected files | Collision control |
|---|---|---|
| Auth/request context | new auth ports/services/routes plus app startup wiring | Keep separate from existing Local V1 interview routes until request dependency is stable |
| Principal mapping | new mapping port/in-memory/PostgreSQL services and migration registry | Serialize edits to `postgres_runtime_migrations.py` and `postgres_schema_contract.py` |
| Session owner | `session.py`, `postgres_session.py`, `session_serialization.py`, graph/session creation callers | Land after mapping contracts; run legacy/v1/v2 Session compatibility tests |
| Async binding | domain event, proposal builder/processor, outbox dispatcher/worker | Preserve existing event idempotency and source-version checks |
| Consent v2 | new consent-v2 contract/store/migration; lifecycle/retrieval adapters | Do not mutate v1 grants into v2 authorization |
| Authenticated API | separate auth/memory routes and dependency injection | Do not edit trusted-local helper into a production authenticator |
| Memory Center | new frontend route/page/styles/client and browser specs | Coordinate with concurrent frontend work; stage exact files only |
| Lifecycle | fact stores, lifecycle/deletion/control/export services, migrations | Atomic transactions and database constraints precede API exposure |
| Acceptance | focused Python/PG/browser tests and external evidence tooling | Production records and private evidence remain outside Git |

## Stop conditions

Even after Productization and Data-use decisions pass, implementation stops if:

- the decision revision, ADR/spec digest, region, Provider class, or deployment scope no longer matches;
- Local V1 compatibility requires silently assigning an owner or Consent;
- raw issuer/subject or other PII would enter Principal Memory storage;
- account recovery cannot prove old/new ownership without automatic inheritance;
- Session ownership can change through an ordinary update;
- worker ownership depends on the current HTTP user;
- Consent v2 cannot remain purpose-specific, append-only, and default off;
- authenticated controls cannot meet CSRF, re-auth, enumeration, accessibility, or no-penalty requirements;
- atomic correct/exclusive active constraints or tombstone replay cannot be proven;
- any implementation would enable Write, Read, Assist, or real candidate processing before its later phase gate.

## Current boundary

```text
CONTROL_FOUNDATION_PHASE_PLAN=READY_AFTER_EXTERNAL_DECISIONS
CONTROL_FOUNDATION_IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_BUDGET_SHADOW=NOT_RUN
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_MEMORY_C1A_SPEC=DRAFT
PRODUCTION_CANARY=NOT_AUTHORIZED
```
