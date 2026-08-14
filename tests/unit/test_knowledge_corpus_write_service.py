import json
from pathlib import Path

import pytest

from app.application.knowledge import corpus_write_service as subject
from app.application.knowledge.corpus_write_service import (
    CorpusConflictError,
    CorpusWriteUnavailable,
    CorpusWriteService,
)
from app.application.knowledge.diagnostic_models import (
    CorpusEntryInput,
    CorpusReleaseRequest,
)
from scripts.build_knowledge_manifest_v2 import build_manifest_v2, iter_markdown_files


class FakeProvider:
    provider_name = "test"
    model_name = "test-embedding"
    model_revision = "revision-v1"
    dimension = 3

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("private provider failure")
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeStore:
    def __init__(self, catalog):
        self.catalog = catalog
        self.activations = []

    def get_corpus_catalog(self):
        return self.catalog

    def ensure_schema(self):
        return None

    def migrate_legacy_rows(self):
        return None

    def find_reusable_embeddings(self, chunks, **_identity):
        return {
            chunk.chunk_id: [0.3, 0.2, 0.1]
            for chunk in chunks
            if chunk.chunk_id != "rocketmq_delay_queue"
        }

    def activate_corpus(self, **payload):
        self.activations.append(payload)
        provider = payload["provider"]
        chunks = payload["chunks"]
        self.catalog = {
            "corpus_version": payload["corpus_version"],
            "manifest_sha256": payload["manifest_sha256"],
            "embedding": {
                "provider": provider.provider_name,
                "model": provider.model_name,
                "revision": provider.model_revision,
                "dimension": provider.dimension,
            },
            "chunk_count": len(chunks),
            "units": tuple(
                {
                    "unit_id": prepared.chunk.chunk_id,
                    "content_sha256": prepared.content_sha256,
                }
                for prepared in chunks
            ),
        }


def _entry(content="中" * 320):
    return CorpusEntryInput(
        unit_id="rocketmq_delay_queue",
        title="RocketMQ 延迟消息实践",
        domain="rocketmq",
        topic="delay-message",
        source_type="engineering_guide",
        content_kind="engineering_practice",
        difficulty="intermediate",
        tags=("rocketmq", "reliability"),
        aliases=("延迟队列",),
        technical_terms=("RocketMQ",),
        question_patterns=("如何实现延迟消息？", "延迟消息失败时如何处理？"),
        references=(
            {
                "title": "RocketMQ 中文文档",
                "url": "https://rocketmq.apache.org/zh/docs/featureBehavior/02delaymessage",
                "source_kind": "official_cn",
                "publisher": "Apache RocketMQ",
            },
        ),
        content=content,
    )


@pytest.fixture
def isolated_corpus(tmp_path, monkeypatch):
    corpus_root = tmp_path / "knowledge_v2"
    corpus_root.mkdir()
    source = next(path for path in iter_markdown_files(subject.CORPUS_ROOT))
    (corpus_root / "base.md").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    manifest = build_manifest_v2(corpus_root, corpus_version="base-v1")
    manifest_path = corpus_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(subject, "CORPUS_ROOT", corpus_root)
    monkeypatch.setattr(subject, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(
        subject, "CONSOLE_ENTRY_ROOT", corpus_root / "extensions" / "console"
    )
    return corpus_root, manifest


def _service(manifest, *, provider=None):
    catalog = {
        "corpus_version": manifest["corpus_version"],
        "manifest_sha256": manifest["corpus_manifest_sha256"],
    }
    provider = provider or FakeProvider()
    store = FakeStore(catalog)
    return CorpusWriteService(store=store, provider=provider), store, provider


def test_validate_is_provider_free_and_reports_governance_issues(isolated_corpus):
    _root, manifest = isolated_corpus
    service, _store, provider = _service(manifest)

    valid = service.validate(_entry())
    invalid = service.validate(_entry(content="中文太短"))

    assert valid.valid is True
    assert valid.estimated_embedding_count == 1
    assert len(valid.validation_sha256) == 64
    assert provider.calls == []
    assert invalid.valid is False
    assert invalid.estimated_embedding_count == 0
    assert invalid.issues[0].code == "CONTENT_LENGTH_INVALID"


def test_release_reuses_existing_embeddings_and_activates_complete_version(
    isolated_corpus,
):
    root, manifest = isolated_corpus
    service, store, provider = _service(manifest)
    entry = _entry()
    validation = service.validate(entry)

    response = service.release(
        CorpusReleaseRequest(
            entry=entry,
            corpus_version="base-v2",
            expected_active_manifest_sha256=manifest["corpus_manifest_sha256"],
            validation_sha256=validation.validation_sha256,
            confirm_provider_cost=True,
            confirm_activation=True,
        )
    )

    assert response.discovered == 2
    assert response.reused == 1
    assert response.embedded == 1
    assert response.activated == 2
    assert len(provider.calls) == 1
    assert len(provider.calls[0]) == 1
    assert len(store.activations) == 1
    assert (root / "extensions" / "console" / "rocketmq_delay_queue.md").is_file()
    persisted = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert persisted["corpus_version"] == "base-v2"
    assert persisted["chunk_count"] == 2
    assert response.replayed is False


def test_release_requires_both_confirmations_and_current_manifest(isolated_corpus):
    _root, manifest = isolated_corpus
    service, store, _provider = _service(manifest)
    entry = _entry()
    validation = service.validate(entry)
    base = {
        "entry": entry,
        "corpus_version": "base-v2",
        "expected_active_manifest_sha256": manifest["corpus_manifest_sha256"],
        "validation_sha256": validation.validation_sha256,
        "confirm_provider_cost": True,
        "confirm_activation": True,
    }

    with pytest.raises(ValueError):
        service.release(CorpusReleaseRequest(**{**base, "confirm_provider_cost": False}))
    with pytest.raises(ValueError):
        service.release(CorpusReleaseRequest(**{**base, "confirm_activation": False}))
    store.catalog["manifest_sha256"] = "f" * 64
    with pytest.raises(CorpusWriteUnavailable, match="identity conflict"):
        service.release(CorpusReleaseRequest(**base))
    assert store.activations == []


def test_provider_failure_does_not_activate_or_leave_managed_source(isolated_corpus):
    root, manifest = isolated_corpus
    service, store, _provider = _service(manifest, provider=FakeProvider(fail=True))
    entry = _entry()
    validation = service.validate(entry)

    with pytest.raises(RuntimeError):
        service.release(
            CorpusReleaseRequest(
                entry=entry,
                corpus_version="base-v2",
                expected_active_manifest_sha256=manifest["corpus_manifest_sha256"],
                validation_sha256=validation.validation_sha256,
                confirm_provider_cost=True,
                confirm_activation=True,
            )
        )

    assert store.activations == []
    assert not (root / "extensions" / "console" / "rocketmq_delay_queue.md").exists()
    persisted = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert persisted["corpus_version"] == "base-v1"


def test_manifest_write_failure_after_activation_is_recoverable(
    isolated_corpus,
    monkeypatch,
):
    root, manifest = isolated_corpus
    service, store, provider = _service(manifest)
    entry = _entry()
    validation = service.validate(entry)
    request = CorpusReleaseRequest(
        entry=entry,
        corpus_version="base-v2",
        expected_active_manifest_sha256=manifest["corpus_manifest_sha256"],
        validation_sha256=validation.validation_sha256,
        confirm_provider_cost=True,
        confirm_activation=True,
    )
    original_write = subject._write_json_atomic
    attempts = 0

    def fail_once(path, payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated manifest write failure")
        return original_write(path, payload)

    monkeypatch.setattr(subject, "_write_json_atomic", fail_once)

    with pytest.raises(OSError, match="simulated manifest write failure"):
        service.release(request)

    stale = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert stale["corpus_version"] == "base-v1"
    assert store.catalog["corpus_version"] == "base-v2"
    assert (root / "extensions" / "console" / "rocketmq_delay_queue.md").is_file()

    replay = service.release(request)

    recovered = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert recovered["corpus_version"] == "base-v2"
    assert recovered["corpus_manifest_sha256"] == store.catalog["manifest_sha256"]
    assert replay.replayed is True
    assert replay.embedded == 0
    assert replay.reused == replay.discovered == 2
    assert len(provider.calls) == 1
    assert len(store.activations) == 1


def test_manifest_recovery_is_idempotent(isolated_corpus, monkeypatch):
    _root, manifest = isolated_corpus
    service, store, _provider = _service(manifest)
    entry = _entry()
    validation = service.validate(entry)
    request = CorpusReleaseRequest(
        entry=entry,
        corpus_version="base-v2",
        expected_active_manifest_sha256=manifest["corpus_manifest_sha256"],
        validation_sha256=validation.validation_sha256,
        confirm_provider_cost=True,
        confirm_activation=True,
    )
    original_write = subject._write_json_atomic

    def fail_write(*_args, **_kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(subject, "_write_json_atomic", fail_write)
    with pytest.raises(OSError, match="write failed"):
        service.release(request)
    monkeypatch.setattr(subject, "_write_json_atomic", original_write)

    first = service.release(request)
    second = service.release(request)

    assert first.replayed is True
    assert second == first
    assert len(store.activations) == 1


def test_manifest_recovery_refuses_hash_mismatch(isolated_corpus):
    _root, manifest = isolated_corpus
    service, store, _provider = _service(manifest)
    store.catalog["manifest_sha256"] = "f" * 64

    with pytest.raises(CorpusWriteUnavailable, match="identity conflict"):
        service.validate(_entry())
