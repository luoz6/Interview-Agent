# Production Memory Shadow approval request

**Packet readiness:** Ready for review

**Approval status:** Pending

**Requested phase:** Budget Shadow only

**Production observation:** Not run

## Decision requested

Approve or reject a tightly bounded production Budget Shadow observation. The
requested change computes hypothetical context-budget selection outcomes and
publishes aggregate metrics. It must not change Provider input, enforce a
budget, compress context, consume Question Memory, write/read Principal Memory,
or change interview behavior.

This request does not ask for Principal Write Shadow, Principal Read Shadow,
Budget enforcement, Context Compression consumption, Question Memory
consumption, a production migration, or long-term-memory consumption.

No production action may begin while this document says **Pending**.

## Why the request is Budget Shadow only

The Staging program validated Budget, Principal Write, and Principal Read
Shadow serially. Production must preserve the same one-axis-at-a-time rule.
Budget Shadow is the lowest-risk first variable because it observes
hypothetical selection without changing the Provider request.

Principal Write Shadow would persist candidate-related proposals and therefore
requires a separately approved product identity, Consent, data classification,
retention, and deletion operating model. Principal Read Shadow depends on that
approved write population. Both remain not authorized by this request.

## Evidence summary

The submitted evidence is bound to:

- validated base RC `a982b1f`;
- operational validation revision `ffc58a1`;
- final repository documentation/contract revision at or after `f8ca5c7`;
- isolated Staging, synthetic Profile B;
- production observation `NOT_RUN`;
- long-term-memory consumption `BLOCKED`.

Validated results:

- final Python regression: 1548 passed, 163 skipped;
- live PostgreSQL runtime: 44 passed, 1659 deselected;
- frontend production build: 4587 modules transformed;
- full browser suite: 54 passed, 22 configured skips;
- Budget Shadow samples: 300, with 100 each for English, Chinese, and mixed;
- Principal Write samples: 300; automatic active/confirmed: 0;
- proposal reviews: 300; privacy-sensitive: 0;
- Principal Read samples: 300; Prompt/provider mutation: 0;
- old-backup restore cycles: 3; fault boundaries: 6; private residue: 0;
- observation artifacts audited: 9; violations: 0;
- public Knowledge files: 58; mutation: 0;
- test listener and isolated PostgreSQL relation residue: 0.

Canonical evidence:

- `docs/memory-operational-shadow-evidence.json`;
- `docs/memory-operational-regression-evidence.json`;
- `docs/memory-shadow-status.json`;
- `docs/memory-shadow-security-review-evidence.json`;
- `docs/memory-shadow-restore-drill-evidence.json`.

## Requested production guardrails

The operator must begin with effective traffic `min(0.1%, approved cap)`. The
warm-up lasts at least 30 minutes and 20 follow-up samples. Only a zero-hard-stop
warm-up may ramp to the approved cap, which must not exceed 1%. The final
production conclusion requires at least 24 hours and 200 follow-up samples.

Insufficient samples produce `CONTINUE_OBSERVATION`, close the current window,
and require a new approval record. They never extend this approval implicitly.

| Guardrail | Requested limit |
|---|---:|
| Traffic | maximum 1% of eligible interview follow-up operations |
| First observation window | minimum 24 hours |
| Statistical evaluation | minimum 200 follow-up samples before error/latency promotion decisions |
| Provider input changes | 0 |
| Mandatory current-content loss | 0 |
| Over-limit Provider calls | 0 |
| Budget enforcement | disabled |
| Compression and Question Memory consumption | disabled |
| Principal Write/Read Shadow | disabled |
| Principal Memory consumption | blocked |
| Metrics | durable aggregate only; no per-entity drill-down |

Low traffic does not convert elapsed time into evidence. If production cannot
reach the statistical minimum, the result is `CONTINUE_OBSERVATION`; privacy,
content-loss, input-mutation, and configuration violations remain immediate
hard stops at any sample size.

## Automatic stop and rollback

Immediately stop Budget Shadow and restore disabled mode on any of:

- mandatory current-content loss above 0;
- Provider input mutation above 0;
- a known over-limit Provider call above 0;
- a privacy artifact hit above 0;
- incomplete durable metrics or more than one unavailable bucket;
- configuration conflict or any other memory axis enabled;
- error-rate regression above 0.5 percentage points at 200 or more samples;
- P95 latency regression above 20% at sufficient samples;
- non-zero test/temporary relation residue or evidence containing private data.

Rollback stops new Shadow work, sets Budget mode to disabled, leaves every
enforcement/consumption/Principal gate disabled, and preserves the deterministic
Interview path. Only minimum aggregate counts and stable gate codes are
retained.

## Data and privacy boundary requiring approval

Before approval, the reviewers must name the authorized production data
category, lawful/contractual basis, metric retention period, operator roles,
incident route, observation window, and rollback owner. No Prompt, answer,
resume, report, source excerpt/digest, object locator, DSN, database identifier,
table prefix, or Provider payload may enter an approval or observation artifact.

This packet does not itself establish the production data basis. That is a
required reviewer decision.

## Required approval record

| Approval owner | Status | Required decision |
|---|---|---|
| Change owner | Pending | exact deployment, revision, traffic, window, and change ticket |
| Operations / rollback owner | Pending | monitoring coverage, stop authority, rollback readiness, on-call route |
| Privacy owner | Pending | production data category, retention, aggregate dimensions, incident handling |
| Security owner | Pending | configuration boundary, secrets, store access, artifact and firewall controls |
| Fairness owner | Pending | zero behavior/scoring impact and no protected-category observation |

Approval must be explicit, attributable, time-bounded, and bound to one
revision and one deployment. Silence, repository merge, Staging PASS, or this
document's existence is not approval.

## Current decision state

```text
MEMORY_PRODUCTION_SHADOW_PACKET=READY_FOR_REVIEW
REQUESTED_PHASE=BUDGET_SHADOW_ONLY
APPROVAL_STATUS=PENDING
PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED
PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED
LONG_TERM_MEMORY_CONSUMPTION=BLOCKED
PRODUCTION_OBSERVATION=NOT_RUN
```
