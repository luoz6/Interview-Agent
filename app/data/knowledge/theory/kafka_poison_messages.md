---
id: kafka_poison_messages
domain: kafka
source_type: theory
content_kind: failure_mode
tags: [kafka, consumer, retry]
title: Kafka Poison Messages
---

# Kafka Poison Messages

A poison message repeatedly fails deterministic processing and can block a partition when the consumer retries it forever. Immediate retries are useful for brief dependency failures but amplify load when the payload is invalid or the code cannot handle its schema.

Classify failures, bound retries with backoff, and move terminal records to a dead-letter topic with the original key, headers, offset, and error category. Preserve observability and provide a controlled replay path after remediation. Commit offsets only according to the chosen loss and duplication policy; skipping silently creates an unaudited data gap.
