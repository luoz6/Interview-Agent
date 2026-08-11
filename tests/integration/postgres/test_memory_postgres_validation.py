"""PostgreSQL integration coverage."""

from __future__ import annotations
from contextlib import contextmanager

import pytest

from scripts import memory_postgres_validation as validation


def test_validation_prefix_is_isolated_and_bounded():
    prefix = validation.make_validation_prefix()

    validation.assert_safe_prefix(prefix)
    assert len(prefix) < 63
    with pytest.raises(ValueError):
        validation.assert_safe_prefix("interview")


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
    runtime_table_prefix,
):
    result = validation.run_validation(
        dsn=postgres_dsn,
        table_prefix=runtime_table_prefix,
    )

    assert result.table_prefix == runtime_table_prefix
    assert result.relation_count > 0
    assert "memory_session_policy_v1" in result.required_migration_ids
    assert "question_memory_index_v1" in result.required_migration_ids
    assert "session_deletion_v1" in result.required_migration_ids
    assert "session_deletion_tombstone_v1" in result.required_migration_ids
    assert "memory_metric_bucket_v1" in result.required_migration_ids
    assert "principal_memory_v1" in result.required_migration_ids
