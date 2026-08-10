from types import SimpleNamespace

from app.services.context_budget import DynamicCompressionTargetPolicy
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)
from tests.test_question_memory import (
    CompressorAgent,
    ParentOwnership,
    make_state,
)
from tests.test_question_memory_target_persistence import (
    _CountingIndex,
    _capture_runner,
    _coordinator,
    _dynamic_selection,
    _runtime,
    _seed_completed_artifact,
)


def test_removed_current_tier_reuses_persisted_target_without_reattribution():
    state = make_state()
    selection = _dynamic_selection(state)
    artifact_store = InMemoryContextArtifactStore()
    index_store = _CountingIndex()
    seed = _coordinator(
        agent=CompressorAgent(),
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=_runtime(source_tokens=2_000),
    )
    seeded, entry, _source = _seed_completed_artifact(
        coordinator=seed,
        index_store=index_store,
        state=state,
        selection=selection,
        artifact_target=512,
        indexed_target=512,
    )

    base_runtime = _runtime(
        source_tokens=3_000,
        forbid_dynamic_frames=True,
    )
    restart_runtime = SimpleNamespace(
        estimator_resolution=base_runtime.estimator_resolution,
        model_profile=base_runtime.model_profile,
        dynamic_compression_target_policy=DynamicCompressionTargetPolicy(
            floor_tokens=256,
            source_ratio_basis_points=2_500,
            allowed_target_tokens=(256, 1_024, 1_536, 2_000),
        ),
    )
    restart_agent = CompressorAgent()
    restarted = _coordinator(
        agent=restart_agent,
        index_store=index_store,
        artifact_store=artifact_store,
        runtime=restart_runtime,
    )
    calls, resolutions = _capture_runner(restarted)

    result = restarted.build_context(
        state=state,
        deterministic_context=list(selection.provider_messages),
        selection=selection,
        parent_ownership=ParentOwnership(),
    )

    assert result.route == "memory_index_retrieved"
    assert restart_agent.calls == 0
    assert restart_runtime.estimator_resolution.estimator.dynamic_frames == []
    assert len(calls) == len(resolutions) == 1
    request = calls[0]["request"]
    assert request.resolved_target_output_tokens == 512
    assert request.resolved_target_authority == "persisted_index"
    assert request.target_policy is None
    assert resolutions[0].ref == seeded.ref
    assert resolutions[0].record.identity.artifact_key == (
        seeded.record.identity.artifact_key
    )
    assert index_store.activate_calls == 1
    assert index_store.get_historical(entry.artifact_ref) == entry
