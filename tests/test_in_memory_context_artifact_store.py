from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest

from app.services.context_artifacts import (
    ContextArtifactBusy,
    ContextArtifactCleanupPolicy,
    ContextArtifactConflict,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextArtifactLeaseLost,
    ContextArtifactMissing,
)
from app.services.in_memory_context_artifact_store import (
    InMemoryContextArtifactStore,
)


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


def make_identity(**changes):
    material = ContextArtifactIdentityMaterial(
        artifact_type="question_conversation",
        privacy_scope_sha256="1" * 64,
        source_sha256="2" * 64,
        source_manifest_sha256=None,
        semantic_focus_sha256="3" * 64,
        compression_policy_version="conversation-v1",
        prompt_contract_version="prompt-v1",
        output_schema_version="question-conversation-v1",
        compressor_provider="openai-compatible",
        compressor_model="gpt-4o",
        compressor_settings_sha256="4" * 64,
        target_output_tokens=256,
    )
    return ContextArtifactIdentity.from_material(replace(material, **changes))


def make_payload(summary="Use idempotency for retry safety."):
    return {
        "schema_version": "question-conversation-v1",
        "question_id_sha256": "5" * 64,
        "units": [],
        "unresolved_topics": [],
        "source_message_count": 1,
    }


def make_prep_payload():
    return {
        "schema_version": "prep-context-v1",
        "role_units": [],
        "responsibility_units": [],
        "experience_units": [],
        "project_units": [],
        "constraint_units": [],
    }


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(clock):
    counters = {"artifact": 0, "claim": 0, "ref": 0}

    def next_value(kind):
        counters[kind] += 1
        return f"{kind}-{counters[kind]}"

    return InMemoryContextArtifactStore(
        clock=clock,
        artifact_id_factory=lambda: next_value("artifact"),
        claim_token_factory=lambda: next_value("claim"),
        ref_id_factory=lambda: next_value("ref"),
    )


def test_claim_busy_expired_reclaim_and_fencing(store, clock):
    identity = make_identity()
    first = store.claim(identity, worker_id="worker-1", lease_seconds=30)

    assert first.status == "running"
    assert first.fencing_version == 1
    with pytest.raises(ContextArtifactBusy):
        store.claim(identity, worker_id="worker-2", lease_seconds=30)

    clock.advance(seconds=31)
    current = store.claim(identity, worker_id="worker-2", lease_seconds=30)

    assert current.artifact_id == first.artifact_id
    assert current.claim_token != first.claim_token
    assert current.fencing_version == 2
    assert store.heartbeat(first, lease_seconds=30) is False
    with pytest.raises(ContextArtifactLeaseLost):
        store.complete(first, make_payload())


def test_failed_claim_is_immediately_reclaimed_as_running(store):
    identity = make_identity()
    first = store.claim(identity, worker_id="worker-1", lease_seconds=30)
    store.fail(first, error_code="provider_timeout")

    failed = store.get_terminal_by_key(identity.artifact_key)
    current = store.claim(identity, worker_id="worker-2", lease_seconds=30)

    assert failed.status == "failed"
    assert failed.last_error_code == "provider_timeout"
    assert current.status == "running"
    assert current.fencing_version == first.fencing_version + 1


def test_completed_artifact_is_write_once_and_reused(store):
    identity = make_identity()
    claim = store.claim(identity, worker_id="worker-1", lease_seconds=30)
    record = store.complete(claim, make_payload())
    reused = store.claim(identity, worker_id="worker-2", lease_seconds=30)

    assert record.status == "completed"
    assert reused.status == "completed"
    assert reused.claim_token is None
    assert reused.output_sha256 == record.output_sha256
    assert store.get_terminal_by_key(identity.artifact_key) == record
    with pytest.raises(ContextArtifactLeaseLost):
        store.fail(claim, error_code="late_failure")


def test_completed_reuse_revalidates_payload_schema_and_digest(store):
    identity = make_identity()
    store.complete(
        store.claim(identity, worker_id="worker-1", lease_seconds=30),
        make_payload(),
    )
    store._rows_by_key[identity.artifact_key].payload[
        "source_message_count"
    ] = 99

    with pytest.raises(ContextArtifactConflict, match="output digest"):
        store.claim(identity, worker_id="worker-2", lease_seconds=30)
    with pytest.raises(ContextArtifactConflict):
        store.get_terminal_by_key(identity.artifact_key)


def test_claim_record_and_load_payloads_are_deep_copies(store):
    identity = make_identity()
    record = store.complete(
        store.claim(identity, worker_id="worker-1", lease_seconds=30),
        make_payload(),
    )
    ref = store.create_owner_ref(
        record,
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
    )

    record.payload["source_message_count"] = 99
    completed_claim = store.claim(
        identity,
        worker_id="worker-2",
        lease_seconds=30,
    )
    assert completed_claim.payload["source_message_count"] == 1
    completed_claim.payload["source_message_count"] = 77

    loaded = store.load_ref(
        ref,
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
        expected_identity=identity,
    )
    assert loaded.payload["source_message_count"] == 1
    loaded.payload["source_message_count"] = 55
    assert store.get_terminal_by_key(identity.artifact_key).payload[
        "source_message_count"
    ] == 1


def test_load_ref_checks_owner_purpose_digest_and_complete_identity(store):
    identity = make_identity()
    claim = store.claim(identity, worker_id="worker-1", lease_seconds=30)
    record = store.complete(claim, make_payload())
    ref = store.create_owner_ref(
        record,
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
    )
    same_ref = store.create_owner_ref(
        record,
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
    )

    assert same_ref == ref
    assert store.load_ref(
        ref,
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
        expected_identity=identity,
    ) == record

    identity_mismatches = (
        make_identity(source_sha256="6" * 64),
        make_identity(semantic_focus_sha256="7" * 64),
        make_identity(compressor_provider="other-compatible"),
        make_identity(compressor_model="deepseek-chat"),
        make_identity(compressor_settings_sha256="8" * 64),
        make_identity(prompt_contract_version="prompt-v2"),
        make_identity(target_output_tokens=128),
    )
    for kwargs in (
        {"owner_key": "session-2"},
        {"purpose": "interview_evidence_context"},
        *(
            {"expected_identity": mismatch}
            for mismatch in identity_mismatches
        ),
    ):
        arguments = {
            "owner_type": "interview_session",
            "owner_key": "session-1",
            "purpose": "interview_conversation_context",
            "expected_identity": identity,
        }
        arguments.update(kwargs)
        with pytest.raises(ContextArtifactConflict):
            store.load_ref(ref, **arguments)

    with pytest.raises(ContextArtifactMissing):
        store.load_ref(
            ref.model_copy(update={"artifact_ref": "context-artifact-ref:missing"}),
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_identity=identity,
        )


def test_owner_purpose_contract_is_checked_on_create_and_load(store):
    identity = make_identity()
    record = store.complete(
        store.claim(identity, worker_id="worker", lease_seconds=30),
        make_payload(),
    )
    with pytest.raises(ContextArtifactConflict, match="owner purpose"):
        store.create_owner_ref(
            record,
            owner_type="review_job",
            owner_key="job-1",
            purpose="interview_conversation_context",
        )

    ref = store.create_owner_ref(
        record,
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
    )
    stored_ref = store._refs[ref.artifact_ref]
    stored_ref.owner_type = "review_job"
    stored_ref.owner_key = "job-1"
    with pytest.raises(ContextArtifactConflict):
        store.load_ref(
            ref,
            owner_type="review_job",
            owner_key="job-1",
            purpose="interview_conversation_context",
            expected_identity=identity,
        )

    with pytest.raises(ContextArtifactConflict):
        store.load_ref(
            ref.model_copy(update={"artifact_sha256": "9" * 64}),
            owner_type="interview_session",
            owner_key="session-1",
            purpose="interview_conversation_context",
            expected_identity=identity,
        )


def test_cleanup_uses_one_policy_and_never_deletes_live_references(store, clock):
    completed_identity = make_identity(source_sha256="6" * 64)
    failed_identity = make_identity(source_sha256="7" * 64)
    retained_identity = make_identity(
        artifact_type="prep_context",
        source_sha256="8" * 64,
        semantic_focus_sha256=None,
        output_schema_version="prep-context-v1",
    )

    completed = store.complete(
        store.claim(completed_identity, worker_id="worker", lease_seconds=30),
        make_payload(),
    )
    failed_claim = store.claim(failed_identity, worker_id="worker", lease_seconds=30)
    store.fail(failed_claim, error_code="provider_timeout")
    retained = store.complete(
        store.claim(retained_identity, worker_id="worker", lease_seconds=30),
        make_prep_payload(),
    )
    store.create_owner_ref(
        retained,
        owner_type="prep_run",
        owner_key="prep-1",
        purpose="prep_plan_context",
        retain_until=clock() + timedelta(hours=2),
    )
    clock.advance(hours=1)
    result = store.cleanup(
        ContextArtifactCleanupPolicy(
            completed_before=clock(),
            failed_before=clock(),
            prep_ref_expires_before=clock(),
            batch_size=10,
        )
    )

    assert result.deleted_owner_refs == 0
    assert result.deleted_completed_artifacts == 1
    assert result.deleted_failed_artifacts == 1
    assert store.get_terminal_by_key(completed_identity.artifact_key) is None
    assert store.get_terminal_by_key(failed_identity.artifact_key) is None
    assert store.get_terminal_by_key(retained_identity.artifact_key) is not None


def test_expired_prep_ref_and_artifact_are_cleaned_in_one_bounded_operation(
    store,
    clock,
):
    identity = make_identity(
        artifact_type="prep_context",
        source_sha256="9" * 64,
        semantic_focus_sha256=None,
        output_schema_version="prep-context-v1",
    )
    record = store.complete(
        store.claim(identity, worker_id="worker", lease_seconds=30),
        make_prep_payload(),
    )
    store.create_owner_ref(
        record,
        owner_type="prep_run",
        owner_key="prep-1",
        purpose="prep_plan_context",
        retain_until=clock() + timedelta(minutes=30),
    )
    clock.advance(hours=1)

    first = store.cleanup(
        ContextArtifactCleanupPolicy(
            completed_before=clock(),
            failed_before=clock(),
            prep_ref_expires_before=clock(),
            batch_size=1,
        )
    )
    second = store.cleanup(
        ContextArtifactCleanupPolicy(
            completed_before=clock(),
            failed_before=clock(),
            prep_ref_expires_before=clock(),
            batch_size=1,
        )
    )

    assert first.deleted_owner_refs == 1
    assert first.deleted_completed_artifacts == 0
    assert second.deleted_completed_artifacts == 1
    assert store.get_terminal_by_key(identity.artifact_key) is None


def test_concurrent_cleanup_does_not_double_count_candidates(store, clock):
    for index in range(4):
        identity = make_identity(source_sha256=f"{index + 10:064x}")
        store.complete(
            store.claim(identity, worker_id="worker", lease_seconds=30),
            make_payload(),
        )
    clock.advance(hours=1)
    policy = ContextArtifactCleanupPolicy(
        completed_before=clock(),
        failed_before=clock(),
        prep_ref_expires_before=clock(),
        batch_size=2,
    )
    barrier = Barrier(3)
    results = []

    def cleanup():
        barrier.wait()
        results.append(store.cleanup(policy))

    threads = [Thread(target=cleanup), Thread(target=cleanup)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sum(item.deleted_completed_artifacts for item in results) == 4
    assert all(item.deleted_completed_artifacts <= 2 for item in results)


def test_cleanup_applies_completed_and_failed_cutoffs_independently(store, clock):
    completed_identity = make_identity(source_sha256="a" * 64)
    failed_identity = make_identity(source_sha256="b" * 64)
    store.complete(
        store.claim(completed_identity, worker_id="worker", lease_seconds=30),
        make_payload(),
    )
    failed_claim = store.claim(
        failed_identity,
        worker_id="worker",
        lease_seconds=30,
    )
    store.fail(failed_claim, error_code="provider_timeout")
    created_at = clock()
    clock.advance(hours=1)

    result = store.cleanup(
        ContextArtifactCleanupPolicy(
            completed_before=created_at,
            failed_before=clock(),
            prep_ref_expires_before=clock(),
            batch_size=10,
        )
    )

    assert result.deleted_completed_artifacts == 0
    assert result.deleted_failed_artifacts == 1
    assert store.get_terminal_by_key(completed_identity.artifact_key) is not None
    assert store.get_terminal_by_key(failed_identity.artifact_key) is None
