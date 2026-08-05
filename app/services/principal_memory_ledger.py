from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from time import monotonic, sleep
from uuid import uuid4


LEDGER_SCHEMA_VERSION = "principal-memory-tombstone-ledger-v2"
GENESIS_HEAD_SHA256 = "0" * 64
MAX_LEDGER_BYTES = 4_000_000
MAX_LEDGER_EVENTS = 100_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
        raise PrincipalMemoryLedgerError(
            "TOMBSTONE_LEDGER_CORRUPTED"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_CORRUPTED")
    return value


class ProtectedPrincipalMemoryLedger:
    def __init__(
        self,
        path: Path,
        *,
        workspace: Path,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        if lock_timeout_seconds <= 0:
            raise ValueError("ledger lock timeout must be positive")
        self.path = Path(path)
        self.workspace = Path(workspace)
        self.lock_timeout_seconds = lock_timeout_seconds
        self.resolved_path = self._validate_path()
        self.lock_path = self.resolved_path.with_name(
            self.resolved_path.name + ".lock"
        )

    def _validate_path(self) -> Path:
        try:
            if (
                not self.path.is_absolute()
                or not self.workspace.is_absolute()
                or self.path.suffix.lower() != ".jsonl"
                or (os.name == "nt" and str(self.path).startswith("\\\\"))
            ):
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_PATH_INVALID")
            lexical = Path(os.path.abspath(self.path))
            resolved = self.path.resolve(strict=False)
            workspace = self.workspace.resolve(strict=True)
            lexical_key = os.path.normcase(str(lexical))
            resolved_key = os.path.normcase(str(resolved))
            if lexical_key != resolved_key:
                # Symlink/junction traversal weakens host-local durability and
                # can disguise a workspace-contained path as an external one.
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_PATH_INVALID")
            if (
                lexical == workspace
                or workspace in lexical.parents
                or resolved == workspace
                or workspace in resolved.parents
            ):
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_PATH_INVALID")
            if not resolved.parent.is_dir():
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_PATH_INVALID")
            if resolved.exists() and not resolved.is_file():
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_PATH_INVALID")
            return resolved
        except PrincipalMemoryLedgerError:
            raise
        except OSError as exc:
            raise PrincipalMemoryLedgerError(
                "TOMBSTONE_LEDGER_UNWRITABLE"
            ) from exc
        except Exception as exc:
            raise PrincipalMemoryLedgerError(
                "TOMBSTONE_LEDGER_PATH_INVALID"
            ) from exc

    @contextmanager
    def exclusive_lock(self):
        descriptor = None
        acquired = False
        deadline = monotonic() + self.lock_timeout_seconds
        try:
            if self.lock_path.is_symlink():
                raise PrincipalMemoryLedgerError(
                    "TOMBSTONE_LEDGER_PATH_INVALID"
                )
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            if os.fstat(descriptor).st_size < 1:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            while not acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except (BlockingIOError, OSError):
                    if monotonic() >= deadline:
                        raise PrincipalMemoryLedgerError(
                            "TOMBSTONE_LEDGER_LOCK_UNAVAILABLE"
                        )
                    sleep(0.02)
            yield
        finally:
            if descriptor is not None:
                if acquired:
                    try:
                        if os.name == "nt":
                            import msvcrt

                            os.lseek(descriptor, 0, os.SEEK_SET)
                            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(descriptor)

    def probe_writable(self) -> None:
        probe = self.resolved_path.with_name(
            f".{self.resolved_path.name}.{uuid4().hex}.probe"
        )
        descriptor = None
        try:
            descriptor = os.open(
                probe,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            payload = b"principal-memory-ledger-probe-v1\n"
            if os.write(descriptor, payload) != len(payload):
                raise OSError("short ledger probe write")
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            if os.name != "nt":
                directory = os.open(probe.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except Exception as exc:
            raise PrincipalMemoryLedgerError(
                "TOMBSTONE_LEDGER_UNWRITABLE"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                probe.unlink(missing_ok=True)
            except OSError as exc:
                raise PrincipalMemoryLedgerError(
                    "TOMBSTONE_LEDGER_UNWRITABLE"
                ) from exc

    def load(self) -> tuple[PrincipalMemoryLedgerEvent, ...]:
        try:
            if not self.resolved_path.exists():
                return ()
            if self.resolved_path.stat().st_size > MAX_LEDGER_BYTES:
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_CORRUPTED")
            raw = self.resolved_path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_CORRUPTED")
            events: list[PrincipalMemoryLedgerEvent] = []
            previous = GENESIS_HEAD_SHA256
            for index, line in enumerate(raw.splitlines(), start=1):
                if not line:
                    raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_CORRUPTED")
                value = json.loads(line)
                if set(value) != {
                    "schema_version",
                    "event_index",
                    "previous_head_sha256",
                    "event_sha256",
                    "opaque_deployment_ref",
                    "opaque_principal_ref",
                    "deletion_cycle",
                    "completed_at",
                }:
                    raise PrincipalMemoryLedgerError(
                        "TOMBSTONE_LEDGER_CORRUPTED"
                    )
                event = PrincipalMemoryLedgerEvent(**value)
                if event.schema_version != LEDGER_SCHEMA_VERSION:
                    raise PrincipalMemoryLedgerError(
                        "TOMBSTONE_LEDGER_SCHEMA_UNSUPPORTED"
                    )
                if (
                    event.event_index != index
                    or event.previous_head_sha256 != previous
                    or _SHA256.fullmatch(event.opaque_deployment_ref) is None
                    or _SHA256.fullmatch(event.opaque_principal_ref) is None
                    or _SHA256.fullmatch(event.deletion_cycle) is None
                ):
                    raise PrincipalMemoryLedgerError(
                        "TOMBSTONE_LEDGER_CORRUPTED"
                    )
                _validated_timestamp(event.completed_at)
                expected = _event_digest(event.payload_without_digest())
                if event.event_sha256 != expected:
                    raise PrincipalMemoryLedgerError(
                        "TOMBSTONE_LEDGER_CORRUPTED"
                    )
                previous = event.event_sha256
                events.append(event)
                if len(events) > MAX_LEDGER_EVENTS:
                    raise PrincipalMemoryLedgerError(
                        "TOMBSTONE_LEDGER_CORRUPTED"
                    )
            return tuple(events)
        except PrincipalMemoryLedgerError:
            raise
        except OSError as exc:
            raise PrincipalMemoryLedgerError(
                "TOMBSTONE_LEDGER_UNWRITABLE"
            ) from exc
        except Exception as exc:
            raise PrincipalMemoryLedgerError(
                "TOMBSTONE_LEDGER_CORRUPTED"
            ) from exc

    def summary(self) -> PrincipalMemoryLedgerSummary:
        events = self.load()
        return PrincipalMemoryLedgerSummary(
            ledger_schema_version=LEDGER_SCHEMA_VERSION,
            ledger_event_count=len(events),
            ledger_head_sha256=(
                events[-1].event_sha256 if events else GENESIS_HEAD_SHA256
            ),
        )

    def append_tombstone(self, tombstone) -> dict[str, object]:
        if tombstone.status not in {"completed", "replayed"}:
            raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_INVALID_EVENT")
        if tombstone.completed_at is None:
            raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_INVALID_EVENT")
        if (
            tombstone.completed_at.tzinfo is None
            or tombstone.completed_at.utcoffset() is None
        ):
            raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_INVALID_EVENT")
        deployment_ref = opaque_ledger_ref(
            "deployment",
            tombstone.deployment_id,
        )
        principal_ref = opaque_ledger_ref(
            "principal",
            tombstone.deployment_id,
            tombstone.principal_id,
        )
        deletion_cycle = opaque_ledger_ref(
            "deletion-cycle",
            tombstone.tombstone_ref,
        )
        with self.exclusive_lock():
            events = self.load()
            if any(event.deletion_cycle == deletion_cycle for event in events):
                summary = self.summary()
                return {
                    "schema_version": "principal-memory-ledger-append-v2",
                    "status": "completed",
                    "appended": 0,
                    "already_present": 1,
                    **summary.as_dict(),
                }
            previous = (
                events[-1].event_sha256 if events else GENESIS_HEAD_SHA256
            )
            payload = {
                "schema_version": LEDGER_SCHEMA_VERSION,
                "event_index": len(events) + 1,
                "previous_head_sha256": previous,
                "opaque_deployment_ref": deployment_ref,
                "opaque_principal_ref": principal_ref,
                "deletion_cycle": deletion_cycle,
                "completed_at": tombstone.completed_at.isoformat(),
            }
            event = PrincipalMemoryLedgerEvent(
                **payload,
                event_sha256=_event_digest(payload),
            )
            line = _canonical_json(event.as_dict()) + b"\n"
            self._append_line_durably(line)
            verified = self.summary()
            if (
                verified.ledger_event_count != event.event_index
                or verified.ledger_head_sha256 != event.event_sha256
            ):
                raise PrincipalMemoryLedgerError("TOMBSTONE_LEDGER_CORRUPTED")
            return {
                "schema_version": "principal-memory-ledger-append-v2",
                "status": "completed",
                "appended": 1,
                "already_present": 0,
                **verified.as_dict(),
            }

    def _append_line_durably(self, line: bytes) -> None:
        descriptor = None
        try:
            descriptor = os.open(
                self.resolved_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            if os.write(descriptor, line) != len(line):
                raise PrincipalMemoryLedgerError(
                    "TOMBSTONE_LEDGER_UNWRITABLE"
                )
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            if os.name != "nt":
                directory = os.open(self.resolved_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except PrincipalMemoryLedgerError:
            raise
        except OSError as exc:
            raise PrincipalMemoryLedgerError(
                "TOMBSTONE_LEDGER_UNWRITABLE"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
