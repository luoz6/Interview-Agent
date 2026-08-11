from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.adapters.postgres.migration_harness import RuntimeMigrationHarness
from app.ports.postgres_migrations import (
    MigrationExecution,
    MigrationIdempotencyFailure,
)
from app.ports.postgres_scope import OwnedPostgresLease, PostgresTargetIdentity


class FakeScope:
    def __init__(self):
        self.assertions = 0

    def open(self, approval):
        raise AssertionError("the migration harness must not create scopes")

    def assert_owned(self, lease):
        self.assertions += 1

    def heartbeat(self, lease, *, lease_seconds):
        raise AssertionError("the migration harness does not own heartbeat policy")


class FakeMigrationAdapter:
    def __init__(self, executions):
        self.executions = iter(executions)
        self.calls = []

    def apply(self, lease):
        self.calls.append(("apply", lease.scope_prefix))
        return next(self.executions)

    def validate(self, lease):
        self.calls.append(("validate", lease.scope_prefix))


def _lease():
    identity = PostgresTargetIdentity(
        system_identifier="7612345678901234567",
        database_name="interview_test",
        database_oid=16384,
        server_version_num=160004,
        server_address="127.0.0.1",
        server_port=5432,
        current_user="test_owner",
        current_schema="public",
    )
    return OwnedPostgresLease(
        scope_prefix="test_migrate_0123456789ab",
        target_identity=identity,
        approval_receipt_sha256="a" * 64,
        ownership_token="b" * 32,
        fencing_version=1,
        lease_expires_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
    )


def _execution(applied: bool):
    return MigrationExecution(
        migration_id="runtime-v15",
        checksum="c" * 64,
        applied=applied,
    )


def test_apply_validate_and_second_apply_prove_idempotency():
    scope = FakeScope()
    adapter = FakeMigrationAdapter([_execution(True), _execution(False)])
    harness = RuntimeMigrationHarness(scope=scope, adapter=adapter)

    result = harness.apply_and_validate(_lease())

    assert result.first_apply_changed_schema is True
    assert result.second_apply_changed_schema is False
    assert result.validation_passed is True
    assert adapter.calls == [
        ("apply", "test_migrate_0123456789ab"),
        ("validate", "test_migrate_0123456789ab"),
        ("apply", "test_migrate_0123456789ab"),
        ("validate", "test_migrate_0123456789ab"),
    ]
    assert scope.assertions == 4


def test_second_apply_that_changes_schema_fails_idempotency_gate():
    adapter = FakeMigrationAdapter([_execution(True), _execution(True)])
    harness = RuntimeMigrationHarness(scope=FakeScope(), adapter=adapter)

    with pytest.raises(MigrationIdempotencyFailure, match="second migration"):
        harness.apply_and_validate(_lease())


def test_migration_identity_change_fails_idempotency_gate():
    second = MigrationExecution(
        migration_id="runtime-v16",
        checksum="d" * 64,
        applied=False,
    )
    harness = RuntimeMigrationHarness(
        scope=FakeScope(),
        adapter=FakeMigrationAdapter([_execution(True), second]),
    )

    with pytest.raises(MigrationIdempotencyFailure, match="identity changed"):
        harness.apply_and_validate(_lease())


def test_validate_only_never_calls_apply():
    scope = FakeScope()
    adapter = FakeMigrationAdapter([])
    harness = RuntimeMigrationHarness(scope=scope, adapter=adapter)

    harness.validate_only(_lease())

    assert adapter.calls == [("validate", "test_migrate_0123456789ab")]
    assert scope.assertions == 2
