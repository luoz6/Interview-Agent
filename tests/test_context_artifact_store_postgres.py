from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from app.services.context_artifact_store import PostgresContextArtifactStore
from app.services.context_artifacts import (
    ContextArtifactBusy,
    ContextArtifactCleanupPolicy,
    ContextArtifactConflict,
    ContextArtifactIdentity,
    ContextArtifactIdentityMaterial,
    ContextArtifactLeaseLost,
)
from tests.postgres_support import make_runtime_table_prefix, require_postgres_dsn


pytestmark = pytest.mark.pg_runtime


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


def make_payload():
    return {
        "schema_version": "question-conversation-v1",
        "question_id_sha256": "5" * 64,
        "units": [],
        "unresolved_topics": [],
        "source_message_count": 1,
    }


def database_clock(store):
    """Return a cutoff in the same clock domain as PostgreSQL row timestamps."""
    with store._connection_provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp()")
            return cursor.fetchone()[0]


@pytest.fixture
def store():
    prefix = make_runtime_table_prefix("context_artifacts")
    result = PostgresContextArtifactStore(
        dsn=require_postgres_dsn(),
        table_prefix=prefix,
        schema_mode="migrate",
    )
    yield result
    from psycopg2 import sql

    with result._connection_provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("DROP TABLE IF EXISTS {refs}").format(
                    refs=sql.Identifier(result.refs_table)
                )
            )
            cursor.execute(
                sql.SQL("DROP TABLE IF EXISTS {artifacts}").format(
                    artifacts=sql.Identifier(result.artifacts_table)
                )
            )


def test_claim_complete_reuse_and_owner_ref(store):
    identity = make_identity()
    claim = store.claim(identity, worker_id="worker-1", lease_seconds=30)
    record = store.complete(claim, make_payload())

    reused = store.claim(identity, worker_id="worker-2", lease_seconds=30)
    ref = store.create_owner_ref(
        record,
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
    )
    loaded = store.load_ref(
        ref,
        owner_type="interview_session",
        owner_key="session-1",
        purpose="interview_conversation_context",
        expected_identity=identity,
    )

    assert reused.status == "completed"
    assert reused.output_sha256 == record.output_sha256
    assert loaded == record


def test_identity_v0_columns_remain_null_and_reload_without_rekeying(store):
    identity = make_identity()
    record = store.complete(
        store.claim(identity, worker_id="worker-v0", lease_seconds=30),
        make_payload(),
    )

    with store._connection_provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    "SELECT identity_schema_version, compression_intent_sha256 "
                    "FROM {artifacts} WHERE artifact_id = %s::uuid"
                ),
                (record.artifact_id,),
            )
            stored_identity_version = cursor.fetchone()

    reloaded = store.get_terminal_by_key(identity.artifact_key)

    assert stored_identity_version == (None, None)
    assert reloaded is not None
    assert reloaded.identity == identity
    assert reloaded.identity.artifact_key == identity.artifact_key


def test_identity_v1_round_trips_through_direct_and_joined_ref_loaders(store):
    identity = make_identity(
        identity_schema_version="identity-v1",
        compression_intent_sha256="6" * 64,
    )
    record = store.complete(
        store.claim(identity, worker_id="worker-v1", lease_seconds=30),
        make_payload(),
    )
    ref = store.create_owner_ref(
        record,
        owner_type="interview_session",
        owner_key="session-v1",
        purpose="interview_conversation_context",
    )

    direct = store.get_terminal_by_key(identity.artifact_key)
    joined = store.load_ref(
        ref,
        owner_type="interview_session",
        owner_key="session-v1",
        purpose="interview_conversation_context",
        expected_identity=identity,
    )

    assert direct is not None
    assert direct.identity == identity
    assert joined.identity == identity
    assert joined.identity.material.identity_schema_version == "identity-v1"
    assert joined.identity.material.compression_intent_sha256 == "6" * 64


@pytest.mark.parametrize(
    ("identity_schema_version", "compression_intent_sha256"),
    (
        ("identity-v1", None),
        (None, "6" * 64),
        ("identity-v2", "6" * 64),
        ("identity-v1", "not-a-digest"),
    ),
)
def test_database_rejects_invalid_identity_v1_material(
    store,
    identity_schema_version,
    compression_intent_sha256,
):
    identity = make_identity()
    claim = store.claim(identity, worker_id="worker-invalid-v1", lease_seconds=30)

    import psycopg2

    with pytest.raises(psycopg2.errors.CheckViolation):
        with store._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    store._sql(
                        "UPDATE {artifacts} SET identity_schema_version = %s, "
                        "compression_intent_sha256 = %s "
                        "WHERE artifact_id = %s::uuid"
                    ),
                    (
                        identity_schema_version,
                        compression_intent_sha256,
                        claim.artifact_id,
                    ),
                )


def test_live_claim_busy_and_expired_claim_is_fenced(store):
    identity = make_identity()
    first = store.claim(identity, worker_id="worker-1", lease_seconds=30)
    with pytest.raises(ContextArtifactBusy):
        store.claim(identity, worker_id="worker-2", lease_seconds=30)

    with store._connection_provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    "UPDATE {artifacts} SET claim_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE artifact_id = %s::uuid"
                ),
                (first.artifact_id,),
            )

    current = store.claim(identity, worker_id="worker-2", lease_seconds=30)
    assert current.fencing_version == first.fencing_version + 1
    with pytest.raises(ContextArtifactLeaseLost):
        store.complete(first, make_payload())


def test_failed_artifact_reclaims_and_cleanup_uses_one_global_batch(store):
    identity = make_identity()
    claim = store.claim(identity, worker_id="worker-1", lease_seconds=30)
    store.fail(claim, error_code="provider_timeout")
    current = store.claim(identity, worker_id="worker-2", lease_seconds=30)
    store.fail(current, error_code="provider_timeout")

    now = database_clock(store)
    result = store.cleanup(
        ContextArtifactCleanupPolicy(
            completed_before=now + timedelta(seconds=1),
            failed_before=now + timedelta(seconds=1),
            prep_ref_expires_before=now + timedelta(seconds=1),
            batch_size=1,
        )
    )

    assert result.deleted_failed_artifacts == 1
    assert sum(
        (
            result.deleted_owner_refs,
            result.deleted_completed_artifacts,
            result.deleted_failed_artifacts,
        )
    ) == 1


def test_ref_foreign_key_targets_exact_prefixed_artifact_table(store):
    with store._connection_provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT target.relname
                FROM pg_constraint constraint_row
                JOIN pg_class source
                  ON source.oid = constraint_row.conrelid
                JOIN pg_class target
                  ON target.oid = constraint_row.confrelid
                WHERE constraint_row.contype = 'f'
                  AND source.relname = %s
                """,
                (store.refs_table,),
            )
            targets = {row[0] for row in cursor.fetchall()}

    assert targets == {store.artifacts_table}


def test_concurrent_claim_has_one_owner_and_one_busy_result(store):
    identity = make_identity()
    barrier = Barrier(2)

    def claim(worker_id):
        barrier.wait(timeout=5)
        try:
            value = store.claim(
                identity,
                worker_id=worker_id,
                lease_seconds=30,
            )
            return ("owned", value.claim_owner, value.fencing_version)
        except ContextArtifactBusy:
            return ("busy", worker_id, None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-1", "worker-2")))

    assert sorted(item[0] for item in results) == ["busy", "owned"]
    owned = next(item for item in results if item[0] == "owned")
    assert owned[1] in {"worker-1", "worker-2"}
    assert owned[2] == 1


def test_terminal_transitions_clear_all_claim_fields(store):
    completed_identity = make_identity()
    completed = store.claim(
        completed_identity,
        worker_id="worker-completed",
        lease_seconds=30,
    )
    store.complete(completed, make_payload())

    failed_identity = make_identity(source_sha256="6" * 64)
    failed = store.claim(
        failed_identity,
        worker_id="worker-failed",
        lease_seconds=30,
    )
    store.fail(failed, error_code="provider_timeout")

    with store._connection_provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    "SELECT status, claim_owner, claim_token, claim_expires_at, "
                    "output_json IS NOT NULL, output_sha256 IS NOT NULL, "
                    "completed_at IS NOT NULL, last_error_code "
                    "FROM {artifacts} WHERE artifact_id IN (%s::uuid, %s::uuid) "
                    "ORDER BY status"
                ),
                (completed.artifact_id, failed.artifact_id),
            )
            rows = cursor.fetchall()

    assert rows == [
        ("completed", None, None, None, True, True, True, None),
        (
            "failed",
            None,
            None,
            None,
            False,
            False,
            False,
            "provider_timeout",
        ),
    ]


def test_load_ref_rejects_complete_identity_mismatch(store):
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

    for mismatch in (
        make_identity(source_sha256="6" * 64),
        make_identity(semantic_focus_sha256="7" * 64),
        make_identity(compressor_model="gpt-4.1"),
        make_identity(compressor_settings_sha256="8" * 64),
        make_identity(target_output_tokens=512),
        make_identity(
            identity_schema_version="identity-v1",
            compression_intent_sha256="9" * 64,
        ),
    ):
        with pytest.raises(ContextArtifactConflict):
            store.load_ref(
                ref,
                owner_type="interview_session",
                owner_key="session-1",
                purpose="interview_conversation_context",
                expected_identity=mismatch,
            )


def test_concurrent_cleanup_uses_skip_locked_without_double_deletion(store):
    for index in range(6):
        digest_character = format(index + 9, "x")[-1]
        identity = make_identity(
            source_sha256=digest_character * 64,
            semantic_focus_sha256=format(index + 2, "x")[-1] * 64,
        )
        claim = store.claim(
            identity,
            worker_id=f"worker-{index}",
            lease_seconds=30,
        )
        store.fail(claim, error_code="provider_timeout")

    barrier = Barrier(2)
    cutoff = database_clock(store) + timedelta(seconds=1)
    policy = ContextArtifactCleanupPolicy(
        completed_before=cutoff,
        failed_before=cutoff,
        prep_ref_expires_before=cutoff,
        batch_size=2,
    )

    def cleanup():
        barrier.wait(timeout=5)
        return store.cleanup(policy)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: cleanup(), range(2)))

    assert sum(result.deleted_failed_artifacts for result in results) == 4
    assert all(
        result.deleted_owner_refs == 0
        and result.deleted_completed_artifacts == 0
        for result in results
    )
    with store._connection_provider.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                store._sql(
                    "SELECT COUNT(*) FROM {artifacts} WHERE status = 'failed'"
                )
            )
            remaining = cursor.fetchone()[0]
    assert remaining == 2
