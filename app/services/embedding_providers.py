from __future__ import annotations

import math


class EmbeddingConfigurationError(RuntimeError):
    pass


class EmbeddingProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(f"embedding provider failed: {code}")
        self.code = code
        self.retryable = retryable


class DisabledEmbeddingProvider:
    provider_name = "disabled"
    model_revision = "disabled"

    def __init__(self, *, model_name: str, dimension: int) -> None:
        self.model_name = model_name
        self.dimension = dimension

    def embed_query(self, text: str) -> list[float]:
        raise EmbeddingConfigurationError("embedding provider is disabled")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingConfigurationError("embedding provider is disabled")


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
