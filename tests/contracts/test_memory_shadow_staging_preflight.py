from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from contracts.evidence import AtomicEvidenceWriter, EvidenceIssuer, HmacReceiptSigner
from contracts.policies import OperationalRcEvidencePolicy

from app.runtime.config.memory import load_effective_memory_config
from app.ports.postgres_scope import PostgresTargetMismatch
from scripts import memory_shadow_staging_preflight as staging
from scripts.postgres_acceptance_support import AcceptanceConfigurationError
from tests.memory_shadow_staging_fixtures import (
    staging_declaration as _declaration,
    staging_rc_evidence as _rc_evidence,
)
from tests.operational_shadow_fixtures import rc_payload


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


@pytest.mark.parametrize(
    ("section", "field", "value", "expected_code"),
    [
        (
            "release_candidate",
            "passed",
            "false",
            "RC_REPRODUCIBILITY_NOT_PROVEN",
        ),
        (
            "release_candidate",
            "clean_detached_worktree",
            1,
            "RC_CLEAN_CHECKOUT_NOT_PROVEN",
        ),
        (
            "release_candidate",
            "shadow_modes_changed",
            0,
            "RC_VALIDATION_CHANGED_SHADOW_MODES",
        ),
        ("full_python", "passed", "true", "RC_FULL_PYTHON_NOT_GREEN"),
        ("pg_runtime", "executed", True, "RC_POSTGRES_TESTS_NOT_EXECUTED"),
        ("cleanup", "test_listeners", "0", "RC_TEST_LISTENER_RESIDUE"),
        (
            "cleanup",
            "isolated_test_relation_residue",
            0.0,
            "RC_POSTGRES_RELATION_RESIDUE",
        ),
    ],
)
def test_rc_evidence_rejects_coerced_boolean_and_integer_values(
    section,
    field,
    value,
    expected_code,
):
    evidence = _rc_evidence()
    evidence[section] = dict(evidence[section])
    evidence[section][field] = value

    failures = staging.validate_rc_evidence(_declaration(), evidence)

    assert expected_code in failures


def test_live_preflight_static_failure_never_touches_postgres(monkeypatch):
    evidence = _rc_evidence()
    evidence["release_candidate"] = dict(evidence["release_candidate"])
    evidence["release_candidate"]["passed"] = "false"
    for name in ("run_validation", "_validate_durable_metrics"):
        monkeypatch.setattr(
            staging,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(
                f"static failure must not call {_name}"
            ),
        )

    @contextmanager
    def forbidden_scope(**_kwargs):
        pytest.fail("static failure must not create an OwnedPostgresScope")
        yield

    result = staging.run_live_preflight(
        declaration=_declaration(),
        rc_evidence=evidence,
        environ={},
        dsn="secret-dsn",
        table_prefix=staging.make_staging_prefix(),
        scope_context_factory=forbidden_scope,
    )

    assert result["passed"] is False
    assert result["live_validation_executed"] is False
    assert "RC_REPRODUCIBILITY_NOT_PROVEN" in result["gate_codes"]
    assert "LIVE_VALIDATION_NOT_RUN" in result["gate_codes"]


def test_live_preflight_requires_target_match_before_migration(
    monkeypatch,
):
    for name in ("run_validation", "_validate_durable_metrics"):
        monkeypatch.setattr(
            staging,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(
                f"target mismatch must not call {_name}"
            ),
        )

    @contextmanager
    def mismatched_scope(**_kwargs):
        raise PostgresTargetMismatch("target mismatch")
        yield

    result = staging.run_live_preflight(
        declaration=_declaration(),
        rc_evidence=_rc_evidence(),
        environ={},
        dsn="secret-dsn",
        table_prefix=staging.make_staging_prefix(),
        scope_context_factory=mismatched_scope,
    )

    assert result["passed"] is False
    assert result["live_validation_executed"] is False
    assert "POSTGRES_TARGET_MISMATCH" in result["gate_codes"]


def test_live_preflight_reports_invalid_approval_configuration_as_stable_gate(
    monkeypatch,
):
    for name in ("run_validation", "_validate_durable_metrics"):
        monkeypatch.setattr(
            staging,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(
                f"invalid approval configuration must not call {_name}"
            ),
        )

    @contextmanager
    def invalid_scope(**_kwargs):
        raise AcceptanceConfigurationError("approval is incomplete")
        yield

    result = staging.run_live_preflight(
        declaration=_declaration(),
        rc_evidence=_rc_evidence(),
        environ={},
        dsn="secret-dsn",
        table_prefix=staging.make_staging_prefix(),
        scope_context_factory=invalid_scope,
    )

    assert result["passed"] is False
    assert result["live_validation_executed"] is False
    assert "ACCEPTANCE_CONFIGURATION_INVALID" in result["gate_codes"]


def test_live_preflight_reports_missing_dsn_before_creating_scope():
    @contextmanager
    def forbidden_scope(**_kwargs):
        pytest.fail("missing DSN must not create an OwnedPostgresScope")
        yield

    result = staging.run_live_preflight(
        declaration=_declaration(),
        rc_evidence=_rc_evidence(),
        environ={},
        dsn="",
        table_prefix=staging.make_staging_prefix(),
        scope_context_factory=forbidden_scope,
    )

    assert result["passed"] is False
    assert result["live_validation_executed"] is False
    assert "ACCEPTANCE_CONFIGURATION_INVALID" in result["gate_codes"]


def test_live_preflight_uses_owned_scope_cleanup_receipt(monkeypatch):
    prefix = staging.make_staging_prefix()
    scope_arguments = {}

    @contextmanager
    def owned_scope(**kwargs):
        scope_arguments.update(kwargs)
        active = SimpleNamespace(
            lease=SimpleNamespace(
                cleanup_receipt=SimpleNamespace(residue_count=0)
            )
        )
        yield active
    monkeypatch.setattr(
        staging,
        "run_validation",
        lambda **_kwargs: SimpleNamespace(
            relation_count=3,
            required_migration_ids={
                spec.migration_id for spec in staging.RUNTIME_MIGRATIONS
            },
        ),
    )
    monkeypatch.setattr(staging, "_validate_durable_metrics", lambda *_args: True)

    result = staging.run_live_preflight(
        declaration=_declaration(),
        rc_evidence=_rc_evidence(),
        environ={},
        dsn="secret-dsn",
        table_prefix=prefix,
        scope_context_factory=owned_scope,
    )

    assert result["passed"] is True
    assert result["live_validation_executed"] is True
    assert result["rollback_verified"] is True
    assert scope_arguments == {
        "dsn": "secret-dsn",
        "scope_prefix": prefix,
        "environ": {},
    }


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
    output_record = tmp_path / "staging-record.json"
    secret = b"s" * 32
    signer = HmacReceiptSigner(key_id="staging-test", secret=secret)
    payload = rc_payload()
    bundle = EvidenceIssuer(
        signer=signer,
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="operational-rc-evidence",
        payload=payload,
        policy_result=OperationalRcEvidencePolicy().evaluate(payload),
        producer="tests.staging-rc",
        tool_version="1.0.0",
        revision="bcdefa2",
        scope="memory.operational-rc.controlled",
    )
    AtomicEvidenceWriter().write(evidence, bundle)
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "staging-test")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(secret).decode("ascii"),
    )
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
            "bcdefa2",
            "--observation-profile",
            "B",
            "--observation-hours",
            "24",
            "--co-resident-isolated-staging",
            "--dedicated-connection-scope",
            "--dedicated-worker-scope",
            "--dedicated-owner-scope",
            "--deterministic-path-verified",
            "--output-record",
            str(output_record),
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "DRY_RUN"
    assert payload["configuration_changed"] is False
    assert payload["all_memory_shadows_disabled"] is True
    assert json.loads(output_record.read_text(encoding="utf-8")) == payload
