from __future__ import annotations

import asyncio
import codecs
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Iterable, Mapping

import httpx

from app.runtime.config.compatibility import derive_pgvector_table_names
from app.services.postgres_identifiers import (
    validate_postgres_identifier,
    validate_runtime_table_prefix,
)
from app.services.t65_provider_http_transport import (
    T65DeepSeekAsyncTransport,
    T65DeepSeekSyncTransport,
    T65ProviderHTTPClients,
    T65ProviderTransportIdentity,
    T65ProviderTransportRejected,
    get_t65_provider_http_clients,
    install_t65_provider_http_clients,
    require_t65_provider_http_registry_available,
    shutdown_t65_provider_http_clients_async,
    shutdown_t65_provider_http_clients_sync,
)


EXECUTOR_CODE_PATHS = tuple(
    sorted(
        (
            "app/api/routes.py",
            "app/graphs/durable_interview_graph.py",
            "app/main.py",
            "app/services/config.py",
            "app/services/decision_store.py",
            "app/services/followup_decision_service.py",
            "app/services/followup_diagnostics.py",
            "app/services/followup_prompts.py",
            "app/services/interview_event_stream.py",
            "app/services/interview_generation_store.py",
            "app/services/llm.py",
            "app/services/postgres_decision_store.py",
            "app/services/postgres_identifiers.py",
            "app/services/provider_usage.py",
            "app/services/runtime.py",
            "app/services/runtime_events.py",
            "app/services/t65_builtin_production_executor.py",
            "app/services/t65_formal_execution_receipt.py",
            "app/services/t65_production_capture.py",
            "app/services/t65_provider_http_transport.py",
            "app/services/t65_runtime_performance.py",
            "scripts/run_t65_runtime_performance.py",
        )
    )
)
_MANIFEST_SCHEMA = "t65-production-executor-code-manifest-v1"
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_T65_PREFIX_RE = re.compile(r"^test_t65perf_[0-9a-f]{12}$")


class T65ProductionCaptureError(RuntimeError):
    """Stable fail-closed error for production-capture construction."""


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class T65ExecutorCodeFile:
    path: str
    raw_sha256: str

    def __post_init__(self) -> None:
        _validate_executor_code_paths((self.path,))
        if _SHA256_RE.fullmatch(self.raw_sha256) is None:
            raise T65ProductionCaptureError("executor file hash must be SHA-256")

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "raw_sha256": self.raw_sha256}


@dataclass(frozen=True)
class T65ExecutorCodeManifest:
    candidate_revision: str
    candidate_tree: str
    files: tuple[T65ExecutorCodeFile, ...]
    executor_sha256: str

    def __post_init__(self) -> None:
        for label, value in (
            ("candidate revision", self.candidate_revision),
            ("candidate tree", self.candidate_tree),
        ):
            if _GIT_OBJECT_RE.fullmatch(value) is None:
                raise T65ProductionCaptureError(f"{label} must be a git object id")
        paths = tuple(item.path for item in self.files)
        _validate_executor_code_paths(paths)
        if paths != EXECUTOR_CODE_PATHS:
            raise T65ProductionCaptureError(
                "executor manifest must exactly cover the fixed executor surface"
            )
        expected = sha256(_canonical_json_bytes(self.canonical_payload())).hexdigest()
        if self.executor_sha256 != expected:
            raise T65ProductionCaptureError(
                "executor manifest hash does not match its canonical payload"
            )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_version": _MANIFEST_SCHEMA,
            "candidate_revision": self.candidate_revision,
            "candidate_tree": self.candidate_tree,
            "files": [item.as_dict() for item in self.files],
        }

    def as_dict(self) -> dict[str, object]:
        return {
            **self.canonical_payload(),
            "executor_sha256": self.executor_sha256,
        }


def build_t65_executor_code_manifest(
    *,
    repository_root: Path,
    candidate_revision: str,
    candidate_tree: str,
    _test_expected_git_blob_sha256s: Mapping[str, str] | None = None,
    _test_expected_candidate_revision: str | None = None,
    _test_expected_candidate_tree: str | None = None,
) -> T65ExecutorCodeManifest:
    """Hash the fixed executor surface after independent blob comparison.

    Production Git trust is intentionally not wired in B2.  Consequently the
    default call fails closed.  The underscored mapping is an offline-test-only
    dependency that stands in for a future module-owned trusted Git object
    reader; it cannot make the current runner formally eligible.
    """

    for label, value in (
        ("candidate revision", candidate_revision),
        ("candidate tree", candidate_tree),
    ):
        if not isinstance(value, str) or _GIT_OBJECT_RE.fullmatch(value) is None:
            raise T65ProductionCaptureError(f"{label} must be a git object id")
    _validate_executor_code_paths(EXECUTOR_CODE_PATHS)
    if _test_expected_git_blob_sha256s is None:
        raise T65ProductionCaptureError(
            "trusted Git blob evidence is unavailable; production manifest is blocked"
        )
    if (
        _test_expected_candidate_revision != candidate_revision
        or _test_expected_candidate_tree != candidate_tree
    ):
        raise T65ProductionCaptureError(
            "requested candidate does not match the frozen trusted candidate"
        )
    expected_blobs = dict(_test_expected_git_blob_sha256s)
    if set(expected_blobs) != set(EXECUTOR_CODE_PATHS):
        raise T65ProductionCaptureError(
            "trusted Git blob evidence must exactly cover the executor surface"
        )
    for relative, digest in expected_blobs.items():
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise T65ProductionCaptureError(
                f"trusted Git blob digest is invalid: {relative}"
            )

    root_input = Path(repository_root).absolute()
    if _path_has_reparse_component(root_input):
        raise T65ProductionCaptureError(
            "repository root must not traverse a symlink or reparse point"
        )
    root = root_input.resolve(strict=True)
    if not root.is_dir():
        raise T65ProductionCaptureError("repository root must be a directory")

    files: list[T65ExecutorCodeFile] = []
    for relative in EXECUTOR_CODE_PATHS:
        candidate = root.joinpath(*relative.split("/"))
        if _path_has_reparse_component(candidate):
            raise T65ProductionCaptureError(
                f"executor code path traverses a symlink or reparse point: {relative}"
            )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, ValueError):
            raise T65ProductionCaptureError(
                f"executor code path is unavailable: {relative}"
            ) from None
        if not resolved.is_file():
            raise T65ProductionCaptureError(
                f"executor code path must be a regular file: {relative}"
            )
        raw_bytes = resolved.read_bytes()
        if _path_has_reparse_component(candidate):
            raise T65ProductionCaptureError(
                f"executor code path changed to a symlink or reparse point: {relative}"
            )
        raw_sha256 = sha256(raw_bytes).hexdigest()
        if raw_sha256 != expected_blobs[relative]:
            raise T65ProductionCaptureError(
                f"workspace file does not match trusted Git blob: {relative}"
            )
        files.append(
            T65ExecutorCodeFile(
                path=relative,
                raw_sha256=raw_sha256,
            )
        )

    payload: dict[str, object] = {
        "schema_version": _MANIFEST_SCHEMA,
        "candidate_revision": candidate_revision,
        "candidate_tree": candidate_tree,
        "files": [item.as_dict() for item in files],
    }
    return T65ExecutorCodeManifest(
        candidate_revision=candidate_revision,
        candidate_tree=candidate_tree,
        files=tuple(files),
        executor_sha256=sha256(_canonical_json_bytes(payload)).hexdigest(),
    )


def _validate_executor_code_paths(paths: tuple[str, ...]) -> None:
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise T65ProductionCaptureError(
            "executor code paths must be unique and canonically sorted"
        )
    for relative in paths:
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or relative.startswith("/")
            or "\x00" in relative
        ):
            raise T65ProductionCaptureError(
                f"executor code path is not canonical: {relative!r}"
            )
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise T65ProductionCaptureError(
                f"executor code path is not canonical: {relative!r}"
            )
        canonical = Path(*parts).as_posix()
        if canonical != relative:
            raise T65ProductionCaptureError(
                f"executor code path is not canonical: {relative!r}"
            )


def _path_has_reparse_component(path: Path) -> bool:
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
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True
    return False


T65ControlledHttpClients = T65ProviderHTTPClients


def install_t65_controlled_http_clients(
    *,
    ledger_directory: Path,
    active_identity: T65ProviderTransportIdentity,
    expected_identity: T65ProviderTransportIdentity,
) -> T65ControlledHttpClients:
    """Install one fail-closed sync/async client pair in the current process."""

    active_identity.validated()
    expected_identity.validated()
    if active_identity != expected_identity:
        raise T65ProviderTransportRejected(
            "active provider identity does not match the authorized identity"
        )
    try:
        require_t65_provider_http_registry_available()
    except T65ProviderTransportRejected as exc:
        raise T65ProductionCaptureError(str(exc)) from exc
    sync_delegate = httpx.HTTPTransport(retries=0)
    try:
        async_delegate = httpx.AsyncHTTPTransport(retries=0)
    except BaseException as exc:
        sync_delegate.close()
        if isinstance(exc, T65ProviderTransportRejected):
            raise T65ProductionCaptureError(str(exc)) from exc
        raise
    try:
        sync_client: httpx.Client | None = None
        async_client: httpx.AsyncClient | None = None
        sync_transport: T65DeepSeekSyncTransport | None = None
        async_transport: T65DeepSeekAsyncTransport | None = None
        sync_transport = T65DeepSeekSyncTransport(
            delegate=sync_delegate,
            ledger_directory=ledger_directory,
            identity=active_identity,
            expected_identity=expected_identity,
        )
        async_transport = T65DeepSeekAsyncTransport(
            delegate=async_delegate,
            ledger_directory=ledger_directory,
            identity=active_identity,
            expected_identity=expected_identity,
        )
        sync_client = httpx.Client(transport=sync_transport, trust_env=False)
        async_client = httpx.AsyncClient(
            transport=async_transport,
            trust_env=False,
        )
        return install_t65_provider_http_clients(
            sync_client=sync_client,
            async_client=async_client,
            identity=active_identity,
        )
    except BaseException as exc:
        if sync_client is None:
            if sync_transport is None:
                sync_delegate.close()
            else:
                sync_transport.close()
        else:
            sync_client.close()
        if async_client is None:
            _close_async_transport_blocking(async_transport or async_delegate)
        else:
            _close_async_transport_blocking(async_client)
        if isinstance(exc, T65ProviderTransportRejected):
            raise T65ProductionCaptureError(str(exc)) from exc
        raise


def _close_async_transport_blocking(transport: httpx.AsyncBaseTransport) -> None:
    """Best-effort cleanup for a partially constructed async client path."""

    errors: list[BaseException] = []

    def close_in_thread() -> None:
        try:
            asyncio.run(transport.aclose())
        except BaseException as exc:
            errors.append(exc)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        close_in_thread()
    else:
        worker = threading.Thread(target=close_in_thread, daemon=False)
        worker.start()
        worker.join()
    # Preserve the construction error that led here. A failed best-effort close
    # must not replace the original fail-closed exception.
    del errors


def get_t65_controlled_http_clients() -> T65ControlledHttpClients:
    try:
        return get_t65_provider_http_clients()
    except T65ProviderTransportRejected as exc:
        raise T65ProductionCaptureError(str(exc)) from exc


def shutdown_t65_controlled_http_clients_sync() -> None:
    shutdown_t65_provider_http_clients_sync()


async def shutdown_t65_controlled_http_clients_async() -> None:
    await shutdown_t65_provider_http_clients_async()


class T65SSEParseError(ValueError):
    """Raised when an SSE byte stream is malformed or incomplete."""


@dataclass(frozen=True)
class T65SSEEvent:
    event: str
    data: str
    event_id: str | None


class T65IncrementalSSEParser:
    """Incrementally parse SSE frames without inventing timing metadata."""

    def __init__(self, *, max_buffer_chars: int = 1_048_576) -> None:
        if max_buffer_chars <= 0:
            raise ValueError("max_buffer_chars must be positive")
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._max_buffer_chars = int(max_buffer_chars)
        self._line_buffer = ""
        self._data_lines: list[str] = []
        self._event_name = "message"
        self._last_event_id: str | None = None
        self._closed = False

    def feed(self, chunk: bytes) -> list[T65SSEEvent]:
        if self._closed:
            raise T65SSEParseError("SSE parser is already closed")
        if not isinstance(chunk, bytes):
            raise TypeError("SSE parser accepts bytes only")
        try:
            self._line_buffer += self._decoder.decode(chunk, final=False)
        except UnicodeDecodeError:
            raise T65SSEParseError("SSE stream is not valid UTF-8") from None
        self._require_within_limit()
        return self._drain_lines(final=False)

    def close(self) -> list[T65SSEEvent]:
        if self._closed:
            return []
        self._closed = True
        try:
            self._line_buffer += self._decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            raise T65SSEParseError("SSE stream ended inside a UTF-8 sequence") from None
        self._require_within_limit()
        events = self._drain_lines(final=True)
        if self._line_buffer or self._data_lines or self._event_name != "message":
            raise T65SSEParseError("SSE stream ended with an incomplete frame")
        return events

    def _drain_lines(self, *, final: bool) -> list[T65SSEEvent]:
        events: list[T65SSEEvent] = []
        while True:
            boundary = self._next_line_boundary(final=final)
            if boundary is None:
                break
            index, width = boundary
            line = self._line_buffer[:index]
            self._line_buffer = self._line_buffer[index + width :]
            event = self._consume_line(line)
            if event is not None:
                events.append(event)
        self._require_within_limit()
        return events

    def _next_line_boundary(self, *, final: bool) -> tuple[int, int] | None:
        for index, character in enumerate(self._line_buffer):
            if character == "\n":
                return index, 1
            if character == "\r":
                if index + 1 == len(self._line_buffer) and not final:
                    return None
                width = 2 if self._line_buffer[index + 1 : index + 2] == "\n" else 1
                return index, width
        return None

    def _consume_line(self, line: str) -> T65SSEEvent | None:
        if line == "":
            if not self._data_lines:
                self._event_name = "message"
                return None
            event = T65SSEEvent(
                event=self._event_name or "message",
                data="\n".join(self._data_lines),
                event_id=self._last_event_id,
            )
            self._data_lines = []
            self._event_name = "message"
            return event
        if line.startswith(":"):
            return None
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data_lines.append(value)
        elif field == "event":
            self._event_name = value
        elif field == "id":
            if "\x00" in value:
                raise T65SSEParseError("SSE event id contains NUL")
            self._last_event_id = value
        return None

    def _require_within_limit(self) -> None:
        buffered = (
            len(self._line_buffer)
            + len(self._event_name)
            + len(self._last_event_id or "")
            + sum(len(item) + 1 for item in self._data_lines)
        )
        if buffered > self._max_buffer_chars:
            raise T65SSEParseError("SSE frame exceeds the configured buffer limit")


@dataclass(frozen=True)
class T65CleanupTargetPlan:
    runtime_prefix: str
    vector_prefix: str
    relations: tuple[str, ...]


def build_t65_cleanup_target_plan(
    *,
    runtime_prefix: str,
    vector_prefix: str,
    discovered_relations: Iterable[str],
) -> T65CleanupTargetPlan:
    """Validate isolated relation names and return a plan without executing SQL."""

    for label, prefix in (
        ("runtime", runtime_prefix),
        ("vector", vector_prefix),
    ):
        if not isinstance(prefix, str) or _SAFE_T65_PREFIX_RE.fullmatch(prefix) is None:
            raise T65ProductionCaptureError(
                f"{label} prefix is not an isolated T65 prefix"
            )
    if runtime_prefix == vector_prefix:
        raise T65ProductionCaptureError("runtime and vector prefixes must be distinct")
    validate_runtime_table_prefix(runtime_prefix)
    vector_relations = frozenset(
        (vector_prefix, *derive_pgvector_table_names(vector_prefix))
    )

    names = tuple(discovered_relations)
    if len(set(names)) != len(names):
        raise T65ProductionCaptureError("cleanup relation list contains duplicates")
    for name in names:
        try:
            validate_postgres_identifier(name)
        except ValueError:
            raise T65ProductionCaptureError(
                "cleanup relation is not a safe PostgreSQL identifier"
            ) from None
        if not (
            name.startswith(runtime_prefix + "_") or name in vector_relations
        ):
            raise T65ProductionCaptureError(
                "cleanup relation escaped the isolated T65 prefixes"
            )
    return T65CleanupTargetPlan(
        runtime_prefix=runtime_prefix,
        vector_prefix=vector_prefix,
        relations=tuple(sorted(names, reverse=True)),
    )
