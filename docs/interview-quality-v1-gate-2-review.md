# Interview Quality V1 Gate 2 automatic review

## Outcome

```text
implementation_revision=2a51b71e2fa87189e1f83f087f309904419db79f
implementation_tree=8dd125f027251cba37f633ae9ff263d9875a7ae4
engineering_status=BLOCKED
quality_status=NOT_RUN
overall_status=BLOCKED
automatic_review=PASS
open_findings=0
blocking_skips=0
```

The deterministic Gate 2 implementation is complete and regression-clean, but the
Gate itself is not declared PASS. The 80-case calibration dataset remains
`PENDING_INDEPENDENT_REVIEW`, so its blind partition was correctly kept closed. T25
and T26 therefore remain BLOCKED, and T27 was not run because its frozen dependency
requires T26 to pass. The unified Provider authorization is valid, but authorization
does not replace either the independent review or the required blind-test evidence.

This Gate status does not stop independent Engineering work in later phases. It does
prevent T27 and any later Quality claim that depends on scoring calibration.

## Completed implementation

- T19 publishes explicit `scored`, `partial`, and `unscored` states. An unanswered or
  skipped question is `not_evaluated` with null score and null dimensions; it cannot
  lower an ability average. A deterministically identifiable attempted non-answer may
  receive a real zero without trusting a Provider-supplied score.
- T20 removes the fixed 60 score and the fallback runtime-quality bypass. A degraded
  report may remain scored only when backend-owned rules and evidence support it;
  otherwise it is degraded and unscored.
- T21 routes report, microbatch, contract, quality, replay, and rule-score aggregation
  through one coverage service. Applicable numeric dimensions alone enter weighted
  aggregation, and partial reports retain evaluated and eligible denominators.
- T22 preserves null and partial semantics through API projections, React, the legacy
  static report page, and PDF. No unscored value is rendered as `0`, `None`, or a
  zero-valued progress bar.
- T23 publishes Chinese-friendly Rubric `interview-quality-rubric-v3.3-candidate`
  (`913d673fad8bfbde134788e2a48d96acfafe92936abded090bb3b9d5de514c03`).
  It covers process, causality, trade-offs, risks, recovery, metrics, production
  operation, mixed language, negation, unsafe absolute claims, and structured missing
  points. Missing-point annotations override false-positive keyword signals.
- T24 consumes the frozen GateConfig for expected ranges, absolute calibration,
  strata, Spearman correlation, interval error, stability, fallback, completeness,
  and insufficient-sample decisions. Replay sources are read-only and every rubric
  revision writes a new immutable target directory.
- T25 provides a deterministic 80-case synthetic dataset: 60 dev and 20 blind,
  balanced across four question types and five quality levels, with Chinese, English,
  and mixed-language coverage. Every case has an expected interval, rationale,
  required evidence, missing-point/error annotations, source boundaries, and pending
  independent-review metadata. No real candidate or Principal Memory data is present.
- T26's deterministic harness, saved-response replay, dev diagnostics, error taxonomy,
  blind-access lock, and zero-Provider invariant are implemented. Completion is
  blocked only at the required independent review and one-time blind evaluation.

## Saved-response and calibration evidence

The historical v2 saved-response result remains immutable at 28/40 expected-range
hits (70%, FAIL). Subsequent replay directories were never overwritten. The final
v3.3 candidate replay of the same 40 saved attempts produced:

- expected-range hit rate: 1.0 (40/40);
- strong hit rate: 1.0 (10/10);
- interval-outside MAE: 0.0;
- expert-score Spearman: approximately 0.985151;
- pairwise ranking accuracy: 1.0;
- evidence grounding rate: 1.0;
- maximum repeated score delta: 4.0;
- deterministic saved-response replay delta: 0.0;
- fallback rate: 0.0;
- Provider invocations: 0.

Its overall decision remains `INSUFFICIENT_SAMPLE` solely because the old 40-case run
has four sparse strata (`language:unknown`, `language:zh`, `quality:empty`, and
`quality:off_topic`). The minimum sample size was not lowered and expected ranges were
not widened.

The unreviewed 60-case dev partition is diagnostic-only. It produced 1.0 expected-
range hit rate, 1.0 strong hit rate, 1.0 grounding, 1.0 ranking, zero interval error,
zero fallback, zero blocking failures, and no remaining error categories. Its
`INSUFFICIENT_SAMPLE` result is expected because the dev-only split has two strata
below the frozen minimum. The blind partition was not opened or executed.

## Automatic review findings closed

1. Medium calibration cases originally described omissions only in prose, so the
   scorer could not apply missing-evidence semantics. `required_missing_points` is now
   validated, generated, and passed to `DimensionEvidence.missing`.
2. Mixed-language evidence annotations used Chinese tokens where the answer contained
   English tokens. This reduced grounding and could misclassify a candidate's unsafe
   statement as generated output. Evidence terms now match their source language.
3. A negated phrase such as “does not include production metrics” could still expose
   advanced keywords. Rubric v3.3 gives structured missing-point codes authority over
   those false-positive signals and records the changed hash.
4. A Provider-adapter test still expected an attempted one-character non-answer to be
   unscored. It now proves the Provider's 95 is rejected while the deterministic rule
   assigns a grounded zero.
5. Round-review tests still expected skipped and unanswered questions to receive zero.
   They now prove null score, null dimensions, `not_evaluated`, and the exact reason
   code.

No review finding remains open. A PostgreSQL immediate-claim fixture returned no job
once under parallel test pressure. The identical test passed in isolation, the
complete report batch passed, the durable batch passed 31/31, and a 100-job immediate
enqueue/claim probe completed without recurrence. It is retained as a non-blocking
observation rather than hidden or promoted to a reproducible defect.

## Verification and rollback

- report-focused backend: 397 passed, 0 failed;
- Gate/config/authorization/calibration contracts: 23 passed, 0 failed;
- real PostgreSQL durable Report/Artifact workflow: 31 passed, 0 failed;
- calibration, metrics, and rubric focused batch: 42 passed, 0 failed;
- round-review truth-semantics regression: 11 passed, 0 failed;
- full backend excluding two non-applicable historical freeze guards: 2,327 passed,
  3 non-blocking skips, 0 failed;
- frontend: 7 Vitest tests passed and Vite production build passed;
- `git diff --check`: passed;
- secret and fixed-score scans: no credential, DSN, fixed 60 score, or fallback quality
  bypass in the Gate 2 change set.

This phase adds no destructive database migration. Existing report Artifact and legacy
read compatibility tests pass, active reports are not replaced on failed regeneration,
and every replay output is append-only. Rollback can return to the previous code
revision without deleting report Artifacts or rewriting the historical v2/v3 replay
evidence.

Two deliberately excluded historical guards were not edited: the Local V1 publication
lock requires the implementation tree to remain unchanged relative to its accepted
revision, and the lock-byte test compares historical LF bytes in a Windows
`core.autocrlf=true` worktree. The three full-suite skips are two POSIX-only path tests
on Windows and the opt-in real-LLM smoke; none substitutes for a Gate 2 required test.

Machine-readable evidence is recorded in
`docs/interview-quality-v1-gate-2-evidence.json`.
