# Memory Budget Shadow Staging Acceptance

Status: deterministic Profile B Budget Shadow gate passed.

This reference record evaluates the committed aggregate observation against
the Task 4 hard stops, statistical thresholds and sample-coverage gates. It
authorizes the next Staging-only Principal Memory Write Shadow task. It does
not authorize Budget enforcement, Principal Memory consumption, production
Shadow or production observation.

## Pinned inputs

- Validated application RC: `a982b1f`.
- Isolated Staging preflight implementation: `5280c9d`.
- Budget Shadow observer implementation: `adcbe68`.
- Budget Shadow observation evidence commit: `dbc44b2`.
- Validated Task 4 acceptance implementation: `ec43d7d`.
- Observation schema: `memory-budget-shadow-observation-v1`.
- Profile: B, synthetic deterministic matrix.

The Task 4 runner and tests were executed from a clean detached `ec43d7d`
worktree. The later commit containing this document is not substituted as the
validated runner revision.

## Sample gate

| Dimension | Required | Observed | Result |
|---|---:|---:|---|
| Synthetic Sessions | 300 | 300 | Pass |
| Follow-up samples | 200 | 300 | Pass |
| `zh_hans` | 100 | 100 | Pass |
| `en` | 100 | 100 | Pass |
| `mixed` | 100 | 100 | Pass |
| Ten required scenarios | at least 1 each | 30 each | Pass |
| Estimator fallback | at least 1 | 300 | Pass |
| Would-select | greater than 0 | 3360 | Pass |
| Would-drop | greater than 0 | 1800 | Pass |

The scenarios cover long code identifiers, numbers, corrections, negation,
fallback, long history, bounded current content, mixed structure and replay
shape. No raw synthetic content is stored in the observation.

## Non-statistical hard stops

| Hard stop | Threshold | Observed | Result |
|---|---:|---:|---|
| Known-over-budget Provider call | 0 | 0 | Pass |
| Mandatory current-content loss | 0 | 0 | Pass |
| Provider input change | 0 | 0 | Pass |
| Privacy artifact hit | 0 | 0 | Pass |
| Budget/config conflict | false | false | Pass |
| Execution error | 0 | 0 | Pass |
| Durable metrics incomplete | false | false | Pass |
| Unavailable observation bucket | at most 1 | 0 | Pass |
| Isolated cleanup residue | 0 | 0 | Pass |
| Provider call in synthetic Profile B | 0 | 0 | Pass |

Durable aggregate metrics reported `data_complete=true`; isolated cleanup and
rollback were verified.

## Statistical gates

The error-rate sample is 300, so the statistical threshold applies.

- Baseline follow-up error rate: 0.0.
- Observed follow-up error rate: 0.0.
- Error-rate delta: 0.0 percentage points; threshold is 0.5 percentage points.
- Declared synthetic baseline P95: 500.0 ms.
- Observed synthetic P95: 533.252 ms.
- P95 regression: approximately 6.65%; threshold is 20%.

The latency source is explicitly
`synthetic_baseline_plus_measured_shadow_overhead`. Because no Provider call
occurred, this is deterministic Profile B gate evidence rather than production
latency evidence.

The runner also proves that a statistical path with fewer than 200 samples
returns `CONTINUE_OBSERVATION`; low sample does not become PASS or a
statistical BLOCKED result. Non-statistical hard stops remain blocking at any
sample size.

## Final configuration boundary

~~~text
budget mode after observation=disabled
budget enforcement=disabled
compression consumption=disabled
question memory consumption=disabled
principal memory=disabled
configuration persisted=false
production observation=NOT_RUN
~~~

The Shadow result was never consumed by context assembly and the observer
called no Provider.

## Reproducibility evidence

- Clean detached acceptance worktree: `ec43d7d`.
- Focused Budget preflight, observer and acceptance tests: 18 passed, 0 failed.
- Compile check: passed.
- Acceptance runner exit code: 0.
- Acceptance runner output: exact four-line contract below.
- User-owned Reports, Help and visual-design changes remained outside the
  Task 4 commit.

No DSN, database fingerprint, exact prefix, Session/Principal/Fact/Question
identifier, Prompt, Answer, Resume, Excerpt, Source Manifest, Artifact
reference or Provider payload is stored in this record.

## Acceptance result

~~~text
BUDGET_SHADOW_STAGING=PASS
BUDGET_ENFORCEMENT=BLOCKED
PRINCIPAL_MEMORY_SHADOW=NOT_RUN
PRODUCTION_OBSERVATION=NOT_RUN
~~~
