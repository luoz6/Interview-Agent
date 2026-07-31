# Principal Memory consumption risk review

**Decision: do not implement.** The operational Shadow evidence is sufficient
to draft requirements, not to authorize consumption or a production canary.
This review explains the risks a future implementation and approval packet must
resolve.

## Decision context

The existing system has explicit test/trusted-local identity, purpose-scoped
Consent, proposed-only model output, bounded taxonomy, deletion/tombstone
replay, aggregate metrics, and zero-injection Read Shadow. It does not have the
authenticated product identity, candidate self-service UX, consumption Prompt
contract, production privacy/fairness approval, or independent canary needed
for safe personalization.

## Risk register

| Risk | Severity | Why it matters | Mitigation | Required evidence | Approval owner |
|---|---|---|---|---|---|
| Identity collision or account takeover | Critical | Memory from another person could influence an interview or be disclosed | authenticated Principal, recovery controls, deployment isolation, fail-closed reads | authentication threat model, cross-Principal penetration and recovery tests | Security |
| Cross-Principal cache/store leakage | Critical | Same taxonomy values can cause unsafe key reuse | deployment/Principal exact keys, no similarity lookup, bounded cache identity | concurrency, cache-collision, multi-account deletion tests | Security + Platform |
| Consent dark pattern or bundled participation | Critical | Candidates may feel forced to accept personalization | default off, separate purposes, plain-language copy, decline with no penalty | moderated UX/accessibility study and Consent audit | Privacy + Product |
| Disable race | Critical | A revoked preference could remain in the next request | operation-time re-read, next-context barrier, 60-second SLO, in-flight disclosure | revoke/select/context-assembly race and provider cancellation tests | Platform + Privacy |
| Backup resurrection | Critical | Deleted facts may return after restore | operator tombstone ledger and replay before restored traffic | repeated restore drill with zero private residue | Operations + Privacy |
| Protected-class proxy | Critical | Accessibility, language, role, or goals could proxy protected traits | closed taxonomy, direct declaration, UI-only accommodation, no scoring/report use, disparate-impact review | proxy analysis and adversarial fairness fixtures | Fairness + Privacy |
| Historical anchoring | Critical | Prior labels may bias questions, difficulty, evidence, or scoring | current-session evidence priority, exclude confirmed skill from C1, scoring/report isolation | exact equality tests and blinded evaluator review | Fairness + Interview Quality |
| Prompt injection | High | Candidate text or provider output may request active status, Consent bypass, scoring, disclosure, or Knowledge writes | untrusted extractor, canonical taxonomy, proposed-only status, visible bounded block | seven-intent adversarial suite plus mutation tripwires | Security |
| Stale or conflicting preference | High | Outdated language/goal/role could personalize incorrectly | freshness windows, source status checks, exclude all conflicting values, correction/supersede flow | stale, source deletion, contradiction, and correction tests | Product + Platform |
| Hidden implicit personalization | High | Candidate cannot understand or contest behavior changes | candidate-visible indicator and Non-authoritative historical preference marker | accessible UX snapshots and comprehension study | Product + Privacy |
| Current evidence suppression | Critical | Historical memory could override what the candidate says now | current-session evidence always wins and contradiction excludes memory | deterministic conflict fixtures and Prompt/source ordering tests | Interview Quality + Fairness |
| Public Knowledge contamination | Critical | Personal facts could become shared retrieval data | separate stores/dependencies, no embedding, loader rejection, corpus fingerprint gate | Knowledge Firewall source audit and deletion invariance | Security + Knowledge Owner |
| Excessive retention or incomplete deletion | Critical | Revoked personal data may persist in facts, effects, refs, logs, or metrics | 24-hour online deletion SLO, tombstones, aggregate-only metrics, artifact audit | lifecycle/deletion/fault/restore drills and residue queries | Privacy + Operations |
| Observation re-identification | High | Low-volume dimensions can identify a candidate | aggregate only, minimum display threshold, merge/delay/suppress small buckets | artifact scan and low-cardinality query review | Privacy + Observability |
| Availability and latency regression | Medium | Memory failure could degrade or block the interview | fail open to deterministic interview, fact/token caps, 20% P95 stop | sufficient-sample error/latency canary metrics | Operations |
| Canary assignment drift | High | Mid-session behavior changes make attribution and consent unclear | 1% cap, sticky session assignment, one deployment, explicit opt-in | deterministic assignment and rollback tests | Release Manager |
| Canary rollback failure | Critical | Unsafe context injection may continue after stop | central kill switch, stop new leasing, context-assembly barrier, deterministic fallback | timed rollback drill and zero post-stop injection | Operations + Security |
| Correction history misuse | High | Superseded values could still influence behavior or reveal sensitive history | terminal predecessor exclusion and minimum history exposure | correction/export/deletion contract tests | Privacy + Product |
| Provider retention or logging | Critical | A personal preference block can leave system boundaries | approved provider/data policy, no raw source, minimal block, content-free telemetry | provider DPA/security review and log/artifact sentinel audit | Legal + Security |

## C1-specific risk decision

The four discussed categories are not equally safe:

- `interview_language` is the clearest candidate for C1 because it is visible,
  reversible, and easily overridden in the current session.
- `accessibility_preference` can support access but has protected-class proxy
  risk. It must remain a direct, candidate-controlled UI/interaction setting
  and never enter scoring or reporting.
- `learning_goal` can create difficulty or weakness inferences. It may only
  break a tie between equivalent follow-ups and needs fairness evaluation.
- `target_role_family` can reproduce historical role assumptions. The current
  job and plan must override it.
- `confirmed_skill` is excluded from C1 because it creates strong historical
  anchoring and scoring/difficulty risk.

## Required approval sequence

1. Product approves authenticated identity and candidate controls.
2. Privacy approves Consent, retention, export, deletion, provider, and
   jurisdiction behavior.
3. Security approves identity, Prompt, provider, cross-Principal, firewall, and
   rollback controls.
4. Fairness approves the taxonomy, proxy analysis, no-penalty behavior, and
   scoring/report isolation.
5. Operations approves SLOs, metrics, automatic stop, tombstone replay, and the
   independent canary rollback drill.
6. A new implementation plan is reviewed. Only then may implementation
   authorization be considered; a separate decision is still required for the
   production canary.

No Shadow acceptance result substitutes for any step.

## Residual risk

Even a canonical fact can be semantically wrong, outdated, coerced, or a proxy
for a protected trait. Candidate-visible disclosure and correction reduce but
do not eliminate this risk. Provider cancellation cannot guarantee retraction
after a request has left the system. Low-volume canaries can miss rare privacy
failures, while high-volume canaries increase exposure. These residual risks
require user research, legal/privacy review, fairness measurement, and a
time-bounded canary with immediate rollback authority.

## Review result

```text
PRINCIPAL_MEMORY_CONSUMPTION_SPEC=DRAFT
IMPLEMENTATION=NOT_AUTHORIZED
PRODUCTION_CANARY=NOT_AUTHORIZED
```
