from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts import memory_postgres_validation as validation


class FingerprintCursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement):
        assert "current_database" in statement

    def fetchone(self):
        return self.row


class FingerprintConnection:
    def __init__(self, row):
        self.row = row
        self.closed = False

    def cursor(self):
        return FingerprintCursor(self.row)

    def close(self):
        self.closed = True


def test_validation_prefix_is_isolated_and_bounded():
    prefix = validation.make_validation_prefix()

    validation.assert_safe_prefix(prefix)
    assert len(prefix) < 63
    with pytest.raises(ValueError):
        validation.assert_safe_prefix("interview")


def test_database_fingerprint_is_stable_and_redacts_dsn():
    connection = FingerprintConnection(("interview", 160004))

    result = validation.database_fingerprint(
        "secret-dsn",
        connect=lambda dsn, **kwargs: connection,
    )

    assert len(result.digest) == 16
    assert "secret" not in result.digest
    assert result.database_name == "interview"
    assert connection.closed is True


def test_database_fingerprint_rejects_production_like_database():
    connection = FingerprintConnection(("candidate_production", 160004))

    with pytest.raises(RuntimeError, match="production-like"):
        validation.database_fingerprint(
            "secret-dsn",
            connect=lambda dsn, **kwargs: connection,
        )
    assert connection.closed is True


def test_cli_defaults_to_dry_run_without_connecting(monkeypatch, capsys):
    monkeypatch.setattr(
        validation,
        "run_validation",
        lambda **kwargs: pytest.fail("dry-run must not connect"),
    )

    assert validation.main([]) == 0
    output = capsys.readouterr().out
    assert "mode=DRY_RUN" in output
    assert "dsn=REDACTED" in output
    assert "postgresql://" not in output


@pytest.mark.pg_runtime
def test_isolated_live_postgres_validation_executes_and_cleans(
    postgres_dsn,
    monkeypatch,
):
    prefix = validation.make_validation_prefix()
    monkeypatch.setattr(
        validation,
        "database_fingerprint",
        lambda dsn: SimpleNamespace(digest="a" * 16),
    )

    result = validation.run_validation(
        dsn=postgres_dsn,
        table_prefix=prefix,
    )

    assert result.table_prefix == prefix
    assert result.relation_count > 0
    assert result.cleaned is True
    assert "memory_session_policy_v1" in result.required_migration_ids
    assert "question_memory_index_v1" in result.required_migration_ids
    assert "session_deletion_v1" in result.required_migration_ids
    assert "session_deletion_tombstone_v1" in result.required_migration_ids
    assert "memory_metric_bucket_v1" in result.required_migration_ids
    assert "principal_memory_v1" in result.required_migration_ids
