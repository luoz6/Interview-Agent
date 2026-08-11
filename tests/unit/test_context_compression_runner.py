from dataclasses import replace
from hashlib import sha256

import pytest

from app.domain.context.artifacts import (
    CompressionSourceSegment,
    ContextArtifactIdentityMaterial,
    ContextArtifactLeaseLost,
    ContextArtifactProviderFailed,
    ContextCompressionPolicy,
)
from app.services.context_compression_runner import (
    ContextArtifactHeartbeat,
    ContextCompressionRunner,
)
from app.adapters.memory.context_artifacts import (
    InMemoryContextArtifactStore,
)


class Estimator:
    def estimate_text(self, text, *, model):
        return max(1, len(text) // 20)

    def estimate_message(self, message, *, model):
        return self.estimate_text(message["content"], model=model)


class ParentOwnership:
    def __init__(self):
        self.calls = 0
        self.failure = None

    def ensure_owned(self):
        self.calls += 1
        if self.failure is not None:
            raise self.failure


def make_contract(**identity_changes):
    content = "Candidate used idempotency for retry safety."
    digest = sha256(content.encode("utf-8")).hexdigest()
    source = CompressionSourceSegment(
        segment_index=0,
        segment_type="conversation_message",
        content=content,
        content_sha256=digest,
    )
    policy = ContextCompressionPolicy(
        artifact_type="question_conversation",
        policy_version="conversation-v1",
        prompt_contract_version="prompt-v1",
        output_schema_version="question-conversation-v1",
        compressor_operation="context_compressor.question_conversation",
        compressor_input_cap_tokens=2048,
        target_output_tokens=256,
        max_output_units=4,
        max_supporting_excerpt_tokens=64,
    )
    material = ContextArtifactIdentityMaterial(
        artifact_type="question_conversation",
        privacy_scope_sha256="1" * 64,
        source_sha256="2" * 64,
        source_manifest_sha256="3" * 64,
        semantic_focus_sha256="4" * 64,
        compression_policy_version=policy.policy_version,
        prompt_contract_version=policy.prompt_contract_version,
        output_schema_version=policy.output_schema_version,
        compressor_provider="openai-compatible",
        compressor_model="gpt-4o",
        compressor_settings_sha256="5" * 64,
        target_output_tokens=policy.target_output_tokens,
    )
    question_digest = "6" * 64
    payload = {
        "schema_version": "question-conversation-v1",
        "question_id_sha256": question_digest,
        "units": [
            {
                "summary": "Candidate used idempotency for retry safety.",
                "source_segment_sha256": [digest],
                "supporting_excerpts": ["idempotency for retry safety"],
            }
        ],
        "unresolved_topics": [],
        "source_message_count": 1,
    }
    return replace(material, **identity_changes), policy, [source], payload, question_digest


def resolve(runner, *, compressor, parent=None):
    material, policy, sources, _, question_digest = make_contract()
    return runner.resolve(
        identity_material=material,
        policy=policy,
        source_segments=sources,
        estimator=Estimator(),
        model="gpt-4o",
        compressor=compressor,
        worker_id="worker-1",
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
        parent_ownership=parent,
        expected_question_id_sha256=question_digest,
    )


def test_runner_creates_then_reuses_without_second_provider_call():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    _, _, _, payload, _ = make_contract()
    calls = 0

    def compressor():
        nonlocal calls
        calls += 1
        return payload

    created = resolve(runner, compressor=compressor)
    reused = resolve(runner, compressor=compressor)

    assert created.route == "artifact_created"
    assert reused.route == "artifact_reused"
    assert created.record == reused.record
    assert calls == 1


def test_runner_checks_parent_before_provider_completion_and_return():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    _, _, _, payload, _ = make_contract()
    parent = ParentOwnership()

    result = resolve(runner, compressor=lambda: payload, parent=parent)

    assert result.route == "artifact_created"
    assert parent.calls == 3


def test_provider_failure_is_stable_and_failed_row_can_be_reclaimed():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    with pytest.raises(ContextArtifactProviderFailed) as raised:
        resolve(
            runner,
            compressor=lambda: (_ for _ in ()).throw(RuntimeError("secret")),
        )
    assert "secret" not in str(raised.value)

    _, _, _, payload, _ = make_contract()
    result = resolve(runner, compressor=lambda: payload)
    assert result.route == "artifact_created"


def test_heartbeat_false_fails_closed():
    class LostStore:
        def heartbeat(self, claim, *, lease_seconds):
            return False

    material, _, _, _, _ = make_contract()
    from app.domain.context.artifacts import ContextArtifactIdentity

    identity = ContextArtifactIdentity.from_material(material)
    claim = InMemoryContextArtifactStore().claim(
        identity, worker_id="worker", lease_seconds=30
    )
    heartbeat = ContextArtifactHeartbeat(LostStore(), claim, lease_seconds=30)

    with pytest.raises(ContextArtifactLeaseLost):
        heartbeat.__enter__()


def test_heartbeat_exception_is_preserved_as_cause():
    failure = RuntimeError("database unavailable")

    class FailingStore:
        def heartbeat(self, claim, *, lease_seconds):
            raise failure

    material, _, _, _, _ = make_contract()
    from app.domain.context.artifacts import ContextArtifactIdentity

    identity = ContextArtifactIdentity.from_material(material)
    claim = InMemoryContextArtifactStore().claim(
        identity, worker_id="worker", lease_seconds=30
    )
    heartbeat = ContextArtifactHeartbeat(FailingStore(), claim, lease_seconds=30)

    with pytest.raises(ContextArtifactLeaseLost) as raised:
        heartbeat.__enter__()
    assert raised.value.__cause__ is failure
