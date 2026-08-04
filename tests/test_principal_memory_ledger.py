import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.principal_memory_ledger import (
    GENESIS_HEAD_SHA256,
    LEDGER_SCHEMA_VERSION,
    PrincipalMemoryLedgerError,
    ProtectedPrincipalMemoryLedger,
)
from app.services.principal_memory_rights import (
    InMemoryPrincipalMemoryDeletionTombstoneStore,
)
from tests.test_principal_memory_rights import NOW


def completed_tombstone():
    store = InMemoryPrincipalMemoryDeletionTombstoneStore(clock=lambda: NOW)
    return store.mark(
        store.record_requested(
            deployment_id="single-tenant-local",
            principal_id="local-owner",
        ),
        status="completed",
    )


def ledger(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    return ProtectedPrincipalMemoryLedger(
        protected / "principal-memory.jsonl",
        workspace=workspace,
    )


def test_empty_ledger_is_valid_genesis_and_probe_does_not_mutate_it(tmp_path):
    value = ledger(tmp_path)

    before = value.summary()
    value.probe_writable()
    after = value.summary()

    assert before == after
    assert after.ledger_event_count == 0
    assert after.ledger_head_sha256 == GENESIS_HEAD_SHA256
    assert list(value.resolved_path.parent.glob("*.probe")) == []


def test_append_is_opaque_hash_chained_durable_and_idempotent(tmp_path):
    value = ledger(tmp_path)
    tombstone = completed_tombstone()

    first = value.append_tombstone(tombstone)
    second = value.append_tombstone(tombstone)
    events = value.load()

    assert first["appended"] == 1
    assert second["already_present"] == 1
    assert len(events) == 1
    assert events[0].schema_version == LEDGER_SCHEMA_VERSION
    assert events[0].previous_head_sha256 == GENESIS_HEAD_SHA256
    rendered = value.resolved_path.read_text(encoding="utf-8")
    for forbidden in (
        tombstone.deployment_id,
        tombstone.principal_id,
        tombstone.tombstone_ref,
        "principal_id",
        "deployment_id",
        "normalized_fact",
    ):
        assert forbidden not in rendered


def test_torn_line_unknown_field_and_hash_tampering_fail_closed(tmp_path):
    value = ledger(tmp_path)
    value.append_tombstone(completed_tombstone())
    original = value.resolved_path.read_text(encoding="utf-8")

    value.resolved_path.write_text(original.rstrip("\n"), encoding="utf-8")
    with pytest.raises(PrincipalMemoryLedgerError) as torn:
        value.load()
    assert torn.value.gate_code == "TOMBSTONE_LEDGER_CORRUPTED"

    payload = json.loads(original)
    payload["unknown"] = True
    value.resolved_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(PrincipalMemoryLedgerError):
        value.load()

    payload.pop("unknown")
    payload["schema_version"] = "future-ledger-v9"
    value.resolved_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(PrincipalMemoryLedgerError) as unsupported:
        value.load()
    assert unsupported.value.gate_code == "TOMBSTONE_LEDGER_SCHEMA_UNSUPPORTED"

    payload["schema_version"] = LEDGER_SCHEMA_VERSION
    payload["event_sha256"] = "f" * 64
    value.resolved_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(PrincipalMemoryLedgerError):
        value.load()


def test_relative_and_workspace_paths_are_rejected(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(PrincipalMemoryLedgerError) as relative:
        ProtectedPrincipalMemoryLedger(
            Path("relative.jsonl"),
            workspace=workspace,
        )
    assert relative.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"

    with pytest.raises(PrincipalMemoryLedgerError) as contained:
        ProtectedPrincipalMemoryLedger(
            workspace / "ledger.jsonl",
            workspace=workspace,
        )
    assert contained.value.gate_code == "TOMBSTONE_LEDGER_PATH_INVALID"


def test_os_lock_times_out_across_processes_and_recovers_after_exit(tmp_path):
    value = ledger(tmp_path)
    child_code = (
        "from pathlib import Path; from time import sleep; "
        "from app.services.principal_memory_ledger import "
        "ProtectedPrincipalMemoryLedger; "
        f"value=ProtectedPrincipalMemoryLedger(Path({str(value.resolved_path)!r}),"
        f"workspace=Path({str(value.workspace)!r}),lock_timeout_seconds=1); "
        "\nwith value.exclusive_lock():\n print('LOCKED',flush=True)\n sleep(1)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "LOCKED"
        blocked = ProtectedPrincipalMemoryLedger(
            value.resolved_path,
            workspace=value.workspace,
            lock_timeout_seconds=0.1,
        )
        with pytest.raises(PrincipalMemoryLedgerError) as captured:
            with blocked.exclusive_lock():
                pass
        assert captured.value.gate_code == "TOMBSTONE_LEDGER_LOCK_UNAVAILABLE"
        assert child.wait(timeout=5) == 0
        with blocked.exclusive_lock():
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
