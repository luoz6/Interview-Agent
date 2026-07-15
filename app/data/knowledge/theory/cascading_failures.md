---
id: cascading_failures
domain: system-design
source_type: theory
content_kind: failure_mode
tags: [system-design, resilience, overload]
title: Cascading Service Failures
---

# Cascading Service Failures

A slow dependency holds callers' threads, connections, or coroutine slots. Retries add more traffic, queues grow, timeouts synchronize, and a local slowdown becomes system-wide saturation. Average latency can look acceptable while tail latency and resource occupancy are already unstable.

Contain the failure with end-to-end timeout budgets, bounded concurrency, circuit breaking, load shedding, and retry budgets with jitter. Isolate critical and optional workloads so one exhausted pool does not consume every request path. Validate recovery behavior because a sudden retry wave can overload a dependency just as it returns.
