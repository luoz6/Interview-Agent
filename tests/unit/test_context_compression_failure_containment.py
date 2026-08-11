from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from importlib import import_module
import json
from threading import Barrier

import pytest


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 10, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def _subjects(*, clock=None):
    domain = import_module(
        "app.services.context_compression_failure_containment"
    )
    stores = import_module(
        "app.services.in_memory_context_compression_failure_store"
    )
    clock = clock or Clock()
    store = stores.InMemoryContextCompressionFailureStore(clock=clock)
    config = domain.FailureContainmentConfig(
        provider_circuit_threshold=3,
        provider_circuit_cooldown_seconds=300,
        validation_quarantine_threshold=2,
        validation_quarantine_cooldown_seconds=3_600,
        failure_state_lease_seconds=60,
    )
    service = domain.ContextCompressionFailureContainment(
        store=store,
        config=config,
        clock=clock,
    )
    return domain, store, service, clock


def _provider_scope(domain, **changes):
    values = {
        "privacy_scope_sha256": "1" * 64,
        "owner_type": "interview_session",
        "owner_key": "PRIVATE_SESSION_CANARY",
        "provider": "openai-compatible",
        "model": "gpt-4o",
        "artifact_type": "question_memory",
        "policy_version": "question-memory-v1",
    }
    values.update(changes)
    return domain.build_provider_circuit_scope(**values)


def _quarantine_scope(domain, **changes):
    values = {
        "privacy_scope_sha256": "1" * 64,
        "owner_type": "interview_session",
        "owner_key": "PRIVATE_SESSION_CANARY",
        "artifact_type": "question_memory",
        "source_manifest_sha256": "2" * 64,
        "compression_intent_sha256": "3" * 64,
        "prompt_contract_version": "question-memory-prompt-v1",
        "output_schema_version": "question-memory-v1",
        "policy_version": "question-memory-v1",
        "provider": "openai-compatible",
        "model": "gpt-4o",
    }
    values.update(changes)
    return domain.build_validation_quarantine_scope(**values)


def _payload(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    return dict(value)


def _fail(service, scope, code, *, worker="worker-1"):
    decision = service.before_attempt(scope, worker_id=worker)
    assert decision.allow_provider_call is True
    return service.record_failure(
        scope,
        failure_code=code,
        decision=decision,
    )


def test_provider_and_validation_scopes_have_exact_independent_keys():
    domain, _, _, _ = _subjects()
    provider = _provider_scope(domain)
    quarantine = _quarantine_scope(domain)

    assert provider.kind == "provider_circuit"
    assert quarantine.kind == "validation_quarantine"
    assert provider.state_key_sha256 != quarantine.state_key_sha256
    assert provider.owner_key_sha256 == quarantine.owner_key_sha256
    assert provider.privacy_scope_sha256 == quarantine.privacy_scope_sha256
    assert not hasattr(provider, "source_manifest_sha256")
    assert quarantine.source_manifest_sha256 == "2" * 64


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("privacy_scope_sha256", "9" * 64),
        ("owner_key", "another-owner"),
        ("provider", "another-provider"),
        ("model", "another-model"),
        ("artifact_type", "evidence_compression"),
        ("policy_version", "question-memory-v2"),
    ),
)
def test_provider_circuit_key_is_exactly_owner_and_provider_scoped(
    field,
    replacement,
):
    domain, _, _, _ = _subjects()
    baseline = _provider_scope(domain)
    changed = _provider_scope(domain, **{field: replacement})

    assert changed.state_key_sha256 != baseline.state_key_sha256


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("privacy_scope_sha256", "9" * 64),
        ("owner_key", "another-owner"),
        ("artifact_type", "evidence_compression"),
        ("source_manifest_sha256", "4" * 64),
        ("compression_intent_sha256", "5" * 64),
        ("prompt_contract_version", "question-memory-prompt-v2"),
        ("output_schema_version", "question-memory-v2"),
        ("policy_version", "question-memory-v2"),
        ("provider", "another-provider"),
        ("model", "another-model"),
    ),
)
def test_validation_quarantine_key_is_exactly_semantic_input_scoped(
    field,
    replacement,
):
    domain, _, _, _ = _subjects()
    baseline = _quarantine_scope(domain)
    changed = _quarantine_scope(domain, **{field: replacement})

    assert changed.state_key_sha256 != baseline.state_key_sha256


def test_three_provider_failures_open_owner_circuit_and_block_fourth_call():
    domain, store, service, _ = _subjects()
    scope = _provider_scope(domain)

    for _ in range(3):
        record = _fail(service, scope, "provider_timeout")

    blocked = service.before_attempt(scope, worker_id="worker-4")
    authoritative = store.get(scope.state_key_sha256)
    assert record.state == authoritative.state == "open"
    assert authoritative.consecutive_failures == 3
    assert blocked.allow_provider_call is False
    assert blocked.reason == "provider_circuit_open"


def test_two_validation_failures_quarantine_only_matching_source_and_intent():
    domain, store, service, _ = _subjects()
    matching = _quarantine_scope(domain)
    unrelated_source = _quarantine_scope(
        domain,
        source_manifest_sha256="8" * 64,
    )
    unrelated_intent = _quarantine_scope(
        domain,
        compression_intent_sha256="7" * 64,
    )

    _fail(service, matching, "invalid_schema")
    _fail(service, matching, "grounding_failed")

    assert store.get(matching.state_key_sha256).state == "open"
    assert service.before_attempt(
        matching,
        worker_id="matching",
    ).allow_provider_call is False
    assert service.before_attempt(
        unrelated_source,
        worker_id="source",
    ).allow_provider_call is True
    assert service.before_attempt(
        unrelated_intent,
        worker_id="intent",
    ).allow_provider_call is True


@pytest.mark.parametrize(
    "failure_code",
    (
        "artifact_busy",
        "parent_lease_lost",
        "identity_conflict",
        "privacy_scope_invalid",
        "cancelled",
        "stale_ownership",
    ),
)
def test_non_counted_failures_never_increment_either_state(failure_code):
    domain, store, service, _ = _subjects()
    for scope in (_provider_scope(domain), _quarantine_scope(domain)):
        decision = service.before_attempt(scope, worker_id="worker")
        result = service.record_failure(
            scope,
            failure_code=failure_code,
            decision=decision,
        )
        assert result is None
        assert store.get(scope.state_key_sha256) is None


@pytest.mark.parametrize(
    ("scope_kind", "failure_code", "counted"),
    (
        ("provider", "provider_timeout", True),
        ("provider", "provider_connection", True),
        ("provider", "provider_unavailable", True),
        ("provider", "invalid_schema", False),
        ("quarantine", "invalid_schema", True),
        ("quarantine", "grounding_failed", True),
        ("quarantine", "provider_timeout", False),
    ),
)
def test_failure_codes_count_only_in_their_declared_state(
    scope_kind,
    failure_code,
    counted,
):
    domain, store, service, _ = _subjects()
    scope = (
        _provider_scope(domain)
        if scope_kind == "provider"
        else _quarantine_scope(domain)
    )
    decision = service.before_attempt(scope, worker_id="worker")
    result = service.record_failure(
        scope,
        failure_code=failure_code,
        decision=decision,
    )

    assert (result is not None) is counted
    assert (store.get(scope.state_key_sha256) is not None) is counted


def test_cooldown_allows_exactly_one_fenced_half_open_probe():
    domain, _, service, clock = _subjects()
    scope = _provider_scope(domain)
    for _ in range(3):
        _fail(service, scope, "provider_unavailable")
    clock.advance(301)

    barrier = Barrier(2)

    def claim(worker):
        barrier.wait()
        return service.before_attempt(scope, worker_id=worker)

    with ThreadPoolExecutor(max_workers=2) as executor:
        decisions = list(
            executor.map(
                claim,
                ("worker-a", "worker-b"),
            )
        )

    assert sum(item.allow_provider_call for item in decisions) == 1
    probe = next(item for item in decisions if item.allow_provider_call)
    blocked = next(item for item in decisions if not item.allow_provider_call)
    assert probe.reason == "half_open_probe"
    assert probe.fencing_version >= 1
    assert blocked.reason == "half_open_probe_owned"


def test_successful_half_open_probe_resets_streak_and_closes_state():
    domain, store, service, clock = _subjects()
    scope = _provider_scope(domain)
    for _ in range(3):
        _fail(service, scope, "provider_timeout")
    clock.advance(301)
    probe = service.before_attempt(scope, worker_id="probe-worker")

    closed = service.record_success(scope, decision=probe)

    assert closed.state == "closed"
    assert closed.consecutive_failures == 0
    assert closed.probe_owner_sha256 is None
    assert service.before_attempt(
        scope,
        worker_id="next-worker",
    ).allow_provider_call is True
    assert store.get(scope.state_key_sha256).state_version > 0


def test_expired_probe_reclaim_fences_stale_success_and_failure():
    domain, _, service, clock = _subjects()
    scope = _provider_scope(domain)
    for _ in range(3):
        _fail(service, scope, "provider_timeout")
    clock.advance(301)
    stale = service.before_attempt(scope, worker_id="stale-worker")
    clock.advance(61)
    current = service.before_attempt(scope, worker_id="current-worker")

    with pytest.raises(domain.FailureStateLeaseLost):
        service.record_success(scope, decision=stale)
    with pytest.raises(domain.FailureStateLeaseLost):
        service.record_failure(
            scope,
            failure_code="provider_timeout",
            decision=stale,
        )
    service.record_success(scope, decision=current)


def test_live_half_open_lease_is_not_removed_by_retention():
    domain, store, service, clock = _subjects()
    scope = _provider_scope(domain)
    for _ in range(3):
        _fail(service, scope, "provider_timeout")
    clock.advance(301)
    service.before_attempt(scope, worker_id="probe-worker")
    cleanup_now = clock()
    store.clock = lambda: pytest.fail(
        "cleanup_expired must use its explicit now argument"
    )

    deleted = store.cleanup_expired(
        before=cleanup_now + timedelta(days=1),
        now=cleanup_now,
        batch_size=100,
    )

    assert deleted == 0
    assert store.get(scope.state_key_sha256).state == "half_open"

    assert store.cleanup_expired(
        before=cleanup_now + timedelta(days=1),
        now=cleanup_now + timedelta(seconds=61),
        batch_size=100,
    ) == 1
    assert store.get(scope.state_key_sha256) is None


@pytest.mark.parametrize(
    "reason",
    ("artifact_reused", "provider_unclassified", "parent_or_runtime_failed"),
)
def test_runner_cleanup_reasons_are_stable_non_counted_codes(reason):
    domain, store, service, _ = _subjects()
    provider = _provider_scope(domain)
    validation = _quarantine_scope(domain)
    authorization = service.authorize_attempt(
        provider_scope=provider,
        validation_scope=validation,
        worker_id="cleanup-worker",
    )

    assert reason in domain.NON_COUNTED_FAILURE_CODES
    result = service.abort_attempt(authorization, reason=reason)
    assert result.released_probe_count == 0
    assert store.get(provider.state_key_sha256) is None
    assert store.get(validation.state_key_sha256) is None


def test_owner_delete_is_exactly_privacy_and_owner_scoped():
    domain, store, service, _ = _subjects()
    owned = _provider_scope(domain)
    other_owner = _provider_scope(domain, owner_key="other-owner")
    other_privacy = _provider_scope(
        domain,
        privacy_scope_sha256="9" * 64,
    )
    for scope in (owned, other_owner, other_privacy):
        _fail(service, scope, "provider_timeout")

    deleted = store.delete_owner(
        privacy_scope_sha256=owned.privacy_scope_sha256,
        owner_type=owned.owner_type,
        owner_key_sha256=owned.owner_key_sha256,
    )

    assert deleted == 1
    assert store.get(owned.state_key_sha256) is None
    assert store.get(other_owner.state_key_sha256) is not None
    assert store.get(other_privacy.state_key_sha256) is not None


def test_state_and_metric_dimensions_never_expose_raw_or_digest_identity():
    domain, store, service, _ = _subjects()
    scope = _provider_scope(domain)
    record = _fail(
        service,
        scope,
        "provider_timeout",
        worker="PRIVATE_WORKER_CANARY",
    )
    serialized = json.dumps(_payload(record), ensure_ascii=False, sort_keys=True)
    dimensions = domain.failure_state_metric_dimensions(record)

    assert "PRIVATE_SESSION_CANARY" not in serialized
    assert "candidate answer canary" not in serialized
    assert "raw provider error" not in serialized
    assert "PRIVATE_WORKER_CANARY" not in serialized
    assert "probe_token" not in dimensions
    assert set(dimensions) <= {
        "kind",
        "state",
        "failure_code",
        "store_outcome",
    }
    assert not any("sha256" in key or "digest" in key for key in dimensions)
    assert scope.owner_key_sha256 not in dimensions.values()
    assert scope.state_key_sha256 not in dimensions.values()
    assert store.get(scope.state_key_sha256) == record


@pytest.mark.parametrize(
    ("scope_factory", "failure_codes"),
    (
        (_provider_scope, ("provider_timeout", "provider_connection")),
        (_quarantine_scope, ("invalid_schema",)),
    ),
)
def test_ordinary_success_breaks_consecutive_failure_streak(
    scope_factory,
    failure_codes,
):
    domain, store, service, _ = _subjects()
    scope = scope_factory(domain)
    for failure_code in failure_codes:
        _fail(service, scope, failure_code)

    success = service.before_attempt(scope, worker_id="success-worker")
    closed = service.record_success(scope, decision=success)

    assert closed.state == "closed"
    assert closed.consecutive_failures == 0
    for failure_code in failure_codes:
        _fail(service, scope, failure_code)
    assert service.before_attempt(
        scope,
        worker_id="still-allowed",
    ).allow_provider_call is True
    assert store.get(scope.state_key_sha256).consecutive_failures == len(
        failure_codes
    )


@pytest.mark.parametrize(
    ("scope_factory", "failure_code", "threshold", "cooldown"),
    (
        (_provider_scope, "provider_timeout", 3, 301),
        (_quarantine_scope, "invalid_schema", 2, 3_601),
    ),
)
def test_failed_half_open_probe_reopens_each_state(
    scope_factory,
    failure_code,
    threshold,
    cooldown,
):
    domain, store, service, clock = _subjects()
    scope = scope_factory(domain)
    for _ in range(threshold):
        _fail(service, scope, failure_code)
    clock.advance(cooldown)
    probe = service.before_attempt(scope, worker_id="half-open-worker")

    reopened = service.record_failure(
        scope,
        failure_code=failure_code,
        decision=probe,
    )

    assert reopened.state == "open"
    assert reopened.open_until > clock()
    assert reopened.probe_owner_sha256 is None
    assert store.get(scope.state_key_sha256) == reopened
    assert service.before_attempt(
        scope,
        worker_id="blocked-worker",
    ).allow_provider_call is False


def test_live_probe_heartbeat_extends_lease_without_changing_fence():
    domain, store, service, clock = _subjects()
    scope = _provider_scope(domain)
    for _ in range(3):
        _fail(service, scope, "provider_timeout")
    clock.advance(301)
    probe = service.before_attempt(scope, worker_id="PRIVATE_WORKER_CANARY")
    clock.advance(50)

    renewed = service.heartbeat_probe(scope, decision=probe)
    clock.advance(20)
    blocked = service.before_attempt(scope, worker_id="competitor")

    assert renewed.fencing_version == probe.fencing_version
    assert renewed.probe_lease_until > clock()
    assert blocked.allow_provider_call is False
    assert blocked.reason == "half_open_probe_owned"
    serialized = json.dumps(_payload(store.get(scope.state_key_sha256)))
    assert "PRIVATE_WORKER_CANARY" not in serialized


def test_dual_scope_authorize_leaves_no_new_probe_when_second_scope_blocks():
    domain, store, service, clock = _subjects()
    provider = _provider_scope(domain)
    quarantine = _quarantine_scope(domain)
    unrelated = _quarantine_scope(
        domain,
        source_manifest_sha256="7" * 64,
    )
    for _ in range(3):
        _fail(service, provider, "provider_timeout")
    for _ in range(2):
        _fail(service, quarantine, "invalid_schema")
    clock.advance(301)

    blocked = service.authorize_attempt(
        provider_scope=provider,
        validation_scope=quarantine,
        worker_id="worker-blocked-by-quarantine",
    )

    assert blocked.allow_provider_call is False
    assert blocked.reason == "validation_quarantine_open"
    released = store.get(provider.state_key_sha256)
    assert released.state == "open"
    assert released.probe_owner_sha256 is None
    assert released.probe_lease_until is None

    allowed = service.authorize_attempt(
        provider_scope=provider,
        validation_scope=unrelated,
        worker_id="worker-unrelated-source",
    )
    assert allowed.allow_provider_call is True
    assert allowed.reason == "half_open_probe"


def test_dual_scope_authorize_uses_one_atomic_store_boundary():
    domain = import_module(
        "app.services.context_compression_failure_containment"
    )

    class FaultingAtomicStore:
        def __init__(self):
            self.authorize_calls = 0

        def authorize_attempt(self, **_kwargs):
            self.authorize_calls += 1
            raise RuntimeError("injected transaction loss")

        def before_attempt(self, **_kwargs):
            raise AssertionError("dual authorization must not split key writes")

    store = FaultingAtomicStore()
    service = domain.ContextCompressionFailureContainment(
        store=store,
        config=domain.FailureContainmentConfig(
            provider_circuit_threshold=3,
            provider_circuit_cooldown_seconds=300,
            validation_quarantine_threshold=2,
            validation_quarantine_cooldown_seconds=3_600,
            failure_state_lease_seconds=60,
        ),
    )

    with pytest.raises(RuntimeError, match="transaction loss"):
        service.authorize_attempt(
            provider_scope=_provider_scope(domain),
            validation_scope=_quarantine_scope(domain),
            worker_id="worker-atomic",
        )
    assert store.authorize_calls == 1


def test_validation_failure_finishes_both_states_in_one_combined_commit():
    domain, store, service, _ = _subjects()
    provider = _provider_scope(domain)
    quarantine = _quarantine_scope(domain)
    _fail(service, provider, "provider_timeout")
    authorization = service.authorize_attempt(
        provider_scope=provider,
        validation_scope=quarantine,
        worker_id="worker-validation-failure",
    )

    result = service.finish_attempt(
        authorization,
        outcome="validation_failed",
        failure_code="grounding_failed",
    )

    assert result.provider_state.state == "closed"
    assert result.provider_state.consecutive_failures == 0
    assert result.validation_state.consecutive_failures == 1
    assert store.get(provider.state_key_sha256) == result.provider_state
    assert store.get(quarantine.state_key_sha256) == result.validation_state


def test_combined_finish_fault_leaves_both_states_unchanged():
    domain = import_module(
        "app.services.context_compression_failure_containment"
    )
    provider_before = object()
    validation_before = object()

    class FaultingAtomicStore:
        def __init__(self):
            self.provider_state = provider_before
            self.validation_state = validation_before
            self.finish_calls = 0

        def finish_attempt(self, **_kwargs):
            self.finish_calls += 1
            raise RuntimeError("injected second-state mutation failure")

    store = FaultingAtomicStore()
    service = domain.ContextCompressionFailureContainment(
        store=store,
        config=domain.FailureContainmentConfig(
            provider_circuit_threshold=3,
            provider_circuit_cooldown_seconds=300,
            validation_quarantine_threshold=2,
            validation_quarantine_cooldown_seconds=3_600,
            failure_state_lease_seconds=60,
        ),
    )
    authorization = object()

    with pytest.raises(RuntimeError, match="second-state"):
        service.finish_attempt(
            authorization,
            outcome="validation_failed",
            failure_code="grounding_failed",
        )

    assert store.finish_calls == 1
    assert store.provider_state is provider_before
    assert store.validation_state is validation_before


@pytest.mark.parametrize(
    ("builder", "changes"),
    (
        (_provider_scope, {"owner_type": "deployment"}),
        (_provider_scope, {"owner_key": ""}),
        (_provider_scope, {"owner_key": "   "}),
        (_quarantine_scope, {"owner_type": "global"}),
        (_quarantine_scope, {"owner_key": ""}),
    ),
)
def test_scope_builders_reject_noncanonical_owner_identity(builder, changes):
    domain, _, _, _ = _subjects()
    with pytest.raises((TypeError, ValueError)):
        builder(domain, **changes)


@pytest.mark.parametrize(
    "changes",
    (
        {
            "provider_circuit_cooldown_seconds": 60,
            "failure_state_lease_seconds": 60,
        },
        {
            "validation_quarantine_cooldown_seconds": 59,
            "failure_state_lease_seconds": 60,
        },
    ),
)
def test_failure_state_lease_must_be_shorter_than_both_cooldowns(changes):
    domain = import_module(
        "app.services.context_compression_failure_containment"
    )
    values = {
        "provider_circuit_threshold": 3,
        "provider_circuit_cooldown_seconds": 300,
        "validation_quarantine_threshold": 2,
        "validation_quarantine_cooldown_seconds": 3_600,
        "failure_state_lease_seconds": 60,
    }
    values.update(changes)

    with pytest.raises(ValueError, match="shorter"):
        domain.FailureContainmentConfig(**values)
