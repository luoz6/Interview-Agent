---
id: mysql_online_migration
domain: mysql
source_type: engineering_guide
content_kind: engineering_practice
tags: [mysql, schema, migration]
title: MySQL Online Schema Migration
---

# MySQL Online Schema Migration

Treat schema changes as compatibility rollouts. Deploy code that can read both shapes before backfilling, write compatible data during the transition, verify counts and checksums, and remove the old path only after rollback is no longer needed.

Estimate table size, lock behavior, replication lag, and extra disk before running DDL. Use an online migration mechanism when native DDL cannot meet the lock budget. Throttle or pause on lag and production latency, record progress durably, and rehearse cancellation. A migration is not complete until old indexes and compatibility code are safely removed.
