import math

import pytest

from app.ports.runtime import EmbeddingProvider
from app.services.config import EmbeddingSettings
import app.services.embedding_providers as embedding_providers
from app.services.embedding_providers import (
    build_embedding_provider,
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


def make_settings(provider_name):
    return EmbeddingSettings(
        provider_name=provider_name,
        api_base="https://unit.test/v1",
        model_name="BAAI/bge-m3",
        model_revision="test-revision",
        dimension=3,
        batch_size=2,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
    )


def test_disabled_factory_does_not_read_siliconflow_key(monkeypatch):
    def fail_on_env_read(name, default=None):
        raise AssertionError(f"unexpected environment read: {name}")

    monkeypatch.setattr(embedding_providers.os, "getenv", fail_on_env_read)

    provider = build_embedding_provider(make_settings("disabled"))

    assert isinstance(provider, DisabledEmbeddingProvider)


def test_siliconflow_factory_requires_key_without_exposing_environment(monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-appear")

    with pytest.raises(EmbeddingConfigurationError) as exc:
        build_embedding_provider(make_settings("siliconflow"))

    assert "SILICONFLOW_API_KEY" in str(exc.value)
    assert "must-not-appear" not in str(exc.value)
