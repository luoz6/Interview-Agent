from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from importlib import import_module
import os
from threading import Barrier, Thread

import pytest

from app.services.postgres_connections import PostgresSchemaNotReady
from app.services.postgres_schema_contract import (
    required_check_tokens_for_relation,
    required_columns_for_relation,
    required_index_tokens_for_relation,
    required_nullable_columns_for_relation,
)
from tests.postgres_support import (
    drop_runtime_tables,
    make_runtime_table_prefix,
    require_postgres_dsn,
)


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
RELATION_SUFFIX = "_context_compression_failure_states"
REQUIRED_COLUMNS = {
    "state_key_sha256",
    "kind",
    "privacy_scope_sha256",
    "owner_type",
    "owner_key_sha256",
    "provider",
    "model",
    "artifact_type",
    "policy_version",
    "source_manifest_sha256",
    "compression_intent_sha256",
    "prompt_contract_version",
    "output_schema_version",
    "consecutive_failure_count",
    "state",
    "open_until",
    "probe_owner_sha256",
    "probe_token",
    "probe_lease_until",
    "fencing_version",
    "state_version",
    "last_failure_code",
    "created_at",
    "updated_at",
}
QUARANTINE_ONLY_COLUMNS = {
    "source_manifest_sha256",
    "compression_intent_sha256",
    "prompt_contract_version",
    "output_schema_version",
}


def _store_module():
    return import_module("app.services.context_compression_failure_store")


def _require_task8_postgres_dsn():
    if os.getenv("TASK8_PG_FAILURE_STORE_TESTS") != "isolated":
        pytest.skip(
            "TASK8_PG_FAILURE_STORE_TESTS=isolated is required for Task 8 DDL"
        )
    return require_postgres_dsn()


class RecordingCursor:
    def __init__(self, connection, delegate=None):
        self.connection = connection
        self.delegate = delegate
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.delegate is not None:
            self.delegate.close()
        return False

    def execute(self, statement, params=None):
        rendered = str(statement)
        self.connection.calls.append((rendered, params))
        if "UPDATE " in rendered.upper():
            self.connection.update_count += 1
            if self.connection.update_count == self.connection.fail_on_update:
                raise RuntimeError("injected second-row update failure")
        if self.delegate is not None:
            self.delegate.execute(statement, params)
            self.rowcount = self.delegate.rowcount

    def fetchone(self):
        if self.delegate is not None:
            return self.delegate.fetchone()
        return None

    def fetchall(self):
        if self.delegate is not None:
            return self.delegate.fetchall()
        return []


class RecordingConnection:
    def __init__(self, delegate=None, *, fail_on_update=None):
        self.delegate = delegate
        self.fail_on_update = fail_on_update
        self.update_count = 0
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        delegate = self.delegate.cursor() if self.delegate is not None else None
        return RecordingCursor(self, delegate)

    def commit(self):
        self.commits += 1
        if self.delegate is not None:
            self.delegate.commit()

    def rollback(self):
        self.rollbacks += 1
        if self.delegate is not None:
            self.delegate.rollback()

    def close(self):
        if self.delegate is not None:
            self.delegate.close()


class RecordingProvider:
    def __init__(self, connection):
        self.connection_object = connection

    @contextmanager
    def connection(self):
        try:
            yield self.connection_object
        except BaseException:
            self.connection_object.rollback()
            raise
        else:
            self.connection_object.commit()


class CountingPostgresProvider:
    def __init__(self, dsn):
        self.dsn = dsn
        self.checkouts = 0

    @contextmanager
    def connection(self):
        import psycopg2

        self.checkouts += 1
        with psycopg2.connect(self.dsn) as connection:
            yield connection


def _valid_row(*, kind="provider_circuit", state="closed"):
    domain = import_module(
        "app.services.context_compression_failure_containment"
    )
    if kind == "provider_circuit":
        scope = domain.build_provider_circuit_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
        )
    else:
        scope = domain.build_validation_quarantine_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
            source_manifest_sha256="4" * 64,
            compression_intent_sha256="5" * 64,
            prompt_contract_version="question-memory-prompt-v1",
            output_schema_version="question-memory-v1",
        )
    row = {
        "state_key_sha256": scope.state_key_sha256,
        "kind": kind,
        "privacy_scope_sha256": scope.privacy_scope_sha256,
        "owner_type": scope.owner_type,
        "owner_key_sha256": scope.owner_key_sha256,
        "provider": scope.provider,
        "model": scope.model,
        "artifact_type": scope.artifact_type,
        "policy_version": scope.policy_version,
        "source_manifest_sha256": getattr(
            scope, "source_manifest_sha256", None
        ),
        "compression_intent_sha256": getattr(
            scope, "compression_intent_sha256", None
        ),
        "prompt_contract_version": getattr(
            scope, "prompt_contract_version", None
        ),
        "output_schema_version": getattr(
            scope, "output_schema_version", None
        ),
        "consecutive_failure_count": 0,
        "state": state,
        "open_until": None,
        "probe_owner_sha256": None,
        "probe_token": None,
        "probe_lease_until": None,
        "fencing_version": 0,
        "state_version": 1,
        "last_failure_code": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return row


def decode_failure_state_record(row):
    domain = import_module(
        "app.services.context_compression_failure_containment"
    )
    return domain.FailureStateRecord.from_mapping(row)


def test_failure_state_relation_contract_is_dedicated_and_complete():
    relation = f"interview{RELATION_SUFFIX}"

    assert frozenset(REQUIRED_COLUMNS) <= required_columns_for_relation(relation)
    assert frozenset(
        QUARANTINE_ONLY_COLUMNS
        | {
            "open_until",
            "probe_owner_sha256",
            "probe_token",
            "probe_lease_until",
            "last_failure_code",
        }
    ) <= required_nullable_columns_for_relation(relation)


def test_failure_state_schema_contract_requires_state_fencing_and_cleanup_indexes():
    relation = f"interview{RELATION_SUFFIX}"
    checks = required_check_tokens_for_relation(relation)
    indexes = required_index_tokens_for_relation(relation)

    assert any({"kind", "provider_circuit", "validation_quarantine"} <= item for item in checks)
    assert any({"state", "closed", "open", "half_open"} <= item for item in checks)
    assert any({"state_version", "fencing_version"} <= item for item in checks)
    assert any({"probe_owner_sha256", "probe_token", "probe_lease_until"} <= item for item in checks)
    assert any({"privacy_scope_sha256", "owner_type", "owner_key_sha256"} <= item for item in indexes)
    assert any({"open_until", "probe_lease_until", "updated_at"} <= item for item in indexes)


def test_validate_mode_rejects_missing_relation_without_issuing_ddl():
    module = _store_module()
    connection = RecordingConnection()

    with pytest.raises(PostgresSchemaNotReady):
        module.PostgresContextCompressionFailureStore(
            dsn="private-dsn",
            connection_provider=RecordingProvider(connection),
            table_prefix="interview",
            schema_mode="validate",
        )

    statements = "\n".join(statement for statement, _ in connection.calls).upper()
    assert "CREATE " not in statements
    assert "ALTER " not in statements
    assert "DROP " not in statements


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("state_key_sha256", "raw-owner"),
        ("state_key_sha256", "f" * 64),
        ("privacy_scope_sha256", "not-a-digest"),
        ("owner_key_sha256", "PRIVATE_SESSION_CANARY"),
        ("kind", "global_provider_circuit"),
        ("state", "unknown"),
        ("consecutive_failure_count", -1),
        ("state_version", 0),
        ("fencing_version", -1),
    ),
)
def test_row_reconstruction_rejects_invalid_or_raw_identity_fields(field, value):
    row = _valid_row()
    row[field] = value

    with pytest.raises(ValueError):
        decode_failure_state_record(row)


@pytest.mark.parametrize(
    "updates",
    (
        {"state": "half_open"},
        {"probe_owner_sha256": "6" * 64},
        {"probe_token": "probe-a"},
        {"probe_lease_until": NOW + timedelta(seconds=60)},
    ),
)
def test_row_reconstruction_rejects_impossible_probe_shapes(updates):
    row = _valid_row()
    row.update(updates)

    with pytest.raises(ValueError):
        decode_failure_state_record(row)


def test_provider_rows_leave_quarantine_identity_null_and_quarantine_rows_require_it():
    provider = decode_failure_state_record(_valid_row())
    assert all(getattr(provider, name) is None for name in QUARANTINE_ONLY_COLUMNS)

    quarantine_row = _valid_row(kind="validation_quarantine")
    quarantine_row["compression_intent_sha256"] = None
    with pytest.raises(ValueError):
        decode_failure_state_record(quarantine_row)


def test_mutating_sql_uses_state_version_and_fencing_predicates():
    module = _store_module()
    connection = RecordingConnection()
    store = module.PostgresContextCompressionFailureStore(
        dsn="private-dsn",
        connection_provider=RecordingProvider(connection),
        table_prefix="interview",
        schema_mode="migrate",
    )
    connection.calls.clear()

    with pytest.raises(module.FailureStateConflict):
        store.reset(
            state_key_sha256="1" * 64,
            expected_state_version=7,
            expected_fencing_version=3,
            probe_token="probe-a",
            now=NOW,
        )

    updates = [
        statement.lower()
        for statement, _ in connection.calls
        if "update" in statement.lower()
    ]
    assert len(updates) == 1
    normalized_update = " ".join(updates[0].split())
    assert " where " in normalized_update
    where_clause = normalized_update.split(" where ", 1)[1]
    assert {
        "state_key_sha256",
        "state_version",
        "fencing_version",
        "probe_token",
    } <= {
        field
        for field in (
            "state_key_sha256",
            "state_version",
            "fencing_version",
            "probe_token",
        )
        if field in where_clause
    }
    assert "probe_lease_until" in where_clause
    assert "current_timestamp" not in where_clause
    assert connection.calls[-1][1][-1] == NOW


def test_cleanup_uses_one_explicit_application_clock_for_live_probe_filter():
    module = _store_module()
    connection = RecordingConnection()
    store = module.PostgresContextCompressionFailureStore(
        dsn="private-dsn",
        connection_provider=RecordingProvider(connection),
        table_prefix="interview",
        schema_mode="migrate",
    )
    connection.calls.clear()
    before = NOW - timedelta(days=1)

    store.cleanup_expired(before=before, now=NOW, batch_size=7)

    statement, params = connection.calls[-1]
    assert "CURRENT_TIMESTAMP" not in statement.upper()
    assert "probe_lease_until>%s" in statement
    assert params == (before, NOW, 7)


@pytest.mark.pg_runtime
def test_postgres_half_open_claim_has_one_winner_and_stale_fencing_fails():
    dsn = _require_task8_postgres_dsn()
    module = _store_module()
    domain = import_module("app.services.context_compression_failure_containment")
    prefix = make_runtime_table_prefix("failure")
    try:
        store = module.PostgresContextCompressionFailureStore(
            dsn=dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        service = domain.ContextCompressionFailureContainment(
            store=store,
            config=domain.FailureContainmentConfig(
                provider_circuit_threshold=1,
                provider_circuit_cooldown_seconds=120,
                validation_quarantine_threshold=1,
                validation_quarantine_cooldown_seconds=120,
                failure_state_lease_seconds=60,
            ),
            clock=lambda: NOW,
        )
        scope = domain.build_provider_circuit_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
        )
        first = service.before_attempt(scope, worker_id="opener")
        service.record_failure(scope, failure_code="provider_timeout", decision=first)
        service.clock = lambda: NOW + timedelta(seconds=121)

        barrier = Barrier(2)
        decisions = []

        def claim(worker_id):
            barrier.wait()
            decisions.append(service.before_attempt(scope, worker_id=worker_id))

        threads = [Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert not any(thread.is_alive() for thread in threads)

        assert sum(item.allow_provider_call for item in decisions) == 1
        stale = next(item for item in decisions if item.allow_provider_call)
        store.reclaim_expired_probe(
            state_key_sha256=scope.state_key_sha256,
            expected_state_version=stale.state_version,
            expected_fencing_version=stale.fencing_version,
            probe_owner_sha256="6" * 64,
            now=NOW + timedelta(seconds=182),
            lease_seconds=60,
        )
        with pytest.raises(domain.FailureStateLeaseLost):
            service.record_success(scope, decision=stale)
    finally:
        drop_runtime_tables(dsn, prefix)


@pytest.mark.pg_runtime
@pytest.mark.parametrize(
    "stale_dimension",
    (
        "state_key_sha256",
        "probe_token",
        "state_version",
        "fencing_version",
    ),
)
def test_postgres_probe_reset_rejects_each_stale_cas_dimension(
    stale_dimension,
):
    dsn = _require_task8_postgres_dsn()
    module = _store_module()
    domain = import_module("app.services.context_compression_failure_containment")
    prefix = make_runtime_table_prefix("failure")
    try:
        store = module.PostgresContextCompressionFailureStore(
            dsn=dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        service = domain.ContextCompressionFailureContainment(
            store=store,
            config=domain.FailureContainmentConfig(
                provider_circuit_threshold=1,
                provider_circuit_cooldown_seconds=120,
                validation_quarantine_threshold=1,
                validation_quarantine_cooldown_seconds=120,
                failure_state_lease_seconds=60,
            ),
            clock=lambda: NOW,
        )
        scope = domain.build_provider_circuit_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
        )
        opened = service.before_attempt(scope, worker_id="opener")
        service.record_failure(
            scope,
            failure_code="provider_timeout",
            decision=opened,
        )
        service.clock = lambda: NOW + timedelta(seconds=121)
        probe = service.before_attempt(scope, worker_id="probe-owner")
        expected = {
            "state_key_sha256": scope.state_key_sha256,
            "probe_token": probe.probe_token,
            "state_version": probe.state_version,
            "fencing_version": probe.fencing_version,
        }
        if stale_dimension == "state_key_sha256":
            expected[stale_dimension] = "f" * 64
        elif stale_dimension == "probe_token":
            expected[stale_dimension] = "stale-probe-token"
        else:
            expected[stale_dimension] += 1

        with pytest.raises(module.FailureStateConflict):
            store.reset(
                state_key_sha256=expected["state_key_sha256"],
                expected_state_version=expected["state_version"],
                expected_fencing_version=expected["fencing_version"],
                probe_token=expected["probe_token"],
                now=NOW + timedelta(seconds=122),
            )
        current = store.get(scope.state_key_sha256)
        assert current.state == "half_open"
        assert current.state_version == probe.state_version
        assert current.fencing_version == probe.fencing_version
    finally:
        drop_runtime_tables(dsn, prefix)


@pytest.mark.pg_runtime
def test_postgres_dual_key_authorize_and_finish_each_use_one_sorted_transaction():
    dsn = _require_task8_postgres_dsn()
    module = _store_module()
    domain = import_module("app.services.context_compression_failure_containment")
    prefix = make_runtime_table_prefix("failure")
    provider = CountingPostgresProvider(dsn)
    try:
        store = module.PostgresContextCompressionFailureStore(
            dsn=dsn,
            connection_provider=provider,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        service = domain.ContextCompressionFailureContainment(
            store=store,
            config=domain.FailureContainmentConfig(
                provider_circuit_threshold=3,
                provider_circuit_cooldown_seconds=300,
                validation_quarantine_threshold=2,
                validation_quarantine_cooldown_seconds=3_600,
                failure_state_lease_seconds=60,
            ),
            clock=lambda: NOW,
        )
        provider_scope = domain.build_provider_circuit_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
        )
        validation_scope = domain.build_validation_quarantine_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
            source_manifest_sha256="4" * 64,
            compression_intent_sha256="5" * 64,
            prompt_contract_version="question-memory-prompt-v1",
            output_schema_version="question-memory-v1",
        )
        provider.checkouts = 0

        authorization = service.authorize_attempt(
            provider_scope=provider_scope,
            validation_scope=validation_scope,
            worker_id="dual-state-worker",
        )

        assert authorization.locked_state_keys == tuple(
            sorted(
                (
                    provider_scope.state_key_sha256,
                    validation_scope.state_key_sha256,
                )
            )
        )
        assert provider.checkouts == 1

        service.finish_attempt(authorization, outcome="success")
        assert provider.checkouts == 2
        assert store.get(provider_scope.state_key_sha256).state == "closed"
        assert store.get(validation_scope.state_key_sha256).state == "closed"
    finally:
        drop_runtime_tables(dsn, prefix)


@pytest.mark.pg_runtime
def test_postgres_dual_authorize_releases_probe_when_other_scope_is_blocked():
    dsn = _require_task8_postgres_dsn()
    module = _store_module()
    domain = import_module("app.services.context_compression_failure_containment")
    prefix = make_runtime_table_prefix("failure")
    try:
        store = module.PostgresContextCompressionFailureStore(
            dsn=dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        service = domain.ContextCompressionFailureContainment(
            store=store,
            config=domain.FailureContainmentConfig(
                provider_circuit_threshold=3,
                provider_circuit_cooldown_seconds=300,
                validation_quarantine_threshold=2,
                validation_quarantine_cooldown_seconds=3_600,
                failure_state_lease_seconds=60,
            ),
            clock=lambda: NOW,
        )
        provider_scope = domain.build_provider_circuit_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
        )
        validation_scope = domain.build_validation_quarantine_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
            source_manifest_sha256="4" * 64,
            compression_intent_sha256="5" * 64,
            prompt_contract_version="question-memory-prompt-v1",
            output_schema_version="question-memory-v1",
        )
        for index in range(3):
            decision = service.before_attempt(
                provider_scope,
                worker_id=f"provider-opener-{index}",
            )
            service.record_failure(
                provider_scope,
                failure_code="provider_timeout",
                decision=decision,
            )
        for index in range(2):
            decision = service.before_attempt(
                validation_scope,
                worker_id=f"validation-opener-{index}",
            )
            service.record_failure(
                validation_scope,
                failure_code="invalid_schema",
                decision=decision,
            )
        service.clock = lambda: NOW + timedelta(seconds=301)

        blocked = service.authorize_attempt(
            provider_scope=provider_scope,
            validation_scope=validation_scope,
            worker_id="blocked-dual-worker",
        )

        assert blocked.allow_provider_call is False
        assert blocked.reason == "validation_quarantine_open"
        for scope in (provider_scope, validation_scope):
            stored = store.get(scope.state_key_sha256)
            assert stored.state == "open"
            assert stored.probe_owner_sha256 is None
            assert stored.probe_token is None
            assert stored.probe_lease_until is None
    finally:
        drop_runtime_tables(dsn, prefix)


@pytest.mark.pg_runtime
def test_postgres_combined_validation_failure_rolls_back_second_row_fault():
    dsn = _require_task8_postgres_dsn()
    module = _store_module()
    domain = import_module("app.services.context_compression_failure_containment")
    prefix = make_runtime_table_prefix("failure")
    recording = None
    try:
        store = module.PostgresContextCompressionFailureStore(
            dsn=dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
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
            clock=lambda: NOW,
        )
        provider_scope = domain.build_provider_circuit_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
        )
        validation_scope = domain.build_validation_quarantine_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
            source_manifest_sha256="4" * 64,
            compression_intent_sha256="5" * 64,
            prompt_contract_version="question-memory-prompt-v1",
            output_schema_version="question-memory-v1",
        )
        provider_decision = service.before_attempt(
            provider_scope,
            worker_id="provider-seed",
        )
        service.record_failure(
            provider_scope,
            failure_code="provider_timeout",
            decision=provider_decision,
        )
        validation_decision = service.before_attempt(
            validation_scope,
            worker_id="validation-seed",
        )
        service.record_failure(
            validation_scope,
            failure_code="invalid_schema",
            decision=validation_decision,
        )
        authorization = service.authorize_attempt(
            provider_scope=provider_scope,
            validation_scope=validation_scope,
            worker_id="combined-finish-worker",
        )
        provider_before = store.get(provider_scope.state_key_sha256)
        validation_before = store.get(validation_scope.state_key_sha256)

        import psycopg2

        recording = RecordingConnection(
            psycopg2.connect(dsn),
            fail_on_update=2,
        )
        faulting_store = module.PostgresContextCompressionFailureStore(
            dsn=dsn,
            connection_provider=RecordingProvider(recording),
            table_prefix=prefix,
            schema_mode="validate",
        )
        faulting_service = domain.ContextCompressionFailureContainment(
            store=faulting_store,
            config=config,
            clock=lambda: NOW,
        )
        recording.update_count = 0
        rollbacks_before = recording.rollbacks

        with pytest.raises(RuntimeError, match="second-row update"):
            faulting_service.finish_attempt(
                authorization,
                outcome="validation_failed",
                failure_code="grounding_failed",
            )

        assert recording.update_count == 2
        assert recording.rollbacks > rollbacks_before
        assert store.get(provider_scope.state_key_sha256) == provider_before
        assert store.get(validation_scope.state_key_sha256) == validation_before
    finally:
        if recording is not None:
            recording.close()
        drop_runtime_tables(dsn, prefix)


@pytest.mark.pg_runtime
def test_expired_unreclaimed_probe_rejects_stale_success():
    dsn = _require_task8_postgres_dsn()
    module = _store_module()
    domain = import_module("app.services.context_compression_failure_containment")
    prefix = make_runtime_table_prefix("failure")
    try:
        store = module.PostgresContextCompressionFailureStore(
            dsn=dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        service = domain.ContextCompressionFailureContainment(
            store=store,
            config=domain.FailureContainmentConfig(
                provider_circuit_threshold=1,
                provider_circuit_cooldown_seconds=120,
                validation_quarantine_threshold=1,
                validation_quarantine_cooldown_seconds=120,
                failure_state_lease_seconds=60,
            ),
            clock=lambda: NOW,
        )
        scope = domain.build_provider_circuit_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
        )
        opener = service.before_attempt(scope, worker_id="opener")
        service.record_failure(
            scope,
            failure_code="provider_timeout",
            decision=opener,
        )
        service.clock = lambda: NOW + timedelta(seconds=121)
        stale = service.before_attempt(scope, worker_id="stale-probe")
        service.clock = lambda: NOW + timedelta(seconds=182)

        with pytest.raises(domain.FailureStateLeaseLost):
            service.record_success(scope, decision=stale)
        assert store.get(scope.state_key_sha256).state == "half_open"
    finally:
        drop_runtime_tables(dsn, prefix)


@pytest.mark.pg_runtime
def test_postgres_owner_delete_and_retention_preserve_live_probe():
    dsn = _require_task8_postgres_dsn()
    module = _store_module()
    domain = import_module("app.services.context_compression_failure_containment")
    prefix = make_runtime_table_prefix("failure")
    try:
        store = module.PostgresContextCompressionFailureStore(
            dsn=dsn,
            table_prefix=prefix,
            schema_mode="migrate",
        )
        clock = lambda: NOW
        service = domain.ContextCompressionFailureContainment(
            store=store,
            config=domain.FailureContainmentConfig(
                provider_circuit_threshold=1,
                provider_circuit_cooldown_seconds=120,
                validation_quarantine_threshold=1,
                validation_quarantine_cooldown_seconds=120,
                failure_state_lease_seconds=60,
            ),
            clock=clock,
        )
        scope = domain.build_provider_circuit_scope(
            privacy_scope_sha256="2" * 64,
            owner_type="interview_session",
            owner_key="PRIVATE_SESSION_CANARY",
            provider="openai-compatible",
            model="gpt-4o-mini",
            artifact_type="question_memory",
            policy_version="question-memory-v1",
        )
        decision = service.before_attempt(scope, worker_id="opener")
        service.record_failure(
            scope,
            failure_code="provider_timeout",
            decision=decision,
        )
        service.clock = lambda: NOW + timedelta(seconds=121)
        service.before_attempt(scope, worker_id="live-probe")
        assert store.cleanup_expired(
            before=NOW + timedelta(days=1),
            now=NOW + timedelta(seconds=121),
            batch_size=100,
        ) == 0
        assert store.get(scope.state_key_sha256).state == "half_open"
        assert store.delete_owner(
            privacy_scope_sha256=scope.privacy_scope_sha256,
            owner_type=scope.owner_type,
            owner_key_sha256=scope.owner_key_sha256,
        ) == 1
        assert store.get(scope.state_key_sha256) is None
        assert store.delete_owner(
            privacy_scope_sha256=scope.privacy_scope_sha256,
            owner_type=scope.owner_type,
            owner_key_sha256=scope.owner_key_sha256,
        ) == 0

        expired_scopes = []
        for owner_key in ("expired-owner-a", "expired-owner-b"):
            expired = domain.build_provider_circuit_scope(
                privacy_scope_sha256="2" * 64,
                owner_type="interview_session",
                owner_key=owner_key,
                provider="openai-compatible",
                model="gpt-4o-mini",
                artifact_type="question_memory",
                policy_version="question-memory-v1",
            )
            allowed = service.before_attempt(
                expired,
                worker_id=f"opener-{owner_key}",
            )
            service.record_failure(
                expired,
                failure_code="provider_timeout",
                decision=allowed,
            )
            expired_scopes.append(expired)

        retention_before = NOW + timedelta(days=1)
        assert store.cleanup_expired(
            before=retention_before,
            now=NOW + timedelta(days=1),
            batch_size=1,
        ) == 1
        assert sum(
            store.get(item.state_key_sha256) is None
            for item in expired_scopes
        ) == 1
        assert store.cleanup_expired(
            before=retention_before,
            now=NOW + timedelta(days=1),
            batch_size=1,
        ) == 1
        assert all(
            store.get(item.state_key_sha256) is None
            for item in expired_scopes
        )
        assert store.cleanup_expired(
            before=retention_before,
            now=NOW + timedelta(days=1),
            batch_size=1,
        ) == 0
    finally:
        drop_runtime_tables(dsn, prefix)
