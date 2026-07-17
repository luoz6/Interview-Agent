---
id: kafka_operations
domain: kafka
source_type: engineering_guide
content_kind: engineering_practice
tags: [kafka, observability, capacity]
title: Kafka Consumer Operations
---

# Kafka Consumer Operations

Operate consumers with lag by partition, processing latency, retry volume, rebalance count, and dead-letter rate. Lag is a backlog measure, not a latency guarantee, so relate it to input rate and estimated drain time. Alert on sustained trends instead of isolated offset spikes.

Set batch size, poll interval, fetch limits, and concurrency from measured processing cost. Scale only up to the useful partition count. During rollout, watch assignment churn and duplicate side effects. A safe runbook covers pausing intake, draining, replaying a bounded offset range, and verifying downstream idempotency.
