"""Unit tests for durable review state identity and reuse rules."""

import hashlib
import json

from app.domain.interview.question_intent import QuestionIntentV1
from app.graphs.durable_review_state import (
    DurableReviewInputManifest,
    is_reusable_for_review,
    make_durable_review_initial_state,
    review_thread_id,
)
from app.services.evaluator import build_evaluation_chunks
from app.services.interview_plan_revision import (
    InterviewPlanV3,
    default_plan_configuration,
)
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.question_evaluations import QuestionEvaluationRecord
from app.services.round_review import build_single_question_review_state


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


def make_v3_finished_state():
    plan = InterviewPlanV3(
        title="JIT interview",
        configuration_snapshot=default_plan_configuration(),
        questions=(
            QuestionIntentV1(
                question_id="q1",
                position=1,
                kind="project",
                focus="Redis consistency",
                difficulty="advanced",
                assessment_goals=("failure_mode", "recovery"),
                expected_minutes=5,
                expected_followups=1,
                knowledge_binding={},
            ),
            QuestionIntentV1(
                question_id="q2",
                position=2,
                kind="technical",
                focus="future RocketMQ retry intent",
                difficulty="intermediate",
                assessment_goals=("reliability",),
                expected_minutes=5,
                expected_followups=0,
                knowledge_binding={},
            ),
        ),
    )
    return {
        "session_id": "session-v3",
        "state_version": 9,
        "status": "finished",
        "workflow_engine": "langgraph-v3",
        "graph_schema_version": "langgraph-v3",
        "plan": plan,
        "rendered_question": {
            "question_id": "q2",
            "text": "generation-stage text must not be reviewed",
        },
        "rendered_questions": {
            "q1": {
                "question_id": "q1",
                "text": "How do you recover after Redis succeeds but MQ fails?",
                "intent_sha256": "a" * 64,
                "generation_id": "generation-q1",
            }
        },
        "messages": [
            {
                "role": "interviewer",
                "content": "How do you recover after Redis succeeds but MQ fails?",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "I persist an outbox and retry delivery.",
                "question_id": "q1",
            },
            {
                "role": "interviewer",
                "content": "uncommitted future message must not be reviewed",
                "question_id": "q2",
            },
        ],
        "skipped_question_ids": [],
        "job_description": "Backend engineer",
        "resume_text": "Built distributed systems",
        "job_tags": ["redis", "rocketmq"],
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


def test_review_prompt_hash_matches_report_runtime_text_hash_contract():
    manifest = DurableReviewInputManifest.from_finished_state(make_finished_state())

    assert manifest.questions[0].prompt_sha256 == hashlib.sha256(
        b"candidate-visible question"
    ).hexdigest()


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


def test_v3_review_manifest_only_hashes_committed_rendered_questions():
    state = make_v3_finished_state()

    manifest = DurableReviewInputManifest.from_finished_state(state)

    assert [item.question_id for item in manifest.questions] == ["q1"]
    assert len(manifest.message_refs) == 2
    assert manifest.questions[0].prompt_sha256 == hashlib.sha256(
        b"How do you recover after Redis succeeds but MQ fails?"
    ).hexdigest()
    assert manifest.questions[0].intent_sha256 == "a" * 64
    assert manifest.questions[0].generation_id == "generation-q1"
    assert len(manifest.questions[0].rendered_question_sha256) == 64

    changed = make_v3_finished_state()
    changed_plan = changed["plan"]
    future = changed_plan.questions[1].model_copy(
        update={"focus": "changed unpublished future intent"}
    )
    changed["plan"] = changed_plan.model_copy(
        update={"questions": (changed_plan.questions[0], future)}
    )
    changed_manifest = DurableReviewInputManifest.from_finished_state(changed)

    assert changed_manifest.plan_sha256 == manifest.plan_sha256
    assert changed_manifest.input_sha256 == manifest.input_sha256


def test_v3_evaluation_chunks_ignore_uncommitted_and_future_questions():
    chunks = build_evaluation_chunks(make_v3_finished_state())

    assert [chunk.question_id for chunk in chunks] == ["q1"]
    assert chunks[0].question_text == (
        "How do you recover after Redis succeeds but MQ fails?"
    )
    assert chunks[0].messages[-1] == {
        "role": "candidate",
        "content": "I persist an outbox and retry delivery.",
    }


def test_v3_rendered_question_without_matching_message_is_not_published():
    state = make_v3_finished_state()
    state["messages"] = [
        message for message in state["messages"]
        if message.get("role") != "interviewer"
    ]

    assert build_evaluation_chunks(state) == []


def test_v3_single_question_review_preserves_published_question_authority():
    state = make_v3_finished_state()

    review_state = build_single_question_review_state(state, "q1")
    chunks = build_evaluation_chunks(review_state)

    assert review_state["workflow_engine"] == "langgraph-v3"
    assert review_state["rendered_questions"] == state["rendered_questions"]
    assert [chunk.question_id for chunk in chunks] == ["q1"]
    assert chunks[0].question_text == state["rendered_questions"]["q1"]["text"]
