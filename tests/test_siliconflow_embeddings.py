from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import pytest

from app.services.embedding_providers import EmbeddingProviderError
from app.services.siliconflow_embeddings import SiliconFlowEmbeddingProvider


def make_provider(handler, *, sleeps=None, batch_size=2, jitter=None):
    sleeps = sleeps if sleeps is not None else []
    return SiliconFlowEmbeddingProvider(
        api_key="test-secret-key-not-real",
        api_base="https://unit.test/v1",
        model_name="BAAI/bge-m3",
        model_revision="test-revision",
        dimension=3,
        batch_size=batch_size,
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: sleeps.append(seconds),
        jitter=jitter or (lambda: 0.0),
    )


def success_response(count=1):
    return httpx.Response(
        200,
        json={
            "data": [
                {"index": index, "embedding": [float(index), 1.0, 2.0]}
                for index in range(count)
            ]
        },
    )


def test_documents_are_batched_and_response_indices_restore_order():
    request_sizes = []

    def handler(request):
        payload = json.loads(request.content)
        request_sizes.append(len(payload["input"]))
        data = [
            {"index": index, "embedding": [float(index), 1.0, 2.0]}
            for index in reversed(range(len(payload["input"])))
        ]
        return httpx.Response(200, json={"data": data})

    result = make_provider(handler).embed_documents(["a", "b", "c"])

    assert request_sizes == [2, 1]
    assert result == [
        [0.0, 1.0, 2.0],
        [1.0, 1.0, 2.0],
        [0.0, 1.0, 2.0],
    ]


def test_query_payload_and_empty_query_fallback_are_bounded():
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-secret-key-not-real"
        return success_response()

    provider = make_provider(handler)

    assert provider.embed_query(" redis consistency ") == [0.0, 1.0, 2.0]
    assert provider.embed_query("  ") == [0.0, 1.0, 2.0]
    assert payloads == [
        {
            "model": "BAAI/bge-m3",
            "input": ["redis consistency"],
            "encoding_format": "float",
        },
        {
            "model": "BAAI/bge-m3",
            "input": ["general knowledge"],
            "encoding_format": "float",
        },
    ]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_retry_once_then_succeed(status):
    attempts = 0
    sleeps = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status, json={"message": "do not expose body"})
        return success_response()

    result = make_provider(handler, sleeps=sleeps).embed_query("redis")

    assert result == [0.0, 1.0, 2.0]
    assert attempts == 2
    assert sleeps == [0.25]


def test_read_timeout_retries_without_exposing_exception():
    attempts = 0
    sleeps = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("private query timed out", request=request)
        return success_response()

    result = make_provider(handler, sleeps=sleeps).embed_query("private query")

    assert result == [0.0, 1.0, 2.0]
    assert attempts == 2
    assert sleeps == [0.25]


def test_network_reset_retries_with_stable_error_code():
    attempts = 0
    sleeps = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadError("connection reset with private data", request=request)
        return success_response()

    provider = make_provider(handler, sleeps=sleeps)

    assert provider.embed_query("private query") == [0.0, 1.0, 2.0]
    assert attempts == 2
    assert sleeps == [0.25]
    assert provider.snapshot_metrics()["error_counts"] == {"network_error": 1}


@pytest.mark.parametrize("status", [400, 401, 403])
def test_permanent_http_errors_do_not_retry_and_are_redacted(status):
    attempts = 0
    sleeps = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, text="test-secret-key-not-real private query")

    provider = make_provider(handler, sleeps=sleeps)

    with pytest.raises(EmbeddingProviderError) as exc:
        provider.embed_query("private query")

    assert exc.value.code == f"http_{status}"
    assert exc.value.retryable is False
    assert attempts == 1
    assert sleeps == []
    assert "test-secret-key-not-real" not in str(exc.value)
    assert "private query" not in str(exc.value)


def test_invalid_json_is_a_stable_permanent_error():
    provider = make_provider(
        lambda request: httpx.Response(
            200,
            content=b"{",
            headers={"content-type": "application/json"},
        )
    )

    with pytest.raises(EmbeddingProviderError) as exc:
        provider.embed_query("private query")

    assert exc.value.code == "invalid_json"
    assert exc.value.retryable is False


@pytest.mark.parametrize(
    "data",
    [
        [
            {"index": 0, "embedding": [1, 2, 3]},
            {"index": 0, "embedding": [1, 2, 3]},
        ],
        [{"index": 1, "embedding": [1, 2, 3]}],
        [{"index": "0", "embedding": [1, 2, 3]}],
        [{"index": 3, "embedding": [1, 2, 3]}],
        [],
    ],
)
def test_response_indices_must_be_complete_unique_integers(data):
    provider = make_provider(
        lambda request: httpx.Response(200, json={"data": data})
    )

    with pytest.raises(EmbeddingProviderError) as exc:
        provider.embed_query("redis")

    assert exc.value.code == "response_index_mismatch"
    assert exc.value.retryable is False


@pytest.mark.parametrize(
    "embedding",
    [
        [1, 2],
        [1, float("nan"), 3],
        [1, float("inf"), 3],
    ],
)
def test_invalid_vectors_are_stable_permanent_errors(embedding):
    provider = make_provider(
        lambda request: httpx.Response(
            200,
            content=json.dumps(
                {"data": [{"index": 0, "embedding": embedding}]}
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
    )

    with pytest.raises(EmbeddingProviderError) as exc:
        provider.embed_query("redis")

    assert exc.value.code == "invalid_vector"
    assert exc.value.retryable is False


def test_transient_failure_stops_after_three_attempts():
    attempts = 0
    sleeps = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="upstream unavailable")

    provider = make_provider(handler, sleeps=sleeps)

    with pytest.raises(EmbeddingProviderError) as exc:
        provider.embed_query("redis")

    assert exc.value.code == "http_503"
    assert exc.value.retryable is True
    assert attempts == 3
    assert sleeps == [0.25, 0.5]
    assert provider.snapshot_metrics()["retry_count"] == 2


def test_metrics_are_thread_safe_and_contain_only_safe_fields():
    provider = make_provider(lambda request: success_response())

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(provider.embed_query, ["private query"] * 8))

    assert results == [[0.0, 1.0, 2.0]] * 8
    metrics = provider.snapshot_metrics()
    assert set(metrics) == {
        "request_count",
        "retry_count",
        "error_counts",
        "latency_p50_ms",
        "latency_p95_ms",
    }
    assert metrics["request_count"] == 8
    assert metrics["retry_count"] == 0
    assert metrics["error_counts"] == {}
    serialized = json.dumps(metrics)
    assert "private query" not in serialized
    assert "test-secret-key-not-real" not in serialized
    assert "authorization" not in serialized.lower()
    assert "https://" not in serialized


def test_empty_metrics_use_zero_percentiles():
    provider = make_provider(lambda request: success_response())

    assert provider.snapshot_metrics() == {
        "request_count": 0,
        "retry_count": 0,
        "error_counts": {},
        "latency_p50_ms": 0.0,
        "latency_p95_ms": 0.0,
    }
