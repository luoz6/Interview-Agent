from asyncio import CancelledError
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
from hashlib import sha256
from inspect import signature
from importlib import import_module
from threading import Event, Thread

import pytest

from app.services.context_artifacts import (
    CompressionSourceSegment,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextArtifactBusy,
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


def test_failure_authorization_refresh_is_serialized_and_detached_atomically():
    class Authorization:
        def __init__(self, version):
            self.version = version

    class ArtifactStore:
        def heartbeat(self, _claim, *, lease_seconds):
            assert lease_seconds == 30
            return True

    class Containment:
        def __init__(self):
            self.calls = []
            self.first_entered = Event()
            self.release_first = Event()

        def heartbeat_attempt(self, authorization):
            self.calls.append(authorization.version)
            if len(self.calls) == 1:
                self.first_entered.set()
                assert self.release_first.wait(timeout=2)
            return Authorization(authorization.version + 1)

    containment = Containment()
    heartbeat = ContextArtifactHeartbeat(
        ArtifactStore(),
        object(),
        lease_seconds=30,
        failure_containment=containment,
        failure_authorization=Authorization(0),
    )
    second_started = Event()
    failures = []

    def refresh(*, started=None):
        if started is not None:
            started.set()
        try:
            heartbeat.ensure_owned()
        except BaseException as exc:
            failures.append(exc)

    first = Thread(target=refresh)
    second = Thread(target=refresh, kwargs={"started": second_started})
    first.start()
    assert containment.first_entered.wait(timeout=2)
    second.start()
    assert second_started.wait(timeout=2)
    containment.release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert failures == []
    assert containment.calls == [0, 1]
    detached = heartbeat.detach_failure_authorization()
    assert detached.version == 2
    heartbeat.ensure_owned()
    assert containment.calls == [0, 1]


def test_runner_finishes_with_latest_immutable_failure_authorization():
    class Authorization:
        allow_provider_call = True
        reason = "half_open_probe"

        def __init__(self, version):
            self.version = version

    class Containment:
        def __init__(self):
            self.latest = None
            self.heartbeat_inputs = []
            self.finish_calls = []

        def authorize_attempt(self, **_kwargs):
            self.latest = Authorization(0)
            return self.latest

        def heartbeat_attempt(self, authorization):
            assert authorization is self.latest
            self.heartbeat_inputs.append(authorization.version)
            self.latest = Authorization(authorization.version + 1)
            return self.latest

        def finish_attempt(
            self,
            authorization,
            *,
            outcome,
            failure_code,
        ):
            assert authorization is self.latest
            self.finish_calls.append(
                (authorization.version, outcome, failure_code)
            )

        def abort_attempt(self, *_args, **_kwargs):
            pytest.fail("successful completion must not abort containment")

    containment = Containment()
    runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=containment,
    )
    _, _, _, payload, _ = make_contract()

    created = resolve(runner, compressor=lambda _request: payload)

    assert created.route == "artifact_created"
    assert containment.heartbeat_inputs == [0, 1]
    assert containment.finish_calls == [(2, "success", None)]


def make_failure_containment(
    *,
    provider_circuit_threshold=3,
    validation_quarantine_threshold=2,
):
    domain = import_module(
        "app.services.context_compression_failure_containment"
    )
    memory = import_module(
        "app.services.in_memory_context_compression_failure_store"
    )
    failure_store = memory.InMemoryContextCompressionFailureStore()
    containment = domain.ContextCompressionFailureContainment(
        store=failure_store,
        config=domain.FailureContainmentConfig(
            provider_circuit_threshold=provider_circuit_threshold,
            provider_circuit_cooldown_seconds=300,
            validation_quarantine_threshold=validation_quarantine_threshold,
            validation_quarantine_cooldown_seconds=3600,
            failure_state_lease_seconds=60,
        ),
    )
    return domain, failure_store, containment


def _stable_containment_reason(exc):
    return getattr(exc, "failure_code", None)


def test_runner_opens_provider_circuit_after_three_failures_and_skips_fourth_call():
    _, _, containment = make_failure_containment()
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(
        store,
        lease_seconds=30,
        failure_containment=containment,
    )
    calls = []

    def unavailable(_request):
        calls.append("provider")
        raise TimeoutError("PRIVATE PROVIDER ERROR CANARY")

    for _ in range(3):
        with pytest.raises(ContextArtifactProviderFailed):
            resolve(runner, compressor=unavailable)

    with pytest.raises(ContextArtifactProviderFailed) as blocked:
        resolve(runner, compressor=unavailable)

    assert calls == ["provider", "provider", "provider"]
    assert _stable_containment_reason(blocked.value) == "provider_circuit_open"
    assert "PRIVATE PROVIDER ERROR CANARY" not in str(blocked.value)


def test_runner_quarantines_only_matching_validation_identity_and_skips_third_call():
    _, _, containment = make_failure_containment()
    store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(
        store,
        lease_seconds=30,
        failure_containment=containment,
    )
    _, _, _, payload, _ = make_contract()
    invalid_payload = {
        **payload,
        "units": [
            {
                **payload["units"][0],
                "supporting_excerpts": ["candidate answer canary not in source"],
            }
        ],
    }
    calls = []

    def invalid(_request):
        calls.append("provider")
        return invalid_payload

    for _ in range(2):
        with pytest.raises(ContextArtifactValidationFailed):
            resolve(runner, compressor=invalid)

    with pytest.raises(ContextArtifactValidationFailed) as blocked:
        resolve(runner, compressor=invalid)
    assert calls == ["provider", "provider"]
    assert _stable_containment_reason(blocked.value) == (
        "validation_quarantine_open"
    )

    material, policy, sources, valid_payload, question_digest = make_contract(
        source_manifest_sha256="7" * 64
    )
    request = make_request(policy, sources)
    result = runner.resolve(
        identity_material=material,
        request=request,
        estimator=Estimator(),
        model="gpt-4o",
        compressor=lambda _request: calls.append("unrelated") or valid_payload,
        worker_id="worker-2",
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
        expected_question_id_sha256=question_digest,
    )
    assert result.route == "artifact_created"
    assert calls[-1] == "unrelated"


def test_runner_does_not_count_parent_ownership_or_identity_failures():
    domain, failure_store, containment = make_failure_containment(
        provider_circuit_threshold=1,
        validation_quarantine_threshold=1,
    )
    runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=containment,
    )
    parent = ParentOwnership()
    parent.failure = ContextArtifactLeaseLost("parent lease lost")
    _, _, _, payload, _ = make_contract()

    with pytest.raises(ContextArtifactLeaseLost):
        resolve(runner, compressor=lambda _request: payload, parent=parent)

    provider_scope = domain.build_provider_circuit_scope(
        privacy_scope_sha256="1" * 64,
        owner_type="interview_session",
        owner_key="session-1",
        provider="openai-compatible",
        model="gpt-4o",
        artifact_type="question_conversation",
        policy_version="conversation-v1",
    )
    assert failure_store.get(provider_scope.state_key_sha256) is None

    material, policy, sources, _, question_digest = make_contract(
        compression_policy_version="conversation-v2"
    )
    with pytest.raises(ContextArtifactConflict):
        runner.resolve(
            identity_material=material,
            request=make_request(policy, sources),
            estimator=Estimator(),
            model="gpt-4o",
            compressor=lambda _request: pytest.fail("provider must not run"),
            worker_id="worker-1",
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_question_id_sha256=question_digest,
        )
    assert failure_store.get(provider_scope.state_key_sha256) is None


def test_completed_artifact_reuse_bypasses_open_failure_state():
    domain, _, containment = make_failure_containment(
        provider_circuit_threshold=1
    )
    store = InMemoryContextArtifactStore()
    plain_runner = ContextCompressionRunner(store, lease_seconds=30)
    _, _, _, payload, _ = make_contract()
    created = resolve(plain_runner, compressor=lambda _request: payload)

    provider_scope = domain.build_provider_circuit_scope(
        privacy_scope_sha256="1" * 64,
        owner_type="interview_session",
        owner_key="session-1",
        provider="openai-compatible",
        model="gpt-4o",
        artifact_type="question_conversation",
        policy_version="conversation-v1",
    )
    decision = containment.before_attempt(provider_scope, worker_id="opener")
    containment.record_failure(
        provider_scope,
        failure_code="provider_timeout",
        decision=decision,
    )
    guarded_runner = ContextCompressionRunner(
        store,
        lease_seconds=30,
        failure_containment=containment,
    )

    reused = resolve(
        guarded_runner,
        compressor=lambda _request: pytest.fail("provider must not run"),
    )
    assert reused.route == "artifact_reused"
    assert reused.record == created.record


def test_runner_never_passes_raw_payload_or_exception_text_to_failure_store():
    class SpyContainment:
        def __init__(self):
            self.values = []
            self.finish_calls = []

        def authorize_attempt(
            self,
            *,
            provider_scope,
            validation_scope,
            worker_id,
        ):
            self.values.append(
                (provider_scope, validation_scope, worker_id)
            )
            authorization = type(
                "Decision",
                (),
                {"allow_provider_call": True, "reason": "closed"},
            )()
            self.authorization = authorization
            return authorization

        def finish_attempt(
            self,
            authorization,
            *,
            outcome,
            failure_code=None,
        ):
            self.finish_calls.append(
                (authorization, outcome, failure_code)
            )

    spy = SpyContainment()
    runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=spy,
    )
    with pytest.raises(ContextArtifactProviderFailed):
        resolve(
            runner,
            compressor=lambda _request: (_ for _ in ()).throw(
                TimeoutError("PRIVATE PROVIDER ERROR CANARY")
            ),
        )

    rendered = repr(spy.values)
    assert "PRIVATE PROVIDER ERROR CANARY" not in rendered
    assert "Candidate used idempotency" not in rendered
    assert "session-1" not in rendered
    assert spy.finish_calls == [
        (spy.authorization, "provider_failed", "provider_timeout")
    ]


def test_validation_failure_uses_one_combined_finish_boundary():
    class FinishOnlyContainment:
        def __init__(self):
            self.finish_calls = []

        def authorize_attempt(self, **_kwargs):
            self.authorization = type(
                "Authorization",
                (),
                {"allow_provider_call": True, "reason": "closed"},
            )()
            return self.authorization

        def finish_attempt(
            self,
            authorization,
            *,
            outcome,
            failure_code=None,
        ):
            self.finish_calls.append(
                (authorization, outcome, failure_code)
            )

    containment = FinishOnlyContainment()
    runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=containment,
    )
    _, _, _, payload, _ = make_contract()
    invalid_payload = {
        **payload,
        "units": [
            {
                **payload["units"][0],
                "supporting_excerpts": ["not present in source"],
            }
        ],
    }

    with pytest.raises(ContextArtifactValidationFailed):
        resolve(runner, compressor=lambda _request: invalid_payload)

    assert containment.finish_calls == [
        (
            containment.authorization,
            "validation_failed",
            "grounding_failed",
        )
    ]


def test_success_uses_one_combined_finish_boundary_without_per_scope_calls():
    class FinishOnlyContainment:
        def __init__(self):
            self.finish_calls = []

        def authorize_attempt(self, **_kwargs):
            self.authorization = type(
                "Authorization",
                (),
                {"allow_provider_call": True, "reason": "closed"},
            )()
            return self.authorization

        def finish_attempt(
            self,
            authorization,
            *,
            outcome,
            failure_code=None,
        ):
            self.finish_calls.append(
                (authorization, outcome, failure_code)
            )

    containment = FinishOnlyContainment()
    runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=containment,
    )
    _, _, _, payload, _ = make_contract()

    created = resolve(runner, compressor=lambda _request: payload)

    assert created.route == "artifact_created"
    assert containment.finish_calls == [
        (containment.authorization, "success", None)
    ]


@pytest.mark.parametrize("heartbeat_outcome", (True, False, "exception"))
def test_dual_probe_heartbeat_renews_both_or_prevents_stale_completion(
    heartbeat_outcome,
):
    class ProbeLease:
        def __init__(self):
            self.probe_lease_until = 1

    class ProbeContainment:
        def __init__(self):
            self.heartbeat_calls = []
            self.finish_calls = []
            self.abort_calls = []
            self.background_heartbeat = Event()

        def authorize_attempt(self, **_kwargs):
            self.authorization = type(
                "Authorization",
                (),
                {
                    "allow_provider_call": True,
                    "reason": "half_open_probe",
                    "probe_count": 2,
                    "provider_probe": ProbeLease(),
                    "validation_probe": ProbeLease(),
                },
            )()
            return self.authorization

        def heartbeat_attempt(self, authorization):
            self.heartbeat_calls.append(authorization)
            if len(self.heartbeat_calls) == 1:
                authorization.provider_probe.probe_lease_until += 1
                authorization.validation_probe.probe_lease_until += 1
                return True
            self.background_heartbeat.set()
            if heartbeat_outcome == "exception":
                raise RuntimeError("PRIVATE PROBE HEARTBEAT CANARY")
            if heartbeat_outcome is True:
                authorization.provider_probe.probe_lease_until += 1
                authorization.validation_probe.probe_lease_until += 1
            return heartbeat_outcome

        def finish_attempt(
            self,
            authorization,
            *,
            outcome,
            failure_code=None,
        ):
            self.finish_calls.append(
                (authorization, outcome, failure_code)
            )

        def abort_attempt(self, authorization, *, reason):
            self.abort_calls.append((authorization, reason))

    class CompleteSpyStore(InMemoryContextArtifactStore):
        def __init__(self):
            super().__init__()
            self.complete_calls = 0

        def complete(self, *args, **kwargs):
            self.complete_calls += 1
            return super().complete(*args, **kwargs)

    containment = ProbeContainment()
    store = CompleteSpyStore()
    runner = ContextCompressionRunner(
        store,
        lease_seconds=30,
        heartbeat_factory=partial(
            ContextArtifactHeartbeat,
            interval_seconds=0.01,
        ),
        failure_containment=containment,
    )
    _, _, _, payload, _ = make_contract()

    def blocking_provider(_request):
        assert containment.background_heartbeat.wait(timeout=2)
        return payload

    if heartbeat_outcome is True:
        created = resolve(runner, compressor=blocking_provider)
        raised = None
    else:
        with pytest.raises(ContextArtifactLeaseLost) as raised_info:
            resolve(runner, compressor=blocking_provider)
        raised = raised_info.value

    assert len(containment.heartbeat_calls) >= 2
    assert all(
        item is containment.authorization
        for item in containment.heartbeat_calls
    )
    assert containment.authorization.probe_count == 2
    if heartbeat_outcome is True:
        assert (
            containment.authorization.provider_probe.probe_lease_until
            >= 3
        )
        assert (
            containment.authorization.validation_probe.probe_lease_until
            >= 3
        )
        assert created.route == "artifact_created"
        assert containment.finish_calls == [
            (containment.authorization, "success", None)
        ]
        assert store.complete_calls == 1
    else:
        assert containment.finish_calls == []
        assert store.complete_calls == 0
    if heartbeat_outcome == "exception":
        assert isinstance(raised.__cause__, RuntimeError)
        assert "PRIVATE PROBE HEARTBEAT CANARY" not in str(raised)


def test_cancellation_aborts_dual_probe_authorization_without_finish():
    class CancellationContainment:
        def __init__(self):
            self.abort_calls = []
            self.finish_calls = []

        def authorize_attempt(self, **_kwargs):
            self.authorization = type(
                "Authorization",
                (),
                {
                    "allow_provider_call": True,
                    "reason": "half_open_probe",
                    "probe_count": 2,
                },
            )()
            return self.authorization

        def abort_attempt(self, authorization, *, reason):
            self.abort_calls.append((authorization, reason))

        def finish_attempt(self, *args, **kwargs):
            self.finish_calls.append((args, kwargs))

    containment = CancellationContainment()
    runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=containment,
    )

    with pytest.raises(CancelledError):
        resolve(
            runner,
            compressor=lambda _request: (_ for _ in ()).throw(
                CancelledError()
            ),
        )

    assert containment.abort_calls == [
        (containment.authorization, "cancelled")
    ]
    assert containment.finish_calls == []


def test_identity_conflict_fails_before_failure_store_or_provider():
    class NeverCalledContainment:
        def before_attempt(self, *_args, **_kwargs):
            raise AssertionError("identity conflict must precede containment")

        def authorize_attempt(self, *_args, **_kwargs):
            raise AssertionError("identity conflict must precede containment")

    runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=NeverCalledContainment(),
    )
    material, policy, sources, payload, question_digest = make_contract(
        compression_policy_version="conversation-v2"
    )

    with pytest.raises(ContextArtifactConflict):
        runner.resolve(
            identity_material=material,
            request=make_request(policy, sources),
            estimator=Estimator(),
            model="gpt-4o",
            compressor=lambda _request: pytest.fail("provider must not run"),
            worker_id="worker-1",
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_question_id_sha256=question_digest,
        )


def test_failure_store_error_fails_safely_without_provider_or_raw_error():
    class UnavailableContainment:
        def authorize_attempt(self, **_kwargs):
            raise RuntimeError("PRIVATE FAILURE STORE ERROR CANARY")

    runner = ContextCompressionRunner(
        InMemoryContextArtifactStore(),
        lease_seconds=30,
        failure_containment=UnavailableContainment(),
    )
    provider_calls = []

    with pytest.raises(ContextArtifactProviderFailed) as raised:
        resolve(
            runner,
            compressor=lambda _request: provider_calls.append("provider"),
        )

    assert provider_calls == []
    assert _stable_containment_reason(raised.value) == (
        "failure_state_unavailable"
    )
    assert "PRIVATE FAILURE STORE ERROR CANARY" not in str(raised.value)


def test_finish_store_error_preserves_completed_artifact_but_fails_closed():
    class FinishUnavailableContainment:
        def __init__(self):
            self.authorize_calls = 0
            self.finish_calls = 0

        def authorize_attempt(self, **_kwargs):
            self.authorize_calls += 1
            return type(
                "Authorization",
                (),
                {"allow_provider_call": True, "reason": "closed"},
            )()

        def finish_attempt(self, _authorization, *, outcome, failure_code):
            assert outcome == "success"
            assert failure_code is None
            self.finish_calls += 1
            raise RuntimeError("PRIVATE FINISH STORE CANARY")

        def abort_attempt(self, _authorization, *, reason):
            assert reason == "parent_or_runtime_failed"

    containment = FinishUnavailableContainment()
    artifact_store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(
        artifact_store,
        lease_seconds=30,
        failure_containment=containment,
    )
    material, policy, sources, payload, _ = make_contract()
    provider_calls = []

    with pytest.raises(ContextArtifactProviderFailed) as raised:
        resolve(
            runner,
            compressor=lambda _request: provider_calls.append("provider") or payload,
        )

    assert _stable_containment_reason(raised.value) == "failure_state_unavailable"
    assert "PRIVATE FINISH STORE CANARY" not in str(raised.value)
    identity = ContextArtifactIdentity.from_material(
        bind_resolved_target_to_identity(
            material,
            make_request(policy, sources),
        )
    )
    completed = artifact_store.get_terminal_by_key(identity.artifact_key)
    assert completed is not None
    assert completed.status == "completed"
    assert provider_calls == ["provider"]
    assert containment.authorize_calls == 1
    assert containment.finish_calls == 1

    reused = resolve(
        runner,
        compressor=lambda _request: pytest.fail("provider must not rerun"),
    )
    assert reused.route == "artifact_reused"
    assert provider_calls == ["provider"]
    assert containment.authorize_calls == 1
    assert containment.finish_calls == 1


def test_provider_success_then_parent_failure_keeps_completed_artifact_and_no_streak():
    domain, failure_store, containment = make_failure_containment()
    artifact_store = InMemoryContextArtifactStore()
    runner = ContextCompressionRunner(
        artifact_store,
        lease_seconds=30,
        failure_containment=containment,
    )
    _, _, _, payload, _ = make_contract()

    class FailAfterCompletion:
        def __init__(self):
            self.calls = 0

        def ensure_owned(self):
            self.calls += 1
            if self.calls == 3:
                raise ContextArtifactLeaseLost("parent lease lost after completion")

    with pytest.raises(ContextArtifactLeaseLost):
        resolve(
            runner,
            compressor=lambda _request: payload,
            parent=FailAfterCompletion(),
        )

    material, policy, sources, _, _ = make_contract()
    identity = ContextArtifactIdentity.from_material(
        bind_resolved_target_to_identity(
            material,
            make_request(policy, sources),
        )
    )
    completed = artifact_store.get_terminal_by_key(identity.artifact_key)
    assert completed is not None
    assert completed.status == "completed"
    provider_scope = domain.build_provider_circuit_scope(
        privacy_scope_sha256="1" * 64,
        owner_type="interview_session",
        owner_key="session-1",
        provider="openai-compatible",
        model="gpt-4o",
        artifact_type="question_conversation",
        policy_version="conversation-v1",
    )
    failure_record = failure_store.get(provider_scope.state_key_sha256)
    assert failure_record is None or failure_record.consecutive_failures == 0


def test_blocked_authorization_skips_artifact_claim_and_provider():
    domain, _, containment = make_failure_containment(
        provider_circuit_threshold=1
    )
    provider_scope = domain.build_provider_circuit_scope(
        privacy_scope_sha256="1" * 64,
        owner_type="interview_session",
        owner_key="session-1",
        provider="openai-compatible",
        model="gpt-4o",
        artifact_type="question_conversation",
        policy_version="conversation-v1",
    )
    opener = containment.before_attempt(
        provider_scope,
        worker_id="opener",
    )
    containment.record_failure(
        provider_scope,
        failure_code="provider_timeout",
        decision=opener,
    )

    class ClaimSpyStore(InMemoryContextArtifactStore):
        def __init__(self):
            super().__init__()
            self.claim_calls = 0

        def claim(self, *args, **kwargs):
            self.claim_calls += 1
            return super().claim(*args, **kwargs)

    artifact_store = ClaimSpyStore()
    runner = ContextCompressionRunner(
        artifact_store,
        lease_seconds=30,
        failure_containment=containment,
    )
    provider_calls = []

    with pytest.raises(ContextArtifactProviderFailed) as blocked:
        resolve(
            runner,
            compressor=lambda _request: provider_calls.append("provider"),
        )

    assert blocked.value.failure_code == "provider_circuit_open"
    assert artifact_store.claim_calls == 0
    assert provider_calls == []


def test_parent_ownership_failure_precedes_authorization_and_artifact_claim():
    class NeverCalledContainment:
        def authorize_attempt(self, **_kwargs):
            raise AssertionError("parent ownership must precede authorization")

    class ClaimSpyStore(InMemoryContextArtifactStore):
        def __init__(self):
            super().__init__()
            self.claim_calls = 0

        def claim(self, *args, **kwargs):
            self.claim_calls += 1
            return super().claim(*args, **kwargs)

    artifact_store = ClaimSpyStore()
    runner = ContextCompressionRunner(
        artifact_store,
        lease_seconds=30,
        failure_containment=NeverCalledContainment(),
    )
    parent = ParentOwnership()
    parent.failure = ContextArtifactLeaseLost("parent lease lost")

    with pytest.raises(ContextArtifactLeaseLost):
        resolve(
            runner,
            compressor=lambda _request: pytest.fail("provider must not run"),
            parent=parent,
        )

    assert artifact_store.claim_calls == 0


@pytest.mark.parametrize("probe_count", (0, 1, 2))
@pytest.mark.parametrize(
    ("failure_kind", "expected_exception", "abort_reason"),
    (
        ("parent", ContextArtifactLeaseLost, "parent_lease_lost"),
        ("artifact_busy", ContextArtifactBusy, "artifact_busy"),
        ("artifact_lease_lost", ContextArtifactLeaseLost, "artifact_lease_lost"),
    ),
)
def test_permit_abort_clears_all_probes_without_incrementing_streak(
    probe_count,
    failure_kind,
    expected_exception,
    abort_reason,
):
    class AbortSpyContainment:
        def __init__(self):
            self.abort_calls = []
            self.finish_calls = []
            self.streak_updates = 0

        def authorize_attempt(self, **_kwargs):
            self.authorization = type(
                "Authorization",
                (),
                {
                    "allow_provider_call": True,
                    "reason": (
                        "half_open_probe" if probe_count else "closed"
                    ),
                    "probe_count": probe_count,
                },
            )()
            return self.authorization

        def abort_attempt(self, authorization, *, reason):
            self.abort_calls.append((authorization, reason))
            return type("AbortResult", (), {"released_probe_count": probe_count})()

        def finish_attempt(self, *args, **kwargs):
            self.finish_calls.append((args, kwargs))

    class ClaimFailureStore(InMemoryContextArtifactStore):
        def claim(self, *args, **kwargs):
            if failure_kind == "artifact_busy":
                raise ContextArtifactBusy("busy")
            if failure_kind == "artifact_lease_lost":
                raise ContextArtifactLeaseLost("lease lost")
            return super().claim(*args, **kwargs)

    class FailAfterAuthorizationParent:
        def __init__(self):
            self.calls = 0

        def ensure_owned(self):
            self.calls += 1
            if failure_kind == "parent" and self.calls == 2:
                raise ContextArtifactLeaseLost("parent lease lost")

    containment = AbortSpyContainment()
    runner = ContextCompressionRunner(
        ClaimFailureStore(),
        lease_seconds=30,
        failure_containment=containment,
    )
    provider_calls = []

    with pytest.raises(expected_exception):
        resolve(
            runner,
            compressor=lambda _request: provider_calls.append("provider"),
            parent=FailAfterAuthorizationParent(),
        )

    assert containment.abort_calls == [
        (containment.authorization, abort_reason)
    ]
    assert containment.finish_calls == []
    assert containment.streak_updates == 0
    assert provider_calls == []
