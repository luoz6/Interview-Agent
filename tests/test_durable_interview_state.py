from app.graphs.interview_state import (
    build_initial_state,
    choose_workflow_engine,
)
from app.graphs.durable_interview_state import make_durable_initial_state
from app.graphs.durable_interview_state_v2 import make_durable_initial_state_v2
import json
from app.services.prep import InterviewPlan, InterviewQuestion


def make_start_kwargs():
    return {
        "session_id": "session-fixed",
        "plan": InterviewPlan(
            title="Backend interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="technical",
                    prompt="Explain an API boundary.",
                    focus="Architecture",
                )
            ],
        ),
        "job_description": "Backend role",
        "resume_text": "Built APIs",
        "job_tags": ["python"],
    }


def test_legacy_session_defaults_are_explicit():
    state = build_initial_state(**make_start_kwargs())

    assert state["workflow_engine"] == "legacy"
    assert state["graph_schema_version"] is None
    assert state["projection_sha256"] is None
    assert state["memory_policy_version"] == "deterministic-v1"


def test_engine_assignment_is_stable_for_one_session():
    values = {
        choose_workflow_engine(
            "session-fixed",
            runtime_store="postgres",
            runtime_enabled=True,
            rollout_percent=25,
        )
        for _ in range(10)
    }

    assert len(values) == 1
    assert choose_workflow_engine(
        "session-fixed",
        runtime_store="memory",
        runtime_enabled=True,
        rollout_percent=100,
    ) == "legacy"


def test_state_has_no_pending_action_or_raw_source_documents():
    kwargs = make_start_kwargs()
    state = make_durable_initial_state(kwargs["session_id"], kwargs["plan"])

    assert "pending_action" not in state
    serialized = json.dumps(state, ensure_ascii=False)
    assert "job_description" not in serialized
    assert "resume_text" not in serialized
    assert "knowledge_evidence" not in serialized
    assert choose_workflow_engine(
        "session-fixed",
        runtime_store="postgres",
        runtime_enabled=False,
        rollout_percent=100,
    ) == "legacy"


def test_v2_initial_state_does_not_persist_status_projection():
    kwargs = make_start_kwargs()
    state = make_durable_initial_state_v2(
        kwargs["session_id"],
        kwargs["plan"],
    )

    # Existing checkpoints have neither a rendered semantic message nor a
    # Task-7 mode field. Their effective behavior must remain disabled.
    assert state.get("interview_semantic_status") is None
    assert state.get("status_projection_mode", "disabled") == "disabled"
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True)
    assert "interview_semantic_status" not in serialized
