---
id: mysql_deadlocks
domain: mysql
source_type: theory
content_kind: failure_mode
tags: [mysql, transaction, locking]
title: MySQL Deadlocks
---

# MySQL Deadlocks

A deadlock occurs when transactions hold locks that the others need and no participant can progress. InnoDB detects the cycle and aborts one transaction, so application code must treat deadlock errors as retryable only when the operation is idempotent and the retry count is bounded.

Diagnosis starts with the deadlock report and the exact statements and indexes involved. Reduce risk by accessing rows in a consistent order, keeping transactions short, indexing predicates to avoid broad lock ranges, and avoiding network calls inside transactions. Deadlocks differ from slow queries and require lock evidence, not only EXPLAIN output.
