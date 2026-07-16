import pytest
from pydantic import ValidationError

from app.services.agent_runtime import (
    AgentExecutionContext,
    AgentRunRecord,
    correlation_id_from_plan,
    evidence_ids_for_question,
)
from app.services.prep import (
    InterviewPlan,
    KnowledgeBindingSnapshot,
    PrepContext,
    PrepQuestionHint,
)


def make_plan(prep_run_id: str | None = "prep-123") -> InterviewPlan:
    context = None
    if prep_run_id is not None:
        context = PrepContext(
            summary="Grounded prep context.",
            schema_version="v2",
            question_hints=[
                PrepQuestionHint(
                    question_id="q1",
                    evidence_ids=["redis-1", "redis-1", "mysql-1"],
                )
            ],
            binding_snapshot=KnowledgeBindingSnapshot(
                prep_run_id=prep_run_id,
                corpus_manifest_sha256="manifest-123",
                status="completed",
            ),
        )
    return InterviewPlan(title="Backend interview", questions=[], prep_context=context)


def make_context() -> AgentExecutionContext:
    return AgentExecutionContext(
        correlation_id="prep-123",
        agent="report_coach",
        operation="generate_report",
        phase="review",
        session_id="s1",
    )


def test_agent_execution_context_has_stable_schema_and_unique_run_id():
    first = make_context()
    second = make_context()

    assert first.schema_version == "agent-runtime-v1"
    assert first.run_id.startswith("agent-")
    assert second.run_id.startswith("agent-")
    assert first.run_id != second.run_id


def test_agent_execution_context_deduplicates_evidence_ids():
    context = AgentExecutionContext(
        correlation_id="prep-123",
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id="s1",
        question_id="q1",
        evidence_ids=["redis-1", "redis-1", "mysql-1", ""],
    )

    assert context.evidence_ids == ["redis-1", "mysql-1"]


def test_agent_execution_context_forbids_unknown_raw_payload_fields():
    with pytest.raises(ValidationError):
        AgentExecutionContext.model_validate(
            {
                "correlation_id": "prep-123",
                "agent": "examiner",
                "operation": "generate_followup",
                "phase": "interview",
                "candidate_answer": "raw answer must not enter runtime metadata",
            }
        )


def test_correlation_id_uses_prep_run_then_session_fallback():
    assert correlation_id_from_plan(make_plan(), session_id="s1") == "prep-123"
    assert correlation_id_from_plan(make_plan(None), session_id="s1") == "s1"


def test_evidence_ids_for_question_uses_bound_hint_and_deduplicates():
    assert evidence_ids_for_question(make_plan(), "q1") == ["redis-1", "mysql-1"]
    assert evidence_ids_for_question(make_plan(), "missing") == []


def test_agent_run_record_rejects_negative_latency():
    with pytest.raises(ValidationError):
        AgentRunRecord(
            **make_context().model_dump(),
            status="completed",
            started_at="2026-07-16T00:00:00Z",
            finished_at="2026-07-16T00:00:01Z",
            latency_ms=-1,
            output_type="InterviewReport",
        )
