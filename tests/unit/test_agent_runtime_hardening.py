"""Unit tests for agent runtime failure isolation and metadata privacy."""

import logging

import pytest

import app.services.agent_runtime as agent_runtime_module
from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentExecutionRunner,
    AgentFallback,
)
from app.services.trace_sanitization import sanitize_agent_safe_metadata


class CapturingRecorder:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


class FailingRecorder:
    def record(self, record):
        raise RuntimeError(
            "postgresql://private:secret@localhost/db "
            "token=private prompt=private C:\\private\\trace.json"
        )


def make_context() -> AgentExecutionContext:
    return AgentExecutionContext(
        correlation_id="prep-123",
        agent="examiner",
        operation="stream_followup",
        phase="interview",
        session_id="session-original",
        question_id="q1",
        evidence_ids=["evidence-original"],
    )


def test_run_records_entry_context_snapshot_when_provider_mutates_original():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)
    context = make_context()

    def invoke():
        context.session_id = "session-mutated"
        context.evidence_ids.append("evidence-mutated")
        return "result"

    assert runner.run(context, invoke) == "result"
    assert recorder.records[0].session_id == "session-original"
    assert recorder.records[0].evidence_ids == ["evidence-original"]


def test_stream_snapshots_context_at_call_before_first_next():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)
    context = make_context()

    stream = runner.stream(context, lambda: iter(["chunk"]))
    context.session_id = "session-mutated-before-next"
    context.evidence_ids.append("evidence-mutated-before-next")

    assert list(stream) == ["chunk"]
    assert recorder.records[0].session_id == "session-original"
    assert recorder.records[0].evidence_ids == ["evidence-original"]


def test_stream_latency_clock_starts_at_first_next(monkeypatch):
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)
    ticks = iter([100.0, 104.0])
    perf_counter_calls = []

    def fake_perf_counter():
        perf_counter_calls.append(True)
        return next(ticks)

    monkeypatch.setattr(
        agent_runtime_module,
        "perf_counter",
        fake_perf_counter,
    )

    stream = runner.stream(make_context(), lambda: iter(["chunk"]))
    assert perf_counter_calls == []

    assert list(stream) == ["chunk"]
    assert len(perf_counter_calls) == 2
    assert recorder.records[0].latency_ms == 4000.0


def test_metadata_callback_failure_does_not_replace_successful_output(caplog):
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    with caplog.at_level(logging.WARNING):
        result = runner.run(
            make_context(),
            lambda: "provider-result",
            metadata=lambda _output: (_ for _ in ()).throw(
                RuntimeError("private provider response")
            ),
        )

    assert result == "provider-result"
    assert recorder.records[0].status == "completed"
    assert recorder.records[0].safe_metadata == {}
    assert [record.message for record in caplog.records].count(
        "agent telemetry helper failed"
    ) == 1
    assert caplog.records[0].error_code == "agent_metadata_extraction_failed"
    assert "private provider response" not in caplog.text


def test_classifier_failure_does_not_replace_successful_output(caplog):
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    with caplog.at_level(logging.WARNING):
        result = runner.run(
            make_context(),
            lambda: "provider-result",
            classify=lambda _output: (_ for _ in ()).throw(
                RuntimeError("private classification detail")
            ),
            metadata=lambda _output: {"feedback_count": 2},
        )

    assert result == "provider-result"
    assert recorder.records[0].status == "completed"
    assert recorder.records[0].safe_metadata == {"feedback_count": 2}
    assert caplog.records[0].error_code == (
        "agent_outcome_classification_failed"
    )
    assert "private classification detail" not in caplog.text


@pytest.mark.parametrize("invalid_outcome", [None, {}, "completed"])
def test_invalid_classifier_result_does_not_replace_successful_output(
    caplog, invalid_outcome
):
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    with caplog.at_level(logging.WARNING):
        result = runner.run(
            make_context(),
            lambda: "provider-result",
            classify=lambda _output: invalid_outcome,
        )

    assert result == "provider-result"
    assert recorder.records[0].status == "completed"
    assert caplog.records[0].error_code == (
        "agent_outcome_classification_failed"
    )


def test_fallback_metadata_failure_preserves_degraded_output(caplog):
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    with caplog.at_level(logging.WARNING):
        result = runner.run(
            make_context(),
            lambda: (_ for _ in ()).throw(RuntimeError("provider failed")),
            fallback=lambda _exc: AgentFallback("fallback", "provider_error"),
            metadata=lambda _output: (_ for _ in ()).throw(
                RuntimeError("private fallback metadata")
            ),
        )

    assert result == "fallback"
    assert recorder.records[0].status == "degraded"
    assert recorder.records[0].fallback_reason == "provider_error"
    assert recorder.records[0].safe_metadata == {}
    assert caplog.records[0].error_code == "agent_metadata_extraction_failed"
    assert "private fallback metadata" not in caplog.text


def test_runner_sanitizes_metadata_before_any_recorder_sees_it(caplog):
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    class PrivateObject:
        def __str__(self):
            return "private object content"

    with caplog.at_level(logging.WARNING):
        result = runner.run(
            make_context(),
            lambda: "result",
            metadata=lambda _output: {
                "feedback_count": 2,
                "report_path": "microbatch",
                "prompt": "private",
                "user_prompt": "private",
                "artifact": "C:\\private\\trace.json",
                "note": "I used cache aside in production",
                "object": PrivateObject(),
                "nested": {"provider_response_debug": "private"},
            },
        )

    assert result == "result"
    assert recorder.records[0].safe_metadata == {
        "feedback_count": 2,
        "report_path": "microbatch",
    }
    serialized = recorder.records[0].model_dump_json()
    assert "private" not in serialized
    assert "cache aside" not in serialized
    assert "agent metadata sanitized" in caplog.text


def test_safe_metadata_policy_is_no_weaker_than_agent_trace_blocked_keys():
    result = sanitize_agent_safe_metadata(
        {
            "answer": "private",
            "prompt": "private",
            "raw_response": "private",
            "feedback_count": 2,
        }
    )

    assert result.value == {"feedback_count": 2}
    assert result.rejected_count == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"summary": "candidateSecret123"},
        {"debug": "sk-proj-ABCDEF1234567890"},
        {"value": "postgresql://user:password@host/db"},
        {"detail": "Bearer.eyJhbGciOiJIUzI1NiJ9.signature"},
    ],
)
def test_safe_metadata_rejects_sensitive_or_free_text_under_generic_keys(payload):
    result = sanitize_agent_safe_metadata(payload)

    assert result.value == {}
    assert result.rejected_count >= 1


def test_safe_metadata_keeps_only_declared_machine_string_fields():
    result = sanitize_agent_safe_metadata(
        {
            "report_path": "microbatch",
            "knowledge_status": "degraded",
            "question_ids": ["q1", "q2"],
        }
    )

    assert result.value == {
        "report_path": "microbatch",
        "knowledge_status": "degraded",
        "question_ids": ["q1", "q2"],
    }


def test_top_level_recorder_failure_is_logged_without_exception_text(caplog):
    runner = AgentExecutionRunner(recorder=FailingRecorder())

    with caplog.at_level(logging.WARNING):
        assert runner.run(make_context(), lambda: "result") == "result"

    assert len(caplog.records) == 1
    assert caplog.records[0].message == "agent run emission failed"
    assert caplog.records[0].error_code == "agent_run_emission_failed"
    assert "postgresql://" not in caplog.text
    assert "private" not in caplog.text


def test_failed_fallback_iterator_retains_selection_reason():
    recorder = CapturingRecorder()
    runner = AgentExecutionRunner(recorder=recorder)

    def failed_fallback():
        yield "fallback-partial"
        raise TypeError("fallback delivery failed")

    stream = runner.stream(
        make_context(),
        lambda: (_ for _ in ()).throw(RuntimeError("provider failed")),
        fallback=lambda _exc: AgentFallback(
            failed_fallback(),
            "provider_error",
        ),
    )

    assert next(stream) == "fallback-partial"
    with pytest.raises(TypeError, match="fallback delivery failed"):
        next(stream)

    assert recorder.records[0].status == "failed"
    assert recorder.records[0].fallback_reason == "provider_error"
    assert recorder.records[0].error_code == "TypeError"
