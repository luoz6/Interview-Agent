from __future__ import annotations

import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path

import httpx
import pytest

from app.services import t65_production_capture as capture
import app.services.t65_provider_http_transport as transport_registry
from app.services.t65_production_capture import (
    EXECUTOR_CODE_PATHS,
    T65IncrementalSSEParser,
    T65ProductionCaptureError,
    T65SSEParseError,
    build_t65_cleanup_target_plan,
    build_t65_executor_code_manifest,
    get_t65_controlled_http_clients,
    install_t65_controlled_http_clients,
    shutdown_t65_controlled_http_clients_async,
    shutdown_t65_controlled_http_clients_sync,
)
from app.services.t65_provider_http_transport import (
    T65ProviderTransportIdentity,
    T65ProviderTransportRejected,
)


def _identity(**overrides) -> T65ProviderTransportIdentity:
    values = {
        "run_id": "t65-run",
        "process_role": "api",
        "candidate_revision": "a" * 40,
        "candidate_tree": "b" * 40,
        "authorization_id": "authorization-v1",
        "authorization_sha256": "c" * 64,
        "executor_sha256": "d" * 64,
    }
    values.update(overrides)
    return T65ProviderTransportIdentity(**values)


def _write_executor_surface(root: Path) -> None:
    for index, relative in enumerate(EXECUTOR_CODE_PATHS):
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"raw-file-{index}\r\n".encode("utf-8"))


def _trusted_git_blob_fixture(root: Path) -> dict[str, str]:
    return {
        relative: sha256(root.joinpath(*relative.split("/")).read_bytes()).hexdigest()
        for relative in EXECUTOR_CODE_PATHS
    }


def _build_executor_manifest(root: Path, **updates):
    values = {
        "repository_root": root,
        "candidate_revision": "a" * 40,
        "candidate_tree": "b" * 40,
        "_test_expected_git_blob_sha256s": _trusted_git_blob_fixture(root),
        "_test_expected_candidate_revision": "a" * 40,
        "_test_expected_candidate_tree": "b" * 40,
    }
    values.update(updates)
    return build_t65_executor_code_manifest(**values)


def test_executor_manifest_hashes_fixed_sorted_raw_file_surface(tmp_path):
    _write_executor_surface(tmp_path)
    manifest = _build_executor_manifest(tmp_path)

    assert tuple(item.path for item in manifest.files) == EXECUTOR_CODE_PATHS
    assert EXECUTOR_CODE_PATHS == tuple(sorted(EXECUTOR_CODE_PATHS))
    for index, item in enumerate(manifest.files):
        assert item.raw_sha256 == sha256(
            f"raw-file-{index}\r\n".encode("utf-8")
        ).hexdigest()
    payload = manifest.canonical_payload()
    expected = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert manifest.executor_sha256 == expected
    assert manifest.as_dict()["executor_sha256"] == expected


def test_executor_manifest_binds_candidate_and_raw_bytes(tmp_path):
    _write_executor_surface(tmp_path)
    first = _build_executor_manifest(tmp_path)
    frozen_blobs = _trusted_git_blob_fixture(tmp_path)
    changed_path = tmp_path.joinpath(*EXECUTOR_CODE_PATHS[0].split("/"))
    changed_path.write_bytes(changed_path.read_bytes() + b"\n")
    with pytest.raises(T65ProductionCaptureError, match="trusted Git blob"):
        _build_executor_manifest(
            tmp_path, _test_expected_git_blob_sha256s=frozen_blobs
        )
    changed_file = _build_executor_manifest(tmp_path)
    with pytest.raises(T65ProductionCaptureError, match="frozen trusted candidate"):
        _build_executor_manifest(tmp_path, candidate_revision="e" * 40)
    changed_candidate = _build_executor_manifest(
        tmp_path,
        candidate_revision="e" * 40,
        _test_expected_candidate_revision="e" * 40,
    )

    assert first.executor_sha256 != changed_file.executor_sha256
    assert changed_file.executor_sha256 != changed_candidate.executor_sha256


def test_executor_manifest_rejects_missing_or_invalid_identity(tmp_path):
    _write_executor_surface(tmp_path)
    trusted_blobs = _trusted_git_blob_fixture(tmp_path)
    missing = tmp_path.joinpath(*EXECUTOR_CODE_PATHS[-1].split("/"))
    missing.unlink()
    with pytest.raises(T65ProductionCaptureError, match="unavailable"):
        build_t65_executor_code_manifest(
            repository_root=tmp_path,
            candidate_revision="a" * 40,
            candidate_tree="b" * 40,
            _test_expected_git_blob_sha256s={
                **trusted_blobs,
                EXECUTOR_CODE_PATHS[-1]: "0" * 64,
            },
            _test_expected_candidate_revision="a" * 40,
            _test_expected_candidate_tree="b" * 40,
        )
    with pytest.raises(T65ProductionCaptureError, match="git object"):
        build_t65_executor_code_manifest(
            repository_root=tmp_path,
            candidate_revision="not-a-revision",
            candidate_tree="b" * 40,
            _test_expected_git_blob_sha256s={},
            _test_expected_candidate_revision="not-a-revision",
            _test_expected_candidate_tree="b" * 40,
        )


def test_executor_manifest_fails_closed_without_production_git_trust(tmp_path):
    _write_executor_surface(tmp_path)
    with pytest.raises(T65ProductionCaptureError, match="production manifest is blocked"):
        build_t65_executor_code_manifest(
            repository_root=tmp_path,
            candidate_revision="a" * 40,
            candidate_tree="b" * 40,
        )


def test_executor_manifest_requires_exact_valid_trusted_blob_surface(tmp_path):
    _write_executor_surface(tmp_path)
    blobs = _trusted_git_blob_fixture(tmp_path)
    blobs.pop(EXECUTOR_CODE_PATHS[0])
    with pytest.raises(T65ProductionCaptureError, match="exactly cover"):
        _build_executor_manifest(tmp_path, _test_expected_git_blob_sha256s=blobs)

    blobs = _trusted_git_blob_fixture(tmp_path)
    blobs[EXECUTOR_CODE_PATHS[0]] = "A" * 64
    with pytest.raises(T65ProductionCaptureError, match="digest is invalid"):
        _build_executor_manifest(tmp_path, _test_expected_git_blob_sha256s=blobs)


@pytest.mark.parametrize(
    "paths, message",
    [
        (("app/main.py", "app/main.py"), "unique"),
        (("z.py", "a.py"), "sorted"),
        (("../escape.py",), "canonical"),
        (("app\\main.py",), "canonical"),
        (("/absolute.py",), "canonical"),
    ],
)
def test_executor_manifest_rejects_noncanonical_duplicate_or_unsorted_surface(
    tmp_path, monkeypatch, paths, message
):
    monkeypatch.setattr(capture, "EXECUTOR_CODE_PATHS", paths)
    with pytest.raises(T65ProductionCaptureError, match=message):
        build_t65_executor_code_manifest(
            repository_root=tmp_path,
            candidate_revision="a" * 40,
            candidate_tree="b" * 40,
            _test_expected_git_blob_sha256s={path: "0" * 64 for path in paths},
            _test_expected_candidate_revision="a" * 40,
            _test_expected_candidate_tree="b" * 40,
        )


def test_executor_manifest_rejects_symlinked_file_surface(tmp_path):
    _write_executor_surface(tmp_path)
    relative = EXECUTOR_CODE_PATHS[0]
    target = tmp_path.joinpath(*relative.split("/"))
    external = tmp_path / "external.py"
    external.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(T65ProductionCaptureError, match="symlink or reparse"):
        _build_executor_manifest(tmp_path)


def test_executor_manifest_rejects_reparse_detection_before_read(tmp_path, monkeypatch):
    _write_executor_surface(tmp_path)
    root = tmp_path.absolute()
    monkeypatch.setattr(
        capture,
        "_path_has_reparse_component",
        lambda path: Path(path).absolute() != root,
    )
    with pytest.raises(T65ProductionCaptureError, match="symlink or reparse"):
        _build_executor_manifest(tmp_path)


class _FakeSyncDelegate(httpx.BaseTransport):
    def __init__(self) -> None:
        self.closed = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError("offline registry test must not send")

    def close(self) -> None:
        self.closed += 1


class _FakeAsyncDelegate(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.closed = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError("offline registry test must not send")

    async def aclose(self) -> None:
        self.closed += 1


def test_controlled_client_registry_uses_zero_retry_no_env_and_explicit_shutdown(
    tmp_path, monkeypatch
):
    sync_delegate = _FakeSyncDelegate()
    async_delegate = _FakeAsyncDelegate()
    constructor_calls = []

    def build_sync(*, retries):
        constructor_calls.append(("sync", retries))
        return sync_delegate

    def build_async(*, retries):
        constructor_calls.append(("async", retries))
        return async_delegate

    monkeypatch.setattr(capture.httpx, "HTTPTransport", build_sync)
    monkeypatch.setattr(capture.httpx, "AsyncHTTPTransport", build_async)

    clients = install_t65_controlled_http_clients(
        ledger_directory=tmp_path,
        active_identity=_identity(),
        expected_identity=_identity(),
    )
    assert constructor_calls == [("sync", 0), ("async", 0)]
    assert clients.process_id == os.getpid()
    assert clients.sync_client._trust_env is False
    assert clients.async_client._trust_env is False
    assert get_t65_controlled_http_clients() is clients

    shutdown_t65_controlled_http_clients_sync()
    assert sync_delegate.closed == 1
    with pytest.raises(T65ProductionCaptureError, match="closing or closed"):
        get_t65_controlled_http_clients()
    asyncio.run(shutdown_t65_controlled_http_clients_async())
    assert async_delegate.closed == 1
    with pytest.raises(T65ProductionCaptureError, match="not installed"):
        get_t65_controlled_http_clients()

    shutdown_t65_controlled_http_clients_sync()
    asyncio.run(shutdown_t65_controlled_http_clients_async())
    assert sync_delegate.closed == 1
    assert async_delegate.closed == 1


def test_controlled_client_registry_rejects_duplicate_install(tmp_path, monkeypatch):
    monkeypatch.setattr(
        capture.httpx, "HTTPTransport", lambda *, retries: _FakeSyncDelegate()
    )
    monkeypatch.setattr(
        capture.httpx, "AsyncHTTPTransport", lambda *, retries: _FakeAsyncDelegate()
    )
    install_t65_controlled_http_clients(
        ledger_directory=tmp_path,
        active_identity=_identity(),
        expected_identity=_identity(),
    )
    try:
        with pytest.raises(T65ProductionCaptureError, match="already installed"):
            install_t65_controlled_http_clients(
                ledger_directory=tmp_path,
                active_identity=_identity(),
                expected_identity=_identity(),
            )
    finally:
        shutdown_t65_controlled_http_clients_sync()
        asyncio.run(shutdown_t65_controlled_http_clients_async())


def test_successful_double_shutdown_allows_same_process_reinstall(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        capture.httpx, "HTTPTransport", lambda *, retries: _FakeSyncDelegate()
    )
    monkeypatch.setattr(
        capture.httpx, "AsyncHTTPTransport", lambda *, retries: _FakeAsyncDelegate()
    )
    first = install_t65_controlled_http_clients(
        ledger_directory=tmp_path,
        active_identity=_identity(),
        expected_identity=_identity(),
    )
    shutdown_t65_controlled_http_clients_sync()
    asyncio.run(shutdown_t65_controlled_http_clients_async())
    first_ledger = first.sync_transport.ledger_path
    first_ledger_bytes = first_ledger.read_bytes()

    second = install_t65_controlled_http_clients(
        ledger_directory=tmp_path,
        active_identity=_identity(run_id="t65-run-2"),
        expected_identity=_identity(run_id="t65-run-2"),
    )
    try:
        assert second is not first
        assert second.sync_transport.ledger_path != first_ledger
        assert first_ledger.read_bytes() == first_ledger_bytes
        assert get_t65_controlled_http_clients() is second
    finally:
        shutdown_t65_controlled_http_clients_sync()
        asyncio.run(shutdown_t65_controlled_http_clients_async())


def test_async_shutdown_marks_registry_closing_before_await(tmp_path, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowAsyncDelegate(_FakeAsyncDelegate):
        async def aclose(self) -> None:
            entered.set()
            await release.wait()
            await super().aclose()

    monkeypatch.setattr(
        capture.httpx, "HTTPTransport", lambda *, retries: _FakeSyncDelegate()
    )
    monkeypatch.setattr(
        capture.httpx, "AsyncHTTPTransport", lambda *, retries: SlowAsyncDelegate()
    )
    install_t65_controlled_http_clients(
        ledger_directory=tmp_path,
        active_identity=_identity(),
        expected_identity=_identity(),
    )

    async def exercise() -> None:
        closing = asyncio.create_task(shutdown_t65_controlled_http_clients_async())
        await entered.wait()
        with pytest.raises(T65ProductionCaptureError, match="closing or closed"):
            get_t65_controlled_http_clients()
        release.set()
        await closing

    try:
        asyncio.run(exercise())
    finally:
        shutdown_t65_controlled_http_clients_sync()


def test_close_failure_never_restores_registry_availability(tmp_path, monkeypatch):
    class FailingSyncDelegate(_FakeSyncDelegate):
        def close(self) -> None:
            self.closed += 1
            raise RuntimeError("offline close failure")

    monkeypatch.setattr(
        capture.httpx, "HTTPTransport", lambda *, retries: FailingSyncDelegate()
    )
    monkeypatch.setattr(
        capture.httpx, "AsyncHTTPTransport", lambda *, retries: _FakeAsyncDelegate()
    )
    clients = install_t65_controlled_http_clients(
        ledger_directory=tmp_path,
        active_identity=_identity(),
        expected_identity=_identity(),
    )
    try:
        with pytest.raises(RuntimeError, match="close failure"):
            shutdown_t65_controlled_http_clients_sync()
        assert clients.sync_state == "CLOSE_FAILED"
        with pytest.raises(T65ProductionCaptureError, match="closing or closed"):
            get_t65_controlled_http_clients()
        with pytest.raises(T65ProviderTransportRejected, match="failed"):
            shutdown_t65_controlled_http_clients_sync()
        with pytest.raises(T65ProductionCaptureError, match="already installed"):
            install_t65_controlled_http_clients(
                ledger_directory=tmp_path,
                active_identity=_identity(run_id="replacement-prohibited"),
                expected_identity=_identity(run_id="replacement-prohibited"),
            )
    finally:
        asyncio.run(shutdown_t65_controlled_http_clients_async())
        transport_registry._client_pair = None


def test_identity_mismatch_rejects_before_transport_construction(tmp_path, monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError("delegate construction must not occur")

    monkeypatch.setattr(capture.httpx, "HTTPTransport", forbidden)
    monkeypatch.setattr(capture.httpx, "AsyncHTTPTransport", forbidden)
    with pytest.raises(T65ProviderTransportRejected, match="does not match"):
        install_t65_controlled_http_clients(
            ledger_directory=tmp_path,
            active_identity=_identity(candidate_tree="e" * 40),
            expected_identity=_identity(),
        )


def test_partial_client_construction_closes_both_delegates(tmp_path, monkeypatch):
    sync_delegate = _FakeSyncDelegate()
    async_delegate = _FakeAsyncDelegate()
    monkeypatch.setattr(
        capture.httpx, "HTTPTransport", lambda *, retries: sync_delegate
    )
    monkeypatch.setattr(
        capture.httpx, "AsyncHTTPTransport", lambda *, retries: async_delegate
    )

    def fail_async_client(**kwargs):
        raise RuntimeError("offline async client construction failure")

    monkeypatch.setattr(capture.httpx, "AsyncClient", fail_async_client)
    with pytest.raises(RuntimeError, match="construction failure"):
        install_t65_controlled_http_clients(
            ledger_directory=tmp_path,
            active_identity=_identity(),
            expected_identity=_identity(),
        )

    assert sync_delegate.closed == 1
    assert async_delegate.closed == 1
    with pytest.raises(T65ProductionCaptureError, match="not installed"):
        get_t65_controlled_http_clients()


def test_incremental_sse_parser_handles_partial_utf8_crlf_and_multiline_data():
    parser = T65IncrementalSSEParser()
    payload = (
        ": keepalive\r\n"
        "id: generation:1:7\r\n"
        "event: chunk\r\n"
        "data: first\r\n"
        "data: 中文\r\n\r\n"
        "event: done\n"
        "data: {}\n\n"
    ).encode("utf-8")
    events = []
    for byte in payload:
        events.extend(parser.feed(bytes((byte,))))
    events.extend(parser.close())

    assert [asdict(event) for event in events] == [
        {
            "event": "chunk",
            "data": "first\n中文",
            "event_id": "generation:1:7",
        },
        {
            "event": "done",
            "data": "{}",
            "event_id": "generation:1:7",
        },
    ]
    assert all(set(asdict(event)) == {"event", "data", "event_id"} for event in events)


def test_sse_keepalive_and_unknown_fields_do_not_fabricate_events():
    parser = T65IncrementalSSEParser()
    assert parser.feed(b": ping\nretry: 1000\nunknown: ignored\n\n") == []
    assert parser.close() == []


@pytest.mark.parametrize(
    "payload",
    [
        b"event: chunk\ndata: incomplete",
        b"id: bad\x00id\ndata: value\n\n",
        b"data: \xff\n\n",
    ],
)
def test_sse_parser_rejects_incomplete_nul_id_and_invalid_utf8(payload):
    parser = T65IncrementalSSEParser()
    if payload.startswith(b"event"):
        parser.feed(payload)
        with pytest.raises(T65SSEParseError, match="incomplete"):
            parser.close()
    else:
        with pytest.raises(T65SSEParseError):
            parser.feed(payload)


def test_sse_parser_enforces_bounded_frame_buffer():
    parser = T65IncrementalSSEParser(max_buffer_chars=8)
    with pytest.raises(T65SSEParseError, match="buffer limit"):
        parser.feed(b"data: 123456789")

    parser = T65IncrementalSSEParser(max_buffer_chars=16)
    with pytest.raises(T65SSEParseError, match="buffer limit"):
        parser.feed(b"id: 12345678901234567\n")


def test_cleanup_plan_accepts_only_isolated_runtime_and_vector_relations():
    runtime = "test_t65perf_aaaaaaaaaaaa"
    vector = "test_t65perf_bbbbbbbbbbbb"
    relations = [
        f"{runtime}_sessions",
        f"{runtime}_generation_chunks",
        vector,
        f"{vector}_versions",
        f"{vector}_releases",
    ]
    plan = build_t65_cleanup_target_plan(
        runtime_prefix=runtime,
        vector_prefix=vector,
        discovered_relations=relations,
    )

    assert plan.runtime_prefix == runtime
    assert plan.vector_prefix == vector
    assert plan.relations == tuple(sorted(relations, reverse=True))
    assert "DROP" not in repr(plan)


@pytest.mark.parametrize(
    ("runtime", "vector", "relations"),
    [
        ("interview", "test_t65perf_bbbbbbbbbbbb", []),
        (
            "test_t65perf_aaaaaaaaaaaa",
            "test_t65perf_aaaaaaaaaaaa",
            [],
        ),
        (
            "test_t65perf_aaaaaaaaaaaa",
            "test_t65perf_bbbbbbbbbbbb",
            ["interview_sessions"],
        ),
        (
            "test_t65perf_aaaaaaaaaaaa",
            "test_t65perf_bbbbbbbbbbbb",
            ["test_t65perf_aaaaaaaaaaaa_sessions"] * 2,
        ),
        (
            "test_t65perf_aaaaaaaaaaaa",
            "test_t65perf_bbbbbbbbbbbb",
            ["test_t65perf_bbbbbbbbbbbb_unapproved"],
        ),
    ],
)
def test_cleanup_plan_rejects_unsafe_prefixes_escapes_and_duplicates(
    runtime, vector, relations
):
    with pytest.raises(T65ProductionCaptureError):
        build_t65_cleanup_target_plan(
            runtime_prefix=runtime,
            vector_prefix=vector,
            discovered_relations=relations,
        )
