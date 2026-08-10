from threading import Event, Thread

from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from tests.test_question_memory import (
    CompressorAgent,
    ParentOwnership,
    make_coordinator,
    make_state,
    make_structured_selection,
)


class BlockingCompressorAgent(CompressorAgent):
    def __init__(self):
        super().__init__()
        self.started = Event()
        self.release = Event()

    def compress(self, *, request, **kwargs):
        self.started.set()
        assert self.release.wait(timeout=5)
        return super().compress(request=request, **kwargs)


def test_concurrent_generation_attempts_create_one_artifact_and_one_active_index():
    agent = BlockingCompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    results = []

    first = Thread(
        target=lambda: results.append(
            coordinator.build_context(
                state=state,
                deterministic_context=list(selection.provider_messages),
                selection=selection,
                parent_ownership=ParentOwnership(),
            )
        )
    )
    first.start()
    assert agent.started.wait(timeout=5)

    competing = coordinator.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )
    agent.release.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert agent.calls == 1
    assert competing.route == "deterministic"
    assert len(
        index.list_active(
            session_id="session-1",
            policy_version="question-memory-v1",
            limit=10,
        )
    ) == 1


class LostOwnership:
    worker_id = "worker-1"

    def ensure_owned(self):
        raise RuntimeError("generation ownership lost")


def test_parent_ownership_loss_happens_before_question_memory_provider_call():
    agent = CompressorAgent()
    coordinator = make_coordinator(agent)
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )

    try:
        coordinator.build_context(
            state=state,
            deterministic_context=list(selection.provider_messages),
            selection=selection,
            parent_ownership=LostOwnership(),
        )
    except RuntimeError as exc:
        assert "ownership lost" in str(exc)
    else:
        raise AssertionError("ownership loss must fail closed")

    assert agent.calls == 0


class FailingScopeResolver:
    def for_interview(self, *, deployment_scope, session_id):
        raise ValueError("scope resolution failed")


def test_scope_resolution_failure_returns_original_context_without_effects():
    agent = CompressorAgent()
    index = InMemoryQuestionMemoryIndexStore()
    coordinator = make_coordinator(agent, index)
    coordinator.scope_resolver = FailingScopeResolver()
    state = make_state()
    selection = make_structured_selection(
        state,
        mandatory_question_ids=("q2",),
        selected_compressible_question_ids=("q1",),
    )
    deterministic = list(selection.provider_messages)

    result = coordinator.build_context(
        state=state,
        deterministic_context=deterministic,
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "deterministic"
    assert result.context_messages is deterministic
    assert agent.calls == 0
    assert index.list_active(
        session_id=state["session_id"],
        policy_version="question-memory-v1",
        limit=10,
    ) == []
