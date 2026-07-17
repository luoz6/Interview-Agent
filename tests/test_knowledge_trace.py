import json

from app.agents.knowledge import KnowledgeAgent
from app.services.knowledge_trace import KnowledgeTraceRecorder
from tests.test_grounded_knowledge_agent import GroundedPlanLLM, make_repository


def test_trace_recorder_writes_only_sanitized_operational_fields(tmp_path):
    recorder = KnowledgeTraceRecorder(root_dir=tmp_path)

    path = recorder.record(
        prep_run_id="prep-1",
        stage="retrieval",
        payload={
            "status": "degraded",
            "degraded_reason": "knowledge_unavailable",
            "query_text": "redis consistency interview evidence",
            "hit_ids": ["redis_consistency"],
            "scores": {"redis_consistency": 0.91},
            "dsn": "postgresql://secret:secret@localhost/private",
            "api_key": "sk-secret",
            "resume_text": "Alice private resume",
            "content": "Internal benchmark answer",
            "embedding": [0.1, 0.2],
            "provider_response": {"raw": "private"},
        },
    )

    saved = path.read_text(encoding="utf-8")
    payload = json.loads(saved)
    assert payload["prep_run_id"] == "prep-1"
    assert payload["query_text"] == "redis consistency interview evidence"
    assert payload["hit_ids"] == ["redis_consistency"]
    assert "postgresql://" not in saved
    assert "sk-secret" not in saved
    assert "Alice private resume" not in saved
    assert "Internal benchmark answer" not in saved
    assert "embedding" not in payload
    assert "provider_response" not in payload


def test_grounded_agent_records_compact_trace_without_knowledge_content(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("KNOWLEDGE_TRACE_DIR", str(tmp_path))

    plan = KnowledgeAgent(
        llm=GroundedPlanLLM(),
        vector_store=make_repository(),
    ).generate_plan(
        job_description="Backend Engineer using Redis and Kafka.",
        resume_text="Alice built private Redis and Kafka systems.",
    )

    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    saved = files[0].read_text(encoding="utf-8")
    payload = json.loads(saved)
    assert payload["prep_run_id"] == plan.prep_context.binding_snapshot.prep_run_id
    assert payload["status"] == "completed"
    assert len(payload["queries"]) == 2
    assert all(
        isinstance(query["latency_ms"], float) and query["latency_ms"] >= 0
        for query in payload["queries"]
    )
    assert "Internal benchmark answer" not in saved
    assert "Alice" not in saved
    assert "content_sha256" not in saved


def test_trace_recorder_is_disabled_without_directory(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_TRACE_DIR", raising=False)

    assert KnowledgeTraceRecorder.from_env().record(
        prep_run_id="prep-disabled",
        stage="retrieval",
        payload={"status": "completed"},
    ) is None


def test_knowledge_trace_keeps_legacy_substring_blocking(tmp_path):
    recorder = KnowledgeTraceRecorder(root_dir=tmp_path)

    path = recorder.record(
        prep_run_id="prep-legacy-policy",
        stage="retrieval",
        payload={
            "hit_ids": ["redis-1"],
            "content_sha256": "a" * 64,
            "safe_counter": 1,
        },
    )

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["hit_ids"] == ["redis-1"]
    assert body["safe_counter"] == 1
    assert "content_sha256" not in body
