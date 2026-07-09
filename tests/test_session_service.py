import pytest

from app.graphs.interview_graph import INTERVIEW_FINISHED_MESSAGE
from app.services.prep import InterviewPlan, InterviewQuestion
from app.services.report import InterviewReport
from app.services.session import InterviewSessionStore
from app.services.session_errors import SessionVersionConflict


class FakeInterviewLLM:
    def __init__(self):
        self.last_context = None
        self.should_fail_followup = False

    def generate_plan(self, job_description: str, resume_text: str):
        return InterviewPlan(
            title="LLM generated backend interview",
            questions=[
                InterviewQuestion(
                    id="q1",
                    kind="project",
                    prompt="Introduce one project.",
                    focus="project",
                ),
                InterviewQuestion(
                    id="q2",
                    kind="technical",
                    prompt="Explain Redis.",
                    focus="Redis",
                ),
                InterviewQuestion(
                    id="q3",
                    kind="system-design",
                    prompt="Design a backend service.",
                    focus="system design",
                ),
            ],
        )

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        self.last_context = context
        if self.should_fail_followup:
            raise RuntimeError("llm failed")
        return (
            "You mentioned caching. Please explain how you protect the database "
            "when the cache becomes invalid."
        )

    def stream_followup(self, context: list[dict[str, str]]):
        self.last_context = context
        if self.should_fail_followup:
            raise RuntimeError("llm failed")
        yield "You mentioned caching. "
        yield "Please explain how you protect the database "
        yield "when the cache becomes invalid."

    def generate_report(
        self,
        plan: InterviewPlan,
        evaluation_items: list[dict],
        session_id: str,
    ) -> InterviewReport:
        raise AssertionError("Session store tests do not generate reports")


def make_plan():
    return FakeInterviewLLM().generate_plan("Backend role", "Backend resume")


def start_session(store: InterviewSessionStore):
    return store.start(
        make_plan(),
        job_description="Backend role using Python and Redis.",
        resume_text="Built a Python API with Redis.",
        job_tags=["python", "redis"],
    )


def test_start_session_returns_first_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = start_session(store)

    assert session.session_id
    assert session.current_question is not None
    assert session.current_question.kind == "project"
    assert session.status == "active"


def test_start_session_records_job_context_in_store():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = start_session(store)

    state = store.get(session.session_id)
    assert state["job_tags"] == ["python", "redis"]
    assert state["job_description"] == "Backend role using Python and Redis."
    assert state["resume_text"] == "Built a Python API with Redis."


def test_start_session_records_timing_and_empty_skip_list():
    store = InterviewSessionStore(llm=FakeInterviewLLM())

    session = start_session(store)

    state = store.get(session.session_id)
    assert state["started_at"]
    assert state["finished_at"] is None
    assert state["skipped_question_ids"] == []


def test_submit_answer_uses_llm_context_to_generate_followup():
    llm = FakeInterviewLLM()
    store = InterviewSessionStore(llm=llm)
    session = start_session(store)

    response = store.submit_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )

    assert response.follow_up == (
        "You mentioned caching. Please explain how you protect the database "
        "when the cache becomes invalid."
    )
    assert llm.last_context == [
        {"role": "interviewer", "content": "Introduce one project."},
        {
            "role": "candidate",
            "content": "I used Redis to cache frequently requested records.",
        },
    ]


def test_submit_answer_falls_back_when_llm_followup_fails():
    llm = FakeInterviewLLM()
    llm.should_fail_followup = True
    store = InterviewSessionStore(llm=llm)
    session = start_session(store)

    response = store.submit_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )

    assert response.follow_up == "请继续深挖 project：你当时做了什么取舍，为什么这样选？"


def test_submit_answer_rejects_stale_expected_version():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    with pytest.raises(SessionVersionConflict) as exc:
        store.submit_answer(
            session.session_id,
            "I used Redis.",
            expected_version=0,
            command_id="cmd-1",
        )

    assert exc.value.expected_version == 0
    assert exc.value.actual_version == 1


def test_submit_answer_is_idempotent_for_duplicate_command_id():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    first = store.submit_answer(
        session.session_id,
        "I used Redis.",
        expected_version=1,
        command_id="cmd-1",
    )
    duplicate = store.submit_answer(
        session.session_id,
        "I used Redis.",
        expected_version=1,
        command_id="cmd-1",
    )
    snapshot = store.snapshot(session.session_id)

    assert duplicate.follow_up == first.follow_up
    assert snapshot["state_version"] == 2
    assert len([m for m in snapshot["messages"] if m["role"] == "candidate"]) == 1
    assert snapshot["last_command_id"] == "cmd-1"


def test_prepare_and_complete_streaming_answer_persists_followup():
    llm = FakeInterviewLLM()
    store = InterviewSessionStore(llm=llm)
    session = start_session(store)

    prepared = store.prepare_streaming_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )
    assert prepared.stream_follow_up is True

    chunks = list(store.stream_followup(session.session_id))
    finalized = store.complete_streaming_answer(
        session.session_id,
        follow_up_text="".join(chunks),
    )

    assert chunks == [
        "You mentioned caching. ",
        "Please explain how you protect the database ",
        "when the cache becomes invalid.",
    ]
    assert finalized["pending_output"] == (
        "You mentioned caching. Please explain how you protect the database "
        "when the cache becomes invalid."
    )


def test_complete_streaming_answer_advances_version_without_replacing_user_command_id():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.prepare_streaming_answer(
        session.session_id,
        "I used Redis.",
        expected_version=1,
        command_id="cmd-stream",
    )
    finalized = store.complete_streaming_answer(
        session.session_id,
        follow_up_text="Please explain cache invalidation.",
        expected_version=2,
        command_id="cmd-stream",
    )
    snapshot = store.snapshot(session.session_id)

    assert finalized["state_version"] == 3
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-stream"
    assert snapshot["messages"][-1]["role"] == "interviewer"
    assert snapshot["messages"][-1]["content"] == "Please explain cache invalidation."


def test_complete_streaming_answer_is_structurally_idempotent_after_finalization():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.prepare_streaming_answer(
        session.session_id,
        "I used Redis.",
        expected_version=1,
        command_id="cmd-stream",
    )
    first = store.complete_streaming_answer(
        session.session_id,
        follow_up_text="Please explain cache invalidation.",
        expected_version=2,
        command_id="cmd-stream",
    )
    duplicate = store.complete_streaming_answer(
        session.session_id,
        follow_up_text="Please explain cache invalidation.",
        expected_version=2,
        command_id="cmd-stream",
    )
    snapshot = store.snapshot(session.session_id)

    assert duplicate == first
    assert snapshot["state_version"] == 3
    assert len(
        [
            message
            for message in snapshot["messages"]
            if message["role"] == "interviewer"
            and message["content"] == "Please explain cache invalidation."
        ]
    ) == 1


def test_submit_answer_advances_after_followup():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )
    second_response = store.submit_answer(
        session.session_id,
        "I used logical expiration and rate limiting.",
    )

    assert second_response.current_question is not None
    assert second_response.current_question.id == "q2"
    state = store.get(session.session_id)
    assert (
        len([message for message in state["messages"] if message["role"] == "candidate"])
        == 2
    )


def test_store_persists_graph_messages_in_order():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(
        session.session_id,
        "I used Redis to cache frequently requested records.",
    )
    store.submit_answer(
        session.session_id,
        "I used logical expiration and rate limiting.",
    )
    state = store.get(session.session_id)

    assert [message["role"] for message in state["messages"]] == [
        "interviewer",
        "candidate",
        "interviewer",
        "candidate",
        "interviewer",
    ]
    assert state["messages"][-1]["content"] == "Explain Redis."


def test_session_snapshot_includes_progress_tags_questions_and_messages():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    snapshot = store.snapshot(session.session_id)

    assert snapshot["session_id"] == session.session_id
    assert snapshot["status"] == "active"
    assert snapshot["current_index"] == 0
    assert snapshot["total_questions"] == 3
    assert snapshot["completed_questions"] == 0
    assert snapshot["current_question"]["id"] == "q1"
    assert snapshot["questions"][0]["state"] == "current"
    assert snapshot["questions"][1]["state"] == "pending"
    assert snapshot["messages"][0]["role"] == "interviewer"
    assert snapshot["job_tags"] == ["python", "redis"]


def test_session_snapshot_marks_completed_question_after_advance():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(session.session_id, "I used Redis cache-aside.")
    store.submit_answer(session.session_id, "I handled misses with fallback.")

    snapshot = store.snapshot(session.session_id)

    assert snapshot["current_question"]["id"] == "q2"
    assert snapshot["completed_questions"] == 1
    assert snapshot["questions"][0]["state"] == "answered"
    assert snapshot["questions"][1]["state"] == "current"


def test_session_snapshot_marks_finished_session_without_current_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.finish(session.session_id)

    snapshot = store.snapshot(session.session_id)

    assert snapshot["status"] == "finished"
    assert snapshot["current_question"] is None
    assert snapshot["completed_questions"] == 0
    assert snapshot["unanswered_questions"] == 3
    assert [question["state"] for question in snapshot["questions"]] == [
        "unanswered",
        "unanswered",
        "unanswered",
    ]


def test_skip_unanswered_question_marks_snapshot_skipped():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.skip(session.session_id)

    state = store.get(session.session_id)
    snapshot = store.snapshot(session.session_id)
    assert state["skipped_question_ids"] == ["q1"]
    assert snapshot["completed_questions"] == 1
    assert snapshot["answered_questions"] == 0
    assert snapshot["skipped_questions"] == 1
    assert snapshot["unanswered_questions"] == 2
    assert snapshot["questions"][0]["state"] == "skipped"
    assert snapshot["questions"][1]["state"] == "current"


def test_skip_after_answer_does_not_mark_question_skipped():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.submit_answer(session.session_id, "I built a Redis cache service.")
    store.skip(session.session_id)

    snapshot = store.snapshot(session.session_id)
    assert store.get(session.session_id)["skipped_question_ids"] == []
    assert snapshot["answered_questions"] == 1
    assert snapshot["skipped_questions"] == 0
    assert snapshot["questions"][0]["state"] == "answered"
    assert snapshot["questions"][1]["state"] == "current"


def test_skip_advances_to_next_question():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    skipped = store.skip(session.session_id)

    assert skipped.status == "active"
    assert skipped.current_question is not None
    assert skipped.current_question.id == "q2"
    assert skipped.follow_up is None
    snapshot = store.snapshot(session.session_id)
    assert snapshot["questions"][0]["state"] == "skipped"
    assert snapshot["questions"][1]["state"] == "current"
    assert snapshot["messages"][-1]["content"] == "Explain Redis."


def test_skip_last_unanswered_question_records_skip_before_finish():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.skip(session.session_id)
    store.skip(session.session_id)
    final_turn = store.skip(session.session_id)

    state = store.get(session.session_id)
    snapshot = store.snapshot(session.session_id)
    assert final_turn.status == "finished"
    assert state["skipped_question_ids"] == ["q1", "q2", "q3"]
    assert state["finished_at"]
    assert snapshot["completed_questions"] == 3
    assert snapshot["answered_questions"] == 0
    assert snapshot["skipped_questions"] == 3
    assert snapshot["unanswered_questions"] == 0
    assert [question["state"] for question in snapshot["questions"]] == [
        "skipped",
        "skipped",
        "skipped",
    ]


def test_finish_without_answer_marks_remaining_questions_unanswered():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.finish(session.session_id)

    snapshot = store.snapshot(session.session_id)
    assert snapshot["status"] == "finished"
    assert snapshot["completed_questions"] == 0
    assert snapshot["answered_questions"] == 0
    assert snapshot["skipped_questions"] == 0
    assert snapshot["unanswered_questions"] == 3
    assert [question["state"] for question in snapshot["questions"]] == [
        "unanswered",
        "unanswered",
        "unanswered",
    ]


def test_skip_last_question_finishes_session():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.skip(session.session_id)
    store.skip(session.session_id)
    final_turn = store.skip(session.session_id)

    assert final_turn.status == "finished"
    assert final_turn.current_question is None
    assert final_turn.follow_up == INTERVIEW_FINISHED_MESSAGE
    snapshot = store.snapshot(session.session_id)
    assert snapshot["status"] == "finished"
    assert snapshot["current_question"] is None
    assert snapshot["messages"][-1]["content"] == INTERVIEW_FINISHED_MESSAGE


def test_skip_finished_session_is_idempotent():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)

    store.finish(session.session_id)
    message_count = len(store.snapshot(session.session_id)["messages"])
    skipped = store.skip(session.session_id)

    assert skipped.status == "finished"
    assert skipped.current_question is None
    assert skipped.follow_up == INTERVIEW_FINISHED_MESSAGE
    snapshot = store.snapshot(session.session_id)
    assert snapshot["status"] == "finished"
    assert len(snapshot["messages"]) == message_count


def test_mark_report_processing_moves_session_into_review_phase():
    store = InterviewSessionStore(llm=FakeInterviewLLM())
    session = start_session(store)
    store.finish(session.session_id, expected_version=1, command_id="cmd-finish")

    assert store.mark_report_processing(session.session_id) is True

    snapshot = store.snapshot(session.session_id)
    assert snapshot["phase"] == "review"
    assert snapshot["phase_status"] == "active"
    assert snapshot["review_status"] == "processing"
    assert snapshot["state_version"] == 3
    assert snapshot["checkpoint_version"] == 3
    assert snapshot["last_command_id"] == "cmd-finish"
