from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.services.principal_memory_durable_ledger import (
    PrincipalMemoryDurableLedger,
)
from app.services.principal_memory_ledger import (
    GENESIS_HEAD_SHA256,
    PrincipalMemoryLedgerError,
    ProtectedPrincipalMemoryLedger,
)
from app.services.principal_memory_ledger_replay import (
    PrincipalMemoryOpaqueLedgerReplay,
)
from app.services.principal_memory_rights import (
    InMemoryPrincipalMemoryDeletionTombstoneStore,
)


NOW = datetime(2026, 8, 4, 20, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Watermark:
    last_applied_ledger_event_count: int
    last_applied_ledger_head_sha256: str
    last_applied_at: datetime | None = None


class WatermarkStore:
    def __init__(self):
        self.value = Watermark(0, GENESIS_HEAD_SHA256)

    def get(self):
        return self.value

    def advance(
        self,
        *,
        expected_event_count,
        expected_head_sha256,
        new_event_count,
        new_head_sha256,
        applied_at,
    ):
        if self.value != Watermark(
            expected_event_count,
            expected_head_sha256,
            self.value.last_applied_at,
        ):
            raise RuntimeError("watermark conflict")
        self.value = Watermark(new_event_count, new_head_sha256, applied_at)
        return self.value


def _completed(*, deployment_id="single-tenant-local", principal_id="owner"):
    store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    requested = store.record_requested(
        deployment_id=deployment_id, principal_id=principal_id
    )
    return store.completion_candidate(requested)


def _completed_at(when, *, principal_id="owner"):
    store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: when)
    return store.completion_candidate(
        store.record_requested(
            deployment_id="single-tenant-local",
            principal_id=principal_id,
        )
    )


def _coordinator(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    watermark = WatermarkStore()
    coordinator = PrincipalMemoryDurableLedger(
        path=protected / "ledger.jsonl",
        workspace=workspace,
        watermark_store=watermark,
    )
    return coordinator, watermark


def test_online_append_then_watermark_advance_becomes_ready(tmp_path):
    coordinator, watermark = _coordinator(tmp_path)
    tombstone = _completed()

    coordinator.require_ready()
    receipt = coordinator.append_completed(tombstone)
    assert coordinator.readiness()["gate_codes"] == [
        "TOMBSTONE_REPLAY_REQUIRED"
    ]
    result = coordinator.mark_applied(tombstone, receipt)

    assert result["advanced"] == 1
    assert watermark.get().last_applied_ledger_event_count == 1
    assert coordinator.readiness()["ready"] is True


def test_wrong_watermark_fails_closed_without_rewriting_ledger(tmp_path):
    coordinator, watermark = _coordinator(tmp_path)
    tombstone = _completed()
    receipt = coordinator.append_completed(tombstone)
    original = coordinator.ledger.resolved_path.read_bytes()
    watermark.value = Watermark(0, "f" * 64)

    with pytest.raises(PrincipalMemoryLedgerError) as captured:
        coordinator.mark_applied(tombstone, receipt)
    assert captured.value.gate_code == "TOMBSTONE_LEDGER_DIVERGED"
    assert coordinator.ledger.resolved_path.read_bytes() == original


def test_opaque_restore_replay_maps_scope_purges_and_advances(tmp_path):
    coordinator, watermark = _coordinator(tmp_path)
    tombstone = _completed(principal_id="restored-owner")
    coordinator.append_completed(tombstone)

    class Inventory:
        def list_scopes(self):
            return (("single-tenant-local", "restored-owner"),)

    class Deletion:
        def __init__(self):
            self.calls = []

        def replay_opaque_scope(self, **scope):
            self.calls.append(scope)
            return {"facts_deleted": 2, "cache_deleted": 1}

    deletion = Deletion()
    replay = PrincipalMemoryOpaqueLedgerReplay(
        ledger=coordinator.ledger,
        watermark_store=watermark,
        scope_inventory=Inventory(),
        deletion_service=deletion,
    )
    result = replay.replay_missing()

    assert result["events_replayed"] == 1
    assert result["facts_deleted"] == 2
    assert result["cache_deleted"] == 1
    assert len(deletion.calls) == 1
    assert coordinator.readiness()["ready"] is True
    rendered = repr(result)
    assert "restored-owner" not in rendered
    assert "single-tenant-local" not in rendered


def test_opaque_restore_replay_requires_one_unique_scope_match(tmp_path):
    coordinator, watermark = _coordinator(tmp_path)
    coordinator.append_completed(_completed(principal_id="missing-owner"))

    class EmptyInventory:
        def list_scopes(self):
            return ()

    replay = PrincipalMemoryOpaqueLedgerReplay(
        ledger=coordinator.ledger,
        watermark_store=watermark,
        scope_inventory=EmptyInventory(),
        deletion_service=object(),
    )
    with pytest.raises(PrincipalMemoryLedgerError) as captured:
        replay.replay_missing()
    assert captured.value.gate_code == "TOMBSTONE_REPLAY_RESIDUE"
    assert watermark.get().last_applied_ledger_event_count == 0


def test_opaque_replay_preserves_multiple_delete_recreate_cycles(tmp_path):
    coordinator, watermark = _coordinator(tmp_path)
    first = _completed_at(NOW, principal_id="repeat-owner")
    second = _completed_at(
        NOW.replace(minute=NOW.minute + 1), principal_id="repeat-owner"
    )
    coordinator.append_completed(first)
    coordinator.append_completed(second)

    class Inventory:
        def list_scopes(self):
            return (("single-tenant-local", "repeat-owner"),)

    class Deletion:
        def __init__(self):
            self.calls = 0

        def replay_opaque_scope(self, **_scope):
            self.calls += 1
            return {"facts_deleted": int(self.calls == 1)}

    deletion = Deletion()
    result = PrincipalMemoryOpaqueLedgerReplay(
        ledger=coordinator.ledger,
        watermark_store=watermark,
        scope_inventory=Inventory(),
        deletion_service=deletion,
    ).replay_missing()

    assert first.tombstone_ref != second.tombstone_ref
    assert result["events_replayed"] == 2
    assert deletion.calls == 2
    assert watermark.get().last_applied_ledger_event_count == 2
