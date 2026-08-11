from __future__ import annotations


def safe_provider_metrics(provider) -> dict:
    snapshot = getattr(provider, "snapshot_metrics", None)
    if not callable(snapshot):
        return {
            "request_count": 0,
            "retry_count": 0,
            "error_counts": {},
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
        }
    raw = snapshot()
    return {
        key: raw[key]
        for key in (
            "request_count",
            "retry_count",
            "error_counts",
            "latency_p50_ms",
            "latency_p95_ms",
        )
        if key in raw
    }


__all__ = ["safe_provider_metrics"]
