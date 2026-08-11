from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import base64
import json

from app.runtime.config.compatibility import PostgresCapacitySettings, PostgresPoolSettings
from app.services.postgres_capacity import (
    PostgresServerCapacity,
    build_capacity_evidence_payload,
)
from contracts.evidence import AtomicEvidenceWriter, EvidenceIssuer, HmacReceiptSigner
from contracts.policies import CapacityEvidencePolicy
from scripts import repository_acceptance as acceptance
from scripts.repository_acceptance import (
    capacity_artifact_eligible as _capacity_artifact_eligible,
    evaluate_stage48_acceptance,
)


REVISION = "abcdef1"
SECRET = b"k" * 32


def _capacity_bundle():
    pools = PostgresPoolSettings(
        business_min_size=1,
        business_max_size=2,
        business_acquire_timeout_seconds=2,
        telemetry_min_size=1,
        telemetry_max_size=1,
        telemetry_acquire_timeout_seconds=1,
        lock_min_size=1,
        lock_max_size=1,
        lock_acquire_timeout_seconds=2,
        checkpointer_min_size=1,
        checkpointer_max_size=1,
        checkpointer_acquire_timeout_seconds=2,
        checkpointer_overhead=1,
        connect_timeout_seconds=3,
        drain_timeout_seconds=10,
        max_lifetime_seconds=1800,
        max_idle_seconds=300,
    )
    capacity = PostgresCapacitySettings(
        expected_api_processes=1,
        expected_celery_processes=0,
        expected_outbox_processes=0,
        external_connection_reserve=10,
        max_utilization=0.8,
    )
    domains = {
        "business": {
            "max_size": 2,
            "peak_leased": 2,
            "acquire_timeout_count": 0,
            "discard_count": 0,
            "p95_wait_ms": 1.0,
        },
        "telemetry": {
            "max_size": 1,
            "peak_leased": 1,
            "acquire_timeout_count": 0,
            "discard_count": 0,
            "p95_wait_ms": 1.0,
        },
        "advisory_lock": {
            "max_size": 1,
            "peak_leased": 1,
            "acquire_timeout_count": 0,
            "discard_count": 0,
            "p95_wait_ms": 1.0,
        },
    }
    payload = build_capacity_evidence_payload(
        pools=pools,
        capacity=capacity,
        server=PostgresServerCapacity(100, 3, 5, 0),
        domain_snapshots=domains,
        schema_ready=True,
        load_errors=[],
        observed_checkpointer_peak=1,
        observed_application_peak=5,
        expected_application_peak=5,
        observed_advisory_locks=1,
        expected_advisory_locks=1,
        simultaneous_domains_verified=True,
    )
    result = CapacityEvidencePolicy(
        minimum_samples=1,
        minimum_headroom_percent=0.0,
    ).evaluate(payload, production_scope=False)
    return EvidenceIssuer(
        signer=HmacReceiptSigner(key_id="acceptance-v1", secret=SECRET),
        clock=lambda: datetime.now(timezone.utc),
    ).issue(
        payload_type="capacity-evidence",
        payload=payload,
        policy_result=result,
        producer="tests.stage48",
        tool_version="2.0.0",
        revision=REVISION,
        scope="capacity.controlled",
    )


def _environment():
    return {
        "EVIDENCE_REVISION": REVISION,
        "EVIDENCE_HMAC_KEY_ID": "acceptance-v1",
        "EVIDENCE_HMAC_SECRET_B64": base64.b64encode(SECRET).decode("ascii"),
    }


def test_stage48_acceptance_requires_postgres():
    result = evaluate_stage48_acceptance(
        {"contracts": True}, postgres_configured=False
    )
    assert result["status"] == "BLOCKED_POSTGRES_GATE"
    assert result["production_observation"] == "NOT_RUN"


def test_stage48_acceptance_reports_repository_readiness_only_when_all_pass():
    result = evaluate_stage48_acceptance(
        {"contracts": True, "postgres": True}, postgres_configured=True
    )
    assert result["status"] == "READY_FOR_CAPACITY_AWARE_FENCING_CANARY"
    assert result["production_observation"] == "NOT_RUN"


def test_stage48_acceptance_fails_any_repository_gate():
    result = evaluate_stage48_acceptance(
        {"contracts": True, "postgres": False}, postgres_configured=True
    )
    assert result["status"] == "FAILED_REPOSITORY_GATE"


def test_unified_cli_dispatches_stage48_profile(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POSTGRES_DSN", "configured-without-connecting")
    monkeypatch.setattr(acceptance, "run_pytest", lambda arguments: True)
    monkeypatch.setattr(
        acceptance,
        "capacity_artifact_eligible",
        lambda path: True,
    )

    assert acceptance.main(
        ["stage48", "--capacity-artifact", str(tmp_path / "capacity.json")]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == (
        "READY_FOR_CAPACITY_AWARE_FENCING_CANARY"
    )


def test_stage48_capacity_consumer_verifies_receipt_revision_and_policy(tmp_path):
    target = tmp_path / "capacity.json"
    AtomicEvidenceWriter().write(target, _capacity_bundle())

    assert _capacity_artifact_eligible(target, environ=_environment()) is True


def test_stage48_capacity_consumer_rejects_signed_policy_bypass(tmp_path):
    value = _capacity_bundle().model_dump(mode="json")
    value["artifact"]["payload"]["schema_ready"] = False
    target = tmp_path / "capacity.json"
    target.write_text(json.dumps(value), encoding="utf-8")

    assert _capacity_artifact_eligible(target, environ=_environment()) is False


def test_stage48_capacity_consumer_rejects_wrong_revision(tmp_path):
    target = tmp_path / "capacity.json"
    AtomicEvidenceWriter().write(target, _capacity_bundle())
    environment = deepcopy(_environment())
    environment["EVIDENCE_REVISION"] = "deadbee"

    assert _capacity_artifact_eligible(target, environ=environment) is False
