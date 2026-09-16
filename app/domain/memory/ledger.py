from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json


LEDGER_SCHEMA_VERSION = "principal-memory-tombstone-ledger-v2"
GENESIS_HEAD_SHA256 = "0" * 64
MAX_LEDGER_BYTES = 4_000_000
MAX_LEDGER_EVENTS = 100_000


class PrincipalMemoryLedgerError(RuntimeError):
    def __init__(self, gate_code: str):
        self.gate_code = gate_code
        super().__init__(gate_code)


@dataclass(frozen=True)
class PrincipalMemoryLedgerEvent:
    schema_version: str
    event_index: int
    previous_head_sha256: str
    event_sha256: str
    opaque_deployment_ref: str
    opaque_principal_ref: str
    deletion_cycle: str
    completed_at: str

    def payload_without_digest(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_index": self.event_index,
            "previous_head_sha256": self.previous_head_sha256,
            "opaque_deployment_ref": self.opaque_deployment_ref,
            "opaque_principal_ref": self.opaque_principal_ref,
            "deletion_cycle": self.deletion_cycle,
            "completed_at": self.completed_at,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.payload_without_digest(), "event_sha256": self.event_sha256}


@dataclass(frozen=True)
class PrincipalMemoryLedgerSummary:
    ledger_schema_version: str
    ledger_event_count: int
    ledger_head_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "ledger_schema_version": self.ledger_schema_version,
            "ledger_event_count": self.ledger_event_count,
            "ledger_head_sha256": self.ledger_head_sha256,
        }


def opaque_ledger_ref(domain: str, *values: str) -> str:
    return sha256("\0".join((domain, *values)).encode("utf-8")).hexdigest()


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _event_digest(payload: dict[str, object]) -> str:
    return sha256(_canonical_json(payload)).hexdigest()


def _validated_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_CORRUPTED") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_CORRUPTED")
    return value


__all__ = [
    "GENESIS_HEAD_SHA256",
    "LEDGER_SCHEMA_VERSION",
    "MAX_LEDGER_BYTES",
    "MAX_LEDGER_EVENTS",
    "PrincipalMemoryLedgerError",
    "PrincipalMemoryLedgerEvent",
    "PrincipalMemoryLedgerSummary",
    "opaque_ledger_ref",
]
