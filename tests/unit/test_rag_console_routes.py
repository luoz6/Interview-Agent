from fastapi.testclient import TestClient

from app.api.shared.dependencies import (
    get_rag_corpus_write_service,
    get_rag_diagnostics_service,
)
from app.main import app
from app.runtime.config import use_environment
from app.application.knowledge.diagnostics_service import DiagnosticCapacityExhausted


class FakeDiagnostics:
    def overview(self):
        return {
            "schema_version": "rag-overview-v2",
            "generated_at": "2026-08-13T00:00:00Z",
            "project_scope": "learning_project_technical_showcase",
            "current_engine": "legacy",
            "comparison_engines": ["legacy", "hybrid-v2"],
            "remote_reranker_enabled": False,
            "evidence_gate_enabled": True,
            "corpus": {"version": "v1", "manifest_sha256": "a" * 64, "chunk_count": 1},
            "embedding": {"provider": "test", "model": "test", "revision": "v1", "dimension": 3},
            "profiles": [],
            "component_versions": {},
            "capabilities": {
                "console_read": True,
                "live_execution": False,
                "corpus_write": False,
                "access_mode": "loopback",
            },
            "technologies": ["Semantic Retrieval", "Lexical Retrieval"],
            "diagnostic_dataset": {
                "label": "Demo Diagnostic Dataset",
                "curation": "Curated / Machine-assisted",
                "tuning_case_count": 75,
                "diagnostic_case_count": 25,
                "human_annotator_count": 0,
                "production_claim": False,
            },
            "experiment_findings": ["Hybrid 尚未被证明整体优于 Legacy。"],
            "demo_boundaries": ["仅用于本地技术展示。"],
        }

    def evidence_trace(self, trace_id):
        return {
            "schema_version": "rag-evidence-trace-v1",
            "trace_id": trace_id,
            "generated_at": "2026-08-13T00:00:00Z",
            "stages": [{
                "stage": "followup_decision",
                "recording_status": "not_recorded",
                "evidence_ids": [],
                "corpus_manifest_sha256": "",
                "note": "No value inferred.",
            }],
        }

    def inspect(self, payload):
        raise AssertionError("invalid requests must not reach diagnostics service")

    def compare(self, payload):
        raise AssertionError("invalid requests must not reach diagnostics service")


class SaturatedDiagnostics(FakeDiagnostics):
    def inspect(self, payload):
        raise DiagnosticCapacityExhausted("private capacity detail")


class FakeCorpusWriter:
    def __init__(self):
        self.validated = 0
        self.created = 0

    def validate(self, entry, corpus_version):
        self.validated += 1
        return {
            "schema_version": "rag-corpus-validation-v2",
            "valid": True,
            "validation_sha256": "b" * 64,
            "current_corpus_version": "memory-p1-zh-v4",
            "current_manifest_sha256": "a" * 64,
            "current_chunk_count": 31,
            "target_corpus_version": corpus_version,
            "target_manifest_sha256": "d" * 64,
            "target_chunk_count": 32,
            "added_chunk_count": 1,
            "reused_embedding_count": 31,
            "content_sha256": "c" * 64,
            "chinese_character_count": 320,
            "provider_call_required": True,
            "estimated_embedding_count": 1,
            "provider_name": "siliconflow",
            "model_name": "BAAI/bge-m3",
            "model_revision": "revision-v1",
            "issues": [],
        }

    def create_version(self, payload):
        self.created += 1
        return {
            "schema_version": "rag-corpus-version-v1",
            "corpus_version": payload.corpus_version,
            "manifest_sha256": "d" * 64,
            "discovered": 32,
            "reused": 31,
            "embedded": 1,
            "activated": 32,
            "provider_name": "siliconflow",
            "model_name": "BAAI/bge-m3",
            "model_revision": "revision-v1",
            "dimension": 1024,
        }


def _corpus_entry():
    return {
        "unit_id": "rocketmq_delay_queue",
        "title": "RocketMQ 延迟消息实践",
        "domain": "rocketmq",
        "topic": "delay-message",
        "source_type": "engineering_guide",
        "content_kind": "engineering_practice",
        "difficulty": "intermediate",
        "tags": ["rocketmq", "reliability"],
        "aliases": ["延迟队列"],
        "technical_terms": ["RocketMQ"],
        "question_patterns": ["如何实现延迟消息？", "延迟消息失败时如何处理？"],
        "references": [{
            "title": "RocketMQ 中文文档",
            "url": "https://rocketmq.apache.org/zh/docs/featureBehavior/02delaymessage",
            "source_kind": "official_cn",
            "publisher": "Apache RocketMQ",
        }],
        "content": "中" * 320,
    }


def test_console_is_404_by_default():
    client = TestClient(app)
    assert client.get("/api/rag/overview").status_code == 404
    assert client.post("/api/rag/inspections", json={"query_text": "Redis"}).status_code == 404
    assert client.post(
        "/api/rag/inspections/compare", json={"query_text": "Redis"}
    ).status_code == 404
    assert client.post(
        "/api/rag/corpus/drafts/validate", json={"entry": _corpus_entry(), "corpus_version": "memory-p1-zh-v5"}
    ).status_code == 404


def test_corpus_write_requires_loopback_and_explicit_capability():
    writer = FakeCorpusWriter()
    app.dependency_overrides[get_rag_corpus_write_service] = lambda: writer
    try:
        with use_environment(
            {
                "RAG_CONSOLE_ENABLED": "true",
                "RAG_CORPUS_WRITE_ENABLED": "true",
            }
        ):
            remote = TestClient(app, client=("198.51.100.7", 50000))
            assert remote.post(
                "/api/rag/corpus/drafts/validate",
                json={"entry": _corpus_entry(), "corpus_version": "memory-p1-zh-v5"},
            ).status_code == 404
            local = TestClient(app, client=("127.0.0.1", 50000))
            response = local.post(
                "/api/rag/corpus/drafts/validate",
                json={"entry": _corpus_entry(), "corpus_version": "memory-p1-zh-v5"},
            )
            assert response.status_code == 200
            assert response.json()["estimated_embedding_count"] == 1
            assert writer.validated == 1
    finally:
        app.dependency_overrides.pop(get_rag_corpus_write_service, None)


def test_corpus_version_response_does_not_reflect_content():
    private_content = "敏感资料" * 110
    writer = FakeCorpusWriter()
    app.dependency_overrides[get_rag_corpus_write_service] = lambda: writer
    try:
        with use_environment(
            {
                "RAG_CONSOLE_ENABLED": "true",
                "RAG_CORPUS_WRITE_ENABLED": "true",
            }
        ):
            client = TestClient(app, client=("127.0.0.1", 50000))
            entry = {**_corpus_entry(), "content": private_content}
            response = client.post(
                "/api/rag/corpus/versions",
                json={
                    "entry": entry,
                    "corpus_version": "memory-p1-zh-v5",
                    "expected_active_manifest_sha256": "a" * 64,
                    "expected_target_manifest_sha256": "d" * 64,
                    "validation_sha256": "b" * 64,
                    "confirm_create_version": True,
                },
            )
            assert response.status_code == 200
            assert private_content not in response.text
            assert writer.created == 1
    finally:
        app.dependency_overrides.pop(get_rag_corpus_write_service, None)


def test_loopback_capability_allows_safe_overview_but_not_live_inspection():
    app.dependency_overrides[get_rag_diagnostics_service] = FakeDiagnostics
    try:
        with use_environment({"RAG_CONSOLE_ENABLED": "true"}):
            client = TestClient(app, client=("127.0.0.1", 50000))
            response = client.get("/api/rag/overview")
            assert response.status_code == 200
            assert response.json()["current_engine"] == "legacy"
            assert client.post(
                "/api/rag/inspections", json={"query_text": "Redis"}
            ).status_code == 404
    finally:
        app.dependency_overrides.pop(get_rag_diagnostics_service, None)


def test_forwarded_header_does_not_turn_remote_client_into_loopback():
    app.dependency_overrides[get_rag_diagnostics_service] = FakeDiagnostics
    try:
        with use_environment({"RAG_CONSOLE_ENABLED": "true"}):
            client = TestClient(app, client=("198.51.100.7", 50000))
            response = client.get(
                "/api/rag/overview",
                headers={"x-forwarded-for": "127.0.0.1"},
            )
            assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(get_rag_diagnostics_service, None)


def test_no_persistent_inspection_get_endpoint_exists():
    client = TestClient(app)
    assert client.get("/api/rag/inspections/any-id").status_code == 404


def test_invalid_live_query_is_not_reflected_by_validation_response():
    private_query = "PRIVATE QUERY MUST NOT BE REFLECTED"
    app.dependency_overrides[get_rag_diagnostics_service] = FakeDiagnostics
    try:
        with use_environment(
            {
                "RAG_CONSOLE_ENABLED": "true",
                "RAG_LIVE_EXECUTION_ENABLED": "true",
            }
        ):
            client = TestClient(app, client=("127.0.0.1", 50000))
            response = client.post(
                "/api/rag/inspections",
                json={"query_text": private_query, "unexpected": private_query},
            )
            assert response.status_code == 422
            assert response.json()["code"] == "RAG_REQUEST_INVALID"
            assert private_query not in response.text
            compare = client.post(
                "/api/rag/inspections/compare",
                json={"query_text": private_query, "unexpected": private_query},
            )
            assert compare.status_code == 422
            assert compare.json()["code"] == "RAG_REQUEST_INVALID"
            assert private_query not in compare.text
    finally:
        app.dependency_overrides.pop(get_rag_diagnostics_service, None)


def test_live_diagnostic_saturation_has_stable_non_sensitive_error():
    app.dependency_overrides[get_rag_diagnostics_service] = SaturatedDiagnostics
    try:
        with use_environment(
            {
                "RAG_CONSOLE_ENABLED": "true",
                "RAG_LIVE_EXECUTION_ENABLED": "true",
            }
        ):
            client = TestClient(app, client=("127.0.0.1", 50000))
            response = client.post(
                "/api/rag/inspections",
                json={"query_text": "private Redis query"},
            )
            assert response.status_code == 429
            assert response.json()["detail"]["code"] == (
                "RAG_DIAGNOSTIC_CAPACITY_EXHAUSTED"
            )
            assert "private Redis query" not in response.text
            assert "private capacity detail" not in response.text
    finally:
        app.dependency_overrides.pop(get_rag_diagnostics_service, None)


def test_evidence_trace_is_capability_protected_and_safe():
    app.dependency_overrides[get_rag_diagnostics_service] = FakeDiagnostics
    try:
        client = TestClient(app, client=("127.0.0.1", 50000))
        assert client.get("/api/rag/evidence-traces/session-1").status_code == 404
        with use_environment({"RAG_CONSOLE_ENABLED": "true"}):
            response = client.get("/api/rag/evidence-traces/session-1")
            assert response.status_code == 200
            rendered = response.text
            assert "query_text" not in rendered
            assert '"chain_of_thought"' not in rendered
    finally:
        app.dependency_overrides.pop(get_rag_diagnostics_service, None)
