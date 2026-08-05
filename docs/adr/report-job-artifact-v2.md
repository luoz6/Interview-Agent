# ADR: Report Job, Artifact, and Head V2

- Status: Accepted for Phase 1 implementation
- Date: 2026-08-05
- Schema version: `report-artifact-v2`

## Decision

Report work, immutable published content, and active/latest selection use three
separate authorities:

- `report_jobs` is the sole authority for job lifecycle;
- `report_artifacts` is the sole authority for published V2 report content;
- `report_heads` is the sole authority for active report and latest job selection.

The existing `{prefix}_review_artifacts` table remains internal
`ReviewRunOutput`. The legacy single-row `reports` table remains a compatibility read
source during migration. Neither may bypass runtime gates or become a second public
authority.

## Five independent Artifact axes

```text
job_status:        queued|running|completed|failed
generation_status: complete|degraded|failed
score_status:      scored|partial|unscored
coverage_status:   complete|partial|none
report_path:       microbatch|full_session|heuristic|legacy
```

Published Artifacts have generation status `complete` or `degraded`; `failed` belongs
to a job outcome and produces no Artifact. Score and generation reason codes are
stored independently. `is_fallback` is a legacy-derived display value only.
The storage-only `retrying` substate maps to public `job_status=running`; it exists to
coordinate attempts and does not add a sixth public Report Artifact axis.

## Additive storage model

`report_jobs v2` changes `session_id` from unique to indexed and permits historical
jobs. It adds `job_kind=initial|rescore`, `parent_job_id`, `source_report_id`,
`activate_on_success`, idempotency identity, attempts, lease, fencing, error, and
timestamps. A partial unique index or session-scoped lock allows at most one
`queued|running|retrying` job per session.

`report_artifacts` uses `report_id` as primary key and includes session, monotonic
revision, schema/rubric versions, the four Artifact axes, reason codes, immutable
payload, SHA-256, source/supersedes links, source job, and created time.
`(session_id, revision)` and `source_job_id` are unique.

`report_heads` contains one row per session with nullable `active_report_id`,
`latest_job_id`, and `updated_at`. Both referenced records must belong to the same
session.

## Job semantics

- `requeue` creates a new attempt for the same failed job and retains `job_id` and
  `latest_job_id`. It cannot create an Artifact or clear an active report by itself.
- `rescore` creates a new job with a new identity and `source_report_id`. It never
  overwrites the source Artifact.
- Creating an initial/rescore job locks the session/head, inserts the job, and updates
  only `latest_job_id` in one transaction.
- A final failure writes the fenced job failure and retains the existing
  `active_report_id`. Therefore one API view may correctly expose an old active
  Artifact and a newer failed or running job at the same time.
- PDF export is a separate operation bound to an exact `report_id + artifact_sha256`;
  export failure changes neither the report job nor the active pointer.

## Atomic publication protocol

Using one PostgreSQL connection and transaction while holding the valid job fencing
token:

1. lock job, session, and report head;
2. verify status, lease/fence, source hashes, and runtime gates;
3. insert the immutable Artifact with unique `source_job_id`;
4. conditionally update `active_report_id` when `activate_on_success=true`;
5. update `latest_job_id`;
6. mark the job completed;
7. update session/review-run state and result hash;
8. write required outbox records;
9. commit.

Any failure rolls back every step. If commit succeeds but the response is lost,
replay resolves `source_job_id + artifact_sha256` and returns the same Artifact.
In-memory behavior must model the same atomic visibility and fencing outcomes.

## Migration and rollback

The migration is additive: remove the old session uniqueness only after creating the
replacement indexes and concurrency guard; create Artifact/Head tables and foreign
keys; backfill compatible heads without altering payloads. During `artifact_shadow`,
old internal review output and V2 output may be compared by hash. During
`artifact_primary`, new public reads use Artifact+Head and no third long-lived payload
is written.

Rollback disables V2 routing and restores compatibility reads. It does not drop new
tables, overwrite old reports, move active pointers, or destructively clean history.
