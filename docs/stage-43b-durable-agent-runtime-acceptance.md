# Stage 43B Durable Agent Runtime Acceptance

Status: `PENDING_RECOVERY_ACCEPTANCE`

Date: 2026-07-17

## Implemented Gates

| Gate | Status |
| --- | --- |
| Runtime work contracts | PASS |
| PostgreSQL control schema and CASCADE | PASS |
| Atomic session and outbox commit | PASS |
| Leased Local dispatcher | PASS |
| Receipt-controlled round review | PASS |
| Sanitized Agent run ledger | PASS |
| Safe read-only runtime APIs | PASS |
| Dead-letter and report recovery | PASS |
| Control-plane privacy unit gate | PASS |

## Pending Release Gates

- Authenticated PostgreSQL runtime preflight and ledger p95.
- Authenticated Redis/Celery outbox and receipt recovery.
- Duplicate delivery and expired receipt recovery.
- Transient retry, permanent dead-letter, and operator replay.
- Five-Agent persisted ledger correlation.
- Deterministic browser and full Python regression.
- Stage 40, Stage 42, and Stage 43A artifact audits.

PASS may be recorded only after every named Stage 43B recovery check succeeds.
Acceptance artifacts must contain metadata and stable IDs only.
