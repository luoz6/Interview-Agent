from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.principal_memory_ledger import (
    GENESIS_HEAD_SHA256,
    ProtectedPrincipalMemoryLedger,
)
from app.services.principal_memory_ledger_readiness import (
    check_principal_memory_ledger_readiness,
    evaluate_ledger_watermark,
)
from tests.test_principal_memory_ledger import completed_tombstone


@dataclass(frozen=True)
class Watermark:
    last_applied_ledger_event_count: int
    last_applied_ledger_head_sha256: str


class Store:
    def __init__(self, watermark):
        self.watermark = watermark

    def get(self):
        return self.watermark


def _ledger(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    value = ProtectedPrincipalMemoryLedger(
        protected / "memory.jsonl", workspace=workspace
    )
    return workspace, value


def test_missing_path_is_required_and_result_is_content_free(tmp_path):
    result = check_principal_memory_ledger_readiness(
        path=None,
        workspace=tmp_path,
        watermark_store=Store(Watermark(0, GENESIS_HEAD_SHA256)),
    )
    assert result["gate_codes"] == ["TOMBSTONE_LEDGER_REQUIRED"]
    assert result["ready"] is False
    assert "path" not in result
    assert "head" not in result


def test_equal_empty_and_equal_nonempty_are_ready(tmp_path):
    workspace, ledger = _ledger(tmp_path)
    empty = check_principal_memory_ledger_readiness(
        path=ledger.resolved_path,
        workspace=workspace,
        watermark_store=Store(Watermark(0, GENESIS_HEAD_SHA256)),
    )
    assert empty["ready"] is True

    ledger.append_tombstone(completed_tombstone())
    summary = ledger.summary()
    current = check_principal_memory_ledger_readiness(
        path=ledger.resolved_path,
        workspace=workspace,
        watermark_store=Store(
            Watermark(summary.ledger_event_count, summary.ledger_head_sha256)
        ),
    )
    assert current["ready"] is True


def test_external_strict_continuation_requires_replay(tmp_path):
    workspace, ledger = _ledger(tmp_path)
    ledger.append_tombstone(completed_tombstone())
    result = check_principal_memory_ledger_readiness(
        path=ledger.resolved_path,
        workspace=workspace,
        watermark_store=Store(Watermark(0, GENESIS_HEAD_SHA256)),
    )
    assert result["gate_codes"] == ["TOMBSTONE_REPLAY_REQUIRED"]


def test_watermark_that_is_not_an_external_prefix_is_diverged(tmp_path):
    workspace, ledger = _ledger(tmp_path)
    ledger.append_tombstone(completed_tombstone())
    events = ledger.load()
    wrong_head = "f" * 64
    assert wrong_head != events[0].event_sha256
    result = evaluate_ledger_watermark(
        events=events, watermark=Watermark(1, wrong_head)
    )
    assert result["gate_codes"] == ["TOMBSTONE_LEDGER_DIVERGED"]

    ahead = evaluate_ledger_watermark(
        events=events, watermark=Watermark(2, "e" * 64)
    )
    assert ahead["gate_codes"] == ["TOMBSTONE_LEDGER_DIVERGED"]


def test_unsupported_and_corrupt_ledgers_keep_stable_gate_codes(tmp_path):
    workspace, ledger = _ledger(tmp_path)
    ledger.append_tombstone(completed_tombstone())
    raw = ledger.resolved_path.read_text(encoding="utf-8")
    ledger.resolved_path.write_text(
        raw.replace(
            "principal-memory-tombstone-ledger-v2", "future-ledger-v3"
        ),
        encoding="utf-8",
    )
    unsupported = check_principal_memory_ledger_readiness(
        path=ledger.resolved_path,
        workspace=workspace,
        watermark_store=Store(Watermark(0, GENESIS_HEAD_SHA256)),
    )
    assert unsupported["gate_codes"] == [
        "TOMBSTONE_LEDGER_SCHEMA_UNSUPPORTED"
    ]

    ledger.resolved_path.write_text("{\"torn\":true}", encoding="utf-8")
    corrupted = check_principal_memory_ledger_readiness(
        path=ledger.resolved_path,
        workspace=workspace,
        watermark_store=Store(Watermark(0, GENESIS_HEAD_SHA256)),
    )
    assert corrupted["gate_codes"] == ["TOMBSTONE_LEDGER_CORRUPTED"]
