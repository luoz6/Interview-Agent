---
id: service_scaling
domain: system-design
source_type: theory
content_kind: mechanism
tags: [system-design, scaling, architecture]
title: Service Scaling
---

# Service Scaling

Key interview checkpoints:

- Stateless service instances scale horizontally more easily.
- Shared bottlenecks usually move to cache, database, queue, or downstream APIs.
- Rate limiting, circuit breaking, and graceful degradation should be planned early.
