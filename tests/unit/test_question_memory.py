from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from tests.question_memory_fixtures import (
    CompressorAgent,
    ParentOwnership,
    deterministic_context,
    make_coordinator,
    make_state,
)


def test_question_memory_creates_one_artifact_and_removes_exact_overlap():
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)

    result = coordinator.build_context(
        state=make_state(),
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    assert agent.calls == 1
    assert result.route == "artifact_created"
    assert result.memory_unit_count == 1
    assert result.context_messages[0]["role"] == "conversation_summary"
    assert "old cache question" not in [
        item["content"] for item in result.context_messages
    ]
    assert "current system answer" in [
        item["content"] for item in result.context_messages
    ]


def test_question_memory_reuses_index_without_second_provider_call():
    agent = CompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    state = make_state()

    first = coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )
    second = coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    assert first.route == "artifact_created"
    assert second.route == "memory_index_retrieved"
    assert agent.calls == 1


def test_corrected_answer_creates_new_manifest_and_supersedes_old_entry():
    agent = CompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    state = make_state()
    coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )
    previous = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )
    state["messages"].append(
        {"role": "candidate", "content": "corrected cache answer", "question_id": "q1"}
    )

    coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )
    current = index.get_active(
        session_id="session-1",
        question_id="q1",
        policy_version="question-memory-v1",
    )

    assert agent.calls == 2
    assert current.source_manifest_sha256 != previous.source_manifest_sha256
    assert current.supersedes_artifact_ref == previous.artifact_ref
    assert index.get_historical(previous.artifact_ref).status == "superseded"


def test_current_question_is_never_replaced_by_question_memory_summary():
    state = make_state()
    state["current_index"] = 0
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)

    result = coordinator.build_context(
        state=state,
        deterministic_context=deterministic_context(),
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "deterministic"
    assert agent.calls == 0
