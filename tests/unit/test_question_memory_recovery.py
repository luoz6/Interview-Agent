from __future__ import annotations

from threading import Event, Thread
from importlib import import_module

from app.services.context_compression_runner import ContextCompressionRunner
from app.adapters.memory.context_artifacts import (
    InMemoryContextArtifactStore,
)

from app.services.in_memory_question_memory_index import (
    InMemoryQuestionMemoryIndexStore,
)
from tests.question_memory_fixtures import (
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


class ProviderUnavailableAgent(CompressorAgent):
    def compress(self, *, request, **kwargs):
        self.calls += 1
        self.requests.append(request)
        raise TimeoutError("PRIVATE PROVIDER ERROR CANARY")


class InvalidPayloadAgent(CompressorAgent):
    def compress(self, *, request, **kwargs):
        payload = super().compress(request=request, **kwargs)
        payload["claims"][0]["supporting_excerpts"] = [
            "candidate answer canary absent from source"
        ]
        return payload


def _guarded_coordinator(agent, *, failure_store=None):
    domain = import_module(
        "app.services.context_compression_failure_containment"
    )
    memory = import_module(
        "app.services.in_memory_context_compression_failure_store"
    )
    failure_store = failure_store or (
        memory.InMemoryContextCompressionFailureStore()
    )
    containment = domain.ContextCompressionFailureContainment(
        store=failure_store,
        config=domain.FailureContainmentConfig(
            provider_circuit_threshold=3,
            provider_circuit_cooldown_seconds=300,
            validation_quarantine_threshold=2,
            validation_quarantine_cooldown_seconds=3600,
            failure_state_lease_seconds=60,
        ),
    )
    coordinator = make_coordinator(agent)
    coordinator.runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=containment,
    )
    return coordinator, failure_store


def _build_once(coordinator, state):
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
    assert result.context_messages == deterministic
    return result


def test_provider_circuit_returns_deterministic_context_and_skips_fourth_call():
    agent = ProviderUnavailableAgent()
    coordinator, _ = _guarded_coordinator(agent)
    state = make_state()

    for _ in range(4):
        _build_once(coordinator, state)

    assert agent.calls == 3


def test_validation_quarantine_returns_deterministic_context_and_skips_third_call():
    agent = InvalidPayloadAgent()
    coordinator, _ = _guarded_coordinator(agent)
    state = make_state()

    for _ in range(3):
        _build_once(coordinator, state)

    assert agent.calls == 2


def test_failure_state_survives_worker_replacement_but_not_owner_boundary():
    first_agent = ProviderUnavailableAgent()
    first, failure_store = _guarded_coordinator(first_agent)
    state = make_state()
    for _ in range(3):
        _build_once(first, state)
    assert first_agent.calls == 3

    replacement_agent = ProviderUnavailableAgent()
    replacement, _ = _guarded_coordinator(
        replacement_agent,
        failure_store=failure_store,
    )
    _build_once(replacement, state)
    assert replacement_agent.calls == 0

    other_owner = make_state()
    other_owner["session_id"] = "session-2"
    _build_once(replacement, other_owner)
    assert replacement_agent.calls == 1
