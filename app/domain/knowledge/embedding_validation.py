from __future__ import annotations

import math


class EmbeddingConfigurationError(RuntimeError):
    pass


class EmbeddingProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(f"embedding provider failed: {code}")
        self.code = code
        self.retryable = retryable


def validate_embedding_batch(
    vectors,
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise ValueError("embedding response count mismatch")
    normalized: list[list[float]] = []
    for vector in vectors:
        values = [float(value) for value in vector]
        if len(values) != dimension:
            raise ValueError("embedding response dimension mismatch")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding response contains non-finite values")
        normalized.append(values)
    return normalized
