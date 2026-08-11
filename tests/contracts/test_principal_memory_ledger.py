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
from tests.principal_memory_fixtures import (
    RIGHTS_NOW as NOW,
    completed_tombstone,
    completed_tombstone_for,
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


def test_invalid_completion_time_is_rejected_before_any_append(tmp_path):
    value = ledger(tmp_path)
    invalid = completed_tombstone().model_copy(
        update={"completed_at": completed_tombstone().completed_at.replace(tzinfo=None)}
    )

    with pytest.raises(PrincipalMemoryLedgerError) as captured:
        value.append_tombstone(invalid)
    assert captured.value.gate_code == "TOMBSTONE_LEDGER_INVALID_EVENT"
    assert value.summary().ledger_event_count == 0


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


def test_ledger_read_failure_is_unwritable_not_content_corruption(
    tmp_path, monkeypatch
):
    value = ledger(tmp_path)
    value.append_tombstone(completed_tombstone())

    def unreadable(_path):
        raise OSError("private path deliberately omitted")

    monkeypatch.setattr(type(value.resolved_path), "read_bytes", unreadable)
    with pytest.raises(PrincipalMemoryLedgerError) as captured:
        value.load()
    assert captured.value.gate_code == "TOMBSTONE_LEDGER_UNWRITABLE"


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
        cwd=Path(__file__).resolve().parents[2],
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


def test_two_process_append_has_contiguous_chain_and_no_lost_event(tmp_path):
    value = ledger(tmp_path)
    children = []
    for principal_id in ("owner-a", "owner-b"):
        encoded = completed_tombstone_for(principal_id).model_dump_json()
        child_code = (
            "from pathlib import Path; "
            "from app.services.principal_memory_ledger import "
            "ProtectedPrincipalMemoryLedger; "
            "from app.services.principal_memory_rights import "
            "PrincipalMemoryDeletionTombstone; "
            f"item=PrincipalMemoryDeletionTombstone.model_validate_json({encoded!r}); "
            f"ledger=ProtectedPrincipalMemoryLedger(Path({str(value.resolved_path)!r}),"
            f"workspace=Path({str(value.workspace)!r}),lock_timeout_seconds=5); "
            "ledger.append_tombstone(item)"
        )
        children.append(
            subprocess.Popen(
                [sys.executable, "-c", child_code],
                cwd=Path(__file__).resolve().parents[2],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    failures = []
    for child in children:
        stdout, stderr = child.communicate(timeout=10)
        if child.returncode != 0:
            failures.append((child.returncode, stdout, stderr))
    assert failures == []
    events = value.load()
    assert [event.event_index for event in events] == [1, 2]
    assert events[0].event_sha256 == events[1].previous_head_sha256
    assert len({event.deletion_cycle for event in events}) == 2
