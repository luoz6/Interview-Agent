---
id: mysql_isolation
domain: mysql
source_type: theory
content_kind: hard_negative
tags: [mysql, transaction, isolation]
title: MySQL Transaction Isolation
---

# MySQL Transaction Isolation

Isolation controls which concurrent changes a transaction can observe. Under InnoDB repeatable read, consistent reads use a snapshot while locking reads and writes interact with current records and may acquire next-key locks. Read committed creates a newer snapshot for each statement and changes the anomaly and locking tradeoffs.

An answer should connect the chosen level to a concrete invariant and retry strategy. Isolation alone does not guarantee application-level uniqueness or eliminate lost updates; constraints, conditional writes, or explicit locks may still be required. This topic is adjacent to indexing but should not be retrieved for a pure covering-index question.
