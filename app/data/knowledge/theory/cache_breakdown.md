---
id: cache_breakdown
domain: redis
source_type: theory
content_kind: failure_mode
tags: [redis, cache, hotkey]
title: Cache Breakdown
---

# Cache Breakdown

Cache breakdown usually means many concurrent requests miss the same hot key and hit the database together.

Useful mitigation patterns:

- Mutex or single-flight protection.
- Logical expiration with background rebuild.
- Rate limiting and degraded fallback.
