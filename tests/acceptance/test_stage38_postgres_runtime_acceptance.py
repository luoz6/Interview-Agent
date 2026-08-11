from __future__ import annotations

"""Acceptance coverage for the Stage 38 PostgreSQL runtime gate."""

import base64
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from app.ports.postgres_scope import PostgresTargetMismatch
from contracts.evidence import EvidenceRegistry, EvidenceVerifier
from scripts import stage38_postgres_runtime_acceptance as stage38
from scripts.postgres_acceptance_support import load_receipt_signer


SAFE_PREFIX = "test_stage38_0123456789ab"
REVISION = "abcdef1"


def _configure_execution(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://user:secret@db/interview")
    monkeypatch.setenv("EVIDENCE_REVISION", REVISION)
    monkeypatch.setenv("EVIDENCE_HMAC_KEY_ID", "stage38-test-key")
    monkeypatch.setenv(
        "EVIDENCE_HMAC_SECRET_B64",
        base64.b64encode(b"s" * 32).decode("ascii"),
    )


def _observations() -> dict[str, bool]:
    return {
        "schema_initialized": True,
        "stale_version_rejected": True,
        "duplicate_command_idempotent": True,
        "stream_completion_exactly_once": True,
        "report_lifecycle_preserved": True,
        "reinstantiation_recovered": True,
    }


def test_cli_defaults_to_dry_run_without_connecting(monkeypatch, capsys):
    @contextmanager
    def forbidden_scope(**_kwargs):
        pytest.fail("dry-run must not create an Owned PostgreSQL scope")
        yield

    monkeypatch.setattr(stage38, "approved_postgres_scope", forbidden_scope)

    assert stage38.main([]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "DRY_RUN"
    assert payload["status"] == "not_run"
    assert payload["database_connection_attempted"] is False
    assert "POSTGRES_ACCEPTANCE_APPROVED_FINGERPRINT" in payload[
        "required_authorization"
    ]
    rendered = json.dumps(payload).casefold()
    assert "postgresql://" not in rendered
    assert "password" not in rendered


def test_execute_requires_protected_configuration_before_scope(monkeypatch):
    @contextmanager
    def forbidden_scope(**_kwargs):
        pytest.fail("missing configuration must stop before Owned PostgreSQL scope")
        yield

    monkeypatch.setattr(stage38, "approved_postgres_scope", forbidden_scope)

    with pytest.raises(
        stage38.AcceptanceGateError,
        match="ACCEPTANCE_CONFIGURATION_INVALID",
    ):
        stage38.main(["--execute", "--table-prefix", SAFE_PREFIX])


def test_execute_requires_owned_target_match_before_acceptance(monkeypatch):
    _configure_execution(monkeypatch)

    @contextmanager
    def mismatched_scope(**_kwargs):
        raise PostgresTargetMismatch("target mismatch")
        yield

    monkeypatch.setattr(stage38, "approved_postgres_scope", mismatched_scope)
    monkeypatch.setattr(
        stage38,
        "run_acceptance",
        lambda **_kwargs: pytest.fail("target mismatch must stop before acceptance"),
    )

    with pytest.raises(
        stage38.AcceptanceGateError,
        match="POSTGRES_TARGET_MISMATCH",
    ):
        stage38.main(["--execute", "--table-prefix", SAFE_PREFIX])


def test_execute_writes_signed_redacted_evidence_and_uses_cleanup_receipt(
    monkeypatch,
    tmp_path,
    capsys,
):
    _configure_execution(monkeypatch)
    captured = {}

    @contextmanager
    def owned_scope(**kwargs):
        captured.update(kwargs)
        lease = SimpleNamespace(cleanup_receipt=None)
        active = SimpleNamespace(lease=lease)
        yield active
        lease.cleanup_receipt = SimpleNamespace(
            ownership_verified=True,
            target_verified=True,
            residue_count=0,
        )

    monkeypatch.setattr(stage38, "approved_postgres_scope", owned_scope)
    monkeypatch.setattr(stage38, "run_acceptance", lambda **_kwargs: _observations())
    output = tmp_path / "evidence.json"

    assert stage38.main(
        [
            "--execute",
            "--table-prefix",
            SAFE_PREFIX,
            "--output",
            str(output),
        ]
    ) == 0

    stdout = capsys.readouterr().out
    persisted = output.read_text(encoding="utf-8")
    value = json.loads(persisted)
    verified = EvidenceVerifier(
        registry=EvidenceRegistry.default(),
        receipt_signer=load_receipt_signer(stage38.os.environ),
    ).verify(
        value,
        expected_revision=REVISION,
        expected_scope="stage38.postgres-runtime.controlled",
    )
    assert verified.bundle.artifact.verification_status.value == "PASS"
    assert verified.bundle.artifact.promotion_decision.value == "HOLD"
    assert verified.payload.cleanup_residue_count == 0
    assert captured["scope_prefix"] == SAFE_PREFIX
    assert "VERIFICATION_STATUS=PASS" in stdout
    assert "PROMOTION_DECISION=HOLD" in stdout
    assert "secret" not in stdout
    assert "secret" not in persisted
    assert "postgresql://" not in stdout
    assert "postgresql://" not in persisted
    assert SAFE_PREFIX not in persisted


def test_stage38_policy_blocks_failed_runtime_or_cleanup_observation():
    payload = stage38.Stage38AcceptanceEvidencePayload(
        schema_version="stage38-acceptance-evidence-v1",
        synthetic=True,
        **{**_observations(), "stream_completion_exactly_once": False},
        cleanup_ownership_verified=True,
        cleanup_target_verified=True,
        cleanup_residue_count=1,
    )

    result = stage38.Stage38AcceptanceEvidencePolicy().evaluate(payload)

    assert result.verification_status.value == "BLOCKED"
    assert result.promotion_decision.value == "HOLD"
    assert "STAGE38_STREAM_STATE_MISMATCH" in result.gate_codes
    assert "STAGE38_CLEANUP_RESIDUE" in result.gate_codes


def test_table_prefix_validation_happens_before_database_access(monkeypatch):
    @contextmanager
    def forbidden_scope(**_kwargs):
        pytest.fail("unsafe prefix must fail before database access")
        yield

    monkeypatch.setattr(stage38, "approved_postgres_scope", forbidden_scope)

    with pytest.raises(
        stage38.AcceptanceGateError,
        match="STAGE38_TABLE_PREFIX_UNSAFE",
    ):
        stage38.main(["--execute", "--table-prefix", "interview"])


def test_gate_checks_are_not_python_assert_statements():
    source = stage38.Path(stage38.__file__).read_text(encoding="utf-8")

    assert "assert " not in source
    assert "from tests" not in source
    assert "DEFAULT_DSN" not in source
    assert "--inspect-database-fingerprint" not in source
    assert "--expected-database-fingerprint" not in source
    assert "drop_isolated_tables" not in source
