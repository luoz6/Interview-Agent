from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from threading import Event, Lock, Thread
from types import SimpleNamespace

import httpx
import pytest

from app.services.t65_provider_http_transport import (
    T65DeepSeekAsyncTransport,
    T65DeepSeekSyncTransport,
    T65ProviderTransportIdentity,
    T65ProviderLedgerRejected,
    T65ProviderTransportRejected,
    finalize_t65_provider_attempt_ledger,
    get_t65_provider_http_clients,
    install_t65_provider_http_clients,
    verify_t65_provider_attempt_ledger,
)
import app.services.t65_provider_http_transport as controlled_transport


SECRET_CANARIES = (
    "sk-test-never-write-this",
    "postgresql://private-user:private-password@private-db/runtime",
    "candidate answer must remain private",
    "provider-response-id-plaintext",
    "authorization-private-name",
)


def _identity(**overrides) -> T65ProviderTransportIdentity:
    values = {
        "run_id": "t65-formal-run-private",
        "process_role": "api",
        "candidate_revision": "a" * 40,
        "candidate_tree": "b" * 40,
        "authorization_id": "authorization-private-name",
        "authorization_sha256": "c" * 64,
        "executor_sha256": "d" * 64,
    }
    values.update(overrides)
    return T65ProviderTransportIdentity(**values)


def _request_json() -> dict:
    return {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "user", "content": "candidate answer must remain private"}
        ],
    }


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def _rewrite_hash_chain(path: Path, events: list[dict]) -> None:
    previous = "0" * 64
    for event in events:
        event["previous_event_sha256"] = previous
        event["event_sha256"] = controlled_transport._canonical_event_sha256(event)
        previous = event["event_sha256"]
    path.write_text(
        "\n".join(
            json.dumps(event, sort_keys=True, separators=(",", ":"))
            for event in events
        )
        + "\n",
        encoding="utf-8",
    )


def test_sync_transport_fsyncs_start_before_delegate_and_writes_safe_finish(
    tmp_path, monkeypatch
):
    observed = {"fsynced": False}
    original_fsync = os.fsync

    def tracked_fsync(descriptor):
        original_fsync(descriptor)
        observed["fsynced"] = True

    monkeypatch.setattr(
        "app.services.t65_provider_http_transport.os.fsync", tracked_fsync
    )
    wrapper = None

    def handler(request: httpx.Request) -> httpx.Response:
        assert observed["fsynced"] is True
        assert wrapper is not None
        starts = _read_events(wrapper.ledger_path)
        assert [event["event"] for event in starts] == ["ATTEMPT_START"]
        return httpx.Response(
            200,
            headers={"x-request-id": "provider-response-id-plaintext"},
            json={"id": "raw-response-must-not-be-read"},
        )

    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(handler),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    with httpx.Client(transport=wrapper) as client:
        response = client.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": "Bearer sk-test-never-write-this"},
            json=_request_json(),
        )

    assert response.status_code == 200
    events = _read_events(wrapper.ledger_path)
    assert [event["event"] for event in events] == [
        "ATTEMPT_START",
        "ATTEMPT_FINISH",
        "LEDGER_SEAL",
    ]
    assert events[0]["status"] == "delegating"
    assert events[1]["status"] == "response"
    assert events[1]["http_status"] == 200
    assert events[1]["provider_response_id_sha256"] == sha256(
        b"provider-response-id-plaintext"
    ).hexdigest()
    assert events[0]["attempt_id"] == events[1]["attempt_id"]
    assert events[0]["previous_event_sha256"] == "0" * 64
    assert events[1]["previous_event_sha256"] == events[0]["event_sha256"]
    assert events[0]["process_id"] == os.getpid()
    assert events[0]["process_role"] == "api"
    assert events[0]["authorization_sha256"] == "c" * 64
    assert events[0]["executor_sha256"] == "d" * 64

    rendered = wrapper.ledger_path.read_text("utf-8")
    for canary in SECRET_CANARIES:
        assert canary not in rendered
    assert "Authorization" not in rendered
    assert "messages" not in rendered
    assert "raw-response-must-not-be-read" not in rendered

    receipt = verify_t65_provider_attempt_ledger(
        wrapper.ledger_path,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    )
    assert receipt.complete is True
    assert receipt.start_count == receipt.finish_count == receipt.success_count == 1
    assert receipt.error_count == 0
    assert receipt.sequence_first == receipt.sequence_last == 1
    assert receipt.provider_response_id_sha256s == (
        sha256(b"provider-response-id-plaintext").hexdigest(),
    )
    serialized_receipt = json.dumps(receipt.as_dict(), sort_keys=True)
    assert "formal" not in serialized_receipt
    assert "external" not in serialized_receipt
    for canary in SECRET_CANARIES:
        assert canary not in serialized_receipt


def test_nonempty_existing_pid_ledger_is_rejected(tmp_path):
    ledger = tmp_path / (
        f"provider-attempts-api-{os.getpid()}-"
        f"{controlled_transport._ledger_identity_key(_identity())}.jsonl"
    )
    ledger.write_text("prior-process-data\n", encoding="utf-8")

    with pytest.raises(T65ProviderTransportRejected, match="not reusable"):
        T65DeepSeekSyncTransport(
            delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
            ledger_directory=tmp_path,
            identity=_identity(),
            expected_identity=_identity(),
        )


def _completed_ledger(tmp_path):
    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"x-request-id": "safe-id"})
        ),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    with httpx.Client(transport=wrapper) as client:
        client.post(
            "https://api.deepseek.com/chat/completions",
            json=_request_json(),
        )
    return wrapper.ledger_path


def test_active_or_empty_ledger_cannot_finalize_complete(tmp_path):
    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    active_receipt = finalize_t65_provider_attempt_ledger(
        wrapper.ledger_path,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    )
    assert active_receipt.complete is False
    assert active_receipt.failure_code == "LEDGER_NOT_SEALED"

    wrapper.close()
    sealed_empty_receipt = finalize_t65_provider_attempt_ledger(
        wrapper.ledger_path,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    )
    assert sealed_empty_receipt.complete is False
    assert sealed_empty_receipt.failure_code == "LEDGER_NO_ATTEMPTS"


def test_sync_async_writers_share_state_and_seal_only_after_both_close(tmp_path):
    sync_transport = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    async_transport = T65DeepSeekAsyncTransport(
        delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    with httpx.Client(transport=sync_transport) as client:
        client.post("https://api.deepseek.com/chat/completions", json=_request_json())

    assert [event["event"] for event in _read_events(sync_transport.ledger_path)] == [
        "ATTEMPT_START",
        "ATTEMPT_FINISH",
    ]
    assert finalize_t65_provider_attempt_ledger(
        sync_transport.ledger_path,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    ).failure_code == "LEDGER_NOT_SEALED"

    asyncio.run(async_transport.aclose())
    events = _read_events(sync_transport.ledger_path)
    assert [event["event"] for event in events] == [
        "ATTEMPT_START",
        "ATTEMPT_FINISH",
        "LEDGER_SEAL",
    ]
    assert verify_t65_provider_attempt_ledger(
        sync_transport.ledger_path,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    ).complete is True


def test_stale_empty_ledger_is_not_accepted_as_shared_initialization(tmp_path):
    ledger = tmp_path / (
        f"provider-attempts-api-{os.getpid()}-"
        f"{controlled_transport._ledger_identity_key(_identity())}.jsonl"
    )
    ledger.touch()
    with pytest.raises(T65ProviderTransportRejected, match="not reusable"):
        T65DeepSeekSyncTransport(
            delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
            ledger_directory=tmp_path,
            identity=_identity(),
            expected_identity=_identity(),
        )


def test_non_2xx_and_missing_response_id_are_counted_as_error(tmp_path):
    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(lambda request: httpx.Response(429)),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    with httpx.Client(transport=wrapper) as client:
        assert client.post(
            "https://api.deepseek.com/chat/completions", json=_request_json()
        ).status_code == 429
    receipt = verify_t65_provider_attempt_ledger(
        wrapper.ledger_path,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    )
    assert receipt.success_count == 0
    assert receipt.error_count == 1
    assert receipt.response_id_missing_count == 1
    assert receipt.duplicate_response_id_count == 0


def test_duplicate_response_id_is_rejected_as_replay(tmp_path):
    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"x-request-id": "same-id"})
        ),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    with httpx.Client(transport=wrapper) as client:
        for _ in range(2):
            client.post("https://api.deepseek.com/chat/completions", json=_request_json())
    with pytest.raises(T65ProviderLedgerRejected, match="DUPLICATE_RESPONSE_ID"):
        verify_t65_provider_attempt_ledger(
            wrapper.ledger_path,
            expected_identity=_identity(),
            expected_process_id=os.getpid(),
        )


@pytest.mark.parametrize("field", ["raw_prompt", "raw_response"])
def test_rehashed_raw_payload_field_injection_is_rejected(tmp_path, field):
    ledger = _completed_ledger(tmp_path)
    events = _read_events(ledger)
    events[0][field] = "candidate answer must remain private"
    _rewrite_hash_chain(ledger, events)
    with pytest.raises(T65ProviderLedgerRejected, match="EVENT_FIELDS_INVALID"):
        verify_t65_provider_attempt_ledger(
            ledger,
            expected_identity=_identity(),
            expected_process_id=os.getpid(),
        )


def test_close_during_active_attempt_seals_only_after_finish(tmp_path):
    entered = Event()
    release = Event()
    failures: list[BaseException] = []

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        assert release.wait(5)
        return httpx.Response(200)

    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(handler),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    request = httpx.Request(
        "POST", "https://api.deepseek.com/chat/completions", json=_request_json()
    )

    def send() -> None:
        try:
            wrapper.handle_request(request)
        except BaseException as exc:
            failures.append(exc)

    worker = Thread(target=send)
    worker.start()
    assert entered.wait(5)
    wrapper.close()
    assert [event["event"] for event in _read_events(wrapper.ledger_path)] == [
        "ATTEMPT_START"
    ]
    active_receipt = finalize_t65_provider_attempt_ledger(
        wrapper.ledger_path,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    )
    assert active_receipt.complete is False
    assert active_receipt.failure_code == "LEDGER_ORPHAN_START"
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert failures == []
    assert [event["event"] for event in _read_events(wrapper.ledger_path)] == [
        "ATTEMPT_START",
        "ATTEMPT_FINISH",
        "LEDGER_SEAL",
    ]


def test_reparse_point_in_ledger_path_component_is_detected(tmp_path, monkeypatch):
    junction = tmp_path / "junction"
    target = junction / "ledger"
    original_lstat = controlled_transport.os.lstat

    def fake_lstat(path):
        if Path(path) == junction:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                ),
            )
        return original_lstat(path)

    monkeypatch.setattr(controlled_transport.os, "lstat", fake_lstat)
    assert controlled_transport._path_has_reparse_component(target) is True


@pytest.mark.parametrize("mutation", ["truncate", "tamper", "inject", "orphan_start"])
def test_ledger_verifier_fails_closed_on_mutation(tmp_path, mutation):
    ledger = _completed_ledger(tmp_path)
    raw = ledger.read_bytes()
    lines = raw.decode("utf-8").splitlines()
    if mutation == "truncate":
        ledger.write_bytes(raw[:-1])
    elif mutation == "tamper":
        event = json.loads(lines[1])
        event["http_status"] = 201
        lines[1] = json.dumps(event, sort_keys=True, separators=(",", ":"))
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif mutation == "inject":
        ledger.write_text("\n".join([*lines, lines[0]]) + "\n", encoding="utf-8")
    else:
        ledger.write_text(lines[0] + "\n", encoding="utf-8")

    with pytest.raises(T65ProviderLedgerRejected):
        verify_t65_provider_attempt_ledger(
            ledger,
            expected_identity=_identity(),
            expected_process_id=os.getpid(),
        )
    receipt = finalize_t65_provider_attempt_ledger(
        ledger,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    )
    assert receipt.complete is False
    assert receipt.failure_code


def test_ledger_verifier_rejects_rehashed_orphan_finish_and_bad_sequence(tmp_path):
    ledger = _completed_ledger(tmp_path)
    events = _read_events(ledger)
    finish = events[1]
    finish["previous_event_sha256"] = "0" * 64
    finish["event_sha256"] = controlled_transport._canonical_event_sha256(finish)
    ledger.write_text(
        json.dumps(finish, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(T65ProviderLedgerRejected, match="ORPHAN_FINISH"):
        verify_t65_provider_attempt_ledger(
            ledger,
            expected_identity=_identity(),
            expected_process_id=os.getpid(),
        )

    ledger = _completed_ledger(tmp_path / "bad-sequence")
    events = _read_events(ledger)
    for event in events:
        event["attempt_sequence"] = 2
    events[0]["previous_event_sha256"] = "0" * 64
    events[0]["event_sha256"] = controlled_transport._canonical_event_sha256(events[0])
    events[1]["previous_event_sha256"] = events[0]["event_sha256"]
    events[1]["event_sha256"] = controlled_transport._canonical_event_sha256(events[1])
    ledger.write_text(
        "\n".join(
            json.dumps(event, sort_keys=True, separators=(",", ":"))
            for event in events
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(T65ProviderLedgerRejected, match="START_SEQUENCE"):
        verify_t65_provider_attempt_ledger(
            ledger,
            expected_identity=_identity(),
            expected_process_id=os.getpid(),
        )


def test_async_transport_uses_fake_delegate_and_records_error_without_message(
    tmp_path,
):
    private_error = "sk-test-never-write-this private provider failure"

    async def exercise():
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadError(private_error, request=request)

        wrapper = T65DeepSeekAsyncTransport(
            delegate=httpx.MockTransport(handler),
            ledger_directory=tmp_path,
            identity=_identity(process_role="report_worker"),
            expected_identity=_identity(process_role="report_worker"),
        )
        async with httpx.AsyncClient(transport=wrapper) as client:
            with pytest.raises(httpx.ReadError, match="private provider failure"):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json=_request_json(),
                )
        return wrapper

    wrapper = asyncio.run(exercise())
    events = _read_events(wrapper.ledger_path)
    assert [event["event"] for event in events] == [
        "ATTEMPT_START",
        "ATTEMPT_FINISH",
        "LEDGER_SEAL",
    ]
    assert events[1]["status"] == "delegate_error"
    assert events[1]["error_class_sha256"] == sha256(
        b"httpx.ReadError"
    ).hexdigest()
    assert private_error not in wrapper.ledger_path.read_text("utf-8")
    assert wrapper.ledger_path.name == (
        f"provider-attempts-report_worker-{os.getpid()}-"
        f"{controlled_transport._ledger_identity_key(_identity(process_role='report_worker'))}.jsonl"
    )
    receipt = verify_t65_provider_attempt_ledger(
        wrapper.ledger_path,
        expected_identity=_identity(process_role="report_worker"),
        expected_process_id=os.getpid(),
    )
    assert receipt.complete is True
    assert receipt.start_count == receipt.finish_count == receipt.error_count == 1
    assert receipt.success_count == 0


@pytest.mark.parametrize(
    ("url", "payload"),
    [
        ("http://api.deepseek.com/chat/completions", _request_json()),
        ("https://api.deepseek.com/v1/chat/completions", _request_json()),
        ("https://evil.example/chat/completions", _request_json()),
        (
            "https://api.deepseek.com/chat/completions",
            {"model": "deepseek-chat", "messages": []},
        ),
        ("https://api.deepseek.com/chat/completions", {"messages": []}),
    ],
)
def test_endpoint_and_model_drift_are_rejected_before_delegate(
    tmp_path, url, payload
):
    delegated = []

    def handler(request: httpx.Request) -> httpx.Response:
        delegated.append(request)
        return httpx.Response(200)

    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(handler),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    with httpx.Client(transport=wrapper) as client:
        with pytest.raises(T65ProviderTransportRejected):
            client.post(url, json=payload)

    assert delegated == []
    assert wrapper.ledger_path.exists()
    assert [event["event"] for event in _read_events(wrapper.ledger_path)] == [
        "LEDGER_SEAL"
    ]
    receipt = finalize_t65_provider_attempt_ledger(
        wrapper.ledger_path,
        expected_identity=_identity(),
        expected_process_id=os.getpid(),
    )
    assert receipt.complete is False
    assert receipt.failure_code == "LEDGER_NO_ATTEMPTS"


def test_each_delegate_send_is_an_independent_attempt_for_sdk_retry_accounting(
    tmp_path,
):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("private endpoint detail", request=request)
        return httpx.Response(200)

    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(handler),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    with httpx.Client(transport=wrapper) as client:
        with pytest.raises(httpx.ConnectError):
            client.post(
                "https://api.deepseek.com/chat/completions", json=_request_json()
            )
        assert client.post(
            "https://api.deepseek.com/chat/completions", json=_request_json()
        ).status_code == 200

    events = _read_events(wrapper.ledger_path)
    starts = [event for event in events if event["event"] == "ATTEMPT_START"]
    finishes = [event for event in events if event["event"] == "ATTEMPT_FINISH"]
    assert calls == 2
    assert [event["attempt_sequence"] for event in starts] == [1, 2]
    assert len({event["attempt_id"] for event in starts}) == 2
    assert [event["status"] for event in finishes] == [
        "delegate_error",
        "response",
    ]


def test_shared_process_ledger_serializes_threaded_writes(tmp_path):
    delegate_lock = Lock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        with delegate_lock:
            calls += 1
        return httpx.Response(200)

    wrapper = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(handler),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )

    with httpx.Client(transport=wrapper) as client:
        def send():
            client.post(
                "https://api.deepseek.com/chat/completions", json=_request_json()
            )
        threads = [Thread(target=send) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    events = _read_events(wrapper.ledger_path)
    starts = [event for event in events if event["event"] == "ATTEMPT_START"]
    assert calls == 12
    assert len(events) == 25
    assert events[-1]["event"] == "LEDGER_SEAL"
    assert len({event["attempt_id"] for event in starts}) == 12
    assert len({event["attempt_sequence"] for event in starts}) == 12


@pytest.mark.parametrize(
    "identity",
    [
        _identity(process_role="unknown"),
        _identity(candidate_revision="not-a-git-object"),
        _identity(authorization_sha256="short"),
        _identity(executor_sha256="E" * 64),
        _identity(authorization_id=" "),
    ],
)
def test_invalid_identity_is_rejected_during_construction(tmp_path, identity):
    with pytest.raises(T65ProviderTransportRejected):
        T65DeepSeekSyncTransport(
            delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
            ledger_directory=tmp_path,
            identity=identity,
            expected_identity=_identity(),
        )


def test_identity_mismatch_is_rejected_before_delegate(tmp_path):
    delegated = []

    wrapper_delegate = httpx.MockTransport(
        lambda request: delegated.append(request) or httpx.Response(200)
    )
    with pytest.raises(
        T65ProviderTransportRejected, match="does not match the authorized identity"
    ):
        T65DeepSeekSyncTransport(
            delegate=wrapper_delegate,
            ledger_directory=tmp_path,
            identity=_identity(candidate_tree="e" * 40),
            expected_identity=_identity(),
        )

    assert delegated == []
    assert list(tmp_path.iterdir()) == []


def test_process_client_pair_requires_both_controlled_transports(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(controlled_transport, "_client_pair", None)
    sync_transport = T65DeepSeekSyncTransport(
        delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    async_transport = T65DeepSeekAsyncTransport(
        delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    sync_client = httpx.Client(transport=sync_transport, trust_env=False)
    async_client = httpx.AsyncClient(transport=async_transport, trust_env=False)
    try:
        installed = install_t65_provider_http_clients(
            sync_client=sync_client,
            async_client=async_client,
            identity=_identity(),
        )
        assert get_t65_provider_http_clients() is installed
        assert installed.sync_client is sync_client
        assert installed.async_client is async_client
    finally:
        sync_client.close()
        asyncio.run(async_client.aclose())


def test_process_client_pair_rejects_default_uncontrolled_client(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(controlled_transport, "_client_pair", None)
    async_transport = T65DeepSeekAsyncTransport(
        delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
        ledger_directory=tmp_path,
        identity=_identity(),
        expected_identity=_identity(),
    )
    sync_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    async_client = httpx.AsyncClient(transport=async_transport)
    try:
        with pytest.raises(T65ProviderTransportRejected, match="must own"):
            install_t65_provider_http_clients(
                sync_client=sync_client,
                async_client=async_client,
                identity=_identity(),
            )
    finally:
        sync_client.close()
        asyncio.run(async_client.aclose())


def test_pid_drift_discards_inherited_pair_and_allows_process_local_reinstall(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(controlled_transport, "_client_pair", None)

    def build_pair(run_id, ledger_directory):
        identity = _identity(run_id=run_id)
        sync_transport = T65DeepSeekSyncTransport(
            delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
            ledger_directory=ledger_directory,
            identity=identity,
            expected_identity=identity,
        )
        async_transport = T65DeepSeekAsyncTransport(
            delegate=httpx.MockTransport(lambda request: httpx.Response(200)),
            ledger_directory=ledger_directory,
            identity=identity,
            expected_identity=identity,
        )
        return (
            identity,
            httpx.Client(transport=sync_transport, trust_env=False),
            httpx.AsyncClient(transport=async_transport, trust_env=False),
        )

    first_identity, first_sync, first_async = build_pair("parent-run", tmp_path / "parent")
    second_sync = second_async = None
    try:
        inherited = install_t65_provider_http_clients(
            sync_client=first_sync,
            async_client=first_async,
            identity=first_identity,
        )
        inherited.process_id = os.getpid() + 1
        with pytest.raises(T65ProviderTransportRejected, match="not installed"):
            get_t65_provider_http_clients()

        second_identity, second_sync, second_async = build_pair(
            "child-run", tmp_path / "child"
        )
        replacement = install_t65_provider_http_clients(
            sync_client=second_sync,
            async_client=second_async,
            identity=second_identity,
        )
        assert get_t65_provider_http_clients() is replacement
    finally:
        monkeypatch.setattr(controlled_transport, "_client_pair", None)
        first_sync.close()
        asyncio.run(first_async.aclose())
        if second_sync is not None:
            second_sync.close()
        if second_async is not None:
            asyncio.run(second_async.aclose())
