# Cache Breakdown

Cache breakdown usually means many concurrent requests miss the same hot key and hit the database together.

Useful mitigation patterns:

- Mutex or single-flight protection.
- Logical expiration with background rebuild.
- Rate limiting and degraded fallback.
