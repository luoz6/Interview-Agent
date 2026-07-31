# Principal Memory consumption specification

**Status:** Draft for future planning only

**Audience:** product, privacy, security, fairness, platform, and interview-runtime reviewers

**Scope:** candidate-controlled use of confirmed Principal Memory in an interview
**Implementation authority:** none

```text
PRINCIPAL_MEMORY_CONSUMPTION_SPEC=DRAFT
IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
```

## 1. Purpose and boundary

This document defines the conditions a future plan would have to satisfy before
Principal Memory could influence a candidate-facing experience. It does not
authorize code, routes, migrations, provider-context changes, a production
canary, or configuration support for `MEMORY_LONG_TERM_MODE=consume`.

The current product remains zero-injection Read Shadow. Principal Memory is not
public Knowledge, scoring evidence, a hiring signal, or an authoritative account
of candidate ability. Current-session evidence always wins over historical
memory.

## 2. Terms

- **Authenticated Principal:** a product account identity established by an
  approved authentication system. Resume, name, email, phone, IP address,
  User-Agent, browser/device identifier, embedding similarity, and model output
  are not Principal identity sources.
- **Consumption:** using an eligible active fact to change an allowed operation.
- **C1:** the proposed first, 1% canary stage. C1 is not authorized by this
  draft.
- **Ignore for this interview:** a sticky session control that prevents memory
  reads for one interview without changing stored Consent or facts.
- **Disable memory now:** a real-time revocation control that stops future
  proposal, storage, and read operations until the user explicitly opts in
  again.

## 3. Mandatory prerequisites

Every prerequisite is blocking. A future implementation plan must link each
item to a named acceptance test and approval record.

| ID | Requirement |
|---|---|
| PMC-001 | A formally authenticated Principal must exist. Trusted-local and inferred identity are insufficient for production consumption. |
| PMC-002 | Consent must be candidate-visible, understandable, purpose-specific, and default off. It must not be bundled with interview participation. |
| PMC-003 | The candidate must be able to view, confirm, correct, revoke, delete, and export memory through authenticated self-service controls. |
| PMC-004 | The interview start flow must offer **Ignore memory for this interview**. The active interview must offer **Disable memory now**. |
| PMC-005 | Every use must have a candidate-visible indicator and an accessible explanation of the used category and effect. |
| PMC-006 | Revocation, correction, deletion, export, and backup replay SLAs must be approved and monitored. |
| PMC-007 | Production privacy approval, security approval, and fairness approval must all be recorded after implementation review. |
| PMC-008 | Consumption needs an independent canary, independent rollback, and independent observation plan. Shadow approval cannot be reused as consumption approval. |
| PMC-009 | Scoring and report isolation must be demonstrated by deterministic equality tests and adversarial fixtures. |
| PMC-010 | The public Knowledge Firewall, deletion replay, low-cardinality metrics, and zero cross-Principal leakage gates must remain green. |

Declining, ignoring, disabling, correcting, revoking, deleting, or exporting
memory is a no penalty action. It must not reduce score, must not remove
features, must not shorten the interview, and must not create a negative label
or hiring signal.

## 4. C1 fact allowlist

C1 may discuss only these canonical, user-confirmed fact categories:

| Fact | Proposed bounded effect | Restrictions |
|---|---|---|
| `interview_language` | Choose the language used to phrase a follow-up | The current interview's explicit language choice overrides memory immediately. |
| `accessibility_preference` | Apply an approved UI or interaction accommodation | Must be a direct user declaration; never infer health or disability; never expose it to scoring or reports. |
| `learning_goal` | Prefer one relevant practice topic when current-session evidence leaves multiple equivalent choices | Cannot lower difficulty, infer weakness, or replace the interview plan. |
| `target_role_family` | Break a tie between equally valid follow-up examples | The current job and interview plan always override it. |

`confirmed_skill` is excluded from C1. It must never affect scoring, evidence
selection, report text, difficulty, or hiring conclusions. Any future proposal
to use it for follow-up selection requires a separate fairness analysis,
candidate study, canary plan, and approval.

The allowlist is closed. Free text, project/company names, inferred preferences,
scores, evaluations, and recruiting outcomes remain invalid.

## 5. Prohibited uses

The following are prohibited in every phase:

- using a historical score, historical feedback, or prior answer to calculate a
  current score;
- producing or influencing a hiring recommendation, recruiting outcome, rank,
  risk label, or candidate disposition;
- inferring personality, integrity, emotion, mental health, physical health,
  political belief, religious belief, ethnicity, race, age, marital status,
  pregnancy, or another protected or sensitive trait;
- writing Principal facts into public Knowledge, corpus manifests, embeddings,
  or retrieval hits;
- cross-Principal similarity, nearest-neighbor lookup, merging, sharing, or
  collaborative filtering;
- implicit personalization without a candidate-visible indicator;
- hiding, contradicting, downgrading, or replacing current-session evidence;
- penalizing a candidate for refusing, ignoring, disabling, correcting,
  revoking, deleting, or exporting memory;
- continuing new reads or writes after **Disable memory now**;
- presenting a historical fact as current-session evidence or provider truth.

## 6. Consent and user-control contract

### 6.1 Consent

Consent purposes remain separate: proposal creation, fact storage, and
consumption cannot imply one another. The Consent screen must show the proposed
fact categories, permitted operation, retention, deletion behavior, and the
fact that scoring/reporting do not use memory. The checkbox or equivalent
control is default off and requires a positive user action.

Every context assembly re-reads current identity, policy version, Consent,
session ignore state, disable state, fact status, source status, expiry, and
deletion state. Cached authorization is not sufficient.

### 6.2 View, correction, export, and deletion

- View lists the canonical category/value, authority, confirmation time,
  expiry, source status, and last-use category. It does not reveal hidden model
  reasoning.
- Confirm is an explicit action. A model proposal never confirms itself.
- Correct creates a new candidate-confirmed fact and supersedes the old value;
  the old value becomes immediately ineligible.
- Revoke stops authorization while retaining the minimum record needed to
  complete deletion and audit.
- Delete removes facts, effects, bindings, and derived references and records an
  operator tombstone for backup replay.
- Export returns a documented, machine-readable, authenticated package. It must
  not include another Principal, internal provider payload, or unrelated
  interview content.

## 7. Real-time ignore and disable

**Ignore memory for this interview** is captured before the first context
assembly and remains sticky for that session. It produces no new selection and
does not alter the stored account preference.

**Disable memory now** takes effect before the next context assembly and within
60 seconds, whichever is earlier. From that point there is no new proposal, no
new selection, and no new context injection. It must not reduce score, must not
remove features, and must not create a negative label.

The system cannot retract an in-flight provider request that was already sent.
The UI must state that boundary without implying that later calls may continue
using memory. Completion of the in-flight request must not schedule another
memory read or write. If the provider supports cancellation, cancellation is
best-effort and is not a substitute for the next-assembly barrier.

## 8. Runtime consumption contract

### 8.1 Allowed operation

C1 permits only `interview.followup.context_assembly`. It does not permit prep,
answer evaluation, question scoring, report generation/repair, evidence
selection, PDF generation, public Knowledge retrieval, or agent-control use.

### 8.2 Selection

All of the following must be true at operation time:

1. authenticated deployment and Principal exact match;
2. current consumption Consent and policy version;
3. session is not ignored, deleting, or deleted;
4. fact is active, user-confirmed, unexpired, unrevoked, and undeleted;
5. source status is available and not deleting/deleted;
6. fact category and operation are in the C1 allowlist;
7. fact is relevant to the current follow-up and does not conflict with the
   current interview plan or evidence.

For an exclusive key, exclude all conflicting values. Do not guess a winner.
Treat a fact as stale when its policy-defined freshness window expires, its
source is unavailable, or current-session evidence contradicts it. Stale facts
are excluded; they are never silently refreshed from model output.

### 8.3 Bounds

- Maximum fact count: 3.
- Maximum token budget: 120, measured by the resolved provider tokenizer with a
  conservative fallback.
- Select deterministically before rendering. Never truncate a structured fact
  into an ambiguous fragment.
- Deduplicate by canonical category/value and preserve deterministic ordering.

### 8.4 Prompt location and marker

The rendered block is candidate-visible and labeled exactly as
**Non-authoritative historical preference**. It appears after system policy and
the current interview plan/evidence, and before the current candidate message.
Each item contains only the canonical category/value, authority, confirmation
state, and a coarse source status. It contains no raw source excerpt, prior
answer, score, report, Principal locator, or internal digest.

The block states: **Current-session evidence always wins. Do not use this block
for scoring, evaluation, reporting, hiring decisions, or claims about ability.**

### 8.5 Failure behavior

- Fail closed for memory on missing/ambiguous identity, missing Consent, policy
  mismatch, conflict, stale/deleted source, invalid taxonomy, privacy error, or
  uncertain authorization.
- Fail open to deterministic interview when the memory store, selector,
  renderer, metrics, or provider-side personalization helper fails. The
  interview continues without memory.
- A failure must not expose a fact, change scoring/reporting, or suppress an
  interview feature.

## 9. Propagation and service-level objectives

| Operation | Proposed maximum | Required proof |
|---|---:|---|
| Ignore or Disable memory now | next context assembly and within 60 seconds | concurrent selection/revoke barrier test |
| Fact correction/revocation eligibility | next context assembly and within 60 seconds | cache invalidation and race test |
| Online fact/effect/reference deletion | within 24 hours | deletion job and residue metrics |
| Backup resurrection protection | before a restored copy receives traffic | operator tombstone replay |
| Authenticated export | within 24 hours | export completeness and cross-Principal isolation test |

An SLO breach is an automatic stop for consumption and blocks new canary
assignment. Backup retention may remain longer only when deleted data is
cryptographically inaccessible or guaranteed to be re-deleted by tombstone
replay before traffic.

## 10. C1 canary and automatic stop

C1 is capped at 1% of explicitly opted-in, eligible sessions in one approved
deployment. Assignment uses a sticky session assignment so a session cannot
switch behavior mid-interview. Control sessions use the deterministic path.
Candidate-visible disclosure remains mandatory in the canary.

Automatic stop disables new consumption assignment and context injection while
preserving deterministic interviews. Hard stops include:

- any cross-Principal, no-Consent, revoked, expired, deleted, unconfirmed, or
  protected-category fact consumed;
- any Prompt block outside the allowed operation or bounds;
- any scoring and report isolation difference;
- any current-session evidence override;
- any hidden personalization or missing disclosure;
- any delete/disable SLA breach or backup replay residue;
- any public Knowledge mutation or personal fact in an embedding;
- error-rate regression above 0.5 percentage points at at least 200 samples;
- P95 latency regression above 20% at sufficient samples;
- incomplete durable metrics or private observation artifact.

Rollback sets consumption to disabled, stops new consumption worker leasing,
retains minimal aggregate evidence and tombstones, and leaves facts inactive or
unchanged according to lifecycle policy. Rollback never converts a terminal
fact back to active and never removes still-required migrations or deletion
records.

## 11. Required test and approval package

A future implementation cannot request C1 until it supplies:

- authenticated identity and account-recovery threat tests;
- accessible Consent/decline/ignore/disable UX tests;
- cross-Principal, purpose, policy-version, conflict, stale, deletion, restore,
  and in-flight-disable race tests;
- exact Prompt snapshot tests for placement, marker, fact/token bounds, and raw
  content exclusion;
- scoring and report isolation equality tests;
- protected-class proxy and disparate-impact review;
- Knowledge Firewall and artifact privacy audits;
- live isolated PostgreSQL migration, concurrency, cleanup, and rollback tests;
- an independent canary/rollback/observation plan and approvals from product,
  privacy, security, fairness, and operations owners.

## 12. Open decisions

The future product team must still decide the authenticated account model,
Consent copy and jurisdiction handling, correction history visibility, export
format, exact freshness windows, provider cancellation capability, canary
population, fairness metrics, and the product owner for emergency disable.
Those decisions require user research and formal review; this draft does not
make them implicitly.

## 13. Terminal state

```text
PRINCIPAL_MEMORY_CONSUMPTION_SPEC=DRAFT
IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
```
