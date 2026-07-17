---
id: queue_backpressure
domain: system-design
source_type: theory
content_kind: hard_negative
tags: [system-design, queue, backpressure]
title: Queue Backpressure
---

# Queue Backpressure

A queue absorbs short bursts but does not create processing capacity. When arrival rate remains above service rate, backlog and completion latency grow without bound. Unlimited buffering delays failure and increases recovery time, while aggressive producer retries can make the imbalance worse.

Define maximum queue age and depth, admission control, producer throttling, and a policy for expired work. Scale consumers from measured service time and downstream limits, then expose drain-time estimates. Backpressure is related to Kafka lag, but the design applies to task queues and in-memory buffers even when ordering and replay semantics are different.
