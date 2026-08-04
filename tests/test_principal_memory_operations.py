from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.memory_config import load_effective_memory_config
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
    append_completed_tombstone_ledger,
    evaluate_local_memory_readiness,
    load_protected_tombstone_ledger,
    replay_tombstone_ledger,
)
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
    )
    assert ready["local_consume_ready"] is True
    assert ready["gate_codes"] == []

    blocked = evaluate_local_memory_readiness(
        config=config,
        runtime_store="memory",
        migration_current=False,
        metrics_diagnostics={"data_complete": False},
        identity_ready=False,
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


def test_operational_status_has_no_identity_and_cleanup_returns_only_counts():
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


def test_protected_tombstone_ledger_rejects_empty_unknown_and_oversized(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="TOMBSTONE_LEDGER_INVALID"):
        load_protected_tombstone_ledger(empty)

    unknown = tmp_path / "unknown.jsonl"
    unknown.write_text(json.dumps({"principal_id": "private"}), encoding="utf-8")
    with pytest.raises(ValueError, match="TOMBSTONE_LEDGER_INVALID"):
        load_protected_tombstone_ledger(unknown)

    oversized = tmp_path / "large.jsonl"
    oversized.write_text("x" * 1_000_001, encoding="utf-8")
    with pytest.raises(ValueError, match="TOMBSTONE_LEDGER_INVALID"):
        load_protected_tombstone_ledger(oversized)


def test_protected_tombstone_ledger_validates_integrity_before_database_use(
    tmp_path,
):
    store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    tombstone = store.record_requested(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )
    completed = store.mark(tombstone, status="completed")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(completed.model_dump_json() + "\n", encoding="utf-8")

    assert load_protected_tombstone_ledger(ledger) == [completed]

    ledger.write_text(
        completed.model_copy(
            update={"integrity_sha256": "b" * 64}
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="TOMBSTONE_LEDGER_INVALID"):
        load_protected_tombstone_ledger(ledger)


def test_operator_ledger_capture_is_durable_idempotent_and_loadable(tmp_path):
    store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    completed = store.mark(
        store.record_requested(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ),
        status="completed",
    )
    ledger = (tmp_path / "operator-ledger.jsonl").resolve()

    first = append_completed_tombstone_ledger(ledger, completed)
    second = append_completed_tombstone_ledger(ledger, completed)

    assert first["appended"] == 1
    assert second["already_present"] == 1
    assert load_protected_tombstone_ledger(ledger) == [completed]


def test_operator_ledger_capture_rejects_workspace_destination(tmp_path):
    del tmp_path
    store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    completed = store.mark(
        store.record_requested(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ),
        status="completed",
    )

    with pytest.raises(ValueError, match="TOMBSTONE_LEDGER_INVALID"):
        append_completed_tombstone_ledger(
            Path.cwd() / "forbidden-ledger.jsonl",
            completed,
        )


def test_operator_ledger_imports_missing_tombstone_before_replay():
    source = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    tombstone = source.record_requested(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    )
    tombstone = source.mark(tombstone, status="completed")
    restored_store = InMemoryPrincipalMemoryDeletionTombstoneStore(
        clock=lambda: NOW + timedelta(seconds=1)
    )

    class Deletion:
        tombstone_store = restored_store

        def replay(self, imported):
            assert restored_store.get(
                deployment_id="single-tenant-local",
                principal_id="local-owner",
            ) == imported
            restored_store.mark(imported, status="replayed")
            return {"status": "replayed", "facts_deleted": 2}

    result = replay_tombstone_ledger(
        tombstones=[tombstone], deletion_service=Deletion()
    )

    assert result["validated"] == 1
    assert result["replayed"] == 1
    assert result["facts_deleted"] == 2
    assert restored_store.get(
        deployment_id="single-tenant-local",
        principal_id="local-owner",
    ).status == "replayed"


def test_operator_ledger_replays_multiple_deletion_cycles_for_one_principal():
    times = iter(
        (
            NOW,
            NOW,
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=1),
        )
    )
    source = InMemoryPrincipalMemoryDeletionTombstoneStore(
        clock=lambda: next(times)
    )
    first = source.mark(
        source.record_requested(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ),
        status="completed",
    )
    second = source.mark(
        source.record_requested(
            deployment_id="single-tenant-local", principal_id="local-owner"
        ),
        status="completed",
    )
    restored = InMemoryPrincipalMemoryDeletionTombstoneStore(
        clock=lambda: NOW + timedelta(seconds=2)
    )

    class Deletion:
        tombstone_store = restored

        def replay(self, imported):
            restored.mark(imported, status="replayed")
            return {"status": "replayed", "facts_deleted": 0}

    result = replay_tombstone_ledger(
        tombstones=[first, second], deletion_service=Deletion()
    )

    assert result["validated"] == 2
    assert result["replayed"] == 2
    assert first.tombstone_ref != second.tombstone_ref


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
