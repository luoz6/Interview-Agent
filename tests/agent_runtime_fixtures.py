"""Shared deterministic fixtures for agent runtime tests."""

from app.services.agent_runtime import AgentRunRecord


def make_record(
    session_id: str | None = None,
    *,
    parent_run_id: str | None = None,
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id="agent-run-1",
        correlation_id="prep-1",
        causation_id="cmd-1",
        parent_run_id=parent_run_id,
        agent="examiner",
        operation="generate_followup",
        phase="interview",
        session_id=session_id,
        question_id="q1" if session_id else None,
        state_version=2 if session_id else None,
        command_id="cmd-1" if session_id else None,
        evidence_ids=["redis-1"],
        attempt_number=2,
        status="completed",
        started_at="2026-07-17T00:00:00Z",
        finished_at="2026-07-17T00:00:00.050000Z",
        latency_ms=50,
        output_type="str",
        safe_metadata={"chunk_count": 2},
    )
