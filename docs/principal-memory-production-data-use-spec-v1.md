# Principal Memory Production Data-use Spec v1

**Spec status:** `DRAFT_FOR_EXTERNAL_REVIEW`
**Approval state:** `PRODUCTION_DATA_USE_SPEC=NOT_APPROVED`
**Entry gate:** `HOSTED_PRODUCTIZATION_DECISION=APPROVED`
**Current entry-gate result:** `BLOCKED`
**Applies to:** Principal Memory proposal write, fact storage, Read Shadow, and C1-A assist

## Purpose

This specification defines the proposed production data-use contract for Principal Memory in Hosted Multi-user V2. It is Task 2 review material for the v0.2 revised roadmap.

The document is complete enough for Product, Privacy, Security, Legal, Fairness, Accessibility, Operations, and Interview Quality review, but it is not yet submitted for approval because the Hosted Productization ADR is not approved. Nothing in this draft authorizes real candidate processing, a Provider call, human source review, Write Shadow, Read Shadow, C1-A, or a production configuration change.

External approvals, identities, tickets, signatures, deployment locators, Provider contracts, and decision digests must remain in the authorized external systems and must not be copied into Git.

## Data-use principles

1. Current-session data is authoritative. Principal Memory is derived, optional, revocable, and non-authoritative.
2. Every purpose is separately consented, versioned, default off, and checked at operation time.
3. Provider or model output can create only a `proposed` fact.
4. Only an authenticated candidate's explicit confirm or correct action can create an active fact.
5. Refusal, ignore, disable, revoke, correction, export, or deletion has no scoring or product penalty.
6. Data minimization applies to storage, Provider payloads, human review, metrics, logs, exports, and backups.
7. Principal Memory never becomes an input to score, evidence, report, rank, recommendation, hiring decision, public Knowledge, shared embeddings, or cross-Principal retrieval.
8. Uncertainty fails closed for the affected purpose without breaking the deterministic interview path.

## Data subjects and identity boundary

The data subject is the authenticated candidate. Every operation is scoped by `deployment_id` and stable random internal `principal_id`.

Principal Memory stores must not contain:

- raw OIDC issuer or subject;
- email, display name, phone number, postal address, IP address, device fingerprint, User-Agent, or government identifier;
- resume text, candidate answers, report text, project names, employer names, or free-text biographies as facts;
- model-generated personality, protected-class, health, disability, performance, employability, or hiring inferences;
- cross-Principal similarity, neighborhood, or identity-merge data.

Identity mapping is governed by the Hosted V2 ADR. Human reviewers and Providers receive no raw identity fields from the Principal Memory subsystem.

## Allowed structured data

### Proposal and fact taxonomy

All fact values use canonical JSON with versioned, allowlisted keys and enum values. Free text and unknown fields are rejected before persistence.

The production review scope is limited to:

| Fact type | Write proposal | Candidate-confirmed storage | Read Shadow | C1-A |
|---|---:|---:|---:|---:|
| `interview_language` | direct declaration only | allowed | would-select only | visible prefill; confirmation required |
| `accessibility_preference` | direct declaration only; never inferred | allowed | would-select only | UI/interaction only; never sent to Provider |
| `learning_goal` | not approved for production | not approved | excluded | excluded |
| `target_role_family` | not approved for production | not approved | excluded | excluded |
| `confirmed_skill` | not approved for production | not approved | excluded | excluded |

A future non-scored C1-B proposal may request `learning_goal` or `target_role_family` through a new plan and new approvals. This specification does not authorize it.

### Operational metadata

The service may store the minimum opaque metadata needed for state, isolation, retry, deletion, and audit:

- deployment-scoped Principal and Session owner identifiers;
- purpose, policy version, consent version, decision, and effective time;
- proposal/fact opaque IDs, taxonomy version, lifecycle status, and timestamps;
- opaque source binding without copied candidate text;
- idempotency, lease, outbox, deletion, and tombstone state;
- low-cardinality aggregate outcome, error, latency, and exposure counters.

Operational metadata must not be repurposed for profiling, ranking, marketing, or cross-candidate analysis.

## Purpose-specific Consent

The ledger is append-only and resolves current authorization deterministically from:

```text
deployment_id
principal_id
purpose
policy_version
consent_version
decision
effective_at
revoked_at
authority
```

Consent is never inherited from another purpose and is never cached only at Session creation. A policy-version change requires new explicit Consent.

### Proposed candidate-facing copy

Each purpose appears as an independent default-off control. The final localized copy requires Product, Privacy, Accessibility, and Legal comprehension review.

| Purpose | English copy | Chinese copy |
|---|---|---|
| `proposal_write` | Allow this service to identify a small set of structured preferences that you directly state, so you can review them later. Nothing is saved as active memory unless you confirm it. | 允许本服务从你直接表达的内容中识别少量结构化偏好，供你稍后审核。除非你明确确认，否则不会保存为生效的长期记忆。 |
| `fact_storage` | Allow this service to store preferences that you explicitly confirm. You can correct, revoke, export, or delete them. | 允许本服务保存你明确确认的偏好。你可以随时纠正、撤回、导出或删除。 |
| `read_shadow` | Allow this service to privately test which confirmed preferences it could find. This test must not change your interview, Provider input, score, or report. | 允许本服务在不改变面试的前提下，测试能否找到你已确认的偏好。该测试不得改变面试、Provider 输入、评分或报告。 |
| `assist_c1a` | Allow this service to show optional pre-interview suggestions based on confirmed language or accessibility preferences. Nothing changes unless you accept or edit a suggestion. | 允许本服务根据已确认的语言或无障碍偏好，在面试前显示可选建议。只有当你接受或修改建议后，当前面试设置才会改变。 |

Every control must also state:

- choosing no does not reduce interview functionality or affect evaluation;
- what data category is used and for which operation;
- the applicable retention and deletion behavior;
- how to revoke, disable, correct, export, and delete;
- which already in-flight operation, if any, may finish before disable takes effect.

### Purpose effects

| Purpose | Grant permits | Revoke effect |
|---|---|---|
| `proposal_write` | approved extraction and short-lived proposed records | stop new extraction/proposals before the next operation; reject queued effects; purge unconfirmed proposals within 24 hours |
| `fact_storage` | storage of candidate-confirmed active facts | make facts immediately ineligible; purge facts and derived references within 24 hours; write an opaque tombstone |
| `read_shadow` | aggregate-only would-select observation | stop new selection before the next operation; do not change other purposes |
| `assist_c1a` | visible optional pre-interview suggestions | stop new suggestions before the next operation; do not delete facts or change other purposes |

Confirm and correct require current `fact_storage` Consent. Correct must supersede the predecessor and activate the replacement in one database transaction. An exclusive fact key has at most one active value.

## Retention schedule proposed for approval

These are normative proposed maxima. Approval may shorten them. Any extension requires a new documented necessity, Privacy and Legal review, and a policy-version change when candidate expectations change.

| Data class | Maximum retention | End-of-period action |
|---|---:|---|
| Unconfirmed proposal and opaque source binding | 7 days from creation | purge proposal, effect, binding, and derived cache |
| Rejected proposal | 24 hours | purge proposal and derived state |
| Candidate-confirmed fact | 180 days from confirmation or explicit re-confirmation | expire and make ineligible; purge within 24 hours unless re-confirmed |
| Superseded/revoked/deleted fact online state | 24 hours | purge values, bindings, effects, refs, and cache; retain only opaque tombstone where required |
| Export artifact | 24 hours after generation | destroy artifact and access token |
| Low-cardinality aggregate observation buckets | 180 days | delete or roll into an approved less-granular aggregate |
| Consent ledger metadata without fact values | 365 days after account closure | purge unless a jurisdiction-specific legal obligation is externally approved |
| Online operational logs | 30 days | delete; logs must contain no identity, fact value, source, Prompt, answer, or report |
| Encrypted backups | 30 days | expire backup; replay operator tombstones before any restored copy receives traffic |
| Opaque operator tombstone | oldest-backup lifetime plus 30 days | delete only after restore-resurrection risk has ended |

The interview Session, transcript, and report have separate product retention policies. Principal Memory source binding does not extend their retention and does not copy their raw content.

Retention clocks must be enforceable by deterministic jobs with idempotent retry, metrics, failure alerts, and residue queries. A job failure does not make expired data eligible again.

## Candidate rights and service levels

Authenticated candidates must be able to:

- view proposed and active records with plain-language status;
- confirm or reject a proposal;
- correct an active fact atomically;
- revoke a purpose independently;
- ignore memory for one interview;
- disable memory now;
- export their Principal Memory in a documented portable format;
- delete Principal Memory and receive a coarse completion state.

Disable must take effect before the next context assembly and no later than 60 seconds. Export and online deletion must complete or return an explicit actionable failure within 24 hours. Candidate-facing APIs use opaque handles, uniform not-found behavior, rate limiting, CSRF protection, and re-authentication for sensitive actions.

An operation already leased at the moment of disable or revoke may complete only when it rechecks current Consent and control state before writing or selecting. Otherwise it becomes a no-op. The UI must explain this boundary without claiming an impossible rollback of a request already delivered to an external Provider.

## Provider processing contract

Production `proposal_write` is prohibited until an externally approved Provider contract and runtime configuration prove all of the following:

- no training or model improvement on candidate content;
- zero or the minimum explicitly approved Provider retention;
- request/response logging disabled or strictly allowlisted and content-free;
- approved processing region and subprocessors;
- DPA and cross-border transfer mechanism where required;
- authenticated transport, secret rotation, access logging, and incident notification;
- strict structured output and unknown-field rejection;
- timeout, bounded retry, rate limit, circuit breaker, and cost cap;
- prompt-injection resistance and direct-declaration-only extraction;
- Provider failure produces no proposal and does not change the interview path.

The application must not persist raw extraction requests or responses in ordinary logs, traces, metrics, test artifacts, or approval bundles. Production preflight must reject a Null, test, or policy-mismatched Provider component.

Read Shadow and C1-A do not invoke the extractor. C1-A never sends Principal facts or accessibility preferences to the Provider.

## Human proposal-quality review

Human source review is prohibited until this specification and a review protocol are externally approved. The approved protocol must enforce:

- a pre-registered sample size and selection method;
- authorized reviewer role, training, confidentiality, and time-bounded access;
- minimum source excerpt needed to classify the proposal;
- no bulk browsing, local download, screenshots, copy into tickets, or free-text reviewer notes in Git;
- controlled review environment with audit logging and automatic access expiry;
- fixed labels such as correct, unsupported, over-generalized, wrong-taxonomy, stale-source, conflict, privacy-sensitive, not-useful, duplicate, and review-unavailable;
- `privacy_sensitive=0` as a hard stop and a pre-registered unsupported threshold;
- deletion/revocation propagation to review queues and artifacts;
- only sanitized low-cardinality aggregate results leaving the controlled system.

Reviewers must not make hiring, scoring, diagnosis, personality, disability, or protected-class judgments.

## Accessibility, protected classes, and fairness

`accessibility_preference` may originate only from a candidate's direct declaration using an approved enum. It must never be inferred from speech, behavior, answer timing, medical content, resume, demographic proxy, or Provider output.

In C1-A it may affect only candidate-visible UI and interaction behavior such as keyboard support, captions, pacing controls, or display settings. It never enters an LLM payload, evaluator, score, evidence, report, rank, recommendation, or hiring decision.

Observation must not create identifiable demographic or accessibility cohorts. Low-volume buckets are suppressed, delayed, or merged. Insufficient data produces `CONTINUE_OBSERVATION`, not a fairness claim.

## Metrics, logs, and evidence

Routine evidence may contain only public revision, phase, duration, approved absolute caps, aggregate exposure counts, low-cardinality category/outcome counts, hard-stop count, aggregate error/latency, and configuration-restored state.

Routine evidence must not contain Principal, Session, fact, question, message, artifact, source, Prompt, answer, resume, report, OIDC, approval, ticket, Provider secret, or deployment locator. Hashing one of these values does not make it acceptable evidence.

Unknown evidence fields fail closed. Private artifacts are quarantined and deleted under Privacy review; they are never used to claim `PASS`.

## Deletion, backup restore, and incident handling

Online deletion must remove facts, proposals, effects, outbox work, source bindings, caches, derived references, and candidate export artifacts. The operator tombstone contains only the minimum opaque scope needed to prevent restoration from resurrecting deleted data.

A restored backup must remain isolated until tombstones are imported and replayed, residue queries return zero, caches and queues are rebuilt safely, and an Operations/Privacy check releases traffic.

The following require immediate phase shutdown and incident handling:

- cross-Principal or cross-deployment access;
- operation without current purpose Consent;
- extraction, selection, or suggestion after disable/delete;
- automatic activation or free-text fact persistence;
- inferred accessibility or protected-class data;
- Principal data in Provider payload outside approved Write extraction;
- Principal data in scoring, report, Knowledge, embedding, or hiring paths;
- deletion or backup-replay residue;
- absolute exposure-cap breach;
- private content in routine evidence.

Incident evidence follows data minimization and need-to-know access. Incident response does not authorize product reuse of quarantined data.

## Jurisdiction and subprocessor decision contract

The external approval record must bind, for each production deployment:

```text
approved_data_region
candidate_jurisdictions
controller_and_processor_roles
lawful_basis_and_consent_requirements
candidate_notice_version
provider_and_subprocessors
provider_retention_and_training_setting
cross_border_transfer_mechanism
retention_schedule_version
rights_and_incident_contacts
required_approving_functions
approval_time
expiry_or_revalidation_trigger
```

If any required field is absent, stale, scope-mismatched, or contradicted by runtime configuration, production preflight fails closed.

## Explicit prohibitions

This specification does not authorize:

- real Principal processing before Productization `GO` and Data-use approval;
- automatic Consent, confirmation, activation, correction, identity merge, or account-memory inheritance;
- free-text long-term memory;
- `learning_goal`, `target_role_family`, or `confirmed_skill` production consumption;
- hidden personalization or a non-dismissible suggestion;
- historical facts in Provider prompts, scoring, evidence, reports, Knowledge, embeddings, or hiring decisions;
- cross-Principal retrieval or analytics;
- external approval records or private candidate locators in Git;
- reusing Budget, Write, Read, or C1-A approvals across phases;
- exceeding approved absolute Principal, Session, duration, evidence, or cost caps;
- expansion, C1-B, or General Availability.

## Approval and change control

Approval requires the external functions named by the master roadmap: Product, Privacy, Security, Legal, Fairness, and Operations, with Accessibility and Interview Quality review for candidate copy and C1-A boundaries.

The approved external record must bind this specification's content revision, repository revision, deployment scope, regions, purposes, retention schedule, Provider contract/configuration, reviewer protocol, candidate notice, and revalidation trigger.

A material change to purpose, taxonomy, candidate copy, Provider, region, retention, human review, C1-A behavior, or data recipient requires a new policy version, impact review, and re-Consent where candidate expectations change.

## Review checklist

The external reviewers must verify:

- four Consent purposes are independent, understandable, and default off;
- candidate refusal and controls are penalty-free;
- taxonomy and operational metadata are minimal and free text is rejected;
- proposed retention values are necessary and enforceable;
- fact-storage revoke triggers purge and tombstone behavior;
- Provider no-training, retention, region, DPA, logging, and incident terms are proven;
- human review is sampled, authorized, minimal, auditable, and deletable;
- accessibility is direct-declaration and UI-only;
- score, report, Knowledge, embeddings, and hiring paths are structurally isolated;
- deletion, export, backup replay, and incident procedures meet the stated SLOs;
- metrics and evidence cannot identify a candidate;
- jurisdiction, transfer, subprocessor, and notice obligations are bound externally;
- no approval is reused for a later phase.

## Current outcome

```text
HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED
PRODUCTION_DATA_USE_SPEC=NOT_APPROVED
TASK_2_REVIEW_MATERIAL=PREPARED_NOT_SUBMITTED
REAL_CANDIDATE_PROCESSING=PROHIBITED
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_MEMORY_C1A_SPEC=DRAFT
PRODUCTION_CANARY=NOT_AUTHORIZED
```
