from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from inspect import signature

import pytest

from app.services.context_artifacts import (
    CompressionSourceSegment,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextArtifactLeaseLost,
    ContextArtifactConflict,
    ContextArtifactProviderFailed,
    ContextArtifactValidationFailed,
    ContextCompressionPolicy,
)
from app.services.context_compression_intent import (
    CompressionIntent,
    compression_intent_sha256,
)
from app.services.context_budget import DynamicCompressionTargetPolicy
from app.services.context_compression_request import (
    ResolvedCompressionRequest,
    bind_resolved_target_to_identity,
)
from app.services.context_compression_runner import (
    ContextArtifactHeartbeat,
    ContextCompressionRunner,
)
from app.services.in_memory_context_artifact_store import (
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
        target_output_tokens=2_000,
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


def make_request(policy, sources, *, intent=None, target=512):
    return ResolvedCompressionRequest(
        policy=policy,
        intent=intent,
        source_segments=tuple(sources),
        resolved_target_output_tokens=target,
        target_policy=DynamicCompressionTargetPolicy(
            floor_tokens=256,
            source_ratio_basis_points=2_500,
            allowed_target_tokens=(256, 512, 1_024, 1_536, 2_000),
        ),
    )


def resolve(runner, *, compressor, parent=None, request=None):
    material, policy, sources, _, question_digest = make_contract()
    request = request or make_request(policy, sources)
    return runner.resolve(
        identity_material=material,
        request=request,
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

    def compressor(_request):
        nonlocal calls
        calls += 1
        return payload

    created = resolve(runner, compressor=compressor)
    reused = resolve(runner, compressor=compressor)

    assert created.route == "artifact_created"
    assert reused.route == "artifact_reused"
    assert created.record == reused.record
    assert calls == 1


def test_runner_public_api_has_one_request_and_no_parallel_contract_fields():
    parameters = signature(ContextCompressionRunner.resolve).parameters

    assert "request" in parameters
    assert "policy" not in parameters
    assert "source_segments" not in parameters
    assert "intent" not in parameters


def test_runner_binds_identity_and_passes_the_same_request_to_compressor():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    material, policy, sources, payload, _question_digest = make_contract()
    request = make_request(policy, sources, target=512)
    seen = []

    result = resolve(
        runner,
        request=request,
        compressor=lambda actual: seen.append(actual) or payload,
    )

    assert seen == [request]
    assert seen[0] is request
    assert result.record.identity.material.target_output_tokens == 512
    assert result.record.identity == ContextArtifactIdentity.from_material(
        bind_resolved_target_to_identity(material, request)
    )


def test_runner_passes_the_same_request_to_create_and_reuse_validation(
    monkeypatch,
):
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    _material, policy, sources, payload, _question_digest = make_contract()
    request = make_request(policy, sources)
    seen = []
    original_validate = ContextCompressionRunner._validate

    def capture_request(**kwargs):
        seen.append(kwargs.get("request"))
        return original_validate(**kwargs)

    monkeypatch.setattr(
        ContextCompressionRunner,
        "_validate",
        staticmethod(capture_request),
    )

    resolve(runner, request=request, compressor=lambda _request: payload)
    resolve(runner, request=request, compressor=lambda _request: payload)

    assert seen == [request, request]
    assert all(actual is request for actual in seen)


@pytest.mark.parametrize(
    ("identity_changes", "message"),
    (
        ({"artifact_type": "evidence_compression"}, "artifact_type"),
        ({"compression_policy_version": "conversation-v2"}, "policy"),
        ({"prompt_contract_version": "prompt-v2"}, "prompt"),
        ({"output_schema_version": "question-conversation-v2"}, "schema"),
    ),
)
def test_runner_rejects_all_identity_policy_drift_before_claim_or_provider(
    identity_changes,
    message,
):
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    material, policy, sources, payload, question_digest = make_contract(
        **identity_changes
    )
    request = make_request(policy, sources)
    provider_calls = []

    with pytest.raises(ContextArtifactConflict, match=message):
        runner.resolve(
            identity_material=material,
            request=request,
            estimator=Estimator(),
            model="gpt-4o",
            compressor=(
                lambda _request: provider_calls.append("provider") or payload
            ),
            worker_id="worker-1",
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_question_id_sha256=question_digest,
        )

    assert provider_calls == []


def test_runner_rejects_intent_digest_mismatch_before_claim_or_provider_call():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    material, policy, sources, payload, question_digest = make_contract(
        identity_schema_version="identity-v1",
        compression_intent_sha256="0" * 64,
    )
    intent = CompressionIntent(
        schema_version="compression-intent-v1",
        consumer_operation="followup",
        phase="interview",
        source_focus=None,
        current_focus="idempotency",
        preserve=["candidate_claims"],
        authority="non_authoritative",
        prohibited_authority_upgrades=["new_fact"],
    )
    request = make_request(policy, sources, intent=intent)
    provider_calls = []

    with pytest.raises(ContextArtifactConflict, match="intent digest"):
        runner.resolve(
            identity_material=material,
            request=request,
            estimator=Estimator(),
            model="gpt-4o",
            compressor=lambda _request: provider_calls.append("provider") or payload,
            worker_id="worker-1",
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_question_id_sha256=question_digest,
        )

    assert provider_calls == []
    assert store.get_terminal_by_key("0" * 64) is None


def test_runner_revalidates_completed_artifact_with_its_intent():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    intent = CompressionIntent(
        schema_version="compression-intent-v1",
        consumer_operation="followup",
        phase="interview",
        source_focus=None,
        current_focus="idempotency",
        preserve=["candidate_claims"],
        authority="non_authoritative",
        prohibited_authority_upgrades=["new_fact"],
    )
    material, policy, sources, payload, question_digest = make_contract(
        identity_schema_version="identity-v1",
        compression_intent_sha256=compression_intent_sha256(intent),
    )
    request = make_request(policy, sources, intent=intent)
    payload["units"][0]["summary"] = (
        "idempotency guarantees perfect delivery under every failure mode."
    )

    claim = store.claim(
        ContextArtifactIdentity.from_material(
            bind_resolved_target_to_identity(material, request)
        ),
        worker_id="seed-worker",
        lease_seconds=30,
    )
    store.complete(claim, payload)
    provider_calls = []

    with pytest.raises(ContextArtifactValidationFailed, match="exact source excerpt"):
        runner.resolve(
            identity_material=material,
            request=request,
            estimator=Estimator(),
            model="gpt-4o",
            compressor=lambda _request: provider_calls.append("provider") or payload,
            worker_id="worker-1",
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_question_id_sha256=question_digest,
        )

    assert provider_calls == []


def test_runner_rejects_fabricated_intent_summary_on_create():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    intent = CompressionIntent(
        schema_version="compression-intent-v1",
        consumer_operation="followup",
        phase="interview",
        source_focus=None,
        current_focus="idempotency",
        preserve=["candidate_claims"],
        authority="non_authoritative",
        prohibited_authority_upgrades=["new_fact"],
    )
    material, policy, sources, payload, question_digest = make_contract(
        identity_schema_version="identity-v1",
        compression_intent_sha256=compression_intent_sha256(intent),
    )
    request = make_request(policy, sources, intent=intent)
    payload["units"][0]["summary"] = (
        "idempotency guarantees perfect delivery under every failure mode."
    )
    identity = ContextArtifactIdentity.from_material(
        bind_resolved_target_to_identity(material, request)
    )

    with pytest.raises(ContextArtifactValidationFailed, match="exact source excerpt"):
        runner.resolve(
            identity_material=material,
            request=request,
            estimator=Estimator(),
            model="gpt-4o",
            compressor=lambda actual: payload if actual is request else None,
            worker_id="worker-1",
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_question_id_sha256=question_digest,
        )

    failed = store.get_terminal_by_key(identity.artifact_key)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.last_error_code == "validation_failed"


def test_runner_revalidates_competing_completion_after_lease_loss(monkeypatch):
    now = [datetime(2026, 8, 8, tzinfo=timezone.utc)]
    store = InMemoryContextArtifactStore(clock=lambda: now[0])
    intent = CompressionIntent(
        schema_version="compression-intent-v1",
        consumer_operation="followup",
        phase="interview",
        source_focus=None,
        current_focus="idempotency",
        preserve=["candidate_claims"],
        authority="non_authoritative",
        prohibited_authority_upgrades=["new_fact"],
    )
    material, policy, sources, payload, question_digest = make_contract(
        identity_schema_version="identity-v1",
        compression_intent_sha256=compression_intent_sha256(intent),
    )
    request = make_request(policy, sources, intent=intent)
    identity = ContextArtifactIdentity.from_material(
        bind_resolved_target_to_identity(material, request)
    )
    competing_payload = {
        **payload,
        "units": [
            {
                **payload["units"][0],
                "summary": (
                    "idempotency guarantees perfect delivery under every failure mode."
                ),
            }
        ],
    }

    class CompletingHeartbeat:
        def __init__(self, _store, _claim, *, lease_seconds):
            assert _store is store
            assert lease_seconds == 30

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def ensure_owned(self):
            now[0] += timedelta(seconds=31)
            competing_claim = store.claim(
                identity,
                worker_id="competing-worker",
                lease_seconds=30,
            )
            store.complete(competing_claim, competing_payload)
            raise ContextArtifactLeaseLost("simulated lease loss")

    runner = ContextCompressionRunner(
        store,
        lease_seconds=30,
        heartbeat_factory=CompletingHeartbeat,
    )
    seen = []
    original_validate = ContextCompressionRunner._validate

    def capture_request(**kwargs):
        seen.append(kwargs.get("request"))
        return original_validate(**kwargs)

    monkeypatch.setattr(
        ContextCompressionRunner,
        "_validate",
        staticmethod(capture_request),
    )

    with pytest.raises(ContextArtifactValidationFailed, match="exact source excerpt"):
        runner.resolve(
            identity_material=material,
            request=request,
            estimator=Estimator(),
            model="gpt-4o",
            compressor=lambda actual: payload if actual is request else None,
            worker_id="worker-1",
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_question_id_sha256=question_digest,
        )

    assert seen == [request, request]
    assert all(actual is request for actual in seen)


def test_runner_revalidates_competing_completion_after_fail_race(monkeypatch):
    now = [datetime(2026, 8, 8, tzinfo=timezone.utc)]
    intent = CompressionIntent(
        schema_version="compression-intent-v1",
        consumer_operation="followup",
        phase="interview",
        source_focus=None,
        current_focus="idempotency",
        preserve=["candidate_claims"],
        authority="non_authoritative",
        prohibited_authority_upgrades=["new_fact"],
    )
    material, policy, sources, payload, question_digest = make_contract(
        identity_schema_version="identity-v1",
        compression_intent_sha256=compression_intent_sha256(intent),
    )
    request = make_request(policy, sources, intent=intent)
    identity = ContextArtifactIdentity.from_material(
        bind_resolved_target_to_identity(material, request)
    )
    competing_payload = {
        **payload,
        "units": [
            {
                **payload["units"][0],
                "summary": (
                    "idempotency guarantees perfect delivery under every failure mode."
                ),
            }
        ],
    }

    class FailRaceStore(InMemoryContextArtifactStore):
        fail_called = False

        def fail(self, claim, *, error_code):
            assert error_code == "validation_failed"
            self.fail_called = True
            now[0] += timedelta(seconds=31)
            competing_claim = self.claim(
                identity,
                worker_id="competing-worker",
                lease_seconds=30,
            )
            self.complete(competing_claim, competing_payload)
            raise ContextArtifactLeaseLost("simulated fail race")

    store = FailRaceStore(clock=lambda: now[0])
    runner = ContextCompressionRunner(store, lease_seconds=30)
    seen = []
    original_validate = ContextCompressionRunner._validate

    def capture_request(**kwargs):
        seen.append(kwargs.get("request"))
        return original_validate(**kwargs)

    monkeypatch.setattr(
        ContextCompressionRunner,
        "_validate",
        staticmethod(capture_request),
    )
    initially_invalid = {
        **payload,
        "units": [
            {
                **payload["units"][0],
                "source_segment_sha256": ["f" * 64],
            }
        ],
    }

    with pytest.raises(ContextArtifactValidationFailed, match="exact source excerpt"):
        runner.resolve(
            identity_material=material,
            request=request,
            estimator=Estimator(),
            model="gpt-4o",
            compressor=(
                lambda actual: initially_invalid if actual is request else None
            ),
            worker_id="worker-1",
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_question_id_sha256=question_digest,
        )

    assert store.fail_called is True
    assert seen == [request, request]
    assert all(actual is request for actual in seen)


def test_runner_checks_parent_before_provider_completion_and_return():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    _, _, _, payload, _ = make_contract()
    parent = ParentOwnership()

    result = resolve(runner, compressor=lambda _request: payload, parent=parent)

    assert result.route == "artifact_created"
    assert parent.calls == 3


def test_provider_failure_is_stable_and_failed_row_can_be_reclaimed():
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(store, lease_seconds=30)
    with pytest.raises(ContextArtifactProviderFailed) as raised:
        resolve(
            runner,
            compressor=(
                lambda _request: (_ for _ in ()).throw(RuntimeError("secret"))
            ),
        )
    assert "secret" not in str(raised.value)

    _, _, _, payload, _ = make_contract()
    result = resolve(runner, compressor=lambda _request: payload)
    assert result.route == "artifact_created"


def test_heartbeat_false_fails_closed():
    class LostStore:
        def heartbeat(self, claim, *, lease_seconds):
            return False

    material, _, _, _, _ = make_contract()
    from app.services.context_artifacts import ContextArtifactIdentity

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
    from app.services.context_artifacts import ContextArtifactIdentity

    identity = ContextArtifactIdentity.from_material(material)
    claim = InMemoryContextArtifactStore().claim(
        identity, worker_id="worker", lease_seconds=30
    )
    heartbeat = ContextArtifactHeartbeat(FailingStore(), claim, lease_seconds=30)

    with pytest.raises(ContextArtifactLeaseLost) as raised:
        heartbeat.__enter__()
    assert raised.value.__cause__ is failure
