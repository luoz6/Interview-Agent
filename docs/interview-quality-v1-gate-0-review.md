# Interview Quality V1 Gate 0 automatic review

## Outcome

```text
engineering_status=PASS
quality_status=NOT_REQUIRED
review_status=PASS
open_blocking_findings=0
open_warnings=0
blocking_test_skips=0
```

The automatic Phase 0 review found three contract-evidence issues, fixed all three,
and reran the affected suite before granting Gate 0 Engineering PASS. This is a
primary-agent automatic review, not a claim of independent external or human review.

## Findings closed during review

1. Active Stage 40 evaluation code and its Markdown renderer still duplicated the
   historical 85%/90%/8/5% gates. They now load the versioned GateConfig; immutable
   historical publications remain unchanged.
2. Saved Stage 40 normalized attempts did not embed expected ranges. A new immutable
   sidecar binds all 20 case ranges to the saved run without editing its run directory.
   Deterministic replay observes 28 hits among 40 attempts, so the frozen 90% interval
   gate returns `FAIL` with exit code 1.
3. `retrying` appeared in the Report storage design but not the four-value public job
   axis. The Report ADR now states that it is an internal attempt substate rendered as
   public `job_status=running`.

## Contract review

The four P0 ADRs are mutually consistent:

- Plan preview/edit/start share one immutable V2 revision; start makes zero Provider
  calls and PlanSource raw text is stored once per family.
- Report Job, Artifact, and Head authorities are separate; active old content may
  coexist with a latest failed/running job; publication is one fenced transaction.
- Followup Decision and question generation are separate durable stages; replay uses
  the persisted decision and the two-follow-up limit is deterministic.
- Score, coverage, generation, job, evidence, and answer states are independent;
  Principal Memory cannot enter scoring or reports.

The Provider manifest matches the user authorization exactly: DeepSeek,
`deepseek-chat`, T27/T36/T57/T65, synthetic/public/redacted data only, no fallback,
unlimited cumulative budget/requests/tokens, mandatory metering/evidence, and explicit
hard stops. No credential is stored.

## Data and metric review

GateConfig is the active threshold source for Python, CLI, pytest, JSON, and Markdown
consumers. It represents blocking, warning/record-only, insufficient sample, and
insufficient comparable baseline outcomes. Latency cohorts distinguish cold/warm,
fixed/adaptive, follow-up/next, first/recovery, schema, question count, and Provider
path.

All four frozen dataset identities have a common machine schema and a minimal
synthetic fixture. Fixtures are explicitly not calibrated datasets and cannot enter a
blocking Gate. Canonical source/content hashes and a file-hash sidecar are verified.

## Verification

The Gate 0 suite completed with 50 passed, 0 failed, and 0 skipped. PostgreSQL 16.14
and pgvector 0.8.6 were rechecked in the isolated local container. Whitespace/diff
validation passed. There were no Prompt, production scorer, Hosted, production,
candidate-data, or destructive database changes.

The external main worktree advanced independently from the T00 baseline to its own
frontend branch while this isolated worktree remained on the original execution HEAD.
No external commit or working-tree content was merged into the quality branch.

Machine evidence: `docs/interview-quality-v1-gate-0-evidence.json`.
