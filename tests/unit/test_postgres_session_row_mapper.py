from app.adapters.postgres.row_mappers.session import SessionRowMapper
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.session_plan_binding import legacy_session_plan_binding


def test_session_row_mapper_reconstructs_followup_count_from_messages():
    plan = InterviewPlan(
        title="Interview",
        questions=[
            InterviewQuestion(
                id="q1",
                kind="technical",
                prompt="Explain an API boundary.",
                focus="API design",
            )
        ],
    )
    session_row = {
        "session_id": "session-1",
        "plan_json": plan.model_dump(mode="json"),
        "current_index": 0,
        "status": "active",
        "phase": "interview",
        "phase_status": "active",
        "review_status": "idle",
        "job_description": "Backend role",
        "resume_text": "Built APIs",
        "job_tags": ["python"],
        "decision_json": {"action": "follow_up", "follow_up": "Why?", "reason": "detail"},
        "pending_output": "Why?",
        "skipped_question_ids": [],
        "started_at": "2026-08-13T00:00:00Z",
        "finished_at": None,
        "state_version": 3,
        "checkpoint_version": 3,
        "last_checkpoint_at": "2026-08-13T00:01:00Z",
        "last_command_id": "command-1",
        "workflow_engine": "legacy",
        "graph_schema_version": None,
        "memory_policy_version": "deterministic-v1",
        "projection_sha256": None,
        "deletion_status": "active",
        "row_schema_version": SessionRowMapper.CURRENT_VERSION,
        "plan_binding_json": legacy_session_plan_binding(plan).model_dump(mode="json"),
    }
    messages = [
        {"sequence_no": 1, "role": "interviewer", "content": plan.questions[0].prompt, "question_id": "q1", "row_schema_version": "message-row-v1"},
        {"sequence_no": 2, "role": "candidate", "content": "It separates clients.", "question_id": "q1", "row_schema_version": "message-row-v1"},
        {"sequence_no": 3, "role": "interviewer", "content": "Why?", "question_id": "q1", "row_schema_version": "message-row-v1"},
    ]

    restored = SessionRowMapper.from_rows(session_row, messages)

    assert restored["current_followup_count"] == 1
