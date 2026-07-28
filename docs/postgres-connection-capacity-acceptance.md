# PostgreSQL Connection Capacity Acceptance

Status: `READY_FOR_CAPACITY_AWARE_FENCING_CANARY`

Stage: `48 — PostgreSQL Connection Ownership and Capacity`

Production observation: `NOT_RUN`

Capacity artifact: `postgres-capacity-v1`

## Outcome

Stage 48 repository implementation and local PostgreSQL evidence are complete.
Runtime PostgreSQL access is split into four separately bounded domains owned
by `PostgresConnectionDomains`:

| Domain | Driver / ownership | Configured max | Observed peak |
| --- | --- | ---: | ---: |
| Business SQL | bounded psycopg2 transaction pool | 12 | 12 |
| Telemetry | independent bounded psycopg2 pool | 4 | 4 |
| Advisory Lock | session-exclusive psycopg2 pool | 4 | 4 |
| Checkpointer | psycopg3 `ConnectionPool` / `PostgresSaver(pool)` | 2 | 2 |

All four domains recorded zero unexpected acquire timeouts. The Checkpointer
observed peak stayed within both pool max and configured max plus overhead.
Telemetry saturation was proven not to consume Business capacity. A single
barrier now holds all 22 configured Business, Telemetry, Advisory Lock and
Checkpointer leases simultaneously; the observer measured 22 Stage 48
connections and four granted PostgreSQL advisory locks at that same instant.

This status authorizes capacity-aware canary preparation only. It does not
claim that a deployed environment has been observed or approved.

## Frozen pre-Stage-48 baseline

The baseline contains aggregate implementation counts only:

| Aggregate | Baseline | Final repository state |
| --- | ---: | ---: |
| Direct `psycopg2.connect` call sites under `app/services` | 43 | 2 ownership/migration boundaries |
| Store constructors that could call `_ensure_schema()` | 7 | runtime uses explicit `validate`; migration uses explicit `migrate` |
| Checkpointer `setup()` calls during runtime start | 1 | 0 |
| Explicit PostgreSQL connection domains | 0 | 4 |

`PostgresSaver.setup()` now appears only in the explicit migration module.
Runtime startup performs read-only schema validation and fails closed with
`PostgresSchemaNotReady` when migration is missing or incompatible.

## Identifier and migration evidence

- Every runtime table, index and constraint is derived through the Stage 48
  identifier registry.
- UTF-8 byte length is checked before any connection is opened.
- PostgreSQL's 63-byte truncation is never accepted as an identity strategy.
- Table names remain readable; secondary identifiers that would exceed 63
  bytes use a stable prefix-scoped SHA-256 token.
- The isolated migration dry-run connected to nothing and printed no DSN.
- The isolated `--apply` run acquired a session advisory lock, applied
  the immutable v1 migration followed by
  `stage48_runtime_schema_v2_contract`, ran Checkpointer setup, and recorded
  the latest structural checksum and transaction mode.
- A second application was idempotent.
- Nineteen isolated test tables were removed after evidence collection.
- Remaining isolated Stage 48 test tables: `0`.

Migration DDL is transaction-mode declared. Current Stage 48 DDL uses the
transactional phase; transaction-forbidden operations remain reserved for an
explicit autocommit phase under the same session advisory lock.

## Capacity evidence

The accepted artifact is
`reports/stage48-acceptance/postgres-capacity-v1.json`.

| Capacity input/result | Value |
| --- | ---: |
| PostgreSQL `max_connections` | 100 |
| Superuser reserved connections | 3 |
| External connection reserve | 10 |
| Available after reserves | 87 |
| Maximum utilization | 0.80 |
| Allowed application budget | 69 |
| Configured API role budget | 23 |
| Configured Celery role budget | 23 |
| Configured Outbox role budget | 23 |
| Configured total | 69 |
| Checkpointer configured overhead | 1 |
| Simultaneous application connections, expected / observed | 22 / 22 |
| Simultaneous granted advisory locks, expected / observed | 4 / 4 |
| Privacy violations | 0 |

Artifact result: `ELIGIBLE_FOR_CAPACITY_CANARY`.

The artifact contains aggregate counts, limits and timings only. It contains
no DSN, host, database, user, client address, backend PID, SQL text, workflow
identity, token, secret or provider payload.

## Test gates

| Gate | Result |
| --- | --- |
| Stage 48 acceptance runner | `READY_FOR_CAPACITY_AWARE_FENCING_CANARY`; all five checks PASS |
| Full Python without PostgreSQL environment | 1140 passed, 148 skipped, 1 warning |
| Full Python with local PostgreSQL | 1287 passed, 1 skipped, 1 warning |
| Existing PostgreSQL Store/lease/fencing batch | 67 passed |
| LangGraph recovery/fencing marker matrix | 28 passed |
| Stage 48 real PostgreSQL capacity tests | 4 passed |
| Focused post-review compatibility rerun | 126 passed |
| CSS build | PASS |
| Python compileall | PASS |
| Browser functional regression | 37 passed, 9 opt-in/reference tests skipped |
| `git diff --check` | PASS |

The Python warning is the existing Starlette/httpx deprecation warning. The
single full-PostgreSQL skip is the opt-in real-provider test; no mandatory
PostgreSQL Stage 48 test was skipped. No external provider was called.

## Cleanup and ownership checks

- Stage 48 pool application-name sessions after acceptance: `0`.
- Isolated Stage 48 migration/capacity tables after cleanup: `0`.
- Browser WebServer process after acceptance: `0`.
- Listener on browser test port 8011 after acceptance: `0`.
- Playwright result/report directories after cleanup: `0`.
- Committed Interview rollout default: `0`.
- Committed Review rollout default: `0`.

## Operator boundary

Do not apply the migration to a deployed prefix without an explicitly
authorized drain/maintenance window. Do not increase either rollout percentage
from repository evidence alone. A production canary must identify the exact
environment and revision, re-run migration/preflight/capacity evidence against
that environment, approve its process topology and capacity reserve, and write
a separate observation record.

Until that happens:

```text
Production observation: NOT_RUN
INTERVIEW_LANGGRAPH_ROLLOUT_PERCENT=0
REPORT_LANGGRAPH_ROLLOUT_PERCENT=0
```
