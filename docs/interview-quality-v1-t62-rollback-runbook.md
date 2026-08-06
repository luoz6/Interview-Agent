# Interview Quality V1 — T62 migration and rollback runbook

This runbook is the operator procedure for an additive PostgreSQL runtime migration, legacy Report JSON promotion, reader rollback, and backup restore. Repository execution is limited to a generated isolated test prefix. It does not authorize changing a deployed or production-like database.

## Safety boundary

- Stop Interview and Review worker leasing for the target prefix and set both LangGraph rollout percentages to `0` before a deployed maintenance window.
- Resolve and independently verify the target database, prefix, revision, backup destination, maintenance window, rollback owner, and restore owner outside repository evidence.
- Never print or commit the DSN, credentials, database fingerprint, candidate data, report payload, session ID, Artifact ID, or backup archive.
- Use custom-format `pg_dump`; encrypt and retain a deployed backup according to the approved data policy.
- The migration and rollback are additive. **do not drop** legacy tables/columns, Artifact history, Head rows, migration records, immutable triggers, or source jobs.
- Repository rehearsal may drop only an automatically generated prefix matching the test-prefix guard. Any other target is `STOP`.

## 1. Preflight and dry run

From the exact approved revision:

```powershell
python -m scripts.postgres_runtime_migrate
python -m scripts.migrate_legacy_reports --limit 100
```

Both commands must report dry-run mode and must not connect. Confirm PostgreSQL and pgvector versions, free space, worker drain, advisory-lock availability, and that the latest backup is restorable. Record aggregate counts and hashes without recording identifiers or payloads.

Capture these invariants before migration:

- row counts for sessions, messages, legacy reports, report jobs, Artifacts, and Heads;
- count of dangling active pointer rows (must be `0`);
- per-history aggregate count and an independently retained Artifact hash manifest;
- latest migration ID/checksum/transaction mode;
- legacy `reports` columns and the current reader mode.

## 2. Create the restore point

For a real maintenance window, prefer a full database custom archive so functions, triggers, sequences, extensions, and every prefix dependency are included:

```powershell
pg_dump --format=custom --no-owner --no-privileges --file '<approved-secure-path>' '<verified-database>'
```

The repository T62 drill uses container-local PostgreSQL 16 tools and an isolated table allowlist. Its archive is held only in process memory, never committed, and is destroyed when the test exits.

Immediately verify the archive with `pg_restore --list`. A missing, empty, unencrypted where required, or unverifiable archive is `STOP`.

## 3. Apply the additive schema migration

```powershell
python -m scripts.postgres_runtime_migrate --apply
python -m scripts.runtime_preflight --profile core
```

The migration holds the prefix advisory lock, uses one transaction-owned business connection, and writes the latest migration marker only after application DDL succeeds. The LangGraph checkpointer phase is separately idempotent. If the process stops before the marker, rerun the same command after diagnosis; never invent or manually insert the marker.

Required checks after apply:

- old sessions and legacy reports are still readable;
- all old report columns remain present;
- foreign keys, uniqueness constraints, immutable/reference triggers, partial active-job index, and `(session_id, revision)` index exist;
- an `EXPLAIN` of Artifact history by session/revision uses the expected index when the planner selects an index path;
- active pointer dangling count remains `0`;
- pre/post business row counts show migration 0 data loss.

## 4. Promote legacy reports

Lazy promotion of one explicitly selected session:

```powershell
python -m scripts.migrate_legacy_reports --apply --session-id '<trusted-session-id>'
```

Bounded batch promotion:

```powershell
python -m scripts.migrate_legacy_reports --apply --limit 100
```

Repeat batches until `migrated_count=0`. Promotion is idempotent and additive: revision 1 uses schema `legacy-v1`, stores the canonical Artifact hash plus the raw legacy source hash, and does not update or delete `reports.report_json`.

## 5. Reader cutover and rollback drill

Start with the new reader only after the schema and promotion checks pass:

```text
REPORT_ARTIFACT_READ_MODE=artifact_first
```

Verify current report, history, exact-version PDF, latest job, active pointer, and hashes. To roll back public reads without deleting new writes:

```text
REPORT_ARTIFACT_READ_MODE=legacy
```

Restart only the read-serving processes through the approved deployment system. Confirm the compatibility shadow is readable and that Artifact/Head row counts and every Artifact hash are unchanged. Rollback changes routing only; it must not move the active pointer, cancel or rewrite completed jobs, or remove new schema data.

After diagnosis, restore the new authority:

```text
REPORT_ARTIFACT_READ_MODE=artifact_first
```

Recheck the same active report ID/hash and history hash manifest. Any drift or dangling active pointer is `STOP`.

## 6. Restore drill

Restore into an isolated recovery database first:

```powershell
pg_restore --no-owner --no-privileges --exit-on-error --dbname '<verified-recovery-database>' '<approved-secure-path>'
```

Do not restore over a live target until the incident owner explicitly authorizes it. After restore, validate:

- all scoped row counts equal the backup snapshot;
- legacy reports are readable;
- each historical Artifact hash is byte-for-byte unchanged;
- each Head references an Artifact and latest job in the same session;
- no active pointer is dangling;
- foreign keys, unique constraints, indexes, triggers, and query plans are present;
- both reader modes work without mutation.

## 7. Stop conditions

Immediately print/record `STOP` and preserve the database for diagnosis when any of the following occurs:

- target identity, prefix, revision, backup, restore owner, or maintenance window is unverified;
- backup tools are absent or archive validation fails;
- migration checksum/transaction mode diverges;
- migration requires destructive repair or manual winner selection;
- old data becomes unreadable or any legacy table/column disappears;
- row counts decrease, Artifact hash changes, or history revisions are missing;
- active pointer is dangling or crosses session ownership;
- a required FK, unique constraint, index, trigger, or migration marker is missing;
- reader rollback deletes or rewrites new-schema data;
- retrying the migration is not idempotent.

Provider calls are not part of this procedure. T62 repository acceptance must report `provider_calls=0` and zero skipped migration tests.
