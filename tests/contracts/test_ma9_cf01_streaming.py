from __future__ import annotations

from app.a2a.runtime import build_local_a2a_runtime
from app.domain.interview.scheduling.requests import (
    GenerateFollowupRequest,
    GenerateMainQuestionRequest,
)


class _ChunkedProvider:
    def generate_main_question(self, **_kwargs):
        return "请说明 backpressure 如何处理？"

    def stream_main_question(self, **_kwargs):
        yield from ("请说明 ", "backpressure ", "如何 ", "处理？")

    def generate_followup(self, _context):
        return "请具体说明你如何处理这个故障？"

    def stream_followup(self, _context):
        yield from ("请具体说明 ", "你如何 ", "处理这个故障？")


class _PartialFailureProvider(_ChunkedProvider):
    def stream_followup(self, _context):
        yield "请具体说明 "
        raise RuntimeError("provider disconnected")


def _main_request() -> GenerateMainQuestionRequest:
    return GenerateMainQuestionRequest(
        intent={
            "schema_version": "question-intent-v1",
            "question_id": "q1",
            "position": 1,
            "kind": "technical",
            "focus": "backpressure",
            "difficulty": "intermediate",
            "assessment_goals": ["reliability"],
            "expected_minutes": 5,
            "expected_followups": 1,
        }
    )


def test_main_provider_chunks_reach_invocation_boundary_and_final_artifact():
    runtime = build_local_a2a_runtime(llm=_ChunkedProvider())
    deltas: list[str] = []

    artifact = runtime.invoker.invoke_stream(
        agent_id="interview-examiner",
        skill="generate-main-question",
        request=_main_request(),
        on_delta=deltas.append,
    )

    assert deltas == ["请说明 ", "backpressure ", "如何 ", "处理？"]
    assert len(deltas) > 1
    assert artifact.question_text == "请说明 backpressure 如何 处理？"


def test_followup_provider_chunks_reach_invocation_boundary_and_final_artifact():
    runtime = build_local_a2a_runtime(llm=_ChunkedProvider())
    deltas: list[str] = []

    artifact = runtime.invoker.invoke_stream(
        agent_id="interview-examiner",
        skill="generate-followup",
        request=GenerateFollowupRequest(
            question_id="q1",
            context=({"role": "candidate", "content": "我遇到过故障"},),
            focus="故障恢复",
            gap_id="gap-1",
        ),
        on_delta=deltas.append,
    )

    assert deltas == ["请具体说明 ", "你如何 ", "处理这个故障？"]
    assert len(deltas) > 1
    assert artifact.followup_text == "请具体说明 你如何 处理这个故障？"


def test_non_streaming_capability_rejects_stream_invocation():
    runtime = build_local_a2a_runtime(llm=_ChunkedProvider())
    try:
        runtime.invoker.invoke_stream(
            agent_id="interview-reviewer",
            skill="evaluate-answer",
            request={"state": {}},
            on_delta=lambda _delta: None,
        )
    except Exception as exc:
        assert getattr(exc, "code", None) == "streaming_unsupported"
    else:
        raise AssertionError("non-streaming capability unexpectedly streamed")


def test_partial_stream_returns_fallback_artifact_without_concatenating_text():
    runtime = build_local_a2a_runtime(llm=_PartialFailureProvider())
    deltas: list[str] = []
    artifact = runtime.invoker.invoke_stream(
        agent_id="interview-examiner",
        skill="generate-followup",
        request=GenerateFollowupRequest(
            question_id="q1",
            context=(),
            focus="故障恢复",
            gap_id="gap-1",
        ),
        on_delta=deltas.append,
    )

    assert deltas == ["请具体说明 "]
    assert artifact.followup_text == "请继续深挖 故障恢复：你当时做了什么取舍，为什么这样选？"
