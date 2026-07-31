# Memory Shadow old-backup restore drill

This runbook proves that restoring a backup taken before a completed deletion
does not permanently revive private interview or Principal Memory data. It is
an operational Shadow gate only. It does not authorize production execution or
Principal Memory consumption.

## Safety boundary

- Use synthetic data only.
- Do not call LLM or embedding providers.
- Do not run against production databases or production backups.
- Keep Budget enforcement, Context Compression consumption, Question Memory
  consumption, and Principal Memory consumption disabled.
- The operator tombstone ledger is the deletion source of truth. Evidence may
  contain aggregate category counts only; never copy locators, source digests,
  prompts, answers, resumes, reports, or normalized facts into the artifact.

The repository's V1 identity model uses an explicit resolver and
principal-scoped Consent. It has no persisted session-scoped consent or identity
binding row. Consequently the `session_bound_consent_bindings` restored count is
expected to be zero. Principal-scoped Consent is deliberately not deleted by a
single-session tombstone; principal deletion remains a separate operation.

## Execute

From a clean checkout of the validated RC:

```powershell
python -m pytest -q tests/test_memory_shadow_restore_drill.py tests/test_session_deletion_tombstone_replay.py tests/test_session_deletion.py
python -m scripts.memory_shadow_restore_drill --execute --restore-cycles 3 --evidence-output docs/memory-shadow-restore-drill-evidence.json
```

The runner creates three independent logical old-backup snapshots using only
synthetic state. For every cycle it deletes the source copy, exports the
integrity-protected operator tombstone, restores the old snapshot into a fresh
isolated in-process store set, imports the tombstone, and replays deletion. It
then exercises process loss and reclaim at all six deletion boundaries.

The live PostgreSQL focused suite separately verifies that session-sourced
Principal facts and queued effects are deleted atomically and that the isolated
runtime relations are cleaned. That suite must run before this gate is accepted:

```powershell
python -m pytest -q tests/test_postgres_principal_memory.py -m pg_runtime
```

## Passing output

```text
BACKUP_RESTORE_TOMBSTONE_REPLAY=PASS
RESTORED_PRIVATE_DATA_RESIDUE=0
PUBLIC_KNOWLEDGE_UNCHANGED=true
```

All private residue categories must be zero, all six injected failures must be
reclaimed, the public Knowledge Corpus fingerprint must be unchanged, and no
provider call may occur. Any non-zero residue or incomplete replay blocks
Principal Write Shadow promotion and retains the operator tombstone for repair.
