from __future__ import annotations

from pathlib import Path

from app.services.principal_memory_ledger import (
    GENESIS_HEAD_SHA256,
    PrincipalMemoryLedgerError,
    PrincipalMemoryLedger,
)


def _blocked(gate_code: str, *, ledger_count=0, watermark_count=0) -> dict:
    return {
        "schema_version": "principal-memory-ledger-readiness-v1",
        "state": "blocked",
        "ready": False,
        "gate_codes": [gate_code],
        "ledger_event_count": int(ledger_count),
        "watermark_event_count": int(watermark_count),
    }


def evaluate_ledger_watermark(*, events, watermark) -> dict:
    """Compare a verified external chain with its database applied prefix.

    The result is content-free: it deliberately omits paths, digest values,
    opaque references, and all Principal Memory locators.
    """

    ledger_count = len(events)
    watermark_count = int(watermark.last_applied_ledger_event_count)
    watermark_head = str(watermark.last_applied_ledger_head_sha256)
    if watermark_count < 0 or watermark_count > ledger_count:
        return _blocked(
            "TOMBSTONE_LEDGER_DIVERGED",
            ledger_count=ledger_count,
            watermark_count=watermark_count,
        )
    expected_prefix_head = (
        GENESIS_HEAD_SHA256
        if watermark_count == 0
        else events[watermark_count - 1].event_sha256
    )
    if watermark_head != expected_prefix_head:
        return _blocked(
            "TOMBSTONE_LEDGER_DIVERGED",
            ledger_count=ledger_count,
            watermark_count=watermark_count,
        )
    if watermark_count < ledger_count:
        return _blocked(
            "TOMBSTONE_REPLAY_REQUIRED",
            ledger_count=ledger_count,
            watermark_count=watermark_count,
        )
    return {
        "schema_version": "principal-memory-ledger-readiness-v1",
        "state": "ready",
        "ready": True,
        "gate_codes": [],
        "ledger_event_count": ledger_count,
        "watermark_event_count": watermark_count,
    }


def check_principal_memory_ledger_readiness(
    *,
    path: str | Path | None,
    workspace: Path,
    watermark_store,
    lock_timeout_seconds: float = 2.0,
) -> dict:
    if path is None or not str(path).strip():
        return _blocked("TOMBSTONE_LEDGER_REQUIRED")
    try:
        ledger = PrincipalMemoryLedger(
            Path(path),
            workspace=workspace,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        ledger.probe_writable()
        with ledger.exclusive_lock():
            events = ledger.load()
            watermark = watermark_store.get()
            return evaluate_ledger_watermark(events=events, watermark=watermark)
    except PrincipalMemoryLedgerError as exc:
        return _blocked(exc.gate_code)
    except Exception:
        return _blocked("TOMBSTONE_LEDGER_DIVERGED")
