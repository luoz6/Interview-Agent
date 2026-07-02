# Redis Cache Consistency

Cache consistency in a cache-aside design usually means updating the database first and then deleting the cache.

Key gaps to watch for in interview answers:

- Ignoring race conditions between concurrent reads and writes.
- Ignoring fallback behavior when Redis is unavailable.
- Ignoring delayed cleanup or retry strategies.
