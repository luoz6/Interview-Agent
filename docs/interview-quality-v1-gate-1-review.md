# Interview Quality V1 Gate 1 automatic review

## Outcome

```text
engineering_status=PASS
quality_status=NOT_REQUIRED
automatic_review=PASS
open_findings=0
blocking_skips=0
```

Gate 1 now has immutable Plan, Report, and Followup Decision authorities in both
memory and PostgreSQL. The review found and closed six implementation issues before
granting Engineering PASS:

1. A fresh Report Artifact prefix assumed the legacy job table already existed.
   The V2 store now creates or upgrades the compatible queue shape independently.
2. Legacy jobs could receive a null idempotency key and later fail V2 mapping. The
   migration derives a stable `legacy:<job_id>` key and freezes the column as non-null.
3. Durable Review completion updated only the legacy single-row report. It now
   publishes Artifact, Head, Job, Session, and ReviewRun in one fenced transaction;
   the old row is a compatibility shadow rather than active truth.
4. Artifact sources and Head pointers relied only on application checks. Database
   triggers now reject cross-session source, supersedes, active-report, and latest-job
   references.
5. The legacy queue did not create a Report Head for jobs created after migration.
   enqueue and migration now update `latest_job_id` without changing the active report.
6. Followup Decision maximum attempts were process configuration rather than frozen
   decision state. `max_attempts` is now persisted at prepare time and used after
   restart or lease takeover.

## Contract and migration review

- T13 supports multiple jobs per session, one active job, immutable monotonic Report
  revisions, canonical hashes, source-job replay, failed-rescore retention, and
  transaction failure injection at Artifact, Head, Job, ReviewRun, and Session steps.
- T14 exposes active Artifact separately from latest Job, version and job histories,
  immutable report-ID reads/PDFs, failed-job requeue, and new rescore jobs.
- T15 provides one five-axis `ReportViewModel`, explicit v1/v2 parser registry,
  nullable score semantics, coverage denominator validation, and per-question and
  per-dimension evaluation states that cannot convert `not_evaluated` into zero.
- T16 promotes completed legacy reports to revision-one `legacy-v1` Artifacts without
  changing old JSON, remains idempotent, and keeps an explicit legacy rollback reader.
- T17 persists unique command Decisions and bounded Attempts with Lease, heartbeat,
  fencing, replay hash, crash takeover, and immutable completed results.
- T18 installs Vitest, React Testing Library, jest-dom, user-event, and jsdom; the first
  fixtures distinguish failed jobs from unscored Artifacts and preserve old active
  content after failed rescoring.

The additive runtime schema is now `followup_decision_v1` (V18). Real PostgreSQL
migration tests prove first apply, repeat no-op, upgrade from older manifests, schema
validation, and durable runtime factory compatibility. No existing table or immutable
Artifact is destructively removed.

## Verification

The stable main backend batch completed with 2,278 passed, zero failed, and two
non-blocking skips (the real-Provider smoke outside Gate 1 and a POSIX-only path test
on Windows). The recovery/cold-start batch completed 6 passed with no skips. The
Gate-focused batch completed 108 passed with no skips. Frontend verification completed
clean `npm ci`, 5 Vitest tests, and the production Vite build.

Two historical guards are not Gate 1 tests: the accepted Local V1 publication test
requires an unchanged implementation tree, and the historical lock-byte test compares
LF hashes in a Windows `core.autocrlf=true` checkout. Their remaining assertions pass;
neither was edited to manufacture a result.

No Provider call, credential, real candidate data, Hosted behavior, production write,
or destructive database migration was used. Machine evidence is recorded in
`docs/interview-quality-v1-gate-1-evidence.json`.
