from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.runtime.config.memory import load_effective_memory_config
from app.services.memory_metrics import (
    InMemoryMemoryMetricStore,
    MemoryMetricEvent,
    configure_memory_metric_store,
    publish_principal_local_consume_metric,
    reset_memory_metric_store,
)
from app.services.principal_identity import ExplicitPrincipalIdentityResolver
from app.services.principal_memory_operations import (
    LOCAL_MEMORY_OPERATION_GATE_CODES,
    PrincipalMemoryOperationsService,
    evaluate_local_memory_readiness,
)
from app.services.principal_memory_ledger import GENESIS_HEAD_SHA256
from app.services.principal_memory_rights import (
    InMemoryPrincipalMemoryDeletionTombstoneStore,
    InMemoryPrincipalMemoryExportStore,
    PrincipalMemoryExportRecord,
)
from app.services.principal_memory_safe_refs import (
    InMemoryPrincipalMemorySafeRefStore,
    PrincipalMemorySafeRefRecord,
)


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def local_consume_config():
    return load_effective_memory_config(
        {
            "MEMORY_TRUSTED_LOCAL_METRICS_ENABLED": "true",
            "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED": "true",
            "MEMORY_LOCAL_PRINCIPAL_ENABLED": "true",
            "MEMORY_LONG_TERM_MODE": "local_consume",
            "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_READ_SHADOW_ENABLED": "true",
            "MEMORY_LONG_TERM_LOCAL_CONSUMPTION_ENABLED": "true",
        }
    )


class Metrics:
    def __init__(self, *, complete=True):
        self.complete = complete

    def diagnostics(self):
        return {
            "store_kind": "postgres_aggregate",
            "data_complete": self.complete,
            "latest_bucket_at": NOW.isoformat(),
        }

    def rollup(self, *, batch_size):
        assert batch_size == 10
        return 3

    def cleanup(self, *, batch_size):
        assert batch_size == 10
        return {"minute_deleted": 4, "hour_deleted": 5}


class Probe:
    def __init__(self, current=True):
        self.current = current

    def is_current(self):
        return self.current


class Facts:
    def expire_batch(self, **kwargs):
        assert kwargs["limit"] == 10
        assert kwargs["now"] == NOW
        return 2


class ExpiryStore:
    def __init__(self, count):
        self.count = count

    def cleanup_expired(self, **kwargs):
        assert kwargs == {"now": NOW, "batch_size": 10}
        return self.count


def test_local_consume_metric_is_aggregate_only_and_rejects_locators():
    store = InMemoryMemoryMetricStore(
        clock=lambda: datetime.now(timezone.utc)
    )
    configure_memory_metric_store(store)
    try:
        publish_principal_local_consume_metric(
            outcome="consumed",
            reason="eligible",
            selected_count=2,
            estimated_input_tokens=48,
        )
        payload = store.aggregate(window_minutes=60)
    finally:
        reset_memory_metric_store()

    item = payload["items"][0]
    assert item["metric_code"] == "principal_local_consume"
    assert item["dimensions"] == {
        "operation": "followup",
        "outcome": "completed",
        "reason": "eligible",
        "shadow_mode": False,
        "consumption_enabled": True,
    }
    assert item["values"]["selected_count"] == 2
    assert item["values"]["estimated_input_tokens"] == 48
    for forbidden in (
        "principal_id",
        "session_id",
        "fact_id",
        "source_manifest_sha256",
        "source_excerpt_sha256",
        "prompt",
        "answer",
        "normalized_fact",
    ):
        assert forbidden not in repr(payload)
        with pytest.raises(ValidationError):
            MemoryMetricEvent.model_validate(
                {
                    "metric_code": "principal_local_consume",
                    "dimensions": {
                        "operation": "followup",
                        forbidden: "private",
                    },
                }
            )


def test_readiness_truth_table_is_fail_closed_and_uses_stable_gate_codes():
    config = local_consume_config()
    ready = evaluate_local_memory_readiness(
        config=config,
        runtime_store="postgres",
        migration_current=True,
        metrics_diagnostics={"data_complete": True},
        identity_ready=True,
        ledger_readiness={"ready": True, "gate_codes": []},
    )
    assert ready["local_consume_ready"] is True
    assert ready["gate_codes"] == []

    blocked = evaluate_local_memory_readiness(
        config=config,
        runtime_store="memory",
        migration_current=False,
        metrics_diagnostics={"data_complete": False},
        identity_ready=False,
        ledger_readiness={"ready": True, "gate_codes": []},
    )
    assert blocked["local_consume_ready"] is False
    assert blocked["gate_codes"] == sorted(
        {
            "POSTGRES_RUNTIME_REQUIRED",
            "POSTGRES_MIGRATION_NOT_CURRENT",
            "DURABLE_METRICS_INCOMPLETE",
            "TRUSTED_LOCAL_IDENTITY_UNAVAILABLE",
        }
    )
    assert set(blocked["gate_codes"]) <= LOCAL_MEMORY_OPERATION_GATE_CODES

    disabled = evaluate_local_memory_readiness(
        config=load_effective_memory_config({}),
        runtime_store="memory",
        migration_current=False,
        metrics_diagnostics={"data_complete": False},
        identity_ready=False,
    )
    assert disabled["state"] == "disabled"
    assert "LOCAL_CONSUME_MODE_DISABLED" in disabled["gate_codes"]


def test_operational_status_has_no_identity_and_cleanup_returns_only_counts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()

    class WatermarkStore:
        def get(self):
            return SimpleNamespace(
                last_applied_ledger_event_count=0,
                last_applied_ledger_head_sha256=GENESIS_HEAD_SHA256,
            )

    service = PrincipalMemoryOperationsService(
        config=local_consume_config(),
        runtime_store="postgres",
        identity_resolver=ExplicitPrincipalIdentityResolver(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            assurance="trusted_local",
            clock=lambda: NOW,
        ),
        migration_probe=Probe(),
        metric_store=Metrics(),
        fact_store=Facts(),
        export_store=ExpiryStore(6),
        safe_ref_store=ExpiryStore(7),
        ledger_path=protected / "ledger.jsonl",
        ledger_watermark_store=WatermarkStore(),
        workspace=workspace,
        clock=lambda: NOW,
    )

    status = service.status()
    cleanup = service.cleanup(batch_size=10)

    assert status["local_consume_ready"] is True
    assert cleanup == {
        "schema_version": "principal-memory-local-cleanup-v1",
        "status": "completed",
        "facts_expired": 2,
        "exports_deleted": 6,
        "safe_refs_deleted": 7,
        "metric_rollups": 3,
        "metric_minute_deleted": 4,
        "metric_hour_deleted": 5,
    }
    rendered = repr({"status": status, "cleanup": cleanup})
    assert "local-owner" not in rendered
    assert "principal_id" not in rendered
    assert "fact_id" not in rendered


def test_expired_export_and_safe_ref_cleanup_is_bounded():
    exports = InMemoryPrincipalMemoryExportStore()
    refs = InMemoryPrincipalMemorySafeRefStore(clock=lambda: NOW)
    for index, expires_at in enumerate(
        (NOW - timedelta(seconds=1), NOW - timedelta(seconds=2), NOW + timedelta(seconds=1))
    ):
        exports.put(
            PrincipalMemoryExportRecord(
                export_ref=f"pm-export-{index:032x}",
                deployment_id="single-tenant-local",
                principal_id="local-owner",
                payload={},
                created_at=NOW - timedelta(hours=24),
                expires_at=expires_at,
            )
        )
        refs._items[f"pm-ref-{index}"] = PrincipalMemorySafeRefRecord(
            safe_ref=f"pm-ref-{index}",
            deployment_id="single-tenant-local",
            principal_id="local-owner",
            fact_id="a" * 64,
            fact_version=1,
            expires_at=expires_at,
        )

    assert exports.cleanup_expired(now=NOW, batch_size=1) == 1
    assert exports.cleanup_expired(now=NOW, batch_size=10) == 1
    assert refs.cleanup_expired(now=NOW, batch_size=1) == 1
    assert refs.cleanup_expired(now=NOW, batch_size=10) == 1
    assert exports.count(
        deployment_id="single-tenant-local", principal_id="local-owner"
    ) == 1
    assert len(refs._items) == 1


def test_operator_ledger_import_rejects_conflicting_tombstone():
    store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    current = store.record_requested(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )
    other = current.model_copy(
        update={
            "tombstone_ref": "pm-delete-" + "b" * 64,
            "integrity_sha256": "b" * 64,
        }
    )
    with pytest.raises(ValueError, match="integrity mismatch"):
        store.import_tombstone(other)
