from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.memory_config import load_effective_memory_config
from scripts import memory_shadow_staging_preflight as staging


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "memory-shadow-staging-runbook.md"
ACCEPTANCE = ROOT / "docs" / "memory-shadow-staging-acceptance.md"


def _declaration(**overrides):
    values = {
        "environment_category": "isolated_staging",
        "validated_rc_revision": "a982b1f",
        "observation_profile": "B",
        "observation_hours": 24,
        "data_category": "synthetic",
        "operator_role": "memory-shadow-operator",
        "rollback_owner_role": "memory-shadow-rollback-owner",
        "retention_days": 7,
        "backup_restore_scope": "isolated_copy",
        "isolation_level": "strict_prefix",
        "co_resident_isolated_staging": True,
        "dedicated_connection_scope": True,
        "dedicated_worker_scope": True,
        "dedicated_owner_scope": True,
        "deterministic_path_verified": True,
        "allow_real_provider": False,
    }
    values.update(overrides)
    return staging.StagingDeclaration(**values)


def _rc_evidence():
    return {
        "validated_rc_revision": "a982b1f",
        "release_candidate": {
            "passed": True,
            "clean_detached_worktree": True,
            "shadow_modes_changed": False,
        },
        "full_python": {"passed": True},
        "pg_runtime": {"passed": True, "executed": 43},
        "frontend_build": {"passed": True},
        "full_browser": {"passed": True, "scope": "full"},
        "cleanup": {
            "passed": True,
            "test_listeners": 0,
            "isolated_test_relation_residue": 0,
        },
        "production_observation": "NOT_RUN",
    }


def test_safe_prefix_is_strict_and_bounded():
    prefix = staging.make_staging_prefix()

    staging.assert_safe_staging_prefix(prefix)
    assert len(prefix) < 63
    for unsafe in ("interview", "memory_stage", "test_memory_validation_bad"):
        with pytest.raises(ValueError):
            staging.assert_safe_staging_prefix(unsafe)


def test_static_preflight_passes_only_with_disabled_memory_modes():
    config = load_effective_memory_config({})

    result = staging.evaluate_preflight(
        declaration=_declaration(),
        rc_evidence=_rc_evidence(),
        config=config,
        consume_rejected=True,
        database_fingerprint_matches=True,
        prefix_valid=True,
        migration_validated=True,
        durable_metrics_validated=True,
        rollback_verified=True,
        cleanup_residue=0,
    )

    assert result["passed"] is True
    assert result["gate_codes"] == []
    assert result["all_memory_shadows_disabled"] is True
    assert result["configuration_changed"] is False
    assert result["migration_scope"] == "isolated"


def test_preflight_fails_closed_for_co_resident_scope_or_unsafe_config():
    config = load_effective_memory_config(
        {
            "MEMORY_BUDGET_MODE": "shadow",
            "MEMORY_BUDGET_SHADOW_ENABLED": "true",
        }
    )

    result = staging.evaluate_preflight(
        declaration=_declaration(
            dedicated_worker_scope=False,
            allow_real_provider=True,
        ),
        rc_evidence=_rc_evidence(),
        config=config,
        consume_rejected=False,
        database_fingerprint_matches=False,
        prefix_valid=False,
        migration_validated=False,
        durable_metrics_validated=False,
        rollback_verified=False,
        cleanup_residue=2,
    )

    assert result["passed"] is False
    assert result["gate_codes"] == sorted(result["gate_codes"])
    for code in (
        "REAL_PROVIDER_NOT_AUTHORIZED",
        "CO_RESIDENT_WORKER_SCOPE_NOT_ISOLATED",
        "BUDGET_MODE_NOT_DISABLED",
        "CONSUME_REJECTION_NOT_PROVEN",
        "DATABASE_FINGERPRINT_MISMATCH",
        "STAGING_PREFIX_INVALID",
        "MIGRATION_VALIDATION_FAILED",
        "DURABLE_METRICS_VALIDATION_FAILED",
        "ROLLBACK_DRILL_FAILED",
        "CLEANUP_RESIDUE_NONZERO",
    ):
        assert code in result["gate_codes"]
    assert "READY" not in json.dumps(result)


def test_profile_a_requires_a_seven_day_window_but_profile_b_does_not():
    profile_a = staging.validate_declaration(
        _declaration(observation_profile="A", observation_hours=24)
    )
    profile_b = staging.validate_declaration(
        _declaration(observation_profile="B", observation_hours=24)
    )

    assert "PROFILE_A_WINDOW_TOO_SHORT" in profile_a
    assert "PROFILE_A_WINDOW_TOO_SHORT" not in profile_b


def test_consume_rejection_is_checked_against_the_real_loader():
    assert staging.verify_consume_rejected({}) is True


def test_rc_evidence_must_match_the_declared_validated_revision():
    failures = staging.validate_rc_evidence(
        _declaration(validated_rc_revision="deadbee"),
        _rc_evidence(),
    )

    assert "RC_REVISION_MISMATCH" in failures


def test_aggregate_result_excludes_sensitive_and_subject_fields():
    config = load_effective_memory_config({})
    result = staging.evaluate_preflight(
        declaration=_declaration(),
        rc_evidence=_rc_evidence(),
        config=config,
        consume_rejected=True,
        database_fingerprint_matches=True,
        prefix_valid=True,
        migration_validated=True,
        durable_metrics_validated=True,
        rollback_verified=True,
        cleanup_residue=0,
    )

    rendered = json.dumps(result, sort_keys=True)
    for forbidden in (
        "postgresql://",
        "session_id",
        "principal_id",
        "fact_id",
        "prompt",
        "answer",
        "source_excerpt",
        "table_prefix",
    ):
        assert forbidden not in rendered.casefold()


def test_cli_dry_run_never_connects(monkeypatch, tmp_path, capsys):
    evidence = tmp_path / "rc.json"
    evidence.write_text(json.dumps(_rc_evidence()), encoding="utf-8")
    monkeypatch.setattr(
        staging,
        "run_live_preflight",
        lambda **kwargs: pytest.fail("dry-run must not connect"),
    )

    code = staging.main(
        [
            "--rc-evidence",
            str(evidence),
            "--validated-rc-revision",
            "a982b1f",
            "--observation-profile",
            "B",
            "--observation-hours",
            "24",
            "--co-resident-isolated-staging",
            "--dedicated-connection-scope",
            "--dedicated-worker-scope",
            "--dedicated-owner-scope",
            "--deterministic-path-verified",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "DRY_RUN"
    assert payload["configuration_changed"] is False
    assert payload["all_memory_shadows_disabled"] is True


def test_cli_can_inspect_only_an_irreversible_database_fingerprint(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("POSTGRES_DSN", "secret-dsn")
    monkeypatch.setattr(
        staging,
        "database_fingerprint",
        lambda dsn: SimpleNamespace(digest="0123456789abcdef"),
    )

    assert staging.main(["--inspect-database-fingerprint"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["database_fingerprint"] == "0123456789abcdef"
    assert payload["dsn_redacted"] is True
    assert "secret" not in json.dumps(payload)


def test_runbook_is_an_executable_how_to_and_keeps_shadow_disabled():
    text = RUNBOOK.read_text(encoding="utf-8")

    for required in (
        "--inspect-database-fingerprint",
        "--expected-database-fingerprint",
        "co_resident_isolated_staging=true",
        "cleanup_residue=0",
        "STAGING_PREFLIGHT=PASS",
        "MIGRATION_SCOPE=ISOLATED",
        "ROLLBACK_DRILL=PASS",
        "ALL_MEMORY_SHADOWS=DISABLED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ):
        assert required in text
    assert "postgresql://" not in text
    assert "PASS_FOR_PRODUCTION" not in text


def test_acceptance_binds_the_clean_preflight_revision_without_overclaiming():
    text = ACCEPTANCE.read_text(encoding="utf-8")

    for required in (
        "a982b1f",
        "5280c9d",
        "42 passed",
        "cleanup_residue=0",
        "STAGING_PREFLIGHT=PASS",
        "MIGRATION_SCOPE=ISOLATED",
        "ROLLBACK_DRILL=PASS",
        "ALL_MEMORY_SHADOWS=DISABLED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ):
        assert required in text
    assert "postgresql://" not in text
    assert "PASS_FOR_PRODUCTION" not in text
    assert "Budget Shadow is enabled or accepted" in text


@pytest.mark.pg_runtime
def test_live_staging_preflight_migrates_metrics_and_cleans(postgres_dsn):
    fingerprint = staging.database_fingerprint(postgres_dsn).digest
    prefix = staging.make_staging_prefix()

    result = staging.run_live_preflight(
        declaration=_declaration(),
        rc_evidence=_rc_evidence(),
        environ={},
        dsn=postgres_dsn,
        table_prefix=prefix,
        expected_database_fingerprint=fingerprint,
    )

    assert result["passed"] is True
    assert result["migration_validated"] is True
    assert result["durable_metrics_validated"] is True
    assert result["rollback_verified"] is True
    assert result["cleanup_residue"] == 0
    assert staging.count_isolated_relations(postgres_dsn, prefix) == 0
