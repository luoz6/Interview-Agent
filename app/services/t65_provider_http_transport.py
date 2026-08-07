from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
from time import perf_counter_ns, time_ns
from typing import Final, Literal
from uuid import uuid4

import httpx


_SCHEMA_VERSION: Final = "t65-provider-attempt-ledger-v1"
_PROVIDER: Final = "deepseek"
_MODEL: Final = "deepseek-v4-pro"
_ALLOWED_URL: Final = "https://api.deepseek.com/chat/completions"
_ALLOWED_ROLES: Final = frozenset({"api", "report_worker"})
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE: Final = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

_registry_guard = threading.Lock()
_ledger_locks: dict[Path, threading.Lock] = {}
_ledger_sequences: dict[Path, int] = {}
_ledger_last_hashes: dict[Path, str] = {}
_ledger_runtime_states: dict[Path, _LedgerRuntimeState] = {}
_client_pair_guard = threading.Lock()
_client_pair: T65ProviderHTTPClients | None = None


class T65ProviderTransportRejected(ValueError):
    """Raised before delegation when a controlled-provider invariant fails."""


class T65ProviderLedgerRejected(ValueError):
    """Raised when an attempt ledger is incomplete or structurally invalid."""


@dataclass
class _LedgerRuntimeState:
    identity: T65ProviderTransportIdentity
    process_id: int
    writer_count: int = 1
    active_attempts: int = 0
    sealed: bool = False


@dataclass(frozen=True)
class T65ProviderTransportIdentity:
    run_id: str
    process_role: str
    candidate_revision: str
    candidate_tree: str
    authorization_id: str
    authorization_sha256: str
    executor_sha256: str

    def validated(self) -> T65ProviderTransportIdentity:
        if not self.run_id.strip():
            raise T65ProviderTransportRejected("run identity is required")
        if self.process_role not in _ALLOWED_ROLES:
            raise T65ProviderTransportRejected("process role is not allowed")
        for label, value in (
            ("candidate revision", self.candidate_revision),
            ("candidate tree", self.candidate_tree),
        ):
            if not _GIT_OBJECT_RE.fullmatch(value):
                raise T65ProviderTransportRejected(f"{label} must be a git object id")
        for label, value in (
            ("authorization hash", self.authorization_sha256),
            ("executor hash", self.executor_sha256),
        ):
            if not _SHA256_RE.fullmatch(value):
                raise T65ProviderTransportRejected(f"{label} must be a SHA-256 digest")
        if not self.authorization_id.strip():
            raise T65ProviderTransportRejected("authorization identity is required")
        return self


@dataclass(frozen=True)
class T65ProviderAttemptLedgerReceipt:
    schema_version: str
    ledger_sha256: str
    run_id_sha256: str
    candidate_revision_sha256: str
    candidate_tree_sha256: str
    authorization_id_sha256: str
    authorization_sha256: str
    executor_sha256: str
    process_role: str
    process_id: int
    start_count: int
    finish_count: int
    success_count: int
    error_count: int
    sequence_first: int | None
    sequence_last: int | None
    provider_response_id_sha256s: tuple[str, ...]
    response_id_missing_count: int
    duplicate_response_id_count: int
    complete: bool
    failure_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "ledger_sha256": self.ledger_sha256,
            "run_id_sha256": self.run_id_sha256,
            "candidate_revision_sha256": self.candidate_revision_sha256,
            "candidate_tree_sha256": self.candidate_tree_sha256,
            "authorization_id_sha256": self.authorization_id_sha256,
            "authorization_sha256": self.authorization_sha256,
            "executor_sha256": self.executor_sha256,
            "process_role": self.process_role,
            "process_id": self.process_id,
            "start_count": self.start_count,
            "finish_count": self.finish_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "sequence_first": self.sequence_first,
            "sequence_last": self.sequence_last,
            "provider_response_id_sha256s": list(
                self.provider_response_id_sha256s
            ),
            "response_id_missing_count": self.response_id_missing_count,
            "duplicate_response_id_count": self.duplicate_response_id_count,
            "complete": self.complete,
            "failure_code": self.failure_code,
        }


ClientLifecycleState = Literal["ACTIVE", "CLOSING", "CLOSED", "CLOSE_FAILED"]


@dataclass
class T65ProviderHTTPClients:
    sync_client: httpx.Client
    async_client: httpx.AsyncClient
    identity: T65ProviderTransportIdentity
    process_id: int
    sync_transport: T65DeepSeekSyncTransport
    async_transport: T65DeepSeekAsyncTransport
    sync_state: ClientLifecycleState = "ACTIVE"
    async_state: ClientLifecycleState = "ACTIVE"

    @property
    def fully_closed(self) -> bool:
        return self.sync_state == "CLOSED" and self.async_state == "CLOSED"


def install_t65_provider_http_clients(
    *,
    sync_client: httpx.Client,
    async_client: httpx.AsyncClient,
    identity: T65ProviderTransportIdentity,
) -> T65ProviderHTTPClients:
    global _client_pair
    validated_identity = identity.validated()
    if not isinstance(sync_client, httpx.Client) or not isinstance(
        async_client, httpx.AsyncClient
    ):
        raise T65ProviderTransportRejected(
            "formal transport requires both sync and async HTTPX clients"
        )
    sync_transport = getattr(sync_client, "_transport", None)
    async_transport = getattr(async_client, "_transport", None)
    if not isinstance(sync_transport, T65DeepSeekSyncTransport) or not isinstance(
        async_transport, T65DeepSeekAsyncTransport
    ):
        raise T65ProviderTransportRejected(
            "formal clients must own the controlled DeepSeek transports"
        )
    if (
        sync_transport._identity != validated_identity
        or async_transport._identity != validated_identity
    ):
        raise T65ProviderTransportRejected(
            "formal client transport identities do not match"
        )
    if (
        getattr(sync_client, "_trust_env", None) is not False
        or getattr(async_client, "_trust_env", None) is not False
    ):
        raise T65ProviderTransportRejected(
            "formal Provider clients must disable environment trust"
        )
    pair = T65ProviderHTTPClients(
        sync_client=sync_client,
        async_client=async_client,
        identity=validated_identity,
        process_id=os.getpid(),
        sync_transport=sync_transport,
        async_transport=async_transport,
    )
    with _client_pair_guard:
        _discard_inherited_client_pair_locked()
        if _client_pair is not None:
            raise T65ProviderTransportRejected(
                "formal Provider clients are already installed in this process"
            )
        _client_pair = pair
    return pair


def require_t65_provider_http_registry_available() -> None:
    """Reject before constructing resources when this process owns a pair."""
    with _client_pair_guard:
        _discard_inherited_client_pair_locked()
        if _client_pair is not None:
            raise T65ProviderTransportRejected(
                "formal Provider clients are already installed in this process"
            )


def get_t65_provider_http_clients() -> T65ProviderHTTPClients:
    with _client_pair_guard:
        _discard_inherited_client_pair_locked()
        if _client_pair is None:
            raise T65ProviderTransportRejected(
                "formal Provider clients were not installed by the production executor"
            )
        if (
            _client_pair.sync_state != "ACTIVE"
            or _client_pair.async_state != "ACTIVE"
            or _client_pair.sync_client.is_closed
            or _client_pair.async_client.is_closed
        ):
            raise T65ProviderTransportRejected(
                "formal Provider clients are closing or closed"
            )
        return _client_pair


def shutdown_t65_provider_http_clients_sync() -> None:
    with _client_pair_guard:
        _discard_inherited_client_pair_locked()
        clients = _client_pair
        if clients is None or clients.sync_state == "CLOSED":
            return
        if clients.sync_state in {"CLOSING", "CLOSE_FAILED"}:
            raise T65ProviderTransportRejected(
                "formal Provider sync client close is incomplete or failed"
            )
        clients.sync_state = "CLOSING"
    try:
        clients.sync_client.close()
    except BaseException:
        with _client_pair_guard:
            if _client_pair is clients:
                clients.sync_state = "CLOSE_FAILED"
        raise
    with _client_pair_guard:
        if _client_pair is clients:
            clients.sync_state = "CLOSED"
            _clear_fully_closed_pair_locked(clients)


async def shutdown_t65_provider_http_clients_async() -> None:
    with _client_pair_guard:
        _discard_inherited_client_pair_locked()
        clients = _client_pair
        if clients is None or clients.async_state == "CLOSED":
            return
        if clients.async_state in {"CLOSING", "CLOSE_FAILED"}:
            raise T65ProviderTransportRejected(
                "formal Provider async client close is incomplete or failed"
            )
        clients.async_state = "CLOSING"
    try:
        await clients.async_client.aclose()
    except BaseException:
        with _client_pair_guard:
            if _client_pair is clients:
                clients.async_state = "CLOSE_FAILED"
        raise
    with _client_pair_guard:
        if _client_pair is clients:
            clients.async_state = "CLOSED"
            _clear_fully_closed_pair_locked(clients)


def _discard_inherited_client_pair_locked() -> None:
    global _client_pair
    if _client_pair is not None and _client_pair.process_id != os.getpid():
        _client_pair = None


def _clear_fully_closed_pair_locked(clients: T65ProviderHTTPClients) -> None:
    global _client_pair
    if _client_pair is clients and clients.fully_closed:
        _client_pair = None


def _reset_t65_process_state_after_fork() -> None:
    global _client_pair_guard, _client_pair, _registry_guard
    global _ledger_locks, _ledger_sequences, _ledger_last_hashes
    global _ledger_runtime_states
    _client_pair_guard = threading.Lock()
    _client_pair = None
    _registry_guard = threading.Lock()
    _ledger_locks = {}
    _ledger_sequences = {}
    _ledger_last_hashes = {}
    _ledger_runtime_states = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_t65_process_state_after_fork)


def _safe_hash(value: str | bytes) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return sha256(payload).hexdigest()


def _ledger_identity_key(identity: T65ProviderTransportIdentity) -> str:
    payload = {
        "run_id_sha256": _safe_hash(identity.run_id),
        "candidate_revision_sha256": _safe_hash(identity.candidate_revision),
        "candidate_tree_sha256": _safe_hash(identity.candidate_tree),
        "authorization_id_sha256": _safe_hash(identity.authorization_id),
        "authorization_sha256": identity.authorization_sha256,
        "executor_sha256": identity.executor_sha256,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()[:16]


def _canonical_event_sha256(event: dict[str, object]) -> str:
    payload = {key: value for key, value in event.items() if key != "event_sha256"}
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _ledger_lock(path: Path) -> threading.Lock:
    with _registry_guard:
        return _ledger_locks.setdefault(path, threading.Lock())


def _path_has_reparse_component(path: Path) -> bool:
    """Reject symlink/junction traversal before resolving a ledger path."""

    candidate = Path(os.path.abspath(path))
    for component in (candidate, *candidate.parents):
        try:
            component_stat = os.lstat(component)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if stat.S_ISLNK(component_stat.st_mode):
            return True
        attributes = getattr(component_stat, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if attributes & reparse_flag:
            return True
    return False


class _ControlledTransportBase:
    def __init__(
        self,
        *,
        delegate: httpx.BaseTransport | httpx.AsyncBaseTransport,
        ledger_directory: Path,
        identity: T65ProviderTransportIdentity,
        expected_identity: T65ProviderTransportIdentity,
    ) -> None:
        self._delegate = delegate
        self._identity = identity.validated()
        if self._identity != expected_identity.validated():
            raise T65ProviderTransportRejected(
                "active provider identity does not match the authorized identity"
            )
        self._pid = os.getpid()
        configured_directory = Path(ledger_directory)
        if _path_has_reparse_component(configured_directory):
            raise T65ProviderTransportRejected(
                "ledger directory must not traverse a reparse point"
            )
        directory = configured_directory.resolve(strict=False)
        directory.mkdir(parents=True, exist_ok=True)
        if _path_has_reparse_component(directory) or not directory.is_dir():
            raise T65ProviderTransportRejected("ledger directory must be a real directory")
        self.ledger_path = directory / (
            f"provider-attempts-{self._identity.process_role}-{self._pid}-"
            f"{_ledger_identity_key(self._identity)}.jsonl"
        )
        if _path_has_reparse_component(self.ledger_path):
            raise T65ProviderTransportRejected("ledger path must not be a reparse point")
        self._lock = _ledger_lock(self.ledger_path)
        self._writer_registered = False
        with self._lock:
            runtime_state = _ledger_runtime_states.get(self.ledger_path)
            if runtime_state is not None:
                if (
                    runtime_state.identity != self._identity
                    or runtime_state.process_id != self._pid
                    or runtime_state.sealed
                ):
                    raise T65ProviderTransportRejected(
                        "Provider attempt ledger runtime identity does not match"
                    )
                if not self.ledger_path.exists() or self.ledger_path.stat().st_size != 0:
                    raise T65ProviderTransportRejected(
                        "shared Provider attempt ledger changed during initialization"
                    )
                runtime_state.writer_count += 1
            else:
                if self.ledger_path.exists():
                    raise T65ProviderTransportRejected(
                        "pre-existing Provider attempt ledger is not reusable"
                    )
                descriptor = os.open(
                    self.ledger_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                try:
                    descriptor_stat = os.fstat(descriptor)
                    path_stat = os.lstat(self.ledger_path)
                    if (
                        _path_has_reparse_component(self.ledger_path)
                        or not stat.S_ISREG(descriptor_stat.st_mode)
                        or not os.path.samestat(descriptor_stat, path_stat)
                    ):
                        raise T65ProviderTransportRejected(
                            "Provider attempt ledger creation identity changed"
                        )
                finally:
                    os.close(descriptor)
                _ledger_runtime_states[self.ledger_path] = _LedgerRuntimeState(
                    identity=self._identity,
                    process_id=self._pid,
                )
            _ledger_sequences.setdefault(self.ledger_path, 0)
            _ledger_last_hashes.setdefault(self.ledger_path, "0" * 64)
            self._writer_registered = True

    def _validate_request(self, request: httpx.Request, body: bytes) -> None:
        if os.getpid() != self._pid:
            raise T65ProviderTransportRejected(
                "transport must be constructed in the sending process"
            )
        if request.method != "POST" or str(request.url) != _ALLOWED_URL:
            raise T65ProviderTransportRejected("provider endpoint is not allowed")
        try:
            payload = json.loads(body)
        except (TypeError, ValueError, UnicodeDecodeError):
            raise T65ProviderTransportRejected("provider request must be JSON") from None
        if not isinstance(payload, dict) or payload.get("model") != _MODEL:
            raise T65ProviderTransportRejected("provider model is not allowed")

    def _start_attempt(self, body: bytes) -> tuple[str, int, int]:
        attempt_id = uuid4().hex
        started_monotonic_ns = perf_counter_ns()
        with self._lock:
            state = self._runtime_state_locked()
            if not self._writer_registered or state.sealed:
                raise T65ProviderTransportRejected("Provider attempt ledger is sealed")
            sequence = _ledger_sequences.get(self.ledger_path, 0) + 1
            self._append_locked(
                {
                    **self._common_event(attempt_id, sequence),
                    "event": "ATTEMPT_START",
                    "started_at_unix_ns": time_ns(),
                    "request_body_sha256": _safe_hash(body),
                    "status": "delegating",
                }
            )
            _ledger_sequences[self.ledger_path] = sequence
            state.active_attempts += 1
        return attempt_id, sequence, started_monotonic_ns

    def _finish_attempt(
        self,
        *,
        attempt_id: str,
        sequence: int,
        started_monotonic_ns: int,
        response: httpx.Response | None = None,
        error: BaseException | None = None,
    ) -> None:
        event = {
            **self._common_event(attempt_id, sequence),
            "event": "ATTEMPT_FINISH",
            "finished_at_unix_ns": time_ns(),
            "latency_ms": round((perf_counter_ns() - started_monotonic_ns) / 1_000_000, 3),
        }
        if error is not None:
            event.update(
                status="delegate_error",
                error_class_sha256=_safe_hash(
                    f"{type(error).__module__}.{type(error).__qualname__}"
                ),
            )
        else:
            assert response is not None
            event.update(status="response", http_status=int(response.status_code))
            response_id = next(
                (
                    response.headers.get(name)
                    for name in ("x-request-id", "request-id", "x-ds-request-id")
                    if response.headers.get(name)
                ),
                None,
            )
            if response_id is not None:
                event["provider_response_id_sha256"] = _safe_hash(response_id)
        with self._lock:
            self._append_locked(event)
            state = self._runtime_state_locked()
            if state.active_attempts < 1:
                raise T65ProviderTransportRejected(
                    "Provider attempt ledger active count is invalid"
                )
            state.active_attempts -= 1
            self._maybe_seal_locked(state)

    def _runtime_state_locked(self) -> _LedgerRuntimeState:
        state = _ledger_runtime_states.get(self.ledger_path)
        if (
            state is None
            or state.identity != self._identity
            or state.process_id != self._pid
        ):
            raise T65ProviderTransportRejected(
                "Provider attempt ledger runtime state is unavailable"
            )
        return state

    def _common_identity_event(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "process_id": self._pid,
            "process_role": self._identity.process_role,
            "run_id_sha256": _safe_hash(self._identity.run_id),
            "candidate_revision_sha256": _safe_hash(self._identity.candidate_revision),
            "candidate_tree_sha256": _safe_hash(self._identity.candidate_tree),
            "authorization_id_sha256": _safe_hash(self._identity.authorization_id),
            "authorization_sha256": self._identity.authorization_sha256,
            "executor_sha256": self._identity.executor_sha256,
            "provider": _PROVIDER,
            "model": _MODEL,
            "endpoint": _ALLOWED_URL,
        }

    def _common_event(self, attempt_id: str, sequence: int) -> dict[str, object]:
        return {
            **self._common_identity_event(),
            "attempt_id": attempt_id,
            "attempt_sequence": sequence,
        }

    def _maybe_seal_locked(self, state: _LedgerRuntimeState) -> None:
        if state.writer_count == 0 and state.active_attempts == 0 and not state.sealed:
            self._append_locked(
                {
                    **self._common_identity_event(),
                    "event": "LEDGER_SEAL",
                    "status": "sealed",
                    "sealed_at_unix_ns": time_ns(),
                }
            )
            state.sealed = True

    def _unregister_writer(self) -> None:
        with self._lock:
            if not self._writer_registered:
                return
            state = self._runtime_state_locked()
            if state.writer_count < 1:
                raise T65ProviderTransportRejected(
                    "Provider attempt ledger writer count is invalid"
                )
            state.writer_count -= 1
            self._writer_registered = False
            self._maybe_seal_locked(state)

    def _append_locked(self, event: dict[str, object]) -> None:
        if _path_has_reparse_component(self.ledger_path):
            raise T65ProviderTransportRejected(
                "Provider attempt ledger path traverses a reparse point"
            )
        event["previous_event_sha256"] = _ledger_last_hashes[self.ledger_path]
        event["event_sha256"] = _canonical_event_sha256(event)
        encoded = (
            json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        flags = (
            os.O_APPEND
            | os.O_CREAT
            | os.O_WRONLY
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(self.ledger_path, flags, 0o600)
        try:
            descriptor_stat = os.fstat(descriptor)
            path_stat = os.lstat(self.ledger_path)
            if (
                _path_has_reparse_component(self.ledger_path)
                or not stat.S_ISREG(descriptor_stat.st_mode)
                or not os.path.samestat(descriptor_stat, path_stat)
            ):
                raise T65ProviderTransportRejected(
                    "Provider attempt ledger file identity changed"
                )
            with os.fdopen(descriptor, "ab", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        _ledger_last_hashes[self.ledger_path] = str(event["event_sha256"])


def _read_ledger_bytes_secure(path: Path) -> bytes:
    if _path_has_reparse_component(path):
        raise T65ProviderLedgerRejected("LEDGER_REPARSE_POINT_PROHIBITED")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise T65ProviderLedgerRejected("LEDGER_UNAVAILABLE") from exc
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = os.lstat(path)
        if (
            _path_has_reparse_component(path)
            or not stat.S_ISREG(descriptor_stat.st_mode)
            or not os.path.samestat(descriptor_stat, path_stat)
        ):
            raise T65ProviderLedgerRejected("LEDGER_FILE_IDENTITY_CHANGED")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    except OSError as exc:
        raise T65ProviderLedgerRejected("LEDGER_UNAVAILABLE") from exc
    finally:
        os.close(descriptor)


def verify_t65_provider_attempt_ledger(
    ledger_path: Path,
    *,
    expected_identity: T65ProviderTransportIdentity,
    expected_process_id: int,
) -> T65ProviderAttemptLedgerReceipt:
    identity = expected_identity.validated()
    path = Path(ledger_path)
    with _ledger_lock(path):
        raw = _read_ledger_bytes_secure(path)
    ledger_sha256 = sha256(raw).hexdigest()
    if raw and not raw.endswith(b"\n"):
        raise T65ProviderLedgerRejected("LEDGER_TRUNCATED")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise T65ProviderLedgerRejected("LEDGER_NOT_UTF8") from exc

    expected_common = {
        "schema_version": _SCHEMA_VERSION,
        "process_id": expected_process_id,
        "process_role": identity.process_role,
        "run_id_sha256": _safe_hash(identity.run_id),
        "candidate_revision_sha256": _safe_hash(identity.candidate_revision),
        "candidate_tree_sha256": _safe_hash(identity.candidate_tree),
        "authorization_id_sha256": _safe_hash(identity.authorization_id),
        "authorization_sha256": identity.authorization_sha256,
        "executor_sha256": identity.executor_sha256,
        "provider": _PROVIDER,
        "model": _MODEL,
        "endpoint": _ALLOWED_URL,
    }
    previous_hash = "0" * 64
    starts: dict[int, str] = {}
    finished: set[int] = set()
    success_count = error_count = 0
    response_ids: set[str] = set()
    response_id_missing_count = 0
    seal_seen = False
    for index, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise T65ProviderLedgerRejected("LEDGER_EVENT_INVALID_JSON") from exc
        if not isinstance(event, dict):
            raise T65ProviderLedgerRejected("LEDGER_EVENT_NOT_OBJECT")
        if event.get("previous_event_sha256") != previous_hash:
            raise T65ProviderLedgerRejected("LEDGER_HASH_CHAIN_BROKEN")
        event_hash = event.get("event_sha256")
        if not isinstance(event_hash, str) or event_hash != _canonical_event_sha256(event):
            raise T65ProviderLedgerRejected("LEDGER_EVENT_HASH_MISMATCH")
        previous_hash = event_hash
        if any(event.get(key) != value for key, value in expected_common.items()):
            raise T65ProviderLedgerRejected("LEDGER_IDENTITY_MISMATCH")
        event_type = event.get("event")
        if event_type == "LEDGER_SEAL":
            if seal_seen or index != len(lines):
                raise T65ProviderLedgerRejected("LEDGER_SEAL_NOT_TERMINAL")
            if set(event) != set(expected_common) | {
                "event", "status", "sealed_at_unix_ns",
                "previous_event_sha256", "event_sha256",
            }:
                raise T65ProviderLedgerRejected("LEDGER_EVENT_FIELDS_INVALID")
            if (
                event.get("status") != "sealed"
                or not isinstance(event.get("sealed_at_unix_ns"), int)
                or isinstance(event.get("sealed_at_unix_ns"), bool)
                or len(finished) != len(starts)
            ):
                raise T65ProviderLedgerRejected("LEDGER_SEAL_INVALID")
            seal_seen = True
            continue
        if seal_seen:
            raise T65ProviderLedgerRejected("LEDGER_EVENT_AFTER_SEAL")
        attempt_id = event.get("attempt_id")
        sequence = event.get("attempt_sequence")
        if not isinstance(attempt_id, str) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
            raise T65ProviderLedgerRejected("LEDGER_ATTEMPT_ID_INVALID")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise T65ProviderLedgerRejected("LEDGER_SEQUENCE_INVALID")
        common_keys = set(expected_common) | {
            "attempt_id",
            "attempt_sequence",
            "event",
            "status",
            "previous_event_sha256",
            "event_sha256",
        }
        if event_type == "ATTEMPT_START":
            if set(event) != common_keys | {
                "started_at_unix_ns",
                "request_body_sha256",
            }:
                raise T65ProviderLedgerRejected("LEDGER_EVENT_FIELDS_INVALID")
            if sequence != len(starts) + 1 or sequence in starts:
                raise T65ProviderLedgerRejected("LEDGER_START_SEQUENCE_INVALID")
            if event.get("status") != "delegating" or not _SHA256_RE.fullmatch(
                str(event.get("request_body_sha256", ""))
            ):
                raise T65ProviderLedgerRejected("LEDGER_START_INVALID")
            if not isinstance(event.get("started_at_unix_ns"), int) or isinstance(
                event.get("started_at_unix_ns"), bool
            ):
                raise T65ProviderLedgerRejected("LEDGER_START_INVALID")
            starts[sequence] = attempt_id
        elif event_type == "ATTEMPT_FINISH":
            if sequence not in starts or starts[sequence] != attempt_id:
                raise T65ProviderLedgerRejected("LEDGER_ORPHAN_FINISH")
            if sequence in finished:
                raise T65ProviderLedgerRejected("LEDGER_DUPLICATE_FINISH")
            status = event.get("status")
            if (
                not isinstance(event.get("finished_at_unix_ns"), int)
                or isinstance(event.get("finished_at_unix_ns"), bool)
                or not isinstance(event.get("latency_ms"), (int, float))
                or isinstance(event.get("latency_ms"), bool)
                or event["latency_ms"] < 0
            ):
                raise T65ProviderLedgerRejected("LEDGER_FINISH_INVALID")
            if status == "response":
                allowed = common_keys | {
                    "finished_at_unix_ns",
                    "latency_ms",
                    "http_status",
                }
                if "provider_response_id_sha256" in event:
                    allowed.add("provider_response_id_sha256")
                if set(event) != allowed:
                    raise T65ProviderLedgerRejected("LEDGER_EVENT_FIELDS_INVALID")
                if not isinstance(event.get("http_status"), int) or isinstance(
                    event.get("http_status"), bool
                ):
                    raise T65ProviderLedgerRejected("LEDGER_FINISH_INVALID")
                response_id = event.get("provider_response_id_sha256")
                if response_id is not None:
                    if not isinstance(response_id, str) or not _SHA256_RE.fullmatch(response_id):
                        raise T65ProviderLedgerRejected("LEDGER_RESPONSE_ID_INVALID")
                    if response_id in response_ids:
                        raise T65ProviderLedgerRejected("LEDGER_DUPLICATE_RESPONSE_ID")
                    response_ids.add(response_id)
                else:
                    response_id_missing_count += 1
                if 200 <= event["http_status"] < 300:
                    success_count += 1
                else:
                    error_count += 1
            elif status == "delegate_error":
                if set(event) != common_keys | {
                    "finished_at_unix_ns",
                    "latency_ms",
                    "error_class_sha256",
                }:
                    raise T65ProviderLedgerRejected("LEDGER_EVENT_FIELDS_INVALID")
                error_hash = event.get("error_class_sha256")
                if not isinstance(error_hash, str) or not _SHA256_RE.fullmatch(error_hash):
                    raise T65ProviderLedgerRejected("LEDGER_ERROR_INVALID")
                error_count += 1
            else:
                raise T65ProviderLedgerRejected("LEDGER_FINISH_INVALID")
            finished.add(sequence)
        else:
            raise T65ProviderLedgerRejected("LEDGER_EVENT_TYPE_INVALID")
    if len(finished) != len(starts):
        raise T65ProviderLedgerRejected("LEDGER_ORPHAN_START")
    if not seal_seen:
        raise T65ProviderLedgerRejected("LEDGER_NOT_SEALED")
    if not starts:
        raise T65ProviderLedgerRejected("LEDGER_NO_ATTEMPTS")
    sequence_first = 1 if starts else None
    sequence_last = len(starts) if starts else None
    return T65ProviderAttemptLedgerReceipt(
        schema_version="t65-provider-attempt-ledger-receipt-v1",
        ledger_sha256=ledger_sha256,
        run_id_sha256=expected_common["run_id_sha256"],
        candidate_revision_sha256=expected_common["candidate_revision_sha256"],
        candidate_tree_sha256=expected_common["candidate_tree_sha256"],
        authorization_id_sha256=expected_common["authorization_id_sha256"],
        authorization_sha256=identity.authorization_sha256,
        executor_sha256=identity.executor_sha256,
        process_role=identity.process_role,
        process_id=expected_process_id,
        start_count=len(starts),
        finish_count=len(finished),
        success_count=success_count,
        error_count=error_count,
        sequence_first=sequence_first,
        sequence_last=sequence_last,
        provider_response_id_sha256s=tuple(sorted(response_ids)),
        response_id_missing_count=response_id_missing_count,
        duplicate_response_id_count=0,
        complete=True,
    )


def finalize_t65_provider_attempt_ledger(
    ledger_path: Path,
    *,
    expected_identity: T65ProviderTransportIdentity,
    expected_process_id: int,
) -> T65ProviderAttemptLedgerReceipt:
    try:
        return verify_t65_provider_attempt_ledger(
            ledger_path,
            expected_identity=expected_identity,
            expected_process_id=expected_process_id,
        )
    except T65ProviderLedgerRejected as exc:
        identity = expected_identity.validated()
        try:
            ledger_sha256 = sha256(
                _read_ledger_bytes_secure(Path(ledger_path))
            ).hexdigest()
        except T65ProviderLedgerRejected:
            ledger_sha256 = "0" * 64
        return T65ProviderAttemptLedgerReceipt(
            schema_version="t65-provider-attempt-ledger-receipt-v1",
            ledger_sha256=ledger_sha256,
            run_id_sha256=_safe_hash(identity.run_id),
            candidate_revision_sha256=_safe_hash(identity.candidate_revision),
            candidate_tree_sha256=_safe_hash(identity.candidate_tree),
            authorization_id_sha256=_safe_hash(identity.authorization_id),
            authorization_sha256=identity.authorization_sha256,
            executor_sha256=identity.executor_sha256,
            process_role=identity.process_role,
            process_id=expected_process_id,
            start_count=0,
            finish_count=0,
            success_count=0,
            error_count=0,
            sequence_first=None,
            sequence_last=None,
            provider_response_id_sha256s=(),
            response_id_missing_count=0,
            duplicate_response_id_count=0,
            complete=False,
            failure_code=str(exc),
        )


class T65DeepSeekSyncTransport(_ControlledTransportBase, httpx.BaseTransport):
    """Fail-closed sync HTTPX transport for controlled T65 Provider attempts."""

    def __init__(
        self,
        *,
        delegate: httpx.BaseTransport,
        ledger_directory: Path,
        identity: T65ProviderTransportIdentity,
        expected_identity: T65ProviderTransportIdentity,
    ) -> None:
        super().__init__(
            delegate=delegate,
            ledger_directory=ledger_directory,
            identity=identity,
            expected_identity=expected_identity,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        self._validate_request(request, body)
        attempt_id, sequence, started = self._start_attempt(body)
        try:
            response = self._delegate.handle_request(request)
        except BaseException as exc:
            self._finish_attempt(
                attempt_id=attempt_id,
                sequence=sequence,
                started_monotonic_ns=started,
                error=exc,
            )
            raise
        self._finish_attempt(
            attempt_id=attempt_id,
            sequence=sequence,
            started_monotonic_ns=started,
            response=response,
        )
        return response

    def close(self) -> None:
        self._delegate.close()
        self._unregister_writer()


class T65DeepSeekAsyncTransport(
    _ControlledTransportBase, httpx.AsyncBaseTransport
):
    """Fail-closed async HTTPX transport for controlled T65 Provider attempts."""

    def __init__(
        self,
        *,
        delegate: httpx.AsyncBaseTransport,
        ledger_directory: Path,
        identity: T65ProviderTransportIdentity,
        expected_identity: T65ProviderTransportIdentity,
    ) -> None:
        super().__init__(
            delegate=delegate,
            ledger_directory=ledger_directory,
            identity=identity,
            expected_identity=expected_identity,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self._validate_request(request, body)
        attempt_id, sequence, started = self._start_attempt(body)
        try:
            response = await self._delegate.handle_async_request(request)
        except BaseException as exc:
            self._finish_attempt(
                attempt_id=attempt_id,
                sequence=sequence,
                started_monotonic_ns=started,
                error=exc,
            )
            raise
        self._finish_attempt(
            attempt_id=attempt_id,
            sequence=sequence,
            started_monotonic_ns=started,
            response=response,
        )
        return response

    async def aclose(self) -> None:
        await self._delegate.aclose()
        self._unregister_writer()
