---
id: kafka_rebalancing
domain: kafka
source_type: theory
content_kind: hard_negative
tags: [kafka, consumer-group, rebalancing]
title: Kafka Consumer Group Rebalancing
---

# Kafka Consumer Group Rebalancing

Within a consumer group, each partition is assigned to at most one member at a time. Membership changes or subscription changes trigger assignment updates. Long processing that prevents timely polling can make a healthy worker appear failed and cause repeated ownership movement.

Cooperative assignment can reduce stop-the-world movement, but consumers still need correct revoke and assign handling. Finish or cancel work deliberately, commit offsets according to processing semantics, and keep side effects idempotent. Rebalancing explains assignment churn; it is not the same problem as broker partition leadership or producer acknowledgements.
