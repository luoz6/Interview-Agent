# Knowledge RAG V2 RocketMQ V4 Legacy baseline

This directory freezes the privacy-safe Legacy retrieval evidence produced on
2026-08-13 for the active `memory-p1-zh-v4` corpus. It is historical baseline
evidence only; it is not an independent Eval V3 dataset, a Hybrid result, or a
production-readiness approval.

The JSON artifacts intentionally contain case identifiers, retrieved chunk
identifiers, scores, timing, binding/replay identifiers, and aggregate metrics.
They do not contain query text, knowledge body, credentials, database DSNs,
resume/JD content, answers, authorization headers, or source URLs.

The byte-level SHA-256 values in `legacy-rmqv4-baseline-receipt.json` bind the
exact repository files. Canonical JSON SHA-256 values are also recorded so the
evidence identity remains independently checkable across line-ending changes.

Runtime rollout remains fail-closed at the repository defaults:

- retrieval engine: `legacy`
- Hybrid rollout: `0%`
- knowledge Shadow: disabled

The 12-case pilot and 18-case Memory P1 set overlap in purpose and must not be
concatenated, relabeled, or cited as the independently annotated Eval V3 set.
