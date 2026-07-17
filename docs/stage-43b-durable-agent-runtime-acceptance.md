# Stage 43B Durable Agent Runtime Acceptance

Status: `PASS`

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

## Release Gates

| Gate | Result |
| --- | --- |
| Full Python regression | 679 passed, 1 skipped |
| PostgreSQL runtime preflight | 3 tables, 11 indexes, 3 CASCADE foreign keys |
| Agent ledger write latency | p95 32.086 ms |
| Authenticated Redis/Celery recovery | 10 of 10 checks passed |
| Duplicate delivery and expired receipt recovery | PASS |
| Transient retry, dead-letter, and replay | PASS |
| Five-Agent persisted ledger correlation | PASS |
| Control-plane privacy | PASS, 0 sensitive-field violations |
| Deterministic Playwright | 8 passed, 2 real-model checks skipped |
| JavaScript syntax and CSS build | PASS |
| Core runtime preflight | PASS |
| Stage 40 artifact audit | PASS |
| Stage 42 artifact audit | PASS, 5 artifacts |
| Stage 43A acceptance | PASS |

The recovery acceptance used isolated PostgreSQL and Redis services with an
authenticated Celery worker. It covered atomic state/outbox persistence,
publisher outage recovery, duplicate delivery, expired receipt reclamation,
bounded retry, permanent dead-letter handling, identity-preserving operator
replay, five-Agent ledger correlation, and public control-plane privacy.

The acceptance artifact contains metadata and stable identifiers only. No
local embedding model was downloaded or loaded; deterministic and fake-agent
paths were used for the recovery checks, and real-model browser cases remain
explicit opt-in tests.
