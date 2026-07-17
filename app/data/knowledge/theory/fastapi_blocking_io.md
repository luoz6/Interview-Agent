---
id: fastapi_blocking_io
domain: fastapi
source_type: theory
content_kind: failure_mode
tags: [fastapi, python, latency]
title: FastAPI Blocking I/O Failure Mode
---

# FastAPI Blocking I/O Failure Mode

Calling a blocking SDK from an async endpoint can occupy the event-loop thread and delay unrelated requests. The symptom is often rising tail latency with moderate CPU, while adding async syntax or more coroutines makes no improvement.

Confirm the cause with request traces and event-loop lag, then use an async client or move bounded blocking work to a thread pool. CPU-heavy work belongs in a separate process or task worker. Apply concurrency limits and timeout budgets so moving the call does not simply create an unbounded queue elsewhere.
