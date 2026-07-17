import json

from app.services.agent_runtime import AgentRunRecord
from app.services.agent_trace import AgentTraceRecorder


def make_record() -> AgentRunRecord:
    return AgentRunRecord(
        correlation_id="prep-123",
        causation_id="cmd-1",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id="s1",
        question_id="q1",
        state_version=2,
        command_id="cmd-1",
        evidence_ids=["redis-1"],
        status="completed",
        started_at="2026-07-16T00:00:00Z",
        finished_at="2026-07-16T00:00:00.100000Z",
        latency_ms=100,
        output_type="str",
        safe_metadata={
            "chunk_count": 2,
            "prompt": "secret prompt",
            "raw_content": "secret raw content",
            "nested": {"provider_response": "secret response"},
        },
    )


def test_agent_trace_writes_under_correlation_directory(tmp_path):
    target = AgentTraceRecorder(tmp_path).record(make_record())

    assert target is not None
    assert target.parent == tmp_path / "prep-123"
    assert "examiner_generate_followup" in target.name
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["correlation_id"] == "prep-123"
    assert payload["evidence_ids"] == ["redis-1"]


def test_agent_trace_removes_sensitive_nested_fields(tmp_path):
    target = AgentTraceRecorder(tmp_path).record(make_record())
    payload = target.read_text(encoding="utf-8")

    assert "secret prompt" not in payload
    assert "secret raw content" not in payload
    assert "secret response" not in payload
    assert '"prompt"' not in payload
    assert '"raw_content"' not in payload
    assert '"provider_response"' not in payload


def test_agent_trace_is_disabled_without_directory():
    assert AgentTraceRecorder(None).record(make_record()) is None


def test_agent_trace_correlation_cannot_escape_root(tmp_path):
    record = make_record().model_copy(
        update={
            "correlation_id": "../../outside",
            "run_id": "../run",
            "operation": "../operation",
        }
    )

    target = AgentTraceRecorder(tmp_path).record(record)

    assert target is not None
    assert target.resolve().is_relative_to(tmp_path.resolve())
