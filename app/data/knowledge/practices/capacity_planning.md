---
id: capacity_planning
domain: system-design
source_type: engineering_guide
content_kind: engineering_practice
tags: [system-design, capacity, performance]
title: Service Capacity Planning
---

# Service Capacity Planning

Start with peak request rate, payload size, read/write mix, retention, and latency objectives. Convert them into storage growth, network throughput, concurrent work, and downstream operations. State assumptions and include headroom for bursts, failures, maintenance, and forecast error.

Validate estimates with load tests that preserve realistic bottlenecks. Identify the first saturated resource and establish scaling and shedding thresholds before launch. Capacity plans should include database connections, cache memory, queue drain time, and third-party quotas, not only application CPU. Revisit the model using production measurements.
