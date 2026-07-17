---
id: redis_distributed_lock
domain: redis
source_type: theory
content_kind: hard_negative
tags: [redis, locking, concurrency]
title: Redis Distributed Lock Safety
---

# Redis Distributed Lock Safety

A Redis lock is a coordination tool, not a substitute for an idempotent business operation. Acquisition should use an atomic set with an expiry and a unique owner token. Release must compare the token and delete in one atomic script so one client cannot remove another client's renewed lock.

The difficult cases are pauses longer than the lease, clock and network uncertainty, and work that continues after ownership is lost. For operations requiring strict ordering, add fencing tokens checked by the protected resource. This topic is related to cache races but should not be used as evidence for cache-aside consistency.
