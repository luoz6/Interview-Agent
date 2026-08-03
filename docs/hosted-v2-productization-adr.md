# ADR: Hosted Multi-user V2 Productization

**ADR status:** `PROPOSED_FOR_EXTERNAL_DECISION`
**Decision state:** `HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED`
**Applies to:** Hosted Multi-user V2 and Principal Memory production work
**Does not change:** Local V1 defaults, production configuration, or production authorization

## Purpose

This ADR is the Task 1 decision package for the v0.2 revised long-term-memory roadmap. It asks whether Interview Agent should become a supported Hosted Multi-user V2 product with real authentication, account isolation, candidate rights, operational ownership, and production data governance.

The repository currently describes Local V1 as a local, single-machine, single-user product without login or account isolation. Hosted V2 is therefore a product and operating-model decision, not a routine implementation detail.

This document is intentionally not an approval record. Approval identities, tickets, signatures, deployment locators, and external-system digests must remain in the authorized external decision system.

## Decision requested

The external decision body must choose exactly one outcome:

| Outcome | Meaning | Repository consequence |
|---|---|---|
| `GO` | The organization accepts the Hosted V2 product and operating model described here, subject to the separate Data-use Spec | Task 2 may seek approval; Tasks 4–34 remain blocked until Task 2 is approved |
| `NO_GO` | The organization will not operate Hosted V2 under this model | The roadmap stops; Local V1 remains unchanged |
| `REVISE` | The direction may continue only after named decision gaps are resolved externally | No implementation or production-data processing is authorized |

Silence, repository access, a merged pull request, or general permission to implement is not a `GO` decision.

## Proposed product boundary

### Users and roles

Hosted V2 would support these distinct roles:

- candidate: owns their interview sessions, current-session settings, Consent decisions, and Principal Memory controls;
- authorized reviewer: may access only the minimum approved proposal-quality sample under the Data-use Spec;
- support operator: may diagnose service state but cannot browse candidate memory values by default;
- privacy/security operator: may execute incident, deletion, and tombstone procedures under controlled access;
- deployment administrator: configures an approved OIDC issuer and deployment scope but cannot merge Principals automatically.

Candidate refusal, revocation, ignore, disable, correction, export, or deletion must not reduce interview functionality, alter scoring, or create a negative signal.

### Tenant and deployment model

The proposed security boundary is an isolated `deployment_id`. A Principal, Session, fact, proposal, Consent record, cache entry, metric assignment, and async owner binding must always be scoped to one deployment.

The first Hosted V2 release must not provide:

- cross-deployment Principal lookup or memory sharing;
- automatic organization-to-organization account linking;
- collaborative filtering or similarity-based retrieval;
- a global mutable current-user singleton;
- shared candidate-memory administration across tenants.

An installation may host more than one deployment only when storage keys, authorization queries, caches, queues, logs, backups, and operational access are proven deployment-scoped.

### Local V1 compatibility

Local V1 remains the default product path. It keeps its no-login, local single-user behavior and all Principal Memory production gates disabled. Hosted V2 authentication and candidate controls must be additive, explicitly configured, and fail-closed; they must not silently convert an existing Local V1 installation.

Local V1 data is not automatically migrated into a Hosted Principal. Any future migration requires a separate, candidate-visible import design and authorization.

## Proposed authentication and account boundary

### Authentication provider class

Hosted V2 requires a standards-compliant external OpenID Connect Provider using authorization code flow. Each deployment must configure an exact allowlist of issuer and audience values. The runtime must validate authorization response state, nonce, signature, key rotation, issuer, audience, expiry, and revocation before creating an application session.

A concrete provider, contract, supported jurisdictions, uptime commitment, incident contact, and subprocessor posture must be selected in the external decision record. Until that selection exists, the ADR cannot become `GO`.

The following are not production identity sources:

- trusted-local headers or test routes;
- email address, display name, phone number, IP address, device fingerprint, resume, or User-Agent;
- model inference, embedding similarity, or administrator guess;
- a raw OIDC issuer or subject stored in Principal Memory tables.

### Application session security

Hosted V2 must include secure cookie or token rotation, CSRF protection, session-fixation prevention, logout, re-authentication for sensitive controls, expiry, incident revocation, and request-scoped Principal resolution.

Async work must use an immutable opaque owner binding frozen from the Session owner. A worker must not infer ownership from the current HTTP request and must re-read current Consent, disable, deletion, and source state before each operation.

### Stable Principal mapping

The internal identity design is fixed as:

```text
verified issuer + subject
  -> deployment-scoped versioned HMAC alias
  -> stable random internal principal_id
```

HMAC rotation may add or replace aliases but must not change the internal Principal ID. Email, name, phone, raw issuer, and raw subject are excluded from Principal Memory storage.

### Recovery, rebind, and compromise

Account recovery must not automatically inherit old memory. Rebinding requires explicit proof for both the old and new authenticated accounts, or an externally governed recovery procedure with equivalent assurance. Ambiguous recovery freezes the old binding and keeps memory inaccessible.

Security operations must be able to revoke application sessions, freeze aliases, disable every Principal Memory mode, stop async leasing, and initiate candidate-visible remediation without rewriting historical ownership.

## Data region and lifecycle boundary

Each deployment must be assigned one approved data region before production use. Principal Memory data, source bindings, queues, backups, and human-review access must remain within the approved regional and subprocessor boundary. Cross-region replication is not part of the initial authorization.

The separate Production Data-use Spec must approve Consent copy, purposes, retention, deletion/export SLOs, Provider behavior, human review, accessibility boundaries, and jurisdiction. A Productization `GO` does not approve those data uses.

Account closure must:

1. stop new authenticated and async Principal operations;
2. revoke or invalidate active application sessions;
3. purge online Principal facts, proposals, effects, bindings, caches, and derived references within the approved SLO;
4. create the minimum opaque operator tombstone needed to prevent backup resurrection;
5. replay tombstones before a restored backup receives traffic;
6. provide export and deletion completion states without exposing internal identifiers.

Legal hold, if supported at all, must be explicitly defined by jurisdiction and must not silently keep data available for product use.

## Operating model

### Required ownership

Before `GO`, the external decision record must assign accountable functions for:

- Product scope and candidate experience;
- Change approval and production revision binding;
- Operations, on-call, rollback, capacity, and cost;
- Privacy rights, notices, retention, export, and deletion;
- Security threat response, identity compromise, and access review;
- Fairness and Interview Quality isolation;
- Accessibility review;
- Legal jurisdiction, Provider, and subprocessor review.

Names and external record locators are deliberately not stored in Git.

### Support and incident response

Hosted V2 requires a support path for account access, Consent, correction, export, deletion, and incident notice. The service must have an on-call owner during every production Shadow or Canary window.

Immediate shutdown conditions include cross-Principal access, identity ambiguity, operation without current Consent, data after disable/delete, unauthorized Provider processing, Principal data entering scoring/report/Knowledge paths, and failure to restore a disabled configuration after a window.

### Service objectives proposed for approval

The following are minimum product requirements, not a claim that the current repository satisfies them:

| Capability | Proposed objective |
|---|---|
| Disable-now | Effective before the next context assembly and no later than 60 seconds |
| Online delete/export | Completed or explicitly failed within 24 hours |
| Production phase stop | Central kill switch and assignment stop available to the on-call owner |
| Backup restore | No traffic before tombstone replay proves residue zero |
| Identity ambiguity | Fail closed; no Principal Memory access |
| Consent uncertainty | Fail closed for the affected purpose |

Availability and recovery-time objectives for the Hosted interview service itself must be selected from measured capacity and support commitments in the external decision record. This ADR does not invent an unsupported uptime promise.

### Cost and capacity

Hosted V2 introduces OIDC, durable Principal mapping, append-only Consent, candidate self-service, deletion/export workers, Provider extraction, observation metrics, backup replay, security operations, and support costs.

Every production phase must use an exact revision, absolute Principal and Session caps, a maximum duration, a cost cap, a rollback owner, and an incident channel. Percentage assignment may only narrow an absolute cap. No phase may be expanded merely because the previous bounded phase passed.

## Alternatives considered

### Keep Local V1 only

This is the safest `NO_GO` option when the organization cannot provide identity, privacy, support, or on-call ownership. It preserves current behavior and avoids representing experimental local components as a hosted account system.

### Use email or trusted-local identity

Rejected for production. These identifiers do not provide the required authentication, account-recovery, collision, and cross-tenant guarantees.

### Use HMAC output as the Principal ID

Rejected. Key rotation would either change identity or require keeping an old pseudonymous identifier as the permanent business key. Versioned aliases must map to a separate stable random Principal ID.

### Build authentication and governance after Shadow starts

Rejected. Real candidate proposal extraction is already a production data use. Authentication, purpose-specific Consent, candidate rights, Provider policy, and incident ownership must exist first.

## Consequences of `GO`

A `GO` authorizes only the next decision step: completing and approving the Production Data-use Spec. It does not authorize OIDC implementation, real candidate data, Write Shadow, Read Shadow, C1-A implementation, Production Canary, expansion, or General Availability.

After the Data-use Spec is separately approved, repository work must proceed in the task order and phase gates defined by the v0.2 revised master roadmap. Local V1 compatibility remains an acceptance requirement.

## Approval checklist

The external decision body must confirm all of the following before recording `GO`:

- Hosted V2 target users, roles, and deployment/tenant boundary are accepted;
- a concrete OIDC Provider class, issuer-management process, and account-recovery owner are accepted;
- data regions and prohibited cross-region behavior are accepted;
- Product, Change, Operations, Privacy, Security, Fairness, Accessibility, Interview Quality, and Legal responsibilities are assigned;
- support, on-call, incident, deletion, export, backup replay, and exit operations are funded;
- Local V1 remains supported and is not silently migrated;
- the stable Principal mapping and no-automatic-inheritance rules are accepted;
- absolute production exposure caps and independent phase approvals are accepted;
- the separate Data-use Spec remains a mandatory gate;
- C1-B, expansion, General Availability, and memory use in hiring decisions remain excluded.

## External decision record contract

The authoritative external record must bind:

```text
decision = GO | NO_GO | REVISE
adr_revision
repository_revision
product_scope
deployment_model
approved_regions
oidc_provider_class
account_recovery_model
support_and_on_call_model
required_approving_functions
decision_time
review_expiry_or_revalidation_trigger
```

The external record must not be copied into the repository. Repository status remains `HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED` until a phase preflight verifies a current, scope-matching decision through the authorized external system.

## Current outcome

```text
HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED
TASK_1_DECISION_PACKAGE=READY_FOR_EXTERNAL_REVIEW
TASKS_4_TO_34=BLOCKED_BY_PRODUCTIZATION_AND_DATA_USE_GATES
LOCAL_V1=UNCHANGED
```
