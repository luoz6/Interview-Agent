import math

import pytest

from app.ports.runtime import EmbeddingProvider
from app.services.embedding_providers import (
    DisabledEmbeddingProvider,
    EmbeddingConfigurationError,
    validate_embedding_batch,
)


def test_disabled_provider_satisfies_port_and_fails_without_network():
    provider = DisabledEmbeddingProvider(model_name="BAAI/bge-m3", dimension=3)

    assert isinstance(provider, EmbeddingProvider)
    with pytest.raises(EmbeddingConfigurationError, match="disabled"):
        provider.embed_query("redis consistency")
    with pytest.raises(EmbeddingConfigurationError, match="disabled"):
        provider.embed_documents(["one"])


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[0.1, 0.2]],
        [[0.1, float("nan"), 0.3]],
        [[0.1, float("inf"), 0.3]],
    ],
)
def test_embedding_batch_validation_rejects_count_dimension_and_nonfinite(vectors):
    with pytest.raises(ValueError):
        validate_embedding_batch(vectors, expected_count=1, dimension=3)


def test_embedding_batch_validation_returns_plain_finite_floats():
    result = validate_embedding_batch([[1, 2.5, 3]], expected_count=1, dimension=3)

    assert result == [[1.0, 2.5, 3.0]]
    assert all(math.isfinite(value) for value in result[0])
