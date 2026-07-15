---
id: fastapi_request_lifecycle
domain: fastapi
source_type: theory
content_kind: mechanism
tags: [fastapi, python, async]
title: FastAPI Request Lifecycle
---

# FastAPI Request Lifecycle

FastAPI resolves dependencies, validates request data, invokes the path operation, serializes the response, and runs scheduled background callbacks around an ASGI request. Async path operations yield control only when awaiting non-blocking work; declaring a function async does not make synchronous database or HTTP clients non-blocking.

Dependency scopes matter for cleanup. Resources opened with a yielding dependency should be released even when validation or handler execution fails. Interview answers should distinguish application middleware, dependency injection, exception handling, and business logic rather than treating them as one callback.
