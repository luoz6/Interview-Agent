---
id: fastapi_dependency_lifecycle
domain: fastapi
source_type: theory
content_kind: hard_negative
tags: [fastapi, dependency-injection, lifecycle]
title: FastAPI Dependency Lifecycle Boundaries
---

# FastAPI Dependency Lifecycle Boundaries

Dependency caching is per request by default: repeated use of the same dependency can reuse one resolved value, while disabling the cache forces another call. A yielding dependency surrounds downstream execution and is appropriate for scoped cleanup such as database sessions.

Application-wide clients should normally be created during lifespan startup and closed during shutdown, not rebuilt for every request. This evidence is useful for resource ownership questions, but it does not establish that a slow endpoint is caused by blocking I/O; that conclusion requires runtime observations.
