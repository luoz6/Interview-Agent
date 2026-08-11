from __future__ import annotations

"""Acceptance coverage for PostgreSQL capacity evidence."""

from scripts import postgres_capacity_acceptance


def test_default_mode_is_dry_run_and_does_not_read_database_configuration(
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    result = postgres_capacity_acceptance.main([])

    assert result == 0
    output = capsys.readouterr().out
    assert "mode=DRY_RUN" in output
    assert "status=NOT_RUN" in output
    assert "CAPACITY_EXECUTION_NOT_REQUESTED" in output


def test_execute_without_external_configuration_fails_before_database_access(
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    database_called = False

    def forbidden(*args, **kwargs):
        nonlocal database_called
        database_called = True
        raise AssertionError("database access must not occur")

    monkeypatch.setattr(
        postgres_capacity_acceptance,
        "approved_postgres_scope",
        forbidden,
    )

    result = postgres_capacity_acceptance.main(["--execute"])

    assert result == 1
    assert database_called is False
    output = capsys.readouterr().out
    assert "status=BLOCKED" in output
    assert "ACCEPTANCE_CONFIGURATION_INVALID" in output
