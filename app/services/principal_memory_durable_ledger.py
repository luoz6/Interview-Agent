from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.services.principal_memory_ledger import (
    PrincipalMemoryLedgerError,
    ProtectedPrincipalMemoryLedger,
    opaque_ledger_ref,
)
from app.services.principal_memory_ledger_readiness import (
    check_principal_memory_ledger_readiness,
    evaluate_ledger_watermark,
)


class PrincipalMemoryDurableLedger:
    """Coordinate external deletion truth with the PostgreSQL applied prefix."""

    def __init__(
        self,
        *,
        path: str | Path,
        workspace: Path,
        watermark_store,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        self.ledger = ProtectedPrincipalMemoryLedger(
            Path(path),
            workspace=workspace,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self.watermark_store = watermark_store

    def readiness(self) -> dict:
        return check_principal_memory_ledger_readiness(
            path=self.ledger.resolved_path,
            workspace=self.ledger.workspace,
            watermark_store=self.watermark_store,
            lock_timeout_seconds=self.ledger.lock_timeout_seconds,
        )

    def require_ready(self) -> None:
        result = self.readiness()
        if not result["ready"]:
            raise PrincipalMemoryLedgerError(result["gate_codes"][0])

    def append_completed(self, tombstone) -> dict[str, object]:
        return self.ledger.append_tombstone(tombstone)

    def mark_applied(self, tombstone, receipt) -> dict[str, object]:
        deletion_cycle = opaque_ledger_ref(
            "deletion-cycle", tombstone.tombstone_ref
        )
        with self.ledger.exclusive_lock():
            events = self.ledger.load()
            matching = [
                event
                for event in events
                if event.deletion_cycle == deletion_cycle
            ]
            if len(matching) != 1:
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_DIVERGED")
            event = matching[0]
            try:
                receipt_count = int(receipt["ledger_event_count"])
            except (KeyError, TypeError, ValueError) as exc:
                raise PrincipalMemoryLedgerError(
                    "TOMBSTONE_LEDGER_DIVERGED"
                ) from exc
            if receipt_count < event.event_index:
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_DIVERGED")
            watermark = self.watermark_store.get()
            if watermark.last_applied_ledger_event_count >= event.event_index:
                current = evaluate_ledger_watermark(
                    events=events, watermark=watermark
                )
                if not current["ready"]:
                    raise PrincipalMemoryLedgerError(
                        current["gate_codes"][0]
                    )
                return {
                    "schema_version": "principal-memory-ledger-watermark-advance-v1",
                    "status": "already_applied",
                    "advanced": 0,
                }
            expected_count = event.event_index - 1
            if watermark.last_applied_ledger_event_count != expected_count:
                raise PrincipalMemoryLedgerError("TOMBSTONE_REPLAY_REQUIRED")
            expected_head = event.previous_head_sha256
            if watermark.last_applied_ledger_head_sha256 != expected_head:
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_DIVERGED")
            applied_at = datetime.fromisoformat(
                event.completed_at.replace("Z", "+00:00")
            )
            self.watermark_store.advance(
                expected_event_count=expected_count,
                expected_head_sha256=expected_head,
                new_event_count=event.event_index,
                new_head_sha256=event.event_sha256,
                applied_at=applied_at,
            )
            return {
                "schema_version": "principal-memory-ledger-watermark-advance-v1",
                "status": "completed",
                "advanced": 1,
                "ledger_event_count": receipt_count,
            }
