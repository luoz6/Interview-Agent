from __future__ import annotations

from collections import Counter
import math
import threading
from time import perf_counter, sleep as default_sleep
from typing import Callable
import random

import httpx

from app.services.embedding_providers import (
    EmbeddingProviderError,
    validate_embedding_batch,
)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(values[lower])
    weight = position - lower
    return float(values[lower] * (1.0 - weight) + values[upper] * weight)


class SiliconFlowEmbeddingProvider:
    provider_name = "siliconflow"
    max_attempts = 3
    transient_statuses = {429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        model_name: str,
        model_revision: str,
        dimension: int,
        batch_size: int,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = default_sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.model_name = model_name
        self.model_revision = model_revision
        self.dimension = int(dimension)
        self.batch_size = int(batch_size)
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.read_timeout_seconds = float(read_timeout_seconds)
        self._transport = transport
        self._sleep = sleep
        self._jitter = jitter
        self._metrics_lock = threading.Lock()
        self._request_count = 0
        self._retry_count = 0
        self._error_counts: Counter[str] = Counter()
        self._latencies_ms: list[float] = []

    def embed_query(self, text: str) -> list[float]:
        payload = text.strip() or "general knowledge"
        return self._embed_batch([payload])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            result.extend(self._embed_batch(texts[start : start + self.batch_size]))
        return result

    def snapshot_metrics(self) -> dict:
        with self._metrics_lock:
            ordered = sorted(self._latencies_ms)
            return {
                "request_count": self._request_count,
                "retry_count": self._retry_count,
                "error_counts": dict(sorted(self._error_counts.items())),
                "latency_p50_ms": _percentile(ordered, 0.50),
                "latency_p95_ms": _percentile(ordered, 0.95),
            }

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        for attempt in range(1, self.max_attempts + 1):
            started = perf_counter()
            try:
                response = self._post(texts)
            except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                del exc
                error = EmbeddingProviderError("network_error", retryable=True)
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                del exc
                error = EmbeddingProviderError("network_timeout", retryable=True)
            else:
                if response.status_code < 200 or response.status_code >= 300:
                    status = response.status_code
                    error = EmbeddingProviderError(
                        f"http_{status}",
                        retryable=status in self.transient_statuses,
                    )
                else:
                    try:
                        vectors = self._parse_vectors(response, expected_count=len(texts))
                    except EmbeddingProviderError as exc:
                        self._record_attempt(started, error_code=exc.code)
                        raise
                    self._record_attempt(started)
                    return vectors

            self._record_attempt(started, error_code=error.code)
            if error.retryable and attempt < self.max_attempts:
                self._record_retry()
                jitter = min(0.25, max(0.0, float(self._jitter())))
                self._sleep(0.25 * 2 ** (attempt - 1) + jitter)
                continue
            raise error

        raise AssertionError("embedding retry loop exhausted unexpectedly")

    def _post(self, texts: list[str]) -> httpx.Response:
        timeout = httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.read_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )
        with httpx.Client(timeout=timeout, transport=self._transport) as client:
            return client.post(
                f"{self.api_base}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model_name,
                    "input": texts,
                    "encoding_format": "float",
                },
            )

    def _parse_vectors(
        self,
        response: httpx.Response,
        *,
        expected_count: int,
    ) -> list[list[float]]:
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise EmbeddingProviderError("invalid_json", retryable=False) from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise EmbeddingProviderError("invalid_json", retryable=False)

        ordered: list[object | None] = [None] * expected_count
        for item in payload["data"]:
            if not isinstance(item, dict):
                raise EmbeddingProviderError("response_index_mismatch", retryable=False)
            index = item.get("index")
            if (
                type(index) is not int
                or index < 0
                or index >= expected_count
                or ordered[index] is not None
            ):
                raise EmbeddingProviderError("response_index_mismatch", retryable=False)
            ordered[index] = item.get("embedding")
        if any(vector is None for vector in ordered):
            raise EmbeddingProviderError("response_index_mismatch", retryable=False)

        try:
            return validate_embedding_batch(
                ordered,
                expected_count=expected_count,
                dimension=self.dimension,
            )
        except (TypeError, ValueError, OverflowError):
            raise EmbeddingProviderError("invalid_vector", retryable=False) from None

    def _record_attempt(self, started: float, *, error_code: str | None = None) -> None:
        latency_ms = max(0.0, (perf_counter() - started) * 1000.0)
        with self._metrics_lock:
            self._request_count += 1
            self._latencies_ms.append(latency_ms)
            if error_code is not None:
                self._error_counts[error_code] += 1

    def _record_retry(self) -> None:
        with self._metrics_lock:
            self._retry_count += 1
