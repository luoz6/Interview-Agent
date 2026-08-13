import json
from pathlib import Path

import pytest

from scripts.build_t62_migration_acceptance import (
    DEFAULT_OUTPUT,
    INVARIANTS,
    REQUIREMENTS,
    build_acceptance,
    validate_acceptance,
)
from scripts.migrate_legacy_reports import main as migrate_legacy_main
from scripts.run_t62_migration_acceptance import _preflight


ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_t62_acceptance_matches_deterministic_builder():
    checked_in = json.loads((ROOT / DEFAULT_OUTPUT).read_text(encoding="utf-8"))
    generated = build_acceptance()

    assert checked_in == generated
    validate_acceptance(checked_in, root=ROOT)


def test_t62_acceptance_maps_ten_work_items_and_five_invariants():
    payload = build_acceptance()

    assert payload["requirement_count"] == len(REQUIREMENTS) == 10
    assert payload["acceptance_invariant_count"] == len(INVARIANTS) == 5
    assert payload["backup_tools_required"] == ["pg_dump", "pg_restore"]
    assert payload["skip_policy"] == "forbidden"
    assert payload["provider_calls_expected"] == 0


def test_t62_legacy_migration_cli_is_dry_run_by_default(monkeypatch, capsys):
    monkeypatch.setenv("POSTGRES_DSN", "must-not-be-used-or-printed")
    monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "interview")

    assert migrate_legacy_main([]) == 0
    output = capsys.readouterr().out
    assert "mode=DRY_RUN" in output
    assert "batch_limit=100" in output
    assert "must-not-be-used-or-printed" not in output


def test_t62_legacy_migration_cli_reports_effective_lazy_limit(monkeypatch, capsys):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "interview")

    assert migrate_legacy_main(["--session-id", "  synthetic-session  "]) == 0
    output = capsys.readouterr().out
    assert "migration_mode=LAZY" in output
    assert "batch_limit=1" in output
    assert "synthetic-session" not in output


def test_t62_legacy_migration_cli_rejects_blank_session_id(monkeypatch):
    monkeypatch.setenv("INTERVIEW_RUNTIME_TABLE_PREFIX", "interview")

    with pytest.raises(SystemExit) as raised:
        migrate_legacy_main(["--session-id", "   "])
    assert raised.value.code == 2


def test_t62_runner_preflight_fails_closed_without_postgres(monkeypatch):
    monkeypatch.delenv("POSTGRES_DSN", raising=False)

    with pytest.raises(RuntimeError, match="POSTGRES_DSN is required"):
        _preflight("unused-container")


def test_t62_rollback_runbook_is_complete_and_non_destructive():
    text = (
        ROOT / "docs/interview-quality-v1-t62-rollback-runbook.md"
    ).read_text(encoding="utf-8")

    for required in (
        "pg_dump",
        "pg_restore",
        "REPORT_ARTIFACT_READ_MODE=legacy",
        "REPORT_ARTIFACT_READ_MODE=artifact_first",
        "python -m scripts.postgres_runtime_migrate --apply",
        "python -m scripts.migrate_legacy_reports --apply",
        "active pointer",
        "Artifact hash",
        "STOP",
        "do not drop",
    ):
        assert required in text
