# A2A-V1 Semantic Business Baseline

## Status

- Phase: `T00 — Semantic Business Baseline`
- Plan version: `v0.2`

## Purpose

Freeze the business meaning before transport changes. A later `completed`
status must continue to mean real business execution, not merely a successful
HTTP/task status.

## Primary Business Path

```text
Resume / JD
  -> Interview Plan
  -> Session Start
  -> Question
  -> Answer
  -> Follow-up Decision
  -> Follow-up or Next Question
  -> Review
  -> Report
```

## Required Business Invariants

### Prep

- Role profile is produced or safely degraded.
- Interview plan is launchable.
- Question count matches configuration budget.
- Evidence scope is legal.
- Grounding is either present or explicitly degraded.

### Interview

- Current question is correct.
- Answer is persisted with the correct question id.
- Follow-up decision is executed.
- Gap lifecycle is consistent.
- Follow-up text does not duplicate prior question/follow-up.
- State version advances correctly.

### Review

- Real evaluation is executed.
- Evidence is present or explicitly degraded.
- Score is within the legal range.
- Evaluation result is persisted.

### Report

- Report generation consumes evaluation artifacts.
- Final report is generated.
- Advice is non-empty or explicitly degraded.
- Required report sections exist.

## Fake Success Guard

Any `completed` claim must be backed by actual domain artifact/state. A task
may not be considered complete when:

```text
status = completed
artifact = none
business execution = none
```

## Current Observed Baseline

Local preview execution has been observed to:

- Generate a five-question default plan.
- Start a legacy interview session.
- Persist candidate answers and skipped questions.
- Finish the interview.
- Run legacy review/report generation.
- Persist a completed report with metadata.

This is the semantic reference for the legacy/local path.

## Artifacts

The actual automated baseline snapshots will be placed under:

```text
tests/a2a_baseline/
artifacts/a2a-baseline/
```

after the A2A boundary/artifact contracts are implemented and the comparator
is available.

## Gate A

Status: `PENDING_REVIEW`

The semantic baseline above must be frozen before any Agent is switched to the
A2A transport path.
