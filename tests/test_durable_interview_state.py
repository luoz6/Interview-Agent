from app.graphs.interview_state import (
    build_initial_state,
    choose_workflow_engine,
)
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
    assert choose_workflow_engine(
        "session-fixed",
        runtime_store="postgres",
        runtime_enabled=False,
        rollout_percent=100,
    ) == "legacy"
