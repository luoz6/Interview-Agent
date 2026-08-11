from __future__ import annotations

from copy import deepcopy
from importlib import import_module
import json
from types import SimpleNamespace

import pytest

from app.services.question_memory_index import QUESTION_MEMORY_TAXONOMY


EXPECTED_FIELDS = {
    "schema_version",
    "plan_question_count",
    "current_question_index",
    "completed_question_count",
    "reviewed_question_count",
    "active_focus_tags",
    "advisory_unresolved_topic_codes",
}


def _subject():
    module = import_module("app.services.interview_status_projection")
    return (
        module.build_interview_status_projection,
        module.render_interview_status_message,
    )


def _subject_module():
    return import_module("app.services.interview_status_projection")


def _projection_payload(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value)


def _state():
    return {
        "session_id": "session-status",
        "workflow_engine": "langgraph-v2",
        "graph_schema_version": "langgraph-v2",
        "interview_status": "active",
        "current_index": 2,
        "skipped_question_ids": ["q2"],
        "plan_snapshot": {
            "title": "private plan title",
            "questions": [
                {
                    "id": "q1",
                    "kind": "technical",
                    "prompt": "private prompt one",
                    "focus": "API design",
                },
                {
                    "id": "q2",
                    "kind": "technical",
                    "prompt": "private prompt two",
                    "focus": "Testing",
                },
                {
                    "id": "q3",
                    "kind": "system-design",
                    "prompt": "private current prompt",
                    "focus": "Cache consistency and failure handling",
                },
                {
                    "id": "q4",
                    "kind": "behavioral",
                    "prompt": "private future prompt",
                    "focus": "Leadership",
                },
            ],
        },
        "messages": [
            {
                "role": "candidate",
                "content": "CANDIDATE_PRIVATE_CANARY",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "CANDIDATE_DUPLICATE_CANARY",
                "question_id": "q1",
            },
            {
                "role": "candidate",
                "content": "CURRENT_ANSWER_PRIVATE_CANARY",
                "question_id": "q3",
            },
        ],
        "job_tags": ["PRIVATE_JOB_TAG"],
        "current_focus": "FORGED_RUNTIME_FOCUS",
        "current_advisory": {
            "unresolved_topic_codes": ["missing_tradeoff"],
        },
        "memory_route": "artifact_created",
        "memory_unit_count": 999,
        "active_context_artifact_ref": "context-artifact-ref:PRIVATE_REF",
        "circuit_state": "half_open",
        "quarantine_state": "open",
        "exact_recent_message_count": 777,
        "dedup_count": 888,
    }


def _review_records():
    return [
        SimpleNamespace(
            session_id="session-status",
            question_id="q1",
            status="completed",
        ),
        # A replay of the same authoritative record must not inflate progress.
        SimpleNamespace(
            session_id="session-status",
            question_id="q1",
            status="completed",
        ),
        SimpleNamespace(
            session_id="session-status",
            question_id="q2",
            status="failed",
        ),
        SimpleNamespace(
            session_id="another-session",
            question_id="q2",
            status="completed",
        ),
        SimpleNamespace(
            session_id="session-status",
            question_id="not-in-the-plan",
            status="completed",
        ),
        # A completed Review for a future plan question is authoritative but
        # is not part of progress at the current session boundary.
        SimpleNamespace(
            session_id="session-status",
            question_id="q4",
            status="completed",
        ),
    ]


def test_projection_uses_only_authoritative_progress_and_immutable_plan_focus():
    build, _ = _subject()

    projection = build(
        _state(),
        review_records=_review_records(),
        advisory_unresolved_topic_codes=(
            "missing_tradeoff",
            "candidate free-form evaluation",
            "missing_boundary",
            "missing_tradeoff",
        ),
    )

    payload = _projection_payload(projection)
    assert set(payload) == EXPECTED_FIELDS
    assert payload == {
        "schema_version": "interview-semantic-status-v1",
        "plan_question_count": 4,
        "current_question_index": 2,
        "completed_question_count": 2,
        "reviewed_question_count": 1,
        "active_focus_tags": [
            "cache_consistency",
            "failure_handling",
            "system_design",
        ],
        "advisory_unresolved_topic_codes": [
            "missing_boundary",
            "missing_tradeoff",
        ],
    }


def test_projection_is_deterministic_bounded_and_contains_no_private_or_control_data():
    module = _subject_module()
    build = module.build_interview_status_projection
    render = module.render_interview_status_message
    first_state = _state()
    second_state = deepcopy(first_state)
    second_state["messages"] = list(reversed(second_state["messages"]))
    second_state["memory_route"] = "artifact_reused"
    second_state["memory_unit_count"] = 1

    first = render(
        build(
            first_state,
            review_records=_review_records(),
            advisory_unresolved_topic_codes=("missing_boundary",),
        )
    )
    second = render(
        build(
            second_state,
            review_records=list(reversed(_review_records())),
            advisory_unresolved_topic_codes=("missing_boundary",),
        )
    )

    assert first == second
    assert set(first) == {"role", "content"}
    assert first["role"] == "interview_semantic_status"
    assert json.loads(first["content"])["advisory_unresolved_topic_codes"] == [
        "missing_boundary"
    ]
    payload = json.loads(first["content"])
    assert module.MAX_ADVISORY_UNRESOLVED_TOPIC_CODES == 2
    assert len(payload["active_focus_tags"]) <= module.MAX_ACTIVE_FOCUS_TAGS
    assert (
        len(payload["advisory_unresolved_topic_codes"])
        <= module.MAX_ADVISORY_UNRESOLVED_TOPIC_CODES
    )
    assert set(payload["advisory_unresolved_topic_codes"]) <= {
        "missing_boundary",
        "missing_tradeoff",
    }
    assert module.INTERVIEW_STATUS_FOCUS_TAGS <= QUESTION_MEMORY_TAXONOMY
    assert module.INTERVIEW_STATUS_ADVISORY_TOPIC_CODES <= QUESTION_MEMORY_TAXONOMY
    assert module.INTERVIEW_STATUS_FOCUS_TAGS.isdisjoint(
        module.INTERVIEW_STATUS_ADVISORY_TOPIC_CODES
    )
    assert set(payload["active_focus_tags"]) <= (
        module.INTERVIEW_STATUS_FOCUS_TAGS
    )
    rendered = json.dumps(first, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "CANDIDATE_PRIVATE_CANARY",
        "CANDIDATE_DUPLICATE_CANARY",
        "CURRENT_ANSWER_PRIVATE_CANARY",
        "private prompt",
        "private plan title",
        "PRIVATE_JOB_TAG",
        "FORGED_RUNTIME_FOCUS",
        "PRIVATE_REF",
        "memory_route",
        "memory_unit_count",
        "artifact",
        "circuit",
        "quarantine",
        "exact_recent",
        "dedup",
        "session-status",
        "verified_coverage_codes",
        "required_next_check_codes",
    ):
        assert forbidden.casefold() not in rendered.casefold()


def test_projection_ignores_unvalidated_state_advisory_and_defaults_old_checkpoint_inputs():
    build, render = _subject()
    old_checkpoint = _state()
    old_checkpoint.pop("current_advisory")

    message = render(build(old_checkpoint))
    payload = json.loads(message["content"])

    assert payload["reviewed_question_count"] == 0
    assert payload["advisory_unresolved_topic_codes"] == []
    assert "missing_tradeoff" not in message["content"]


def test_projection_enforces_focus_cap_from_immutable_plan_input():
    module = _subject_module()
    state = _state()
    state["plan_snapshot"]["questions"][2]["focus"] = (
        "api design, cache consistency, distributed systems, failure handling, "
        "idempotency, observability, performance, reliability, security, testing"
    )

    payload = _projection_payload(module.build_interview_status_projection(state))

    assert len(payload["active_focus_tags"]) == module.MAX_ACTIVE_FOCUS_TAGS
    assert set(payload["active_focus_tags"]) <= module.INTERVIEW_STATUS_FOCUS_TAGS


def test_projection_accepts_terminal_authoritative_progress_boundary():
    build, _ = _subject()
    state = _state()
    state["current_index"] = len(state["plan_snapshot"]["questions"])

    payload = _projection_payload(build(state, review_records=_review_records()))

    assert payload["current_question_index"] == 4
    assert payload["completed_question_count"] == 4
    assert payload["active_focus_tags"] == []


def test_renderer_rejects_reviewed_progress_beyond_completed_progress():
    build, render = _subject()
    projection = build(_state(), review_records=_review_records())
    projection["reviewed_question_count"] = (
        projection["completed_question_count"] + 1
    )

    with pytest.raises(ValueError, match="projection is invalid"):
        render(projection)


@pytest.mark.parametrize("current_index", (-1, 5, True, 1.5))
def test_projection_fails_closed_on_invalid_authoritative_progress(current_index):
    build, _ = _subject()
    state = _state()
    state["current_index"] = current_index

    with pytest.raises((TypeError, ValueError)):
        build(state)


@pytest.mark.parametrize(
    ("status_projection_enabled", "compression_mode", "expected"),
    (
        (False, "disabled", "disabled"),
        (False, "shadow", "disabled"),
        (False, "consume", "disabled"),
        (True, "disabled", "disabled"),
        (True, "shadow", "shadow"),
        (True, "consume", "consume"),
    ),
)
def test_status_projection_mode_is_purely_resolved_from_effective_configuration(
    status_projection_enabled,
    compression_mode,
    expected,
):
    module = _subject_module()

    assert module.resolve_status_projection_mode(
        status_projection_enabled=status_projection_enabled,
        compression_mode=compression_mode,
    ) == expected
