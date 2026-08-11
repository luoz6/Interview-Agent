from __future__ import annotations

import math

from app.runtime.config import load_provider_credentials


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


def build_embedding_provider(settings=None):
    from app.runtime.config.compatibility import get_embedding_settings

    resolved = settings or get_embedding_settings()
    if resolved.provider_name == "disabled":
        return DisabledEmbeddingProvider(
            model_name=resolved.model_name,
            dimension=resolved.dimension,
        )
    api_key = load_provider_credentials().siliconflow_api_key
    if not api_key:
        raise EmbeddingConfigurationError("SILICONFLOW_API_KEY is not configured")
    from app.services.siliconflow_embeddings import SiliconFlowEmbeddingProvider

    return SiliconFlowEmbeddingProvider(
        api_key=api_key,
        api_base=resolved.api_base,
        model_name=resolved.model_name,
        model_revision=resolved.model_revision,
        dimension=resolved.dimension,
        batch_size=resolved.batch_size,
        connect_timeout_seconds=resolved.connect_timeout_seconds,
        read_timeout_seconds=resolved.read_timeout_seconds,
    )
