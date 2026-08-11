from __future__ import annotations

import pytest

from app.runtime.config.compatibility import get_embedding_settings


def test_embedding_defaults_are_disabled_and_never_select_local_model(monkeypatch):
    for name in (
        "EMBEDDING_PROVIDER",
        "EMBEDDING_API_BASE",
        "EMBEDDING_MODEL_NAME",
        "EMBEDDING_MODEL_REVISION",
        "EMBEDDING_DIMENSION",
        "EMBEDDING_BATCH_SIZE",
        "EMBEDDING_CONNECT_TIMEOUT_SECONDS",
        "EMBEDDING_READ_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = get_embedding_settings()

    assert settings.provider_name == "disabled"
    assert settings.model_name == "BAAI/bge-m3"
    assert settings.dimension == 1024
    assert settings.batch_size == 32
    assert "api_key" not in repr(settings).lower()


@pytest.mark.parametrize("value", ["local", "sentence-transformers", "unknown"])
def test_embedding_provider_rejects_unsupported_values(monkeypatch, value):
    monkeypatch.setenv("EMBEDDING_PROVIDER", value)

    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        get_embedding_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EMBEDDING_DIMENSION", "0"),
        ("EMBEDDING_BATCH_SIZE", "0"),
        ("EMBEDDING_CONNECT_TIMEOUT_SECONDS", "0"),
        ("EMBEDDING_READ_TIMEOUT_SECONDS", "-1"),
    ],
)
def test_embedding_numeric_settings_must_be_positive(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        get_embedding_settings()


def test_pgvector_derived_table_names_are_valid_and_bounded():
    from app.runtime.config.compatibility import derive_pgvector_table_names

    assert derive_pgvector_table_names("knowledge_chunks") == (
        "knowledge_chunks_versions",
        "knowledge_chunks_releases",
    )
    versions, releases = derive_pgvector_table_names("x" * 54)
    assert len(versions.encode("ascii")) == 63
    assert len(releases.encode("ascii")) == 63


@pytest.mark.parametrize(
    "base",
    ["", "9invalid", "contains-dash", "knowledge_chunks_非法", "x" * 55],
)
def test_pgvector_table_rejects_invalid_or_overlong_derived_names(base):
    from app.runtime.config.compatibility import derive_pgvector_table_names

    with pytest.raises(ValueError, match="PGVECTOR_TABLE"):
        derive_pgvector_table_names(base)


def test_get_pgvector_table_validates_derived_names(monkeypatch):
    from app.runtime.config.compatibility import get_pgvector_table

    monkeypatch.setenv("PGVECTOR_TABLE", "x" * 55)
    with pytest.raises(ValueError, match="PGVECTOR_TABLE"):
        get_pgvector_table()
