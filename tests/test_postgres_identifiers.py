import pytest

from app.services.config import get_runtime_table_prefix
from app.services.postgres_identifiers import (
    POSTGRES_IDENTIFIER_MAX_BYTES,
    PostgresIdentifierInvalid,
    PostgresIdentifierTooLong,
    derive_runtime_identifiers,
    runtime_identifier_suffixes,
    runtime_schema_identifier,
    validate_postgres_identifier,
    validate_runtime_table_prefix,
)


def test_default_runtime_prefix_derives_a_unique_safe_registry():
    registry = derive_runtime_identifiers("interview")

    assert registry.prefix == "interview"
    assert len(registry.names) == len(runtime_identifier_suffixes())
    assert len(registry.names) == len(set(registry.names))
    assert registry.longest_byte_length <= POSTGRES_IDENTIFIER_MAX_BYTES
    assert "interview_runtime_event_receipts_status_available_idx" in registry.names


@pytest.mark.parametrize("name", ["a", "_", "a" * 63, "A_9"])
def test_identifier_accepts_safe_names_up_to_63_utf8_bytes(name):
    assert validate_postgres_identifier(name) == name


def test_identifier_rejects_64_ascii_bytes():
    with pytest.raises(PostgresIdentifierTooLong):
        validate_postgres_identifier("a" * 64)


def test_identifier_checks_utf8_bytes_before_character_safety():
    assert len("测" * 22) < POSTGRES_IDENTIFIER_MAX_BYTES
    with pytest.raises(PostgresIdentifierTooLong):
        validate_postgres_identifier("测" * 22)


@pytest.mark.parametrize("prefix", ["", " ", "bad-name", "has space", "9starts"])
def test_runtime_prefix_rejects_empty_whitespace_or_unsafe_values(prefix):
    with pytest.raises(PostgresIdentifierInvalid):
        validate_runtime_table_prefix(prefix)


def test_runtime_prefix_rejects_longest_table_identifier_before_truncation():
    accepted = "p" * 27
    rejected = accepted + "x"

    assert validate_runtime_table_prefix(accepted) == accepted
    with pytest.raises(PostgresIdentifierTooLong):
        validate_runtime_table_prefix(rejected)


def test_long_secondary_identifier_is_stably_shortened_without_truncation():
    prefix = "test_stage48_123456789abc"
    first = runtime_schema_identifier(
        prefix, "runtime_event_receipts_status_available_idx"
    )
    second = runtime_schema_identifier(
        prefix, "runtime_event_receipts_status_available_idx"
    )

    assert first == second
    assert first.startswith(prefix + "_x_")
    assert len(first.encode("utf-8")) <= 63


def test_two_prefixes_that_postgres_would_truncate_are_both_rejected():
    common = "p" * 63
    for prefix in (common + "a", common + "b"):
        with pytest.raises(PostgresIdentifierTooLong):
            validate_runtime_table_prefix(prefix)


def test_config_validates_runtime_prefix(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "bad-prefix")

    with pytest.raises(PostgresIdentifierInvalid):
        get_runtime_table_prefix()


def test_invalid_prefix_is_rejected_without_opening_a_connection(monkeypatch):
    import psycopg2

    calls = 0

    def forbidden_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("connection must not be opened")

    monkeypatch.setattr(psycopg2, "connect", forbidden_connect)
    monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "unsafe-prefix")

    with pytest.raises(PostgresIdentifierInvalid):
        get_runtime_table_prefix()
    assert calls == 0
