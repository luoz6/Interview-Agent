# Stage 44A Remote BGE-M3 And Versioned Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace local SentenceTransformer embeddings with an explicitly enabled SiliconFlow `BAAI/bge-m3` provider, migrate the current 25-unit corpus into versioned PostgreSQL storage, atomically activate a remotely embedded release, and preserve every Stage 42 retrieval and evidence-continuity gate.

**Architecture:** Keep `KnowledgeRepository.search()` and `get_by_ids()` stable. Add a provider port plus disabled/SiliconFlow adapters, let `PgVectorKnowledgeStore` own versioned PostgreSQL reads and writes, and put remote batching plus pre-transaction vector preparation in a separate `KnowledgeCorpusIngestor`. Preserve the original knowledge table, optionally copy its rows as truthful legacy history, and activate only a distinct SiliconFlow release.

**Tech Stack:** Python 3.11, FastAPI configuration patterns, Pydantic v2, httpx, PostgreSQL 15+, pgvector, psycopg2, SiliconFlow OpenAI-compatible Embeddings API, pytest, existing Stage 42 evaluation tooling.

---

## Scope And File Map

Stage 44A changes infrastructure only. The 25 committed Markdown units and the
30-case `knowledge_retrieval_v1.json` remain the accepted content baseline.
Stage 44B owns corpus expansion and v2 metrics.

New ownership boundaries:

- `app/ports/runtime.py`: provider protocol only.
- `app/services/embedding_providers.py`: disabled provider, shared errors,
  response-vector validation, and provider factory.
- `app/services/siliconflow_embeddings.py`: SiliconFlow HTTP, batching, retry,
  and response parsing only.
- `app/services/vector_store.py`: versioned pgvector schema, legacy copy,
  activation transaction, active search, historical evidence lookup, and
  reranking.
- `app/services/knowledge_ingestion.py`: prepare all vectors before the write
  transaction, reuse only identity-compatible vectors, and activate a complete
  release.
- `scripts/load_knowledge.py`: CLI parsing and orchestration only.
- `scripts/run_stage44a_acceptance.py`: opt-in real-provider gate only.
- `scripts/audit_stage44a_artifacts.py`: Stage 44A whitelist and privacy audit.

No real key appears in code, tests, commands, docs, or committed artifacts.
Before Task 8, revoke every key previously pasted into chat and configure a new
key only in the local process environment.

### Task 1: Lock Provider And Configuration Contracts

**Files:**
- Modify: `app/ports/runtime.py`
- Modify: `app/services/config.py`
- Create: `app/services/embedding_providers.py`
- Create: `tests/test_embedding_config.py`
- Create: `tests/test_embedding_providers.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing configuration and provider contract tests**

Create `tests/test_embedding_config.py` with explicit defaults, validation, and
no-secret behavior:

```python
import pytest

from app.services.config import get_embedding_settings


def test_embedding_defaults_are_disabled_and_never_select_local_model(monkeypatch):
    for name in (
        "EMBEDDING_PROVIDER",
        "EMBEDDING_API_BASE",
        "EMBEDDING_MODEL_NAME",
        "EMBEDDING_MODEL_REVISION",
        "EMBEDDING_DIMENSION",
        "EMBEDDING_BATCH_SIZE",
        "EMBEDDING_CONNECT_TIMEOUT_SECONDS",
        "EMBEDDING_READ_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = get_embedding_settings()

    assert settings.provider_name == "disabled"
    assert settings.model_name == "BAAI/bge-m3"
    assert settings.dimension == 1024
    assert settings.batch_size == 32
    assert "api_key" not in repr(settings).lower()


@pytest.mark.parametrize("value", ["local", "sentence-transformers", "unknown"])
def test_embedding_provider_rejects_unsupported_values(monkeypatch, value):
    monkeypatch.setenv("EMBEDDING_PROVIDER", value)

    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        get_embedding_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EMBEDDING_DIMENSION", "0"),
        ("EMBEDDING_BATCH_SIZE", "0"),
        ("EMBEDDING_CONNECT_TIMEOUT_SECONDS", "0"),
        ("EMBEDDING_READ_TIMEOUT_SECONDS", "-1"),
    ],
)
def test_embedding_numeric_settings_must_be_positive(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        get_embedding_settings()


def test_pgvector_derived_table_names_are_valid_and_bounded():
    from app.services.config import derive_pgvector_table_names

    assert derive_pgvector_table_names("knowledge_chunks") == (
        "knowledge_chunks_versions",
        "knowledge_chunks_releases",
    )
    versions, releases = derive_pgvector_table_names("x" * 54)
    assert len(versions.encode("ascii")) == 63
    assert len(releases.encode("ascii")) == 63


@pytest.mark.parametrize(
    "base",
    ["", "9invalid", "contains-dash", "知识", "x" * 55],
)
def test_pgvector_table_rejects_invalid_or_overlong_derived_names(base):
    from app.services.config import derive_pgvector_table_names

    with pytest.raises(ValueError, match="PGVECTOR_TABLE"):
        derive_pgvector_table_names(base)


def test_get_pgvector_table_validates_derived_names(monkeypatch):
    from app.services.config import get_pgvector_table

    monkeypatch.setenv("PGVECTOR_TABLE", "x" * 55)
    with pytest.raises(ValueError, match="PGVECTOR_TABLE"):
        get_pgvector_table()
```

Create `tests/test_embedding_providers.py`:

```python
import math

import pytest

from app.ports.runtime import EmbeddingProvider
from app.services.embedding_providers import (
    DisabledEmbeddingProvider,
    EmbeddingConfigurationError,
    validate_embedding_batch,
)


def test_disabled_provider_satisfies_port_and_fails_without_network():
    provider = DisabledEmbeddingProvider(model_name="BAAI/bge-m3", dimension=3)

    assert isinstance(provider, EmbeddingProvider)
    with pytest.raises(EmbeddingConfigurationError, match="disabled"):
        provider.embed_query("redis consistency")
    with pytest.raises(EmbeddingConfigurationError, match="disabled"):
        provider.embed_documents(["one"])


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[0.1, 0.2]],
        [[0.1, float("nan"), 0.3]],
        [[0.1, float("inf"), 0.3]],
    ],
)
def test_embedding_batch_validation_rejects_count_dimension_and_nonfinite(vectors):
    with pytest.raises(ValueError):
        validate_embedding_batch(vectors, expected_count=1, dimension=3)


def test_embedding_batch_validation_returns_plain_finite_floats():
    result = validate_embedding_batch([[1, 2.5, 3]], expected_count=1, dimension=3)

    assert result == [[1.0, 2.5, 3.0]]
    assert all(math.isfinite(value) for value in result[0])
```

- [ ] **Step 2: Run the new tests and verify contract failures**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_embedding_config.py tests/test_embedding_providers.py -q
```

Expected: FAIL because `EmbeddingProvider`, `get_embedding_settings()`, and
`embedding_providers.py` do not exist.

- [ ] **Step 3: Add the provider port and validated settings**

Add to `app/ports/runtime.py`:

```python
@runtime_checkable
class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    model_revision: str
    dimension: int

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
```

Add a frozen settings value to `app/services/config.py` and keep the API key
out of it:

```python
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class EmbeddingSettings:
    provider_name: str
    api_base: str
    model_name: str
    model_revision: str
    dimension: int
    batch_size: int
    connect_timeout_seconds: float
    read_timeout_seconds: float


def get_embedding_settings() -> EmbeddingSettings:
    provider = os.getenv("EMBEDDING_PROVIDER", "disabled").strip().lower() or "disabled"
    if provider not in {"disabled", "siliconflow"}:
        raise ValueError("EMBEDDING_PROVIDER must be disabled or siliconflow")
    return EmbeddingSettings(
        provider_name=provider,
        api_base=os.getenv(
            "EMBEDDING_API_BASE", "https://api.siliconflow.cn/v1"
        ).strip().rstrip("/"),
        model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3").strip(),
        model_revision=os.getenv(
            "EMBEDDING_MODEL_REVISION", "siliconflow-current"
        ).strip(),
        dimension=_positive_int("EMBEDDING_DIMENSION", 1024),
        batch_size=_positive_int("EMBEDDING_BATCH_SIZE", 32),
        connect_timeout_seconds=_positive_float(
            "EMBEDDING_CONNECT_TIMEOUT_SECONDS", 5.0
        ),
        read_timeout_seconds=_positive_float(
            "EMBEDDING_READ_TIMEOUT_SECONDS", 30.0
        ),
    )
```

Add one shared identifier helper to the same module. The longest suffix is nine
ASCII bytes, so a valid base is at most 54 bytes:

```python
_PG_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def derive_pgvector_table_names(base: str) -> tuple[str, str]:
    versions = f"{base}_versions"
    releases = f"{base}_releases"
    if not _PG_IDENTIFIER.fullmatch(base):
        raise ValueError("PGVECTOR_TABLE must be a valid PostgreSQL identifier")
    if max(len(versions.encode("ascii")), len(releases.encode("ascii"))) > 63:
        raise ValueError("PGVECTOR_TABLE is too long for derived tables")
    return versions, releases
```

`get_pgvector_table()` must call `derive_pgvector_table_names(base)` before
returning the base value. `PgVectorKnowledgeStore` and
`scripts.init_local_runtime` must import this helper instead of defining their
own regex or silently relying on PostgreSQL identifier truncation.

Create `app/services/embedding_providers.py` with stable non-secret errors,
validation, and a disabled provider:

```python
from __future__ import annotations

import math


class EmbeddingConfigurationError(RuntimeError):
    pass


class EmbeddingProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(f"embedding provider failed: {code}")
        self.code = code
        self.retryable = retryable


class DisabledEmbeddingProvider:
    provider_name = "disabled"
    model_revision = "disabled"

    def __init__(self, *, model_name: str, dimension: int) -> None:
        self.model_name = model_name
        self.dimension = dimension

    def embed_query(self, text: str) -> list[float]:
        raise EmbeddingConfigurationError("embedding provider is disabled")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingConfigurationError("embedding provider is disabled")


def validate_embedding_batch(
    vectors,
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise ValueError("embedding response count mismatch")
    normalized: list[list[float]] = []
    for vector in vectors:
        values = [float(value) for value in vector]
        if len(values) != dimension:
            raise ValueError("embedding response dimension mismatch")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding response contains non-finite values")
        normalized.append(values)
    return normalized
```

- [ ] **Step 4: Update the environment template without a credential value**

Replace the current embedding block in `.env.example` with:

```text
EMBEDDING_PROVIDER=disabled
EMBEDDING_API_BASE=https://api.siliconflow.cn/v1
EMBEDDING_MODEL_NAME=BAAI/bge-m3
EMBEDDING_MODEL_REVISION=siliconflow-current
EMBEDDING_DIMENSION=1024
EMBEDDING_BATCH_SIZE=32
EMBEDDING_CONNECT_TIMEOUT_SECONDS=5
EMBEDDING_READ_TIMEOUT_SECONDS=30
SILICONFLOW_API_KEY=
```

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_embedding_config.py tests/test_embedding_providers.py tests/test_runtime_ports.py -q
git diff --check
```

Expected: PASS; no test performs a network call.

Commit:

```powershell
git add app/ports/runtime.py app/services/config.py app/services/embedding_providers.py tests/test_embedding_config.py tests/test_embedding_providers.py .env.example
git commit -m "feat: define remote embedding provider contracts"
```

### Task 2: Implement The SiliconFlow Adapter

**Files:**
- Create: `app/services/siliconflow_embeddings.py`
- Modify: `app/services/embedding_providers.py`
- Create: `tests/test_siliconflow_embeddings.py`

- [ ] **Step 1: Write failing adapter tests with `httpx.MockTransport`**

Cover query input, ordered batch reconstruction, batch size, transient retry,
permanent errors, malformed responses, timeout, and redaction. The core test
fixture must use a fake key that never reaches output:

```python
import httpx
import pytest

from app.services.embedding_providers import EmbeddingProviderError
from app.services.siliconflow_embeddings import SiliconFlowEmbeddingProvider


def make_provider(handler, *, sleeps=None, batch_size=2):
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
        jitter=lambda: 0.0,
    )


def test_documents_are_batched_and_response_indices_restore_order():
    request_sizes = []

    def handler(request):
        payload = __import__("json").loads(request.content)
        request_sizes.append(len(payload["input"]))
        data = [
            {"index": index, "embedding": [float(index), 1.0, 2.0]}
            for index in reversed(range(len(payload["input"])))
        ]
        return httpx.Response(200, json={"data": data})

    result = make_provider(handler).embed_documents(["a", "b", "c"])

    assert request_sizes == [2, 1]
    assert result == [[0.0, 1.0, 2.0], [1.0, 1.0, 2.0], [0.0, 1.0, 2.0]]


def test_429_retries_but_401_is_permanent_and_messages_are_redacted():
    statuses = iter([429, 200])
    sleeps = []

    def retry_handler(request):
        status = next(statuses)
        if status == 429:
            return httpx.Response(status, json={"message": "do not expose body"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 2, 3]}]})

    assert make_provider(retry_handler, sleeps=sleeps).embed_query("redis") == [1.0, 2.0, 3.0]
    assert len(sleeps) == 1

    provider = make_provider(lambda request: httpx.Response(401, text="test-secret-key-not-real"))
    with pytest.raises(EmbeddingProviderError) as exc:
        provider.embed_query("private query")
    assert exc.value.retryable is False
    assert "test-secret-key-not-real" not in str(exc.value)
    assert "private query" not in str(exc.value)
```

Also add parametrized tests for 500/502/503/504, `httpx.ReadTimeout`, duplicate
indices, missing indices, wrong dimensions, NaN, and a three-attempt ceiling.
Add concurrent-safe metrics tests asserting `snapshot_metrics()` returns only
request count, retry count, stable error-code counts, and p50/p95 milliseconds;
it must not contain inputs, headers, the key, URLs with credentials, or response
bodies.

- [ ] **Step 2: Run the adapter tests and verify failure**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_siliconflow_embeddings.py -q
```

Expected: FAIL because the adapter module does not exist.

- [ ] **Step 3: Implement bounded HTTP and retry behavior**

Create `SiliconFlowEmbeddingProvider` with these public semantics:

```python
class SiliconFlowEmbeddingProvider:
    provider_name = "siliconflow"
    max_attempts = 3
    transient_statuses = {429, 500, 502, 503, 504}

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
```

For each attempt, construct an `httpx.Client` with the injected transport and:

```python
response = client.post(
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
```

Never include `response.text`, request JSON, query text, headers, or exceptions
from httpx in the raised message. Map failures to stable codes such as
`http_401`, `http_429`, `network_timeout`, `invalid_json`,
`response_index_mismatch`, and `invalid_vector`. Backoff is
`0.25 * 2 ** (attempt - 1) + jitter()` and occurs only before a retry.

Parse `data` by index, reject non-integer/duplicate/out-of-range/missing
indices, then call `validate_embedding_batch()`.

Record safe attempt metrics behind a `threading.Lock`. Measure each HTTP attempt
with `perf_counter()`, increment `request_count`, increment `retry_count` only
when another attempt will occur, and count stable error codes. Expose:

```python
def snapshot_metrics(self) -> dict:
    with self._metrics_lock:
        ordered = sorted(self._latencies_ms)
        return {
            "request_count": self._request_count,
            "retry_count": self._retry_count,
            "error_counts": dict(sorted(self._error_counts.items())),
            "latency_p50_ms": percentile(ordered, 0.50),
            "latency_p95_ms": percentile(ordered, 0.95),
        }
```

Return `0.0` percentiles for no requests. Do not put these metrics on the
provider Protocol because runtime consumers do not require operational stats.

- [ ] **Step 4: Add a provider factory without reading secrets into settings**

Add to `app/services/embedding_providers.py`:

```python
def build_embedding_provider(settings=None):
    from app.services.config import get_embedding_settings

    resolved = settings or get_embedding_settings()
    if resolved.provider_name == "disabled":
        return DisabledEmbeddingProvider(
            model_name=resolved.model_name,
            dimension=resolved.dimension,
        )
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        raise EmbeddingConfigurationError("SILICONFLOW_API_KEY is not configured")
    from app.services.siliconflow_embeddings import SiliconFlowEmbeddingProvider

    return SiliconFlowEmbeddingProvider(
        api_key=api_key,
        api_base=resolved.api_base,
        model_name=resolved.model_name,
        model_revision=resolved.model_revision,
        dimension=resolved.dimension,
        batch_size=resolved.batch_size,
        connect_timeout_seconds=resolved.connect_timeout_seconds,
        read_timeout_seconds=resolved.read_timeout_seconds,
    )
```

Add factory tests proving disabled mode never reads the key and SiliconFlow
mode rejects a missing key without exposing environment contents.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_embedding_config.py tests/test_embedding_providers.py tests/test_siliconflow_embeddings.py -q
git diff --check
```

Expected: PASS with zero outbound network traffic.

Commit:

```powershell
git add app/services/embedding_providers.py app/services/siliconflow_embeddings.py tests/test_embedding_providers.py tests/test_siliconflow_embeddings.py
git commit -m "feat: add siliconflow bge-m3 adapter"
```

### Task 3: Add Versioned PostgreSQL Schema And Legacy Preservation

**Files:**
- Modify: `app/services/vector_store.py`
- Modify: `tests/test_vector_store.py`
- Modify: `tests/test_vector_store_pgvector.py`
- Modify: `scripts/init_local_runtime.py`
- Modify: `tests/test_init_local_runtime.py`

- [ ] **Step 1: Replace model fakes with a provider fake and add table-name tests**

Replace `FakeEmbeddingModel.encode()` in `tests/test_vector_store.py` with:

```python
class FakeEmbeddingProvider:
    provider_name = "fake"
    model_name = "fake-bge-m3"
    model_revision = "fake-v1"
    dimension = 3

    def embed_query(self, text):
        base = 0.1 if "redis" in text.lower() else 0.2
        return [base, base + 0.1, base + 0.2]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]
```

Update `make_store()` to inject `embedding_provider=FakeEmbeddingProvider()`.
Add assertions:

```python
assert store.legacy_table == "knowledge_chunks"
assert store.versions_table == "knowledge_chunks_versions"
assert store.releases_table == "knowledge_chunks_releases"
```

- [ ] **Step 2: Add failing DSN-gated schema and legacy migration tests**

In `tests/test_vector_store_pgvector.py`, generate a unique base table name and
test:

1. releases and versions tables are created with one active partial unique
   index and `ON DELETE RESTRICT` FK;
2. no base table is created when the legacy table is absent;
3. an empty legacy table migrates zero rows cleanly;
4. two legacy rows copy to `legacy-stage42-v1` as `legacy-unknown`, preserving
   IDs, calculated/mapped hashes, and vectors;
5. rerunning identical migration is idempotent;
6. changed legacy content under the same migration identity fails;
7. copied legacy release is `retired`, never active.

Add failing `test_init_local_runtime.py` cases with exact result assertions:

```python
def test_check_runtime_uses_versioned_tables_and_active_release_only():
    result = check_runtime(
        dsn="postgresql://example",
        table_prefix="stage44",
        knowledge_table="knowledge_stage44",
        connect=VersionedReadOnlyConnection,
    )

    assert result["initialized"] is True
    assert result["knowledge_table"] == "knowledge_stage44"
    assert result["knowledge_corpus_version"] == "stage44a-bge-m3-v1"
    assert result["knowledge_chunks"] == 25
    assert result["required_knowledge_tables"] == [
        "knowledge_stage44_versions",
        "knowledge_stage44_releases",
    ]
```

The fake read-only connection returns runtime tables plus the two derived
tables, one active release row `("stage44a-bge-m3-v1", 25)`, and captures every
statement. Assert none contains `CREATE`, `ALTER`, `INSERT`, `UPDATE`, or
`DELETE`. Add separate cases for no active release (`version=None`, count 0),
missing derived table (`initialized=False`), a 55-character base name
(`ValueError`), and a legacy base table being absent while initialization still
succeeds.

Use safe psycopg2 SQL composition in test setup/cleanup; do not interpolate a
DSN or secret into assertion messages.

- [ ] **Step 3: Run focused tests and verify schema failures**

Run without DSN:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store.py tests/test_vector_store_pgvector.py -q
```

Expected: unit failures plus DSN-gated skips because the version table API does
not exist.

With the existing `interview` PostgreSQL DSN configured, run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store_pgvector.py -q -m pgvector
```

Expected: FAIL on missing version/release schema behavior. Do not create a new
database or container.

- [ ] **Step 4: Implement derived names and exact version schema**

Change `PgVectorKnowledgeStore.__init__` to accept `embedding_provider` and set:

```python
self.embedding_provider = embedding_provider
self.embedding_dimension = embedding_provider.dimension
self.legacy_table = table_name
self.versions_table, self.releases_table = derive_pgvector_table_names(table_name)
```

Replace `_ensure_schema()` with versioned DDL equivalent to:

```sql
CREATE TABLE IF NOT EXISTS {releases} (
    corpus_version TEXT PRIMARY KEY,
    manifest_sha256 TEXT NOT NULL,
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_revision TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL CHECK (embedding_dimension > 0),
    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('staged', 'active', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS {one_active_idx}
ON {releases} ((1)) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS {versions} (
    corpus_version TEXT NOT NULL REFERENCES {releases}(corpus_version)
        ON DELETE RESTRICT,
    chunk_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    embedding_provider TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_revision TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL,
    domain TEXT NOT NULL,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding VECTOR({dimension}) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (corpus_version, chunk_id)
);
```

Add indexes on active-search columns and reusable identity:

```text
(corpus_version, source_type)
GIN(tags)
(content_sha256, embedding_provider, embedding_model,
 embedding_revision, embedding_dimension)
```

Update `scripts/init_local_runtime.check_runtime()` to call
`derive_pgvector_table_names()` and treat the derived
versions/releases tables as the required knowledge schema. Count chunks with a
read-only join on the active release. Continue returning `knowledge_table` as
the configured base name for operator clarity, and add `knowledge_corpus_version`
and `required_knowledge_tables` to the result. The read-only check must issue no
CREATE/ALTER/write statement and must not require the legacy base table.

- [ ] **Step 5: Implement optional idempotent legacy copy**

Add `migrate_legacy_rows()` that checks `to_regclass`, reads the old rows only
when the table exists, computes a normalized SHA-256 when metadata lacks one,
and writes a retired `legacy-stage42-v1` release plus its rows in one
transaction. Label unknown old identity truthfully:

```python
legacy_identity = {
    "embedding_provider": "legacy-unknown",
    "embedding_model": "legacy-unknown",
    "embedding_revision": "legacy-stage42-v1",
    "embedding_dimension": self.embedding_dimension,
}
```

Before treating a repeat as success, compare source/destination row count,
chunk IDs, content hashes, and vector dimensions. Never mark the legacy release
active and never reuse it for a SiliconFlow identity.

- [ ] **Step 6: Run PostgreSQL tests and commit**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store.py tests/test_init_local_runtime.py -q
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store_pgvector.py -q -m pgvector
git diff --check
```

Expected: PASS against isolated table names in the existing `interview`
database.

Commit:

```powershell
git add app/services/vector_store.py scripts/init_local_runtime.py tests/test_vector_store.py tests/test_vector_store_pgvector.py tests/test_init_local_runtime.py
git commit -m "feat: add versioned pgvector corpus schema"
```

### Task 4: Implement Precomputed Atomic Corpus Activation

**Files:**
- Create: `app/services/knowledge_ingestion.py`
- Create: `tests/test_knowledge_ingestion.py`
- Modify: `app/services/vector_store.py`
- Modify: `scripts/load_knowledge.py`
- Modify: `tests/test_load_knowledge.py`
- Modify: `scripts/init_local_runtime.py`
- Modify: `tests/test_init_local_runtime.py`

- [ ] **Step 1: Write failing ingestion-service tests**

Use in-memory fakes for the provider/store and assert:

- empty chunks are rejected;
- manifest count and chunk IDs must match;
- only matching content-hash/provider/model/revision/dimension vectors reuse;
- new texts are embedded in provider batches;
- wrong response count/dimension fails before `activate_corpus()`;
- provider failure leaves `activate_calls == []`;
- activation failure returns no success summary;
- a repeated identical release embeds zero documents and is idempotent;
- summary contains counts and hashes but no content, query, key, or DSN.

Define a real fixture shape:

```python
def test_provider_failure_never_opens_activation():
    store = FakeCorpusStore(reusable={})
    provider = FailingEmbeddingProvider()
    ingestor = KnowledgeCorpusIngestor(store=store, provider=provider)

    with pytest.raises(EmbeddingProviderError):
        ingestor.ingest(
            chunks=[make_chunk("redis-1")],
            manifest={
                "corpus_version": "stage44a-bge-m3-v1",
                "corpus_manifest_sha256": "a" * 64,
                "chunk_count": 1,
                "chunks": [
                    {"chunk_id": "redis-1", "content_sha256": "b" * 64}
                ],
            },
        )

    assert store.activate_calls == []
```

- [ ] **Step 2: Run the tests and verify missing ingestion boundary**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_ingestion.py tests/test_load_knowledge.py -q
```

Expected: FAIL because `KnowledgeCorpusIngestor` does not exist and the loader
still calls `upsert_chunks()`.

- [ ] **Step 3: Implement the ingestion models and service**

Create `app/services/knowledge_ingestion.py` with immutable prepared rows and a
sanitized summary:

```python
@dataclass(frozen=True)
class PreparedKnowledgeChunk:
    chunk: KnowledgeChunk
    content_sha256: str
    embedding: list[float]


class IngestionSummary(BaseModel):
    corpus_version: str
    manifest_sha256: str
    discovered: int
    reused: int
    embedded: int
    activated: int
    provider_name: str
    model_name: str
    model_revision: str
    dimension: int
```

`KnowledgeCorpusIngestor.ingest()` must:

```python
def ingest(self, *, chunks, manifest):
    corpus_version = str(manifest["corpus_version"])
    manifest_sha256 = str(manifest["corpus_manifest_sha256"])
    validate_manifest_and_chunks(manifest, chunks)
    self.store.ensure_schema()
    self.store.migrate_legacy_rows()
    reusable = self.store.find_reusable_embeddings(
        chunks,
        provider_name=self.provider.provider_name,
        model_name=self.provider.model_name,
        model_revision=self.provider.model_revision,
        dimension=self.provider.dimension,
    )
    missing = [chunk for chunk in chunks if chunk.chunk_id not in reusable]
    generated = self.provider.embed_documents(
        [f"{chunk.title}\n{chunk.content}" for chunk in missing]
    )
    validate_embedding_batch(
        generated,
        expected_count=len(missing),
        dimension=self.provider.dimension,
    )
    generated_by_id = {
        chunk.chunk_id: vector
        for chunk, vector in zip(missing, generated, strict=True)
    }
    prepared = [
        PreparedKnowledgeChunk(
            chunk=chunk,
            content_sha256=str(chunk.metadata["content_sha256"]),
            embedding=(
                reusable[chunk.chunk_id]
                if chunk.chunk_id in reusable
                else generated_by_id[chunk.chunk_id]
            ),
        )
        for chunk in chunks
    ]
    self.store.activate_corpus(
        corpus_version=corpus_version,
        manifest_sha256=manifest_sha256,
        provider=self.provider,
        chunks=prepared,
    )
    return IngestionSummary(
        corpus_version=corpus_version,
        manifest_sha256=manifest_sha256,
        discovered=len(chunks),
        reused=len(reusable),
        embedded=len(missing),
        activated=len(prepared),
        provider_name=self.provider.provider_name,
        model_name=self.provider.model_name,
        model_revision=self.provider.model_revision,
        dimension=self.provider.dimension,
    )
```

`validate_manifest_and_chunks()` must compare manifest `chunk_count`, the exact
set of manifest chunk IDs, and each manifest/chunk `content_sha256` before any
provider or store call. Reject duplicate chunk IDs before constructing either
dictionary.

- [ ] **Step 4: Implement reusable-vector reads and one-transaction activation**

In `PgVectorKnowledgeStore`, add
`find_reusable_embeddings(self, chunks, *, provider_name, model_name,
model_revision, dimension) -> dict[str, list[float]]` and
`activate_corpus(self, *, corpus_version, manifest_sha256, provider, chunks) ->
None`.

`find_reusable_embeddings()` matches all five identity fields and returns only
rows whose stored chunk ID/content hash match the requested chunk. It may read
retired releases. Use one parameterized query equivalent to:

```sql
SELECT
    chunk_id, content_sha256, embedding::text
FROM {versions}
WHERE chunk_id = ANY(%s)
  AND embedding_provider = %s
  AND embedding_model = %s
  AND embedding_revision = %s
  AND embedding_dimension = %s
ORDER BY chunk_id, created_at DESC;
```

Do not collapse rows in SQL. Iterate all returned versions newest-first and
select the first row whose `(chunk_id, content_sha256)` matches the requested
manifest entry. This allows an older exact hash to be reused when a newer
release changed the same logical chunk. A hash mismatch is not reusable.

`activate_corpus()` opens one connection and:

1. rejects an existing version with a different manifest or provider identity;
2. inserts the release as `staged`;
3. inserts every precomputed row without making provider calls;
4. validates inserted count equals manifest count;
5. changes any current active release to `retired`;
6. changes this release to `active` with `activated_at=NOW()`;
7. commits on context exit.

Use immutable inserts with `ON CONFLICT (corpus_version, chunk_id) DO NOTHING`,
then query and compare every stored ID/hash/provider/model/revision/dimension
before deciding an identical rerun is safe. Any difference raises
`ValueError("corpus version identity conflict")` and rolls back. Retire the old
active release and activate the new one only after this comparison and the
exact inserted/stored row-count check.

An identical already-active release is a no-op after full identity/count/hash
validation. No `ON CONFLICT DO UPDATE` may overwrite an immutable version row.

- [ ] **Step 5: Convert `load_knowledge.py` into an explicit versioned CLI**

Keep `build_chunks()` default behavior for Stage 42 tests, but allow a supplied
manifest so parsing and ingestion use one identity. The CLI first calls
`build_manifest(root, corpus_version=args.corpus_version)`, then
`build_chunks(root, manifest=manifest)`, then `ingestor.ingest(chunks=chunks,
manifest=manifest)`. Replace `upsert_chunks()` and add CLI args:

```text
--corpus-version (required for command-line ingestion)
--knowledge-root (default app/data/knowledge)
```

The loader prints only `IngestionSummary.model_dump_json()`. It never prints
the DSN, key, document content, or provider response.

Update `scripts/init_local_runtime.py` so `--seed-knowledge` requires
`--corpus-version` and calls the new loader with explicit keywords:

```python
if seed_knowledge:
    if not corpus_version:
        raise ValueError("corpus_version is required when seeding knowledge")
    seed_loader(
        store=knowledge_store,
        corpus_version=corpus_version,
    )
```

`count_chunks()` now counts only rows in the active release. Add tests proving
initialization without seeding reports zero/None active state, seeding forwards
the exact version, and a second identical seed is idempotent.

- [ ] **Step 6: Run unit and PostgreSQL activation tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_knowledge_ingestion.py tests/test_load_knowledge.py tests/test_knowledge_manifest.py tests/test_init_local_runtime.py -q
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store_pgvector.py -q -m pgvector -k "activation or reusable or legacy"
```

Expected: PASS. Add DSN-gated cases if any named selector has no matching test.

- [ ] **Step 7: Commit the atomic ingestion path**

```powershell
git add app/services/knowledge_ingestion.py app/services/vector_store.py scripts/load_knowledge.py scripts/init_local_runtime.py tests/test_knowledge_ingestion.py tests/test_load_knowledge.py tests/test_init_local_runtime.py tests/test_vector_store_pgvector.py
git commit -m "feat: activate complete knowledge corpus versions"
```

### Task 5: Switch Runtime Search And Evidence Lookup To Versioned Rows

**Files:**
- Modify: `app/services/vector_store.py`
- Modify: `scripts/evaluate_knowledge_retrieval.py`
- Modify: `tests/test_vector_store.py`
- Modify: `tests/test_vector_store_pgvector.py`
- Modify: `tests/test_knowledge_eval_cli.py`

- [ ] **Step 1: Add failing rerank and historical-lookup tests**

Add unit coverage for the private pure helpers `_normalize_technical_terms()`
and `_rerank_chunks()`:

```python
def make_scored_chunk(
    chunk_id,
    *,
    score,
    title,
    domain,
    tags,
    metadata=None,
):
    return KnowledgeChunk(
        chunk_id=chunk_id,
        title=title,
        content="test content",
        source_type="curated",
        domain=domain,
        tags=tags,
        metadata=metadata or {},
        score=score,
    )


def test_normalize_technical_terms_has_a_fixed_dependency_free_contract():
    assert _normalize_technical_terms("FastAPI PostgreSQL") == {
        "fastapi",
        "postgresql",
    }
    assert _normalize_technical_terms("cache-aside") == {"cache", "aside"}
    assert _normalize_technical_terms("Ｃ＋＋ Redis") == {"c++", "redis"}
    assert _normalize_technical_terms("缓存一致性 与 数据库") == {
        "缓存一致性",
        "数据库",
    }
    assert _normalize_technical_terms("the cache and database") == {
        "cache",
        "database",
    }


@pytest.mark.parametrize(
    ("aliases", "expected_score"),
    [
        ("cache-aside", 0.56),
        (["cache-aside", 7], 0.56),
        (None, 0.50),
    ],
)
def test_rerank_normalizes_alias_metadata_shapes(aliases, expected_score):
    metadata = {} if aliases is None else {"aliases": aliases}
    chunk = make_scored_chunk(
        "cache",
        score=0.50,
        title="General cache",
        domain="redis",
        tags=["redis"],
        metadata=metadata,
    )

    ranked = _rerank_chunks(
        [chunk],
        query_text="cache-aside",
        requested_tags=[],
        minimum_score=0.45,
        limit=5,
    )

    assert ranked[0].score == pytest.approx(expected_score)


def test_rerank_applies_each_signal_once_and_breaks_ties_by_id():
    chunks = [
        make_scored_chunk("b", score=0.80, title="General cache", domain="redis", tags=["redis"]),
        make_scored_chunk("a", score=0.80, title="Redis consistency", domain="redis", tags=["redis"]),
    ]

    ranked = _rerank_chunks(
        chunks,
        query_text="redis consistency consistency",
        requested_tags=["redis", "redis"],
        minimum_score=0.45,
        limit=5,
    )

    assert [item.chunk_id for item in ranked] == ["a", "b"]
    assert ranked[0].score == pytest.approx(0.90)
    assert ranked[1].score == pytest.approx(0.84)
```

Add DSN-gated tests proving:

- search returns only rows from the active release;
- SQL fetches 12 candidates even when public `limit=5`;
- `general` participates in SQL fallback filtering but never earns the 0.04
  canonical-tag boost;
- expected hashes find a retired historical row;
- no expected hash reads only the active row;
- a missing expected hash reports `version_mismatch`, not a different version;
- Reviewer-style `get_by_ids()` makes zero provider calls.

- [ ] **Step 2: Run focused tests and verify failures**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store.py tests/test_knowledge_eval_cli.py -q
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store_pgvector.py -q -m pgvector -k "search or historical or active"
```

Expected: FAIL because runtime reads still target the old table and there is no
metadata reranker.

- [ ] **Step 3: Implement active search and the exact two-signal reranker**

Query `{base}_versions` joined to the one active release. Fetch
`max(12, limit)` dense candidates after source/tag filters. Stage 44A uses an
exact pgvector cosine scan and creates no IVFFLAT or HNSW index. At 25 units,
and later at the approximately 140-unit Stage 44B target, exact scanning keeps
activation and rollback simple; ANN is considered only after acceptance data
shows a measured need.

Use this exact dependency-free normalizer and reranker:

First widen `KnowledgeChunk.metadata` from its scalar-only value union to
`dict[str, Any]`. This is required because corpus metadata may contain the
normalized `aliases: list[str]`; metadata remains internal and is never copied
into retrieval traces or public report rendering.

```python
import re
import unicodedata


_ENGLISH_TECHNICAL_TERM = re.compile(r"[a-z0-9]+(?:\+\+|#)?")
_CJK_TERM = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}")
_TECHNICAL_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "for", "with",
    "how", "what", "why",
}


def _normalize_technical_terms(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    english = {
        term
        for term in _ENGLISH_TECHNICAL_TERM.findall(normalized)
        if term not in _TECHNICAL_STOPWORDS
    }
    return english | set(_CJK_TERM.findall(normalized))


def _rerank_chunks(chunks, *, query_text, requested_tags, minimum_score, limit):
    terms = _normalize_technical_terms(query_text)
    boost_tags = {
        tag.strip().casefold()
        for tag in requested_tags
        if tag and tag.strip() and tag.strip().casefold() != "general"
    }
    ranked = []
    for chunk in chunks:
        raw_aliases = chunk.metadata.get("aliases", [])
        if isinstance(raw_aliases, str):
            aliases = [raw_aliases]
        elif isinstance(raw_aliases, list):
            aliases = [alias for alias in raw_aliases if isinstance(alias, str)]
        else:
            aliases = []
        searchable = _normalize_technical_terms(
            " ".join([chunk.title, *aliases])
        )
        exact_boost = 0.06 if terms & searchable else 0.0
        metadata_values = {chunk.domain.casefold(), *(tag.casefold() for tag in chunk.tags)}
        tag_boost = 0.04 if boost_tags & metadata_values else 0.0
        final_score = min(1.0, max(0.0, float(chunk.score or 0.0) + exact_boost + tag_boost))
        if final_score >= minimum_score:
            ranked.append(chunk.model_copy(update={"score": final_score}))
    return sorted(ranked, key=lambda item: (-float(item.score or 0.0), item.chunk_id))[:limit]
```

The normalizer performs Unicode NFKC normalization, case-folding, the fixed
English and CJK regex extraction shown above, and only the explicit English
stopword removal shown above. It does not add jieba, CJK n-grams, stemming, or
another tokenizer. Missing aliases become `[]`, one string alias becomes a
one-item list, and a list keeps only string items. Apply each boost once
regardless of duplicate terms.

Extend `last_search_trace` with safe fields only:

```text
provider_name
model_name
model_revision
corpus_version
candidate_count
hit_ids
scores
latency_ms
filters
```

- [ ] **Step 4: Implement active/default and historical/hash lookup semantics**

For `get_by_ids()`:

- with an expected hash, search all retained versions for the exact ID/hash and
  prefer the most recently activated match;
- without an expected hash, search only the active release;
- preserve requested order and deduplicate IDs;
- classify no ID anywhere as `missing`;
- classify an existing ID with no matching expected hash as
  `version_mismatch`.

Do not call `embed_query()` or `embed_documents()` in this method.

- [ ] **Step 5: Preserve Stage 42 warmup and artifact shape**

Add `warm_embedding(text)` to `PgVectorKnowledgeStore`, delegating to
`embedding_provider.embed_query()`. Update `_warm_repository()` in
`scripts/evaluate_knowledge_retrieval.py` to prefer `warm_embedding` and retain
zero for repositories without it. Add safe top-level `provider`, `model`,
`model_revision`, and `corpus_version` fields to the evaluation result, but do
not change the existing `metrics` model or thresholds.

- [ ] **Step 6: Run Stage 42 and PostgreSQL regressions**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store.py tests/test_knowledge_eval_cli.py tests/test_knowledge_eval_metrics.py tests/test_knowledge_binding_resolver.py tests/test_grounded_knowledge_agent.py -q
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store_pgvector.py -q -m pgvector
```

Expected: PASS with the v1 metric names and thresholds unchanged.

- [ ] **Step 7: Commit runtime versioned retrieval**

```powershell
git add app/services/vector_store.py scripts/evaluate_knowledge_retrieval.py tests/test_vector_store.py tests/test_vector_store_pgvector.py tests/test_knowledge_eval_cli.py
git commit -m "feat: search active corpus with stable reranking"
```

### Task 6: Wire Runtime Factory And Remove Local Model Dependencies

**Files:**
- Modify: `app/services/vector_store.py`
- Modify: `requirements.txt`
- Generate: `requirements.lock.txt`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/local-v1-runbook.md`
- Modify: `tests/test_vector_store.py`
- Modify: `tests/test_local_v1_docs.py`

- [ ] **Step 1: Add failing no-local-model and factory tests**

Update `test_from_env_defaults_to_local_postgres` to assert:

```python
store = PgVectorKnowledgeStore.from_env()
assert store.dsn == DEFAULT_POSTGRES_DSN
assert store.legacy_table == "knowledge_chunks"
assert store.embedding_provider.provider_name == "disabled"
```

Add a source/dependency contract:

```python
def test_runtime_has_no_local_embedding_dependency():
    vector_source = Path("app/services/vector_store.py").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "sentence_transformers" not in vector_source
    assert "sentence-transformers" not in requirements
    assert "langchain-huggingface" not in requirements
```

Add a subprocess test with `EMBEDDING_PROVIDER=disabled` and an empty temporary
model cache directory proving `PgVectorKnowledgeStore.from_env()` performs no
network or filesystem model creation.

- [ ] **Step 2: Run tests and verify current local dependency failures**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store.py tests/test_local_v1_docs.py -q
```

Expected: FAIL because the store still imports SentenceTransformer and the
requirements still install both local-model packages.

- [ ] **Step 3: Wire the provider factory into `from_env()`**

Replace all `embedding_model_name`, `embedding_model`, `_get_embedding_model()`,
and direct `encode()` behavior. `from_env()` becomes:

```python
@classmethod
def from_env(cls):
    settings = get_embedding_settings()
    return cls(
        dsn=get_postgres_dsn(),
        table_name=get_pgvector_table(),
        embedding_provider=build_embedding_provider(settings),
        minimum_score=float(
            os.getenv("KNOWLEDGE_MIN_SCORE", str(DEFAULT_KNOWLEDGE_MIN_SCORE))
        ),
    )
```

Keep the module-level cache behavior unchanged. Ensure disabled provider errors
continue through the existing Knowledge Agent degradation boundary instead of
failing application import.

- [ ] **Step 4: Remove unused local dependencies and regenerate the lock**

Delete these lines from `requirements.txt`:

```text
sentence-transformers>=3.0.0
langchain-huggingface>=0.1.0
```

Regenerate exactly as documented at the top of the lock file:

```powershell
& 'F:\python3.11\python.exe' -m piptools compile --allow-unsafe --generate-hashes --output-file=requirements.lock.txt requirements.txt
& 'F:\python3.11\python.exe' -m pip check
```

Expected: the lock contains neither direct package. Review the diff to confirm
only now-unreachable transitive packages disappear.

- [ ] **Step 5: Document explicit enablement and safe ingestion**

Update README and runbook with:

- default `disabled` behavior and explicit degraded Prep;
- SiliconFlow environment variable names without values;
- mandatory rotated key handling;
- no local download statement;
- `python -m scripts.load_knowledge --corpus-version stage44a-bge-m3-v1`;
- use of the existing `interview` PostgreSQL database and no new container;
- Reviewer bound evidence makes no embedding call.

Add static documentation assertions to `tests/test_local_v1_docs.py` for those
phrases and the absence of a literal API key.

- [ ] **Step 6: Run dependency and runtime tests, then commit**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_vector_store.py tests/test_local_v1_docs.py tests/test_grounded_knowledge_agent.py tests/test_report_tasks.py tests/test_round_review.py -q
& 'F:\python3.11\python.exe' -m pip check
git diff --check
```

Expected: PASS and no local embedding package in the dependency files.

Commit:

```powershell
git add app/services/vector_store.py requirements.txt requirements.lock.txt .env.example README.md docs/local-v1-runbook.md tests/test_vector_store.py tests/test_local_v1_docs.py
git commit -m "refactor: remove local embedding runtime"
```

### Task 7: Add Stage 44A Acceptance Runner And Privacy Audit

**Files:**
- Create: `scripts/run_stage44a_acceptance.py`
- Create: `scripts/audit_stage44a_artifacts.py`
- Create: `tests/test_stage44a_acceptance.py`
- Create: `tests/test_stage44a_artifact_audit.py`
- Modify: `scripts/evaluate_knowledge_retrieval.py`

- [ ] **Step 1: Write failing deterministic acceptance-runner tests**

Inject fake provider/store dependencies so tests cover the complete orchestration
without network. Assert the runner:

- requires exactly 25 manifest chunks and v1 dataset version;
- invokes ingestion before evaluation;
- requires active provider/model/revision/corpus identity to match metrics;
- treats incomplete/degraded cases as failure;
- writes no raw content, query text, DSN, key, authorization header, or absolute
  path;
- writes one safe file per retrieval case containing IDs, scores, latency, and
  status only;
- records `storage_strategy=exact_pgvector_cosine`, active chunk count, and
  retrieval p95 so the no-ANN decision is measurable;
- exits nonzero if v1 metrics fail.

- [ ] **Step 2: Write failing artifact whitelist/privacy tests**

Define the Stage 44A artifact layout:

```text
manifest.json
metrics.json
report.md
retrieval-cases/*.json
```

Test rejection of:

```text
`sk-[A-Za-z0-9_-]{8,}` token patterns
authorization/bearer headers
postgresql:// and redis:// URLs
Windows and POSIX absolute paths
email and phone values
raw_query, query_text, content, resume_text, job_description keys
unexpected files
changed files after manifest generation
metrics.passed != true
```

- [ ] **Step 3: Run tests and verify missing acceptance modules**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_stage44a_acceptance.py tests/test_stage44a_artifact_audit.py -q
```

Expected: FAIL because both scripts are missing.

- [ ] **Step 4: Implement a dependency-injectable acceptance runner**

`run_stage44a_acceptance()` accepts `repository`, `ingestor`, `dataset`,
`chunks`, `run_id`, and `run_dir` for tests. The CLI path builds real
dependencies only after checking:

```python
if os.getenv("RUN_SILICONFLOW_ACCEPTANCE") != "1":
    raise RuntimeError("RUN_SILICONFLOW_ACCEPTANCE=1 is required")
if get_embedding_settings().provider_name != "siliconflow":
    raise RuntimeError("EMBEDDING_PROVIDER=siliconflow is required")
if get_embedding_settings().model_revision == "siliconflow-current":
    raise RuntimeError("a release-specific EMBEDDING_MODEL_REVISION is required")
```

Run ingestion for `stage44a-bge-m3-v1`, evaluate all v1 cases, and save:

```python
metrics_payload = {
    "passed": bool(evaluation["metrics"]["passed"]),
    "run_id": run_id,
    "provider_name": provider.provider_name,
    "model_name": provider.model_name,
    "model_revision": provider.model_revision,
    "dimension": provider.dimension,
    "corpus_version": ingestion.corpus_version,
    "corpus_manifest_sha256": ingestion.manifest_sha256,
    "chunk_count": ingestion.activated,
    "storage_strategy": "exact_pgvector_cosine",
    "dataset_version": dataset.version,
    "retrieval_metrics": evaluation["metrics"],
    "provider_metrics": provider.snapshot_metrics(),
}
```

The report is a short generated Markdown summary of those fields only. Do not
serialize environment variables, request/response objects, exceptions, or
KnowledgeChunk content.

- [ ] **Step 5: Implement the new artifact auditor**

Reuse the Stage 42 hash-inventory pattern, but use the Stage 44A whitelist and
add blocked JSON keys recursively. The auditor must compare the committed
manifest to the actual relative-path/size/SHA-256 inventory and return a stable
non-secret error.

- [ ] **Step 6: Run acceptance unit tests and existing Stage 42 tests**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest tests/test_stage44a_acceptance.py tests/test_stage44a_artifact_audit.py tests/test_stage42_artifact_audit.py tests/test_knowledge_eval_cli.py tests/test_knowledge_eval_metrics.py -q
git diff --check
```

Expected: PASS; no real provider call occurs.

- [ ] **Step 7: Commit acceptance tooling**

```powershell
git add scripts/run_stage44a_acceptance.py scripts/audit_stage44a_artifacts.py scripts/evaluate_knowledge_retrieval.py tests/test_stage44a_acceptance.py tests/test_stage44a_artifact_audit.py
git commit -m "test: add stage 44a remote embedding acceptance"
```

### Task 8: Run Real Gates And Record Stage 44A Acceptance

**Files:**
- Create: `docs/stage-44a-remote-bge-m3-acceptance.md`
- Modify only if a defect is found: files owned by Tasks 1-7

- [ ] **Step 1: Create a PENDING acceptance record before real calls**

Create the document with `Status: PENDING` and gate rows for provider contract,
25-unit activation, v1 metrics, historical evidence, privacy, PostgreSQL,
Python, browser, JavaScript/CSS, Stage 40/42/43 regressions, and no-local-model
proof. Add a storage-strategy row recording that Stage 44A uses exact pgvector
cosine scan, creates no IVFFLAT/HNSW index, and defers ANN until a later measured
need. Do not mark PASS before every required command succeeds.

- [ ] **Step 2: Verify secret hygiene and rotate the exposed key**

In the SiliconFlow console, revoke every key previously pasted into chat.
Create a new key and set it only in the current PowerShell process. Do not put
the value in command history, plan text, `.env`, logs, screenshots, or Git.

Required non-secret environment shape:

```powershell
$env:EMBEDDING_PROVIDER = 'siliconflow'
$env:EMBEDDING_MODEL_NAME = 'BAAI/bge-m3'
$env:EMBEDDING_MODEL_REVISION = 'siliconflow-bge-m3-20260721'
$env:EMBEDDING_DIMENSION = '1024'
$env:RUN_SILICONFLOW_ACCEPTANCE = '1'
$env:PGVECTOR_TABLE = 'knowledge_chunks_stage44a_rc'
```

Set `SILICONFLOW_API_KEY` through a secure local mechanism without displaying
it. Use the existing `interview` database and Redis services; create no new
database or container.

- [ ] **Step 3: Run deterministic and PostgreSQL gates first**

Run:

```powershell
npm.cmd run build:prototype-css
Get-ChildItem app/static/*.js | ForEach-Object { node --check $_.FullName }
& 'F:\python3.11\python.exe' -m pytest tests/test_embedding_config.py tests/test_embedding_providers.py tests/test_siliconflow_embeddings.py tests/test_knowledge_ingestion.py tests/test_vector_store.py tests/test_vector_store_pgvector.py tests/test_load_knowledge.py tests/test_knowledge_eval_dataset.py tests/test_knowledge_eval_metrics.py tests/test_knowledge_eval_cli.py tests/test_stage44a_acceptance.py tests/test_stage44a_artifact_audit.py -q
```

Expected: PASS, including DSN-gated pgvector tests against isolated derived
tables.

- [ ] **Step 4: Run the opt-in SiliconFlow 25-unit/v1 acceptance**

Choose a UTC run ID without an absolute path in artifacts:

```powershell
$runId = '20260721T000000Z-stage44a-bge-m3'
& 'F:\python3.11\python.exe' -m scripts.run_stage44a_acceptance --run-id $runId --run-dir "reports/stage44a-acceptance/$runId"
& 'F:\python3.11\python.exe' -m scripts.audit_stage44a_artifacts --run-dir "reports/stage44a-acceptance/$runId" --run-id $runId
```

Expected:

- 25 discovered and active chunks;
- provider `siliconflow`, model `BAAI/bge-m3`, dimension 1024;
- Hit@3 >= 0.90;
- MRR >= 0.75;
- binding and continuity = 1.0;
- invalid reference = 0;
- false positive <= 0.20;
- p95 <= 1500 ms;
- completeness = 1.0;
- privacy audit = PASS.

If provider latency alone makes p95 fail, record the failure; do not remove or
raise the Stage 42 threshold without a separately reviewed design change.

- [ ] **Step 5: Run complete regression gates**

Run:

```powershell
& 'F:\python3.11\python.exe' -m pytest -q
$env:STAGE41_PYTHON = 'F:\python3.11\python.exe'
npm.cmd run test:browser
& 'F:\python3.11\python.exe' -m scripts.audit_stage40_artifacts --run-dir reports/stage40-acceptance/20260710T124843Z --run-id 20260710T124843Z
& 'F:\python3.11\python.exe' -m scripts.audit_stage42_artifacts --run-dir reports/stage42-acceptance/20260716T062331Z-real-model-rc --run-id 20260716T062331Z-real-model-rc
git diff --check
```

Expected: full deterministic suite and preserved Stage 40/42 artifacts PASS;
real-model tests remain explicit opt-in unless their separate credentials are
configured. If the existing Windows Playwright `webServer` cannot exit, use a
controlled uvicorn process and a gitignored config that disables Playwright
server ownership, while running the same complete browser projects.

- [ ] **Step 6: Prove no local model is downloaded or importable**

Run the no-local dependency test in a clean Python environment installed from
the regenerated lock. Confirm:

```text
sentence-transformers is not installed
langchain-huggingface is not installed
EMBEDDING_PROVIDER=disabled performs no network or cache write
Reviewer get_by_ids performs zero embedding calls
```

- [ ] **Step 7: Update acceptance from PENDING to PASS and commit**

Record exact timestamps, commit, table prefix, corpus/dataset hashes, model
revision, test counts, retrieval metrics including retrieval p95, p50/p95
provider latency, artifact
path, active corpus size, exact-scan storage strategy, and zero privacy
violations. State that ANN was not used and may be reconsidered in Stage 44B or
later only after corpus-size and p95 measurements demonstrate a need. Never
record the DSN, API key, absolute path, request content, or authorization
header.

Commit:

```powershell
git add docs/stage-44a-remote-bge-m3-acceptance.md
git commit -m "docs: record stage 44a remote embedding acceptance"
```

Stage 44B planning may begin only after this acceptance document records PASS.
