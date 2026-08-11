"""Unit tests for durable review state identity and reuse rules."""

import json

from app.graphs.durable_review_state import (
    DurableReviewInputManifest,
    is_reusable_for_review,
    make_durable_review_initial_state,
    review_thread_id,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import QuestionEvaluationRecord


def make_finished_state():
    return {
        "session_id": "session-1",
        "state_version": 7,
        "status": "finished",
        "plan": InterviewPlan(
            title="Backend role",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="candidate-visible question",
                    focus="ownership",
                )
            ],
        ),
        "messages": [
            {
                "role": "interviewer",
                "content": "candidate-visible question",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "candidate answer text",
                "question_id": "q1",
            },
        ],
        "skipped_question_ids": [],
        "job_description": "resume source text",
        "resume_text": "resume source text",
    }


def make_job():
    return {
        "job_id": "job-1",
        "review_engine": "langgraph-review-v1",
        "review_graph_schema_version": "langgraph-review-v1",
    }


def test_review_state_contains_references_not_interview_content():
    state = make_durable_review_initial_state(make_job(), make_finished_state())
    payload = json.dumps(state, ensure_ascii=False)

    assert "candidate answer text" not in payload
    assert "resume source text" not in payload
    assert "candidate-visible question" not in payload
    assert state["review_input_manifest"]["message_refs"][0]["content_sha256"]


def test_review_input_digest_changes_when_message_content_changes():
    first = DurableReviewInputManifest.from_finished_state(make_finished_state())
    changed = make_finished_state()
    changed["messages"][1]["content"] = "changed candidate answer"
    second = DurableReviewInputManifest.from_finished_state(changed)

    assert first.input_sha256 != second.input_sha256
    assert first.questions[0].input_sha256 != second.questions[0].input_sha256


def test_legacy_evaluation_is_not_reusable_for_durable_review():
    manifest = DurableReviewInputManifest.from_finished_state(make_finished_state())
    record = QuestionEvaluationRecord(
        session_id="session-1",
        question_id="q1",
        status="failed",
        error="legacy failed record",
    )

    assert not is_reusable_for_review(
        record,
        manifest,
        question_id="q1",
        graph_schema_version="langgraph-review-v1",
    )


def test_review_thread_is_namespaced_from_uuid_session_id():
    assert review_thread_id("job-1") == "review:job-1"
    assert review_thread_id("job-1") != "9e3c8de6-6efe-4bc5-925b-ecf5af77d403"
